import json
import os

from conftest import H
from fakes import gallery_dl_case, owner, png, write_meta, write_post

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
    # and the alias the whole account (links from a filename-only post use it)
    assert ids(client, "platform=instagram&author=alice.example") == ids(client, "platform=instagram&author=111")
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


def test_alias_ends_with_its_id_and_never_joins_two_people(env, client):
    archive(env)
    # every post with the id trashed: the folder name is an account again
    post(client, "/api/delete", {"posts": ["instagram:A1", "instagram:A2"]})
    folder = account(client, "instagram", "alice.example")
    assert (folder["id"], folder["count"]) == ("alice.example", 1)
    p = create(client, "A", folder)["person"]
    assert [(a["id"], a["count"]) for a in p["accounts"]] == [("alice.example", 1)]
    # the folder and the id linked to different people before they met
    (env["media"] / "erin").mkdir()
    png(env["media"] / "erin" / "erin-2024-06-03-EEEEEEEEEE1.jpg")
    write_post(env["media"] / "elsewhere", "E1", TS, owner("erin", 777), "image")
    scanner.scan(env["roots"])
    by_id = {a["id"]: a for a in get(client, "/api/authors")}
    create(client, "Folder", by_id["erin"])
    create(client, "Id", by_id["777"])
    write_post(env["media"] / "erin", "E2", TS + 5, owner("erin", 777), "image")
    scanner.scan(env["roots"])
    assert ("instagram", "erin") not in db.aliases(db.connect())
    shown = {p["name"]: [a["id"] for a in p["accounts"]] for p in get(client, "/api/people")}
    assert (shown["Folder"], shown["Id"]) == (["erin"], ["777"])


def filename_posts(folder, handle, *codes):
    folder.mkdir(parents=True, exist_ok=True)
    for i, code in enumerate(codes):
        png(folder / f"{handle}-2024-05-0{i + 1}-{code}.jpg")


def test_an_id_file_names_the_account_and_a_rename_splits_nothing(env, client):
    # Carol's first downloads: file names only, and instaloader's id file.
    old = env["media"] / "carol.cooks"
    filename_posts(old, "carol.cooks", "CCCCCCCCCC1", "CCCCCCCCCC2")
    (old / "id").write_text("333\n")
    scanner.scan(env["roots"])
    [folder] = get(client, "/api/authors")
    assert folder["id"] == "carol.cooks"                       # no metadata yet: the folder handle
    pid = create(client, "Carol", folder)["person"]["id"]
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO sources(platform, author_id, tool, target, folder, created_at) "
                     "VALUES ('instagram', 'carol.cooks', 'instaloader', 'carol.cooks', ?, 0)", (str(old),))
    # She renamed herself; the new handle's posts come with metadata, elsewhere.
    write_post(env["media"] / "carol.bakes", "N1", TS, owner("carol.bakes", 333, "Carol"), "image")
    write_post(env["media"] / "carol.bakes", "N2", TS + 50, owner("carol.bakes", 333, "Carol"), "image")
    scanner.scan(env["roots"])
    [a] = get(client, "/api/authors")                          # one account
    assert (a["id"], a["handle"], a["aliases"], a["count"]) == ("333", "carol.bakes", ["carol.cooks"], 4)
    # The link and the source moved to the id: a rebuild or a folder rename keeps them.
    assert links() == [("Carol", "instagram", "333")]
    assert conn.execute("SELECT author_id FROM sources").fetchone()[0] == "333"
    # One person, one feed, sorted by date.
    feed = get(client, f"/api/posts?person={pid}")["posts"]
    assert [p["id"] for p in feed] == ["instagram:N2", "instagram:N1", "instagram:CCCCCCCCCC2",
                                       "instagram:CCCCCCCCCC1"]
    userdata.flush()
    saved = json.load(open(userdata.path(config.load()["data_directory"], "person_accounts")))["rows"]
    assert [(r["author_id"], r["person"]) for r in saved] == [("333", "Carol")]
    # An id file of another account in the folder wins over a handle that matches.
    write_post(old, "X1", TS, owner("carol.cooks", 999), "image")
    scanner.scan(env["roots"])
    assert db.aliases(db.connect())[("instagram", "carol.cooks")] == "333"


