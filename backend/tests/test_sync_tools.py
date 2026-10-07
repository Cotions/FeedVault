"""gallery-dl and yt-dlp sync: argv from the stored source, archives, never again."""
import collections
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

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

    def put(self, url, account, fail=None, config_cookies=False, config_no_metadata=False):
        self.accounts[url] = account
        self.data.write_text(json.dumps({"accounts": self.accounts, "fail": fail, "config_cookies": config_cookies,
                                         "config_no_metadata": config_no_metadata}))

    def runs(self, tool=None):
        runs = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [r for r in runs if tool is None or r["tool"] == tool]


@pytest.fixture
def fake(env, monkeypatch):
    monkeypatch.setattr(jobs, "_active", collections.OrderedDict())
    monkeypatch.setattr(jobs, "_closing", False)
    monkeypatch.setattr(jobs, "_cool", {})
    monkeypatch.setattr(jobs, "_wake", None)
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
    set_config(**{"gallery-dl": {"pause": 0}, "yt-dlp": {"pause": 0}})
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


def test_yt_dlp_that_cannot_start_leaves_nothing_behind(env, fake, client, monkeypatch):
    """#31: the after hook never runs then; the folder listing taken by the start goes too."""
    fake.put(TT, tt_account(1))
    s = add(client, TT)
    real = subprocess.Popen

    def popen(argv, *a, **kw):
        if os.path.basename(argv[0]) == "yt-dlp":
            raise PermissionError(13, "Permission denied")
        return real(argv, *a, **kw)
    monkeypatch.setattr(jobs.subprocess, "Popen", popen)
    job = run_sync(client, s["id"])
    assert job["state"] == "failed" and job["message"] == "yt-dlp could not start: Permission denied"
    assert sync._info_before == {}


def test_yt_dlp_start_failure_drops_the_listing_even_if_an_ended_step_fails(env, fake, client, monkeypatch):
    """#31: the listing goes before anything else the ended hook does."""
    fake.put(TT, tt_account(1))
    s = add(client, TT)
    real = subprocess.Popen

    def popen(argv, *a, **kw):
        if os.path.basename(argv[0]) == "yt-dlp":
            assert sync._info_before                # the start listed the folder
            raise PermissionError(13, "Permission denied")
        return real(argv, *a, **kw)

    def broken(job):
        raise OSError("disk gone")
    monkeypatch.setattr(jobs.subprocess, "Popen", popen)
    monkeypatch.setattr(sync, "_mark_muted", broken)
    job = run_sync(client, s["id"])
    assert job["state"] == "failed" and job["message"] == "yt-dlp could not start: Permission denied"
    assert sync._info_before == {}


def test_a_sync_cancelled_while_queued_keeps_the_running_ones_listing(env, monkeypatch):
    """Only a job that ran drops the listing: one cancelled in the queue leaves its running sibling's."""
    monkeypatch.setattr(sync, "_info_before", {7: ("/m/someone", {})})
    sync._ended({"id": 99, "state": "cancelled", "started_at": None, "params": {"source": "7"},
                 "result": None, "label": "Sync"})
    assert sync._info_before == {7: ("/m/someone", {})}


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


@pytest.mark.parametrize("fail, state, message", [
    (None, "done", "Works"),
    ("login", "failed", "Login required: the site refused it without a session; set one in its sync settings"),
    ("429", "failed", "Rate limited: the site is limiting requests, try again later")])
