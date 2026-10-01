import collections
import configparser
import json
import os
import sys
import threading
import time

import pytest

from conftest import H
from fakes import owner, write_filename_post, write_post

import config
import db
import jobs
import scanner
import sources
import sync

TS = 1717243200                                     # 2024-06-01 12:00 UTC
DAY = 86400
TESTS = os.path.dirname(os.path.abspath(__file__))


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


def set_config(**kw):
    cfg = config.load()
    cfg.update(kw)
    config.save(cfg)


class Fake:
    """The fake instaloader on PATH, its profiles and the runs it saw."""

    def __init__(self, tmp):
        self.data, self.log = tmp / "fake.json", tmp / "fake.log"
        self.set({})

    def set(self, profiles, fail=None, delay=0):
        self.data.write_text(json.dumps({"profiles": profiles, "fail": fail, "delay": delay}))

    def runs(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


@pytest.fixture
def fake(env, monkeypatch):
    monkeypatch.setattr(jobs, "_active", collections.OrderedDict())
    monkeypatch.setattr(jobs, "_closing", False)
    monkeypatch.setattr(jobs, "_cool", {})
    monkeypatch.setattr(jobs, "KILL_AFTER", 0.5)
    bin_dir = env["tmp"] / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "instaloader"
    exe.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {TESTS!r})\n"
                   "import fake_instaloader\nsys.exit(fake_instaloader.main(sys.argv[1:]))\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    f = Fake(env["tmp"])
    monkeypatch.setenv("FAKE_INSTALOADER", str(f.data))
    monkeypatch.setenv("FAKE_INSTALOADER_LOG", str(f.log))
    monkeypatch.delenv("FAKE_INSTALOADER_SESSION", raising=False)
    set_config(instaloader={"pause": 0})
    yield f
    jobs.shutdown()
    for t in threading.enumerate():
        if t.name.startswith("job-") and not t.name.endswith("-log"):
            t.join(10)