def test_id_files_as_instaloader_writes_them(env):
    import parsers
    d = env["media"] / "f.one"
    d.mkdir()
    (d / "id").write_text("00123\n")
    (env["media"] / "f.two_id").write_text("456")
    (env["media"] / "big_id").write_text("1" * 40)
    (env["media"] / "text_id").write_text("abc")
    (env["media"] / "bad name_id").write_text("7")
    r = parsers.parse_dir(str(env["media"]), str(d), ["id"])
    assert [(a.author_id, a.handle) for a in r.account_files] == [("123", "f.one")] and r.claimed == {"id"}
    names = sorted(os.listdir(env["media"]))
    r = parsers.parse_dir(str(env["media"]), str(env["media"]), names)
    assert [(a.author_id, a.handle) for a in r.account_files] == [("456", "f.two")]
    scanner.scan(env["roots"])
    assert sorted(r[0] for r in db.connect().execute("SELECT author_id FROM account_files")) == ["123", "456"]
    os.remove(d / "id")
    scanner.index_dirs(env["roots"], [str(d)])
    assert [r[0] for r in db.connect().execute("SELECT author_id FROM account_files")] == ["456"]


def test_a_folder_renamed_by_the_tool_keeps_its_person(env, client):
    old = env["media"] / "dana.old"
    filename_posts(old, "dana.old", "DDDDDDDDDD1")
    scanner.scan(env["roots"])
    pid = create(client, "Dana", account(client, "instagram", "dana.old"))["person"]["id"]
    # instaloader renames the folder after the profile's new name, and goes on in it.
    new = env["media"] / "dana.new"
    os.rename(old, new)
    write_post(new, "D2", TS, owner("dana.new", 444, "Dana"), "image")
    scanner.scan(env["roots"])
    assert links() == [("Dana", "instagram", "444")]
    assert ids(client, f"person={pid}") == ["instagram:D2", "instagram:DDDDDDDDDD1"]
    [a] = get(client, "/api/authors")
    assert [h["handle"] for h in a["handles"]] == ["dana.new", "dana.old"]


def test_a_gone_link_never_moves_to_two_candidates_or_another_person(env, client):
    filename_posts(env["media"] / "erin", "erin", "EEEEEEEEEE1")
    scanner.scan(env["roots"])
    pid = create(client, "Erin", account(client, "instagram", "erin"))["person"]["id"]
    post(client, "/api/delete", {"posts": ["instagram:EEEEEEEEEE1"]})
    # two accounts have had the handle: the link stays as it is
    write_post(env["media"] / "x1", "F1", TS, owner("erin", 1), "image")
    write_post(env["media"] / "x2", "F2", TS, owner("erin", 2), "image")
    scanner.scan(env["roots"])
    assert links() == [("Erin", "instagram", "erin")]
    # one, linked to someone else: it stays too
    post(client, "/api/delete", {"posts": ["instagram:F2"]})
    create(client, "Other", account(client, "instagram", "erin"))
    scanner.scan(env["roots"])
    assert ("Erin", "instagram", "erin") in links()
    assert get(client, f"/api/people/{pid}")["count"] == 0


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


# ---------------------------------------------------------------------------
# Handle history
# ---------------------------------------------------------------------------

def test_handle_history_from_renamed_posts(env, client):
    old = owner("alice.old", 111, "Alice")
    write_post(env["media"] / "alice.example", "A0", TS - 1000, old, "image")
    write_post(env["media"] / "alice.example", "A1", TS - 500, old, "image")
    write_post(env["media"] / "alice.example", "A2", TS, ALICE, "image")
    png(env["media"] / "alice.example" / "alice.example-2024-06-01-AAAAAAAAAA3.jpg")
    os.utime(env["media"] / "alice.example" / "alice.example-2024-06-01-AAAAAAAAAA3.jpg", (TS + 2000, TS + 2000))
    scanner.scan(env["roots"])
    [a] = get(client, "/api/authors")
    assert (a["id"], a["handle"], a["name"], a["count"]) == ("111", "alice.example", "Alice Example", 4)
    # the alias's posts count too: alice.example was last seen on the filename post
    assert a["handles"] == [{"handle": "alice.example", "first": TS, "last": TS + 2000},
                            {"handle": "alice.old", "first": TS - 1000, "last": TS - 500}]
    assert a["names"] == [{"name": "Alice Example", "first": TS, "last": TS},
                          {"name": "Alice", "first": TS - 1000, "last": TS - 500}]
    assert a["newest"] == TS + 2000
    # a person lists the same history
    p = create(client, "A", a)["person"]
    assert p["accounts"][0]["handles"] == a["handles"]


