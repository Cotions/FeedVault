"""Profiles: separate libraries (a data directory and media roots each), one
active at a time. The config's shape and its migration on read, the
/api/profiles routes, the page's profile header (a tab left open across a
switch), and the switch itself: refused while a job or a scan runs, and
after it the API serves the other library."""
import json
import os
import sqlite3
import sys
import threading
import time

import pytest

from conftest import H
from fakes import owner, write_post

import app as app_module
import config
import db
import hashing
import jobs
import library
import scanner
import scheduler
import sync
import userdata

ALICE = owner("alice.example", 111, "Alice Example")
BOB = owner("bob_example", 222, "Bob")


def on_disk():
    with open(config.config_path()) as f:
        return json.load(f)


def flat(env, **more):
    """config.json as a FeedVault from before profiles wrote it."""
    cfg = {"data_directory": str(env["tmp"] / "data"), "media_roots": env["roots"],
           "tools": {}, "schedules_paused": True, **more}
    with open(config.config_path(), "w") as f:
        json.dump(cfg, f)
    return cfg


@pytest.fixture
def scans(monkeypatch):
    """A switch's scan, run before the request is answered (and no manifest
    read in the background after the test)."""
    def start(roots):
        if not scanner.claim():
            return False
        scanner._run(list(roots))
        return True
    monkeypatch.setattr(scanner, "start", start)
    monkeypatch.setattr(app_module.trash, "warm", lambda roots: None)


def create(client, env, name="Work", data="data-work", roots=("media-work",), **headers):
    for r in roots:
        (env["tmp"] / r).mkdir(exist_ok=True)
    return client.post("/api/profiles", headers={**H, **headers}, json={
        "name": name, "data_directory": str(env["tmp"] / data), "media_roots": [str(env["tmp"] / r) for r in roots]})


def activate(client, pid, **headers):
    return client.post(f"/api/profiles/{pid}/activate", headers={**H, **headers})


def listed(client):
    return client.get("/api/profiles", headers=H).get_json()


# --- the config file ---------------------------------------------------------

def test_a_flat_config_is_read_as_one_default_profile(env):
    before = flat(env, similar_threshold=4)
    text = open(config.config_path()).read()
    cfg = config.load()
    assert cfg["data_directory"] == before["data_directory"] and cfg["media_roots"] == before["media_roots"]
    assert (cfg["active_profile"], cfg["profile_name"]) == ("default", "Default")
    assert cfg["schedules_paused"] is True and cfg["similar_threshold"] == 4
    assert "profiles" not in cfg
    raw = config.read()
    assert raw["profiles"] == [{"id": "default", "name": "Default", "data_directory": before["data_directory"],
                                "media_roots": before["media_roots"]}]
    # Reading moves nothing and rewrites nothing.
    assert open(config.config_path()).read() == text


def test_a_flat_config_stays_flat_until_something_is_saved(env, client):
    flat(env)
    text = open(config.config_path()).read()
    assert client.get("/api/config", headers=H).get_json()["profile"] == {"id": "default", "name": "Default"}
    assert [p["id"] for p in listed(client)["profiles"]] == ["default"]
    assert client.post("/api/config", headers=H, json={}).get_json()["ok"] is True       # nothing to save
    assert open(config.config_path()).read() == text
    assert client.post("/api/config", headers=H, json={"check_updates": True}).get_json()["ok"] is True
    saved = on_disk()
    assert "data_directory" not in saved and "media_roots" not in saved
    assert saved["active_profile"] == "default" and saved["check_updates"] is True and saved["schedules_paused"] is True
    assert saved["profiles"] == [{"id": "default", "name": "Default", "data_directory": str(env["tmp"] / "data"),
                                  "media_roots": env["roots"]}]
    # The same library as before.
    assert config.load()["data_directory"] == str(env["tmp"] / "data")


def test_no_config_at_all_is_the_default_profile_on_the_default_data_directory(env):
    os.remove(config.config_path())
    cfg = config.load()
    assert cfg["data_directory"] == config.DEFAULT_DATA and cfg["media_roots"] == []
    assert cfg["active_profile"] == "default"
    assert not os.path.exists(config.config_path())


