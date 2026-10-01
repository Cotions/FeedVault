"""gallery-dl and yt-dlp sync: argv from the stored source, archives, never again."""
import collections
import json
import os
import sqlite3
import sys
import threading

import pytest

from conftest import H
from fakes import gallery_dl_case, yt_dlp_case

import archives
import config
import db
import info_cookies
import jobs
import scanner
import sources
import sync

TS = 1717243200                                     # 2024-06-01 12:00 UTC
DAY = 86400
TESTS = os.path.dirname(os.path.abspath(__file__))

X = "https://x.com/someone/media"
TT = "https://tiktok.com/@someone"
YT = "https://youtube.com/@somechannel"
USER = {"id": 900, "name": "someone", "nick": "Some One"}


def wait_for(pred, timeout=10):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("timed out")


def ended(job_id, timeout=10):
    wait_for(lambda: job_id not in jobs._active and jobs.get(job_id)["state"] not in ("queued", "running"), timeout)
    return jobs.get(job_id)


def get(client, url, status=200):
    r = client.get(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def post(client, url, body, status=200):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def set_config(**kw):
    cfg = config.load()
    cfg.update(kw)
    config.save(cfg)


class Fake:
    """The fake gallery-dl and yt-dlp on PATH, their accounts and the runs they saw."""

    def __init__(self, tmp):
        self.data, self.log = tmp / "downloads.json", tmp / "downloads.log"
        self.accounts = {}
        self.data.write_text(json.dumps({"accounts": {}, "fail": None}))

    def put(self, url, account, fail=None):
        self.accounts[url] = account
        self.data.write_text(json.dumps({"accounts": self.accounts, "fail": fail}))

    def runs(self, tool=None):
        runs = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [r for r in runs if tool is None or r["tool"] == tool]


@pytest.fixture
def fake(env, monkeypatch):
    monkeypatch.setattr(jobs, "_active", collections.OrderedDict())
    monkeypatch.setattr(jobs, "_closing", False)
    monkeypatch.setattr(jobs, "_cool", {})
    monkeypatch.setattr(jobs, "KILL_AFTER", 0.5)
    bin_dir = env["tmp"] / "bin"
    bin_dir.mkdir()
    for tool, main in (("gallery-dl", "gallery_dl_main"), ("yt-dlp", "yt_dlp_main")):
        exe = bin_dir / tool
        exe.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {TESTS!r})\n"
                       f"import fake_downloaders\nsys.exit(fake_downloaders.{main}(sys.argv[1:]))\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    f = Fake(env["tmp"])
    monkeypatch.setenv("FAKE_DOWNLOADS", str(f.data))
    monkeypatch.setenv("FAKE_DOWNLOADS_LOG", str(f.log))
    yield f
    jobs.shutdown()
    for t in threading.enumerate():
        if t.name.startswith("job-") and not t.name.endswith("-log"):
            t.join(10)


def x_account(*posts):
    return {"category": "twitter", "user": USER,
            "posts": [{"id": str(1800000000000000000 + i), "ts": TS + i * DAY, "text": f"tweet {i}", "files": n}
                      for i, n in posts]}


def tt_account(*ids):
    return {"extractor_key": "TikTok", "uploader_id": "6800000000000000009", "uploader": "someone",
            "channel": "Some One", "uploader_url": "https://www.tiktok.com/@someone",
            "videos": [{"id": str(7300000000000000000 + i), "ts": TS + i * DAY, "title": f"clip {i}",
                        "description": f"clip {i} #fun", "duration": 12} for i in ids]}


def yt_account(*videos):
    return {"extractor_key": "Youtube", "uploader_id": "@somechannel", "uploader": "somechannel",
            "channel": "somechannel", "channel_id": "UCexampleChannelAAAAAAA1",
            "uploader_url": "https://www.youtube.com/@somechannel",
            "videos": [{"id": f"VIDEO{i:06d}", "ts": TS + i * DAY, "title": f"video {i}", "duration": d}
                       for i, d in videos]}


def add(client, url, **body):
    return post(client, "/api/sources", {"target": url, **body})["source"]


def run_sync(client, sid):
    job = post(client, f"/api/sources/{sid}/sync", {})["job"]
    return ended(job["id"])


def archive_entries(env, tool):
    p = archives.path(tool, str(env["tmp"] / "data"))
    if not os.path.exists(p):
        return set()
    if tool == "gallery-dl":
        conn = sqlite3.connect(p)
        try:
            return {r[0] for r in conn.execute("SELECT entry FROM archive")}
        finally:
            conn.close()
    return set(open(p).read().splitlines())


# ---------------------------------------------------------------------------
# The argument list
# ---------------------------------------------------------------------------