def test_handles_from_id_files_and_accepted_renames(env, client):
    write_post(env["media"] / "gina.now", "G1", TS, owner("gina.now", 555, "Gina"), "image")
    # an older folder of hers, renamed by hand, with instaloader's id file
    filename_posts(env["media"] / "gina_archive", "gina.first", "GGGGGGGGGG1")
    (env["media"] / "gina_archive" / "id").write_text("555")
    os.utime(env["media"] / "gina_archive" / "id", (TS - 900, TS - 900))
    scanner.scan(env["roots"])
    [a] = get(client, "/api/authors")
    assert (a["id"], a["aliases"]) == ("555", ["gina_archive"])
    assert {h["handle"]: (h["first"], h["last"]) for h in a["handles"]}["gina_archive"] == (TS - 900, TS - 900)
    assert {"gina.now", "gina.first", "gina_archive"} == {h["handle"] for h in a["handles"]}
    # a rename the user accepted: the new handle is current until a post says otherwise
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO handle_renames(platform, author_id, old, new, at) "
                     "VALUES ('instagram', '555', 'gina.now', 'gina.later', ?)", (TS + 10,))
    [a] = get(client, "/api/authors")
    assert a["handle"] == "gina.later" and a["handles"][0]["handle"] == "gina.later"
    assert a["url"] == "https://www.instagram.com/gina.later/"
    write_post(env["media"] / "gina.now", "G2", TS + 20, owner("gina.again", 555, "Gina"), "image")
    scanner.scan(env["roots"])
    assert get(client, "/api/authors")[0]["handle"] == "gina.again"


def test_a_rebuild_keeps_links_dismissals_and_handles(env, client):
    suggestion_archive(env)
    filename_posts(env["media"] / "old.folder", "old.folder", "OOOOOOOOOO1")
    (env["media"] / "old.folder" / "id").write_text("501")
    scanner.scan(env["roots"])
    x = account(client, "twitter", "example_user1")
    ig = next(a for a in get(client, "/api/authors") if a["id"] == "501")
    pid = create(client, "Eee", ig, x)["person"]["id"]
    s = next(s for s in suggestions(client)["suggestions"] if s["reason"] == "same_name")
    post(client, "/api/people/suggestions/dismiss", {"id": s["id"]})
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO handle_renames(platform, author_id, old, new, at) "
                     "VALUES ('instagram', '501', 'example_user1', 'eee.now', ?)", (TS + 10,))
    userdata.changed("handle_renames")
    before = (links(), get(client, "/api/authors"), suggestions(client), ids(client, f"person={pid}"))
    userdata.flush()
    path = config.db_path(config.load())
    db.init(str(env["tmp"] / "other.db"))
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db.init(path)
    userdata.restore_all(db.connect(), config.load()["data_directory"])
    scanner.scan(env["roots"])
    pid = get(client, "/api/people")[0]["id"]
    after = (links(), get(client, "/api/authors"), suggestions(client), ids(client, f"person={pid}"))
    assert after == before
    assert before[0] == [("Eee", "instagram", "501"), ("Eee", "twitter", x["id"])]
    assert before[2]["dismissed"] == 1
    assert "example_user1" in [h["handle"] for h in ig["handles"]]


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------

def edit_json(path, change):
    with open(path) as f:
        d = json.load(f)
    change(d)
    with open(path, "w") as f:
        json.dump(d, f)