@pytest.mark.parametrize("tool", ["gallery-dl", "yt-dlp"])
def test_the_fakes_answer_the_downloaders_test(env, fake, tool, fail, state, message):
    """#35: the demo's fake tools take the Test's --simulate (and --no-playlist)."""
    import downloaders                         # registers tool-test
    fake.put(TT, tt_account(1), fail=fail)
    job = ended(jobs.submit("tool-test", {"tool": tool})["id"])
    assert "--simulate" in job["argv"] and (job["state"], job["message"]) == (state, message)
    assert not (env["tmp"] / "data" / "downloaders" / "test").exists() \
        or not os.listdir(env["tmp"] / "data" / "downloaders" / "test")


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
            # yt-dlp's TikTok user that does not exist says only that it found no user id,
            # as for a private profile with embedding off: not a guess, an error (health.py).
            want = "generic" if (fail, s["tool"]) == ("notfound", "yt-dlp") else error
            job = run_sync(client, s["id"])
            assert job["state"] == "failed", (fail, s["tool"])
            assert job["result"]["error"] == want, (fail, s["tool"], job["result"])
            assert get(client, f"/api/sources/{s['id']}")["last_result"]["error"] == want
    fake.put(TT, tt_account(1), fail=None)
    assert run_sync(client, y["id"])["state"] == "done"


def test_private_message_follows_the_cookies_in_use(env, fake, client):
    """#124: with no cookies, a private profile's message says where to set
    them (Settings → Sync), not that the cookies in use lack access."""
    g = add(client, X)
    y = add(client, TT)
    fake.put(X, x_account((1, 1)), fail="private")
    fake.put(TT, tt_account(1), fail="private")
    for s in (g, y):
        job = run_sync(client, s["id"])
        assert (job["result"]["error"], job["result"]["login"]["mode"]) == ("private", "none"), s["tool"]
        assert job["message"] == sync.NO_SESSION["tools"], s["tool"]
        assert "Settings → Sync" in job["message"] and "cookies in use do" not in job["message"]
    # The tool's cookies, then the source's own: the message the cookies can explain.
    set_config(**{"gallery-dl": {"pause": 0, "session": {"mode": "cookies", "browser": "firefox"}}})
    post(client, f"/api/sources/{y['id']}", {"options": {"session": {"mode": "cookies", "browser": "firefox"}}})
    for s in (g, y):
        job = run_sync(client, s["id"])
        assert (job["result"]["error"], job["result"]["login"]["mode"]) == ("private", "cookies"), s["tool"]
        assert job["message"] == sync.TOOL_MESSAGES["private"] == \
            "Private profile: the cookies in use do not have access to it", s["tool"]


def test_one_item_failing_is_not_the_profile_failing():
    index = {"added": 3, "updated": 0}
    for tool, lines in [
            ("yt-dlp", ["[download] Destination: a.mp4", "ERROR: [youtube] AAAAAAAAAA1: Private video. Sign in if "
                                                         "you've been granted access to this video"]),
            ("yt-dlp", ["ERROR: [TikTok] 7300000000000000001: Video unavailable"]),
            ("gallery-dl", ["/a/1.jpg", "[download][error] Failed to download 2.jpg"])]:
        state, result, message = sync._outcome({}, 1, list(enumerate(lines)), index, None, tool)
        assert state == "done" and result["error"] is None, lines
        assert message.startswith("3 new posts; 1 item could not be downloaded: "), message
    for tool, lines, error in [
            ("yt-dlp", ["ERROR: [youtube:tab] @x: Unable to download webpage: HTTP Error 404: Not Found "
                        "(caused by <HTTPError 404: Not Found>)"], "not_found"),
            ("yt-dlp", ["ERROR: [youtube] A: Private video", "ERROR: [tiktok:user] x: This user's account is "
                        "private. Log into an account that has access"], "private"),
            ("yt-dlp", ["ERROR: [youtube] A: Sign in to confirm you’re not a bot"], "login_required"),
            ("yt-dlp", ["ERROR: [youtube] A: HTTP Error 429: Too Many Requests"], "rate_limited"),
            ("gallery-dl", ["[twitter][error] NotFoundError: Requested user could not be found"], "not_found"),
            ("gallery-dl", ["something broke"], "generic")]:
        state, result, _ = sync._outcome({}, 1, list(enumerate(lines)), index, None, tool)
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
            ("ERROR: [youtube:tab] @x: YouTube said: This channel does not exist.", "not_found"),
            ("ERROR: [youtube:tab] @x: The channel/playlist does not exist and the URL redirected to youtube.com "
             "home page", "not_found"),
            ("ERROR: [tiktok:user] x: TikTok is requiring login for access to this content", "login_required"),
            ("ERROR: [tiktok:user] x: Unable to extract secondary user ID. If you are able to get the channel_id",
             "generic"),
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