def test_gallery_dl_argv(env, fake, client):
    s = add(client, X)
    spec = sync._build_gallery_dl({"source": str(s["id"])})
    data = str(env["tmp"] / "data")
    assert spec == {"tool": "gallery-dl", "rescan": s["folder"], "args": [
        "--write-metadata", "--download-archive", f"{data}/gallery-dl/archive.sqlite3", "-o", "skip=abort:5",
        "-D", s["folder"], "--", X]}
    post(client, f"/api/sources/{s['id']}", {"options": {"full_history": True,
                                                         "session": {"mode": "cookies", "browser": "firefox"}}})
    args = sync._build_gallery_dl({"source": str(s["id"])})["args"]
    assert args[args.index("-o") + 1] == "skip=true"
    assert args[-4:] == ["--cookies-from-browser", "firefox", "--", X]


def test_yt_dlp_argv(env, fake, client):
    s = add(client, TT)
    data = str(env["tmp"] / "data")
    assert sync._build_yt_dlp({"source": str(s["id"])})["args"] == [
        "--write-info-json", "--write-thumbnail", "--download-archive", f"{data}/yt-dlp/archive.txt",
        "-o", os.path.join(s["folder"], "%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s"), "--", TT]
    # YouTube: the ChannelVault rule as a filter, from the setting.
    set_config(youtube_max_seconds=90)
    y = add(client, YT)
    args = sync._build_yt_dlp({"source": str(y["id"])})["args"]
    assert args[args.index("--match-filters") + 1] == "duration <= 90"
    set_config(**{"yt-dlp": {"session": {"mode": "cookies", "browser": "chromium"}}})
    assert sync._build_yt_dlp({"source": str(y["id"])})["args"][-4:] == ["--cookies-from-browser", "chromium", "--", YT]


def test_youtube_channel_page_does_not_stop_at_the_first_tab(env, fake, client):
    # A channel's page lists Videos, then Shorts: no --break-on-existing there.
    root = add(client, YT)
    assert "--break-on-existing" not in sync._build_yt_dlp({"source": str(root["id"])})["args"]
    tab = add(client, "https://youtube.com/@somechannel/shorts", folder=str(env["media"] / "shorts"))
    assert "--break-on-existing" in sync._build_yt_dlp({"source": str(tab["id"])})["args"]
    for page in ("https://youtube.com/channel/UCexampleChannelAAAAAAA1", "https://youtube.com/@x/featured"):
        assert sync._youtube_root(page) is (page.endswith("1"))


def test_folder_escaped_for_yt_dlp(env, fake, client):
    folder = env["media"] / "100% clips {x}"
    s = add(client, TT, folder=str(folder))
    args = sync._build_yt_dlp({"source": str(s["id"])})["args"]
    assert args[args.index("-o") + 1] == str(env["media"] / "100%% clips {x}") + "/" + sync.YT_DLP_NAME
    # gallery-dl takes -D as it is: no format string.
    g = add(client, X, folder=str(folder))
    args = sync._build_gallery_dl({"source": str(g["id"])})["args"]
    assert args[args.index("-D") + 1] == str(folder)


def test_argv_only_from_the_stored_source(env, fake, client):
    s = add(client, X)
    conn = db.connect()
    # Hand-edited sources.json / database: each refused when the job is built.
    for column, value in [("target", "https://x.com/a; rm -rf ~"), ("target", "$(id)"), ("target", "--exec=id"),
                          ("target", "https://x.com.evil.example/a"), ("target", "https://www.x.com/someone"),
                          ("target", "https://x.com/someone/"), ("target", "https://x.com/a b"),
                          ("target", "https://x.com/a\n--exec=id"), ("folder", "/etc"),
                          ("folder", str(env["media"] / "$HOME")), ("folder", str(env["media"] / ".." / "x")),
                          ("tool", "yt-dlp")]:
        original = conn.execute(f"SELECT {column} FROM sources WHERE id = ?", (s["id"],)).fetchone()[0]
        conn.execute(f"UPDATE sources SET {column} = ? WHERE id = ?", (value, s["id"]))
        conn.commit()
        with pytest.raises(jobs.BadRequest):
            sync._build_gallery_dl({"source": str(s["id"])})
        conn.execute(f"UPDATE sources SET {column} = ? WHERE id = ?", (original, s["id"]))
        conn.commit()
    # A login (instaloader only) stored in the options: ignored, defaults.
    conn.execute("UPDATE sources SET options = ? WHERE id = ?",
                 (json.dumps({"session": {"mode": "login", "user": "me"}, "argv": ["--exec", "id"]}), s["id"]))
    conn.commit()
    args = sync._build_gallery_dl({"source": str(s["id"])})["args"]
    assert "--login" not in args and "--exec" not in args and args[-2:] == ["--", X]
    # A host taken out of the routing table: no sync.
    set_config(routes={"instagram.com": "instaloader"})
    assert "routing" in post(client, f"/api/sources/{s['id']}/sync", {}, 400)["error"]


def test_odd_but_valid_link_is_one_argument(env, fake, client):
    s = add(client, "https://x.com/a%24%28id%29%3Bb")       # %-encoded: nothing for a shell, and no shell anyway
    args = sync._build_gallery_dl({"source": str(s["id"])})["args"]
    assert args[-2:] == ["--", "https://x.com/a%24%28id%29%3Bb"]


