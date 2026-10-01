import os

from conftest import H
from fakes import gallery_dl_case, owner, png, write_post

import config
import db
import scanner
import userdata
from test_duplicates import hashed_copies, listing

ALICE = owner("alice.example", 111, "Alice Example")
BOB = owner("bob.example", 222, "Bob Example")
TS = 1717243200


def archive(env):
    """Alice on Instagram (two posts with metadata, one rebuilt from its file
    name in the same folder), Bob on Instagram, and an X and a TikTok
    account from the gallery-dl fixtures."""
    write_post(env["media"] / "alice.example", "A1", TS, ALICE, "image")
    write_post(env["media"] / "alice.example", "A2", TS + 100, ALICE, "carousel")
    png(env["media"] / "alice.example" / "alice.example-2024-06-03-AAAAAAAAAA3.jpg")
    write_post(env["media"] / "bob", "B1", TS + 200, BOB, "image")
    gallery_dl_case("twitter/photo", env["media"] / "twitter" / "example_user1")
    gallery_dl_case("tiktok/video", env["media"] / "tiktok" / "example_user6")
    scanner.scan(env["roots"])


def get(client, url, status=200):
    r = client.get(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def post(client, url, body, status=200):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def account(client, platform, handle):
    return next(a for a in get(client, "/api/authors") if a["platform"] == platform and a["handle"] == handle)


def ref(a):
    return {"platform": a["platform"], "id": a["id"]}


def create(client, name, *accounts, status=200):
    return post(client, "/api/people", {"name": name, "accounts": [ref(a) for a in accounts]}, status)


def ids(client, query):
    """Post ids of a filter, checking /api/posts and its summary agree."""
    r = get(client, f"/api/posts?limit=200&{query}")
    assert get(client, f"/api/posts/summary?{query}")["posts"] == r["total"]
    return sorted(p["id"] for p in r["posts"])


def links():
    return sorted(tuple(r) for r in db.connect().execute(
        "SELECT p.name, pa.platform, pa.author_id FROM person_accounts pa JOIN people p ON p.id = pa.person_id"))


# ---------------------------------------------------------------------------
# Accounts and aliases
# ---------------------------------------------------------------------------

def test_folder_name_is_an_alias_of_the_numeric_id(env, client):
    archive(env)
    authors = get(client, "/api/authors")
    alice = [a for a in authors if a["platform"] == "instagram" and a["handle"] == "alice.example"]
    assert len(alice) == 1                                         # one account, not two
    [a] = alice
    assert (a["id"], a["aliases"], a["count"]) == ("111", ["alice.example"], 3)
    assert a["url"] == "https://www.instagram.com/alice.example/" and a["person"] is None
    # the author filter takes the alias in
    assert ids(client, "platform=instagram&author=111") == ["instagram:A1", "instagram:A2", "instagram:AAAAAAAAAA3"]
    # bytes add up both
    total = get(client, "/api/posts/summary?author=111")["bytes"]
    assert a["bytes"] == total > 0
    s = get(client, "/api/storage")
    row = next(r for r in s["by_author"] if r["id"] == "111")
    assert (row["posts"], row["bytes"], row["aliases"]) == (3, total, ["alice.example"])
    assert not any(r["id"] == "alice.example" for r in s["by_author"])


def test_no_alias_for_another_handle_or_two_candidates(env):
    # a filename post named after someone else, in a folder of Bob's posts
    write_post(env["media"] / "carol", "B1", TS, BOB, "image")
    png(env["media"] / "carol" / "carol-2024-06-03-CCCCCCCCCC1.jpg")
    scanner.scan(env["roots"])
    assert db.aliases(db.connect()) == {}
    # two accounts with the folder's handle: ambiguous, no alias
    write_post(env["media"] / "carol", "C2", TS, owner("carol", 333), "image")
    write_post(env["media"] / "carol", "C3", TS + 5, owner("carol", 444), "image")
    scanner.scan(env["roots"])
    assert db.aliases(db.connect()) == {}
    # the same in a folder of its own: one candidate
    write_post(env["media"] / "dave", "D1", TS, owner("Dave", 555), "image")
    png(env["media"] / "dave" / "dave-2024-06-03-DDDDDDDDDD1.jpg")
    scanner.scan(env["roots"])
    assert db.aliases(db.connect()) == {("instagram", "dave"): "555"}


# ---------------------------------------------------------------------------
# Create, link, unlink, merge, delete
# ---------------------------------------------------------------------------

def test_create_link_unlink(env, client):
    archive(env)
    alice, x = account(client, "instagram", "alice.example"), account(client, "twitter", "example_user1")
    tt = account(client, "tiktok", "example_user6")
    r = create(client, "  Alice   E ", alice, x)
    p = r["person"]
    assert r["ok"] and p["name"] == "Alice E" and p["notes"] == "" and p["platforms"] == ["instagram", "twitter"]
    assert [(a["platform"], a["id"]) for a in p["accounts"]] == [("instagram", "111"), ("twitter", "641286")]
    assert p["count"] == 4 and p["bytes"] == alice["bytes"] + x["bytes"]
    assert p["newest"] == max(alice["newest"], x["newest"])
    assert account(client, "instagram", "alice.example")["person"] == {"id": p["id"], "name": "Alice E"}
    assert get(client, "/api/people") == [p]
    assert get(client, f"/api/people/{p['id']}") == p

    r = post(client, f"/api/people/{p['id']}/accounts", {"add": [ref(tt)], "remove": [ref(x)]})
    assert (r["added"], r["removed"]) == (1, 1)
    assert r["person"]["platforms"] == ["instagram", "tiktok"]
    assert account(client, "twitter", "example_user1")["person"] is None
    # adding again changes nothing
    r = post(client, f"/api/people/{p['id']}/accounts", {"add": [ref(tt)]})
    assert (r["added"], r["removed"]) == (0, 0)

    r = post(client, f"/api/people/{p['id']}", {"name": "Alice", "notes": "the real one"})
    assert (r["person"]["name"], r["person"]["notes"]) == ("Alice", "the real one")
    r = client.delete(f"/api/people/{p['id']}", headers=H).get_json()
    assert r == {"ok": True, "unlinked": 2}
    assert get(client, "/api/people") == [] and links() == []
    assert get(client, "/api/posts?limit=1")["total"] == 6          # posts untouched


def test_linking_an_alias_links_the_account(env, client):
    archive(env)
    p = post(client, "/api/people", {"name": "A", "accounts": [{"platform": "instagram", "id": "alice.example"}]})
    assert [a["id"] for a in p["person"]["accounts"]] == ["111"]
    assert links() == [("A", "instagram", "111")]
    # removing it by its alias works too
    r = post(client, f"/api/people/{p['person']['id']}/accounts",
             {"remove": [{"platform": "instagram", "id": "alice.example"}]})
    assert r["removed"] == 1 and links() == []


def test_an_account_belongs_to_one_person(env, client):
    archive(env)
    alice, bob = account(client, "instagram", "alice.example"), account(client, "instagram", "bob.example")
    a = create(client, "A", alice)["person"]
    b = create(client, "B", bob, alice)["person"]                     # moved from A
    assert links() == [("B", "instagram", "111"), ("B", "instagram", "222")]
    assert get(client, f"/api/people/{a['id']}")["accounts"] == []
    assert len(b["accounts"]) == 2


def test_merge(env, client):
    archive(env)
    alice, x = account(client, "instagram", "alice.example"), account(client, "twitter", "example_user1")
    tt, bob = account(client, "tiktok", "example_user6"), account(client, "instagram", "bob.example")
    a = create(client, "A", alice)["person"]
    post(client, f"/api/people/{a['id']}", {"notes": "one"})
    b = create(client, "B", x)["person"]
    post(client, f"/api/people/{b['id']}", {"notes": "two"})
    r = post(client, "/api/people/merge", {"ids": [b["id"], a["id"]], "name": "A", "accounts": [ref(tt)]})
    m = r["person"]
    assert m["id"] == b["id"] and m["name"] == "A" and m["notes"] == "two\n\none"
    assert m["platforms"] == ["instagram", "tiktok", "twitter"]
    assert [p["id"] for p in get(client, "/api/people")] == [b["id"]]
    # a person and accounts only
    r = post(client, "/api/people/merge", {"ids": [b["id"]], "accounts": [ref(bob)]})
    assert len(r["person"]["accounts"]) == 4 and r["person"]["name"] == "A"


def test_bad_input(env, client):
    archive(env)
    alice = account(client, "instagram", "alice.example")
    assert client.get("/api/people").status_code == 403
    assert client.post("/api/people", json={"name": "A"}).status_code == 403
    assert client.delete("/api/people/1").status_code == 403
    create(client, "", alice, status=400)
    create(client, 'say "hi"', alice, status=400)
    post(client, "/api/people", {"name": "A", "accounts": "nope"}, 400)
    post(client, "/api/people", {"name": "A", "accounts": [{"platform": "instagram"}]}, 400)
    r = post(client, "/api/people", {"name": "A", "accounts": [{"platform": "instagram", "id": "999"}]}, 400)
    assert "unknown account" in r["error"]
    p = create(client, "A", alice)["person"]
    assert "exists" in create(client, "a", status=400)["error"]          # names ignore case
    q = create(client, "Q")["person"]                                    # no account yet is fine
    post(client, f"/api/people/{q['id']}", {"name": "A"}, 400)
    post(client, f"/api/people/{q['id']}", {}, 400)
    post(client, f"/api/people/{q['id']}", {"notes": 5}, 400)
    post(client, f"/api/people/{q['id']}", {"notes": "x" * 5001}, 400)
    post(client, f"/api/people/{q['id']}/accounts", {}, 400)
    post(client, f"/api/people/{q['id']}/accounts", {"add": [{"platform": "x", "id": "1"}]}, 400)
    post(client, "/api/people/999", {"name": "Z"}, 404)
    post(client, "/api/people/999/accounts", {"add": [ref(alice)]}, 404)
    get(client, "/api/people/999", 404)
    assert client.delete("/api/people/999", headers=H).status_code == 404
    post(client, "/api/people/merge", {"ids": [p["id"]]}, 400)            # nothing to merge
    post(client, "/api/people/merge", {"ids": [p["id"], 999]}, 404)
    post(client, "/api/people/merge", {"ids": ["1", "2"]}, 400)
    post(client, "/api/people/merge", {"ids": [p["id"], q["id"]], "name": ""}, 400)
    assert links() == [("A", "instagram", "111")]


# ---------------------------------------------------------------------------
# The person filter
# ---------------------------------------------------------------------------

def test_person_filter_agrees_everywhere(env, client):
    archive(env)
    alice, x = account(client, "instagram", "alice.example"), account(client, "twitter", "example_user1")
    pid = create(client, "A", alice, x)["person"]["id"]
    want = ["instagram:A1", "instagram:A2", "instagram:AAAAAAAAAA3", f"twitter:{get(client, '/api/posts?platform=twitter')['posts'][0]['post_id']}"]
    assert ids(client, f"person={pid}") == sorted(want)
    # one timeline, newest first, across platforms
    posts = get(client, f"/api/posts?person={pid}")["posts"]
    assert [p["posted_at"] for p in posts] == sorted((p["posted_at"] for p in posts), reverse=True)
    assert {p["platform"] for p in posts} == {"instagram", "twitter"}
    summary = get(client, f"/api/posts/summary?person={pid}")
    s = get(client, f"/api/storage?person={pid}")
    assert s["totals"] == summary
    assert sum(r["bytes"] for r in s["by_author"]) == summary["bytes"]
    assert {r["id"] for r in s["by_author"]} == {"111", x["id"]}
    assert all(r["person"] == {"id": pid, "name": "A"} for r in s["by_author"])
    assert {m["post"] for m in s["largest"]} <= set(want)
    assert sum(r["posts"] for r in s["by_kind"]) == sum(r["posts"] for r in s["by_year"]) == 4
    # combined with other filters
    assert ids(client, f"person={pid}&platform=twitter") == [want[3]]
    assert ids(client, f"person={pid}&review=unreviewed") == sorted(want)
    # nobody, or not an id: nothing
    assert ids(client, "person=999") == [] and ids(client, "person=abc") == []
    assert get(client, "/api/storage?person=999")["totals"] == {"posts": 0, "media": 0, "bytes": 0}
    # the unfiltered storage is unchanged
    assert get(client, "/api/storage")["totals"]["posts"] == 6


def test_person_filter_on_the_trash(env, client):
    archive(env)
    alice, bob = account(client, "instagram", "alice.example"), account(client, "instagram", "bob.example")
    pid = create(client, "A", alice)["person"]["id"]
    post(client, "/api/delete", {"posts": ["instagram:A1", "instagram:AAAAAAAAAA3", "instagram:B1"]})
    t = get(client, f"/api/trash/items?person={pid}")
    assert sorted(e["post"] for e in t["entries"]) == ["instagram:A1", "instagram:AAAAAAAAAA3"]
    assert t["total"] == 2 and get(client, "/api/trash/items")["total"] == 3
    assert get(client, "/api/trash/items?person=999")["total"] == 0
    post(client, "/api/trash/purge", {"filter": {"person": "x"}}, 400)
    r = post(client, "/api/trash/purge", {"filter": {"person": pid}})
    assert r["entries"] == 2
    assert [e["post"] for e in get(client, "/api/trash/items")["entries"]] == ["instagram:B1"]
    assert bob["id"] == "222"


# ---------------------------------------------------------------------------
# Links are by account: they survive rebuilds, trash and duplicates
# ---------------------------------------------------------------------------

def test_rebuild_restores_links(env, client):
    archive(env)
    alice, x = account(client, "instagram", "alice.example"), account(client, "twitter", "example_user1")
    pid = create(client, "Alice", alice, x)["person"]["id"]
    post(client, f"/api/people/{pid}", {"notes": "hello"})
    create(client, "Nobody yet")
    before = get(client, f"/api/posts?person={pid}")["total"]
    userdata.flush()
    path = config.db_path(config.load())
    db.init(str(env["tmp"] / "other.db"))                     # let go of the file
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db.init(path)                                              # a brand new index
    userdata.restore_all(db.connect(), config.load()["data_directory"])
    scanner.scan(env["roots"])
    people = get(client, "/api/people")
    assert [(p["name"], p["notes"], len(p["accounts"])) for p in people] == [("Alice", "hello", 2), ("Nobody yet", "", 0)]
    assert get(client, f"/api/posts?person={people[0]['id']}")["total"] == before == 4


def test_trash_and_restore_keep_the_person(env, client):
    archive(env)
    pid = create(client, "A", account(client, "instagram", "alice.example"))["person"]["id"]
    post(client, "/api/delete", {"posts": ["instagram:A1", "instagram:A2", "instagram:AAAAAAAAAA3"]})
    p = get(client, f"/api/people/{pid}")
    assert p["count"] == 0 and [(a["id"], a["count"]) for a in p["accounts"]] == [("111", 0)]
    post(client, "/api/trash/restore", {"posts": ["instagram:A1", "instagram:AAAAAAAAAA3"]})
    assert ids(client, f"person={pid}") == ["instagram:A1", "instagram:AAAAAAAAAA3"]
    assert get(client, f"/api/people/{pid}")["accounts"][0]["aliases"] == ["alice.example"]


def test_duplicate_promotion_keeps_the_person(env, client):
    hashed_copies(env, kind="image")
    pid = create(client, "A", account(client, "instagram", "alice.example"))["person"]["id"]
    g = listing(client)["groups"][0]
    copy = g["members"][1]
    r = post(client, "/api/duplicates/resolve", {"group": g["id"], "keep": copy["id"]})
    assert r["posts"] == ["instagram:P1"]
    assert ids(client, f"person={pid}") == ["instagram:P1"]
    assert get(client, f"/api/people/{pid}")["count"] == 1
