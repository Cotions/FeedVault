import json
import os

from conftest import H
from fakes import owner, write_filename_post, write_post

import db
import scanner
import sources
import userdata

ALICE = owner("alice.example", 111, "Alice Example")
TS = 1717243200                                     # 2024-06-01 12:00 UTC
OPTS = {"full_history": False, "session": None, "content": None, "media": "all", "since": None, "first_posts": None,
        "schedule": "off"}


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


def archive(env):
    """The real archive's shape: filename-only folders (no metadata JSON,
    the folder's name as author id), one of them also holding a post with
    metadata, so its name is an alias of the numeric id."""
    for i in range(3):
        write_filename_post(env["media"] / "carol.cooks", "carol.cooks", f"CCCCCCCCCC{i}", TS + i * 86400, slides=1 + i)
    write_filename_post(env["media"] / "alice.example", "alice.example", "AAAAAAAAAA1", TS)
    write_post(env["media"] / "alice.example", "A2", TS + 3 * 86400, ALICE, "image")
    write_filename_post(env["media"] / "Dave_Old" / "Trips", "Trips", "DDDDDDDDDD1", TS)   # highlight subfolder
    write_filename_post(env["media"] / "Dave_Old", "dave_old", "DDDDDDDDDD2", TS + 86400)
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
    # As in the real archive: the newest file is named after someone else, so
    # the account's handle reads as theirs. The folder's name is the profile.
    folder = env["media"] / "motherbeef"
    folder.mkdir()
    for i, (name, ts) in enumerate([("motherbeef", TS), ("motherbeef", TS + 60), ("tatum.bell", TS + 120)]):
        f = folder / f"{name} - SPACEDcode{i}.jpg"
        f.write_bytes(b"x")
        os.utime(f, (ts, ts))
    scanner.scan(env["roots"])
    [s] = get(client, "/api/sources")["suggestions"]
    assert (s["target"], s["account"]["id"]) == ("motherbeef", "motherbeef")


def test_suggestions_never_create_sources(env, client):
    archive(env)
    for _ in range(2):
        get(client, "/api/sources")
        scanner.scan(env["roots"])
    assert db.connect().execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_create_from_a_url(env, client):
    archive(env)
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "https://www.instagram.com/New.Person/"})["source"]
    assert (s["target"], s["folder"], s["account"]) == ("new.person", str(env["media"] / "new.person"), None)
    assert not os.path.exists(s["folder"])                     # made by the first sync
    # The account is found from the folder's posts when none is given.
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "@alice.example"})["source"]
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
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "carol.cooks"})["source"]
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
    write_post(env["media"] / "newcomer", "N1", TS, owner("newcomer", 555), "image")
    scanner.index_dirs(env["roots"], [str(env["media"] / "newcomer")])
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
    write_post(env["media"] / "newcomer", "N1", TS, owner("newcomer", 555), "image")
    scanner.index_dirs(env["roots"], [str(env["media"] / "newcomer")])
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
    # Its default folder: the target _saved itself.
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "_saved"}, 400)
    assert "pick another folder" in r["error"]
    assert get(client, "/api/sources/resolve?url=instagram.com/_saved")["ok"] is False
    # Named like it elsewhere is fine.
    post(client, "/api/sources", {"tool": "instaloader", "target": "someone", "folder": str(media / "x" / "_saved")})
    post(client, "/api/sources", {"tool": "instaloader", "target": "_saved", "folder": str(media / "saved-account")})
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
