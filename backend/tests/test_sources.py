import collections
import json
import os

from conftest import H
from fakes import owner, write_filename_post, write_meta, write_post

import db
import scanner
import sources
import userdata

ALICE = owner("alice.example", 111, "Alice Example")
TS = 1717243200                                     # 2024-06-01 12:00 UTC
OPTS = {"full_history": False, "session": None, "content": None, "media": "all", "since": None, "first_posts": None,
        "script": None, "schedule": "off"}


def get(client, url, status=200):
    r = client.get(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def post(client, url, body, status=200):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def delete(client, url, status=200):
    r = client.delete(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def archive(env, base=None):
    """The real archive's shape: filename-only folders (no metadata JSON,
    the folder's name as author id), one of them also holding a post with
    metadata, so its name is an alias of the numeric id. Under ``base``
    (default the media root, as older libraries have them)."""
    base = base or env["media"]
    for i in range(3):
        write_filename_post(base / "carol.cooks", "carol.cooks", f"CCCCCCCCCC{i}", TS + i * 86400, slides=1 + i)
    write_filename_post(base / "alice.example", "alice.example", "AAAAAAAAAA1", TS)
    write_post(base / "alice.example", "A2", TS + 3 * 86400, ALICE, "image")
    write_filename_post(base / "Dave_Old" / "Trips", "Trips", "DDDDDDDDDD1", TS)   # highlight subfolder
    write_filename_post(base / "Dave_Old", "dave_old", "DDDDDDDDDD2", TS + 86400)
    scanner.scan(env["roots"])


# ---------------------------------------------------------------------------
# Targets and options
# ---------------------------------------------------------------------------

def test_parse_target():
    for text, want in [("somebody", "somebody"), ("@Some.Body_2", "some.body_2"), ("  name  ", "name"),
                       ("https://www.instagram.com/somebody/", "somebody"), ("instagram.com/somebody", "somebody"),
                       ("http://m.instagram.com/somebody?igsh=abc", "somebody"),
                       ("https://instagram.com/somebody/#top", "somebody")]:
        assert sources.parse_target("instaloader", text) == want, text
    for text in ["", "@", "a b", "-x", "--login", "x;id", "$(id)", "a/b", "../x", "a" * 31,
                 "https://www.instagram.com/p/C8xYzAbCdEf/", "https://www.instagram.com/reel/C8xYzAbCdEf/",
                 "https://www.instagram.com/stories/somebody/1/", "https://evil.example/somebody",
                 "https://www.instagram.com/", "p", "...", None, 3]:
        assert sources.parse_target("instaloader", text) is None, text
    assert sources.parse_target("gallery-dl", "somebody") is None


def test_clean_options_and_session():
    assert sources.clean_options(None) == OPTS
    assert sources.clean_options({"full_history": True}) == {**OPTS, "full_history": True}
    base = {**OPTS, "full_history": True, "session": {"mode": "none"}}
    assert sources.clean_options({"session": None}, base) == {**OPTS, "full_history": True}
    for s in [{"mode": "none"}, {"mode": "cookies", "browser": "firefox"}, {"mode": "login", "user": "me.name_1"}]:
        assert sources.clean_session(s) == s
        assert sources.clean_options({"session": s})["session"] == s
    for s in [{"mode": "cookies", "browser": "safari"}, {"mode": "cookies"}, {"mode": "login", "user": "-x"},
              {"mode": "login", "user": "me; id"}, {"mode": "login", "user": "--password"},
              {"mode": "login", "user": "me", "password": "secret"}, {"mode": "none", "browser": "firefox"},
              {"mode": "cookies", "browser": "firefox", "cookiefile": "/tmp/c"}, {"mode": "shell"}, "none", None]:
        assert sources.clean_session(s) is None, s
    for o in [{"full_history": "yes"}, {"session": {"mode": "x"}}, {"argv": ["--login"]}, [], "x"]:
        assert sources.clean_options(o) is None, o


def test_options_content_media_since_first_posts():
    clean = sources.parse_options
    ig = {"tool": "instaloader", "platform": "instagram", "target": "carol.cooks"}
    x = {"tool": "gallery-dl", "platform": "twitter", "target": "https://x.com/someone"}
    yt = {"tool": "yt-dlp", "platform": "youtube", "target": "https://youtube.com/@someone"}
    # Content: from the source's choices, stored in their order; the default alone is null.
    assert clean({"content": ["stories", "posts", "reels"]}, **ig)[0]["content"] == ["posts", "reels", "stories"]
    assert clean({"content": ["posts"]}, **ig)[0]["content"] is None
    assert clean({"content": ["media", "with_replies"]}, **x)[0]["content"] == ["media", "with_replies"]
    assert clean({"content": None, "media": "videos", "since": "2024-01-01", "first_posts": 50}, **x)[0] == \
        {**OPTS, "media": "videos", "since": "2024-01-01", "first_posts": 50}
    for value, words in [
            ({"content": []}, "non-empty list"), ({"content": ["posts", "igtv"]}, "non-empty list"),
            ({"content": "posts"}, "non-empty list"), ({"content": [1]}, "non-empty list"),
            ({"media": "gifs"}, "media must be"), ({"media": None}, "media must be"),
            ({"since": "2024-1-1"}, "YYYY-MM-DD"), ({"since": "2024-02-30"}, "YYYY-MM-DD"),
            ({"since": "1969-12-31"}, "YYYY-MM-DD"), ({"since": "2999-01-01"}, "YYYY-MM-DD"),
            ({"since": 20240101}, "YYYY-MM-DD"), ({"since": "2024-01-01 or 1"}, "YYYY-MM-DD"),
            ({"first_posts": 5}, "cannot stop after"), ({"argv": ["--stories"]}, "unknown option")]:
        options, error = clean(value, **ig)
        assert options is None and words in error, (value, error)
    for value, words in [({"first_posts": 0}, "1 to 10000"), ({"first_posts": 10001}, "1 to 10000"),
                         ({"first_posts": True}, "1 to 10000"), ({"first_posts": "5"}, "1 to 10000"),
                         ({"first_posts": 5, "full_history": True}, "not both"),
                         ({"content": ["timeline", "likes"]}, "non-empty list")]:
        options, error = clean(value, **x)
        assert options is None and words in error, (value, error)
    # A link to one of a profile's pages picks its content; yt-dlp has no media choice.
    assert "picks what it downloads" in clean({"content": ["media"]}, **{**x, "target": "https://x.com/someone/media"})[1]
    assert "link lists" in clean({"content": ["videos"]}, **yt)[1]
    assert "every video" in clean({"media": "images"}, **yt)[1]
    assert clean({"first_posts": 10000, "since": "1970-01-01"}, **yt)[0]["first_posts"] == 10000
    # Only stories, highlights and tagged posts on Instagram need a login.
    opts = clean({"content": ["posts", "reels", "stories", "tagged"]}, **ig)[0]
    assert sources.login_refused(opts, "instagram", {"mode": "none"}) == \
        "Stories, tagged need a logged-in session: choose one for this source, or set one in Settings → Sync"
    assert sources.login_refused(opts, "instagram", {"mode": "login", "user": "me"}) is None
    assert sources.login_refused(clean({"content": ["reels"]}, **ig)[0], "instagram", {"mode": "none"}) is None
    tt = clean({"content": ["stories"]}, tool="gallery-dl", platform="tiktok", target="https://tiktok.com/@a")[0]
    assert sources.login_refused(tt, "tiktok", {"mode": "none"}) is None


def test_choices():
    assert sources.choices("instaloader", "instagram", "carol.cooks") == {
        "content": ["posts", "reels", "stories", "highlights", "tagged"], "content_default": ["posts"],
        "login": ["stories", "highlights", "tagged"], "media": True, "since": True, "first_posts": False}
    assert sources.choices("gallery-dl", "twitter", "https://x.com/someone")["content"] == \
        ["timeline", "media", "tweets", "with_replies"]
    for target in ["https://x.com/someone/media", "https://x.com/someone/with_replies"]:
        assert sources.choices("gallery-dl", "twitter", target)["content"] == []
    assert sources.choices("gallery-dl", "bluesky", "https://bsky.app/profile/a.bsky.social")["content_default"] == \
        ["media"]
    assert sources.choices("gallery-dl", "reddit", "https://reddit.com/user/someone")["content"] == []
    assert sources.choices("yt-dlp", "tiktok", "https://tiktok.com/@a") == {
        "content": [], "content_default": [], "login": [], "media": False, "since": True, "first_posts": True}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_suggestions_from_existing_folders(env, client):
    archive(env)
    r = get(client, "/api/sources")
    assert r["sources"] == []
    got = {s["folder"]: s for s in r["suggestions"]}
    media = str(env["media"])
    assert sorted(got) == [f"{media}/Dave_Old", f"{media}/alice.example", f"{media}/carol.cooks"]
    alice = got[f"{media}/alice.example"]
    # The folder's name is an alias of the numeric id: one account, the id's.
    assert (alice["target"], alice["account"], alice["count"]) == \
        ("alice.example", {"platform": "instagram", "id": "111"}, 2)
    carol = got[f"{media}/carol.cooks"]
    assert (carol["target"], carol["account"], carol["count"]) == \
        ("carol.cooks", {"platform": "instagram", "id": "carol.cooks"}, 3)
    # Highlights sit in a subfolder: still the one profile folder.
    dave = got[f"{media}/Dave_Old"]
    assert (dave["target"], dave["account"]["id"], dave["count"]) == ("dave_old", "dave_old", 2)
    # Confirmed: it is a source, and no longer suggested.
    s = post(client, "/api/sources", {k: carol[k] for k in ("tool", "target", "folder", "account")})["source"]
    assert (s["target"], s["folder"], s["account"], s["person"]) == \
        ("carol.cooks", f"{media}/carol.cooks", carol["account"], None)
    assert s["options"] == OPTS and s["last_result"] is None
    assert s["url"] == "https://www.instagram.com/carol.cooks/" and s["job"] is None
    r = get(client, "/api/sources")
    assert [x["id"] for x in r["sources"]] == [s["id"]]
    assert f"{media}/carol.cooks" not in {x["folder"] for x in r["suggestions"]}


def test_suggestion_of_a_filename_only_folder_targets_the_folder(env, client):
    # As in the real archive: the newest file is named after someone else.
    # The folder's name is the profile (and the account's handle, #99).
    folder = env["media"] / "motherbeef"
    folder.mkdir()
    for i, (name, ts) in enumerate([("motherbeef", TS), ("motherbeef", TS + 60), ("tatum.bell", TS + 120)]):
        f = folder / f"{name} - SPACEDcode{i}.jpg"
        f.write_bytes(b"x")
        os.utime(f, (ts, ts))
    scanner.scan(env["roots"])
    [s] = get(client, "/api/sources")["suggestions"]
    assert (s["target"], s["account"]["id"]) == ("motherbeef", "motherbeef")


def _indexed():
    """{post id: what a scan must keep of it} of every indexed post."""
    return {r["id"]: tuple(r)[1:] for r in db.connect().execute(
        "SELECT id, author_id, author_handle, album, kind, posted_at, first_seen, missing FROM posts")}


def test_filename_only_folders_under_the_platform_folder(env, client):
    """The same archive as ``<root>/instagram/<name>``, a new source's
    layout: the accounts are the profile folders', never one named after
    the platform's folder, and each folder is offered as its account's."""
    base = env["media"] / "instagram"
    archive(env, base)
    assert {pid: r[:3] for pid, r in _indexed().items()} == {
        "instagram:CCCCCCCCCC0": ("carol.cooks", "carol.cooks", None),
        "instagram:CCCCCCCCCC1": ("carol.cooks", "carol.cooks", None),
        "instagram:CCCCCCCCCC2": ("carol.cooks", "carol.cooks", None),
        "instagram:AAAAAAAAAA1": ("alice.example", "alice.example", None),
        "instagram:A2": ("111", "alice.example", None),
        "instagram:DDDDDDDDDD1": ("dave_old", "Dave_Old", "Trips"),
        "instagram:DDDDDDDDDD2": ("dave_old", "dave_old", None)}
    authors = {a["id"]: a for a in get(client, "/api/authors")}
    assert sorted(authors) == ["111", "carol.cooks", "dave_old"]
    assert (authors["111"]["aliases"], authors["111"]["count"]) == (["alice.example"], 2)
    got = {s["folder"]: (s["target"], s["account"]["id"], s["count"]) for s in get(client, "/api/sources")["suggestions"]}
    assert got == {str(base / "Dave_Old"): ("dave_old", "dave_old", 2),
                   str(base / "alice.example"): ("alice.example", "111", 2),
                   str(base / "carol.cooks"): ("carol.cooks", "carol.cooks", 3)}
    # A source by name alone: that folder is its default, and its posts' account its own.
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "carol.cooks"})["source"]
    assert (s["folder"], s["account"]) == (str(base / "carol.cooks"), {"platform": "instagram", "id": "carol.cooks"})
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "dave_old", "folder": str(base / "Dave_Old")})
    assert s["source"]["account"] == {"platform": "instagram", "id": "dave_old"}
    assert sources.account_folder(db.connect(), "instagram", "dave_old", env["roots"]) == str(base / "Dave_Old")
    assert [x["folder"] for x in get(client, "/api/sources")["suggestions"]] == [str(base / "alice.example")]


