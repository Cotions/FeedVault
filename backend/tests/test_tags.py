import json
import shutil
import os
import time

from conftest import H
from fakes import owner, write_post

import db
import hashing
import scanner
import userdata
from test_duplicates import listing, two_folders

ALICE = owner("alice.example", 111, "Alice Example")
TS = 1717243200


def three_posts(env):
    write_post(env["media"] / "alice", "P1", TS, ALICE, "image", caption="red dress")
    write_post(env["media"] / "alice", "P2", TS + 100, ALICE, "video", caption="blue dress")
    write_post(env["media"] / "alice", "P3", TS + 200, ALICE, "carousel", caption="red shoes")
    scanner.scan(env["roots"])


def apply(client, posts, add=(), remove=(), status=200):
    r = client.post("/api/tags/apply", json={"posts": posts, "add": list(add), "remove": list(remove)}, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def tags(client):
    return {t["name"]: t["count"] for t in client.get("/api/tags", headers=H).get_json()}


def post_tags(client, pid="P1"):
    r = client.get(f"/api/posts/instagram/{pid}", headers=H)
    return r.get_json()["tags"] if r.status_code == 200 else None


def ids(client, query):
    r = client.get(f"/api/posts?{query}", headers=H).get_json()
    summary = client.get(f"/api/posts/summary?{query}", headers=H).get_json()
    assert summary["posts"] == r["total"]                     # counts and sizes always agree
    return sorted(p["post_id"] for p in r["posts"])


def wait_for(path, check):
    for _ in range(200):
        if os.path.exists(path):
            with open(path) as f:
                if check(json.load(f)["rows"]):
                    return
        time.sleep(0.02)
    raise AssertionError(f"{path} never matched")


# ---------------------------------------------------------------------------
# Apply, rename, merge, delete
# ---------------------------------------------------------------------------

def test_apply_and_remove(env, client):
    three_posts(env)
    r = apply(client, ["instagram:P1", "instagram:P2", "instagram:nope"], add=["Outfits", "  red   things "])
    assert r["ok"] and r["posts"] == ["instagram:P1", "instagram:P2"]
    assert (r["added"], r["removed"], r["created"]) == (4, 0, ["Outfits", "red things"])
    assert post_tags(client) == ["Outfits", "red things"]
    summary = client.get("/api/posts?limit=3", headers=H).get_json()["posts"]
    assert {p["post_id"]: p["tags"] for p in summary} == {
        "P1": ["Outfits", "red things"], "P2": ["Outfits", "red things"], "P3": []}
    # same name in another case is the same tag; adding twice changes nothing
    r = apply(client, ["instagram:P1"], add=["outfits"])
    assert (r["added"], r["created"]) == (0, [])
    assert tags(client) == {"Outfits": 2, "red things": 2}
    r = apply(client, ["instagram:P1"], add=["todo"], remove=["OUTFITS", "unknown"])
    assert (r["added"], r["removed"], r["created"]) == (1, 1, ["todo"])
    assert post_tags(client) == ["red things", "todo"]
    assert tags(client) == {"Outfits": 1, "red things": 2, "todo": 1}


def test_bad_input(env, client):
    three_posts(env)
    assert client.get("/api/tags").status_code == 403
    for path in ("/api/tags/apply", "/api/tags/rename", "/api/tags/delete"):
        assert client.post(path, json={}).status_code == 403
    for body in ({}, {"posts": [], "add": ["a"]}, {"posts": "instagram:P1", "add": ["a"]},
                 {"posts": ["instagram:P1"]}, {"posts": ["instagram:P1"], "add": []},
                 {"posts": ["instagram:P1"], "add": "a"}, {"posts": ["instagram:P1"], "add": [""]},
                 {"posts": ["instagram:P1"], "add": ['say "hi"']}, {"posts": ["instagram:P1"], "add": ["x" * 65]},
                 {"posts": ["instagram:P1"], "add": ["a\x01b"]}, {"posts": ["instagram:P1"], "remove": [3]},
                 {"posts": ["instagram:P1"] * 5001, "add": ["a"]}):
        r = client.post("/api/tags/apply", json=body, headers=H)
        assert r.status_code == 400 and r.get_json()["ok"] is False, body
    assert apply(client, ["instagram:P1"] * 5000, add=["a"])["posts"] == ["instagram:P1"]
    for body in ({}, {"from": "a"}, {"from": "a", "to": ""}, {"from": "a", "to": 'b"'}):
        assert client.post("/api/tags/rename", json=body, headers=H).status_code == 400
    assert client.post("/api/tags/rename", json={"from": "zz", "to": "b"}, headers=H).status_code == 404
    assert client.post("/api/tags/delete", json={"name": "zz"}, headers=H).status_code == 404
    assert client.post("/api/tags/delete", json={"name": 4}, headers=H).status_code == 404


def test_rename_and_merge(env, client):
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2"], add=["outfit"])
    apply(client, ["instagram:P2", "instagram:P3"], add=["Outfits"])
    r = client.post("/api/tags/rename", json={"from": "outfit", "to": "fits"}, headers=H).get_json()
    assert r == {"ok": True, "name": "fits", "merged": False}
    r = client.post("/api/tags/rename", json={"from": "FITS", "to": "outfits"}, headers=H).get_json()
    assert r == {"ok": True, "name": "Outfits", "merged": True}           # the existing spelling stays
    assert tags(client) == {"Outfits": 3}
    assert [post_tags(client, p) for p in ("P1", "P2", "P3")] == [["Outfits"]] * 3
    # only the case changes: a rename, not a merge with itself
    r = client.post("/api/tags/rename", json={"from": "outfits", "to": "OUTFITS"}, headers=H).get_json()
    assert r == {"ok": True, "name": "OUTFITS", "merged": False}
    assert tags(client) == {"OUTFITS": 3}


def test_delete(env, client):
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2"], add=["a", "b"])
    r = client.post("/api/tags/delete", json={"name": "A"}, headers=H).get_json()
    assert r == {"ok": True, "posts": 2}
    assert tags(client) == {"b": 2} and post_tags(client) == ["b"]


def test_changes_are_written_to_userdata(env, client, monkeypatch):
    monkeypatch.setattr(userdata, "DELAY", 0.05)
    three_posts(env)
    apply(client, ["instagram:P1"], add=["a"])
    base = str(env["tmp"] / "data")
    wait_for(userdata.path(base, "tags"), lambda rows: [r["name"] for r in rows] == ["a"])
    wait_for(userdata.path(base, "post_tags"), lambda rows: rows and rows[0]["tag"] == "a")
    client.post("/api/tags/rename", json={"from": "a", "to": "b"}, headers=H)
    wait_for(userdata.path(base, "post_tags"), lambda rows: [(r["post_id"], r["tag"]) for r in rows]
             == [("instagram:P1", "b")])


# ---------------------------------------------------------------------------
# Filters and search
# ---------------------------------------------------------------------------

def test_filters_and_summary_agree(env, client):
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2"], add=["dress"])
    apply(client, ["instagram:P1"], add=["red one"])
    assert ids(client, "tag=dress") == ["P1", "P2"]
    assert ids(client, "tag=DRESS&tag=red%20one") == ["P1"]               # several: all of them
    assert ids(client, "tag=dress&tag=nope") == []
    assert ids(client, "untagged=1") == ["P3"]
    assert ids(client, "untagged=1&tag=dress") == []
    assert ids(client, "tag=dress&kind=video") == ["P2"]
    assert ids(client, "tag=&untagged=0") == ["P1", "P2", "P3"]             # blank: no filter
    s = client.get("/api/posts/summary?tag=dress", headers=H).get_json()
    assert s["media"] == 2 and s["bytes"] > 0                              # a video's poster is not an item
    whole = client.get("/api/posts/summary?untagged=1", headers=H).get_json()
    assert whole["media"] == 2                                              # P3: a two-item carousel
    # the summary cache follows tag changes
    apply(client, ["instagram:P3"], add=["dress"])
    assert ids(client, "tag=dress") == ["P1", "P2", "P3"] and ids(client, "untagged=1") == []


def test_parse_search():
    assert db.parse_search("tag:outfits red dress") == ("red dress", ["outfits"])
    assert db.parse_search('red tag:"two  words" tag:b') == ("red", ["two words", "b"])
    assert db.parse_search('tag:"still typ') == ("", ["still typ"])
    assert db.parse_search("TAG:x") == ("", ["x"])
    assert db.parse_search("tag:") == ("", [])
    assert db.parse_search("hashtag:x") == ("hashtag:x", [])               # only a word of its own
    assert db.parse_search("plain words") == ("plain words", [])


def test_tag_search(env, client):
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2"], add=["two words"])
    apply(client, ["instagram:P1"], add=["x"])
    assert ids(client, "q=tag:%22two%20words%22") == ["P1", "P2"]
    assert ids(client, "q=tag:%22two%20words%22%20blue") == ["P2"]
    assert ids(client, "q=red%20tag:x") == ["P1"]
    assert ids(client, "q=tag:x&tag=two%20words") == ["P1"]
    assert ids(client, "q=tag:nope") == []
    assert ids(client, "q=tag:x%20!!!") == []                              # words that can match nothing


# ---------------------------------------------------------------------------
# Trash, restore, purge, rebuild, duplicates
# ---------------------------------------------------------------------------

def test_tags_survive_trash_and_restore(env, client):
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2"], add=["keepme"])
    assert client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H).get_json()["posts"] == ["instagram:P1"]
    assert tags(client) == {"keepme": 1}                                   # counts only indexed posts
    assert ids(client, "tag=keepme") == ["P2"]
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["posts"] == ["instagram:P1"]
    assert post_tags(client) == ["keepme"] and tags(client) == {"keepme": 2}


