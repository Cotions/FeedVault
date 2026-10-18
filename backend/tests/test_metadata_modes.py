"""Metadata modes full / slim / none: each tool's setting (Settings → Sync)
and a source's own, the flags each command gets for them, and media with no
metadata file beside it indexed from its name (parsers' parse_filenames),
yt-dlp's embedded tags read back with ffprobe (the fake one)."""
import json
import os
import sys

import pytest

from conftest import H
from test_source_options import (DAY, TESTS, TIKTOK, TS, X, YOUTUBE, add, after, build, ig_profile, post, put_x,
                                 sync_now, tools, tweet_ids, x_account, yt_account)  # noqa: F401

import config
import jobs
import scanner
import scripts
import sync
from parsers import gallery_dl, yt_dlp


def setting(**modes):
    """Each tool's metadata mode, the rest of its settings kept."""
    cfg = config.load()
    for tool, mode in modes.items():
        tool = tool.replace("_", "-")
        cfg[tool] = {**(cfg.get(tool) or {}), "metadata": mode}
    config.save(cfg)


def get(client, url):
    r = client.get(url, headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def files(folder):
    return sorted(n for _, _, names in os.walk(folder) for n in names)


def lines(job):
    return [ln["text"] for ln in jobs.log(job["id"])["lines"]]


# ---------------------------------------------------------------------------
# The flags
# ---------------------------------------------------------------------------

def test_full_is_the_default_and_changes_nothing(env, client):
    for target, tool in [("carol.cooks", "instaloader"), (X, None), (YOUTUBE, None)]:
        s = add(client, target, tool)
        assert s["metadata"] == {"mode": "full", "inherited": "full", "setting": "full"}
    args = build(add(client, "dave.draws", "instaloader"))
    assert "--no-metadata-json" not in args and "--no-captions" not in args
    args = build(add(client, "https://x.com/other"))
    assert "--write-metadata" in args and "-f" not in args
    args = build(add(client, "https://youtube.com/@other"))
    assert "--write-info-json" in args and "--write-thumbnail" in args and "--embed-metadata" not in args
    assert after(args, "-o").endswith(sync.YT_DLP_NAME)


def test_none_per_tool(env, client):
    setting(instaloader="none", gallery_dl="none", yt_dlp="none")
    s = add(client, "carol.cooks", "instaloader")
    assert s["metadata"] == {"mode": "none", "inherited": "none", "setting": "none"}
    args = build(s)
    assert args[:2] == ["--no-metadata-json", "--no-captions"] or \
        {"--no-metadata-json", "--no-captions"} <= set(args)
    # The name keeps the date and shortcode, whatever the folder held.
    assert after(args, "--filename-pattern") == sync.DATED
    args = build(add(client, X))
    assert "--write-metadata" not in args and after(args, "-f") == sync.GALLERY_DL_BARE
    args = build(add(client, YOUTUBE))
    assert "--write-info-json" not in args and "--write-thumbnail" not in args
    assert args[args.index("--no-write-info-json"):args.index("--no-write-info-json") + len(sync.YT_DLP_EMBED)] \
        == sync.YT_DLP_EMBED
    assert os.path.basename(after(args, "-o")) == \
        "%(uploader_id)s-%(upload_date)s-%(id)s [%(extractor_key)s %(channel_id,uploader_id)s].%(ext)s"
    args = build(add(client, TIKTOK))
    assert os.path.basename(after(args, "-o")).endswith("[%(extractor_key)s %(uploader_id,channel_id)s].%(ext)s")


def test_a_source_sets_its_own(env, client):
    setting(gallery_dl="none")
    own = add(client, X, metadata="full")
    assert own["overrides"] == ["metadata"] and own["metadata"]["mode"] == "full"
    assert "--write-metadata" in build(own)
    plain = add(client, "https://x.com/other")
    assert "-f" in build(plain) and plain["metadata"] == {"mode": "none", "inherited": "none", "setting": "none"}
    # A platform's default over the setting; the source's own over both.
    post(client, "/api/config", {"source_defaults": {"gallery-dl": {"twitter": {"metadata": "full"}}}})
    assert "--write-metadata" in build(plain)
    assert get(client, f"/api/sources/{plain['id']}")["metadata"] == \
        {"mode": "full", "inherited": "full", "setting": "none"}
    post(client, f"/api/sources/{own['id']}", {"options": {"metadata": "none"}})
    assert "-f" in build(own)
    # Slim is instaloader's only.
    r = client.post("/api/sources", json={"target": "https://x.com/third", "options": {"metadata": "slim"}},
                    headers=H)
    assert r.status_code == 400
    s = add(client, "carol.cooks", "instaloader", metadata="slim")
    assert s["metadata"]["mode"] == "slim" and "--no-metadata-json" not in build(s)


def test_builtins_follow_the_setting(env):
    assert scripts.builtins() == scripts.BUILTINS
    setting(instaloader="none", gallery_dl="none", yt_dlp="none")
    got = scripts.builtins()
    for name, t in got.items():
        argv = t["argv"]
        assert "--write-metadata" not in argv and "--write-info-json" not in argv, name
        assert "No metadata files" in t["description"], name
        if argv[0] == "instaloader":
            assert argv[1:3] == sync.INSTALOADER_BARE
            assert after(argv, "--filename-pattern") == scripts.INSTALOADER_BARE_NAME
        elif argv[0] == "gallery-dl":
            assert after(argv, "-f") == sync.GALLERY_DL_BARE
        else:
            assert "--embed-metadata" in argv and after(argv, "-o") == "{root}/" + sync.yt_dlp_name("none")
    # What a script's template shows changes with it; the stored BUILTINS do not.
    assert "--write-metadata" in scripts.BUILTINS["gallery-dl-user"]["argv"]


# ---------------------------------------------------------------------------
# Syncs in none, and what is read back
# ---------------------------------------------------------------------------

def test_instaloader_none_sync(env, client, tools):
    tools["ig"].write_text(json.dumps(ig_profile()))
    setting(instaloader="none")
    s = add(client, "carol.cooks", "instaloader", content=["posts", "reels"])
    job = sync_now(client, s["id"])
    assert job["state"] == "done", lines(job)
    folder = env["media"] / "instagram" / "carol.cooks"
    names = files(folder)
    assert not [n for n in names if n.endswith((".json", ".json.xz", ".txt"))], names
    assert "carol.cooks-2024-06-02-CPOSTIMG001.jpg" in names
    r = client.get("/api/posts?author=carol.cooks&limit=100", headers=H).get_json()
    assert {p["post_id"] for p in r["posts"]} >= {"CPOSTIMG001", "CPOSTVID001", "CPOSTIMG002", "CREEL000001"}
    # No caption: no text. The next sync finds nothing new.
    assert get(client, "/api/posts/instagram/CPOSTIMG001")["text"] in ("", None)
    assert sync_now(client, s["id"])["result"]["added"] == 0


def test_gallery_dl_none_sync(env, client, tools):
    put_x(tools)
    setting(gallery_dl="none")
    s = add(client, X, content=["media"])
    job = sync_now(client, s["id"])
    assert job["state"] == "done", lines(job)
    names = files(env["media"])
    assert not [n for n in names if n.endswith(".json")], names
    assert "twitter 1800000000000000004 01 20240604-120000.jpg" in names
    assert tweet_ids(client) == ["1", "2", "4"]
    p = get(client, "/api/posts/twitter/1800000000000000004")
    assert p["posted_at"] == TS + 3 * DAY and p["author"]["handle"] == "someone"
    assert p["url"] == "https://x.com/someone/status/1800000000000000004"
    v = get(client, "/api/posts/twitter/1800000000000000002")
    assert v["kind"] == "video"
    # gallery-dl's own archive keeps working: nothing again, nothing indexed twice.
    job = sync_now(client, s["id"])
    assert job["result"]["added"] == 0 and scanner.scan(env["roots"])["added"] == 0


def ffprobe_on(env):
    """The fake ffprobe where thumbs.ffprobe_path looks (PATH, the tools fixture's folder)."""
    exe = env["tmp"] / "bin" / "ffprobe"
    exe.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {TESTS!r})\n"
                   "import fake_ffprobe\nsys.exit(fake_ffprobe.main(sys.argv[1:]))\n")
    exe.chmod(0o755)


