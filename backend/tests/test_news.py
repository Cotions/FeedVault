import json
import os
import time

from conftest import H
from fakes import owner, write_post

import config
import db
import news
import scanner
import userdata

TS = 1717243200


def seen(conn):
    return news.seen_at(conn)


def set_seen(conn, at):
    with conn:
        conn.execute("UPDATE seen_at SET at = ? WHERE id = 1", (at,))


def archive(env, n=2, handle="carol.cooks", uid=777):
    folder = env["media"] / handle
    for i in range(n):
        write_post(folder, f"OLD{handle[:4].upper()}{i:04d}", TS + i, owner(handle, uid))
    return folder


def new_count(client, **params):
    return client.get("/api/new", headers=H, query_string=params).get_json()


def ids(client, **params):
    r = client.get("/api/posts", headers=H, query_string=params).get_json()
    return sorted(p["id"] for p in r["posts"])


# ---------------------------------------------------------------------------
# The mark
# ---------------------------------------------------------------------------

def test_migration_seeds_now_on_a_database_in_use(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:13])
    db.init(path)
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO posts(id, platform, post_id, kind, saved_at, tool, meta_path, indexed_at) "
                     "VALUES ('instagram:P1', 'instagram', 'P1', 'image', 1, 'instaloader', '/a.json', 1)")
    monkeypatch.undo()
    before = int(time.time())
    db.init(path)
    conn = db.connect()
    assert before <= seen(conn) <= time.time()
    # What the index had before is not new.
    assert conn.execute("SELECT first_seen FROM posts").fetchone()[0] == 0
    assert news.summary(conn)["count"] == 0


def test_fresh_database_waits_for_the_restore_then_starts_at_now(env):
    conn = db.connect()
    assert seen(conn) is None
    data_dir = config.load()["data_directory"]
    userdata.restore_all(conn, data_dir)
    news.ensure(conn)
    assert abs(seen(conn) - time.time()) < 5
    userdata.export(conn, "seen_at", data_dir)
    # A rebuilt database gets the mark back from seen_at.json, not now.
    set_seen(conn, 1000)
    userdata.export(conn, "seen_at", data_dir)
    with conn:
        conn.execute("DELETE FROM seen_at")
    userdata.restore_all(conn, data_dir)
    news.ensure(conn)
    assert seen(conn) == 1000


def test_seen_at_round_trips_through_its_file(env):
    conn = db.connect()
    news.ensure(conn)
    set_seen(conn, 123456)
    data_dir = config.load()["data_directory"]
    assert userdata.export(conn, "seen_at", data_dir) == 1
    with open(userdata.path(data_dir, "seen_at")) as f:
        assert json.load(f) == {"version": 1, "rows": [{"id": 1, "at": 123456}]}
    with conn:
        conn.execute("DELETE FROM seen_at")
    assert userdata.load(conn, "seen_at", data_dir) == 1
    assert seen(conn) == 123456
    # Never over a mark already there.
    set_seen(conn, 200000)
    assert userdata.load(conn, "seen_at", data_dir) == 0 and seen(conn) == 200000


def test_mark_seen_never_moves_backwards(env, client):
    conn = db.connect()
    news.ensure(conn)
    set_seen(conn, 5000)

    def mark(body=None, status=200):
        r = client.post("/api/new/seen", headers=H, json=body)
        assert r.status_code == status, r.get_json()
        return r.get_json()

    assert mark({"at": 4000})["since"] == 5000
    assert mark({"at": 6000})["since"] == 6000
    now = mark()["since"]
    assert abs(now - time.time()) < 5
    # Not past now either: what is indexed later must still count.
    assert mark({"at": 2**52})["since"] <= time.time()
    for bad in ({"at": "1"}, {"at": -1}, {"at": True}, {"at": 1.5}, [1]):
        assert "error" in mark(bad, status=400)
    assert client.post("/api/new/seen", json={}).status_code == 403
    assert client.get("/api/new").status_code == 403


# ---------------------------------------------------------------------------
# What is new
# ---------------------------------------------------------------------------

