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