def suggestion_archive(env):
    """X @example_user1 and TikTok @example_user6 (fixtures), and on Instagram:
    @example_user1 (same handle as X), @the_example_user6 (similar to the
    TikTok), @zed.one and @zed.two (same display name), and @bob.example,
    whose Profile file links to the X account; the TikTok's bio links to
    @zed.one."""
    write_post(env["media"] / "example_user1", "E1", TS, owner("example_user1", 501, "Eee"), "image")
    write_post(env["media"] / "the_example_user6", "E6", TS, owner("the_example_user6", 506, "Six"), "image")
    write_post(env["media"] / "zed.one", "Z1", TS, owner("zed.one", 601, "Zed Example ✨"), "image")
    write_post(env["media"] / "zed.two", "Z2", TS, owner("zed.two", 602, "zed  example"), "image")
    write_post(env["media"] / "bob.example", "B1", TS, BOB, "image")
    write_meta(str(env["media"] / "bob.example" / "bob.example_222"),
               {"id": "222", "username": "bob.example", "biography": "hi",
                "external_url": "https://x.com/Example_User1", "bio_links": []}, node_type="Profile")
    gallery_dl_case("twitter/photo", env["media"] / "twitter" / "example_user1")
    tiktok = env["media"] / "tiktok" / "example_user6"
    for n in gallery_dl_case("tiktok/video", tiktok):
        edit_json(tiktok / n, lambda d: d["author"].update(signature="me on ig: instagram.com/zed.one ✌"))
    scanner.scan(env["roots"])


def suggestions(client):
    return get(client, "/api/people/suggestions")


def by_accounts(client):
    return {tuple(sorted(f"{a['platform']}:{a['handle']}" for a in s["accounts"])): s
            for s in suggestions(client)["suggestions"]}


def test_suggestions_for_each_reason(env, client):
    suggestion_archive(env)
    got = by_accounts(client)
    reasons = {k: [(r["reason"], r["detail"]) for r in s["reasons"]] for k, s in got.items()}
    assert reasons == {
        ("instagram:bob.example", "twitter:example_user1"):
            [("bio_link", "Instagram @bob.example links to X @example_user1")],
        ("instagram:zed.one", "tiktok:example_user6"):
            [("bio_link", "TikTok @example_user6 links to Instagram @zed.one")],
        ("instagram:example_user1", "twitter:example_user1"): [("same_handle", "@example_user1")],
        ("instagram:the_example_user6", "tiktok:example_user6"):
            [("similar_handle", "@example_user6 ~ @the_example_user6")],
        ("instagram:zed.one", "instagram:zed.two"): [("same_name", "Zed Example ✨")],
    }
    s = got[("instagram:bob.example", "twitter:example_user1")]
    assert (s["reason"], s["score"], s["person"]) == ("bio_link", 0.95, None)
    assert [x["score"] for x in suggestions(client)["suggestions"]] == [0.95, 0.95, 0.9, 0.7, 0.6]
    assert suggestions(client)["dismissed"] == 0


def test_several_reasons_score_higher(env, client):
    write_post(env["media"] / "example_user1", "E1", TS, owner("example_user1", 501, "Example User 1"), "image")
    gallery_dl_case("twitter/photo", env["media"] / "twitter" / "example_user1")
    scanner.scan(env["roots"])
    [s] = suggestions(client)["suggestions"]
    assert [r["reason"] for r in s["reasons"]] == ["same_handle", "same_name"]
    assert (s["reason"], s["score"]) == ("same_handle", 0.92)


def test_an_old_handle_and_an_alias_suggest_too(env, client):
    # Instagram renamed from example_user1; the X account still has that handle
    write_post(env["media"] / "newname", "E1", TS, owner("example_user1", 501, "A"), "image")
    write_post(env["media"] / "newname", "E2", TS + 10, owner("newname", 501, "B"), "image")
    gallery_dl_case("twitter/photo", env["media"] / "twitter" / "example_user1")
    scanner.scan(env["roots"])
    assert list(by_accounts(client)) == [("instagram:newname", "twitter:example_user1")]


def test_linking_and_people_change_suggestions(env, client):
    suggestion_archive(env)
    zed1, zed2 = account(client, "instagram", "zed.one"), account(client, "instagram", "zed.two")
    tt = account(client, "tiktok", "example_user6")
    # one side linked: the suggestion extends that person
    pid = create(client, "Zed", zed1)["person"]["id"]
    s = by_accounts(client)[("instagram:zed.one", "instagram:zed.two")]
    assert s["person"] == {"id": pid, "name": "Zed"}
    post(client, f"/api/people/{pid}/accounts", {"add": [ref(zed2)]})
    assert ("instagram:zed.one", "instagram:zed.two") not in by_accounts(client)
    # two people: a merge, not a suggestion
    create(client, "Six", tt)
    assert ("instagram:zed.one", "tiktok:example_user6") not in by_accounts(client)


