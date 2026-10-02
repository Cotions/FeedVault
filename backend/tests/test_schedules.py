"""Schedules: a source's schedule option (sources.py) and the scheduler
that syncs due sources (scheduler.py), on a fake clock."""
import json

import pytest

from test_source_options import LOGIN, X, add, put_x, post, set_stored, sync_now, tools, ig_profile  # noqa: F401 (a fixture)

import sources


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
    stored = sources.stored_options(sources.row(__import__("db").connect(), s["id"]))
    assert (stored["content"], stored["schedule"]) == (["stories"], "off")
    set_stored(s["id"], {"content": ["stories"], "session": LOGIN, "schedule": "sometimes"})
    assert sources.stored_options(sources.row(__import__("db").connect(), s["id"]))["schedule"] == "off"
    set_stored(s["id"], {"schedule": "weekly"})
    assert sources.stored_options(sources.row(__import__("db").connect(), s["id"]))["schedule"] == "weekly"


def test_schedule_is_exported(env, client):
    import config
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
    got = client.get(f"/api/sources/{s['id']}", headers={"X-FeedVault": "1"}).get_json()
    assert (got["last_result"]["state"], got["last_result"]["failures"]) == ("failed", 2)
    put_x(tools)
    sync_now(client, s["id"])
    got = client.get(f"/api/sources/{s['id']}", headers={"X-FeedVault": "1"}).get_json()
    assert (got["last_result"]["state"], got["last_result"]["failures"]) == ("done", 0)


def test_only_a_schedule_changes_while_it_syncs(env, client, tools):
    tools["ig"].write_text(json.dumps({**ig_profile(), "delay": 0.5}))
    s = add(client, "carol.cooks", "instaloader")
    job = post(client, f"/api/sources/{s['id']}/sync", {})["job"]
    assert post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "daily"}})["source"][
        "options"]["schedule"] == "daily"
    post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "daily", "since": None}}, 409)
    from test_source_options import ended
    import jobs
    jobs.cancel(job["id"])
    ended(job["id"])