def test_a_filename_only_archive_moved_under_the_platform_folder_keeps_its_posts(env, client):
    """``<root>/<name>`` moved to ``<root>/instagram/<name>`` and scanned
    again: the same posts and accounts (ids, what the user said of them),
    none missing, none new; only their paths change."""
    archive(env)
    conn = db.connect()
    with conn:
        conn.execute("UPDATE posts SET first_seen = 5")
    post(client, "/api/tags/apply", {"posts": ["instagram:CCCCCCCCCC1", "instagram:DDDDDDDDDD1"], "add": ["kept"],
                                     "remove": []})
    person = post(client, "/api/people", {"name": "Carol", "accounts": [{"platform": "instagram",
                                                                         "id": "carol.cooks"}]})["person"]
    before = _indexed()
    authors = get(client, "/api/authors")
    base = env["media"] / "instagram"
    base.mkdir()
    for name in ("carol.cooks", "alice.example", "Dave_Old"):
        os.rename(env["media"] / name, base / name)
    report = scanner.scan(env["roots"])
    assert (report["added"], report["updated"], report["missing"], report["unmatched"]) == (0, 7, 0, 0)
    assert _indexed() == before
    assert get(client, "/api/authors") == authors
    assert all(r[0].startswith(str(base) + os.sep) for r in conn.execute("SELECT meta_path FROM posts"))
    assert all(r[0].startswith(str(base) + os.sep) for r in conn.execute("SELECT path FROM media"))
    assert get(client, "/api/posts/instagram/CCCCCCCCCC1")["tags"] == ["kept"]
    assert get(client, "/api/posts/instagram/DDDDDDDDDD1")["tags"] == ["kept"]
    assert [tuple(r) for r in conn.execute("SELECT person_id, author_id FROM person_accounts")] == \
        [(person["id"], "carol.cooks")]
    carol = [a for a in get(client, "/api/authors") if a["id"] == "carol.cooks"][0]
    assert carol["person"]["name"] == "Carol"
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE first_seen != 5").fetchone()[0] == 0