def test_job_params_are_only_a_source_id(env, fake, client):
    s = add(client, TT)
    for params in [{"source": f"{s['id']} --exec id"}, {"source": "-1"}, {"source": str(s["id"]), "argv": "x"},
                   {"source": "1e3"}, {}]:
        r = post(client, "/api/jobs", {"kind": "yt-dlp-sync", "params": params}, 400)
        assert r["ok"] is False, params
    assert "not a gallery-dl" in post(client, "/api/jobs", {"kind": "gallery-dl-sync",
                                                        "params": {"source": str(s["id"])}}, 400)["error"]


# ---------------------------------------------------------------------------
# Syncs
# ---------------------------------------------------------------------------

def test_gallery_dl_sync_then_nothing_new(env, fake, client):
    fake.put(X, x_account((1, 2), (2, 1), (3, 1)))
    s = add(client, X)
    job = run_sync(client, s["id"])
    assert (job["state"], job["message"], job["label"]) == ("done", "3 new posts", "Sync x.com/someone/media")
    feed = get(client, "/api/posts?platform=twitter")["posts"]
    assert [p["id"] for p in feed] == [f"twitter:180000000000000000{i}" for i in (3, 2, 1)]
    src = get(client, f"/api/sources/{s['id']}")
    assert src["account"] == {"platform": "twitter", "id": "900"}
    assert src["last_result"]["added"] == 3
    assert len(archive_entries(env, "gallery-dl")) == 4
    # Second sync: everything is in the archive, gallery-dl stops at it.
    job = run_sync(client, s["id"])
    assert (job["state"], job["message"]) == ("done", "0 new posts")
    log = jobs.log(job["id"])["lines"]
    assert sum(1 for ln in log if ln["text"].startswith("# ")) == 4
    # A new tweet: only that one comes.
    fake.put(X, x_account((1, 2), (2, 1), (3, 1), (4, 1)))
    assert run_sync(client, s["id"])["message"] == "1 new post"


def test_yt_dlp_sync_then_nothing_new(env, fake, client):
    fake.put(TT, tt_account(1, 2))
    s = add(client, TT)
    job = run_sync(client, s["id"])
    assert (job["state"], job["message"]) == ("done", "2 new posts")
    p = get(client, "/api/posts/tiktok/7300000000000000002")
    assert p["source"]["tool"] == "yt-dlp" and p["text"] == "clip 2 #fun"
    assert archive_entries(env, "yt-dlp") == {"tiktok 7300000000000000001", "tiktok 7300000000000000002"}
    # TikTok: the whole listing, each archived video skipped.
    job = run_sync(client, s["id"])
    assert (job["state"], job["exit_code"], job["message"]) == ("done", 0, "0 new posts")
    assert sum("already been recorded in the archive" in ln["text"] for ln in jobs.log(job["id"])["lines"]) == 2


def test_yt_dlp_break_on_existing(env, fake, client):
    shorts = "https://youtube.com/@somechannel/shorts"
    fake.put(shorts, yt_account((1, 30), (2, 30)))
    s = add(client, shorts)
    assert run_sync(client, s["id"])["message"] == "2 new posts"
    # yt-dlp exits 101 at the first archived video: that is a success.
    job = run_sync(client, s["id"])
    assert (job["state"], job["exit_code"], job["message"]) == ("done", 101, "0 new posts")


def test_pinned_tiktok_video_does_not_stop_the_sync(env, fake, client):
    # The profile lists an old pinned video first; it is already archived.
    fake.put(TT, {**tt_account(1, 2), "pinned": ["7300000000000000001"]})
    s = add(client, TT)
    assert run_sync(client, s["id"])["message"] == "2 new posts"
    fake.put(TT, {**tt_account(1, 2, 3, 4), "pinned": ["7300000000000000001"]})
    job = run_sync(client, s["id"])
    assert (job["state"], job["message"]) == ("done", "2 new posts")
    assert {"tiktok 7300000000000000003", "tiktok 7300000000000000004"} <= archive_entries(env, "yt-dlp")
    # With --break-on-existing, as before, the pinned video would have stopped it.
    assert "--break-on-existing" not in fake.runs("yt-dlp")[-1]["argv"]


def test_youtube_long_videos_are_not_downloaded(env, fake, client):
    fake.put(YT, yt_account((1, 30), (2, 3600), (3, 170)))
    s = add(client, YT)
    job = run_sync(client, s["id"])
    assert job["message"] == "2 new posts"
    assert sorted(p["id"] for p in get(client, "/api/posts?platform=youtube")["posts"]) == [
        "youtube:VIDEO000001", "youtube:VIDEO000003"]
    # The channel's own info JSON came too: claimed, not unmatched.
    assert get(client, "/api/unmatched") == []
    assert get(client, f"/api/sources/{s['id']}")["account"] == {"platform": "youtube", "id": "UCexampleChannelAAAAAAA1"}


