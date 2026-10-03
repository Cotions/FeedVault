"""Schedules: a source's schedule option (sources.py) and the scheduler
that syncs due sources (scheduler.py), on a fake clock."""
import json
import threading

import pytest

from conftest import H
from test_source_options import (LOGIN, X, add, ended, ig_profile, post, put_x, set_stored, sync_now,  # noqa: F401
                                 tools)                                                    # (tools: a fixture)

import config
import db
import jobs
import scheduler
import sources
import sync

NOW = 1790000000
HOUR = 3600


# ---------------------------------------------------------------------------
# The option
# ---------------------------------------------------------------------------

def test_schedule_is_off_by_default_and_checked(env, client):
    s = add(client, X)
    assert s["options"]["schedule"] == "off"
    s = post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "hourly"}})["source"]
    assert s["options"]["schedule"] == "hourly"
    for bad in ("every minute", 3600, None, True, "Daily"):
        r = post(client, f"/api/sources/{s['id']}", {"options": {"schedule": bad}}, 400)
        assert r["error"] == "schedule must be one of: off, hourly, daily, weekly"
    assert add(client, "https://x.com/other", schedule="weekly")["options"]["schedule"] == "weekly"


def test_stories_turned_on_make_it_daily(env, client):
    s = add(client, "carol.cooks", "instaloader", session=LOGIN, content=["posts", "stories"])
    assert s["options"]["schedule"] == "daily"
    # Sent with it, the schedule wins.
    assert add(client, "dave.draws", "instaloader", session=LOGIN, content=["stories"],
               schedule="off")["options"]["schedule"] == "off"
    s = add(client, "erin.eats", "instaloader", session=LOGIN)
    assert s["options"]["schedule"] == "off"
    s = post(client, f"/api/sources/{s['id']}", {"options": {"content": ["stories"]}})["source"]
    assert s["options"]["schedule"] == "daily"
    # The user turned it off: stories already on leave it so.
    s = post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "off"}})["source"]
    s = post(client, f"/api/sources/{s['id']}", {"options": {"content": ["posts", "stories"]}})["source"]
    assert s["options"]["schedule"] == "off"
    # A schedule already picked stays.
    s = add(client, "fay.films", "instaloader", session=LOGIN, schedule="weekly")
    s = post(client, f"/api/sources/{s['id']}", {"options": {"content": ["stories"]}})["source"]
    assert s["options"]["schedule"] == "weekly"
    # TikTok stories too (gallery-dl).
    options, _ = sources.parse_options({"content": ["posts", "stories"]}, tool="gallery-dl", platform="tiktok",
                                       target="https://tiktok.com/@someone")
    assert options["schedule"] == "daily"


def test_stored_schedule(env, client):
    s = add(client, "carol.cooks", "instaloader", session=LOGIN)
    # From before schedules: stories stored without a schedule do not pick one.
    set_stored(s["id"], {"content": ["stories"], "session": LOGIN})
    stored = sources.stored_options(sources.row(db.connect(), s["id"]))
    assert (stored["content"], stored["schedule"]) == (["stories"], "off")
    set_stored(s["id"], {"content": ["stories"], "session": LOGIN, "schedule": "sometimes"})
    assert sources.stored_options(sources.row(db.connect(), s["id"]))["schedule"] == "off"
    set_stored(s["id"], {"schedule": "weekly"})
    assert sources.stored_options(sources.row(db.connect(), s["id"]))["schedule"] == "weekly"


def test_schedule_is_exported(env, client):
    import userdata
    add(client, X, schedule="hourly")
    userdata.flush()
    text = open(userdata.path(config.load()["data_directory"], "sources")).read()
    assert '\\"schedule\\": \\"hourly\\"' in text or '"schedule": "hourly"' in text


def test_failures_in_a_row(env, client, tools):
    tools["dl"].write_text(json.dumps({"fail": "429"}))
    s = add(client, X)
    sync_now(client, s["id"])
    sync_now(client, s["id"])
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert (got["last_result"]["state"], got["last_result"]["failures"]) == ("failed", 2)
    put_x(tools)
    sync_now(client, s["id"])
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert (got["last_result"]["state"], got["last_result"]["failures"]) == ("done", 0)