def fake_gallery_dl_package(tmp, formats, monkeypatch):
    """A gallery-dl install whose Python has a ``gallery_dl`` package with
    these extractor classes ([category, subcategory, archive_fmt]); every
    listing is counted in ``runs``. Set as the tool in Settings."""
    pkg = tmp / "gdl-site" / "gallery_dl"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    runs = tmp / "gdl-runs"
    classes = "".join(f"    type('E{i}', (), {{'category': {c!r}, 'subcategory': {sub!r}, 'archive_fmt': {fmt!r}}}),\n"
                      for i, (c, sub, fmt) in enumerate(formats))
    (pkg / "extractor.py").write_text(f"open({str(runs)!r}, 'a').write('x')\n"
                                      f"def extractors():\n    return [\n{classes}    ]\n")
    python = tmp / "gdl-bin" / "python3"
    python.parent.mkdir()
    python.write_text(f"#!/bin/sh\nPYTHONPATH={tmp / 'gdl-site'}:$PYTHONPATH exec {sys.executable} \"$@\"\n")
    python.chmod(0o755)
    exe = tmp / "gdl-bin" / "gallery-dl"
    exe.write_text(f"#!{python}\nimport sys\nsys.path.insert(0, {TESTS!r})\n"
                   "import fake_downloaders\nsys.exit(fake_downloaders.gallery_dl_main(sys.argv[1:]))\n")
    exe.chmod(0o755)
    set_config(tools={"gallery-dl": str(exe)})
    monkeypatch.setattr(archives, "_formats", {"key": None, "formats": None, "error": None, "last": None})
    return runs


def test_safe_formats():
    for ok in ["{id}", "{tweet_id}_{retweet_id}_{num}", "{id}{suffix}.{extension}", "{asset[id]}",
               "a_{album[id]}_{asset[id]}", "{num:>02}", "{num:02}"]:
        assert archives._safe_format(ok), ok
    for bad in ["", "{id|slug}", "{x.__class__}", "{a!r}", "{date:%Y%m%d}", "{num:>99999999}", "{path:J/}",
                "{gallery_id:? / /}", "{", "{0}", "x" * 201, None, 5]:
        assert not archives._safe_format(bad), bad


def test_archive_formats_from_the_installed_gallery_dl(env, fake, client, monkeypatch):
    runs = fake_gallery_dl_package(env["tmp"], [
        ["twitter", "media", "{tweet_id}_{retweet_id}_{num}"], ["twitter", "tweet", "{tweet_id}_{retweet_id}_{num}"],
        ["newsite", "user", "{post[id]}_{num}"], ["newsite", "post", "p{post[id]}_{num}"],
        ["odd", "user", "{id|slug}"], ["evil", "user", "{x.__class__}"],
        ["mixed", "user", "{id}"], ["mixed", "post", "{id|slug}"]], monkeypatch)
    formats, error = archives.installed_formats()
    assert error is None
    assert formats == {("twitter", "media"): "{tweet_id}_{retweet_id}_{num}",
                       ("twitter", "tweet"): "{tweet_id}_{retweet_id}_{num}",
                       ("twitter", None): "{tweet_id}_{retweet_id}_{num}",
                       ("newsite", "user"): "{post[id]}_{num}", ("newsite", "post"): "p{post[id]}_{num}",
                       ("mixed", "user"): "{id}"}     # not ("mixed", None): its "post" files are not "{id}"
    # Read once, kept: not run again, not for the trash either.
    assert archives.installed_formats() == (formats, None) and archives.installed_formats(run=False)[0] == formats
    assert runs.read_text() == "x"
    d = {"category": "newsite", "subcategory": "post", "post": {"id": 12}, "num": 1, "filename": "a",
         "extension": "jpg"}
    assert archives.gallery_dl_entry(d, formats) == "newsitep12_1"
    assert archives.gallery_dl_entry({**d, "subcategory": "user"}, formats) == "newsite12_1"
    assert archives.gallery_dl_entry({**d, "subcategory": "other"}, formats) is None       # no one format for it
    assert archives.gallery_dl_entry(d) is None                                             # not in the table
    # A new version of the file: read again by the next seed; the trash
    # uses the last formats read until then.
    exe = config.load()["tools"]["gallery-dl"]
    os.utime(exe, (TS, TS))
    assert archives.installed_formats(run=False) == (formats, None)
    archives.installed_formats()
    assert runs.read_text() == "xx"