def test_failures_per_tool(env, fake, client):
    g = add(client, X)
    y = add(client, TT)
    for fail, error in [("429", "rate_limited"), ("login", "login_required"), ("private", "private"),
                        ("notfound", "not_found")]:
        fake.put(X, x_account((1, 1)), fail=fail)
        fake.put(TT, tt_account(1), fail=fail)
        for s in (g, y):
            job = run_sync(client, s["id"])
            assert job["state"] == "failed", (fail, s["tool"])
            assert job["result"]["error"] == error, (fail, s["tool"], job["result"])
            assert get(client, f"/api/sources/{s['id']}")["last_result"]["error"] == error
    fake.put(TT, tt_account(1), fail=None)
    assert run_sync(client, y["id"])["state"] == "done"


def test_one_item_failing_is_not_the_profile_failing():
    index = {"added": 3, "updated": 0}
    for tool, lines in [
            ("yt-dlp", ["[download] Destination: a.mp4", "ERROR: [youtube] AAAAAAAAAA1: Private video. Sign in if "
                                                         "you've been granted access to this video"]),
            ("yt-dlp", ["ERROR: [TikTok] 7300000000000000001: Video unavailable"]),
            ("gallery-dl", ["/a/1.jpg", "[download][error] Failed to download 2.jpg"])]:
        state, result, message = sync._outcome({}, 1, list(enumerate(lines)), index, tool)
        assert state == "done" and result["error"] is None, lines
        assert message.startswith("3 new posts; 1 item could not be downloaded: "), message
    for tool, lines, error in [
            ("yt-dlp", ["ERROR: [youtube:tab] @x: This channel does not exist"], "not_found"),
            ("yt-dlp", ["ERROR: [youtube] A: Private video", "ERROR: [tiktok:user] x: Unable to find user"],
             "private"),
            ("yt-dlp", ["ERROR: [youtube] A: Sign in to confirm you’re not a bot"], "login_required"),
            ("yt-dlp", ["ERROR: [youtube] A: HTTP Error 429: Too Many Requests"], "rate_limited"),
            ("gallery-dl", ["[twitter][error] NotFoundError: Requested user could not be found"], "not_found"),
            ("gallery-dl", ["something broke"], "generic")]:
        state, result, _ = sync._outcome({}, 1, list(enumerate(lines)), index, tool)
        assert (state, result["error"]) == ("failed", error), lines


def test_classification_lines():
    for line, error in [
            ("[twitter][error] HttpError: '429 Too Many Requests' for 'https://x.com/i/api'", "rate_limited"),
            ("[twitter][error] AuthorizationError: someone's Tweets are protected", "private"),
            ("[twitter][error] AuthRequired: 'auth_token' cookie needed", "login_required"),
            ("[tiktok][error] NotFoundError: Requested user could not be found", "not_found"),
            ("[gallery-dl][error] Unsupported URL 'https://example.com/a'", "generic")]:
        assert sync.classify([(1, line)], sync.GALLERY_DL_FAILURES)[0] == error, line
    for line, error in [
            ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser", "login_required"),
            ("ERROR: [TikTok] 123: HTTP Error 429: Too Many Requests", "rate_limited"),
            ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "private"),
            ("ERROR: [youtube] abc: Video unavailable", "not_found"),
            ("ERROR: [TikTok] someone: Unable to find user", "not_found"),
            ("ERROR: something else broke", "generic")]:
        assert sync.classify([(1, line)], sync.YT_DLP_FAILURES)[0] == error, line


def test_sync_all_runs_every_tool(env, fake, client):
    fake.put(X, x_account((1, 1)))
    fake.put(TT, tt_account(1))
    add(client, X)
    add(client, TT)
    r = post(client, "/api/sources/sync-all", {})
    assert sorted(j["kind"] for j in r["jobs"]) == ["gallery-dl-sync", "yt-dlp-sync"] and r["errors"] == []
    for j in r["jobs"]:
        assert ended(j["id"])["state"] == "done"
    assert get(client, "/api/stats")["posts"] == 2


# ---------------------------------------------------------------------------
# The first sync: seeding the archive
# ---------------------------------------------------------------------------

def test_first_gallery_dl_sync_skips_what_is_indexed(env, fake, client):
    # Downloaded by hand earlier, into another folder: the same tweets.
    fake.put(X, x_account((1, 2), (2, 1)))
    from fake_downloaders import _gallery_dl_files
    old = env["media"] / "gallery-dl" / "twitter" / "someone"
    old.mkdir(parents=True)
    for p in x_account((1, 2), (2, 1))["posts"]:
        for name, d in _gallery_dl_files(x_account(), p):
            (old / name).write_bytes(b"\xff\xd8\xff")
            (old / (name + ".json")).write_text(json.dumps(d))
    scanner.scan(env["roots"])
    assert get(client, "/api/stats")["posts"] == 2
    s = add(client, X, account={"platform": "twitter", "id": "900"})
    fake.put(X, x_account((1, 2), (2, 1), (3, 1)))
    job = run_sync(client, s["id"])
    assert (job["state"], job["message"]) == ("done", "1 new post")
    assert any("3 archive entries added for 2 posts" in ln["text"] for ln in jobs.log(job["id"])["lines"])
    assert sorted(os.listdir(s["folder"])) == ["1800000000000000003_1.jpg", "1800000000000000003_1.jpg.json"]