def real_shapes(base):
    """The shapes of a real filename-only library under ``base``: dated and
    spaced names, highlight folders (titles of any text, some holding a
    folder named after the profile again), and instaloader's other files."""
    carol, dave = base / "carol.cooks", base / "Dave_Old"
    for i in range(3):
        write_filename_post(carol, "carol.cooks", f"CCCCCCCCCC{i}", TS + i * 86400, slides=1 + i, video=i == 2)
    for name in ("carol.cooks - SPACEDcode1 - 1.jpg", "carol.cooks - SPACEDcode1 - 2.jpg", "carol.cooks - SPACEDcode2.jpg"):
        (carol / name).write_bytes(b"x")
    for n, title in enumerate(["Summer", "faqs", "Highlights", "\U0001F47E", "Fall∕Halloween", "3﹥", "More food"]):
        write_filename_post(carol / title, title, f"CHIGHLIGHT{n}", TS + n * 86400)
        (carol / title / "2024-06-01_12-00-00_UTC_cover.jpg").write_bytes(b"x")
    # The highlight's folder holding one named after the profile, its files named either way.
    write_filename_post(carol / "Trips" / "carol.cooks", "carol.cooks", "CNESTED0001", TS, slides=2)
    write_filename_post(carol / "Trips" / "carol.cooks", "carol.cooks", "CNESTED0002", TS + 86400)
    write_filename_post(carol / "Dinner \U0001F35D" / "carol.cooks", "Dinner \U0001F35D", "CNESTED0003", TS)
    (carol / "Trips" / "carol.cooks" / "2024-06-01_12-00-00_UTC_profile_pic.jpg").write_bytes(b"x")
    write_filename_post(dave, "dave_old", "DDDDDDDDDD1", TS)
    write_filename_post(dave, "dave.new", "DDDDDDDDDD2", TS + 86400)             # renamed since
    write_post(dave, "D3", TS + 2 * 86400, owner("dave.new", 444), "image")
    # Not posts: profile pictures, resume files, the profile's metadata, a partial download, a stray file.
    for folder in (carol, dave):
        (folder / "2024-06-01_12-00-00_UTC_profile_pic.jpg").write_bytes(b"x")
        (folder / "2023-01-01_08-30-00_UTC_profile_pic.jpg").write_bytes(b"x")
    write_meta(str(carol / "iterator_AbCdEf12"), {"remaining_data": None}, node_type="FrozenNodeIterator", compress=True)
    write_meta(str(carol / "carol.cooks_777"), {"id": "777", "username": "carol.cooks"}, node_type="Profile",
               compress=True)
    (carol / "carol.cooks-2024-06-09-CPARTIAL001.mp4.temp").write_bytes(b"x")
    (carol / "test.py").write_text("print(1)\n")