@pytest.mark.parametrize("probe", [True, False])
def test_yt_dlp_none_sync(env, client, tools, probe):
    if probe:
        ffprobe_on(env)
    videos = [{"id": "vid00000001", "ts": TS, "title": "First one", "description": "About #cats", "duration": 30},
              {"id": "vid00000002", "ts": TS + DAY, "title": "Second", "description": "", "duration": 40}]
    tools["dl"].write_text(json.dumps({"accounts": {YOUTUBE: yt_account(YOUTUBE, videos)}, "fail": None}))
    setting(yt_dlp="none")
    s = add(client, YOUTUBE)
    job = sync_now(client, s["id"])
    assert job["state"] == "done", lines(job)
    names = files(env["media"])
    assert names == ["@somechannel-20240601-vid00000001 [Youtube UCexampleChannelAAAAAAA1].mp4",
                     "@somechannel-20240602-vid00000002 [Youtube UCexampleChannelAAAAAAA1].mp4"], names
    p = get(client, "/api/posts/youtube/vid00000001")
    assert p["author"]["handle"] == "somechannel" and p["posted_at"] // DAY == TS // DAY
    assert p["url"] == "https://www.youtube.com/watch?v=vid00000001"
    if probe:
        assert p["text"] == "First one\n\nAbout #cats" and p["hashtags"] == ["cats"]
    else:
        assert p["text"] in ("", None)
    assert sync_now(client, s["id"])["result"]["added"] == 0


