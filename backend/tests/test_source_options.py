"""What a source downloads: its options turned into each tool's flags (sync.py)."""
import collections
import configparser
import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import H

import config
import db
import jobs
import sources
import sync

LOGIN = {"mode": "login", "user": "me"}
X = "https://x.com/someone"
TIKTOK = "https://tiktok.com/@someone"
YOUTUBE = "https://youtube.com/@somechannel"


def post(client, url, body, status=200):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def add(client, target, tool=None, **options):
    body = {"target": target, "options": options}
    if tool:
        body["tool"] = tool
    return post(client, "/api/sources", body)["source"]


def build(s):
    tool = s["tool"]
    fn = {"instaloader": sync._build, "gallery-dl": sync._build_gallery_dl, "yt-dlp": sync._build_yt_dlp}[tool]
    return fn({"source": str(s["id"])})["args"]


def after(args, flag):
    return args[args.index(flag) + 1]


def set_stored(sid, options):
    """Options as a hand-edited sources.json or database would hold them."""
    conn = db.connect()
    with conn:
        conn.execute("UPDATE sources SET options = ? WHERE id = ?", (json.dumps(options), sid))


def stamps():
    c = configparser.ConfigParser(interpolation=None)
    c.read(sync.stamps_path())
    return c


# ---------------------------------------------------------------------------
# instaloader
# ---------------------------------------------------------------------------

def test_instaloader_flags(env, client):
    s = add(client, "carol.cooks", "instaloader", session=LOGIN, content=["posts", "reels", "stories", "highlights",
                                                                           "tagged"])
    args = build(s)
    assert args[args.index("{date_utc}_UTC_{typename}") + 1:] == [
        "--reels", "--stories", "--highlights", "--tagged", "--login", "me", "--", "carol.cooks"]
    # Reels are walked before posts: no --fast-update even on a first sync.
    assert "--fast-update" not in args
    s = add(client, "dave.draws", "instaloader", session=LOGIN, content=["stories"], media="videos",
            since="2024-02-29")
    args = build(s)
    assert args[args.index("{date_utc}_UTC_{typename}") + 1:] == [
        "--no-posts", "--stories", "--no-pictures",
        "--post-filter", "date_utc >= datetime(2024, 2, 29)",
        "--storyitem-filter", "is_video and date_utc >= datetime(2024, 2, 29)",
        "--login", "me", "--", "dave.draws"]
    # Videos only, posts: --no-pictures, which instaloader refuses with --fast-update.
    s = add(client, "fay.films", "instaloader", media="videos")
    args = build(s)
    assert args[args.index("{date_utc}_UTC_{typename}") + 1:] == ["--no-pictures", "--", "fay.films"]
    assert "--fast-update" not in args
    s = add(client, "erin.paints", "instaloader", media="images")
    args = build(s)
    assert args[args.index("{date_utc}_UTC_{typename}") + 1:] == [
        "--no-videos", "--no-video-thumbnails", "--post-filter", "not is_video", "--", "erin.paints"]
    assert "--fast-update" in args                                  # posts only, no stamp: as before


def test_instaloader_defaults_are_unchanged(env, client):
    s = add(client, "carol.cooks", "instaloader")
    args = build(s)
    assert args[args.index("{date_utc}_UTC_{typename}") + 1:] == ["--", "carol.cooks"]


def test_login_needed_at_sync_time_too(env, client):
    s = add(client, "carol.cooks", "instaloader", content=["posts", "tagged"], session=LOGIN)
    # The source's own login goes; the tool's setting is none.
    post(client, f"/api/sources/{s['id']}", {"options": {"session": None}}, 400)
    set_stored(s["id"], {"content": ["posts", "tagged"]})
    with pytest.raises(jobs.BadRequest, match="Tagged need a logged-in session"):
        build(s)
    r = post(client, f"/api/sources/{s['id']}/sync", {}, 400)
    assert "logged-in session" in r["error"]
    cfg = config.load()
    cfg["instaloader"] = {"session": {"mode": "cookies", "browser": "firefox"}}
    config.save(cfg)
    assert "--tagged" in build(s) and after(build(s), "--load-cookies") == "firefox"


