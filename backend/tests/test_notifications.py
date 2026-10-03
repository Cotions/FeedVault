"""Notifications (notify.py): an entry for each sync that brought new posts
or failed, with the fake tools; a scheduled failure repeated adds none."""
import json

from conftest import H
from test_schedules import NOW, HOUR, sched                                          # noqa: F401
from test_source_options import X, add, ended, ig_profile, post, put_x, sync_now, tools  # noqa: F401

import db
import jobs
import notify
import scheduler


def entries(client):
    return client.get("/api/notifications", headers=H).get_json()


def posts_of(client, nid):
    r = client.get(f"/api/posts?notification={nid}&limit=100", headers=H).get_json()
    return r["total"], sorted(p["post_id"] for p in r["posts"])


def test_a_sync_with_new_posts_leaves_an_entry_that_opens_them(env, client, tools):
    tools["ig"].write_text(json.dumps(ig_profile()))
    s = add(client, "carol.cooks", "instaloader")
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and job["result"]["added"] > 0
    got = entries(client)
    assert got["unread"] == 1 and len(got["entries"]) == 1
    e = got["entries"][0]
    assert job["result"]["notification"] == e["id"] == got["latest"]
    assert (e["kind"], e["source_id"], e["count"], e["read"], e["scheduled"]) == \
        ("new", s["id"], job["result"]["added"], False, False)
    assert e["text"] == f"{job['result']['added']} new posts from @carol.cooks"
    total, ids = posts_of(client, e["id"])
    assert total == job["result"]["added"]
    # Exactly those: another account's posts indexed in the same second stay out.
    every = client.get("/api/posts?limit=100", headers=H).get_json()["posts"]
    assert sorted(p["post_id"] for p in every if p["author"]["handle"] == "carol.cooks") == ids
    # Marked seen since: the entry still opens them.
    post(client, "/api/new/seen", {})
    assert posts_of(client, e["id"])[0] == total
    # A sync that brought nothing adds no entry.
    sync_now(client, s["id"])
    assert len(entries(client)["entries"]) == 1
    assert posts_of(client, 999)[0] == 0 and posts_of(client, "x")[0] == 0
    # The poll carries the unread count; reading them clears it.
    assert client.get("/api/jobs", headers=H).get_json()["notifications"] == {"unread": 1, "latest": e["id"]}
    assert post(client, "/api/notifications/read", {"upto": e["id"]})["read"] == 1
    assert entries(client)["unread"] == 0


def test_read_bodies(env, client):
    for bad in ({"upto": "1"}, {"upto": True}, {"upto": -1}, {"other": 1}, [1]):
        r = client.post("/api/notifications/read", json=bad, headers=H)
        assert r.status_code == 400, bad
    assert post(client, "/api/notifications/read", {})["read"] == 0


def test_failures_and_the_scheduler_repeating_one(env, client, tools, sched, monkeypatch):
    tools["dl"].write_text(json.dumps({"accounts": {}, "fail": "429"}))
    s = add(client, X, schedule="hourly")
    job = sync_now(client, s["id"])
    e = entries(client)["entries"]
    assert len(e) == 1 and e[0]["kind"] == "failed" and e[0]["state"] == "rate_limited"
    assert e[0]["text"] == "x.com/someone: rate limited" and job["result"]["notification"] == e[0]["id"]
    clock = [NOW]
    monkeypatch.setattr(scheduler, "clock", lambda: clock[0])

    def run(at):
        clock[0] = at
        for j in scheduler.tick():
            ended(j["id"])

    # Scheduled retries failing the same way: still the one entry.
    end = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["last_sync_at"]
    run(end + 2 * HOUR)
    end = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["last_sync_at"]
    run(end + 4 * HOUR)
    assert len(client.get(f"/api/sources/{s['id']}", headers=H).get_json()["last_result"]) and \
        len(entries(client)["entries"]) == 1
    # Another state is a new entry, scheduled or not.
    tools["dl"].write_text(json.dumps({"accounts": {}, "fail": "notfound"}))
    end = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["last_sync_at"]
    run(end + 8 * HOUR)
    e = entries(client)["entries"]
    assert len(e) == 2 and e[0]["state"] == "not_found" and e[0]["scheduled"] is True
    assert e[0]["text"] == "x.com/someone: account not found"
    # A manual sync failing the same way says so again: the user asked.
    sync_now(client, s["id"])
    assert len(entries(client)["entries"]) == 3
    # Then it works with new posts: an entry; failing after that is new again.
    put_x(tools)
    sync_now(client, s["id"])
    assert entries(client)["entries"][0]["kind"] == "new"


def test_a_tool_that_cannot_start_leaves_an_entry(env, client, tools, monkeypatch):
    s = add(client, "carol.cooks", "instaloader")
    # No tool at all: never the real one on PATH.
    monkeypatch.setattr(jobs, "tool_path", lambda name: None)
    job = sync_now(client, s["id"])
    assert job["state"] == "failed"
    e = entries(client)["entries"][0]
    assert (e["kind"], e["state"]) == ("failed", "error") and e["text"].startswith("@carol.cooks: ")
    assert job["result"]["notification"] == e["id"]


def test_text_is_scrubbed_and_the_list_is_capped(env, monkeypatch):
    conn = db.connect()
    nid = notify.add(conn, "failed", "x: \x1b[31mfailed\x1b[0m sessionid=abcdef0123456789 <b>hi</b>\n" + "y" * 500)
    text = conn.execute("SELECT text FROM notifications WHERE id = ?", (nid,)).fetchone()[0]
    assert "\x1b" not in text and "abcdef0123456789" not in text and "\n" not in text
    assert len(text) <= notify.TEXT_MAX and "<b>hi</b>" in text           # text, shown as text
    monkeypatch.setattr(notify, "KEPT", 5)
    for i in range(9):
        last = notify.add(conn, "new", f"{i} new posts from @x", count=i)
    rows = [r[0] for r in conn.execute("SELECT id FROM notifications ORDER BY id")]
    assert rows == list(range(last - 4, last + 1))
    assert notify.read(conn, last - 1) == 4 and notify.unread(conn) == (1, last)
