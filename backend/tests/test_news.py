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


def test_posts_indexed_after_the_mark_are_new(env, client):
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
    assert scanner.scan(env["roots"])["added"] == 1
    # Restored from the trash or moved by Duplicates: there before, not new.
    write_post(folder, "BACKCAROL001", TS + 103, owner("carol.cooks", 777))
    scanner.index_dirs(env["roots"], [str(folder)])

    pid = client.post("/api/people", headers=H, json={"name": "Carol", "accounts": [
        {"platform": "instagram", "id": "777"}]}).get_json()["person"]["id"]
    r = new_count(client)
    assert r["count"] == 3 and r["since"] == seen(conn)
    assert r["by_person"] == [{"id": pid, "name": "Carol", "count": 2}]
    assert [(a["handle"], a["count"], a["person"]) for a in r["by_account"]] == \
        [("carol.cooks", 2, pid), ("dana.draws", 1, None)]
    new = ["instagram:NEWCAROL0001", "instagram:NEWCAROL0002", "instagram:NEWDANA00001"]
    assert ids(client, new="1") == new
    assert ids(client, new="1", person=str(pid)) == new[:2]
    assert ids(client, q="is:new") == new
    assert ids(client, q="IS:NEW bread") == ["instagram:NEWCAROL0001"]
    assert ids(client, new="1", review="unreviewed") == new
    summary = client.get("/api/posts/summary", headers=H, query_string={"new": "1"}).get_json()
    assert summary["posts"] == 3
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