def test_gallery_dl_entries_match_the_fixtures(tmp_path):
    names = gallery_dl_case("twitter/four_photos", tmp_path)
    entries = [archives.gallery_dl_entry(json.load(open(tmp_path / n))) for n in names]
    assert entries == [f"twitter491623932184993703_0_{i}" for i in (1, 2, 3, 4)]
    names = gallery_dl_case("tiktok/video", tmp_path / "tt")
    entries = sorted(archives.gallery_dl_entry(json.load(open(tmp_path / "tt" / n))) for n in names)
    assert entries == ["tiktok3914719032600086255_0_", "tiktok3914719032600086255_0_a2810"]
    assert archives.gallery_dl_entry({"category": "twitter", "tweet_id": 1}) is None         # post-level JSON
    assert archives.gallery_dl_entry({"category": "unknown", "filename": "a", "extension": "jpg"}) is None


def test_first_yt_dlp_sync_skips_what_any_tool_indexed(env, fake, client):
    # The account's TikToks, downloaded with gallery-dl before: yt-dlp knows them by id.
    folder = env["media"] / "tiktok" / "someone"
    yt_dlp_case("tiktok/video", folder)
    scanner.scan(env["roots"])
    fake.put(TT, {**tt_account(5), "uploader_id": "6800000000000000002",
                  "videos": tt_account(5)["videos"] + [{"id": "7100000000000000001", "ts": TS - DAY, "duration": 9}]})
    s = add(client, TT, folder=str(folder))
    assert s["account"] == {"platform": "tiktok", "id": "6800000000000000002"}
    job = run_sync(client, s["id"])
    assert (job["state"], job["message"]) == ("done", "1 new post")
    assert archive_entries(env, "yt-dlp") == {"tiktok 7100000000000000001", "tiktok 7300000000000000005"}
    assert len(fake.runs("yt-dlp")) == 1


def test_seeding_with_full_history_but_not_after_the_first_sync(env, fake, client):
    folder = env["media"] / "tiktok" / "someone"
    yt_dlp_case("tiktok/video", folder)
    scanner.scan(env["roots"])
    fake.put(TT, {**tt_account(1), "uploader_id": "6800000000000000002"})
    s = add(client, TT, folder=str(folder), options={"full_history": True})
    run_sync(client, s["id"])
    # Full history walks the whole profile, but what is indexed is not fetched again.
    assert "tiktok 7100000000000000001" in archive_entries(env, "yt-dlp")
    assert get(client, f"/api/sources/{s['id']}")["options"]["full_history"] is False      # once is enough
    # Indexed later, by hand: the next sync does not seed again.
    yt_dlp_case("youtube/short", env["media"] / "tiktok" / "someone" / "later")
    scanner.scan(env["roots"])
    job = run_sync(client, s["id"])
    assert not any("archive entr" in ln["text"] for ln in jobs.log(job["id"])["lines"])


def test_first_sync_finds_the_account_by_its_handle(env, fake, client):
    # Downloaded earlier into another folder: the link's name is the account's handle.
    yt_dlp_case("tiktok/video", env["media"] / "old" / "tiktok")
    scanner.scan(env["roots"])
    s = add(client, "https://tiktok.com/@SomeBody_TT")
    assert s["account"] == {"platform": "tiktok", "id": "6800000000000000002"}
    fake.put("https://tiktok.com/@SomeBody_TT", {**tt_account(3), "uploader_id": "6800000000000000002"})
    run_sync(client, s["id"])
    assert "tiktok 7100000000000000001" in archive_entries(env, "yt-dlp")
    # No account has the name: none.
    assert add(client, "https://tiktok.com/@nobody")["account"] is None


def test_same_profile_twice_is_refused(env, client):
    add(client, "https://x.com/Someone")
    for again in ["https://x.com/someone", "https://twitter.com/someone"]:
        assert "already" in post(client, "/api/sources", {"target": again}, 400)["error"], again
        r = get(client, f"/api/sources/resolve?url={again}")
        assert r["source"] is not None, again
    # Another tool into the same folder is allowed (gallery-dl and yt-dlp for TikTok).
    add(client, "https://tiktok.com/@someone")
    set_config(routes={**sources.ROUTES, "tiktok.com": "gallery-dl"})
    add(client, "https://tiktok.com/@someone")


# ---------------------------------------------------------------------------
# Never again: trash adds to the archive, restore takes it out
# ---------------------------------------------------------------------------

