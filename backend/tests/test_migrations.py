import os
import sqlite3

import pytest

import db


def make_v1(path):
    """A database as FeedVault wrote it before migrations existed: the v1
    schema, meta.schema_version = '1', user_version still 0, WAL mode."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(db.SCHEMA_V1)
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO decisions(post_id, decision, at) VALUES ('instagram:P1', 'keep', 1)")
    conn.commit()
    return conn


def version(path):
    conn = sqlite3.connect(path)
    try:
        return db.schema_version(conn), conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def tables(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def add_note_column(conn):
    conn.execute("ALTER TABLE decisions ADD COLUMN note TEXT")


def half_then_fail(conn):
    conn.execute("CREATE TABLE half_done (x INTEGER)")
    raise RuntimeError("boom")


def test_fresh_database(tmp_path):
    path = str(tmp_path / "data" / "feedvault.db")
    db.init(path)
    assert version(path) == (len(db.MIGRATIONS), len(db.MIGRATIONS))
    assert {"posts", "media", "unmatched", "decisions", "meta"} <= tables(path)
    assert not [f for f in os.listdir(tmp_path / "data") if ".bak-v" in f]    # nothing to back up
    db.init(path)                                                            # idempotent
    assert version(path)[0] == len(db.MIGRATIONS)


def test_v1_database_is_version_1(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    make_v1(path).close()
    assert version(path) == (1, 0)
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:1])
    db.init(path)                           # already current: stamped, no backup, data untouched
    assert version(path) == (1, 1)
    assert not os.path.exists(path + ".bak-v1")
    assert db.connect().execute("SELECT post_id FROM decisions").fetchall()[0][0] == "instagram:P1"


def indexes(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    finally:
        conn.close()


def test_v1_upgraded_to_v2_storage(tmp_path):
    # The first real migration: a v1 file made before migrations existed.
    path = str(tmp_path / "feedvault.db")
    conn = make_v1(path)
    conn.execute("INSERT INTO posts(id, platform, post_id, kind, saved_at, tool, meta_path, indexed_at) "
                 "VALUES ('instagram:P1', 'instagram', 'P1', 'image', 1, 'instaloader', '/m/p1.json', 1)")
    conn.execute("INSERT INTO media(post_id, idx, kind, path, size) VALUES ('instagram:P1', 1, 'image', '/m/p1.jpg', 100)")
    conn.commit()
    conn.close()
    db.init(path)
    assert version(path) == (len(db.MIGRATIONS), len(db.MIGRATIONS))
    assert version(path + ".bak-v1")[0] == 1
    assert {"media_post_size", "media_size"} <= indexes(path)
    assert not {"media_post_size", "media_size"} & indexes(path + ".bak-v1")
    c = db.connect()
    assert c.execute("SELECT post_id FROM decisions").fetchone()[0] == "instagram:P1"
    assert db.storage(c)["totals"] == {"posts": 1, "media": 1, "bytes": 100}
    # the cached answer follows a commit, from this connection or another one
    c.execute("UPDATE media SET size = 250")
    c.commit()
    assert db.storage(c)["totals"]["bytes"] == 250
    other = sqlite3.connect(path)
    other.execute("UPDATE media SET missing = 1")
    other.commit()
    other.close()
    assert db.storage(c)["totals"] == {"posts": 1, "media": 0, "bytes": 0}


def test_early_v1_file_gets_missing_tables(tmp_path):
    # Files from before the decisions table existed still said version 1.
    path = str(tmp_path / "feedvault.db")
    make_v1(path).close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE decisions")
    conn.commit()
    conn.close()
    db.init(path)
    assert "decisions" in tables(path)
    assert version(path) == (len(db.MIGRATIONS), len(db.MIGRATIONS))


def test_v1_upgraded_with_backup(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    live = make_v1(path)                    # stays open: the decision may still sit in the WAL
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS[:1], add_note_column])
    db.init(path)
    live.close()
    assert version(path) == (2, 2)
    row = db.connect().execute("SELECT post_id, note FROM decisions").fetchone()
    assert (row["post_id"], row["note"]) == ("instagram:P1", None)
    # the backup is the v1 file, WAL contents included
    assert version(path + ".bak-v1")[0] == 1
    bak = sqlite3.connect(path + ".bak-v1")
    assert bak.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert "note" not in [c[1] for c in bak.execute("PRAGMA table_info(decisions)")]
    bak.close()


def test_failed_migration_rolls_back_and_keeps_backup(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    make_v1(path).close()
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS[:1], add_note_column, half_then_fail])
    with pytest.raises(RuntimeError, match="boom"):
        db.init(path)
    # step 2 committed, step 3 left nothing behind
    assert version(path) == (2, 2)
    assert "half_done" not in tables(path)
    assert os.path.exists(path + ".bak-v1")
    assert version(path + ".bak-v1")[0] == 1
    # once the bad step is fixed, the next start picks up where it stopped
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS[:2], lambda conn: None])
    db.init(path)
    assert version(path) == (3, 3)
    assert os.path.exists(path + ".bak-v2")


def test_newer_database_refused(tmp_path):
    path = str(tmp_path / "feedvault.db")
    conn = make_v1(path)
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 5}")
    conn.commit()
    conn.close()
    with pytest.raises(db.SchemaTooNew, match="newer FeedVault"):
        db.init(path)
    assert version(path)[1] == len(db.MIGRATIONS) + 5          # left alone
    assert not os.path.exists(path + f".bak-v{len(db.MIGRATIONS) + 5}")


def test_only_last_three_backups_kept(tmp_path):
    path = str(tmp_path / "feedvault.db")
    make_v1(path).close()
    for i, v in enumerate((11, 12, 13, 14)):
        (tmp_path / f"feedvault.db.bak-v{v}").write_bytes(b"old")
        os.utime(tmp_path / f"feedvault.db.bak-v{v}", (1000 + i, 1000 + i))
    conn = sqlite3.connect(path)
    db.backup(conn, path + ".bak-v1")
    conn.close()
    left = sorted(f for f in os.listdir(tmp_path) if ".bak-v" in f)
    assert left == ["feedvault.db.bak-v1", "feedvault.db.bak-v13", "feedvault.db.bak-v14"]


def test_v2_upgraded_to_v3_copies(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:2])
    db.init(path)
    db.connect().execute("INSERT INTO decisions(post_id, decision, at) VALUES ('instagram:P1', 'keep', 1)")
    db.connect().commit()
    monkeypatch.undo()
    db.init(path)
    assert version(path)[0] >= 3 and version(path + ".bak-v2")[0] == 2
    assert "copies" in tables(path) and "copies" not in tables(path + ".bak-v2")
    assert db.connect().execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_v4_upgraded_to_tags_and_collections(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:4])
    db.init(path)
    monkeypatch.undo()
    db.init(path)
    assert version(path)[0] >= 5 and version(path + ".bak-v4")[0] == 4
    assert {"tags", "post_tags", "collections", "collection_posts"} <= tables(path)
    assert "tags" not in tables(path + ".bak-v4")


def test_saved_posts_filled_from_the_save_jobs_kept(tmp_path, monkeypatch):
    """Migration 15: saves made before it are known from the Save jobs still kept."""
    import json
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:14])
    db.migrate(path)
    conn = sqlite3.connect(path)
    for kind, state, result in [
            ("instaloader-post", "done", {"post": "instagram:SAVED1", "added": 1}),
            ("instaloader-post", "done", {"post": "instagram:SYNCED", "added": 0}),     # a sync had it
            ("instaloader-post", "failed", {"post": None, "added": 0}),
            ("instaloader-sync", "done", {"post": "instagram:NOT", "added": 3}),
            ("instaloader-post", "done", "not json")]:
        conn.execute("INSERT INTO jobs(kind, params, argv, cwd, lock_group, state, created_at, ended_at, result) "
                     "VALUES (?, '{}', '[]', '/', 'g', ?, 1, 5, ?)",
                     (kind, state, result if isinstance(result, str) else json.dumps(result)))
    conn.commit()
    conn.close()
    monkeypatch.undo()
    db.migrate(path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT post_id, saved_at FROM saved_posts").fetchall() == [("instagram:SAVED1", 5)]
    conn.close()


def test_source_options_become_overrides(tmp_path, monkeypatch):
    """Migration 26: a source keeps only the options it set itself (those not
    as they were by default), so it follows its platform's download defaults
    for the rest; nothing changes in what it syncs."""
    import json
    import sources
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:25])
    db.migrate(path)
    conn = sqlite3.connect(path)
    every = {"full_history": False, "session": None, "content": None, "media": "all", "since": None,
             "first_posts": None, "script": None, "schedule": "off"}
    rows = [
        json.dumps(every),                                                   # as the form saved it: nothing set
        json.dumps({**every, "media": "images", "schedule": "daily", "content": ["media"]}),
        json.dumps({"since": "2024-01-01"}),                                 # older rows: only some keys
        json.dumps({**every, "session": {"mode": "none"}, "first_posts": 20, "argv": ["-x"]}),
        "{}", "not json", "[1]",
        json.dumps({"v": 2, "media": "videos"}),                             # already stored as overrides
    ]
    for i, text in enumerate(rows):
        conn.execute("INSERT INTO sources(platform, tool, target, folder, options, created_at) "
                     "VALUES ('twitter', 'gallery-dl', ?, '/m', ?, 1)", (f"https://x.com/a{i}", text))
    conn.commit()
    conn.close()
    monkeypatch.undo()
    db.migrate(path)
    conn = sqlite3.connect(path)
    got = [json.loads(t) for (t,) in conn.execute("SELECT options FROM sources ORDER BY id")]
    conn.close()
    assert got == [
        {"v": 2},
        {"v": 2, "media": "images", "schedule": "daily", "content": ["media"]},
        {"v": 2, "since": "2024-01-01"},
        {"v": 2, "session": {"mode": "none"}, "first_posts": 20},
        {"v": 2}, {"v": 2}, {"v": 2},
        {"v": 2, "media": "videos"},
    ]
    # The frozen defaults of migration 26 are BUILTIN's before metadata modes and download (#218).
    assert db._OPTIONS_25 == {k: v for k, v in sources.BUILTIN.items() if k not in ("metadata", "download")}


def test_no_sync_tables_added_and_safe_to_run_again(tmp_path, monkeypatch):
    """Migration 27 (Don't sync, nosync.py): two empty tables on a database
    in use; what it had stays, and running it again changes nothing."""
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:26])
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO people(name, created_at) VALUES ('Carol', 1)")
    conn.execute("INSERT INTO sources(platform, author_id, tool, target, folder, created_at) "
                 "VALUES ('instagram', '777', 'instaloader', 'carol', '/m/carol', 1)")
    conn.commit()
    conn.close()
    monkeypatch.undo()
    db.migrate(path)
    assert version(path) == (len(db.MIGRATIONS),) * 2 and len(db.MIGRATIONS) >= 27
    assert {"no_sync_people", "no_sync_accounts"} <= tables(path)
    conn = sqlite3.connect(path, isolation_level=None)
    assert conn.execute("SELECT COUNT(*) FROM no_sync_people").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM no_sync_accounts").fetchone()[0] == 0
    conn.execute("INSERT INTO no_sync_people(person_id, at) VALUES (1, 5)")
    conn.execute("INSERT INTO no_sync_accounts(platform, author_id, at) VALUES ('instagram', '777', 5)")
    # Again (a file that has them already): nothing lost, nothing added.
    db._migrate_27(conn)
    assert conn.execute("SELECT person_id, at FROM no_sync_people").fetchall() == [(1, 5)]
    assert conn.execute("SELECT platform, author_id, at FROM no_sync_accounts").fetchall() == [("instagram", "777", 5)]
    assert conn.execute("SELECT target FROM sources").fetchall() == [("carol",)]
    conn.close()
    db.migrate(path)                                   # at the latest version: a no-op
    assert version(path)[0] == len(db.MIGRATIONS)


def links_at_27(tmp_path, monkeypatch, rows):
    """A database at version 27 with people Alice (1) and Bob (2) and these
    links: (url, title, notes, person_id, position, created_at, tied_at)."""
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:27])
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO people(id, name, created_at) VALUES (1, 'Alice', 1), (2, 'Bob', 1)")
    conn.executemany("INSERT INTO links(url, title, notes, person_id, position, created_at, tied_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.undo()
    return path


def all_links(conn):
    return conn.execute("SELECT url, title, notes, person_id, position, created_at, tied_at FROM links "
                        "ORDER BY created_at").fetchall()


def migrate_to(path, monkeypatch, version):
    """db.migrate, up to ``version`` only."""
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:version])
    db.migrate(path)
    monkeypatch.undo()


def test_links_lose_their_tracking_and_merge(tmp_path, monkeypatch, capsys):
    """Migration 28: every saved URL cleaned as one saved now is; links that
    become one are merged into the oldest, no notes, title or person lost."""
    long_a, long_b = "a" * 3000, "b" * 3000
    path = links_at_27(tmp_path, monkeypatch, [
        # Three spellings of one TikTok profile: into the first, each note once.
        ("https://www.tiktok.com/@smeepies?_r=1&_t=ZT-94e14uZEVsz", "TikTok", "first", 1, 1, 10, 10),
        ("https://www.tiktok.com/@smeepies?_t=Other", "", "second", None, None, 20, None),
        ("https://www.tiktok.com/@smeepies", "Smeepies", "first", 1, 2, 30, 30),
        # The oldest is no one's: it takes the other's person, place, title and notes.
        ("https://example.com/shop?utm_source=x", "", "", None, None, 5, None),
        ("https://example.com/shop", "Shop", "Bob's", 2, 1, 40, 50),
        # Two people's: left as they were, the clean URL with the one that had it.
        ("https://bob.example/?utm_source=a", "", "Alice's", 1, 3, 1, 1),
        ("https://bob.example", "", "Bob's", 2, 2, 2, 2),
        # Notes too long once joined: kept apart (the oldest cleaned, the
        # other a spelling with a last slash, so the URLs differ still).
        ("https://notes.example/?fbclid=1", "", long_a, None, None, 60, None),
        ("https://notes.example/", "", long_b, None, None, 61, None),
        # Not tracking: untouched. Alone with tracking: cleaned.
        ("https://example.com/v?t=42", "", "", None, None, 70, None),
        ("https://youtu.be/abc?si=x", "", "kept", 2, 3, 80, 80),
    ])
    capsys.readouterr()
    migrate_to(path, monkeypatch, 28)
    assert ("[db] Links: tracking parameters dropped from 4, 3 merged into an older link with the same "
            "address, 2 kept apart (another person's, or notes too long joined)") in capsys.readouterr().out
    conn = sqlite3.connect(path, isolation_level=None)
    assert all_links(conn) == [
        ("https://bob.example/?utm_source=a", "", "Alice's", 1, 3, 1, 1),
        ("https://bob.example", "", "Bob's", 2, 2, 2, 2),
        ("https://example.com/shop", "Shop", "Bob's", 2, 1, 5, 50),
        ("https://www.tiktok.com/@smeepies", "TikTok", "first\n\nsecond", 1, 1, 10, 10),
        ("https://notes.example", "", long_a, None, None, 60, None),
        ("https://notes.example/", "", long_b, None, None, 61, None),
        ("https://example.com/v?t=42", "", "", None, None, 70, None),
        ("https://youtu.be/abc", "", "kept", 2, 3, 80, 80),
    ]
    assert conn.execute("SELECT value FROM meta WHERE key = 'userdata_stale'").fetchone() == ("links",)
    # Again: nothing changes. (Migration 30 then merges the two people's
    # links: test_links_kept_apart_for_two_people_become_one.)
    before = all_links(conn)
    conn.execute("BEGIN")
    db._migrate_28(conn)
    conn.execute("COMMIT")
    assert all_links(conn) == before
    conn.close()


def test_links_without_tracking_are_left_alone(tmp_path, monkeypatch, capsys):
    """Migration 28 on links with nothing to drop: no change, no mark, no line."""
    rows = [("https://linktr.ee/alice", "Linktree", "n", 1, 1, 1, 1),
            ("https://example.com/?q=1", "", "", None, None, 2, None)]
    path = links_at_27(tmp_path, monkeypatch, rows)
    capsys.readouterr()
    migrate_to(path, monkeypatch, 28)
    assert "[db] Links:" not in capsys.readouterr().out
    conn = sqlite3.connect(path)
    assert all_links(conn) == rows
    assert conn.execute("SELECT 1 FROM meta WHERE key = 'userdata_stale'").fetchone() is None


def test_links_json_written_again_after_the_merge(tmp_path, monkeypatch):
    """After migration 28 merged links, the next start writes links.json
    again (it still lists the rows merged away, which a rebuilt index would
    read back), once."""
    import json
    import userdata
    path = links_at_27(tmp_path, monkeypatch, [
        ("https://a.example/?utm_source=x", "", "one", None, None, 1, None),
        ("https://a.example", "", "two", None, None, 2, None),
    ])
    data = tmp_path / "data"
    (data / "userdata").mkdir(parents=True)
    old = data / "userdata" / "links.json"
    old.write_text(json.dumps({"version": 1, "rows": [{"url": "https://a.example/?utm_source=x", "notes": "one"},
                                                      {"url": "https://a.example", "notes": "two"}]}))
    db.migrate(path)
    conn = sqlite3.connect(path)
    userdata.restore_all(conn, str(data))
    assert [(r["url"], r["notes"]) for r in json.loads(old.read_text())["rows"]] == [("https://a.example", "one\n\ntwo")]
    assert conn.execute("SELECT 1 FROM meta WHERE key = 'userdata_stale'").fetchone() is None
    old.write_text(json.dumps({"version": 1, "rows": []}))
    userdata.restore_all(conn, str(data))                  # the mark is gone: the file is left as it is
    assert json.loads(old.read_text())["rows"] == []


def ties(conn):
    """(url, person name, position, tied_at) of every tie (link_people), by URL and name."""
    return conn.execute("SELECT l.url, p.name, lp.position, lp.tied_at FROM link_people lp "
                        "JOIN links l ON l.id = lp.link_id JOIN people p ON p.id = lp.person_id "
                        "ORDER BY l.url, p.name").fetchall()


def test_links_get_their_people_table(tmp_path, monkeypatch, capsys):
    """Migration 30: every tie of links.person_id moves to link_people with
    its place and tied_at (none: from when the link was saved); links
    loses the three columns; a link of no one has no tie."""
    path = links_at_27(tmp_path, monkeypatch, [
        ("https://linktr.ee/alice", "Linktree", "n", 1, 2, 10, 15),
        ("https://alice.example", "", "", 1, 1, 11, None),            # tied before tied_at was kept
        ("https://bob.example", "Bob", "", 2, 1, 12, 12),
        ("https://loose.example", "", "to sort", None, None, 13, None),
    ])
    capsys.readouterr()
    db.migrate(path)
    assert "[db] Links:" not in capsys.readouterr().out          # nothing merged: no line, no mark
    assert version(path)[0] == len(db.MIGRATIONS) >= 30
    conn = sqlite3.connect(path)
    assert [r[1] for r in conn.execute("PRAGMA table_info(links)")] == ["id", "url", "title", "notes", "created_at"]
    assert conn.execute("SELECT id, url, title, notes, created_at FROM links ORDER BY id").fetchall() == [
        (1, "https://linktr.ee/alice", "Linktree", "n", 10), (2, "https://alice.example", "", "", 11),
        (3, "https://bob.example", "Bob", "", 12), (4, "https://loose.example", "", "to sort", 13)]
    assert ties(conn) == [("https://alice.example", "Alice", 1, 11), ("https://bob.example", "Bob", 1, 12),
                          ("https://linktr.ee/alice", "Alice", 2, 15)]
    assert conn.execute("SELECT 1 FROM meta WHERE key = 'userdata_stale'").fetchone() is None
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name IN ('links_28', 'links_person')").fetchone() is None
    # A link or a person deleted takes its ties with it; a URL is still saved once.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("DELETE FROM people WHERE id = 2")
    conn.execute("DELETE FROM links WHERE id = 1")
    assert ties(conn) == [("https://alice.example", "Alice", 1, 11)]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO links(url, created_at) VALUES ('https://alice.example', 1)")


def test_links_kept_apart_for_two_people_become_one(tmp_path, monkeypatch, capsys):
    """Migration 30: the links 28 kept apart because they were two people's
    (one URL once cleaned) are merged into the oldest, with both people,
    each keeping their own place and tied_at; title kept (else the
    other's), notes joined as 28 joins them, the clean URL given to the
    link kept. Notes too long joined: kept apart still."""
    long_a, long_b = "a" * 3000, "b" * 3000
    path = links_at_27(tmp_path, monkeypatch, [
        ("https://bob.example/?utm_source=a", "", "Alice's", 1, 3, 1, 1),
        ("https://bob.example", "Bob's site", "Bob's", 2, 2, 2, 4),
        ("https://www.tiktok.com/@band?_t=x", "Band", "same", 1, 1, 5, 5),
        ("https://www.tiktok.com/@band", "", "same", 2, 1, 6, 6),
        ("https://notes.example/?fbclid=1", "", long_a, 1, 2, 60, 60),
        ("https://notes.example", "", long_b, 2, 3, 61, 61),
        ("https://alone.example", "", "", 1, 4, 70, 70),
    ])
    migrate_to(path, monkeypatch, 28)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("DELETE FROM meta WHERE key = 'userdata_stale'")       # 28's, read already
    assert len(all_links(conn)) == 7                                    # all kept apart by 28
    conn.close()
    capsys.readouterr()
    db.migrate(path)
    assert ("[db] Links: 2 merged into an older link with the same address, with their people, 2 of them given "
            "the clean address, 1 kept apart (notes too long joined)") in capsys.readouterr().out
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT url, title, notes, created_at FROM links ORDER BY created_at").fetchall() == [
        ("https://bob.example", "Bob's site", "Alice's\n\nBob's", 1),
        ("https://www.tiktok.com/@band", "Band", "same", 5),
        ("https://notes.example/?fbclid=1", "", long_a, 60),
        ("https://notes.example", "", long_b, 61),
        ("https://alone.example", "", "", 70),
    ]
    assert ties(conn) == [
        ("https://alone.example", "Alice", 4, 70),
        ("https://bob.example", "Alice", 3, 1), ("https://bob.example", "Bob", 2, 4),
        ("https://notes.example", "Bob", 3, 61), ("https://notes.example/?fbclid=1", "Alice", 2, 60),
        ("https://www.tiktok.com/@band", "Alice", 1, 5), ("https://www.tiktok.com/@band", "Bob", 1, 6),
    ]
    assert conn.execute("SELECT value FROM meta WHERE key = 'userdata_stale'").fetchone() == ("links",)


def test_a_person_both_links_had_keeps_the_kept_links_tie(tmp_path, monkeypatch):
    """_merge_links_30 on two links of one person (28 kept them apart for
    their notes, which a hand-edited file could then shorten): the person
    keeps the place and tied_at of the link kept, once."""
    path = links_at_27(tmp_path, monkeypatch, [])
    migrate_to(path, monkeypatch, 30)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("INSERT INTO links(id, url, title, notes, created_at) VALUES (1, 'https://a.example', '', 'x', 1), "
                 "(2, 'https://a.example/?utm_source=x', 'A', 'y', 2)")
    conn.execute("INSERT INTO link_people(link_id, person_id, position, tied_at) VALUES (1, 1, 5, 1), (2, 1, 7, 2), "
                 "(2, 2, 1, 3)")
    conn.execute("BEGIN")
    assert db._merge_links_30(conn, 1, 2) is True
    conn.execute("COMMIT")
    assert conn.execute("SELECT id, url, title, notes FROM links").fetchall() == [(1, "https://a.example", "A", "x\n\ny")]
    assert ties(conn) == [("https://a.example", "Alice", 5, 1), ("https://a.example", "Bob", 1, 3)]


def test_two_links_of_one_person_merged_keep_the_tie_given_first(tmp_path, monkeypatch):
    """Migration 30, three links of one URL: K Alice's, O and D Bob's (28
    kept O and D apart from K). O comes into K with Bob's tie; D's Bob tie
    then meets it: the one given first (D's, moved to Bob before O was)
    is kept, with its place; nothing of it is lost."""
    path = links_at_27(tmp_path, monkeypatch, [
        ("https://band.example", "K", "", 1, 1, 1, 1),
        ("https://band.example/?utm_source=a", "", "", 2, 5, 2, 9),
        ("https://band.example/?fbclid=b", "", "", 2, 2, 3, 3),
    ])
    migrate_to(path, monkeypatch, 28)
    conn = sqlite3.connect(path)
    assert len(all_links(conn)) == 3                                    # all kept apart by 28
    conn.close()
    db.migrate(path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT url, title FROM links").fetchall() == [("https://band.example", "K")]
    assert ties(conn) == [("https://band.example", "Alice", 1, 1), ("https://band.example", "Bob", 2, 3)]


def test_links_json_from_before_30_restores_into_the_new_tables(tmp_path, monkeypatch):
    """A links.json written before migration 30 (person, position, tied_at
    per link) is read into link_people; the next write has the new shape."""
    import json
    import userdata
    path = str(tmp_path / "feedvault.db")
    db.migrate(path)
    data = tmp_path / "data"
    (data / "userdata").mkdir(parents=True)
    old = data / "userdata" / "links.json"
    old.write_text(json.dumps({"version": 1, "rows": [
        {"url": "https://a.example", "title": "A", "notes": "", "person": "Alice", "position": 2,
         "created_at": 5, "tied_at": 7, "id": 3},
        {"url": "https://b.example", "title": "", "notes": "n", "person": None, "position": None,
         "created_at": 6, "tied_at": None, "id": 4},
    ]}))
    conn = sqlite3.connect(path)
    userdata.restore_all(conn, str(data))
    assert conn.execute("SELECT id, url, title, notes, created_at FROM links ORDER BY id").fetchall() == [
        (3, "https://a.example", "A", "", 5), (4, "https://b.example", "", "n", 6)]
    assert ties(conn) == [("https://a.example", "Alice", 2, 7)]
    userdata.export(conn, "links", str(data))
    assert json.loads(old.read_text())["rows"] == [
        {"url": "https://a.example", "title": "A", "notes": "", "people": [{"name": "Alice", "position": 2, "tied_at": 7}],
         "created_at": 5, "id": 3},
        {"url": "https://b.example", "title": "", "notes": "n", "people": [], "created_at": 6, "id": 4},
    ]


def test_a_persons_profiles_leave_their_links(tmp_path, monkeypatch, capsys):
    """Migration 31: each person's links that are one of their accounts or
    sources (by platform and handle, whatever the spelling) are untied from
    them only; another person's tie to the same link, another person's link
    to the same profile, a link of no one and another site stay (#217)."""
    path = links_at_27(tmp_path, monkeypatch, [
        ("https://www.instagram.com/Alice.IG/?igsh=x", "", "", 1, 1, 1, 1),    # her account
        ("https://m.tiktok.com/@alice/", "", "", 1, 2, 2, 2),                  # her source, and Bob's link too
        ("https://tiktok.com/@alice?lang=en", "", "", 2, 1, 3, 3),             # Bob's only (28 cleans it)
        ("https://alice.example", "", "", 1, 3, 4, 4),
        ("https://instagram.com/alice.ig", "", "", None, None, 5, None),       # no one's
    ])
    migrate_to(path, monkeypatch, 30)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("DELETE FROM meta WHERE key = 'userdata_stale'")
    conn.execute("INSERT INTO link_people(link_id, person_id, position, tied_at) VALUES (2, 2, 2, 6)")
    conn.execute("INSERT INTO person_accounts(person_id, platform, author_id, at) VALUES (1, 'instagram', 'alice.ig', 1)")
    conn.execute("INSERT INTO sources(person_id, platform, tool, target, folder, created_at) "
                 "VALUES (1, 'tiktok', 'yt-dlp', 'https://tiktok.com/@Alice', '', 1)")
    conn.close()
    capsys.readouterr()
    db.migrate(path)
    assert ("[db] Links: 2 taken out of the Links of 1 people, as they are their accounts or sources already"
            in capsys.readouterr().out)
    conn = sqlite3.connect(path)
    assert ties(conn) == [("https://alice.example", "Alice", 3, 4), ("https://m.tiktok.com/@alice/", "Bob", 2, 6),
                          ("https://tiktok.com/@alice", "Bob", 1, 3)]
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 5               # none deleted
    assert conn.execute("SELECT value FROM meta WHERE key = 'userdata_stale'").fetchone() == ("links",)
    # Again: nothing left to take.
    capsys.readouterr()
    conn.execute("BEGIN")
    db._migrate_31(conn)
    conn.execute("COMMIT")
    assert "[db] Links:" not in capsys.readouterr().out