def test_only_a_schedule_changes_while_it_syncs(env, client, tools):
    tools["ig"].write_text(json.dumps({**ig_profile(), "delay": 0.5}))
    s = add(client, "carol.cooks", "instaloader")
    job = post(client, f"/api/sources/{s['id']}/sync", {})["job"]
    assert post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "daily"}})["source"][
        "options"]["schedule"] == "daily"
    post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "daily", "since": None}}, 409)
    jobs.cancel(job["id"])
    ended(job["id"])


# ---------------------------------------------------------------------------
# The scheduler, on a fake clock
# ---------------------------------------------------------------------------

@pytest.fixture
def sched(monkeypatch):
    """Fresh scheduler state; the thread is never left running."""
    monkeypatch.setattr(scheduler, "_notes", {})
    monkeypatch.setattr(scheduler, "_held", {})
    monkeypatch.setattr(scheduler, "_last", {})
    monkeypatch.setattr(scheduler, "clock", lambda: NOW)
    yield
    scheduler.stop()


@pytest.fixture
def queued(monkeypatch, sched):
    """sync.sync recording what it was asked to queue: nothing runs."""
    calls = []

    def fake(sid):
        calls.append(sid)
        return {"id": len(calls)}
    monkeypatch.setattr(sync, "sync", fake)
    monkeypatch.setattr(sync, "active", lambda: {})
    monkeypatch.setattr(jobs, "tool_path", lambda name: f"/fake/{name}")
    return calls


def synced(sid, at, state="done", failures=0, **more):
    conn = db.connect()
    with conn:
        conn.execute("UPDATE sources SET last_sync_at = ?, last_result = ? WHERE id = ?",
                     (at, json.dumps({"state": state, "failures": failures, **more}), sid))


def test_delay_and_back_off():
    assert [scheduler.delay("hourly", n) for n in range(7)] == [HOUR, 2 * HOUR, 4 * HOUR, 8 * HOUR, 16 * HOUR,
                                                              24 * HOUR, 24 * HOUR]
    assert [scheduler.delay("daily", n) for n in range(3)] == [24 * HOUR] * 3
    assert scheduler.delay("weekly", 4) == 7 * 24 * HOUR
    assert scheduler.delay("hourly", 10 ** 6) == 24 * HOUR
    assert scheduler.due_at("daily", None, None) == 0
    assert scheduler.due_at("daily", NOW, {"state": "interrupted", "failures": 3}) == 0
    assert scheduler.due_at("daily", NOW, {"state": "failed", "failures": 1}) == NOW + 24 * HOUR


def test_due_sources_are_queued_once(env, client, queued):
    off = add(client, "https://x.com/off")
    s = add(client, X, schedule="hourly")
    assert scheduler.tick() == [{"id": 1}] and queued == [s["id"]]      # never synced: now
    synced(s["id"], NOW)
    assert scheduler.tick(NOW + HOUR - 1) == []
    assert scheduler.tick(NOW + HOUR) == [{"id": 2}]
    # FeedVault was off for a week: one sync, not one per missed hour.
    synced(s["id"], NOW)
    assert len(scheduler.tick(NOW + 7 * 24 * HOUR)) == 1
    synced(s["id"], NOW + 7 * 24 * HOUR)
    assert scheduler.tick(NOW + 7 * 24 * HOUR + 60) == []
    assert off["id"] not in queued