def _library(base):
    """What a scan made of the library under ``base``: per post its account,
    handle, album, kind and media (paths as under ``base``), the accounts,
    and the files left unmatched."""
    conn = db.connect()
    rel = lambda p: os.path.relpath(p, base)                                    # noqa: E731
    posts = {r["id"]: (r["author_id"], r["author_handle"], r["album"], r["kind"], r["tool"], rel(r["meta_path"]),
                       sorted(rel(m[0]) for m in conn.execute("SELECT path FROM media WHERE post_id = ?", (r["id"],))))
             for r in conn.execute("SELECT * FROM posts")}
    authors = sorted((a["id"], a["handle"], a["count"], tuple(a["aliases"])) for a in db.authors(conn))
    unmatched = sorted((rel(r[0]), r[1]) for r in conn.execute("SELECT path, reason FROM unmatched"))
    return posts, authors, unmatched


def test_a_real_filename_only_library_reads_the_same_in_both_layouts(env, client):
    flat = env["tmp"] / "flat"
    real_shapes(flat)
    report = scanner.scan([str(flat)])
    posts, authors, unmatched = want = _library(flat)
    assert (report["added"], report["errors"]) == (18, [])
    assert authors == [("444", "dave.new", 3, ("dave_old",)), ("carol.cooks", "carol.cooks", 15, ())]
    albums = collections.Counter((p[0], p[2]) for p in posts.values())
    assert albums == {("carol.cooks", None): 5, ("carol.cooks", "Summer"): 1, ("carol.cooks", "faqs"): 1,
                      ("carol.cooks", "Highlights"): 1, ("carol.cooks", "\U0001F47E"): 1,
                      ("carol.cooks", "Fall∕Halloween"): 1, ("carol.cooks", "3﹥"): 1,
                      ("carol.cooks", "More food"): 1, ("carol.cooks", "Trips"): 2,
                      ("carol.cooks", "Dinner \U0001F35D"): 1, ("dave_old", None): 2, ("444", None): 1}
    assert {p[3] for p in posts.values() if p[2]} == {"story"}
    assert len(posts["instagram:CNESTED0001"][6]) == 2 and len(posts["instagram:SPACEDcode1"][6]) == 2
    # Profile pictures, covers and instaloader's own files are its: not
    # posts, not unmatched. Nor are files that are not media.
    assert unmatched == []

    # The same library under <root>/instagram, in an index of its own.
    db.init(str(env["tmp"] / "other" / "feedvault.db"))
    base = env["media"] / "instagram"
    real_shapes(base)
    report = scanner.scan(env["roots"])
    assert (report["added"], report["errors"]) == (18, [])
    assert _library(base) == want


def test_suggestions_never_create_sources(env, client):
    archive(env)
    for _ in range(2):
        get(client, "/api/sources")
        scanner.scan(env["roots"])
    assert db.connect().execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


BOB = owner("bob.example", 222, "Bob Example")
CAROL = owner("carol.example", 333, "Carol Example")


def shared_platform_folder(env):
    """gallery-dl's layout for instaloader posts (#137): a folder per account
    under ``<root>/instagram``, with a highlight subfolder and a post of
    someone else's (a repost) in one of them."""
    shared = env["media"] / "instagram"
    for i in range(3):
        write_post(shared / "alice.example", f"A{i}", TS + i * 86400, ALICE, "image")
    write_post(shared / "alice.example", "B9", TS + 3600, BOB, "image")
    write_post(shared / "bob.example", "B1", TS, BOB, "image")
    write_post(shared / "bob.example" / "Trips", "B2", TS + 86400, BOB, "image")
    scanner.scan(env["roots"])
    return shared