def test_save_routes_the_library_to_the_active_profile_and_the_rest_to_the_top(env, client):
    pid = create(client, env).get_json()["profile"]["id"]
    cfg = config.load()
    other = env["tmp"] / "more"
    other.mkdir()
    cfg["media_roots"] = [*cfg["media_roots"], str(other)]
    cfg["check_updates"] = True
    config.save(cfg)
    saved = on_disk()
    assert saved["check_updates"] is True
    assert "active_profile" in saved and "profile_name" not in saved and "media_roots" not in saved
    by_id = {p["id"]: p for p in saved["profiles"]}
    assert by_id["default"]["media_roots"] == [str(env["media"]), str(other)]
    assert by_id[pid]["media_roots"] == [str(env["tmp"] / "media-work")]


def test_an_active_profile_that_is_gone_falls_back_to_the_first(env):
    raw = config.read()
    raw["active_profile"] = "nope"
    config.write(raw)
    assert config.load()["active_profile"] == "default"


# --- create, rename, remove --------------------------------------------------

def test_create_lists_the_profile_and_leaves_the_active_one(env, client):
    r = create(client, env, name="  Work   stuff ")
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True
    p = body["profile"]
    assert p["name"] == "Work stuff" and p["active"] is False and (p["posts"], p["people"]) == (0, 0)
    assert p["data_directory"] == str(env["tmp"] / "data-work") and os.path.isdir(p["data_directory"])
    assert p["media_roots"] == [str(env["tmp"] / "media-work")]
    out = listed(client)
    assert out["active"] == "default" and [x["id"] for x in out["profiles"]] == ["default", p["id"]]
    assert config.load()["data_directory"] == str(env["tmp"] / "data")
    # No database is made until it is switched to.
    assert not os.path.exists(os.path.join(p["data_directory"], "feedvault.db"))


def test_the_list_counts_each_library(env, client, scans):
    write_post(env["media"] / "alice.example", "P1", 1717243200, ALICE, "image")
    scanner.scan(env["roots"])
    pid = create(client, env).get_json()["profile"]["id"]
    assert [(p["name"], p["posts"]) for p in listed(client)["profiles"]] == [("Default", 1), ("Work", 0)]
    assert activate(client, pid).status_code == 200
    # The one left is read from its file, read-only.
    assert [(p["name"], p["posts"], p["active"]) for p in listed(client)["profiles"]] == \
        [("Default", 1, False), ("Work", 0, True)]


@pytest.mark.parametrize("body, error", [
    ({"data_directory": "/tmp/x"}, "name must be"),
    ({"name": "", "data_directory": "/tmp/x"}, "name must be"),
    ({"name": "x" * 61, "data_directory": "/tmp/x"}, "name must be"),
    ({"name": "a\nb\x00", "data_directory": "/tmp/x"}, "name must be"),
    ({"name": 3, "data_directory": "/tmp/x"}, "name must be"),
    ({"name": "Work"}, "data_directory must be"),
    ({"name": "Work", "data_directory": "  "}, "data_directory must be"),
    ({"name": "Work", "data_directory": ["x"]}, "data_directory must be"),
    ({"name": "Work", "data_directory": "/tmp/x", "media_roots": "nope"}, "media_roots must be"),
    ({"name": "Work", "data_directory": "/tmp/x", "media_roots": ["/no/such/folder"]}, "not a folder"),
])
def test_create_refuses_bad_input(env, client, body, error):
    r = client.post("/api/profiles", headers=H, json=body)
    assert r.status_code == 400 and error in r.get_json()["error"]
    assert len(listed(client)["profiles"]) == 1


def test_create_refuses_a_name_or_a_data_directory_already_taken(env, client):
    assert create(client, env).status_code == 200
    r = create(client, env, name="work", data="other", roots=())
    assert r.status_code == 400 and "name exists" in r.get_json()["error"]
    r = create(client, env, name="Default", data="other", roots=())
    assert r.status_code == 400
    # The same folder, however it is written: as it is, through "..", through a symlink.
    os.symlink(env["tmp"] / "data-work", env["tmp"] / "link")
    for data in ("data-work", "x/../data-work", "link", "data"):
        (env["tmp"] / "x").mkdir(exist_ok=True)
        r = create(client, env, name="Other", data=data, roots=())
        assert r.status_code == 400 and "already uses that data directory" in r.get_json()["error"], data
    # A file is no data directory.
    (env["tmp"] / "file").write_text("")
    assert "not a folder" in create(client, env, name="Other", data="file", roots=()).get_json()["error"]
    assert len(listed(client)["profiles"]) == 2