def test_one_platform_is_spread_out(env, client, queued):
    ig = [add(client, name, "instaloader", schedule="daily")["id"] for name in ("ann", "bob", "cat")]
    x = add(client, X, schedule="daily")["id"]
    for sid, days in zip(ig, (5, 30, 10)):              # the most overdue first: bob, cat, ann
        synced(sid, NOW - days * 24 * HOUR)

    def tick(at):
        before = len(queued)
        scheduler.tick(at)
        for sid in queued[before:]:                     # they ran
            synced(sid, at)
        return queued[before:]

    assert tick(NOW) == [x, ig[1]]                      # x never synced: the most overdue
    assert tick(NOW + 60) == []
    assert tick(NOW + scheduler.SPREAD) == [ig[2]]
    assert tick(NOW + 2 * scheduler.SPREAD) == [ig[0]]


def test_a_platform_busy_waits(env, client, queued, monkeypatch):
    a = add(client, "ann", "instaloader", schedule="daily")["id"]
    b = add(client, "bob", "instaloader", schedule="daily")["id"]
    monkeypatch.setattr(sync, "active", lambda: {a: {"id": 9, "state": "running", "waits_until": None}})
    scheduler.tick(NOW)
    assert queued == []                                # nor bob: one Instagram sync at a time
    monkeypatch.setattr(sync, "active", lambda: {})
    scheduler.tick(NOW)
    assert queued == [a]
    assert b not in queued


def test_paused(env, client, queued):
    add(client, X, schedule="hourly")
    r = post(client, "/api/config", {"schedules_paused": "yes"})
    assert r == {"ok": False, "error": "schedules_paused must be true or false"}
    assert client.get("/api/config", headers=H).get_json()["schedules_paused"] is False
    assert post(client, "/api/config", {"schedules_paused": True})["config"]["schedules_paused"] is True
    assert scheduler.tick(NOW + 30 * 24 * HOUR) == [] and queued == []
    s = client.get("/api/sources", headers=H).get_json()["sources"][0]
    assert s["schedule"]["paused"] is True
    post(client, "/api/config", {"schedules_paused": False})
    assert len(scheduler.tick()) == 1


def test_missing_tool_and_offline_root_skip_it(env, client, queued, monkeypatch):
    s = add(client, X, schedule="hourly")
    monkeypatch.setattr(jobs, "tool_path", lambda name: None)
    assert scheduler.tick() == []
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]
    assert got["skipped"] == "skipped: gallery-dl was not found (Settings → Downloaders)"
    monkeypatch.setattr(jobs, "tool_path", lambda name: f"/fake/{name}")
    # A root that is gone, or empty while the index has posts under it.
    env["media"].rename(env["tmp"] / "unplugged")
    assert scheduler.tick() == []
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]
    assert got["skipped"] == f"skipped: its media root {env['media']} is offline"
    env["media"].mkdir()
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO posts(id, platform, post_id, meta_path, tool, kind, saved_at, indexed_at) "
                     "VALUES ('x:1', 'twitter', '1', ?, 'gallery-dl', 'image', 0, 0)",
                     (str(env["media"] / "a" / "1.json"),))
    assert scheduler.tick() == []
    (env["media"] / "a").mkdir()
    assert scheduler.tick() == [{"id": 1}]
    assert client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]["skipped"] is None


def test_refused_is_held_an_interval(env, client, queued, monkeypatch):
    s = add(client, X, schedule="hourly")

    def refuse(sid):
        raise jobs.BadRequest("its folder is no longer inside a media root")
    monkeypatch.setattr(sync, "sync", refuse)
    assert scheduler.tick() == []
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]
    assert got["skipped"] == "skipped: its folder is no longer inside a media root"
    assert got["next_at"] == NOW + HOUR
    monkeypatch.setattr(sync, "sync", lambda sid: {"id": 7})
    assert scheduler.tick(NOW + HOUR - 1) == []
    assert scheduler.tick(NOW + HOUR) == [{"id": 7}]


def test_status(env, client, sched):
    s = add(client, X)
    assert s["schedule"] == {"every": "off", "next_at": None, "paused": False, "skipped": None, "stopped": None,
                             "failures": 0}
    s = post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "daily"}})["source"]
    assert s["schedule"]["next_at"] == 0                # due
    synced(s["id"], NOW, "failed", 2)
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]
    assert (got["next_at"], got["failures"]) == (NOW + 24 * HOUR, 2)