def test_suggestions_split_a_shared_platform_folder(env, client):
    shared = shared_platform_folder(env)
    got = {s["folder"]: s for s in get(client, "/api/sources")["suggestions"]}
    # One per account, its own folder, its own posts counted: never the
    # platform folder that holds them all.
    assert sorted(got) == [str(shared / "alice.example"), str(shared / "bob.example")]
    alice, bob = got[str(shared / "alice.example")], got[str(shared / "bob.example")]
    assert (alice["target"], alice["account"]["id"], alice["count"]) == ("alice.example", "111", 3)
    assert (bob["target"], bob["account"]["id"], bob["count"]) == ("bob.example", "222", 2)
    # Added, each folder is its account's, and the other one is still offered.
    post(client, "/api/sources", {k: alice[k] for k in ("tool", "target", "folder", "account")})
    assert [s["folder"] for s in get(client, "/api/sources")["suggestions"]] == [str(shared / "bob.example")]
    # A source by name alone: the same folder is its default, and its posts name the account.
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "Bob.Example"})["source"]
    assert (s["folder"], s["account"]) == (str(shared / "bob.example"), {"platform": "instagram", "id": "222"})
    assert get(client, "/api/sources")["suggestions"] == []


def test_default_folder():
    roots = ["/media", "/other"]
    assert sources.default_folder("instaloader", "instagram", "some.body", roots) == "/media/instagram/some.body"
    assert sources.default_folder("instaloader", "instagram", "_u.ser_", roots) == "/media/instagram/_u.ser_"
    assert sources.default_folder("instaloader", "instagram", "media", roots) == "/media/instagram/media"
    assert sources.default_folder("gallery-dl", "twitter", "https://x.com/Some_One/media", roots) == \
        "/media/twitter/some_one"
    assert sources.resolve_name("@Some.Body", roots)["folder"] == "/media/instagram/some.body"


def test_a_source_by_name_takes_the_account_with_posts_in_an_older_folder(env, client):
    """A library laid out ``<root>/<name>``: the new source's folder is the
    default one, and it is still the account's (no second download)."""
    write_post(env["media"] / "alice.example", "A1", TS, ALICE, "image")
    scanner.scan(env["roots"])
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "alice.example"})["source"]
    assert s["folder"] == str(env["media"] / "instagram" / "alice.example")
    assert s["account"] == {"platform": "instagram", "id": "111"}


def test_a_folder_of_loose_posts_holding_other_accounts_folders_is_not_suggested(env, client):
    # Alice's posts loose in a folder that also holds Bob's and Carol's folders.
    mixed = env["media"] / "phone"
    for i in range(6):
        write_post(mixed, f"A{i}", TS + i * 86400, ALICE, "image")
    write_post(mixed / "bob", "B1", TS, BOB, "image")
    write_post(mixed / "carol", "C1", TS, CAROL, "image")
    scanner.scan(env["roots"])
    got = {s["folder"]: (s["target"], s["count"]) for s in get(client, "/api/sources")["suggestions"]}
    assert got == {str(mixed / "bob"): ("bob.example", 1), str(mixed / "carol"): ("carol.example", 1)}


def test_a_source_on_a_shared_folder_is_refused(env, client):
    shared = shared_platform_folder(env)
    media = env["media"]
    alice = {"platform": "instagram", "id": "111"}
    for folder, words in [(shared, "is a platform's folder"), (media, "is a media root"),
                          (media / "gallery-dl", "is a platform's folder"), (media / "youtube", "platform's folder")]:
        for body in ({"tool": "instaloader", "target": "alice.example", "folder": str(folder), "account": alice},
                     {"tool": "instaloader", "target": "alice.example", "folder": str(folder)},
                     {"target": "https://x.com/alice", "folder": str(folder)}):
            r = post(client, "/api/sources", body, 400)
            assert words in r["error"] and "pick the account's" in r["error"], (folder, r)
    # The same folders under another name: refused for what they hold.
    os.rename(shared, media / "archive")
    scanner.scan(env["roots"])
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "alice.example",
                                      "folder": str(media / "archive"), "account": alice}, 400)
    assert r["error"] == (f"{media / 'archive'} holds other accounts' posts (bob.example): pick the account's "
                          f"own folder, such as {media / 'instagram' / 'alice.example'}")
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "bob.example", "account":
                                      {"platform": "instagram", "id": "222"},
                                      "folder": str(media / "archive" / "alice.example")}, 400)
    assert "holds other accounts' posts (alice.example)" in r["error"]
    os.symlink(media / "archive", media / "link")                # followed
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "alice.example",
                                      "folder": str(media / "link"), "account": alice}, 400)
    assert "holds other accounts' posts (bob.example)" in r["error"]
    assert db.connect().execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    # The account's own folder is taken, a repost of someone else's in it or not.
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "alice.example",
                                      "folder": str(media / "archive" / "alice.example")})["source"]
    assert s["account"] == alice
    # A profile named as a platform: its folder is inside instagram's, as any profile's.
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "youtube"})["source"]
    assert s["folder"] == str(media / "instagram" / "youtube")


