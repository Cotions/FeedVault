import json

from conftest import H
from fakes import owner, write_post

import db
import scanner
import userdata

ALICE = owner("alice.example", 111, "Alice Example")
TS = 1717243200


def posts(env, n=4):
    for i in range(1, n + 1):
        write_post(env["media"] / "alice", f"P{i}", TS + i * 100, ALICE, "image")
    scanner.scan(env["roots"])


def create(client, name, status=200):
    r = client.post("/api/collections", json={"name": name}, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def call(client, cid, action, body=None, status=200):
    r = client.post(f"/api/collections/{cid}/{action}", json=body or {}, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def order(client, cid):
    r = client.get(f"/api/collections/{cid}?limit=200", headers=H).get_json()
    assert r["total"] == len(r["posts"])
    return [p["post_id"] for p in r["posts"]]


def ids(*n):
    return [f"instagram:P{i}" for i in n]


def test_create_add_remove_list(env, client):
    posts(env)
    c = create(client, "  Mood   board ")["collection"]
    assert (c["name"], c["count"], c["cover"], c["cover_post"]) == ("Mood board", 0, None, None)
    create(client, "MOOD BOARD", status=400)                               # names ignore case
    r = call(client, c["id"], "add", {"posts": ids(3, 1, 3, 9)})
    assert r["added"] == ids(3, 1)                                         # order kept, unknown and repeats skipped
    assert call(client, c["id"], "add", {"posts": ids(1, 2)})["added"] == ids(2)
    assert order(client, c["id"]) == ["P3", "P1", "P2"]
    listed = client.get("/api/collections", headers=H).get_json()
    assert [(x["name"], x["count"]) for x in listed] == [("Mood board", 3)]
    assert listed[0]["cover"]["url"].startswith("/media/")                  # the first post's
    full = client.get("/api/posts/instagram/P1", headers=H).get_json()
    assert full["collections"] == [{"id": c["id"], "name": "Mood board"}]
    assert call(client, c["id"], "remove", {"posts": ids(1, 4)})["removed"] == 1
    assert order(client, c["id"]) == ["P3", "P2"]
    # paging
    r = client.get(f"/api/collections/{c['id']}?offset=1&limit=1", headers=H).get_json()
    assert r["total"] == 2 and [p["post_id"] for p in r["posts"]] == ["P2"]


def test_rename_delete_and_bad_input(env, client):
    posts(env)
    a = create(client, "a")["collection"]["id"]
    b = create(client, "b")["collection"]["id"]
    assert client.get("/api/collections").status_code == 403
    assert client.post(f"/api/collections/{a}/add", json={"posts": ids(1)}).status_code == 403
    for name in ("", 'x"y', "x" * 65, 3, None):
        create(client, name, status=400)
    call(client, a, "rename", {"name": "B"}, status=400)                    # taken
    call(client, a, "rename", {"name": ""}, status=400)
    assert call(client, a, "rename", {"name": "A"})["collection"]["name"] == "A"   # own name, other case
    for bad in ({}, {"posts": []}, {"posts": "instagram:P1"}, {"posts": ids(1) * 5001}):
        call(client, a, "add", bad, status=400)
    call(client, 999, "add", {"posts": ids(1)}, status=404)
    assert client.get("/api/collections/999", headers=H).status_code == 404
    assert client.post(f"/api/collections/{a}/nope", json={}, headers=H).status_code == 404
    call(client, a, "add", {"posts": ids(1, 2)})
    assert call(client, a, "delete") == {"ok": True, "posts": 2}
    assert [c["id"] for c in client.get("/api/collections", headers=H).get_json()] == [b]
    assert client.get("/api/posts/instagram/P1", headers=H).get_json()["collections"] == []


def test_reorder_and_cover(env, client):
    posts(env)
    cid = create(client, "c")["collection"]["id"]
    call(client, cid, "add", {"posts": ids(1, 2, 3, 4)})
    call(client, cid, "order", {"posts": ids(4, 1, 2, 3)})                 # the whole collection
    assert order(client, cid) == ["P4", "P1", "P2", "P3"]
    call(client, cid, "order", {"posts": ids(2, 4, 9)})                    # two posts swap places
    assert order(client, cid) == ["P2", "P1", "P4", "P3"]
    call(client, cid, "cover", {"post": "instagram:P9"}, status=400)
    call(client, cid, "cover", {"post": 5}, status=400)
    c = call(client, cid, "cover", {"post": "instagram:P4"})["collection"]
    first = client.get("/api/posts/instagram/P4", headers=H).get_json()["cover"]
    assert c["cover_post"] == "instagram:P4" and c["cover"] == first
    call(client, cid, "remove", {"posts": ids(4)})                         # the cover goes with its post
    assert client.get(f"/api/collections/{cid}", headers=H).get_json()["collection"]["cover_post"] is None
    assert call(client, cid, "cover", {"post": None})["ok"]


def test_trash_restore_and_empty(env, client):
    posts(env)
    cid = create(client, "c")["collection"]["id"]
    call(client, cid, "add", {"posts": ids(1, 2)})
    call(client, cid, "cover", {"post": "instagram:P1"})
    client.post("/api/delete", json={"posts": ids(1)}, headers=H)
    c = client.get(f"/api/collections/{cid}", headers=H).get_json()
    assert c["total"] == 1 and c["collection"]["count"] == 1
    assert c["collection"]["cover"] == client.get("/api/posts/instagram/P2", headers=H).get_json()["cover"]
    client.post("/api/trash/restore", json={"posts": ids(1)}, headers=H)
    assert order(client, cid) == ["P1", "P2"]                               # back in its place
    assert client.get(f"/api/collections/{cid}", headers=H).get_json()["collection"]["cover_post"] == "instagram:P1"
    client.post("/api/delete", json={"posts": ids(1)}, headers=H)
    client.post("/api/trash/empty", headers=H)
    conn = db.connect()
    assert [r[0] for r in conn.execute("SELECT post_id FROM collection_posts")] == ["instagram:P2"]
    assert conn.execute("SELECT cover_post FROM collections").fetchone()[0] is None


def test_collections_survive_rebuilding_the_index(env, client):
    posts(env)
    a = create(client, "Alpha")["collection"]["id"]
    create(client, "Empty")
    call(client, a, "add", {"posts": ids(3, 1, 2)})
    call(client, a, "cover", {"post": "instagram:P1"})
    base = str(env["tmp"] / "data")
    conn = db.connect()
    for name in ("collections", "collection_posts"):
        userdata.export(conn, name, base)
    with open(userdata.path(base, "collection_posts")) as f:
        assert [(r["collection"], r["post_id"]) for r in json.load(f)["rows"]] == [
            ("Alpha", "instagram:P3"), ("Alpha", "instagram:P1"), ("Alpha", "instagram:P2")]

    db.init(str(env["tmp"] / "rebuilt.db"))
    userdata.restore_all(db.connect(), base)
    scanner.scan(env["roots"])
    listed = client.get("/api/collections", headers=H).get_json()
    assert [(c["name"], c["count"], c["cover_post"]) for c in listed] == [
        ("Alpha", 3, "instagram:P1"), ("Empty", 0, None)]
    assert order(client, listed[0]["id"]) == ["P3", "P1", "P2"]


# ---------------------------------------------------------------------------
# #22: the collection filter, reordering collections
# ---------------------------------------------------------------------------

def feed(client, query):
    r = client.get(f"/api/posts?{query}", headers=H).get_json()
    summary = client.get(f"/api/posts/summary?{query}", headers=H).get_json()
    assert summary["posts"] == r["total"]
    return sorted(p["post_id"] for p in r["posts"])


def test_feed_filter_by_collection(env, client):
    posts(env)
    a = create(client, "a")["collection"]["id"]
    b = create(client, "b")["collection"]["id"]
    call(client, a, "add", {"posts": ids(1, 3)})
    call(client, b, "add", {"posts": ids(3, 4)})
    assert feed(client, f"collection={a}") == ["P1", "P3"]
    assert feed(client, f"collection={a}&q=") == ["P1", "P3"]
    assert feed(client, f"collection={b}&review=unreviewed") == ["P3", "P4"]
    client.post("/api/review", json={"posts": ids(4), "decision": "keep"}, headers=H)
    assert feed(client, f"collection={b}&review=unreviewed") == ["P3"]
    client.post("/api/tags/apply", json={"posts": ids(3), "add": ["t"]}, headers=H)
    assert feed(client, f"collection={b}&tag=t") == ["P3"]
    assert feed(client, f"collection={b}&untagged=1") == ["P4"]
    assert feed(client, "collection=999") == [] and feed(client, "collection=x") == []
    assert feed(client, "collection=") == ["P1", "P2", "P3", "P4"]
    # A post trashed leaves the filter, and comes back to it.
    client.post("/api/delete", json={"posts": ids(1)}, headers=H)
    assert feed(client, f"collection={a}") == ["P3"]
    client.post("/api/trash/restore", json={"posts": ids(1)}, headers=H)
    assert feed(client, f"collection={a}") == ["P1", "P3"]


def names(client):
    return [c["name"] for c in client.get("/api/collections", headers=H).get_json()]


def reorder(client, body, status=200):
    r = client.post("/api/collections/reorder", json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def test_reorder_collections(env, client):
    c = {n: create(client, n)["collection"]["id"] for n in ("a", "b", "c", "d")}
    r = reorder(client, {"ids": [c["d"], c["a"], c["b"], c["c"]]})
    assert [x["name"] for x in r["collections"]] == ["d", "a", "b", "c"] == names(client)
    reorder(client, {"ids": [c["b"], c["d"], 999]})                       # two swap places
    assert names(client) == ["b", "a", "d", "c"]
    for bad in ({}, {"ids": []}, {"ids": "1"}, {"ids": ["1"]}, {"ids": [True]}):
        reorder(client, bad, status=400)
    assert client.post("/api/collections/reorder", json={"ids": [1]}).status_code == 403
    create(client, "e")                                                    # a new one goes last
    assert names(client)[-1] == "e"


def test_collection_order_survives_rebuilding_the_index(env, client):
    c = {n: create(client, n)["collection"]["id"] for n in ("a", "b", "c")}
    reorder(client, {"ids": [c["c"], c["a"], c["b"]]})
    base = str(env["tmp"] / "data")
    conn = db.connect()
    for name in ("collections", "collection_posts"):
        userdata.export(conn, name, base)
    db.init(str(env["tmp"] / "rebuilt.db"))
    userdata.restore_all(db.connect(), base)
    assert names(client) == ["c", "a", "b"]