def test_dismissals_persist(env, client):
    suggestion_archive(env)
    s = by_accounts(client)[("instagram:zed.one", "instagram:zed.two")]
    assert post(client, "/api/people/suggestions/dismiss", {"id": s["id"]}) == {"ok": True}
    assert ("instagram:zed.one", "instagram:zed.two") not in by_accounts(client)
    assert suggestions(client)["dismissed"] == 1
    post(client, "/api/people/suggestions/dismiss", {"id": s["id"]}, 404)        # not listed any more
    post(client, "/api/people/suggestions/dismiss", {"id": 5}, 400)
    assert client.get("/api/people/suggestions").status_code == 403
    # survives a rebuild
    userdata.flush()
    db.init(str(env["tmp"] / "rebuilt.db"))
    userdata.restore_all(db.connect(), config.load()["data_directory"])
    scanner.scan(env["roots"])
    assert ("instagram:zed.one", "instagram:zed.two") not in by_accounts(client)
    assert len(by_accounts(client)) == 4
    # a group that gains an account shows again
    write_post(env["media"] / "zed.three", "Z3", TS, owner("zed.three", 603, "Zed Example"), "image")
    scanner.scan(env["roots"])
    assert ("instagram:zed.one", "instagram:zed.three", "instagram:zed.two") in by_accounts(client)


def test_profiles_are_read_without_claiming_or_posts(env):
    suggestion_archive(env)
    conn = db.connect()
    rows = {(r[0], r[1]): (r[2], json.loads(r[3])) for r in conn.execute(
        "SELECT platform, author_id, bio, urls FROM profiles")}
    assert rows[("instagram", "222")] == ("hi", ["https://x.com/Example_User1"])
    assert rows[("tiktok", "8419594197139637801")][0].startswith("me on ig:")
    assert rows[("twitter", "641286")] == ("Example text", ["https://example.invalid/cf73a03c581b9cda"])
    assert db.unmatched(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 7
    # a profile file gone: dropped on the next scan
    os.remove(env["media"] / "bob.example" / "bob.example_222.json")
    scanner.scan(env["roots"])
    assert ("instagram", "222") not in {(r[0], r[1]) for r in conn.execute("SELECT platform, author_id FROM profiles")}


def test_handle_and_link_normalizing():
    import people
    assert {h: people.handle_parts(h) for h in ("foo", "foo_", "TheFoo", "real.foo2", "foo_official", "theo")} == {
        "foo": ("foo", ""), "foo_": ("foo", ""), "TheFoo": ("foo", ""), "real.foo2": ("foo", "2"),
        "foo_official": ("foo", ""), "theo": ("theo", "")}
    assert people.handle_parts("ab1") == (None, "1")
    assert people.norm_name("Zoé  Smith!") == "zoe smith" and people.norm_name("Al ✨") is None
    assert people.profile_links("x.com/Foo_bar. https://www.instagram.com/p/abc/ tiktok.com/@baz "
                                "tiktok.com/nope instagram.com/holly.x twitch.tv/z") == [
        ("twitter", "foo_bar"), ("tiktok", "baz"), ("instagram", "holly.x")]
    # other domains that end like one
    assert people.profile_links("https://www.dropbox.com/s/abc netflix.com/title mytiktok.com/@z") == []


def test_similar_handles_with_the_same_digits(env, client):
    write_post(env["media"] / "foo_5", "F1", TS, owner("foo_5", 801), "image")
    write_post(env["media"] / "foo.5", "F2", TS, owner("foo.5", 802), "image")
    write_post(env["media"] / "foo6", "F3", TS, owner("foo6", 803), "image")
    scanner.scan(env["roots"])
    got = [(s["reason"], sorted(a["id"] for a in s["accounts"])) for s in suggestions(client)["suggestions"]]
    assert got == [("similar_handle", ["801", "802"])]