def test_create_from_a_url(env, client):
    archive(env)
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "https://www.instagram.com/New.Person/"})["source"]
    assert (s["target"], s["folder"], s["account"]) == \
        ("new.person", str(env["media"] / "instagram" / "new.person"), None)
    assert not os.path.exists(s["folder"])                     # made by the first sync
    # The account is found from the folder's posts when none is given.
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "@alice.example",
                                      "folder": str(env["media"] / "alice.example")})["source"]
    assert s["account"] == {"platform": "instagram", "id": "111"}


def test_create_refuses_bad_input(env, client, tmp_path):
    archive(env)
    media = env["media"]
    (media / "afile").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (media / "link").symlink_to(outside)
    ok = {"tool": "instaloader", "target": "carol.cooks"}
    for body in [{}, {**ok, "tool": "gallery-dl"}, {**ok, "tool": "sh"}, {**ok, "target": "x; rm -rf ~"},
                 {**ok, "target": "--login=me"}, {**ok, "target": "$(id)"}, {**ok, "target": ["carol"]},
                 {**ok, "folder": "relative/path"}, {**ok, "folder": str(outside)}, {**ok, "folder": "/etc"},
                 {**ok, "folder": str(media / "link" / "x")}, {**ok, "folder": str(media / ".." / "outside")},
                 {**ok, "folder": str(media / "afile")}, {**ok, "folder": 3},
                 {**ok, "person": "1"}, {**ok, "person": 999}, {**ok, "person": True},
                 {**ok, "account": {"platform": "instagram", "id": "nobody"}}, {**ok, "account": "x"},
                 {**ok, "account": {"platform": "twitter", "id": "111"}},
                 {**ok, "options": {"session": {"mode": "login", "user": "a b"}}}, {**ok, "options": {"x": 1}},
                 {**ok, "argv": ["--load-cookies", "firefox"]} | {"target": "a b"}]:
        r = post(client, "/api/sources", body, 400)
        assert r["ok"] is False, body
    post(client, "/api/sources", ok)
    assert "already" in post(client, "/api/sources", {**ok, "target": "@Carol.Cooks"}, 400)["error"]
    assert db.connect().execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1


def test_update_and_delete(env, client):
    archive(env)
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "carol.cooks"})["source"]
    url = f"/api/sources/{s['id']}"
    r = post(client, url, {"options": {"session": {"mode": "cookies", "browser": "firefox"}}})
    assert r["source"]["options"] == {**OPTS, "session": {"mode": "cookies", "browser": "firefox"}}
    r = post(client, url, {"options": {"full_history": True}})        # only the keys sent change
    assert r["source"]["options"] == {**OPTS, "full_history": True, "session": {"mode": "cookies", "browser": "firefox"}}
    for body in [{}, {"options": {"session": {"mode": "login"}}}, {"options": None}, {"folder": "/etc"}]:
        post(client, url, body, 400)
    assert get(client, url)["options"]["full_history"] is True
    files = sorted(os.listdir(env["media"] / "carol.cooks"))
    assert delete(client, url) == {"ok": True}
    assert sorted(os.listdir(env["media"] / "carol.cooks")) == files      # files stay
    assert get(client, "/api/posts?author=carol.cooks")["total"] == 3     # posts too
    get(client, url, 404)
    post(client, url, {"options": {}}, 404)
    delete(client, url, 404)
    get(client, "/api/sources/99999999999999999999", 404)


def test_options_api(env, client, monkeypatch):
    archive(env)
    import config
    body = {"tool": "instaloader", "target": "carol.cooks"}
    r = post(client, "/api/sources", {**body, "options": {"content": ["posts", "stories"]}}, 400)
    assert r["error"].startswith("Stories need a logged-in session")
    r = post(client, "/api/sources", {**body, "options": {"since": "2024-13-01"}}, 400)
    assert "YYYY-MM-DD" in r["error"]
    r = post(client, "/api/sources", {**body, "options": {"first_posts": 10}}, 400)
    assert "cannot stop after" in r["error"]
    # With a login (the source's own, or the tool's setting), they are accepted.
    s = post(client, "/api/sources", {**body, "options": {
        "content": ["stories", "posts"], "session": {"mode": "login", "user": "me"}, "since": "2024-01-01"}})["source"]
    assert s["options"] == {**OPTS, "content": ["posts", "stories"], "session": {"mode": "login", "user": "me"},
                            "since": "2024-01-01", "schedule": "daily"}       # stories: daily
    assert s["choices"]["login"] == ["stories", "highlights", "tagged"]
    url = f"/api/sources/{s['id']}"
    assert "logged-in" in post(client, url, {"options": {"session": {"mode": "none"}}}, 400)["error"]
    cfg = config.load()
    cfg["instaloader"] = {"session": {"mode": "cookies", "browser": "firefox"}}
    config.save(cfg)
    r = post(client, url, {"options": {"session": None, "content": ["posts", "reels", "highlights"], "media": "images"}})
    assert r["source"]["options"] == {**OPTS, "content": ["posts", "reels", "highlights"], "media": "images",
                                      "since": "2024-01-01", "schedule": "daily"}
    for bad in [{"media": "all", "x": 1}, {"since": "tomorrow"}, {"content": ["posts; rm -rf ~"]}]:
        post(client, url, {"options": bad}, 400)
    assert get(client, url)["options"]["media"] == "images"
    # A stored value that is not valid any more (edited by hand) reads as its default; the others stay.
    db.connect().execute("UPDATE sources SET options = ? WHERE id = ?",
                         (json.dumps({"content": ["igtv"], "media": "images"}), s["id"]))
    db.connect().commit()
    assert get(client, url)["options"] == {**OPTS, "media": "images"}
    for broken in ["{", "[]", json.dumps({"media": 3, "since": "2099-01-01", "x": 1})]:
        db.connect().execute("UPDATE sources SET options = ? WHERE id = ?", (broken, s["id"]))
        db.connect().commit()
        assert get(client, url)["options"] == OPTS