# ---------------------------------------------------------------------------
# gallery-dl and yt-dlp
# ---------------------------------------------------------------------------

def test_gallery_dl_flags(env, client):
    s = add(client, X, content=["media", "with_replies"], media="images", since="2024-01-01", first_posts=25)
    args = build(s)
    assert args[args.index("skip=abort:5") + 1:args.index("-D")] == [
        "-o", "include=media,with-replies", "--filter", "extension in exts_image",
        "--date-after", "2023-12-31T23:59:59", "--post-range", "1-25"]
    # TikTok lists pinned posts first: the floor filters, it does not stop the walk.
    cfg = config.load()
    cfg["routes"] = {**sources.ROUTES, "tiktok.com": "gallery-dl"}
    config.save(cfg)
    s = add(client, TIKTOK, content=["posts", "stories"], media="videos", since="2024-01-01")
    args = build(s)
    assert args[args.index("skip=abort:5") + 1:args.index("-D")] == [
        "-o", "include=posts,stories",
        "--filter", "extension in exts_video and (not date or date >= datetime(2024, 1, 1))"]
    s = add(client, "https://x.com/plain/media")
    args = build(s)
    assert args[args.index("skip=abort:5") + 1:args.index("-D")] == []


def test_yt_dlp_flags(env, client):
    s = add(client, "https://youtube.com/@tabbed/videos", since="2024-03-05", first_posts=10)
    args = build(s)
    assert args[args.index("--break-on-existing") + 1:args.index("-o")] == [
        "--dateafter", "20240305", "--break-match-filters", "upload_date >=? 20240305",
        "--playlist-items", "1:10"]
    # A channel's own page lists one tab after another, TikTok its pinned videos first: never stop early.
    for target in [YOUTUBE, TIKTOK]:
        args = build(add(client, target, since="2024-03-05"))
        assert "--break-match-filters" not in args and after(args, "--dateafter") == "20240305"
    args = build(add(client, "https://youtube.com/@other"))
    assert "--dateafter" not in args and "--playlist-items" not in args


# ---------------------------------------------------------------------------
# Nothing a user typed reaches what the tools evaluate
# ---------------------------------------------------------------------------

EVIL = "__import__('os').system('id')"


@pytest.mark.parametrize("options", [
    {"since": f"2024-01-01) or {EVIL} or (1"},
    {"since": "2024-01-01", "media": f"images or {EVIL}"},
    {"content": ["posts", f"reels --post-filter {EVIL}"]},
    {"content": "posts,stories"},
    {"first_posts": f"5 or {EVIL}"},
    {"first_posts": "1-5,7"},
    {"media": ["videos"]},
    {"session": {"mode": "login", "user": "me; id"}},
    {"since": "２０２４-01-01"},
    {"argv": ["--post-filter", EVIL]},
])
@pytest.mark.parametrize("target", ["carol.cooks", X, YOUTUBE])
def test_tampered_options_never_reach_argv(env, client, options, target):
    s = add(client, target, "instaloader" if "." in target and "/" not in target else None)
    good = {k: v for k, v in options.items() if k == "since" and v == "2024-01-01"}
    set_stored(s["id"], good)
    want = build(s)
    set_stored(s["id"], options)
    args = build(s)
    assert args == want                                  # the bad values' defaults, a good one kept
    assert not any("import" in a or "system" in a or "; id" in a for a in args)
    # And through the API: refused.
    assert post(client, f"/api/sources/{s['id']}", {"options": options}, 400)["ok"] is False


def test_filters_are_fixed_text_and_a_date():
    for since in ["1970-01-01", "2024-02-29", "2026-10-02"]:
        y, m, d = (int(p) for p in since.split("-"))
        options = sources.clean_options({"since": since, "media": "videos"})
        assert sync.item_filter(options) == f"date_utc >= datetime({y}, {m}, {d})"
        assert sync.item_filter(options, story=True) == f"is_video and date_utc >= datetime({y}, {m}, {d})"


