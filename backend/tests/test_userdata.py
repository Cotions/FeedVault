import json
import os
import time

from conftest import H
from fakes import owner, write_post

import db
import scanner
import userdata

ALICE = owner("alice.example", 111, "Alice Example")


def data_dir(env):
    return str(env["tmp"] / "data")


def two_kept(env):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    scanner.scan(env["roots"])
    conn = db.connect()
    db.set_decision(conn, ["instagram:P1", "instagram:P2"], "keep", 7)
    return conn


def wipe(conn):
    conn.execute("DELETE FROM decisions")
    conn.commit()


def kept(conn):
    return [tuple(r) for r in conn.execute("SELECT post_id, decision, at FROM decisions ORDER BY post_id")]


def test_round_trip(env):
    conn = two_kept(env)
    assert userdata.export(conn, "decisions", data_dir(env)) == 2
    path = userdata.path(data_dir(env), "decisions")
    assert path.endswith(os.path.join("data", "userdata", "decisions.json"))
    with open(path) as f:
        doc = json.load(f)
    assert doc == {"version": 1, "rows": [
        {"post_id": "instagram:P1", "decision": "keep", "at": 7},
        {"post_id": "instagram:P2", "decision": "keep", "at": 7},
    ]}
    assert not os.path.exists(path + ".tmp")
    wipe(conn)
    assert userdata.load(conn, "decisions", data_dir(env)) == 2
    assert kept(conn) == [("instagram:P1", "keep", 7), ("instagram:P2", "keep", 7)]


def test_import_skipped_when_table_has_rows(env):
    conn = two_kept(env)
    userdata.export(conn, "decisions", data_dir(env))
    db.set_decision(conn, ["instagram:P2"], None, 8)
    assert userdata.load(conn, "decisions", data_dir(env)) == 0      # never merges into live data
    assert kept(conn) == [("instagram:P1", "keep", 7)]


def test_old_decisions_json_is_read(env):
    conn = two_kept(env)
    wipe(conn)
    with open(os.path.join(data_dir(env), "decisions.json"), "w") as f:
        json.dump({"version": 1, "decisions": [
            {"post_id": "instagram:P1", "decision": "keep", "at": 3},
            {"decision": "keep", "at": 3},                            # no key: skipped
        ]}, f)
    userdata.restore_all(conn, data_dir(env))
    assert kept(conn) == [("instagram:P1", "keep", 3)]
    with open(userdata.path(data_dir(env), "decisions")) as f:              # new file written at once
        assert [r["post_id"] for r in json.load(f)["rows"]] == ["instagram:P1"]
    assert os.path.exists(os.path.join(data_dir(env), "decisions.json"))   # old one left alone
    # the new file wins once it exists
    wipe(conn)
    db.set_decision(conn, ["instagram:P2"], "keep", 9)
    userdata.export(conn, "decisions", data_dir(env))
    wipe(conn)
    userdata.restore_all(conn, data_dir(env))
    assert kept(conn) == [("instagram:P2", "keep", 9)]


def test_first_start_after_upgrade_writes_the_file(env):
    conn = two_kept(env)
    path = userdata.path(data_dir(env), "decisions")
    assert not os.path.exists(path)
    userdata.restore_all(conn, data_dir(env))
    with open(path) as f:
        assert len(json.load(f)["rows"]) == 2


def test_corrupt_file_is_logged_and_ignored(env, capsys):
    conn = two_kept(env)
    wipe(conn)
    path = userdata.path(data_dir(env), "decisions")
    os.makedirs(os.path.dirname(path))
    for bad in ('{"version": 1, "rows": [', '{"rows": "nope"}', "[1, 2]"):
        with open(path, "w") as f:
            f.write(bad)
        userdata.restore_all(conn, data_dir(env))                    # does not raise
        assert kept(conn) == []
        assert "[userdata] Skipped decisions" in capsys.readouterr().out
        assert not os.path.exists(path)                              # moved aside, not overwritten
        aside = [f for f in os.listdir(os.path.dirname(path)) if f.startswith("decisions.json.corrupt-")]
        assert len(aside) == 1
        with open(os.path.join(os.path.dirname(path), aside[0])) as f:
            assert f.read() == bad
        os.remove(os.path.join(os.path.dirname(path), aside[0]))


def test_review_writes_file_after_a_pause(env, client, monkeypatch):
    monkeypatch.setattr(userdata, "DELAY", 0.05)
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scanner.scan(env["roots"])
    client.post("/api/review", json={"posts": ["instagram:P1"], "decision": "keep"}, headers=H)
    path = userdata.path(data_dir(env), "decisions")
    for _ in range(100):
        if os.path.exists(path):
            break
        time.sleep(0.02)
    with open(path) as f:
        assert [r["post_id"] for r in json.load(f)["rows"]] == ["instagram:P1"]
    # deleting the post drops its decision, and the file follows
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    for _ in range(100):
        with open(path) as f:
            if json.load(f)["rows"] == []:
                break
        time.sleep(0.02)
    with open(path) as f:
        assert json.load(f)["rows"] == []


def test_flush_writes_pending_changes_now(env, monkeypatch):
    monkeypatch.setattr(userdata, "DELAY", 60)
    two_kept(env)
    userdata.changed("decisions")
    userdata.flush()
    with open(userdata.path(data_dir(env), "decisions")) as f:
        assert len(json.load(f)["rows"]) == 2
    assert userdata._timers == {}