def test_first_posts_only_before_the_first_sync(env, client):
    s = post(client, "/api/sources", {"target": "https://x.com/someone", "options": {"first_posts": 20}})["source"]
    assert s["options"]["first_posts"] == 20 and s["choices"]["first_posts"] is True
    url = f"/api/sources/{s['id']}"
    assert post(client, url, {"options": {"first_posts": 30}})["source"]["options"]["first_posts"] == 30
    # A first sync that failed: last N is still to do, and can still change.
    sources.record(db.connect(), s["id"], 1, TS, {"state": "failed"})
    assert post(client, url, {"options": {"first_posts": 40}})["source"]["options"]["first_posts"] == 40
    # Other changes keep it; it can be cleared, and once gone it cannot come back.
    assert post(client, url, {"options": {"media": "videos"}})["source"]["options"]["first_posts"] == 40
    assert post(client, url, {"options": {"first_posts": None}})["source"]["options"]["first_posts"] is None
    assert "synced already" in post(client, url, {"options": {"first_posts": 40}}, 400)["error"]


def test_resolve_gives_choices(env, client):
    r = get(client, "/api/sources/resolve?url=x.com/someone")
    assert r["choices"]["content"] == ["timeline", "media", "tweets", "with_replies"]
    assert r["session"] == {"mode": "none"}
    r = get(client, "/api/sources/resolve?url=%40Carol.Cooks&tool=instaloader")
    assert (r["ok"], r["tool"], r["target"], r["choices"]["first_posts"]) == (True, "instaloader", "carol.cooks", False)
    assert get(client, "/api/sources/resolve?url=a%20b&tool=instaloader")["ok"] is False


def test_api_guard(env, client):
    assert client.get("/api/sources").status_code == 403
    assert client.post("/api/sources", json={"tool": "instaloader", "target": "x"}).status_code == 403
    assert client.delete("/api/sources/1").status_code == 403
    assert client.post("/api/sources/1/sync").status_code == 403
    assert client.post("/api/sources/sync-all").status_code == 403
    assert client.get("/api/sources", headers={**H, "Host": "evil.example"}).status_code == 403


# ---------------------------------------------------------------------------
# Sources follow accounts and people
# ---------------------------------------------------------------------------

def test_source_follows_account_to_person(env, client):
    archive(env)
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "carol.cooks",
                                      "folder": str(env["media"] / "carol.cooks")})["source"]
    assert s["person"] is None
    p = post(client, "/api/people", {"name": "Carol", "accounts": [s["account"]]})["person"]
    assert get(client, f"/api/sources/{s['id']}")["person"] == {"id": p["id"], "name": "Carol"}
    # Moved to another person: the source goes with it.
    q = post(client, "/api/people", {"name": "Someone else", "accounts": [s["account"]]})["person"]
    assert get(client, f"/api/sources/{s['id']}")["person"]["id"] == q["id"]
    post(client, f"/api/people/{q['id']}/accounts", {"remove": [s["account"]]})
    assert get(client, f"/api/sources/{s['id']}")["person"] is None


def test_source_added_to_a_person_without_an_account(env, client):
    archive(env)
    p = post(client, "/api/people", {"name": "Newcomer"})["person"]
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "newcomer", "person": p["id"]})["source"]
    assert (s["account"], s["person"]) == (None, {"id": p["id"], "name": "Newcomer"})
    # Merged into another person: the source moves to the one kept.
    k = post(client, "/api/people", {"name": "Keeper"})["person"]
    post(client, "/api/people/merge", {"ids": [k["id"], p["id"]]})
    assert get(client, f"/api/sources/{s['id']}")["person"]["id"] == k["id"]
    # Deleted: the source stays, with nobody.
    delete(client, f"/api/people/{k['id']}")
    assert get(client, f"/api/sources/{s['id']}")["person"] is None


def test_adopt_after_the_first_download(env, client):
    archive(env)
    p = post(client, "/api/people", {"name": "Newcomer"})["person"]
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "newcomer", "person": p["id"]})["source"]
    conn = db.connect()
    assert sources.adopt(conn, s["id"], env["roots"], TS) == []          # nothing downloaded yet
    write_post(env["media"] / "instagram" / "newcomer", "N1", TS, owner("newcomer", 555), "image")
    scanner.index_dirs(env["roots"], [str(env["media"] / "instagram" / "newcomer")])
    assert sources.adopt(conn, s["id"], env["roots"], TS) == ["sources", "person_accounts"]
    s = get(client, f"/api/sources/{s['id']}")
    assert s["account"] == {"platform": "instagram", "id": "555"} and s["person"]["id"] == p["id"]
    assert [a["id"] for a in get(client, f"/api/people/{p['id']}")["accounts"]] == ["555"]
    assert sources.adopt(conn, s["id"], env["roots"], TS) == []          # once