# ---------------------------------------------------------------------------
# Floors and stamps
# ---------------------------------------------------------------------------

def stamp(target, key):
    return datetime.strptime(stamps().get(target, key), sync.STAMP_FORMAT)


def test_floor_raises_the_stamps_after_the_seed(env, client):
    s = add(client, "carol.cooks", "instaloader", content=["posts", "reels", "tagged", "highlights"],
            session=LOGIN, since="2024-01-01")
    notes = []
    row = sources.row(db.connect(), s["id"])
    sync._seed_stamp(db.connect(), row, sync._options(row), notes.append)
    floor = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for key in ("post-timestamp", "reels-timestamp", "tagged-timestamp"):
        assert floor - stamp("carol.cooks", key) == timedelta(microseconds=1)
    assert notes[-1] == "carol.cooks: nothing before 2024-01-01; posts, reels, tagged start there"
    # A newer stamp (synced since) stays; an older one is raised.
    c = stamps()
    c.set("carol.cooks", "post-timestamp", "2024-06-01T12:00:00.000000+0000")
    c.set("carol.cooks", "reels-timestamp", "2023-06-01T12:00:00.000000+0000")
    with open(sync.stamps_path(), "w") as f:
        c.write(f)
    sync._floor_stamps(sources.row(db.connect(), s["id"]), sync._options(sources.row(db.connect(), s["id"])),
                       notes.append)
    assert stamp("carol.cooks", "post-timestamp").year == 2024 and stamp("carol.cooks", "post-timestamp").month == 6
    assert floor - stamp("carol.cooks", "reels-timestamp") == timedelta(microseconds=1)
    assert notes[-1].endswith("reels start there")


def test_full_history_walks_back_to_the_floor(env, client):
    s = add(client, "carol.cooks", "instaloader", full_history=True, since="2024-01-01",
            content=["posts", "reels", "tagged"], session=LOGIN)
    c = configparser.ConfigParser(interpolation=None)
    c.read_dict({"carol.cooks": {key: "2024-06-01T12:00:00.000000+0000"
                                 for key in ("post-timestamp", "reels-timestamp", "tagged-timestamp")}})
    sync._write_stamps(c, sync.stamps_path())
    row = sources.row(db.connect(), s["id"])
    sync._seed_stamp(db.connect(), row, sync._options(row), lambda text: None)
    for key in ("post-timestamp", "reels-timestamp", "tagged-timestamp"):   # every kind, not only posts
        assert stamp("carol.cooks", key).date().isoformat() == "2023-12-31", key


def test_a_kind_turned_on_later_has_no_stamp(env, client):
    s = add(client, "carol.cooks", "instaloader")
    c = configparser.ConfigParser(interpolation=None)
    c.read_dict({"carol.cooks": {"post-timestamp": "2024-06-01T12:00:00.000000+0000"}})
    sync._write_stamps(c, sync.stamps_path())
    post(client, f"/api/sources/{s['id']}", {"options": {"content": ["posts", "reels", "tagged"], "session": LOGIN}})
    row = sources.row(db.connect(), s["id"])
    sync._seed_stamp(db.connect(), row, sync._options(row), lambda text: None)
    assert not stamps().has_option("carol.cooks", "reels-timestamp")
    assert not stamps().has_option("carol.cooks", "tagged-timestamp")
    assert "--fast-update" not in build(s) and "--reels" in build(s)


# ---------------------------------------------------------------------------
# Syncs with the fake tools
# ---------------------------------------------------------------------------