def test_tiktok_none_name_and_handle(env, client, tools):
    videos = [{"id": "7300000000000000001", "ts": TS, "title": "tt", "description": "tt", "duration": 9}]
    tools["dl"].write_text(json.dumps({"accounts": {TIKTOK: yt_account(TIKTOK, videos)}, "fail": None}))
    setting(yt_dlp="none")
    s = add(client, TIKTOK)
    job = sync_now(client, s["id"])
    assert job["state"] == "done", lines(job)
    [name] = files(env["media"])
    assert name == "6800000000000000009-20240601-7300000000000000001 [TikTok 6800000000000000009].mp4"
    p = get(client, "/api/posts/tiktok/7300000000000000001")
    # The uploader id is a number: the handle is the folder's.
    assert p["author"]["handle"] == "someone"
    assert p["url"] == "https://www.tiktok.com/@someone/video/7300000000000000001"


# ---------------------------------------------------------------------------
# The file name parsers
# ---------------------------------------------------------------------------

def touch(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"x")


def test_gallery_dl_names(tmp_path):
    folder = tmp_path / "tiktok" / "Someone"
    touch(folder, "tiktok 7300000000000000001 00 20240601-120000.mp4",
          "tiktok 7300000000000000001 00 20240601-120000.jpg",          # the video's poster
          "tiktok 7300000000000000002 00 20240602-120000.jpg",          # a slideshow's cover
          "tiktok 7300000000000000002 01 20240602-120000.jpg",
          "tiktok 7300000000000000002 02 20240602-120000.jpg",
          "tiktok 7300000000000000002 00 20240602-120000.mp3",
          "holiday 2024 1.jpg", "twitter None 01.jpg", "notes.txt")
    r = gallery_dl.parse_filenames(str(tmp_path), str(folder), os.listdir(folder))
    by = {p.post_id: p for p in r.posts}
    assert set(by) == {"7300000000000000001", "7300000000000000002"}
    v, s = by["7300000000000000001"], by["7300000000000000002"]
    assert (v.kind, [m.kind for m in v.media], v.media[0].poster_path is not None) == ("video", ["video"], True)
    assert (s.kind, len(s.media), s.posted_at) == ("carousel", 2, TS + DAY)
    assert {v.author_handle, v.tool} == {"someone", gallery_dl.FILENAMES}
    assert "holiday 2024 1.jpg" not in r.claimed and "notes.txt" not in r.claimed


def test_yt_dlp_names_and_tags(env, tmp_path, monkeypatch):
    folder = tmp_path / "youtube" / "somechannel"
    touch(folder, "@somechannel-20240601-vid00000001 [Youtube UCx].mkv", "NA-NA-abc [Generic NA].mp4",
          "My video [abc].mp4")
    r = yt_dlp.parse_filenames(str(tmp_path), str(folder), os.listdir(folder))
    by = {p.post_id: p for p in r.posts}
    assert set(by) == {"vid00000001", "abc"}
    assert (by["vid00000001"].platform, by["vid00000001"].author_id, by["vid00000001"].author_handle) == \
        ("youtube", "UCx", "somechannel")
    assert (by["abc"].platform, by["abc"].author_id, by["abc"].url) == ("generic", "somechannel", None)
    # Tags, read with ffprobe (the fake's): none, and none without one.
    assert yt_dlp.tags(str(folder / "My video [abc].mp4")) == {}
    exe = tmp_path / "ffprobe"
    exe.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {TESTS!r})\n"
                   "import fake_ffprobe\nsys.exit(fake_ffprobe.main(sys.argv[1:]))\n")
    exe.chmod(0o755)
    video = folder / "@somechannel-20240601-vid00000001 [Youtube UCx].mkv"
    video.write_bytes(b"x\nFAKETAGS " + json.dumps({"Title": "T", "DESCRIPTION": "D #x", "purl": "javascript:1",
                                                     "artist": "Some Channel"}).encode())
    assert yt_dlp.tags(str(video), str(exe)) == {"title": "T", "description": "D #x", "purl": "javascript:1",
                                                "artist": "Some Channel"}
    post = by["vid00000001"]
    orig = yt_dlp.tags
    monkeypatch.setattr(yt_dlp, "tags", lambda path, ffprobe=None: orig(path, str(exe)))
    post.enrich(post)
    # Only a web link is kept as the post's link.
    assert (post.text, post.hashtags, post.author_name, post.url) == \
        ("T\n\nD #x", ["x"], "Some Channel", "https://www.youtube.com/watch?v=vid00000001")