def test_adopt_never_takes_an_account_from_its_person(env, client):
    archive(env)
    other = post(client, "/api/people", {"name": "Other"})["person"]
    p = post(client, "/api/people", {"name": "Newcomer"})["person"]
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "newcomer", "person": p["id"]})["source"]
    write_post(env["media"] / "instagram" / "newcomer", "N1", TS, owner("newcomer", 555), "image")
    scanner.index_dirs(env["roots"], [str(env["media"] / "instagram" / "newcomer")])
    post(client, f"/api/people/{other['id']}/accounts", {"add": [{"platform": "instagram", "id": "555"}]})
    assert sources.adopt(db.connect(), s["id"], env["roots"], TS) == ["sources"]
    assert get(client, f"/api/sources/{s['id']}")["person"]["id"] == other["id"]      # the account's person wins


# ---------------------------------------------------------------------------
# User data
# ---------------------------------------------------------------------------

def test_userdata_rebuild(env, client):
    import config
    archive(env)
    p = post(client, "/api/people", {"name": "Carol"})["person"]
    a = post(client, "/api/sources", {"tool": "instaloader", "target": "carol.cooks", "person": p["id"],
                                      "options": {"session": {"mode": "login", "user": "me"}}})["source"]
    b = post(client, "/api/sources", {"tool": "instaloader", "target": "alice.example"})["source"]
    sources.record(db.connect(), a["id"], 7, TS, {"state": "failed", "error": "rate_limited", "message": "m",
                                                  "line": "429", "added": 1, "job": 7})
    data = config.load()["data_directory"]
    for name in ("people", "sources"):
        userdata.export(db.connect(), name, data)
    rows = json.load(open(userdata.path(data, "sources")))["rows"]
    assert [(r["target"], r["person"]) for r in rows] == [("alice.example", None), ("carol.cooks", "Carol")]
    assert "last_job_id" not in rows[0]
    # The database is lost: a new one, restored from the JSON files and a rescan.
    os.remove(config.db_path())
    db.init(config.db_path())
    userdata.restore_all(db.connect(), data)
    scanner.scan(env["roots"])
    got = {s["target"]: s for s in get(client, "/api/sources")["sources"]}
    carol = got["carol.cooks"]
    assert carol["person"]["name"] == "Carol" and carol["account"] == a["account"]
    assert carol["options"] == a["options"] and carol["folder"] == a["folder"]
    assert (carol["last_sync_at"], carol["last_result"]["error"], carol["last_job_id"]) == (TS, "rate_limited", None)
    assert got["alice.example"]["account"] == b["account"] and got["alice.example"]["person"] is None


def test_userdata_skips_bad_rows(env):
    import config
    data = config.load()["data_directory"]
    os.makedirs(os.path.join(data, "userdata"), exist_ok=True)
    with open(userdata.path(data, "sources"), "w") as f:
        json.dump({"version": 1, "rows": [
            {"tool": "instaloader", "target": "ok", "platform": "instagram", "folder": "/m/ok"},
            {"tool": "instaloader", "target": "nofolder", "platform": "instagram"},
            {"tool": "instaloader", "platform": "instagram", "folder": "/m/x"},
        ]}, f)
    assert userdata.load(db.connect(), "sources", data) == 1
    assert [r[0] for r in db.connect().execute("SELECT target FROM sources")] == ["ok"]


# ---------------------------------------------------------------------------
# _saved is never a source's folder (#41.4)
# ---------------------------------------------------------------------------

def test_saved_is_never_suggested(env, client):
    write_post(env["media"] / "_saved", "S1", TS, owner("someone", 4242), "image")
    write_post(env["media"] / "_saved" / "deeper", "S2", TS, owner("other", 4343), "image")
    write_post(env["media"] / "someone", "S3", TS + 86400, owner("someone", 4242), "image")
    scanner.scan(env["roots"])
    folders = {s["folder"] for s in get(client, "/api/sources")["suggestions"]}
    assert folders == {f"{env['media']}/someone"}


def test_saved_folder_is_refused(env, client, tmp_path):
    media = env["media"]
    (media / "_saved").mkdir()
    os.symlink(media / "_saved", media / "alias")
    for folder in (media / "_saved", media / "_saved" / "x", media / "alias", media / "alias" / "y"):
        r = post(client, "/api/sources", {"tool": "instaloader", "target": "someone", "folder": str(folder)}, 400)
        assert "_saved" in r["error"] and "pick another folder" in r["error"], folder
        r = post(client, "/api/sources", {"target": "https://x.com/someone", "folder": str(folder)}, 400)
        assert "_saved" in r["error"]
    # Named like it elsewhere is fine: a profile named _saved has its default folder, inside instagram's.
    r = get(client, "/api/sources/resolve?url=instagram.com/_saved")
    assert (r["ok"], r["folder"]) == (True, str(media / "instagram" / "_saved"))
    post(client, "/api/sources", {"tool": "instaloader", "target": "someone", "folder": str(media / "x" / "_saved")})
    post(client, "/api/sources", {"tool": "instaloader", "target": "_saved"})
    assert get(client, "/api/sources")["sources"] and not sources.in_saved(str(media / "_savedx"), env["roots"])


def test_sync_refuses_a_stored_source_in_saved(env, client):
    """sources.json edited by hand: the sync is refused before anything runs."""
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "someone"})["source"]
    g = post(client, "/api/sources", {"target": "https://x.com/someone"})["source"]
    conn = db.connect()
    with conn:
        conn.execute("UPDATE sources SET folder = ?", (str(env["media"] / "_saved"),))
    for sid in (s["id"], g["id"]):
        r = post(client, f"/api/sources/{sid}/sync", {}, 400)
        assert "pick another folder" in r["error"]