def get(client, url, status=200):
    r = client.get(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def post(client, url, body=None, status=200):
    r = client.post(url, json=body or {}, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def carol_archive(env):
    """carol.cooks as the real archive has it: filename-only posts (one a
    carousel, one a video), the newest from TS + 2 days."""
    folder = env["media"] / "carol.cooks"
    write_filename_post(folder, "carol.cooks", "CCCCCCCCCC0", TS)
    write_filename_post(folder, "carol.cooks", "CCCCCCCCCC1", TS + DAY, slides=3)
    write_filename_post(folder, "carol.cooks", "CCCCCCCCCC2", TS + 2 * DAY, video=True)
    scanner.scan(env["roots"])
    return folder


def carol_profile(new=2):
    """Instagram's side: the three posts already archived and ``new`` newer ones."""
    posts = [{"shortcode": f"CCCCCCCCCC{i}", "ts": TS + i * DAY, "caption": f"old {i}"} for i in range(3)]
    posts += [{"shortcode": f"CNEWPOST00{i}", "ts": TS + (3 + i) * DAY, "caption": f"New post {i} #fresh",
               "kind": "carousel" if i else "image", "slides": 2} for i in range(new)]
    return {"carol.cooks": {"id": 777, "name": "Carol Cooks", "posts": posts}}


def add_source(client, target="carol.cooks", **body):
    return post(client, "/api/sources", {"tool": "instaloader", "target": target, **body})["source"]


def sync_now(client, sid):
    return ended(post(client, f"/api/sources/{sid}/sync")["job"]["id"])


def stamps(env):
    c = configparser.ConfigParser(interpolation=None)
    c.read(sync.stamps_path())
    return c


# ---------------------------------------------------------------------------
# The argument list comes from the source only
# ---------------------------------------------------------------------------

def test_argv_is_built_from_the_source(env, client, fake):
    carol_archive(env)
    s = add_source(client)
    job = post(client, f"/api/sources/{s['id']}/sync")["job"]
    folder = str(env["media"] / "carol.cooks")
    assert job["argv"] == [
        "instaloader", "--latest-stamps", os.path.join(config.load()["data_directory"], "instaloader", "stamps.ini"),
        "--fast-update", "--no-compress-json", "--dirname-pattern", folder,
        "--filename-pattern", "{target}-{date_utc:%Y-%m-%d}-{shortcode}",
        "--title-pattern", "{date_utc}_UTC_{typename}", "--", "carol.cooks"]
    assert (job["params"], job["group"], job["rescan"], job["label"]) == \
        ({"source": str(s["id"])}, "instaloader", folder, "Sync @carol.cooks")
    ended(job["id"])


def test_session_flags(env, client, fake):
    carol_archive(env)
    s = add_source(client)
    set_config(instaloader={"pause": 0, "session": {"mode": "cookies", "browser": "firefox"}})
    argv = sync_now(client, s["id"])["argv"]
    assert argv[-4:] == ["--load-cookies", "firefox", "--", "carol.cooks"]
    post(client, f"/api/sources/{s['id']}", {"options": {"session": {"mode": "login", "user": "my.account"}}})
    job = sync_now(client, s["id"])
    assert job["argv"][-4:] == ["--login", "my.account", "--", "carol.cooks"]
    # No saved session: instaloader would ask for a password; it fails instead.
    assert (job["state"], job["result"]["error"]) == ("failed", "login_required")
    post(client, f"/api/sources/{s['id']}", {"options": {"session": {"mode": "none"}}})
    assert "--load-cookies" not in sync_now(client, s["id"])["argv"]


def test_request_cannot_inject_flags_or_paths(env, client, fake):
    carol_archive(env)
    s = add_source(client)
    sid = str(s["id"])
    for params in [{"source": sid, "target": "x"}, {"source": sid, "args": ["--login", "me"]},
                   {"source": f"{sid} --login me"}, {"source": "--login"}, {"source": "../../etc"},
                   {"source": "$(id)"}, {"source": "99999"}, {"source": "1" * 16}, {"source": ""}, {"source": 1}, {}]:
        r = post(client, "/api/jobs", {"kind": "instaloader-sync", "params": params}, 400)
        assert r["ok"] is False, params
    for body in [{"argv": ["sh"]}, {"target": "other"}, {"folder": "/tmp"}]:
        job = post(client, f"/api/sources/{s['id']}/sync", body)["job"]       # the body is ignored
        assert job["argv"][-1] == "carol.cooks" and job["rescan"] == str(env["media"] / "carol.cooks")
        ended(job["id"])
    post(client, "/api/sources/99999/sync", status=404)
    assert client.post("/api/sources/abc/sync", headers=H).status_code in (404, 405)


def test_tampered_source_is_refused(env, client, fake, tmp_path):
    carol_archive(env)
    s = add_source(client)
    conn = db.connect()
    with conn:
        conn.execute("UPDATE sources SET target = '--login=me' WHERE id = ?", (s["id"],))
    assert "profile name" in post(client, f"/api/sources/{s['id']}/sync", status=400)["error"]
    with conn:
        conn.execute("UPDATE sources SET target = 'carol.cooks', folder = ? WHERE id = ?", (str(tmp_path), s["id"]))
    assert "media root" in post(client, f"/api/sources/{s['id']}/sync", status=400)["error"]
    with conn:
        conn.execute("UPDATE sources SET folder = ?, options = ? WHERE id = ?",
                     (str(env["media"] / "carol.cooks"),
                      json.dumps({"session": {"mode": "login", "user": "x --password y"}}), s["id"]))
    job = post(client, f"/api/sources/{s['id']}/sync")["job"]
    assert "--login" not in job["argv"] and "--password" not in job["argv"]      # malformed: no session
    ended(job["id"])
    assert fake.runs() and all("--" in r["argv"] for r in fake.runs())


def test_folder_with_braces_is_escaped(env, client, fake):
    folder = env["media"] / "we{ir}d"
    s = add_source(client, "carol.cooks", folder=str(folder))
    fake.set(carol_profile(new=1))
    job = sync_now(client, s["id"])
    assert job["argv"][job["argv"].index("--dirname-pattern") + 1] == str(env["media"] / "we{{ir}}d")
    assert job["state"] == "done" and any(n.endswith(".json") and "CNEWPOST000" in n for n in os.listdir(folder))


# ---------------------------------------------------------------------------
# Incremental
# ---------------------------------------------------------------------------

def test_first_sync_starts_after_the_newest_indexed_post(env, client, fake):
    folder = carol_archive(env)
    fake.set(carol_profile(new=2))
    s = add_source(client)
    job = sync_now(client, s["id"])
    assert (job["state"], job["message"], job["result"]["added"]) == ("done", "2 new posts", 2)
    log = [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert any("starting after its newest indexed post, 2024-06-03 12:00 UTC" in t for t in log)
    # Seeded with the newest posted_at FeedVault had, then moved on by instaloader.
    runs = fake.runs()
    assert len(runs) == 1
    st = stamps(env)
    assert st.get("carol.cooks", "profile-id") == "777"
    # The new posts carry metadata: captions, and the numeric id the folder name is now an alias of.
    posts = get(client, "/api/posts?author=carol.cooks&limit=50")["posts"]
    assert len(posts) == 5
    new = [p for p in posts if p["post_id"].startswith("CNEWPOST")]
    assert sorted(p["text"] for p in new) == ["New post 0 #fresh", "New post 1 #fresh"]
    assert {p["author"]["id"] for p in new} == {"777"}
    [a] = [a for a in get(client, "/api/authors") if a["id"] == "777"]
    assert a["aliases"] == ["carol.cooks"] and a["count"] == 5
    # Beside the old files, named the same way.
    names = sorted(n for n in os.listdir(folder) if n.startswith("carol.cooks-2024-06-04"))
    assert names == ["carol.cooks-2024-06-04-CNEWPOST000.jpg", "carol.cooks-2024-06-04-CNEWPOST000.json",
                     "carol.cooks-2024-06-04-CNEWPOST000.txt"]
    s = get(client, f"/api/sources/{s['id']}")
    assert s["account"] == {"platform": "instagram", "id": "777"}               # the alias's id now
    assert s["last_result"] == {"state": "done", "error": None, "message": "2 new posts", "line": None,
                                "added": 2, "job": job["id"]}
    assert s["last_sync_at"] == job["ended_at"] and s["last_job_id"] == job["id"] and s["job"] is None
    # Nothing new: nothing downloaded.
    job = sync_now(client, s["id"])
    assert (job["state"], job["message"]) == ("done", "0 new posts")
    assert len(fake.runs()) == 2


def test_seed_only_once_and_not_with_full_history(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile(new=0))
    s = add_source(client, options={"full_history": True})
    job = sync_now(client, s["id"])
    assert any("full history" in ln["text"] for ln in jobs.log(job["id"])["lines"])
    # The fake walked the whole profile: every file existed (fast-update stopped at the first).
    assert job["message"] == "0 new posts"
    st = stamps(env)
    assert st.get("carol.cooks", "post-timestamp").startswith("2024-06-03")    # written by instaloader
    # A stamp already there is never replaced by a seed.
    st.set("carol.cooks", "post-timestamp", "2020-01-01T00:00:00.000000+0000")
    with open(sync.stamps_path(), "w") as f:
        st.write(f)
    post(client, f"/api/sources/{s['id']}", {"options": {"full_history": False}})
    sync_now(client, s["id"])
    assert stamps(env).get("carol.cooks", "post-timestamp").startswith("2024-06-03")


def test_seed_leaves_other_profiles_alone(env, client, fake):
    carol_archive(env)
    os.makedirs(os.path.dirname(sync.stamps_path()), exist_ok=True)
    with open(sync.stamps_path(), "w") as f:
        f.write("[someone.else]\nprofile-id = 42\npost-timestamp = 2023-01-01T00:00:00.000000+0000\n")
    s = add_source(client)
    sync._start({"source": str(s["id"])}, lambda text: None)
    st = stamps(env)
    assert st.get("someone.else", "post-timestamp") == "2023-01-01T00:00:00.000000+0000"
    assert st.get("carol.cooks", "post-timestamp") == "2024-06-03T12:00:00.000000+0000"
    assert not st.has_option("carol.cooks", "profile-id")                      # the folder name is no id


def test_new_profile_downloads_everything_and_joins_its_person(env, client, fake):
    fake.set({"newbie": {"id": 999, "name": "New Bie", "posts": [
        {"shortcode": f"NNNNNNNNNN{i}", "ts": TS + i * DAY, "caption": f"n{i}"} for i in range(3)]}})
    p = post(client, "/api/people", {"name": "New Bie"})["person"]
    s = add_source(client, "https://www.instagram.com/newbie/", person=p["id"])
    assert s["account"] is None and s["person"]["id"] == p["id"]
    job = sync_now(client, s["id"])
    assert job["message"] == "3 new posts"
    assert any("no post indexed yet" in ln["text"] for ln in jobs.log(job["id"])["lines"])
    s = get(client, f"/api/sources/{s['id']}")
    assert s["account"] == {"platform": "instagram", "id": "999"} and s["person"]["id"] == p["id"]
    assert get(client, f"/api/posts?person={p['id']}")["total"] == 3


def test_trashed_posts_are_not_downloaded_again(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile(new=2))
    s = add_source(client)
    sync_now(client, s["id"])
    newest = "instagram:CNEWPOST001"
    r = post(client, "/api/delete", {"posts": [newest]})
    assert r["posts"] == [newest]
    job = sync_now(client, s["id"])
    assert job["message"] == "0 new posts"
    assert get(client, "/api/posts/instagram/CNEWPOST001", 404)


# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------

def test_detect_pattern(env, tmp_path):
    d = tmp_path / "dated"
    write_filename_post(d, "carol.cooks", "B_QcFdCp9iM", TS, slides=3)
    write_filename_post(d, "carol.cooks", "Bm3Lr49F_10", TS, video=True)
    write_filename_post(d, "old_name", "CCCCCCCCCC2", TS)                     # before a rename
    write_filename_post(d / "Trips", "Trips", "DDDDDDDDDD1", TS)              # highlights: not looked at
    (d / "2024-06-01_12-00-00_UTC_profile_pic.jpg").write_bytes(b"x")
    (d / "notes.txt").write_text("x")
    assert sync.detect_pattern(str(d)) == (sync.DATED, True)
    s = tmp_path / "spaced"
    s.mkdir()
    for n in ["carol.cooks - B_QcFdCp9iM.jpg", "carol.cooks - B_QcFdCp9iM - 2.jpg", "carol.cooks - CCCCCCCCCC2.mp4"]:
        (s / n).write_bytes(b"x")
    assert sync.detect_pattern(str(s)) == (sync.SPACED, True)
    m = tmp_path / "metadata"
    write_post(m, "A1", TS, owner("alice", 1), "carousel")
    write_post(m, "A2", TS + 60, owner("alice", 1), "video")
    assert sync.detect_pattern(str(m)) == (sync.STAMPED, True)
    # Mostly dated, a few strays: the dated pattern, not clean.
    write_post(d, "A1", TS, owner("alice", 1), "carousel")
    write_post(d, "A2", TS + 60, owner("alice", 1), "image")
    assert sync.detect_pattern(str(d)) == (sync.DATED, False)
    # Highlight-only files (a title, not a profile name) and odd names: the default.
    h = tmp_path / "odd"
    h.mkdir()
    (h / "More food-2024-06-01-DDDDDDDDDD1.jpg").write_bytes(b"x")
    (h / "IMG_0001.jpg").write_bytes(b"x")
    assert sync.detect_pattern(str(h)) == (sync.DATED, False)
    e = tmp_path / "empty"
    e.mkdir()
    assert sync.detect_pattern(str(e)) == (sync.DATED, True)
    assert sync.detect_pattern(str(tmp_path / "missing")) == (sync.DATED, True)


def test_pattern_follows_the_folder(env, client, fake):
    m = env["media"] / "alice.example"
    write_post(m, "A1", TS, owner("alice.example", 111), "image")
    scanner.scan(env["roots"])
    s = add_source(client, "alice.example")
    fake.set({"alice.example": {"id": 111, "posts": [{"shortcode": "A1", "ts": TS},
                                                     {"shortcode": "A2NEWPOST01", "ts": TS + DAY, "caption": "hi"}]}})
    job = sync_now(client, s["id"])
    assert job["argv"][job["argv"].index("--filename-pattern") + 1] == "{date_utc}_UTC"
    assert job["message"] == "1 new post"
    assert "2024-06-02_12-00-00_UTC.json" in os.listdir(m)


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------

def test_classify():
    def lines(*texts):
        return list(enumerate(texts, 1))
    assert sync.classify(lines("JSON Query to graphql/query: 429 Too Many Requests [retrying; skip with ^C]",
                               "x: Login required.")) == \
        ("rate_limited", "JSON Query to graphql/query: 429 Too Many Requests [retrying; skip with ^C]")
    assert sync.classify(lines("x: Please wait a few minutes before you try again."))[0] == "rate_limited"
    assert sync.classify(lines("Profile x does not exist.", "The most similar profile is: x_."))[0] == "not_found"
    assert sync.classify(lines("x: Private but not followed."))[0] == "private"
    assert sync.classify(lines("x: Login required.", "[feedvault] indexing /m/x")) == ("login_required", "x: Login required.")
    assert sync.classify(lines("Redirected to login page. Use --login or --load-cookies."))[0] == "login_required"
    assert sync.classify(lines("Session file does not exist yet - Logging in."))[0] == "login_required"
    # As a real anonymous run answered: blocked, then "does not exist".
    assert sync.classify(lines(
        "JSON Query to graphql/query: 403 Forbidden when accessing https://www.instagram.com/graphql/query "
        "[retrying; skip with ^C]", "nasa: Profile nasa does not exist.", "", "Errors or warnings occurred:",
        "nasa: Profile nasa does not exist."))[0] == "login_required"
    assert sync.classify(lines("something odd", "Traceback: boom", "[feedvault] indexing /m/x")) == \
        ("generic", "Traceback: boom")
    assert sync.classify([]) == ("generic", None)


@pytest.mark.parametrize("fail,error", [("429", "rate_limited"), ("login", "login_required"),
                                        ("private", "private"), ("notfound", "not_found"), ("crash", "generic")])
def test_failure_is_stored_on_the_source(env, client, fake, fail, error):
    carol_archive(env)
    fake.set(carol_profile(), fail=fail)
    s = add_source(client)
    job = sync_now(client, s["id"])
    assert (job["state"], job["result"]["error"], job["message"]) == ("failed", error, sync.MESSAGES[error]
                                                                      if error != "generic" else job["message"])
    if error == "generic":
        assert job["message"].startswith("instaloader failed: ")
    s = get(client, f"/api/sources/{s['id']}")
    assert (s["last_result"]["state"], s["last_result"]["error"]) == ("failed", error)
    assert s["last_result"]["line"] and s["last_sync_at"] == job["ended_at"]


def test_unknown_profile_is_not_found(env, client, fake):
    s = add_source(client, "nobody.here")
    job = sync_now(client, s["id"])
    assert job["result"]["error"] == "not_found"
    assert job["result"]["line"] == "Profile nobody.here does not exist."


def test_what_came_before_a_failure_is_indexed(env, client, fake, monkeypatch):
    carol_archive(env)
    profile = carol_profile(new=2)
    fake.set(profile)
    s = add_source(client)
    # instaloader exits 1 after a non-fatal error late in the run.
    real = sync._outcome
    monkeypatch.setattr(sync, "_outcome", lambda p, code, lines, index: real(p, 1, lines + [(999, "x: boom")], index))
    jobs._kinds[sync.KIND].outcome = sync._outcome
    try:
        job = sync_now(client, s["id"])
    finally:
        jobs._kinds[sync.KIND].outcome = real
    assert (job["state"], job["result"]["added"]) == ("failed", 2)
    assert job["message"] == "instaloader failed: x: boom (2 new posts before it stopped)"
    assert get(client, "/api/posts/instagram/CNEWPOST001")["text"] == "New post 1 #fresh"


def test_missing_instaloader(env, client, fake, monkeypatch):
    monkeypatch.setenv("PATH", str(env["tmp"]))
    s = add_source(client, "carol.cooks")
    job = sync_now(client, s["id"])
    assert job["state"] == "failed" and "not found" in job["message"]
    assert get(client, f"/api/sources/{s['id']}")["last_result"]["state"] == "failed"


# ---------------------------------------------------------------------------
# Queue: busy, cancel, sync all with a pause
# ---------------------------------------------------------------------------

def test_busy_cancel_and_delete(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile(new=3), delay=0.5)
    s = add_source(client)
    job = post(client, f"/api/sources/{s['id']}/sync")["job"]
    wait_for(lambda: fake.runs())
    assert get(client, f"/api/sources/{s['id']}")["job"]["id"] == job["id"]
    assert "already" in post(client, f"/api/sources/{s['id']}/sync", status=409)["error"]
    r = client.delete(f"/api/sources/{s['id']}", headers=H)
    assert r.status_code == 409
    post(client, f"/api/jobs/{job['id']}/cancel")
    assert ended(job["id"])["state"] == "cancelled"
    s = get(client, f"/api/sources/{s['id']}")
    assert s["job"] is None and s["last_result"]["state"] == "cancelled" and s["last_result"]["error"] is None
    assert client.delete(f"/api/sources/{s['id']}", headers=H).status_code == 200


def test_sync_all_in_order_with_a_pause(env, client, fake):
    profiles = {}
    for name in ("cc.three", "aa.one", "bb.two"):
        profiles[name] = {"id": len(profiles) + 1, "posts": [{"shortcode": f"{name[:2].upper()}POST00001", "ts": TS}]}
        add_source(client, name)
    fake.set(profiles)
    set_config(instaloader={"pause": 1})
    r = post(client, "/api/sources/sync-all")
    assert (len(r["jobs"]), r["skipped"], r["errors"]) == (3, 0, [])
    assert [j["label"] for j in r["jobs"]] == ["Sync @aa.one", "Sync @bb.two", "Sync @cc.three"]
    # The second waits the pause out, and says until when.
    second = r["jobs"][1]["id"]
    wait_for(lambda: jobs.get(r["jobs"][0]["id"])["state"] == "done")
    waiting = wait_for(lambda: jobs.get(second)["waits_until"])
    assert jobs.get(second)["state"] == "queued" and waiting >= time.time()
    assert get(client, "/api/sources")["sources"][1]["job"]["waits_until"] == waiting
    for j in r["jobs"]:
        assert ended(j["id"], timeout=15)["state"] == "done"
    runs = fake.runs()
    assert [run["argv"][-1] for run in runs] == ["aa.one", "bb.two", "cc.three"]
    assert all(b["at"] - a["at"] >= 1 for a, b in zip(runs, runs[1:]))
    # Again while they run: the busy ones are skipped.
    fake.set(profiles, delay=0.3)
    set_config(instaloader={"pause": 0})
    first = post(client, "/api/sources/sync-all")["jobs"]
    again = post(client, "/api/sources/sync-all")
    assert (again["jobs"], again["skipped"]) == ([], 3)
    for j in first:
        ended(j["id"], timeout=15)


def test_pause_does_not_hold_other_kinds(env, client, fake):
    s = add_source(client, "aa.one")
    fake.set({"aa.one": {"id": 1, "posts": []}})
    set_config(instaloader={"pause": 30})
    sync_now(client, s["id"])
    job = post(client, "/api/jobs", {"kind": "tool-version", "params": {"tool": "instaloader"}})["job"]
    assert ended(job["id"])["message"] == "4.15.1"
    queued = post(client, f"/api/sources/{s['id']}/sync")["job"]
    assert queued["state"] == "queued" and queued["waits_until"] > time.time() + 20
    post(client, f"/api/jobs/{queued['id']}/cancel")
    assert ended(queued["id"])["state"] == "cancelled"
    # Cancelled before it ran: not a sync, the source keeps its last result.
    assert get(client, f"/api/sources/{s['id']}")["last_result"]["state"] == "done"


def test_interrupted_sync_is_recorded_on_restart(env, client, fake):
    s = add_source(client, "aa.one")
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO jobs(kind, params, argv, cwd, lock_group, state, created_at, started_at) "
                     "VALUES ('instaloader-sync', ?, '[\"instaloader\", \"--\", \"aa.one\"]', '/', 'instaloader', "
                     "'running', 1, 2)", (json.dumps({"source": str(s["id"])}),))
    jobs.recover()
    s = get(client, f"/api/sources/{s['id']}")
    assert s["last_result"]["state"] == "interrupted" and s["last_sync_at"] is not None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_api(env, client):
    cfg = get(client, "/api/config")
    assert cfg["instaloader"] == {"session": {"mode": "none"}, "pause": 60}
    r = post(client, "/api/config", {"instaloader": {"session": {"mode": "cookies", "browser": "brave"}}})
    assert r["config"]["instaloader"] == {"session": {"mode": "cookies", "browser": "brave"}, "pause": 60}
    r = post(client, "/api/config", {"instaloader": {"pause": 5}})
    assert r["config"]["instaloader"] == {"session": {"mode": "cookies", "browser": "brave"}, "pause": 5}
    for bad in [{"session": {"mode": "login", "user": "a b"}}, {"session": {"mode": "cookies", "browser": "x"}},
                {"pause": -1}, {"pause": 3601}, {"pause": "5"}, {"pause": True},
                {"cookies": "/home/me/cookies.txt"}, {"session": {"mode": "login", "user": "me", "password": "pw"}},
                [], "none"]:
        r = post(client, "/api/config", {"instaloader": bad})
        assert r["ok"] is False, bad
    assert get(client, "/api/config")["instaloader"]["pause"] == 5
    assert "password" not in json.dumps(config.load())


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------

def test_full_history_is_once_and_fills_older_gaps(env, client, fake):
    folder = carol_archive(env)
    fake.set(carol_profile(new=1))
    s = add_source(client)
    sync_now(client, s["id"])                                  # seeded: the newest only
    # An old post was never downloaded; ask for the whole profile once.
    profile = carol_profile(new=1)
    profile["carol.cooks"]["posts"].append({"shortcode": "COLDPOST001", "ts": TS - 30 * DAY, "caption": "old"})
    fake.set(profile)
    post(client, f"/api/sources/{s['id']}", {"options": {"full_history": True}})
    job = sync_now(client, s["id"])
    assert "--fast-update" not in job["argv"] and job["message"] == "1 new post"
    assert any("COLDPOST001" in n for n in os.listdir(folder))
    assert stamps(env).get("carol.cooks", "post-timestamp").startswith("2024-06-04")
    # Done once: the option is off again and the next sync is incremental.
    assert get(client, f"/api/sources/{s['id']}")["options"]["full_history"] is False
    job = sync_now(client, s["id"])
    assert "--fast-update" in job["argv"] and job["message"] == "0 new posts"


def test_full_history_stays_on_after_a_failure(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile(), fail="429")
    s = add_source(client, options={"full_history": True})
    assert sync_now(client, s["id"])["state"] == "failed"
    assert get(client, f"/api/sources/{s['id']}")["options"]["full_history"] is True


def test_progress_counters_are_not_a_rate_limit():
    lines = list(enumerate(["[429/1200] carol.cooks-2024-06-01-CCCCCCCCCC0.jpg json",
                            "[ 12/429] something", "x: Login required."], 1))
    assert sync.classify(lines)[0] == "login_required"


def test_folder_that_cannot_be_made_is_a_400(env, client, fake):
    locked = env["media"] / "locked"
    locked.mkdir()
    s = add_source(client, "carol.cooks", folder=str(locked / "carol.cooks"))
    other = add_source(client, "aa.one")
    locked.chmod(0o555)
    try:
        assert "cannot create" in post(client, f"/api/sources/{s['id']}/sync", status=400)["error"]
        r = post(client, "/api/sources/sync-all")
        assert [j["label"] for j in r["jobs"]] == ["Sync @aa.one"]
        assert [e["source"] for e in r["errors"]] == [s["id"]]
        ended(r["jobs"][0]["id"])
    finally:
        locked.chmod(0o755)
    assert other


def test_ended_is_listed_only_once_its_effects_are_in(env, client, fake, monkeypatch):
    s = add_source(client, "aa.one")
    fake.set({"aa.one": {"id": 1, "posts": [{"shortcode": "AAPOST00001", "ts": TS}]}})
    gate, inside = threading.Event(), threading.Event()
    real = jobs._kinds[sync.KIND].ended

    def slow(public):
        inside.set()
        gate.wait(10)
        real(public)
    monkeypatch.setattr(jobs._kinds[sync.KIND], "ended", slow)
    job = post(client, f"/api/sources/{s['id']}/sync")["job"]
    inside.wait(10)
    listed = next(j for j in jobs.listing()["jobs"] if j["id"] == job["id"])
    assert listed["state"] == "running" and get(client, f"/api/sources/{s['id']}")["last_result"] is None
    gate.set()
    assert ended(job["id"])["state"] == "done"
    assert get(client, f"/api/sources/{s['id']}")["last_result"]["state"] == "done"


def test_quitting_mid_sync_records_it_once_and_does_not_index(env, client, fake, monkeypatch):
    carol_archive(env)
    fake.set(carol_profile(new=3), delay=1)
    s = add_source(client)
    calls = []
    real = sources.record
    monkeypatch.setattr(sources, "record", lambda *a: calls.append(a[-1]["state"]) or real(*a))
    job = post(client, f"/api/sources/{s['id']}/sync")["job"]
    # The newest comes first: two more to go when it is there.
    wait_for(lambda: any(n.startswith("carol.cooks-2024-06-06") for n in os.listdir(env["media"] / "carol.cooks")))
    jobs.shutdown()
    for t in threading.enumerate():
        if t.name == f"job-{job['id']}":
            t.join(10)
    assert calls == ["interrupted"]
    assert jobs.get(job["id"])["state"] == "interrupted"
    assert get(client, "/api/posts?author=carol.cooks&limit=50")["total"] == 3      # not indexed on the way out


def test_renaming_a_person_exports_sources(env, client, monkeypatch):
    import userdata
    changed = []
    monkeypatch.setattr(userdata, "changed", changed.append)
    p = post(client, "/api/people", {"name": "Carol"})["person"]
    add_source(client, "carol.cooks", person=p["id"])
    changed.clear()
    post(client, f"/api/people/{p['id']}", {"name": "Carol C"})
    assert "sources" in changed
    changed.clear()
    assert client.delete(f"/api/people/{p['id']}", headers=H).status_code == 200
    assert "sources" in changed


def test_symlinked_root_is_rescanned_under_its_own_name(env, client, fake, tmp_path):
    real = tmp_path / "real-media"
    real.mkdir()
    link = tmp_path / "linked-media"
    link.symlink_to(real)
    set_config(media_roots=[str(link)], instaloader={"pause": 0})
    write_filename_post(link / "carol.cooks", "carol.cooks", "CCCCCCCCCC0", TS)
    scanner.run([str(link)])
    fake.set(carol_profile(new=1))
    s = add_source(client)
    job = sync_now(client, s["id"])
    assert job["rescan"] == str(link / "carol.cooks") and job["message"] == "3 new posts"
    scanner.run([str(link)])                                   # the same files, the same posts
    assert get(client, "/api/posts?author=carol.cooks&limit=50")["total"] == 4