def test_media_roots_of_two_profiles_never_overlap(env, client):
    (env["media"] / "sub").mkdir()
    inside, around = str(env["media"] / "sub"), str(env["tmp"])
    for roots, word in (([str(env["media"])], "is a media root of"), ([inside], "overlaps"), ([around], "overlaps")):
        r = client.post("/api/profiles", headers=H, json={
            "name": "Work", "data_directory": str(env["tmp"] / "data-work"), "media_roots": roots})
        assert r.status_code == 400 and word in r.get_json()["error"] and '"Default"' in r.get_json()["error"]
    assert not os.path.exists(env["tmp"] / "data-work")       # refused before anything is made
    # Nor later, in Settings → Library, either way round.
    assert create(client, env).status_code == 200
    work = str(env["tmp"] / "media-work")
    (env["tmp"] / "media-work" / "deep").mkdir()
    for roots in ([str(env["media"]), work], [str(env["media"]), os.path.join(work, "deep")], [around]):
        r = client.post("/api/config", headers=H, json={"media_roots": roots}).get_json()
        assert r["ok"] is False and '"Work"' in r["error"]
    assert config.load()["media_roots"] == env["roots"]
    # Its own roots are no overlap.
    extra = env["tmp"] / "extra"
    extra.mkdir()
    assert client.post("/api/config", headers=H, json={"media_roots": [*env["roots"], str(extra)]}).get_json()["ok"]


def test_rename(env, client):
    pid = create(client, env).get_json()["profile"]["id"]
    r = client.post(f"/api/profiles/{pid}", headers=H, json={"name": "Art"})
    assert r.status_code == 200 and [p["name"] for p in r.get_json()["profiles"]] == ["Default", "Art"]
    assert client.post("/api/profiles/default", headers=H, json={"name": "Home"}).status_code == 200
    assert config.load()["profile_name"] == "Home"
    assert client.post(f"/api/profiles/{pid}", headers=H, json={"name": "home"}).status_code == 400     # taken
    assert client.post(f"/api/profiles/{pid}", headers=H, json={"name": "art"}).status_code == 200      # its own
    assert client.post(f"/api/profiles/{pid}", headers=H, json={"name": " "}).status_code == 400
    assert client.post("/api/profiles/nope", headers=H, json={"name": "x"}).status_code == 404
    # A rename moves nothing.
    assert {p["id"]: p["data_directory"] for p in listed(client)["profiles"]} == {
        "default": str(env["tmp"] / "data"), pid: str(env["tmp"] / "data-work")}


def test_remove_only_forgets_the_profile(env, client, scans):
    r = client.delete("/api/profiles/default", headers=H)
    assert r.status_code == 400 and "last profile" in r.get_json()["error"]
    pid = create(client, env).get_json()["profile"]["id"]
    r = client.delete("/api/profiles/default", headers=H)
    assert r.status_code == 400 and "switch to another profile" in r.get_json()["error"]
    assert client.delete("/api/profiles/nope", headers=H).status_code == 404
    # Give the other library something to lose.
    write_post(env["tmp"] / "media-work" / "bob_example", "W1", 1717243300, BOB, "image")
    assert activate(client, pid).status_code == 200
    assert activate(client, "default").status_code == 200
    data, media = env["tmp"] / "data-work", env["tmp"] / "media-work"
    before = sorted(os.listdir(data)), sorted(os.listdir(media / "bob_example"))
    r = client.delete(f"/api/profiles/{pid}", headers=H)
    assert r.status_code == 200 and [p["id"] for p in r.get_json()["profiles"]] == ["default"]
    assert (sorted(os.listdir(data)), sorted(os.listdir(media / "bob_example"))) == before
    assert os.path.isfile(data / "feedvault.db")
    # Made again on that data directory, it is all back.
    again = create(client, env, name="Work again").get_json()["profile"]
    assert again["posts"] == 1


# --- the switch ----------------------------------------------------------------

