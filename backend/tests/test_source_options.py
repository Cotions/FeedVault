"""What a source downloads: its options turned into each tool's flags (sync.py)."""
import configparser
import json
from datetime import datetime, timedelta, timezone

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
        "--no-posts", "--stories",
        "--post-filter", "is_video and date_utc >= datetime(2024, 2, 29)",
        "--storyitem-filter", "is_video and date_utc >= datetime(2024, 2, 29)",
        "--login", "me", "--", "dave.draws"]
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
    want = build(s)
    set_stored(s["id"], options)
    args = build(s)
    assert args == want                                  # the defaults, the stored value ignored
    assert not any("import" in a or "system" in a or "; id" in a for a in args)
    # And through the API: refused.
    assert post(client, f"/api/sources/{s['id']}", {"options": options}, 400)["ok"] is False


def test_filters_are_fixed_text_and_a_date():
    for since in ["1970-01-01", "2024-02-29", "2026-10-02"]:
        y, m, d = (int(p) for p in since.split("-"))
        options = sources.clean_options({"since": since, "media": "videos"})
        assert sync.item_filter(options) == f"is_video and date_utc >= datetime({y}, {m}, {d})"


# ---------------------------------------------------------------------------
# Floors and stamps
# ---------------------------------------------------------------------------

def stamp(target, key):
    return datetime.strptime(stamps().get(target, key), sync.STAMP_FORMAT)


def test_floor_raises_the_stamps_after_the_seed(env, client):
    s = add(client, "carol.cooks", "instaloader", content=["posts", "reels", "tagged", "highlights"],
            session=LOGIN, since="2024-01-01")
    notes = []
    sync._seed_stamp(db.connect(), sources.row(db.connect(), s["id"]), sync._options(sources.row(db.connect(), s["id"])),
                     notes.append)
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
    s = add(client, "carol.cooks", "instaloader", full_history=True, since="2024-01-01")
    c = configparser.ConfigParser(interpolation=None)
    c.read_dict({"carol.cooks": {"post-timestamp": "2024-06-01T12:00:00.000000+0000"}})
    sync._write_stamps(c, sync.stamps_path())
    row = sources.row(db.connect(), s["id"])
    sync._seed_stamp(db.connect(), row, sync._options(row), lambda text: None)
    assert stamp("carol.cooks", "post-timestamp").date().isoformat() == "2023-12-31"


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