def test_trashed_post_does_not_come_back(env, fake, client):
    fake.put(X, x_account((1, 2), (2, 1)))
    s = add(client, X)
    run_sync(client, s["id"])
    # Out of the archive (as if downloaded by hand): only the trash puts it back in.
    archives.remove("gallery-dl", archive_entries(env, "gallery-dl"), str(env["tmp"] / "data"))
    r = post(client, "/api/delete", {"posts": ["twitter:1800000000000000001"]})
    assert r["ok"] and r["posts"] == ["twitter:1800000000000000001"]
    assert archive_entries(env, "gallery-dl") == {"twitter1800000000000000001_0_1", "twitter1800000000000000001_0_2"}
    assert archive_entries(env, "yt-dlp") == {"twitter 1800000000000000001"}
    fake.put(X, x_account((1, 2), (2, 1), (3, 1)), )
    post(client, f"/api/sources/{s['id']}", {"options": {"full_history": True}})
    job = run_sync(client, s["id"])
    assert job["message"] == "1 new post"
    ids = sorted(p["id"] for p in get(client, "/api/posts?platform=twitter")["posts"])
    assert ids == ["twitter:1800000000000000002", "twitter:1800000000000000003"]
    # Restored: its entries are taken out again, the others stay.
    r = post(client, "/api/trash/restore", {"posts": ["twitter:1800000000000000001"]})
    assert r["ok"] and r["files"] == 4
    assert archive_entries(env, "gallery-dl") == {"twitter1800000000000000003_0_1"}
    assert archive_entries(env, "yt-dlp") == set()


def test_restore_keeps_entries_the_archive_had_before(env, fake, client):
    fake.put(TT, tt_account(1, 2))
    s = add(client, TT)
    run_sync(client, s["id"])
    before = archive_entries(env, "yt-dlp")
    post(client, "/api/delete", {"posts": ["tiktok:7300000000000000001"]})
    assert archive_entries(env, "yt-dlp") == before            # it was there: nothing added
    post(client, "/api/trash/restore", {"posts": ["tiktok:7300000000000000001"]})
    assert archive_entries(env, "yt-dlp") == before            # and so nothing taken out
    job = run_sync(client, s["id"])
    assert job["message"] == "0 new posts"


def test_trash_then_sync_yt_dlp(env, fake, client):
    fake.put(TT, tt_account(1, 2))
    s = add(client, TT)
    run_sync(client, s["id"])
    open(archives.path("yt-dlp", str(env["tmp"] / "data")), "w").close()      # emptied by hand
    post(client, "/api/delete", {"posts": ["tiktok:7300000000000000002"]})
    post(client, f"/api/sources/{s['id']}", {"options": {"full_history": True}})
    job = run_sync(client, s["id"])
    assert job["message"] == "0 new posts"
    assert [p["id"] for p in get(client, "/api/posts?platform=tiktok")["posts"]] == ["tiktok:7300000000000000001"]


def test_one_item_trashed_on_its_own(env, fake, client):
    fake.put(X, x_account((1, 3)))
    s = add(client, X)
    run_sync(client, s["id"])
    archives.remove("gallery-dl", archive_entries(env, "gallery-dl"), str(env["tmp"] / "data"))
    media = get(client, "/api/posts/twitter/1800000000000000001")["media"]
    post(client, "/api/delete", {"media": [media[1]["id"]]})
    assert archive_entries(env, "gallery-dl") == {"twitter1800000000000000001_0_2"}
    assert archive_entries(env, "yt-dlp") == set()             # the post stays
    keys = [e["key"] for e in get(client, "/api/trash/items")["entries"]]
    post(client, "/api/trash/restore", {"keys": keys})
    assert archive_entries(env, "gallery-dl") == set()


def test_instaloader_posts_add_nothing(env, fake, client):
    from fakes import owner, write_post
    write_post(env["media"] / "alice", "AAA111", TS, owner("alice", 1), "image")
    scanner.scan(env["roots"])
    post(client, "/api/delete", {"posts": ["instagram:AAA111"]})
    assert not os.path.exists(env["tmp"] / "data" / "gallery-dl") and not os.path.exists(env["tmp"] / "data" / "yt-dlp")
    lines = [json.loads(ln) for ln in open(env["media"] / ".feedvault-trash" / ".manifest.jsonl")]
    assert all("archive" not in ln for ln in lines)


def test_trash_that_moves_nothing_takes_its_entries_back(env, fake, client, monkeypatch):
    import trash
    fake.put(TT, tt_account(1))
    s = add(client, TT)
    run_sync(client, s["id"])
    open(archives.path("yt-dlp", str(env["tmp"] / "data")), "w").close()

    def refuse(path, roots, line):
        raise trash.TrashError("read-only")
    monkeypatch.setattr(trash, "_move", refuse)
    r = post(client, "/api/delete", {"posts": ["tiktok:7300000000000000001"]})
    assert r["posts"] == [] and r["errors"]
    assert archive_entries(env, "yt-dlp") == set()             # the post stays, and can sync again


# ---------------------------------------------------------------------------
# The archive files
# ---------------------------------------------------------------------------