def test_the_scan_that_builds_the_index_finds_nothing_new(env, client):
    archive(env)
    news.ensure(db.connect())
    set_seen(db.connect(), 1000)               # an old mark, restored with the user data
    scanner.scan(env["roots"])
    assert new_count(client)["count"] == 0


def test_rescan_of_existing_files_is_never_new(env, client):
    folder = archive(env)
    scanner.scan(env["roots"])
    news.ensure(db.connect())
    set_seen(db.connect(), 1000)
    # Files rewritten (mtime and size): the posts are updated, not added.
    for name in os.listdir(folder):
        if name.endswith(".json"):
            path = folder / name
            path.write_text(path.read_text() + " ")
            os.utime(path, (time.time(), time.time()))
    report = scanner.scan(env["roots"])
    assert report["updated"] == 2 and report["added"] == 0
    scanner.index_dirs(env["roots"], [str(folder)], new=True)
    assert new_count(client)["count"] == 0
    assert ids(client, new="1") == []


def test_a_media_root_just_added_is_not_new(env, client):
    """#13: its first scan builds its part of the index from nothing. A post
    that arrived meanwhile in a root already indexed is still new."""
    folder = archive(env)
    scanner.scan(env["roots"])
    conn = db.connect()
    news.ensure(conn)
    set_seen(conn, int(time.time()) - 10)
    other = env["tmp"] / "media2"              # shares the first root's name as a prefix
    for i in range(3):
        write_post(other / "dana.draws", f"OLDDANA{i:04d}", TS + i, owner("dana.draws", 888))
    write_post(folder, "NEWCAROL0001", TS + 100, owner("carol.cooks", 777))
    r = client.post("/api/config", json={"media_roots": [*env["roots"], str(other)]}, headers=H).get_json()
    assert r["ok"]
    deadline = time.monotonic() + 10
    while scanner.status()["running"] or scanner.status()["last"] is None:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert scanner.status()["last"]["added"] == 4
    assert ids(client, new="1") == ["instagram:NEWCAROL0001"]
    assert db.connect().execute("SELECT COUNT(*) FROM posts WHERE first_seen = 0").fetchone()[0] == 5
    # Later posts in the new root are new as usual.
    write_post(other / "dana.draws", "NEWDANA0001", TS + 100, owner("dana.draws", 888))
    scanner.scan([*env["roots"], str(other)])
    assert ids(client, new="1") == ["instagram:NEWCAROL0001", "instagram:NEWDANA0001"]


def test_a_root_added_while_stopped_is_not_new_whichever_scan_reads_it(env, client):
    """#13 audit: a root put in config.json by hand is first read by the
    Rescan button or by a job's full scan (scanner.run), not by the config
    endpoint. Either way its first scan builds its part of the index."""
    archive(env)
    scanner.scan(env["roots"])
    conn = db.connect()
    news.ensure(conn)
    set_seen(conn, int(time.time()) - 10)
    added = []
    for name, scan in (("media2", lambda roots: client.post("/api/scan", headers=H)),
                       ("media3", scanner.run)):
        root = env["tmp"] / name
        write_post(root / f"{name}.user", f"OLD{name.upper()}01", TS, owner(f"{name}.user", 900 + len(added)))
        added.append(str(root))
        cfg = config.load()
        cfg["media_roots"] = [*env["roots"], *added]
        config.save(cfg)
        scan(cfg["media_roots"])
        deadline = time.monotonic() + 10
        while scanner.status()["running"]:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert scanner.status()["last"]["added"] == 1
    assert new_count(client)["count"] == 0