@pytest.mark.parametrize("line, argv", [
    ("#!/usr/bin/python3", ["/usr/bin/python3"]),
    ("#!/usr/bin/python3 -sP", ["/usr/bin/python3", "-sP"]),
    ("#!/usr/bin/env python3.12", ["/usr/bin/env", "python3.12"]),
    ("#!/usr/bin/env -S python3", None),
    ("#!/bin/sh -c", None),
    ("#!python3", None),
    ("#!/usr/bin/python3 -c 'import os'", None)])
def test_python_of_a_shebang(tmp_path, line, argv):
    exe = tmp_path / "gallery-dl"
    exe.write_text(line + "\nprint()\n")
    assert archives._python_of(str(exe)) == argv


def test_first_sync_seeds_with_installed_formats_and_logs_the_rest(env, fake, client, monkeypatch):
    fake_gallery_dl_package(env["tmp"], [["twitter", "media", "T{tweet_id}-{num}"]], monkeypatch)
    old = env["media"] / "gallery-dl" / "twitter" / "someone"
    old.mkdir(parents=True)
    from fake_downloaders import _gallery_dl_files
    for p in x_account((1, 1))["posts"]:
        for name, d in _gallery_dl_files(x_account(), p):
            (old / name).write_bytes(b"\xff\xd8\xff")
            (old / (name + ".json")).write_text(json.dumps(d))
    (old / "elsewhere_1.jpg").write_bytes(b"\xff\xd8\xff")
    (old / "elsewhere_1.jpg.json").write_text(json.dumps({
        "category": "twitter", "subcategory": "unknownsub", "tweet_id": 1800000000000000009, "num": 1,
        "date": "2024-06-01 12:00:00", "author": USER, "user": USER, "content": "", "filename": "e",
        "extension": "jpg"}))
    scanner.scan(env["roots"])
    s = add(client, X, account={"platform": "twitter", "id": "900"})
    fake.put(X, x_account((1, 1)))
    job = run_sync(client, s["id"])
    assert "twitterT1800000000000000001-1" in archive_entries(env, "gallery-dl")
    # The one extractor known has this format: twitter's table entry is not used for the other.
    assert "twitter1800000000000000009_0_1" not in archive_entries(env, "gallery-dl")
    log = [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert not any("could not be read" in t for t in log)


def test_first_sync_says_when_formats_are_from_the_table(env, fake, client):
    """The fake gallery-dl's Python has no gallery_dl package: the table, said in the log."""
    s = add(client, X)
    old = env["media"] / "gallery-dl" / "twitter" / "someone"
    old.mkdir(parents=True)
    (old / "a_1.jpg").write_bytes(b"\xff\xd8\xff")
    (old / "a_1.jpg.json").write_text(json.dumps({
        "category": "nosuchsite", "subcategory": "user", "id": 5, "date": "2024-06-01 12:00:00",
        "author": USER, "user": USER, "filename": "a", "extension": "jpg"}))
    scanner.scan(env["roots"])
    fake.put(X, x_account((1, 1)))
    conn = db.connect()
    assert [tuple(r) for r in conn.execute("SELECT platform, author_id FROM posts")] == [("nosuchsite", "900")]
    conn.execute("UPDATE sources SET author_id = '900', platform = 'nosuchsite' WHERE id = ?", (s["id"],))
    conn.commit()
    log = [ln["text"] for ln in jobs.log(run_sync(client, s["id"])["id"])["lines"]]
    assert any("gallery-dl's own archive formats could not be read" in t and "twitter" in t for t in log)
    assert "[feedvault] 1 nosuchsite file not seeded (no archive format known for nosuchsite, or its metadata " \
        "lacks a key the format needs), so this sync may download it again" in log


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
    for tool in ("gallery-dl", "yt-dlp"):
        assert cfg[tool] == {"session": {"mode": "none"}, "pause": 30, "ignore_config": False}
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
    # The pause, each tool its own, as instaloader's.
    r = post(client, "/api/config", {"yt-dlp": {"pause": 0}, "gallery-dl": {"pause": 3600}})
    assert r["config"]["yt-dlp"] == {"session": {"mode": "none"}, "pause": 0, "ignore_config": False}
    assert r["config"]["gallery-dl"] == {"session": {"mode": "cookies", "browser": "firefox"}, "pause": 3600,
                                         "ignore_config": False}
    for bad in [-1, 3601, "5", True, None, 1.5]:
        assert post(client, "/api/config", {"yt-dlp": {"pause": bad}})["ok"] is False, bad
    assert config.load()["yt-dlp"]["pause"] == 0
    set_config(**{"gallery-dl": {"pause": "x"}})                    # edited by hand: the default
    assert get(client, "/api/config")["gallery-dl"]["pause"] == 30


def test_ignore_my_config(env, fake, client):
    """Off by default; on, each tool gets its own flag, first, in syncs and in Test."""
    import downloaders
    x, t = add(client, X), add(client, TT)
    for tool, build in (("gallery-dl", sync._build_gallery_dl), ("yt-dlp", sync._build_yt_dlp)):
        flag = sync.IGNORE_CONFIG[tool]
        sid = str((x if tool == "gallery-dl" else t)["id"])
        assert flag not in build({"source": sid})["args"]
        assert flag not in downloaders._build_test({"tool": tool})["args"]
        r = post(client, "/api/config", {tool: {"ignore_config": True}})
        assert r["config"][tool]["ignore_config"] is True and r["config"][tool]["pause"] == 0
        assert build({"source": sid})["args"][0] == flag
        assert downloaders._build_test({"tool": tool})["args"][0] == flag
    assert sync.IGNORE_CONFIG == {"gallery-dl": "--config-ignore", "yt-dlp": "--ignore-config"}
    for bad in [1, "true", None, []]:
        assert post(client, "/api/config", {"yt-dlp": {"ignore_config": bad}})["ok"] is False, bad
    assert config.load()["yt-dlp"]["ignore_config"] is True
    set_config(**{"gallery-dl": {"ignore_config": "yes"}})               # edited by hand: off
    assert get(client, "/api/config")["gallery-dl"]["ignore_config"] is False
    # The fakes take the flags: a sync with it on runs as before.
    fake.put(X, x_account((1, 1)))
    set_config(**{"gallery-dl": {"ignore_config": True, "pause": 0}})
    job = run_sync(client, x["id"])
    assert job["state"] == "done" and job["argv"][1] == "--config-ignore"


def test_files_without_metadata_point_at_the_users_config(env, fake, client):
    """#31: a sync that wrote files no parser reads says the user's config is the likely cause."""
    fake.put(X, x_account((1, 2)))
    fake.put(TT, tt_account(1), config_no_metadata=True)
    x, t = add(client, X), add(client, TT)
    job = run_sync(client, x["id"])
    assert job["state"] == "done" and job["message"].startswith("0 new posts; 2 files it wrote could not be read: "
                                                                "your own gallery-dl config is the likely cause")
    assert '"Ignore my gallery-dl config"' in job["message"]
    job = run_sync(client, t["id"])
    assert job["state"] == "done" and "your own yt-dlp config is the likely cause" in job["message"]
    # With the config skipped the files get their metadata; nothing is blamed.
    for tool in ("gallery-dl", "yt-dlp"):
        post(client, "/api/config", {tool: {"ignore_config": True}})
    fake.put(X, x_account((1, 2), (2, 1)), config_no_metadata=True)
    job = run_sync(client, x["id"])
    assert (job["state"], job["message"]) == ("done", "1 new post")
    # Files already there before the sync are not counted again.
    assert sync._unread("gallery-dl", {"unread": 0}) == "" and sync._unread("instaloader", {"unread": 3}) == ""
    assert sync._unread("yt-dlp", {"unread": 1}) == "; 1 file it wrote could not be read (no metadata FeedVault " \
                                                    "knows beside it)"


def test_a_file_written_now_with_an_old_mtime_counts_as_written(tmp_path):
    """gallery-dl sets a file's mtime from Last-Modified: the ctime says it is new."""
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    since = time.time() - 5
    os.utime(f, (since - 10**7, since - 10**7))
    assert scanner._written_since(str(f), since)
    assert not scanner._written_since(str(f), time.time() + 60)
    assert not scanner._written_since(str(tmp_path / "gone.jpg"), since)


def test_pause_between_two_syncs_of_one_tool(env, fake, client):
    fake.put(TT, tt_account(1))
    fake.put(X, x_account((1, 1)))
    tt, x = add(client, TT), add(client, X)
    set_config(**{"yt-dlp": {"pause": 30}, "gallery-dl": {"pause": 0}})
    run_sync(client, tt["id"])
    queued = post(client, f"/api/sources/{tt['id']}/sync", {})["job"]
    assert queued["state"] == "queued" and queued["waits_until"] > time.time() + 20
    assert get(client, "/api/sources")["sources"][0]["job"]["waits_until"] == queued["waits_until"]
    # Another tool's group is not held.
    assert run_sync(client, x["id"])["state"] == "done"
    post(client, f"/api/jobs/{queued['id']}/cancel", {})
    assert ended(queued["id"])["state"] == "cancelled"
    # gallery-dl pauses too, from its own setting.
    set_config(**{"yt-dlp": {"pause": 0}, "gallery-dl": {"pause": 1}})
    jobs._cool.clear()
    first = run_sync(client, x["id"])
    second = post(client, f"/api/sources/{x['id']}/sync", {})["job"]
    assert second["waits_until"] and ended(second["id"])["started_at"] >= first["ended_at"] + 1


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
    # One whose mtime is ahead of the clock (a file system whose clock is off): known, so left alone too.
    ahead = os.path.join(folder, "old-20200101-2.info.json")
    with open(ahead, "w") as f:
        json.dump(with_cookies(json.load(open(TT_INFO, encoding="utf-8"))), f)
    os.utime(ahead, (time.time() + 3600, time.time() + 3600))
    elsewhere = env["media"] / "elsewhere" / "x.info.json"
    elsewhere.parent.mkdir()
    elsewhere.write_text(open(before).read())
    job = run_sync(client, s["id"])
    assert job["state"] == "done"
    [new] = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".info.json") and not n.startswith("old-")]
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
    assert secret_in(ahead)