TS = 1717243200                                     # 2024-06-01 12:00 UTC
DAY = 86400
TESTS = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def tools(env, monkeypatch):
    """The three fake tools on PATH; returns their data and log files."""
    monkeypatch.setattr(jobs, "_active", collections.OrderedDict())
    monkeypatch.setattr(jobs, "_closing", False)
    monkeypatch.setattr(jobs, "_cool", {})
    monkeypatch.setattr(jobs, "_wake", None)
    monkeypatch.setattr(sync, "_batch", None)
    bin_dir = env["tmp"] / "bin"
    bin_dir.mkdir()
    for tool, module, main in (("instaloader", "fake_instaloader", "main"),
                               ("gallery-dl", "fake_downloaders", "gallery_dl_main"),
                               ("yt-dlp", "fake_downloaders", "yt_dlp_main")):
        exe = bin_dir / tool
        exe.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {TESTS!r})\n"
                       f"import {module}\nsys.exit({module}.{main}(sys.argv[1:]))\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    files = {"ig": env["tmp"] / "ig.json", "dl": env["tmp"] / "dl.json", "log": env["tmp"] / "runs.log"}
    monkeypatch.setenv("FAKE_INSTALOADER", str(files["ig"]))
    monkeypatch.setenv("FAKE_DOWNLOADS", str(files["dl"]))
    monkeypatch.setenv("FAKE_INSTALOADER_LOG", str(files["log"]))
    monkeypatch.setenv("FAKE_DOWNLOADS_LOG", str(files["log"]))
    monkeypatch.setenv("FAKE_INSTALOADER_SESSION", "1")           # --login works: a fake one
    cfg = config.load()
    cfg.update({"instaloader": {"pause": 0}, "gallery-dl": {"pause": 0}, "yt-dlp": {"pause": 0}})
    config.save(cfg)
    yield files
    jobs.shutdown()
    for t in threading.enumerate():
        if t.name.startswith("job-") and not t.name.endswith("-log"):
            t.join(10)


def ended(job_id, timeout=15):
    end = time.monotonic() + timeout
    while job_id in jobs._active or jobs.get(job_id)["state"] in ("queued", "running"):
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)
    return jobs.get(job_id)


def sync_now(client, sid):
    return ended(post(client, f"/api/sources/{sid}/sync", {})["job"]["id"])


def ig_post(code, days, kind="image", **extra):
    return {"shortcode": code, "ts": TS + days * DAY, "caption": code, "kind": kind, **extra}


def ig_profile():
    return {"profiles": {"carol.cooks": {
        "id": 777, "name": "Carol Cooks",
        "posts": [ig_post("COLDPOST001", -200), ig_post("CPOSTIMG001", 1), ig_post("CPOSTVID001", 2, "video"),
                  ig_post("CPOSTIMG002", 3)],
        "reels": [ig_post("CREELOLD001", -200, "video"), ig_post("CREEL000001", 2, "video")],
        "tagged": [ig_post("CTAGGED0001", 1, owner={"username": "dave.draws", "id": "888"})],
        "stories": [{"id": 3100000000000000001, "ts": TS + 3 * DAY, "video": False},
                    {"id": 3100000000000000002, "ts": TS - 300 * DAY, "video": True}],
        "highlights": [{"title": "Trips", "items": [{"id": 3200000000000000001, "ts": TS + DAY, "video": False}]}],
    }}, "fail": None, "delay": 0}


def shortcodes(client, author):
    r = client.get(f"/api/posts?author={author}&limit=100", headers=H).get_json()
    return sorted(p["post_id"] for p in r["posts"])