def test_after_a_switch_the_api_serves_the_other_library(env, client, scans):
    write_post(env["media"] / "alice.example", "P1", 1717243200, ALICE, "image", caption="home")
    scanner.scan(env["roots"])
    assert client.post("/api/tags/apply", headers=H,
                       json={"posts": [db.connect().execute("SELECT id FROM posts").fetchone()[0]], "add": ["keep"]}
                       ).get_json()["ok"]
    pid = create(client, env).get_json()["profile"]["id"]
    write_post(env["tmp"] / "media-work" / "bob_example", "W1", 1717243300, BOB, "image", caption="work")
    write_post(env["tmp"] / "media-work" / "bob_example", "W2", 1717243400, BOB, "image")

    r = activate(client, pid)
    assert r.status_code == 200 and r.get_json()["active"] == pid
    assert config.load()["active_profile"] == pid and on_disk()["active_profile"] == pid
    assert db._path == str(env["tmp"] / "data-work" / "feedvault.db")
    cfg = client.get("/api/config", headers=H).get_json()
    assert cfg["profile"] == {"id": pid, "name": "Work"}
    assert cfg["media_roots"] == [str(env["tmp"] / "media-work")] and cfg["data_directory"] == str(env["tmp"] / "data-work")
    assert cfg["schedules_paused"] is False            # shared
    # Its own posts (the switch scanned its roots), none of the other's, and none of its tags.
    posts = client.get("/api/posts", headers=H).get_json()
    assert sorted(p["post_id"] for p in posts["posts"]) == ["W1", "W2"]
    assert client.get("/api/posts/instagram/P1", headers=H).status_code == 404
    assert client.get("/api/tags", headers=H).get_json() == []
    assert client.get("/api/scan", headers=H).get_json()["last"]["added"] == 2
    # The tag was written to the library it was made in before leaving it, not after.
    assert "tags" not in userdata._timers
    tags = json.load(open(env["tmp"] / "data" / "userdata" / "tags.json"))
    assert [t["name"] for t in tags["rows"]] == ["keep"]

    # And back: the first library as it was left.
    assert activate(client, "default").status_code == 200
    posts = client.get("/api/posts", headers=H).get_json()
    assert [p["post_id"] for p in posts["posts"]] == ["P1"]
    assert [t["name"] for t in client.get("/api/tags", headers=H).get_json()] == ["keep"]
    # Each library's index stayed its own.
    for data, ids in (("data", ["P1"]), ("data-work", ["W1", "W2"])):
        conn = sqlite3.connect(env["tmp"] / data / "feedvault.db")
        assert [r[0] for r in conn.execute("SELECT post_id FROM posts ORDER BY post_id")] == ids
        conn.close()


def test_switching_to_the_active_profile_or_an_unknown_one(env, client, scans):
    before = db._path
    r = activate(client, "default")
    assert r.status_code == 200 and r.get_json()["active"] == "default" and db._path == before
    assert activate(client, "nope").status_code == 404
    assert config.load()["active_profile"] == "default"


def test_a_switch_is_refused_while_a_job_is_queued_or_running(env, client, scans, monkeypatch):
    pid = create(client, env).get_json()["profile"]["id"]
    monkeypatch.setattr(jobs, "active", lambda: [{"id": 7, "kind": "instaloader-sync", "state": "running"}])
    r = activate(client, pid)
    body = r.get_json()
    assert r.status_code == 409 and body["busy"] is True and "profile_switched" not in body
    assert "1 job is queued or running" in body["error"]
    assert config.load()["active_profile"] == "default" and db._path == str(env["tmp"] / "data" / "feedvault.db")
    monkeypatch.setattr(jobs, "active", lambda: [{"id": 7}, {"id": 8}])
    assert "2 jobs are queued or running" in activate(client, pid).get_json()["error"]
    monkeypatch.setattr(jobs, "active", lambda: [])
    assert activate(client, pid).status_code == 200


def test_a_real_queued_job_refuses_the_switch(env, client, scans):
    """Not a stub: a job of a kind registered here, held by a file it waits for."""
    pid = create(client, env).get_json()["profile"]["id"]
    gate = env["tmp"] / "go"
    code = f"import os, time\nwhile not os.path.exists({str(gate)!r}): time.sleep(0.05)\n"
    jobs.register("profiles-wait", label="wait", params={}, group="profiles-wait",
                  build=lambda params: {"tool": sys.executable, "args": ["-c", code]})
    try:
        job = jobs.submit("profiles-wait", {})
        r = activate(client, pid)
        assert r.status_code == 409 and r.get_json()["busy"] is True
        assert config.load()["active_profile"] == "default"
        gate.write_text("")
        deadline = time.time() + 20
        while jobs.active() and time.time() < deadline:
            time.sleep(0.05)
        assert jobs.get(job["id"])["state"] == "done"
        assert activate(client, pid).status_code == 200
        # The job stayed in the library it ran in.
        assert jobs.get(job["id"]) is None
    finally:
        gate.write_text("")
        jobs._kinds.pop("profiles-wait", None)