def test_posts_indexed_after_the_mark_are_new(env, client, monkeypatch):
    archive(env)
    archive(env, 1, "dana.draws", 888)
    scanner.scan(env["roots"])
    conn = db.connect()
    news.ensure(conn)
    set_seen(conn, int(time.time()) - 10)
    # A sync's rescan (new=True) and a full scan both bring new posts.
    folder = env["media"] / "carol.cooks"
    write_post(folder, "NEWCAROL0001", TS + 100, owner("carol.cooks", 777), caption="fresh bread")
    write_post(folder, "NEWCAROL0002", TS + 101, owner("carol.cooks", 777))
    assert scanner.index_dirs(env["roots"], [str(folder)], new=True)["added"] == 2
    write_post(env["media"] / "dana.draws", "NEWDANA00001", TS + 102, owner("dana.draws", 888))
    # The full scan lands in a later second than the rescan, as it may on a
    # slow runner: Carol's newest is not every post's newest.
    later = int(time.time()) + 5
    with monkeypatch.context() as m:
        m.setattr(scanner, "time", type("Clock", (), {"time": staticmethod(lambda: later)}))
        assert scanner.scan(env["roots"])["added"] == 1
    # Restored from the trash or moved by Duplicates: there before, not new.
    write_post(folder, "BACKCAROL001", TS + 103, owner("carol.cooks", 777))
    scanner.index_dirs(env["roots"], [str(folder)])

    pid = client.post("/api/people", headers=H, json={"name": "Carol", "accounts": [
        {"platform": "instagram", "id": "777"}]}).get_json()["person"]["id"]
    r = new_count(client)
    assert r["count"] == 3 and r["since"] == seen(conn)
    # Carol's until is her own newest new post, not dana's later one.
    until = conn.execute("SELECT MAX(first_seen) FROM posts WHERE id IN "
                         "('instagram:NEWCAROL0001', 'instagram:NEWCAROL0002')").fetchone()[0]
    assert r["by_person"] == [{"id": pid, "name": "Carol", "count": 2, "until": until, "muted": False}]
    assert [(a["handle"], a["count"], a["person"]) for a in r["by_account"]] == \
        [("carol.cooks", 2, pid), ("dana.draws", 1, None)]
    new = ["instagram:NEWCAROL0001", "instagram:NEWCAROL0002", "instagram:NEWDANA00001"]
    assert ids(client, new="1") == new
    assert ids(client, new="1", person=str(pid)) == new[:2]
    assert ids(client, q="is:new") == new
    assert ids(client, q="IS:NEW bread") == ["instagram:NEWCAROL0001"]
    # Inside a tag name it is part of the name, not the filter.
    assert db.NEW not in db.post_filter('tag:"draft is:new ideas"')[0]
    assert db.parse_search('tag:"draft is:new ideas"') == ("", ["draft is:new ideas"])
    assert ids(client, new="1", review="unreviewed") == new
    summary = client.get("/api/posts/summary", headers=H, query_string={"new": "1"}).get_json()
    assert summary["posts"] == 3
    # The jobs poll carries the count and its newest post. Marking up to that
    # one leaves a post indexed since the count new.
    newest = conn.execute("SELECT MAX(first_seen) FROM posts").fetchone()[0]
    jobs = client.get("/api/jobs", headers=H).get_json()
    assert (jobs["new"], jobs["new_until"]) == (3, newest)
    mark = seen(conn)
    with conn:
        conn.execute("UPDATE posts SET first_seen = ? WHERE id IN (?, ?)", (mark + 1, *new[:2]))
        conn.execute("UPDATE posts SET first_seen = ? WHERE id = ?", (mark + 2, new[2]))
    assert client.get("/api/jobs", headers=H).get_json()["new_until"] == mark + 2
    assert client.post("/api/new/seen", headers=H, json={"at": mark + 1}).get_json()["since"] == mark + 1
    assert ids(client, new="1") == new[2:]
    # Marked seen: none is new any more, and the summary cache follows.
    assert client.post("/api/new/seen", headers=H, json={}).get_json()["ok"]
    assert new_count(client)["count"] == 0 and ids(client, new="1") == []
    assert client.get("/api/posts/summary", headers=H, query_string={"new": "1"}).get_json()["posts"] == 0


def test_alias_posts_count_for_their_account(env, client):
    """A folder of filename-only posts is an alias of the account id its
    metadata posts carry: on Creators they are one account, here too."""
    from fakes import write_filename_post
    folder = env["media"] / "carol.cooks"
    write_post(folder, "OLDCAROL0001", TS, owner("carol.cooks", 777))
    scanner.scan(env["roots"])
    news.ensure(db.connect())
    set_seen(db.connect(), int(time.time()) - 10)
    write_filename_post(folder, "carol.cooks", "CNAMEPOST01", TS + 5)
    scanner.index_dirs(env["roots"], [str(folder)], new=True)
    accounts = new_count(client)["by_account"]
    assert [(a["id"], a["count"]) for a in accounts] == [("777", 1)]