# ---------------------------------------------------------------------------
# #159: ids kept across a rebuild, and nothing removed comes back
# ---------------------------------------------------------------------------

USER_TABLES = ("post_tags", "tags", "collection_posts", "collections", "links", "person_accounts",
               "muted_people", "sources", "people")


def _rebuild(conn, base):
    """Every file written, the user tables emptied (feedvault.db deleted),
    and read back as at startup."""
    for name in userdata.REGISTRY:
        userdata.export(conn, name, base)
    with conn:
        for t in USER_TABLES:
            conn.execute(f"DELETE FROM {t}")
    userdata.restore_all(conn, base)


def _ids(conn, table, column):
    return {r[1]: r[0] for r in conn.execute(f"SELECT id, {column} FROM {table}")}


def test_ids_come_back_after_a_rebuild(env, client):
    # Inserted in the files' key order (name, URL), each row got a new id:
    # /people/<id> and /collections/<id> bookmarks opened the wrong one.
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scanner.scan(env["roots"])
    conn = db.connect()
    for name in ("Zed", "Gone", "Amy"):
        assert client.post("/api/people", json={"name": name}, headers=H).status_code == 200
    gone = _ids(conn, "people", "name")["Gone"]
    assert client.delete(f"/api/people/{gone}", headers=H).status_code == 200    # a gap in the ids
    for name in ("zeta", "alpha"):
        assert client.post("/api/collections", json={"name": name}, headers=H).status_code == 200
    for tag in ("zz", "aa"):
        client.post("/api/tags/apply", json={"posts": ["instagram:P1"], "add": [tag]}, headers=H)
    for url in ("https://z.example/", "https://a.example/"):
        assert client.post("/api/links", json={"url": url}, headers=H).status_code == 200
    tables = (("people", "name"), ("collections", "name"), ("tags", "name"), ("links", "url"))
    before = {t: _ids(conn, t, c) for t, c in tables}
    assert before["people"] == {"Zed": 1, "Amy": 3}
    _rebuild(conn, data_dir(env))
    assert {t: _ids(conn, t, c) for t, c in tables} == before
    assert client.get("/api/people/3", headers=H).get_json()["name"] == "Amy"
    # and a person made after it does not take an id in use
    r = client.post("/api/people", json={"name": "New"}, headers=H).get_json()
    assert r["person"]["id"] == 4


def test_a_file_without_ids_or_with_bad_ones_still_loads_every_row(env):
    # Files written before ids were kept, or edited by hand: no row is lost.
    base = data_dir(env)
    os.makedirs(os.path.join(base, "userdata"), exist_ok=True)
    rows = [{"name": "b", "created_at": 1, "id": 7}, {"name": "a", "created_at": 1},
            {"name": "c", "created_at": 1, "id": 7}, {"name": "d", "created_at": 1, "id": "9"},
            {"name": "e", "created_at": 1, "id": True}, {"name": "f", "created_at": 1, "id": 2**70},
            {"name": "g", "created_at": 1, "id": 3}]
    with open(userdata.path(base, "tags"), "w") as f:
        json.dump({"version": 1, "rows": rows}, f)
    conn = db.connect()
    assert userdata.load(conn, "tags", base) == 7
    ids = _ids(conn, "tags", "name")
    assert (ids["g"], ids["b"]) == (3, 7)
    assert sorted(ids[n] for n in "acdef") == [8, 9, 10, 11, 12]      # new ones, above those kept


def _rows(env, name):
    with open(userdata.path(data_dir(env), name)) as f:
        return json.load(f)["rows"]


def test_removing_the_last_row_writes_the_file_at_once(env, client, monkeypatch):
    # The file was written 2 s after the last change: FeedVault quit (or
    # killed) within those, the file still had the rows, and the next start
    # read them back into the empty table.
    monkeypatch.setattr(userdata, "DELAY", 3600)     # a change waits; only an emptied table is written now
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scanner.scan(env["roots"])
    conn = db.connect()
    client.post("/api/tags/apply", json={"posts": ["instagram:P1"], "add": ["a", "b"]}, headers=H)
    pid = client.post("/api/people", json={"name": "Alice"}, headers=H).get_json()["person"]["id"]
    cid = client.post("/api/collections", json={"name": "best"}, headers=H).get_json()["collection"]["id"]
    client.post(f"/api/collections/{cid}/add", json={"posts": ["instagram:P1"]}, headers=H)
    userdata.flush()
    assert [r["name"] for r in _rows(env, "tags")] == ["a", "b"]
    assert [len(_rows(env, n)) for n in ("people", "collections", "collection_posts")] == [1, 1, 1]

    client.post("/api/tags/delete", json={"name": "a"}, headers=H)      # one left: written later
    assert [r["name"] for r in _rows(env, "tags")] == ["a", "b"]
    client.post("/api/tags/delete", json={"name": "b"}, headers=H)
    assert client.delete(f"/api/people/{pid}", headers=H).status_code == 200
    assert client.post(f"/api/collections/{cid}/delete", json={}, headers=H).status_code == 200
    # Killed now: what was pending is never written.
    with userdata._lock:
        for timer in userdata._timers.values():
            timer.cancel()
        userdata._timers.clear()
    for name in ("tags", "post_tags", "people", "collections", "collection_posts"):
        assert _rows(env, name) == [], name
    userdata.restore_all(conn, data_dir(env))        # the next start: the tables are empty
    for table in ("tags", "post_tags", "people", "collections", "collection_posts"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