def test_a_failing_source_backs_off(env, client, tools, sched, monkeypatch):
    # The real queue and the fake gallery-dl.
    tools["dl"].write_text(json.dumps({"fail": "429"}))
    s = add(client, X, schedule="hourly")
    clock = [NOW]
    monkeypatch.setattr(scheduler, "clock", lambda: clock[0])

    def run(at):
        clock[0] = at
        got = scheduler.tick()
        for job in got:
            ended(job["id"])
        return got

    assert len(run(NOW)) == 1
    end = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["last_sync_at"]
    assert run(end + HOUR) == []                       # one failure: 2 h
    assert len(run(end + 2 * HOUR)) == 1
    end = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["last_sync_at"]
    assert run(end + 3 * HOUR) == []                   # two: 4 h
    put_x(tools)
    assert len(run(end + 4 * HOUR)) == 1
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert got["last_result"]["state"] == "done"
    assert got["schedule"]["next_at"] == got["last_sync_at"] + HOUR    # back to its interval
    assert len(tools["log"].read_text().splitlines()) == 3


def test_never_twice_with_the_real_queue(env, client, tools, sched):
    tools["ig"].write_text(json.dumps({**ig_profile(), "delay": 0.5}))
    s = add(client, "carol.cooks", "instaloader", schedule="hourly")
    job = post(client, f"/api/sources/{s['id']}/sync", {})["job"]
    assert scheduler.tick() == []                      # already running
    ended(job["id"])
    assert scheduler.tick() == []                      # it just ended: an hour from now


def test_the_thread_starts_and_stops(env, monkeypatch, sched):
    ticked = threading.Event()
    monkeypatch.setattr(scheduler, "STARTUP_DELAY", 0)
    monkeypatch.setattr(scheduler, "tick", ticked.set)
    scheduler.start()
    assert ticked.wait(5)
    thread = scheduler._thread
    scheduler.stop()
    assert not thread.is_alive() and scheduler._thread is None


def test_a_schedule_changed_during_a_sync_stays(env, client, tools, sched):
    # The sync's end sets full history back; the schedule sent meanwhile is kept.
    tools["ig"].write_text(json.dumps({**ig_profile(), "delay": 0.5}))
    s = add(client, "carol.cooks", "instaloader", full_history=True)
    job = post(client, f"/api/sources/{s['id']}/sync", {})["job"]
    post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "weekly"}})
    assert ended(job["id"])["state"] == "done"
    options = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["options"]
    assert (options["full_history"], options["schedule"]) == (False, "weekly")


def test_update_some_keys(env, client):
    s = add(client, X, since="2024-01-01")
    conn = db.connect()
    sources.update(conn, s["id"], {**s["options"], "schedule": "daily", "since": None}, keys=("schedule",))
    stored = sources.stored_options(sources.row(conn, s["id"]))
    assert (stored["schedule"], stored["since"]) == ("daily", "2024-01-01")
    set_stored(s["id"], [1])                           # not an object: the whole options are written
    sources.update(conn, s["id"], {**s["options"], "schedule": "hourly"}, keys=("schedule",))
    assert sources.stored_options(sources.row(conn, s["id"]))["schedule"] == "hourly"


def test_a_note_goes_once_it_synced_or_changed(env, client, queued, monkeypatch):
    s = add(client, X, schedule="weekly")
    monkeypatch.setattr(jobs, "tool_path", lambda name: None)
    scheduler.tick()
    assert client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]["skipped"]
    synced(s["id"], NOW + 5)                            # Sync clicked, it worked
    assert client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]["skipped"] is None
    scheduler.tick(NOW + 8 * 24 * HOUR)
    assert scheduler._notes
    post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "daily"}})
    assert not scheduler._notes
    scheduler.tick(NOW + 9 * 24 * HOUR)
    client.delete(f"/api/sources/{s['id']}", headers=H)
    assert not scheduler._notes


