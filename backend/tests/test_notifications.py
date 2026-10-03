"""Notifications (notify.py): an entry for each sync that brought new posts
or failed, with the fake tools; a scheduled failure repeated adds none."""
import json
import sys

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
    assert client.get("/api/jobs", headers=H).get_json()["notifications"] == {"unread": 1, "latest": e["id"], "desktop": False}
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


def test_a_muted_one_makes_no_entry_and_no_count(env, client, tools):
    tools["ig"].write_text(json.dumps(ig_profile()))
    s = add(client, "carol.cooks", "instaloader")
    sync_now(client, s["id"])                          # links the account
    account = client.get(f"/api/sources/{s['id']}", headers=H).get_json()["account"]
    post(client, "/api/new/mute", {"account": account, "muted": True})
    profile = ig_profile()
    profile["profiles"]["carol.cooks"]["posts"].append({"shortcode": "CPOSTNEW999", "ts": 1717243200 + 9 * 86400,
                                                         "caption": "later", "kind": "image"})
    tools["ig"].write_text(json.dumps(profile))
    before = len(entries(client)["entries"])
    job = sync_now(client, s["id"])
    assert job["result"]["added"] == 1 and job["result"]["muted"] is True and "notification" not in job["result"]
    assert len(entries(client)["entries"]) == before
    # Nor a failure; nor in "Sync all"'s count.
    tools["ig"].write_text(json.dumps({**profile, "fail": "429"}))
    job = sync_now(client, s["id"])
    assert job["state"] == "failed" and job["result"]["muted"] is True
    assert len(entries(client)["entries"]) == before
    queued = post(client, "/api/sources/sync-all", {})["jobs"]
    for j in queued:
        ended(j["id"])
    b = client.get("/api/jobs", headers=H).get_json()["sync_all"]
    assert b["done"] and b["failed"] == 0 and b["added"] == 0
    # Unmuted: entries again.
    post(client, "/api/new/mute", {"account": account, "muted": False})
    sync_now(client, s["id"])
    assert len(entries(client)["entries"]) == before + 1


def test_desktop_notifications_with_notify_send(env, client, monkeypatch, tmp_path):
    bin_dir, out = tmp_path / "nbin", tmp_path / "sent.json"
    bin_dir.mkdir()
    fake = bin_dir / "notify-send"
    fake.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(out)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))           # only the fake: never the real one
    monkeypatch.setattr(notify, "_tab_at", None)
    text = "-x @carol <b>&</b>: sessionid=abcdef0123456789 \x1b[31m" + "z" * 400
    assert notify.desktop(text) is None and not out.exists()       # off by default
    assert client.get("/api/config", headers=H).get_json()["desktop_notifications"] is False
    r = client.post("/api/config", headers=H, json={"desktop_notifications": "yes"}).get_json()
    assert r["ok"] is False
    assert client.post("/api/config", headers=H, json={"desktop_notifications": True}).get_json()["ok"]
    t = notify.desktop(text)
    t.join(10)
    argv = json.loads(out.read_text())
    assert argv[:3] == ["--app-name=FeedVault", "--", "FeedVault"] and len(argv) == 4
    body = argv[3]
    assert "<b>" not in body and "&lt;b&gt;&amp;&lt;/b&gt;" in body and "abcdef0123456789" not in body
    assert "\x1b" not in body and len(body) < notify.TEXT_MAX + 40
    # A tab that shows them itself: notify-send waits.
    client.get("/api/jobs?desktop=1", headers=H)
    assert notify.desktop("2 new posts from @x") is None
    monkeypatch.setattr(notify, "_tab_at", None)
    # No notify-send installed: nothing, quietly.
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert notify.desktop("2 new posts from @x") is None
    assert len(out.read_text().splitlines()) == 1