def test_sync_without_cookies_rewrites_nothing(env, fake, client):
    fake.put(TT, tt_account(1))
    s = add(client, TT)
    job = run_sync(client, s["id"])
    assert not any("[feedvault] cookies" in ln["text"] for ln in jobs.log(job["id"])["lines"])


def test_cookies_from_the_users_own_yt_dlp_config_are_removed_too(env, fake, client):
    # No cookies setting in FeedVault, but yt-dlp's own config passes some.
    fake.put(TT, tt_account(1), config_cookies=True)
    s = add(client, TT)
    job = run_sync(client, s["id"])
    assert "--cookies-from-browser" not in job["argv"]
    assert "[feedvault] cookies removed from 1 info JSON" in [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert not any(secret_in(os.path.join(s["folder"], n)) for n in os.listdir(s["folder"]) if n.endswith(".json"))


def test_cookie_cleaning_failure_does_not_fail_the_sync(env, fake, client, monkeypatch):
    def refuse(path, apply=True):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(info_cookies, "clean", refuse)
    fake.put(TT, tt_account(1))
    s = add(client, TT, options={"session": {"mode": "cookies", "browser": "firefox"}})
    job = run_sync(client, s["id"])
    assert (job["state"], job["message"]) == ("done", "1 new post")
    # The log is scrubbed: this test's folder names cookies, so its path is a private one there.
    assert any(ln["text"].startswith("[feedvault] could not remove the cookies from ")
               and ln["text"].endswith(" Permission denied") for ln in jobs.log(job["id"])["lines"])


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


def test_settings_action_goes_on_past_a_bad_file_or_folder(env, client):
    d = with_cookies(json.load(open(TT_INFO, encoding="utf-8")))
    bad = env["media"] / "a.info.json"                             # a lone surrogate: not writable as UTF-8
    bad.write_text(json.dumps({**d, "title": "\ud83d"}))                 # escaped: "\\ud83d"
    good = env["media"] / "b.info.json"
    good.write_text(json.dumps(d))
    locked = env["media"] / "locked"
    locked.mkdir()
    (locked / "c.info.json").write_text(json.dumps(d))
    locked.chmod(0)
    try:
        r = post(client, "/api/yt-dlp/info-json-cookies", {"apply": True})
    finally:
        locked.chmod(0o755)
    assert (r["files"], r["failures"]) == (1, 2) and not secret_in(good) and secret_in(bad)
    assert sorted(f["path"] for f in r["failed"]) == [str(bad), str(locked)]
    assert not [n for n in os.listdir(env["media"]) if n.endswith(".tmp")]      # no temporary file left


def test_settings_action_waits_for_a_running_yt_dlp_sync(env, client, monkeypatch):
    monkeypatch.setattr(jobs, "active", lambda: [{"kind": "yt-dlp-sync", "group": "yt-dlp", "state": "running"}])
    assert post(client, "/api/yt-dlp/info-json-cookies", {}, 409)["ok"] is False


def test_formats_read_at_startup_only_with_a_gallery_dl_source(env, client, monkeypatch):
    calls = []
    monkeypatch.setattr(archives, "installed_formats", lambda run=True: calls.append(run) or (None, "x"))
    conn = db.connect()
    assert archives.warm(conn) is None
    for target in ("someone", "https://youtube.com/@someone"):
        client.post("/api/sources", json={"target": target, **({"tool": "instaloader"} if "/" not in target else {})},
                    headers=H)
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
    assert archives.warm(conn) is None and calls == []
    assert client.post("/api/sources", json={"target": "https://x.com/someone"}, headers=H).status_code == 200
    archives.warm(conn).join(5)
    assert calls == [True]


def test_formats_read_at_startup_with_gallery_dl_posts_to_trash(env, monkeypatch):
    calls = []
    monkeypatch.setattr(archives, "installed_formats", lambda run=True: calls.append(run) or (None, "x"))
    gallery_dl_case("twitter/four_photos", env["media"] / "twitter" / "someone")
    scanner.scan(env["roots"])
    archives.warm(db.connect()).join(5)
    assert calls == [True]