def test_archive_file_kept_intact_by_concurrent_appends(env):
    data = str(env["tmp"] / "data")
    archives.add("yt-dlp", ["tiktok 1", "tiktok 2"], data)
    p = archives.path("yt-dlp", data)
    with open(p, "a") as f:                                    # yt-dlp appending meanwhile
        f.write("tiktok 3\n")
    assert archives.remove("yt-dlp", ["tiktok 1"], data) == 1
    assert open(p).read() == "tiktok 2\ntiktok 3\n"
    assert archives.add("yt-dlp", ["tiktok 2", "tiktok 4", "bad\nline", "bad\rline", "bad\u2028line"], data) == ["tiktok 4"]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_tool_settings_in_config(env, client):
    cfg = get(client, "/api/config")
    assert cfg["gallery-dl"] == {"session": {"mode": "none"}} and cfg["yt-dlp"] == {"session": {"mode": "none"}}
    assert cfg["youtube_max_seconds"] == 180
    r = post(client, "/api/config", {"gallery-dl": {"session": {"mode": "cookies", "browser": "firefox"}},
                                      "youtube_max_seconds": 600})
    assert r["ok"] and r["config"]["gallery-dl"]["session"] == {"mode": "cookies", "browser": "firefox"}
    assert r["config"]["youtube_max_seconds"] == 600
    for bad in [{"gallery-dl": {"session": {"mode": "login", "user": "me"}}},
                {"yt-dlp": {"session": {"mode": "cookies", "browser": "netscape; id"}}},
                {"yt-dlp": {"cookies": "/home/me/cookies.txt"}}, {"yt-dlp": "x"},
                {"youtube_max_seconds": 0}, {"youtube_max_seconds": "60"}, {"youtube_max_seconds": True}]:
        assert post(client, "/api/config", bad)["ok"] is False, bad
    assert config.load()["youtube_max_seconds"] == 600


def test_source_session_is_cookies_or_none(env, client):
    for session in [{"mode": "login", "user": "me"}, {"mode": "cookies", "browser": "../../x"}]:
        assert post(client, "/api/sources", {"target": X, "options": {"session": session}}, 400)["ok"] is False
    s = add(client, X, options={"session": {"mode": "cookies", "browser": "brave"}})
    assert s["options"]["session"] == {"mode": "cookies", "browser": "brave"}


# ---------------------------------------------------------------------------
# Cookies out of yt-dlp's info JSONs
# ---------------------------------------------------------------------------

TT_INFO = os.path.join(TESTS, "fixtures", "yt_dlp", "tiktok", "video",
                       "6800000000000000002-20190727-7100000000000000001.info.json")


def with_cookies(d):
    """A real info JSON as a sync with cookies writes it: cookies at the top,
    in a format and in the http_headers of both."""
    d = json.loads(json.dumps(d))
    d["cookies"] = "sessionid=SECRET; Domain=.tiktok.com; Path=/"
    d["http_headers"]["Cookie"] = "sessionid=SECRET"
    d["formats"][0]["cookies"] = "sessionid=SECRET"
    d["formats"][0]["http_headers"] = {"User-Agent": "Mozilla/5.0", "cookie": "sessionid=SECRET"}
    return d


def secret_in(path):
    return "SECRET" in open(path, encoding="utf-8").read()


def test_strip_takes_out_cookies_and_nothing_else():
    original = json.load(open(TT_INFO, encoding="utf-8"))
    cleaned, changed = info_cookies.strip(with_cookies(original))
    expected = {k: v for k, v in original.items() if k != "cookies"}
    expected["formats"] = [dict(original["formats"][0], http_headers={"User-Agent": "Mozilla/5.0"}),
                           *original["formats"][1:]]
    assert changed and cleaned == expected
    assert info_cookies.strip(expected) == (expected, False)
    # A "cookies" key at any depth, a Cookie header only inside http_headers.
    assert info_cookies.strip({"a": [{"b": {"cookies": 1, "Cookie": 2}}]}) == ({"a": [{"b": {"Cookie": 2}}]}, True)


def test_clean_is_atomic_and_keeps_mode_and_mtime(tmp_path):
    path = tmp_path / "x.info.json"
    path.write_text(json.dumps(with_cookies(json.load(open(TT_INFO, encoding="utf-8")))))
    os.chmod(path, 0o640)
    os.utime(path, (1_600_000_000, 1_600_000_000))
    inode = os.stat(path).st_ino
    assert info_cookies.clean(str(path), apply=False) is True and secret_in(path)    # a count only
    assert info_cookies.clean(str(path)) is True
    st = os.stat(path)
    assert not secret_in(path) and st.st_mtime == 1_600_000_000 and st.st_mode & 0o777 == 0o640
    assert st.st_ino != inode                                   # renamed over, not written in place
    assert os.listdir(tmp_path) == ["x.info.json"]              # no temporary file left
    assert info_cookies.clean(str(path)) is False               # nothing left to take out


def test_clean_leaves_what_is_not_yt_dlp_alone(tmp_path):
    other = tmp_path / "a.info.json"
    other.write_text(json.dumps({"category": "twitter", "cookies": "SECRET"}))        # not yt-dlp's
    broken = tmp_path / "b.info.json"
    broken.write_text('{"cookies": "SECRET", ')
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(with_cookies(json.load(open(TT_INFO, encoding="utf-8")))))
    link = tmp_path / "c.info.json"
    link.symlink_to(outside)
    for p in (other, broken, link):
        assert info_cookies.clean(str(p)) is False
    assert secret_in(other) and secret_in(broken) and secret_in(outside) and link.is_symlink()