def test_mark_seen_while_a_scan_runs_leaves_the_folders_after_it_new(env, client, monkeypatch):
    archive(env)
    archive(env, 1, "dana.draws", 888)
    scanner.scan(env["roots"])
    conn = db.connect()
    news.ensure(conn)
    set_seen(conn, 1000)
    write_post(env["media"] / "carol.cooks", "NEWCAROL0001", TS + 100, owner("carol.cooks", 777))
    write_post(env["media"] / "dana.draws", "NEWDANA00001", TS + 101, owner("dana.draws", 888))
    # A clock that moves on every look, and "Mark all seen" clicked while
    # the scan is between carol.cooks (committed) and dana.draws.
    clock = [int(time.time()) - 1000]

    def tick():
        clock[0] += 10
        return clock[0]
    monkeypatch.setattr(scanner, "time", type("Clock", (), {"time": staticmethod(tick)}))
    parse_dir = scanner.parsers.parse_dir

    def parse(root, dirpath, names):
        if dirpath.endswith("dana.draws"):
            news.mark_seen(db.connect(), clock[0])
        return parse_dir(root, dirpath, names)
    monkeypatch.setattr(scanner.parsers, "parse_dir", parse)
    assert scanner.scan(env["roots"])["added"] == 2
    assert ids(client, new="1") == ["instagram:NEWDANA00001"]


# ---------------------------------------------------------------------------
# Per person and per account
# ---------------------------------------------------------------------------

def _two_creators(env, client):
    """carol.cooks (linked to Carol) and dana.draws (unlinked), each with
    two new posts. Returns (Carol's id, the mark)."""
    from fakes import write_filename_post
    archive(env)
    archive(env, 1, "dana.draws", 888)
    scanner.scan(env["roots"])
    conn = db.connect()
    news.ensure(conn)
    mark = int(time.time()) - 10
    set_seen(conn, mark)
    carol, dana = env["media"] / "carol.cooks", env["media"] / "dana.draws"
    write_post(carol, "NEWCAROL0001", TS + 100, owner("carol.cooks", 777))
    write_filename_post(carol, "carol.cooks", "CNAMEPOST01", TS + 5)      # an alias post of 777
    write_post(dana, "NEWDANA00001", TS + 101, owner("dana.draws", 888))
    write_post(dana, "NEWDANA00002", TS + 102, owner("dana.draws", 888))
    scanner.index_dirs(env["roots"], [str(carol), str(dana)], new=True)
    pid = client.post("/api/people", headers=H, json={"name": "Carol", "accounts": [
        {"platform": "instagram", "id": "777"}]}).get_json()["person"]["id"]
    return pid, mark


def mark(client, body, status=200):
    r = client.post("/api/new/seen", headers=H, json=body)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def test_mark_seen_per_person_and_per_account(env, client):
    pid, global_mark = _two_creators(env, client)
    r = new_count(client)
    assert r["count"] == 4
    assert [(p["id"], p["count"]) for p in r["by_person"]] == [(pid, 2)]
    carol = r["by_person"][0]
    newest = db.connect().execute("SELECT MAX(first_seen) FROM posts").fetchone()[0]
    assert carol["until"] == newest and all(a["until"] == newest for a in r["by_account"])
    # Carol's posts, the alias one included, stop being new; Dana's stay.
    out = mark(client, {"person": pid, "at": carol["until"]})
    assert out == {"ok": True, "since": global_mark, "at": carol["until"]}
    r = new_count(client)
    assert r["count"] == 2 and r["by_person"] == [] and [a["id"] for a in r["by_account"]] == ["888"]
    assert ids(client, new="1") == ["instagram:NEWDANA00001", "instagram:NEWDANA00002"]
    assert ids(client, new="1", person=str(pid)) == []
    assert client.get("/api/jobs", headers=H).get_json()["new"] == 2
    # An unlinked account counts as its own; never backwards.
    assert mark(client, {"account": {"platform": "instagram", "id": "888"}})["at"] >= newest
    assert new_count(client)["count"] == 0
    assert mark(client, {"account": {"platform": "instagram", "id": "888"}, "at": 5})["at"] == 5
    assert new_count(client)["count"] == 0
    # A post indexed later is new again, for that person too.
    write_post(env["media"] / "carol.cooks", "NEWCAROL0002", TS + 200, owner("carol.cooks", 777))
    scanner.index_dirs(env["roots"], [str(env["media"] / "carol.cooks")], new=True)
    conn = db.connect()
    with conn:                                 # indexed a second after the person's mark
        conn.execute("UPDATE posts SET first_seen = ? WHERE id = 'instagram:NEWCAROL0002'", (newest + 1,))
    assert [(p["id"], p["count"]) for p in new_count(client)["by_person"]] == [(pid, 1)]