def test_a_switch_is_refused_while_a_scan_runs(env, client, scans):
    pid = create(client, env).get_json()["profile"]["id"]
    assert scanner.claim()                             # as a scan under way holds it
    try:
        r = activate(client, pid)
        assert r.status_code == 409 and r.get_json()["busy"] is True and "scan is running" in r.get_json()["error"]
        assert config.load()["active_profile"] == "default"
    finally:
        scanner.release()
    assert activate(client, pid).status_code == 200
    # And no scan can start while a switch holds its place.
    assert scanner.claim() and scanner.start([]) is False
    scanner.release()


def test_a_switch_stops_the_hashing_pass_and_holds_the_next(env, client, scans, monkeypatch):
    """The pass under way is told to stop (as a restart stops it), and the
    switch waits for it; none runs while the library changes."""
    pid = create(client, env).get_json()["profile"]["id"]
    seen = []
    started, switched = threading.Event(), threading.Event()

    def run_pass(conn, restart=None, data_dir=None):
        seen.append(db._path)
        started.set()
        if len(seen) == 1:
            assert restart.wait(10), "the pass was never told to stop"
            assert not switched.is_set()               # the switch waits for this pass to end
            time.sleep(0.1)
        return True

    monkeypatch.setattr(hashing, "run_pass", run_pass)
    monkeypatch.setattr(hashing, "_lower_priority", lambda: None)
    wake = threading.Event()
    monkeypatch.setattr(hashing, "_wake", wake)
    stop = threading.Event()

    def loop():                                        # hashing._loop, until the test ends
        while not stop.is_set():
            if wake.wait(0.05):
                hashing._woken()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        wake.set()
        assert started.wait(10)
        r = activate(client, pid)
        switched.set()
        assert r.status_code == 200
        deadline = time.time() + 10
        while len(seen) < 2 and time.time() < deadline:
            time.sleep(0.02)
    finally:
        stop.set()
        t.join(10)
    # One pass in each library, the second only once the new one is open.
    assert seen == [str(env["tmp"] / "data" / "feedvault.db"), str(env["tmp"] / "data-work" / "feedvault.db")]
    assert hashing._pausing == 0


def test_a_profile_that_cannot_be_opened_leaves_the_old_one_active(env, client, scans):
    write_post(env["media"] / "alice.example", "P1", 1717243200, ALICE, "image")
    scanner.scan(env["roots"])
    pid = create(client, env).get_json()["profile"]["id"]
    # A database from a newer FeedVault.
    conn = sqlite3.connect(env["tmp"] / "data-work" / "feedvault.db")
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    conn.close()
    r = activate(client, pid)
    assert r.status_code == 500 and "cannot open that profile" in r.get_json()["error"]
    assert config.load()["active_profile"] == "default" and on_disk()["active_profile"] == "default"
    assert db._path == str(env["tmp"] / "data" / "feedvault.db")
    assert [p["post_id"] for p in client.get("/api/posts", headers=H).get_json()["posts"]] == ["P1"]
    assert scanner.status()["running"] is False        # given back


def test_a_switch_forgets_what_was_the_other_librarys_in_memory(env, client, scans):
    pid = create(client, env).get_json()["profile"]["id"]
    scheduler._notes[4] = ("skipped: offline", 1)
    scheduler._held[4] = 99
    sync._batch = {"id": 1, "started_at": 1, "jobs": [1], "ended": {}}
    scanner._state["last"] = {"added": 5}
    try:
        assert activate(client, pid).status_code == 200
        assert not scheduler._notes and not scheduler._held and sync.batch() is None
        assert scanner.status()["last"] != {"added": 5}
    finally:
        scheduler.forget_all()
        sync.forget_batch()


# --- a tab left open across a switch --------------------------------------------

PAGE = app_module.PROFILE_HEADER


