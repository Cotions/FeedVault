"""Download defaults per tool and platform (config ``source_defaults``): what
a source inherits for each option it does not set itself, the API that
edits them, resets a source's own choices and applies a default to every
source (sources.py, app.py)."""
import json

import pytest

from conftest import H
from test_source_options import X, after, build, post, set_stored

import app as app_module
import config
import db
import sources


def get(client, url):
    r = client.get(url, headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def add(client, target, tool=None, options=None):
    body = {"target": target}
    if options is not None:
        body["options"] = options
    if tool:
        body["tool"] = tool
    return post(client, "/api/sources", body)["source"]


def defaults(client, tool, platform, block, status=200):
    return post(client, "/api/config", {"source_defaults": {tool: {platform: block}}}, status)


def stored(sid):
    return json.loads(db.connect().execute("SELECT options FROM sources WHERE id = ?", (sid,)).fetchone()[0])


def include(args):
    got = [v for k, v in zip(args, args[1:]) if k == "-o" and v.startswith("include=")]
    return got[0] if got else None


def test_a_platform_default_reaches_every_source_that_does_not_set_it(env, client):
    """The case the defaults are for: set Twitter once, the backlog follows."""
    plain = add(client, X)
    own = add(client, "https://x.com/other", options={"content": ["tweets"], "since": "2024-01-01"})
    other_tool = add(client, "https://youtube.com/@somechannel")
    assert plain["overrides"] == [] and own["overrides"] == ["content", "since"]
    assert include(build(plain)) is None                          # BUILTIN: the timeline, gallery-dl's own
    r = defaults(client, "gallery-dl", "twitter", {"content": ["media", "with_replies"], "since": "2025-06-01",
                                                   "schedule": "daily"})
    assert r["ok"] and config.load()["source_defaults"] == {
        "gallery-dl": {"twitter": {"content": ["media", "with_replies"], "since": "2025-06-01", "schedule": "daily"}}}
    # The next sync command of the source that sets nothing uses them...
    args = build(plain)
    assert include(args) == "include=media,with-replies" and after(args, "--date-after") == "2025-05-31T23:59:59"
    s = get(client, f"/api/sources/{plain['id']}")
    assert s["overrides"] == [] and s["options"]["schedule"] == "daily" and s["schedule"]["every"] == "daily"
    assert stored(plain["id"]) == {"v": 2}
    # ... the one that sets its own keeps them, and follows the rest.
    args = build(own)
    assert include(args) == "include=tweets" and after(args, "--date-after") == "2023-12-31T23:59:59"
    assert get(client, f"/api/sources/{own['id']}")["options"]["schedule"] == "daily"
    # Another platform's source is not touched.
    assert get(client, f"/api/sources/{other_tool['id']}")["options"]["schedule"] == "off"
    # Changed again, followed again; removed, back to BUILTIN.
    defaults(client, "gallery-dl", "twitter", {"content": ["media"]})
    assert include(build(plain)) == "include=media" and "--date-after" not in build(plain)
    defaults(client, "gallery-dl", "twitter", None)
    assert "source_defaults" not in config.load() or config.load()["source_defaults"] == {}
    assert include(build(plain)) is None and include(build(own)) == "include=tweets"


def test_a_new_source_stores_only_what_differs(env, client):
    defaults(client, "gallery-dl", "twitter", {"media": "images", "schedule": "weekly"})
    # The form starts from the defaults (resolve says them) and sends all of it back: nothing is pinned.
    r = get(client, "/api/sources/resolve?url=https://x.com/someone")
    assert r["defaults"]["media"] == "images" and r["defaults"]["schedule"] == "weekly"
    assert r["metadata"] == {"mode": "full", "inherited": "full", "setting": "full"}
    s = add(client, X, options={**r["defaults"]})
    assert s["overrides"] == [] and stored(s["id"]) == {"v": 2}
    s = add(client, "https://x.com/other", options={**r["defaults"], "media": "all"})
    assert s["overrides"] == ["media"] and stored(s["id"]) == {"v": 2, "media": "all"}
    # A source a person adds (POST /api/people) sets nothing.
    p = post(client, "/api/people", {"name": "Someone", "profiles": ["https://x.com/third"]})
    [made] = [x for x in get(client, "/api/sources")["sources"] if x["target"].endswith("/third")]
    assert made["overrides"] == [] and stored(made["id"]) == {"v": 2} and made["options"]["media"] == "images"
    assert p["ok"]


def test_a_pinned_option_stays_pinned_when_the_default_catches_up(env, client):
    """Pinned to images, the default becomes images, a save sends the whole
    form: the pin stays, so a later default (videos) does not move it."""
    defaults(client, "gallery-dl", "twitter", {"media": "all"})
    s = add(client, X, options={"media": "images"})
    url = f"/api/sources/{s['id']}"
    assert s["overrides"] == ["media"]
    defaults(client, "gallery-dl", "twitter", {"media": "images"})
    shown = get(client, url)["options"]
    assert shown["media"] == "images"
    r = post(client, url, {"options": {**shown, "since": "2024-01-01"}})
    assert r["source"]["overrides"] == ["media", "since"] and stored(s["id"])["media"] == "images"
    defaults(client, "gallery-dl", "twitter", {"media": "videos"})
    assert get(client, url)["options"]["media"] == "images"


def test_a_stale_form_does_not_pin_the_old_default(env, client):
    """The form opened when the default was "all", the default changed,
    then the form saved another field: media keeps following the default."""
    s = add(client, X)
    url = f"/api/sources/{s['id']}"
    assert get(client, url)["options"]["media"] == "all"
    defaults(client, "gallery-dl", "twitter", {"media": "images"})
    # The form sends only the fields changed since it opened (frontend changesOf).
    r = post(client, url, {"options": {"since": "2024-01-01"}})
    assert r["source"]["overrides"] == ["since"]
    defaults(client, "gallery-dl", "twitter", {"media": "videos"})
    assert get(client, url)["options"]["media"] == "videos"


def test_a_change_is_set_even_to_the_default_and_reset_inherits(env, client):
    defaults(client, "gallery-dl", "twitter", {"media": "videos"})
    s = add(client, X, options={"media": "images"})
    url = f"/api/sources/{s['id']}"
    # Changed to what the default is now: set, so it stays when the default moves.
    r = post(client, url, {"options": {"media": "videos"}})
    assert r["source"]["overrides"] == ["media"]
    defaults(client, "gallery-dl", "twitter", {"media": "images"})
    assert get(client, url)["options"]["media"] == "videos"
    # Sent as it is now: nothing changes, it stays set.
    assert post(client, url, {"options": {"media": "videos"}})["source"]["overrides"] == ["media"]
    # Only Reset makes it inherited again.
    r = post(client, url, {"reset": ["media"], "options": {"media": "videos"}})
    assert r["source"]["overrides"] == ["media"]       # reset, then set again in the same request
    r = post(client, url, {"reset": ["media"]})
    assert r["source"]["overrides"] == [] and r["source"]["options"]["media"] == "images"


def test_pin_sets_what_is_sent_even_equal_to_a_default_that_moved(env, client):
    """The form opened with media "all" (the default then), the default
    became "images", the user picked "images": with pin it is theirs."""
    s = add(client, X)
    url = f"/api/sources/{s['id']}"
    defaults(client, "gallery-dl", "twitter", {"media": "images"})
    r = post(client, url, {"options": {"media": "images"}, "pin": True})
    assert r["source"]["overrides"] == ["media"]
    defaults(client, "gallery-dl", "twitter", {"media": "videos"})
    assert get(client, url)["options"]["media"] == "images"
    # Without pin, the same is no change.
    other = add(client, "https://x.com/other")
    assert post(client, f"/api/sources/{other['id']}", {"options": {"media": "videos"}})["source"]["overrides"] == []
    assert client.post(url, json={"options": {}, "pin": "yes"}, headers=H).status_code == 400


def test_a_save_writes_only_the_options_that_changed(env, client):
    """What was written meanwhile (another tab, a sync's end) and not sent stays."""
    s = add(client, X, options={"since": "2024-01-01"})
    url = f"/api/sources/{s['id']}"
    set_stored(s["id"], {"v": 2, "since": "2024-01-01", "schedule": "weekly"})
    post(client, url, {"options": {"media": "images"}})
    assert stored(s["id"]) == {"v": 2, "since": "2024-01-01", "schedule": "weekly", "media": "images"}


def test_reset_one_option_or_all(env, client):
    defaults(client, "gallery-dl", "twitter", {"media": "images"})
    s = add(client, X, options={"media": "videos", "since": "2024-01-01", "schedule": "daily"})
    url = f"/api/sources/{s['id']}"
    assert s["overrides"] == ["media", "since", "schedule"]
    r = post(client, url, {"reset": ["media"]})
    assert r["source"]["overrides"] == ["since", "schedule"] and r["source"]["options"]["media"] == "images"
    # Reset with other options in one request: the reset first, then those.
    r = post(client, url, {"reset": ["since"], "options": {"first_posts": 20}})
    assert r["source"]["overrides"] == ["first_posts", "schedule"]
    r = post(client, url, {"reset": True})
    assert r["source"]["overrides"] == [] and stored(s["id"]) == {"v": 2}
    assert r["source"]["options"]["schedule"] == "off"
    for bad in ["media", ["argv"], [1], {"media": True}, False]:
        assert client.post(url, json={"reset": bad}, headers=H).status_code == 400, bad


def test_defaults_are_checked(env, client):
    for tool, platform, block in [
            ("gallery-dl", "twitter", {"content": ["stories"]}), ("gallery-dl", "twitter", {"full_history": True}),
            ("gallery-dl", "twitter", {"argv": ["-x"]}), ("instaloader", "twitter", {"media": "images"}),
            ("gallery-dl", "twitter", {"since": "yesterday"}), ("gallery-dl", "../x", {"media": "images"}),
            ("yt-dlp", "youtube", {"media": "images"}), ("gallery-dl", "twitter", "images"),
            ("gallery-dl", "twitter", {"metadata": "slim"}), ("gallery-dl", "twitter", {"script": "nope"})]:
        r = defaults(client, tool, platform, block)
        assert r["ok"] is False, (tool, platform, block)
    assert "source_defaults" not in config.load()
    # An instaloader default that needs a login the tool's setting does not have is refused too.
    r = defaults(client, "instaloader", "instagram", {"content": ["stories"]})
    assert r["ok"] is False and "logged-in session" in r["error"]
    # Equal to BUILTIN: no default.
    assert defaults(client, "gallery-dl", "twitter", {"media": "all", "schedule": "off"})["ok"]
    assert "twitter" not in (config.load().get("source_defaults") or {}).get("gallery-dl", {})


def test_a_hand_edited_default_is_skipped(env, client):
    s = add(client, X)
    cfg = config.load()
    cfg["source_defaults"] = {"gallery-dl": {"twitter": {"media": "images or 1", "since": "2024-02-01",
                                                          "content": "media"}}, "bogus": []}
    config.save(cfg)
    o = get(client, f"/api/sources/{s['id']}")["options"]
    assert (o["media"], o["since"], o["content"]) == ("all", "2024-02-01", None)
    blocks = {(b["tool"], b["platform"]): b for b in get(client, "/api/config")["source_defaults"]}
    assert blocks[("gallery-dl", "twitter")]["set"] == ["since"]


def test_blocks_in_config(env, client):
    add(client, X)
    add(client, "https://x.com/other", options={"media": "images"})
    blocks = {(b["tool"], b["platform"]): b for b in get(client, "/api/config")["source_defaults"]}
    assert set(sources.DEFAULT_PAIRS) <= set(blocks)
    assert blocks[("gallery-dl", "instagram")]["choices"]["metadata"] == ["full", "none"]
    tw = blocks[("gallery-dl", "twitter")]
    assert tw["sources"] == 2 and tw["overrides"]["media"] == 1 and tw["set"] == []
    assert tw["choices"]["content"] == ["timeline", "media", "tweets", "with_replies"]
    assert tw["defaults"] == {k: sources.BUILTIN[k] for k in sources.DEFAULT_KEYS}
    assert "full_history" not in tw["defaults"]
    assert blocks[("instaloader", "instagram")]["choices"]["metadata"] == ["full", "slim", "none"]


def test_apply_to_all_sources(env, client):
    a = add(client, X, options={"media": "videos"})
    b = add(client, "https://x.com/other", options={"media": "images", "since": "2024-01-01"})
    c = add(client, "https://x.com/third")
    defaults(client, "gallery-dl", "twitter", {"media": "images"})
    r = post(client, "/api/source-defaults/apply", {"tool": "gallery-dl", "platform": "twitter", "option": "media"})
    assert (r["applied"], r["skipped"]) == (2, 0)
    assert [get(client, f"/api/sources/{s['id']}")["overrides"] for s in (a, b, c)] == [[], ["since"], []]
    assert stored(b["id"]) == {"v": 2, "since": "2024-01-01"}
    r = post(client, "/api/source-defaults/apply", {"tool": "gallery-dl", "platform": "twitter", "option": "media"})
    assert (r["applied"], r["skipped"]) == (0, 0)
    for bad in [{"tool": "gallery-dl", "platform": "twitter", "option": "full_history"},
                {"tool": "x", "platform": "twitter", "option": "media"}, {"tool": "gallery-dl", "option": "media"}]:
        assert client.post("/api/source-defaults/apply", json=bad, headers=H).status_code == 400, bad


def test_first_posts_default_is_for_a_first_sync_only(env, client):
    defaults(client, "gallery-dl", "twitter", {"first_posts": 30})
    s = add(client, X)
    assert after(build(s), "--post-range") == "1-30"
    conn = db.connect()
    # A first sync that failed, or was cancelled, leaves it a first sync.
    for result in ({"state": "failed", "ok_at": None}, {"state": "cancelled"}):
        with conn:
            conn.execute("UPDATE sources SET last_sync_at = 1, last_result = ? WHERE id = ?",
                         (json.dumps(result), s["id"]))
        assert after(build(s), "--post-range") == "1-30", result
        source = get(client, f"/api/sources/{s['id']}")
        assert (source["options"]["first_posts"], source["synced"]) == (30, False)
    # One that worked (now, or before a failed one) ends it.
    for result in ({"state": "done"}, {"state": "failed", "ok_at": 1}):
        with conn:
            conn.execute("UPDATE sources SET last_result = ? WHERE id = ?", (json.dumps(result), s["id"]))
        assert "--post-range" not in build(s), result
        assert get(client, f"/api/sources/{s['id']}")["synced"] is True


@pytest.mark.parametrize("legacy", [None, {"added": 3}, {"error": "rate_limited"}, "not json"])
def test_a_source_synced_before_results_had_a_state_is_synced(env, client, legacy):
    """A row from before: last_sync_at set, no result state stored. It is
    an established source, so neither the default last N nor a new one
    applies to it."""
    defaults(client, "gallery-dl", "twitter", {"first_posts": 30})
    s = add(client, X)
    conn = db.connect()
    stored = legacy if legacy is None or isinstance(legacy, str) else json.dumps(legacy)
    with conn:
        conn.execute("UPDATE sources SET last_sync_at = 1, last_result = ? WHERE id = ?", (stored, s["id"]))
    assert sources.synced_ok(sources.row(conn, s["id"])) is True
    assert "--post-range" not in build(s)
    url = f"/api/sources/{s['id']}"
    source = get(client, url)
    assert (source["synced"], source["options"]["first_posts"]) == (True, None)
    assert "synced already" in post(client, url, {"options": {"first_posts": 20}}, 400)["error"]
    # Never synced: still a first sync.
    with conn:
        conn.execute("UPDATE sources SET last_sync_at = NULL, last_result = NULL WHERE id = ?", (s["id"],))
    assert sources.synced_ok(sources.row(conn, s["id"])) is False
    assert after(build(s), "--post-range") == "1-30"


def test_last_n_can_be_set_until_a_sync_has_worked(env, client):
    """The same rule as the default's (sources.synced_ok): a failed first
    sync leaves last N open; one that worked closes it."""
    s = add(client, X)
    url = f"/api/sources/{s['id']}"
    conn = db.connect()
    with conn:
        conn.execute("UPDATE sources SET last_sync_at = 1, last_result = ? WHERE id = ?",
                     (json.dumps({"state": "failed", "ok_at": None}), s["id"]))
    assert post(client, url, {"options": {"first_posts": 20}})["source"]["options"]["first_posts"] == 20
    post(client, url, {"options": {"first_posts": None}})
    with conn:
        conn.execute("UPDATE sources SET last_result = ? WHERE id = ?", (json.dumps({"state": "done"}), s["id"]))
    r = post(client, url, {"options": {"first_posts": 20}}, 400)
    assert "synced already" in r["error"]


@pytest.mark.parametrize("legacy, overrides", [
    ({"full_history": False, "session": None, "content": None, "media": "all", "since": None, "first_posts": None,
      "script": None, "schedule": "off"}, []),
    ({"media": "images", "schedule": "off", "since": None}, ["media"]),
    ({"schedule": "daily", "content": ["media"]}, ["content", "schedule"]),
])
def test_a_legacy_row_overrides_only_what_differs_from_builtin(env, client, legacy, overrides):
    s = add(client, X)
    set_stored(s["id"], legacy)
    defaults(client, "gallery-dl", "twitter", {"media": "videos", "schedule": "weekly"})
    got = get(client, f"/api/sources/{s['id']}")
    assert got["overrides"] == overrides
    assert got["options"]["media"] == (legacy["media"] if "media" in overrides else "videos")


FROM_X = {**H, "Origin": "https://x.com", "Sec-Fetch-Site": "cross-site"}
COOKIES = {"mode": "cookies", "browser": "firefox"}


def test_another_site_gets_no_login_and_sees_no_defaults(env, client):
    """The userscript adds and syncs sources: one it adds has no login,
    whatever the defaults or the tool's setting say, now or later; it never
    reads what a source inherits, nor which login or script it uses."""
    defaults(client, "gallery-dl", "twitter", {"session": COOKIES, "media": "images"})
    r = client.get("/api/sources/resolve?url=https://x.com/someone", headers=FROM_X).get_json()
    assert r["ok"] and r["tool"] == "gallery-dl"
    assert not {"defaults", "session", "metadata"} & set(r)
    r = client.post("/api/sources", json={"target": X}, headers=FROM_X)
    assert r.status_code == 200, r.get_json()
    s = r.get_json()["source"]
    assert not {"defaults", "session", "metadata", "script_warning"} & set(s)
    assert s["options"]["session"] is None and s["options"]["script"] is None and s["dashboard_only"] is False
    assert stored(s["id"])["session"] == {"mode": "none"} and "script" in stored(s["id"])
    # The dashboard sees it all: no login, set on it; media inherited.
    mine = get(client, f"/api/sources/{s['id']}")
    assert mine["options"]["session"] == {"mode": "none"} and mine["session"]["mode"] == "none"
    assert "session" in mine["overrides"] and mine["options"]["media"] == "images"
    # A later login default does not reach it.
    defaults(client, "gallery-dl", "twitter", {"session": {**COOKIES, "browser": "chrome"}})
    assert get(client, f"/api/sources/{s['id']}")["session"]["mode"] == "none"
    # One the dashboard added, with the login it inherits: read from there
    # without it, and its sync only starts from the dashboard.
    own = add(client, "https://x.com/other")
    assert get(client, f"/api/sources/{own['id']}")["session"]["mode"] == "cookies"
    r = client.get(f"/api/sources/{own['id']}", headers=FROM_X).get_json()
    assert r["options"]["session"] is None and "session" not in r and "defaults" not in r and r["dashboard_only"]
    r = client.post(f"/api/sources/{own['id']}/sync", headers=FROM_X)
    assert r.status_code == 403 and r.get_json()["error"] == app_module.FOREIGN_LOGIN