def test_instaloader_sync_fetches_what_the_options_say(env, client, tools):
    tools["ig"].write_text(json.dumps(ig_profile()))
    s = add(client, "carol.cooks", "instaloader", content=["posts", "reels", "stories"], since="2024-01-01",
            session={"mode": "login", "user": "demo_login"})
    job = sync_now(client, s["id"])
    assert job["state"] == "done", job["message"]
    expr = "date_utc >= datetime(2024, 1, 1)"
    assert job["argv"][job["argv"].index("{date_utc}_UTC_{typename}") + 1:] == [
        "--reels", "--stories", "--post-filter", expr, "--storyitem-filter", expr,
        "--login", "demo_login", "--", "carol.cooks"]
    assert "--fast-update" not in job["argv"]
    # Posts and reels since the floor, the newer story; not the old ones, nor tagged or highlights.
    assert shortcodes(client, "777") == ["3100000000000000001", "CPOSTIMG001", "CPOSTIMG002", "CPOSTVID001",
                                         "CREEL000001"]
    log = [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert any("nothing before 2024-01-01; posts, reels start there" in t for t in log)
    # Tagged posts turned on later: they have no stamp, so all of them come; nothing else again.
    post(client, f"/api/sources/{s['id']}", {"options": {"content": ["posts", "reels", "stories", "tagged"],
                                                          "media": "videos"}})
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and "--tagged" in job["argv"]
    assert "--no-pictures" in job["argv"] and after(job["argv"], "--post-filter") == expr
    assert shortcodes(client, "888") == []                     # the tagged post is an image: no file, not a post
    post(client, f"/api/sources/{s['id']}", {"options": {"media": "all"}})
    c = stamps()
    c.remove_option("carol.cooks", "tagged-timestamp")          # the filtered run walked past it
    sync._write_stamps(c, sync.stamps_path())
    job = sync_now(client, s["id"])
    assert job["result"]["added"] == 1 and shortcodes(client, "888") == ["CTAGGED0001"]


def test_instaloader_without_a_login_is_refused(env, client, tools):
    tools["ig"].write_text(json.dumps(ig_profile()))
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "carol.cooks",
                                      "options": {"content": ["posts", "highlights"]}}, 400)
    assert "Highlights need a logged-in session" in r["error"]
    assert not tools["log"].exists()


def test_images_only(env, client, tools):
    tools["ig"].write_text(json.dumps(ig_profile()))
    s = add(client, "carol.cooks", "instaloader", media="images")
    job = sync_now(client, s["id"])
    assert job["state"] == "done"
    assert shortcodes(client, "777") == ["COLDPOST001", "CPOSTIMG001", "CPOSTIMG002"]
    assert not any(n.endswith(".mp4") for n in os.listdir(env["media"] / "carol.cooks"))


def test_videos_only_keeps_a_carousels_videos(env, client, tools):
    data = ig_profile()
    data["profiles"]["carol.cooks"]["posts"] += [ig_post("CCAROMIX001", 4, "carousel", slides=3, video_slides=[2]),
                                                 ig_post("CCAROIMG001", 5, "carousel", slides=2)]
    tools["ig"].write_text(json.dumps(data))
    s = add(client, "carol.cooks", "instaloader", media="videos")
    job = sync_now(client, s["id"])
    assert job["state"] == "done", job["message"]
    assert "--no-pictures" in job["argv"] and "--post-filter" not in job["argv"] and "--fast-update" not in job["argv"]
    # The video post, and the mixed carousel with its one video; image posts and the image carousel are not posts.
    assert shortcodes(client, "777") == ["CCAROMIX001", "CPOSTVID001"]
    names = sorted(os.listdir(env["media"] / "carol.cooks"))
    mixed = [n for n in names if "CCAROMIX001" in n and not n.endswith((".json", ".txt"))]
    assert mixed == ["carol.cooks-2024-06-05-CCAROMIX001_2.mp4"]
    assert not any(n.endswith(".jpg") for n in names)
    r = client.get("/api/posts?author=777&limit=100", headers=H).get_json()
    carousel = next(p for p in r["posts"] if p["post_id"] == "CCAROMIX001")
    assert carousel["kind"] == "carousel"


def x_account():
    def tweet(i, days, **extra):
        return {"id": str(1800000000000000000 + i), "ts": TS + days * DAY, "text": f"tweet {i}", "files": 1, **extra}
    return {"category": "twitter", "user": {"id": 900, "name": "someone", "nick": "Some One"}, "default": "timeline",
            "posts": [tweet(1, -30, **{"in": ["timeline", "media", "tweets"]}),
                      tweet(2, 1, video=True, **{"in": ["timeline", "media", "tweets"]}),
                      tweet(3, 2, **{"in": ["with-replies"]}),
                      tweet(4, 3, **{"in": ["timeline", "media", "tweets"]})]}