def test_sync_with_cookies_cleans_the_info_jsons_it_wrote(env, fake, client):
    fake.put(TT, tt_account(1))
    s = add(client, TT, options={"session": {"mode": "cookies", "browser": "firefox"}})
    folder = s["folder"]
    os.makedirs(folder)
    # An info JSON from before the job, and one outside the source's folder written meanwhile.
    before = os.path.join(folder, "old-20200101-1.info.json")
    with open(before, "w") as f:
        json.dump(with_cookies(json.load(open(TT_INFO, encoding="utf-8"))), f)
    os.utime(before, (1_600_000_000, 1_600_000_000))
    elsewhere = env["media"] / "elsewhere" / "x.info.json"
    elsewhere.parent.mkdir()
    elsewhere.write_text(open(before).read())
    job = run_sync(client, s["id"])
    assert job["state"] == "done"
    [new] = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".info.json") and n != os.path.basename(before)]
    d = json.load(open(new))
    assert "cookies" not in json.dumps(d) and all("Cookie" not in f["http_headers"] for f in d["formats"])
    assert d["http_headers"] == {"User-Agent": "Mozilla/5.0", "Accept": "*/*"} and len(d["formats"]) == 2
    assert d["formats"][1]["url"].endswith("h264_720p.mp4") and d["title"] == "clip 1"
    log = [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert "[feedvault] cookies removed from 1 info JSON" in log
    # Cleaned before the rescan: the post is indexed from the cleaned file.
    assert log.index("[feedvault] cookies removed from 1 info JSON") < next(
        i for i, t in enumerate(log) if t.startswith("[feedvault] indexing"))
    assert secret_in(before) and os.stat(before).st_mtime == 1_600_000_000 and secret_in(elsewhere)


def test_sync_without_cookies_rewrites_nothing(env, fake, client):
    fake.put(TT, tt_account(1))
    s = add(client, TT)
    job = run_sync(client, s["id"])
    assert not any("[feedvault] cookies" in ln["text"] for ln in jobs.log(job["id"])["lines"])


def test_cookie_cleaning_failure_does_not_fail_the_sync(env, fake, client, monkeypatch):
    def refuse(path, apply=True):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(info_cookies, "clean", refuse)
    fake.put(TT, tt_account(1))
    s = add(client, TT, options={"session": {"mode": "cookies", "browser": "firefox"}})
    job = run_sync(client, s["id"])
    assert (job["state"], job["message"]) == ("done", "1 new post")
    assert any(ln["text"].startswith("[feedvault] could not remove the cookies from ")
               and ln["text"].endswith(": Permission denied") for ln in jobs.log(job["id"])["lines"])


def test_settings_action_counts_then_cleans_inside_the_roots(env, fake, client, tmp_path):
    dirty = with_cookies(json.load(open(TT_INFO, encoding="utf-8")))
    paths = [env["media"] / "tiktok" / "a" / "1.info.json", env["media"] / ".feedvault-trash" / "2.info.json"]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(dirty))
    clean = env["media"] / "clean.info.json"
    clean.write_text(json.dumps(info_cookies.strip(json.load(open(TT_INFO, encoding="utf-8")))[0]))
    gallery = env["media"] / "g.info.json"
    gallery.write_text(json.dumps({"category": "twitter", "cookies": "SECRET"}))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "3.info.json").write_text(json.dumps(dirty))
    (env["media"] / "linked").symlink_to(outside)                 # a symlinked folder is not followed
    (env["media"] / "4.info.json").symlink_to(outside / "3.info.json")
    r = post(client, "/api/yt-dlp/info-json-cookies", {})
    assert (r["applied"], r["checked"], r["files"], r["failures"]) == (False, 5, 2, 0)
    assert all(secret_in(p) for p in paths)
    r = post(client, "/api/yt-dlp/info-json-cookies", {"apply": True})
    assert (r["applied"], r["files"]) == (True, 2)
    assert not any(secret_in(p) for p in paths)
    assert secret_in(outside / "3.info.json") and secret_in(gallery)
    assert post(client, "/api/yt-dlp/info-json-cookies", {})["files"] == 0
    assert post(client, "/api/yt-dlp/info-json-cookies", {"apply": "yes"}, 400)["ok"] is False
    assert client.post("/api/yt-dlp/info-json-cookies", json={"apply": True}).status_code == 403


def test_settings_action_reports_failures(env, fake, client, monkeypatch):
    p = env["media"] / "1.info.json"
    p.write_text(json.dumps(with_cookies(json.load(open(TT_INFO, encoding="utf-8")))))
    real = info_cookies.clean

    def failing(path, apply=True):
        if apply:
            raise PermissionError(13, "Permission denied")
        return real(path, apply)
    monkeypatch.setattr(info_cookies, "clean", failing)
    r = post(client, "/api/yt-dlp/info-json-cookies", {"apply": True})
    assert (r["files"], r["failures"], r["failed"]) == (0, 1, [{"path": str(p), "error": "Permission denied"}])


def test_settings_action_waits_for_a_running_yt_dlp_sync(env, client, monkeypatch):
    monkeypatch.setattr(jobs, "active", lambda: [{"kind": "yt-dlp-sync", "state": "running"}])
    assert post(client, "/api/yt-dlp/info-json-cookies", {}, 409)["ok"] is False