def test_mark_seen_bodies(env, client):
    pid, _ = _two_creators(env, client)
    for bad in ({"person": pid, "account": {"platform": "instagram", "id": "888"}}, {"person": "1"},
                {"person": True}, {"person": 99999}, {"account": {"platform": "instagram", "id": "nobody"}},
                {"account": ["instagram", "888"]}, {"account": {"platform": "instagram"}}, {"who": 1}):
        assert "error" in mark(client, bad, status=400), bad
    # An alias names its account.
    assert mark(client, {"account": {"platform": "instagram", "id": "carol.cooks"}})["ok"]
    assert new_count(client)["by_person"] == []


def test_marks_are_user_data_and_survive_a_rebuild(env, client):
    pid, _ = _two_creators(env, client)
    at = mark(client, {"person": pid})["at"]
    conn = db.connect()
    data_dir = config.load()["data_directory"]
    for name in ("people", "person_accounts", "seen_at", "seen_marks"):
        userdata.export(conn, name, data_dir)
    with open(userdata.path(data_dir, "seen_marks")) as f:
        rows = json.load(f)["rows"]
    assert {(r["platform"], r["author_id"]) for r in rows} == {("instagram", "777"), ("instagram", "carol.cooks")}
    new = ids(client, new="1")
    assert new == ["instagram:NEWDANA00001", "instagram:NEWDANA00002"]
    # The index rebuilt from nothing: the marks come back from their files.
    path = config.db_path(config.load())
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db.init(path)
    conn = db.connect()
    userdata.restore_all(conn, data_dir)
    news.ensure(conn)
    assert conn.execute("SELECT COUNT(*) FROM seen_marks").fetchone()[0] == 2
    scanner.scan(env["roots"])                 # builds the index: nothing new
    assert new_count(client)["count"] == 0
    # Indexed at the person's mark (after the global one): Carol's are seen, Dana's new.
    with conn:
        conn.execute("UPDATE posts SET first_seen = ? WHERE id LIKE 'instagram:NEW%' OR id LIKE '%CNAMEPOST01'", (at,))
    assert ids(client, new="1") == new


def test_mark_all_seen_drops_the_marks_it_passes(env, client):
    pid, _ = _two_creators(env, client)
    mark(client, {"person": pid})
    assert db.connect().execute("SELECT COUNT(*) FROM seen_marks").fetchone()[0] == 2
    mark(client, {})
    assert db.connect().execute("SELECT COUNT(*) FROM seen_marks").fetchone()[0] == 0