def put_x(tools, account=None):
    tools["dl"].write_text(json.dumps({"accounts": {X: account or x_account()}, "fail": None}))


def tweet_ids(client):
    r = client.get("/api/posts?platform=twitter&limit=100", headers=H).get_json()
    return sorted(p["post_id"][-1] for p in r["posts"])


def test_x_media_then_replies(env, client, tools):
    put_x(tools)
    s = add(client, X, content=["media"], media="images")
    job = sync_now(client, s["id"])
    assert job["state"] == "done", (job["message"], [l["text"] for l in jobs.log(job["id"])["lines"]])
    assert job["argv"][job["argv"].index("skip=abort:5") + 1:job["argv"].index("-D")] == [
        "-o", "include=media", "--filter", "extension in exts_image"]
    assert tweet_ids(client) == ["1", "4"]                       # the video and the reply left out
    post(client, f"/api/sources/{s['id']}", {"options": {"content": ["media", "with_replies"], "media": "all"}})
    job = sync_now(client, s["id"])
    assert "include=media,with-replies" in job["argv"]
    assert tweet_ids(client) == ["1", "2", "3", "4"]


def test_last_n_is_for_the_first_sync_and_the_next_stops_there(env, client, tools):
    account = x_account()
    for p in account["posts"]:
        p.pop("in")
    put_x(tools, account)
    s = add(client, X, first_posts=2)
    job = sync_now(client, s["id"])
    assert after(job["argv"], "--post-range") == "1-2" and tweet_ids(client) == ["3", "4"]
    s = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    # Done once; the day of the oldest post it got is the floor now.
    assert (s["options"]["first_posts"], s["options"]["since"]) == (None, "2024-06-03")
    account["posts"].append({"id": "1800000000000000005", "ts": TS + 5 * DAY, "text": "new", "files": 1})
    put_x(tools, account)
    job = sync_now(client, s["id"])
    assert "--post-range" not in job["argv"] and after(job["argv"], "--date-after") == "2024-06-02T23:59:59"
    assert tweet_ids(client) == ["3", "4", "5"]                  # the older two never come


def yt_account(url, videos):
    if "tiktok" in url:
        return {"extractor_key": "TikTok", "uploader_id": "6800000000000000009", "uploader": "someone",
                "channel": "Some One", "uploader_url": "https://www.tiktok.com/@someone", "videos": videos}
    return {"extractor_key": "Youtube", "uploader_id": "@somechannel", "uploader": "somechannel",
            "channel": "somechannel", "channel_id": "UCexampleChannelAAAAAAA1",
            "uploader_url": "https://www.youtube.com/@somechannel", "videos": videos}


@pytest.mark.parametrize("url", [TIKTOK, YOUTUBE])
def test_last_n_over_a_seeded_archive_still_sets_a_floor(env, client, tools, url):
    import fake_downloaders
    import scanner
    videos = [{"id": f"VIDEO{i + 10:06d}", "ts": TS + i * DAY, "title": f"video {i}", "duration": 30}
              for i in range(-5, 1)]
    # The 3 newest were downloaded before, into the folder the source uses.
    folder = os.path.join(env["roots"][0], "tiktok" if "tiktok" in url else "youtube",
                          "someone" if "tiktok" in url else "somechannel")
    tools["dl"].write_text(json.dumps({"accounts": {url: yt_account(url, videos[3:])}, "fail": None}))
    assert fake_downloaders.yt_dlp_main(["--write-info-json", "-o", os.path.join(folder, sync.YT_DLP_NAME), url]) == 0
    scanner.run(env["roots"])
    tools["dl"].write_text(json.dumps({"accounts": {url: yt_account(url, videos)}, "fail": None}))
    s = add(client, url, first_posts=3)
    assert s["folder"] == folder and s["account"] is not None
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and job["result"]["added"] == 0, job["message"]
    s = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    # Nothing added, but the N it listed were indexed: the oldest of them is the floor.
    assert (s["options"]["first_posts"], s["options"]["since"]) == (None, "2024-05-30")
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and job["result"]["added"] == 0
    assert after(job["argv"], "--dateafter") == "20240530" and "--playlist-items" not in job["argv"]
    r = client.get(f"/api/posts?platform={s['platform']}&limit=100", headers=H).get_json()
    assert sorted(p["post_id"] for p in r["posts"]) == [v["id"] for v in videos[3:]]     # nothing older