def test_stored_times_of_any_shape(env, client, queued):
    s = add(client, X, schedule="daily")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE sources SET last_sync_at = 'yesterday', last_result = '[1]' WHERE id = ?", (s["id"],))
    got = client.get("/api/sources", headers=H).get_json()["sources"][0]["schedule"]
    assert (got["next_at"], got["failures"]) == (0, 0)
    assert len(scheduler.tick()) == 1


def test_not_found_or_login_required_stops_it(env, client, queued):
    gone = add(client, X, schedule="hourly")["id"]
    walled = add(client, "carol.cooks", "instaloader", schedule="hourly")["id"]
    limited = add(client, "https://x.com/busy", schedule="hourly")["id"]
    synced(gone, NOW, "failed", 2, health="not_found", blocking=2)
    synced(walled, NOW, "failed", 4, health="login_required", blocking=4)
    synced(limited, NOW, "failed", 1, health="rate_limited")
    assert scheduler.tick(NOW + 30 * 24 * HOUR) == [{"id": 1}] and queued == [limited]   # the back-off applies
    synced(limited, NOW + 30 * 24 * HOUR, "done")
    for sid, why in ((gone, "account not found"), (walled, "login required")):
        got = client.get(f"/api/sources/{sid}", headers=H).get_json()
        assert (got["schedule"]["stopped"], got["schedule"]["next_at"]) == (f"paused: {why}", None)
        assert (got["health"]["paused"], got["health"]["warning"]) == (why, why)
    assert client.get(f"/api/sources/{limited}", headers=H).get_json()["schedule"]["stopped"] is None
    # Changing its schedule resumes it, until a sync says so again.
    s = post(client, f"/api/sources/{gone}", {"options": {"schedule": "daily"}})["source"]
    assert (s["schedule"]["stopped"], s["schedule"]["next_at"], s["health"]["paused"]) == \
        (None, NOW + 24 * HOUR, None)
    assert s["health"]["state"] == "not_found"
    assert scheduler.tick(NOW + 30 * 24 * HOUR + scheduler.SPREAD) == [{"id": 2}] and queued[-1] == gone
    synced(gone, NOW + 30 * 24 * HOUR, "failed", 3, health="not_found", blocking=3)
    assert client.get(f"/api/sources/{gone}", headers=H).get_json()["schedule"]["stopped"] == \
        "paused: account not found"
    # Off: nothing to stop.
    s = post(client, f"/api/sources/{walled}", {"options": {"schedule": "off"}})["source"]
    assert s["schedule"]["stopped"] is None


def test_a_manual_sync_that_works_resumes_it(env, client, tools, sched):
    tools["dl"].write_text(json.dumps({"accounts": {}, "fail": "notfound"}))
    s = add(client, X, schedule="hourly")
    sync_now(client, s["id"])
    sync_now(client, s["id"])
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert (got["health"]["state"], got["schedule"]["stopped"]) == ("not_found", "paused: account not found")
    assert scheduler.tick(NOW + 30 * 24 * HOUR) == []
    sync_now(client, s["id"])                           # still not found: still stopped
    assert client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]["stopped"] == \
        "paused: account not found"
    put_x(tools)
    sync_now(client, s["id"])
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert (got["health"]["state"], got["schedule"]["stopped"], got["health"]["warning"]) == ("ok", None, None)
    assert got["schedule"]["next_at"] == got["last_sync_at"] + HOUR


def test_warning_after_three_failures(env, client, queued):
    s = add(client, X)
    for n, warning in ((2, None), (3, "3 failed syncs in a row"), (5, "5 failed syncs in a row")):
        synced(s["id"], NOW, "failed", n, health="error")
        assert client.get(f"/api/sources/{s['id']}", headers=H).get_json()["health"]["warning"] == warning