def test_a_write_from_a_page_of_another_profile_is_refused(env, client, scans):
    pid = create(client, env, **{PAGE: "default"}).get_json()["profile"]["id"]
    assert activate(client, pid, **{PAGE: "default"}).status_code == 200
    stale, fresh = {**H, PAGE: "default"}, {**H, PAGE: pid}
    for method, path, body in (("post", "/api/config", {"check_updates": True}),
                               ("post", "/api/tags/apply", {"posts": [1], "add": ["x"]}),
                               ("post", "/api/scan", None),
                               ("post", "/api/profiles", {"name": "X", "data_directory": str(env["tmp"] / "x")}),
                               ("delete", "/api/profiles/default", None),
                               ("delete", "/api/people/1", None)):
        r = getattr(client, method)(path, headers=stale, json=body)
        assert r.status_code == 409, path
        assert r.get_json() == {"ok": False, "error": app_module.PROFILE_SWITCHED, "profile_switched": True}
    assert "check_updates" not in on_disk() and len(listed(client)["profiles"]) == 2
    # Reading is not refused, and shows the active library.
    assert client.get("/api/config", headers=stale).get_json()["profile"]["id"] == pid
    # The page of the active profile writes; so does a caller that names none (the userscript).
    assert client.post("/api/config", headers=fresh, json={"check_updates": True}).get_json()["ok"] is True
    assert client.post("/api/config", headers=H, json={"check_updates": False}).get_json()["ok"] is True
    # Switching back is what the old page may still do; then it is no longer stale.
    assert activate(client, "default", **{PAGE: "default"}).status_code == 200
    assert client.post("/api/config", headers=stale, json={"check_updates": True}).get_json()["ok"] is True
    assert client.post("/api/config", headers=fresh, json={"check_updates": True}).status_code == 409


def test_quit_is_not_refused_from_an_old_page(env, client, scans, monkeypatch):
    pid = create(client, env).get_json()["profile"]["id"]
    assert activate(client, pid).status_code == 200
    class Never:                                       # the timer that would stop this process
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass
    monkeypatch.setattr(app_module.threading, "Timer", Never)
    assert client.post("/api/quit", headers={**H, PAGE: "default"}).status_code == 200


def test_the_profile_routes_are_the_dashboards_alone(env, client):
    """Another site (the userscript's page) reaches none of them."""
    foreign = {**H, "Origin": "https://www.instagram.com"}
    for method, path in (("get", "/api/profiles"), ("post", "/api/profiles"), ("post", "/api/profiles/default"),
                         ("delete", "/api/profiles/default"), ("post", "/api/profiles/default/activate")):
        r = getattr(client, method)(path, headers=foreign)
        assert r.status_code == 403 and r.get_json()["error"] == app_module.FOREIGN_REFUSED
    assert client.get("/api/profiles").status_code == 403         # nor without the header


# --- the gate --------------------------------------------------------------------

def test_a_switch_waits_for_what_is_inside_and_holds_what_arrives():
    order = []
    library.enter()                                    # a request being answered
    inside = threading.Event()

    def switch():
        library.enter()                                # the switch's own request
        try:
            with library.alone(timeout=5):
                order.append("switch")
                inside.set()
                time.sleep(0.1)
                order.append("switched")
        finally:
            library.leave()

    def late():
        inside.wait(5)
        with library.shared():
            order.append("late")

    threads = [threading.Thread(target=switch), threading.Thread(target=late)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    assert order == []                                 # waits for the request under way
    order.append("answered")
    library.leave()
    for t in threads:
        t.join(5)
    assert order == ["answered", "switch", "switched", "late"]
    assert library._inside == 0 and not library._switching


def test_a_switch_gives_up_on_a_request_that_does_not_end():
    library.enter()
    library.enter()
    try:
        with pytest.raises(library.Busy, match="still answering"):
            with library.alone(timeout=0.05):
                pass
        assert not library._switching                  # and lets the others in again
        with library.shared():
            pass
    finally:
        library.leave()
        library.leave()


def test_every_request_leaves_the_gate(env, client):
    for r in (client.get("/api/config", headers=H), client.get("/api/config"), client.get("/api/nope", headers=H),
              client.post("/api/config", headers={**H, PAGE: "other"}, json={}),
              client.post("/api/people/merge", headers=H, data="[1]", content_type="application/json")):
        assert r.status_code in (200, 400, 403, 404, 409)
    assert library._inside == 0