def test_last_n_that_lists_nothing_known_keeps_it(env, client, tools):
    tools["dl"].write_text(json.dumps({"accounts": {TIKTOK: yt_account(TIKTOK, [])}, "fail": None}))
    s = add(client, TIKTOK, first_posts=3)
    assert sync_now(client, s["id"])["state"] == "done"
    s = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert (s["options"]["first_posts"], s["options"]["since"]) == (3, None)


def test_last_n_failed_first_sync_keeps_it(env, client, tools):
    tools["dl"].write_text(json.dumps({"accounts": {X: x_account()}, "fail": "429"}))
    s = add(client, X, first_posts=2)
    assert sync_now(client, s["id"])["state"] == "failed"
    assert client.get(f"/api/sources/{s['id']}", headers=H).get_json()["options"]["first_posts"] == 2


def test_yt_dlp_floor_and_last_n(env, client, tools):
    videos = [{"id": f"VIDEO{i:06d}", "ts": TS + i * DAY, "title": f"video {i}", "duration": 30} for i in range(-3, 4)]
    tools["dl"].write_text(json.dumps({"accounts": {"https://youtube.com/@somechannel/videos": {
        "extractor_key": "Youtube", "uploader_id": "@somechannel", "uploader": "somechannel",
        "channel": "somechannel", "channel_id": "UCexampleChannelAAAAAAA1",
        "uploader_url": "https://www.youtube.com/@somechannel", "videos": videos}}, "fail": None}))
    s = add(client, "https://youtube.com/@somechannel/videos", since="2024-06-01")
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and job["exit_code"] == 101     # stopped at the first older video
    r = client.get("/api/posts?platform=youtube&limit=100", headers=H).get_json()
    assert sorted(p["post_id"] for p in r["posts"]) == [f"VIDEO{i:06d}" for i in range(0, 4)]


def test_a_sources_session_and_no_edits_while_it_syncs(env, client, tools):
    tools["ig"].write_text(json.dumps({**ig_profile(), "delay": 0.5}))
    s = add(client, "carol.cooks", "instaloader")
    assert s["session"] == {"mode": "none"}                 # the tool's, for the form's login hint
    assert client.get("/api/sources", headers=H).get_json()["sources"][0]["session"] == {"mode": "none"}
    job = post(client, f"/api/sources/{s['id']}/sync", {})["job"]
    r = post(client, f"/api/sources/{s['id']}", {"options": {"full_history": True}}, 409)
    assert "queued or running" in r["error"]
    jobs.cancel(job["id"])
    ended(job["id"])
    assert post(client, f"/api/sources/{s['id']}", {"options": {"session": LOGIN}})["source"]["session"] == LOGIN


def test_last_n_floor_is_never_after_today(env, client):
    s = add(client, X, first_posts=1)
    conn = db.connect()
    started = int(time.time()) - 10
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    meta = os.path.join(s["folder"], "a.json")
    conn.execute("INSERT INTO posts(id, platform, post_id, author_id, kind, posted_at, saved_at, indexed_at, first_seen, "
                 "tool, meta_path) VALUES ('twitter:1', 'twitter', '1', '1', 'image', ?, ?, ?, ?, 'gallery-dl', ?)",
                 (int(tomorrow.timestamp()), started + 5, started + 5, started + 5, meta))
    conn.commit()
    floor = sync._first_posts_floor(conn, sources.row(conn, s["id"]), {"started_at": started})
    assert floor == date.today().isoformat()