def test_what_keeps_or_lifts_the_stop(env, client, queued):
    import health
    # A run that never got that far keeps "resumed": it said nothing new.
    prev = {"state": "failed", "health": "not_found", "resumed": True, "failures": 2, "blocking": 2}
    kept = health.record(prev, None, "interrupted", NOW, "x")
    assert kept["resumed"] is True and kept["blocking"] == 2
    assert health.paused({**kept, "state": "interrupted"}) is None
    assert "resumed" not in health.record(prev, "not_found", "failed", NOW, "x")
    # A result stored before health (its error from broader patterns) does not stop it.
    assert health.paused({"state": "failed", "error": "not_found", "failures": 1}) is None
    # A new session lifts it too (a login fixed).
    s = add(client, "carol.cooks", "instaloader", schedule="daily")
    synced(s["id"], NOW, "failed", 2, health="login_required", blocking=2)
    assert client.get(f"/api/sources/{s['id']}", headers=H).get_json()["schedule"]["stopped"] == \
        "paused: login required"
    s = post(client, f"/api/sources/{s['id']}", {"options": {"session": LOGIN}})["source"]
    assert s["schedule"]["stopped"] is None and s["last_result"]["resumed"] is True


def test_a_lone_blocking_result_backs_off(env, client, tools, sched):
    # instaloader says "does not exist" to a throttled anonymous client too: once is the back-off's.
    tools["ig"].write_text(json.dumps({"profiles": {}, "fail": "notfound"}))
    s = add(client, "carol.cooks", "instaloader", schedule="hourly")
    sync_now(client, s["id"])
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert (got["health"]["state"], got["last_result"]["blocking"], got["last_result"]["failures"]) == \
        ("not_found", 1, 1)
    assert (got["schedule"]["stopped"], got["health"]["paused"]) == (None, None)
    assert got["schedule"]["next_at"] == got["last_sync_at"] + 2 * HOUR     # backed off
    assert scheduler.tick(got["last_sync_at"] + 2 * HOUR - 1) == []
    job = scheduler.tick(got["last_sync_at"] + 2 * HOUR)[0]
    ended(job["id"])                                    # a second in a row: stopped
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert (got["last_result"]["blocking"], got["schedule"]["stopped"], got["schedule"]["next_at"]) == \
        (2, "paused: account not found", None)
    assert scheduler.tick(NOW + 30 * 24 * HOUR) == []


def test_not_found_with_an_accepted_session_stops_at_once(env, client, tools, sched):
    tools["ig"].write_text(json.dumps({"profiles": {}, "fail": "notfound"}))
    s = add(client, "carol.cooks", "instaloader", schedule="hourly", session=LOGIN)
    sync_now(client, s["id"])
    got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
    assert got["health"]["login"] == {"mode": "login", "found": True, "accepted": True}
    assert (got["last_result"]["blocking"], got["schedule"]["stopped"]) == (1, "paused: account not found")
    assert scheduler.tick(NOW + 30 * 24 * HOUR) == []


def test_a_sync_that_works_in_between_starts_the_count_again(env, client, tools, sched):
    s = add(client, "carol.cooks", "instaloader", schedule="hourly")
    for fail, blocking in (("notfound", 1), (None, None), ("notfound", 1)):
        tools["ig"].write_text(json.dumps({**ig_profile(), "fail": fail}))
        sync_now(client, s["id"])
        got = client.get(f"/api/sources/{s['id']}", headers=H).get_json()
        assert (got["last_result"].get("blocking"), got["schedule"]["stopped"]) == (blocking, None)
    # Rate limited in between too: it says nothing of the account, but it is not blocking.
    import health
    assert "blocking" not in health.record({"health": "not_found", "blocking": 1}, "rate_limited", "failed", NOW, "x")
    # Stored before the count: a blocking state is one.
    assert health.blocking({"health": "not_found"}) == 1 and health.paused({"health": "not_found"}) is None
    assert health.record({"health": "login_required"}, "not_found", "failed", NOW, "x")["blocking"] == 2
    for bad in ("2", True, -1, 0, None):
        assert health.blocking({"health": "not_found", "blocking": bad}) == 1
    assert health.blocking({"health": "ok", "blocking": 5}) == 0