def mute(client, body, status=200):
    r = client.post("/api/new/mute", headers=H, json=body)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def test_muted_ones_stay_out_of_the_global_count_but_not_their_own(env, client):
    pid, global_mark = _two_creators(env, client)
    dana = {"platform": "instagram", "id": "888"}
    assert mute(client, {"person": pid, "muted": True})["muted"] == {"people": [pid], "accounts": []}
    r = new_count(client)
    assert r["count"] == 2 and r["muted"] == {"people": [pid], "accounts": []}
    assert [(p["id"], p["count"], p["muted"]) for p in r["by_person"]] == [(pid, 2, True)]
    assert client.get("/api/jobs", headers=H).get_json()["new"] == 2
    assert ids(client, new="1") == ["instagram:NEWDANA00001", "instagram:NEWDANA00002"]
    assert len(ids(client, new="1", person=pid)) == 2            # her own page still shows them
    assert len(ids(client, new="1", author="777")) == 2
    # An unlinked account, and its folder-name alias.
    mute(client, {"account": dana, "muted": True})
    assert new_count(client)["count"] == 0 and ids(client, new="1") == []
    assert len(ids(client, new="1", author="888")) == 2
    # A linked account: mute the person.
    assert "Carol" in mute(client, {"account": {"platform": "instagram", "id": "777"}, "muted": True}, 400)["error"]
    # "Mark all seen" did not show them: they stay new on their own.
    mark(client, {})
    assert len(ids(client, new="1", person=pid)) == 2 and len(ids(client, new="1", author="888")) == 2
    # Until their own "Mark seen".
    until = next(p["until"] for p in new_count(client)["by_person"] if p["id"] == pid)
    mark(client, {"person": pid, "at": until})
    assert ids(client, new="1", person=pid) == []
    # Unmuted: what the global mark passed while muted counts again.
    mute(client, {"account": dana, "muted": False})
    assert new_count(client)["count"] == 2 and len(ids(client, new="1")) == 2
    # A second "Mark all seen" covers it now.
    mark(client, {})
    assert new_count(client)["count"] == 0
    for bad in ({"person": pid}, {"person": pid, "muted": 1}, {"muted": True}, {"person": pid, "account": dana,
                "muted": True}, {"person": 999, "muted": True}, {"person": pid, "muted": True, "x": 1}, [1]):
        mute(client, bad, 400)


def test_mutes_are_user_data_and_survive_a_rebuild(env, client):
    pid, _ = _two_creators(env, client)
    mute(client, {"person": pid, "muted": True})
    mute(client, {"account": {"platform": "instagram", "id": "888"}, "muted": True})
    conn = db.connect()
    data_dir = config.load()["data_directory"]
    for name in ("people", "person_accounts", "seen_at", "seen_marks", "muted_people", "muted_accounts"):
        userdata.export(conn, name, data_dir)
    with open(userdata.path(data_dir, "muted_people")) as f:
        assert [r["person"] for r in json.load(f)["rows"]] == ["Carol"]
    path = config.db_path(config.load())
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db.init(path)
    conn = db.connect()
    userdata.restore_all(conn, data_dir)
    news.ensure(conn)
    scanner.scan(env["roots"])
    pid = conn.execute("SELECT id FROM people WHERE name = 'Carol'").fetchone()[0]
    assert news.muted(conn) == {"people": [pid], "accounts": [{"platform": "instagram", "id": "888"}]}
    # A person deleted takes its mute with it.
    assert client.delete(f"/api/people/{pid}", headers=H).status_code == 200
    assert news.muted(conn)["people"] == []


def test_mute_edge_cases(env, client):
    pid, global_mark = _two_creators(env, client)
    dana = {"platform": "instagram", "id": "888"}
    # Muted while unlinked, then linked: it can still be unmuted.
    mute(client, {"account": dana, "muted": True})
    assert client.post(f"/api/people/{pid}/accounts", headers=H, json={"add": [dana]}).status_code == 200
    assert mute(client, {"account": dana, "muted": False})["muted"]["accounts"] == []
    # A merge keeps a muted person's mute on the one they join.
    other = client.post("/api/people", headers=H, json={"name": "Erin"}).get_json()["person"]["id"]
    mute(client, {"person": other, "muted": True})
    merged = client.post("/api/people/merge", headers=H, json={"ids": [pid, other], "accounts": []}).get_json()
    assert merged["ok"] and news.muted(db.connect())["people"] == [pid]
    # A muted account's own "Mark seen" never puts it below the global mark.
    mute(client, {"person": pid, "muted": False})
    client.post(f"/api/people/{pid}/accounts", headers=H, json={"remove": [dana]})
    mute(client, {"account": dana, "muted": True})
    mark(client, {"account": dana, "at": 0})
    assert len(ids(client, new="1", author="888")) == 2
    row = db.connect().execute("SELECT at FROM seen_marks WHERE author_id = '888'").fetchone()
    assert row[0] == global_mark