def test_emptying_the_trash_forgets_tags_of_posts_gone_for_good(env, client):
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2"], add=["t"])
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM post_tags").fetchone()[0] == 2
    client.post("/api/trash/empty", headers=H)
    assert [r[0] for r in conn.execute("SELECT post_id FROM post_tags")] == ["instagram:P2"]
    assert tags(client) == {"t": 1}


def test_purge_forgets_only_what_is_gone(env, client):
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2", "instagram:P3"], add=["t"])
    # P3: one item, then the rest in another batch: two entries
    media = client.get("/api/posts/instagram/P3", headers=H).get_json()["media"]
    client.post("/api/delete", json={"media": [media[0]["id"]]}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P1", "instagram:P3"]}, headers=H)
    entries = client.get("/api/trash/items", headers=H).get_json()["entries"]
    first_p3 = [e["key"] for e in entries if e["post"] == "instagram:P3" and e["partial"]]
    p1 = [e["key"] for e in entries if e["post"] == "instagram:P1"]
    r = client.post("/api/trash/purge", json={"keys": p1 + first_p3}, headers=H).get_json()
    assert r["entries"] == 2 and "forgotten" not in r
    conn = db.connect()
    # P1 is gone for good; P3 still has an entry in the trash; P2 is indexed
    assert sorted(r[0] for r in conn.execute("SELECT post_id FROM post_tags")) == ["instagram:P2", "instagram:P3"]
    client.post("/api/trash/restore", json={"posts": ["instagram:P3"]}, headers=H)
    assert post_tags(client, "P3") == ["t"]


def test_tags_survive_rebuilding_the_index(env, client):
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2"], add=["a", "B"])
    client.post("/api/tags/rename", json={"from": "a", "to": "Alpha"}, headers=H)
    base = str(env["tmp"] / "data")
    conn = db.connect()
    for name in ("tags", "post_tags"):
        userdata.export(conn, name, base)
    with open(userdata.path(base, "post_tags")) as f:
        rows = json.load(f)["rows"]
    assert {(r["post_id"], r["tag"]) for r in rows} == {
        ("instagram:P1", "Alpha"), ("instagram:P1", "B"), ("instagram:P2", "Alpha"), ("instagram:P2", "B")}
    # a tag missing from tags.json comes back from post_tags.json
    with open(userdata.path(base, "tags"), "w") as f:
        json.dump({"version": 1, "rows": [{"name": "Alpha", "color": None, "created_at": 5}]}, f)

    db.init(str(env["tmp"] / "rebuilt.db"))                               # a brand new index
    conn = db.connect()
    userdata.restore_all(conn, base)
    scanner.scan(env["roots"])
    assert tags(client) == {"Alpha": 2, "B": 2}
    assert post_tags(client, "P2") == ["Alpha", "B"] and post_tags(client, "P3") == []


def test_promoted_copy_keeps_the_tags(env, client):
    two_folders(env, kind="image")
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    apply(client, ["instagram:P1"], add=["kept tag"])
    g = listing(client)["groups"][0]
    copy = g["members"][1]
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": copy["id"]}, headers=H).get_json()
    assert r["ok"] and r["posts"] == ["instagram:P1"]
    post = client.get("/api/posts/instagram/P1", headers=H).get_json()
    assert post["source"]["meta_path"].startswith(str(env["media"] / "alicee"))
    assert post["tags"] == ["kept tag"]


def test_emptying_forgets_only_posts_whose_trash_was_deleted(env, client):
    """A post out of the index for another reason (its root offline, its id
    re-derived) keeps its tags: only posts purged just now are forgotten."""
    three_posts(env)
    apply(client, ["instagram:P1", "instagram:P2"], add=["t"])
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    conn = db.connect()
    with db.write_lock:
        db.remove_post(conn, "instagram:P2")
    client.post("/api/trash/empty", headers=H)
    assert [r[0] for r in conn.execute("SELECT post_id FROM post_tags")] == ["instagram:P2"]


def test_kept_repost_takes_the_tags_and_collections(env, client):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    b = write_post(env["media"] / "bob", "R9", TS + 99, owner("bob", 222), "image")
    shutil.copyfile(a + ".jpg", b + ".jpg")
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    apply(client, ["instagram:P1"], add=["mine"])
    apply(client, ["instagram:R9"], add=["recipes", "mine"])
    cid = client.post("/api/collections", json={"name": "Faves"}, headers=H).get_json()["collection"]["id"]
    client.post(f"/api/collections/{cid}/add", json={"posts": ["instagram:R9"]}, headers=H)
    [g] = listing(client, "content")["groups"]
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": "instagram:P1"}, headers=H).get_json()
    assert r["ok"] and r["posts"] == ["instagram:R9"] and "carried" not in r
    assert post_tags(client, "P1") == ["mine", "recipes"]
    full = client.get("/api/posts/instagram/P1", headers=H).get_json()
    assert full["collections"] == [{"id": cid, "name": "Faves"}]
    client.post("/api/trash/empty", headers=H)
    assert post_tags(client, "P1") == ["mine", "recipes"] and tags(client) == {"mine": 1, "recipes": 1}


def test_odd_filters_and_applies(env, client):
    three_posts(env)
    assert apply(client, ["instagram:NOPE"], add=["ghost"]) == {
        "ok": True, "posts": [], "added": 0, "removed": 0, "created": []}
    assert "ghost" not in tags(client)                                    # no tag made for no post
    assert ids(client, "tag=" + "x" * 70) == [] and ids(client, 'tag=a"b') == []   # not every post
    # NOCASE folds ASCII only: é and É are two tags, and both must match
    apply(client, ["instagram:P1", "instagram:P2"], add=["été"])
    apply(client, ["instagram:P1"], add=["Été"])
    assert tags(client) == {"été": 2, "Été": 1}
    assert ids(client, "tag=%C3%A9t%C3%A9&tag=%C3%89t%C3%A9") == ["P1"]
