"""Presets: a source follows one preset of its tool and platform (Default,
a named one from Settings → Presets) or is Custom (sources.py "Presets:
what a source follows", app.py /api/presets). The Default preset's own
options: test_source_defaults.py."""
import json

import pytest

from conftest import H
from test_schedules import NOW, queued, sched, synced                              # noqa: F401 (fixtures)
from test_source_defaults import FROM_X, add, defaults, get, include, stored
from test_source_options import (TIKTOK, X, after, build, post, put_x, set_stored, sync_now, tools,  # noqa: F401
                                 x_account, yt_account)
from test_userdata import _rebuild, data_dir

import app as app_module
import config
import db
import scheduler
import sources

TW = {"tool": "gallery-dl", "platform": "twitter"}
COOKIES = {"mode": "cookies", "browser": "firefox"}


def preset(client, name, options=None, status=200, **pair):
    body = {**(pair or TW), "name": name}
    if options is not None:
        body["options"] = options
    r = post(client, "/api/presets", body, status)
    return r["preset"] if status == 200 else r


def follow(client, sid, pid, status=200, **options):
    body = {"preset": pid, **({"options": options} if options else {})}
    r = post(client, f"/api/sources/{sid}", body, status)
    return r["source"] if status == 200 else r


def block(client, tool="gallery-dl", platform="twitter"):
    return next(b for b in get(client, "/api/config")["source_defaults"] if (b["tool"], b["platform"]) == (tool, platform))


# ---------------------------------------------------------------------------
# The presets themselves
# ---------------------------------------------------------------------------

def test_create_rename_edit_delete(env, client):
    p = preset(client, "  Media   only ", {"content": ["media"], "media": "images", "schedule": "off"})
    assert p["name"] == "Media only" and p["tool"] == "gallery-dl" and p["platform"] == "twitter"
    assert p["set"] == ["content", "media"]                       # one equal to BUILTIN is not kept
    assert p["options"] == {**{k: sources.BUILTIN[k] for k in sources.DEFAULT_KEYS}, "content": ["media"],
                            "media": "images"}
    assert config.load()["source_presets"] == {"gallery-dl": {"twitter": [
        {"id": p["id"], "name": "Media only", "options": {"content": ["media"], "media": "images"}}]}}
    # Listed with its platform, after Default, with how many follow it.
    b = block(client)
    assert b["presets"] == [{**p, "sources": 0}] and b["set"] == []
    # Renamed, its options stay; other options, its name stays.
    url = f"/api/presets/{p['id']}"
    assert post(client, url, {"name": "Pictures"})["preset"] == {**p, "name": "Pictures"}
    r = post(client, url, {"options": {"since": "2024-01-01"}})["preset"]
    assert (r["name"], r["set"]) == ("Pictures", ["since"]) and r["options"]["media"] == "all"
    # With no options: a preset that downloads as the tool does on its own.
    empty = preset(client, "Plain")
    assert empty["set"] == [] and [x["name"] for x in block(client)["presets"]] == ["Pictures", "Plain"]
    assert empty["id"] != p["id"]
    r = client.delete(url, headers=H)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "sources": 0}
    assert [x["name"] for x in block(client)["presets"]] == ["Plain"]
    assert client.delete(url, headers=H).status_code == 404
    assert post(client, url, {"name": "Again"}, 404)["error"] == "no such preset"
    client.delete(f"/api/presets/{empty['id']}", headers=H)
    assert config.load()["source_presets"] == {}


def test_presets_are_checked(env, client):
    for name in ("", "   ", None, 5, "x" * 41, "Default", "custom", "Custom…", "a\x00b"):
        assert preset(client, name, status=400)["ok"] is False, name
    for pair in ({"tool": "gallery-dl"}, {"tool": "nope", "platform": "twitter"}, {"tool": "instaloader", "platform": "twitter"},
                 {"tool": "gallery-dl", "platform": "../x"}, {"tool": "none", "platform": "spotify"}):
        assert preset(client, "One", status=400, **pair)["ok"] is False, pair
    # Its options: as Default's (what the tool can do there; no full history, no download).
    for options in ({"content": ["stories"]}, {"full_history": True}, {"download": "off"}, {"argv": ["-x"]},
                    {"since": "yesterday"}, "images", {"metadata": "slim"}, {"script": "nope"}, {"session": {"mode": "login", "user": "me"}}):
        assert preset(client, "One", options, 400)["ok"] is False, options
    assert preset(client, "One", {"media": "images"}, 400, tool="yt-dlp", platform="youtube")["ok"] is False
    # Kinds that need a login the tool's session is not: refused, as for Default.
    r = preset(client, "Stories", {"content": ["stories"]}, 400, tool="instaloader", platform="instagram")
    assert "logged-in session" in r["error"]
    assert "source_presets" not in config.load()
    # Names are unique per platform and tool, whatever the case; elsewhere the same name is fine.
    one = preset(client, "One")
    assert "already a preset named one" in preset(client, "one", status=400)["error"]
    assert preset(client, "One", tool="yt-dlp", platform="youtube")["id"] != one["id"]
    two = preset(client, "Two")
    assert "already" in post(client, f"/api/presets/{two['id']}", {"name": "ONE"}, 400)["error"]
    assert post(client, f"/api/presets/{two['id']}", {"name": "two"})["preset"]["name"] == "two"     # its own
    assert post(client, f"/api/presets/{two['id']}", {}, 400)["ok"] is False
    assert post(client, f"/api/presets/{two['id']}", {"options": {"media": "nope"}}, 400)["ok"] is False
    assert post(client, f"/api/presets/{two['id']}", {"options": ["media"]}, 400)["ok"] is False


def test_at_most_twenty_per_platform(env, client):
    for i in range(sources.PRESETS_MAX):
        preset(client, f"P{i}")
    assert "delete one first" in preset(client, "One too many", status=400)["error"]
    assert preset(client, "Elsewhere", tool="yt-dlp", platform="youtube")["name"] == "Elsewhere"
    ids = [p["id"] for b in get(client, "/api/config")["source_defaults"] for p in b["presets"]]
    assert len(ids) == len(set(ids)) == sources.PRESETS_MAX + 1


def test_another_site_reaches_no_preset(env, client):
    p = preset(client, "Mine", {"session": COOKIES})
    assert client.post("/api/presets", json={**TW, "name": "Theirs"}, headers=FROM_X).status_code == 403
    assert client.post(f"/api/presets/{p['id']}", json={"name": "Theirs"}, headers=FROM_X).status_code == 403
    assert client.delete(f"/api/presets/{p['id']}", headers=FROM_X).status_code == 403
    # Nor a new source that follows one (it could carry a login or a script).
    r = client.post("/api/sources", json={"target": X, "preset": p["id"]}, headers=FROM_X)
    assert r.status_code == 403 and r.get_json()["error"] == app_module.FOREIGN_REFUSED
    r = client.get("/api/sources/resolve?url=https://x.com/someone", headers=FROM_X).get_json()
    assert r["ok"] and "presets" not in r
    s = follow(client, add(client, X)["id"], p["id"])
    seen = client.get(f"/api/sources/{s['id']}", headers=FROM_X).get_json()
    assert not {"preset", "presets", "custom", "session"} & set(seen) and seen["dashboard_only"] is True


# ---------------------------------------------------------------------------
# What a source follows
# ---------------------------------------------------------------------------

def test_a_named_preset_is_over_builtin_never_over_default(env, client):
    defaults(client, "gallery-dl", "twitter", {"since": "2025-06-01", "schedule": "daily", "media": "videos"})
    p = preset(client, "Media", {"content": ["media", "with_replies"], "media": "images"})
    s = add(client, X)
    assert (s["preset"], s["custom"], s["presets"]) == (None, False, [{"id": p["id"], "name": "Media"}])
    assert s["options"]["since"] == "2025-06-01" and include(build(s)) is None            # Default
    s = follow(client, s["id"], p["id"])
    assert (s["preset"], s["custom"], s["overrides"]) == ({"id": p["id"], "name": "Media"}, False, [])
    assert stored(s["id"]) == {"v": 2, "preset": p["id"]}
    # The preset's own, and BUILTIN for what it does not set: not Default's date, schedule or media.
    o = s["options"]
    assert (o["content"], o["media"], o["since"], o["schedule"]) == (["media", "with_replies"], "images", None, "off")
    args = build(s)
    assert include(args) == "include=media,with-replies" and "--date-after" not in args
    assert s["defaults"] == o and s["schedule"]["every"] == "off"
    # The preset edited in Settings: its sources follow at their next sync.
    post(client, f"/api/presets/{p['id']}", {"options": {"content": ["tweets"], "since": "2024-02-01"}})
    args = build(s)
    assert include(args) == "include=tweets" and after(args, "--date-after") == "2024-01-31T23:59:59"
    # Back to Default.
    s = follow(client, s["id"], None)
    assert (s["preset"], s["custom"]) == (None, False) and stored(s["id"]) == {"v": 2}
    assert (s["options"]["since"], s["options"]["media"]) == ("2025-06-01", "videos")


def test_only_what_the_source_can_have(env, client):
    """As Default's options (sources._resolve): kinds only when its link
    offers a choice, "last N" only before a first sync that worked and
    never with full history."""
    p = preset(client, "Last", {"content": ["media"], "first_posts": 30})
    page = follow(client, add(client, X)["id"], p["id"])
    tab = follow(client, add(client, "https://x.com/other/media")["id"], p["id"])
    assert page["options"]["content"] == ["media"] and tab["options"]["content"] is None
    assert after(build(page), "--post-range") == "1-30" and include(build(tab)) is None
    synced(page["id"], 1)
    assert "--post-range" not in build(page) and get(client, f"/api/sources/{page['id']}")["options"]["first_posts"] is None
    # Full history asked for alone: "last N" of the preset is not for it.
    r = post(client, f"/api/sources/{tab['id']}", {"options": {"full_history": True}})["source"]
    assert (r["options"]["full_history"], r["options"]["first_posts"]) == (True, None)
    assert (r["preset"]["id"], r["custom"]) == (p["id"], False)
    assert stored(tab["id"]) == {"v": 2, "preset": p["id"], "full_history": True}


def test_picking_a_preset_clears_its_own_options_but_download_and_full_history(env, client):
    p = preset(client, "Images", {"media": "images"})
    s = add(client, X, options={"media": "videos", "since": "2024-01-01", "schedule": "weekly", "download": "off",
                                "full_history": True})
    assert s["custom"] is True and s["preset"] is None and s["profile_only"] is True
    s = follow(client, s["id"], p["id"])
    assert stored(s["id"]) == {"v": 2, "preset": p["id"], "download": "off", "full_history": True}
    assert (s["custom"], s["overrides"], s["profile_only"]) == (False, ["download", "full_history"], True)
    assert (s["options"]["media"], s["options"]["since"], s["options"]["schedule"]) == ("images", None, "off")
    # With it, whether it downloads: the select's "a preset on a profile-only one turns it on".
    s = follow(client, s["id"], None, download="platform")
    assert stored(s["id"]) == {"v": 2, "full_history": True} and s["profile_only"] is False
    s = follow(client, s["id"], p["id"], download="on", full_history=False)
    assert stored(s["id"]) == {"v": 2, "preset": p["id"], "download": "on"}
    # Nothing else can come with a preset, nor custom or reset; an unknown id is refused.
    url = f"/api/sources/{s['id']}"
    assert follow(client, s["id"], p["id"], 400, media="videos")["error"] == app_module.PRESET_OPTIONS
    assert post(client, url, {"preset": p["id"], "custom": True}, 400)["ok"] is False
    assert post(client, url, {"preset": None, "reset": True}, 400)["ok"] is False
    for bad in ("nope", "", 5, ["x"], {"id": p["id"]}, True):
        assert "no such preset" in follow(client, s["id"], bad, 400)["error"], bad
    other = preset(client, "Elsewhere", tool="yt-dlp", platform="youtube")
    assert "no such preset for twitter (gallery-dl)" in follow(client, s["id"], other["id"], 400)["error"]
    assert stored(s["id"]) == {"v": 2, "preset": p["id"], "download": "on"}


def test_custom_starts_from_what_it_has_and_follows_nothing(env, client):
    defaults(client, "gallery-dl", "twitter", {"schedule": "daily"})
    p = preset(client, "Images", {"media": "images", "since": "2024-01-01"})
    s = follow(client, add(client, X)["id"], p["id"])
    url = f"/api/sources/{s['id']}"
    # The Custom dialog saves what it shows: P's values, with what the user changed.
    r = post(client, url, {"custom": True, "options": {**s["options"], "since": "2024-03-01"}})["source"]
    assert (r["custom"], r["preset"]) == (True, None)
    assert stored(s["id"]) == {"v": 2, "session": None, "content": None, "media": "images", "since": "2024-03-01",
                               "first_posts": None, "script": None, "metadata": None, "schedule": "off"}
    assert r["overrides"] == list(sources.DEFAULT_KEYS)
    # Neither the preset nor Default moves it now.
    post(client, f"/api/presets/{p['id']}", {"options": {"media": "videos"}})
    defaults(client, "gallery-dl", "twitter", {"schedule": "weekly", "media": "videos"})
    o = get(client, url)["options"]
    assert (o["media"], o["since"], o["schedule"]) == ("images", "2024-03-01", "off")
    # Saved with no change from Default, it is Custom all the same: the user asked.
    plain = add(client, "https://x.com/other")
    r = post(client, f"/api/sources/{plain['id']}", {"custom": True})["source"]
    assert r["custom"] is True and r["options"] == plain["options"]
    assert post(client, url, {"custom": "yes"}, 400)["ok"] is False


def test_an_option_set_on_a_source_that_follows_a_preset_makes_it_custom(env, client):
    """Options of its own and a named preset do not mix: the API's plain
    "options" on one that follows a preset keeps what it had from it."""
    p = preset(client, "Images", {"media": "images", "since": "2024-01-01"})
    s = follow(client, add(client, X)["id"], p["id"])
    url = f"/api/sources/{s['id']}"
    # Sent as it is: no change, it goes on following.
    assert post(client, url, {"options": {"media": "images"}})["source"]["preset"]["id"] == p["id"]
    r = post(client, url, {"options": {"schedule": "daily"}})["source"]
    assert (r["custom"], r["preset"]) == (True, None)
    o = r["options"]
    assert (o["media"], o["since"], o["schedule"]) == ("images", "2024-01-01", "daily")
    # One that follows Default: only what changed is its own, as before; the rest follows Default.
    d = add(client, "https://x.com/other")
    r = post(client, f"/api/sources/{d['id']}", {"options": {"media": "videos"}})["source"]
    assert (r["custom"], r["overrides"]) == (True, ["media"]) and stored(d["id"]) == {"v": 2, "media": "videos"}
    defaults(client, "gallery-dl", "twitter", {"since": "2025-01-01"})
    assert get(client, f"/api/sources/{d['id']}")["options"]["since"] == "2025-01-01"
    # Reset all: Default again.
    r = post(client, f"/api/sources/{d['id']}", {"reset": True})["source"]
    assert (r["custom"], r["preset"]) == (False, None)
    # Download alone is no option of a preset: it stays on its preset, and is not Custom.
    s2 = follow(client, add(client, "https://x.com/third")["id"], p["id"])
    r = post(client, f"/api/sources/{s2['id']}", {"options": {"download": "off"}})["source"]
    assert (r["custom"], r["preset"]["id"], r["profile_only"]) == (False, p["id"], True)
    r = post(client, f"/api/sources/{s2['id']}", {"reset": True})["source"]
    assert r["preset"]["id"] == p["id"] and r["options"]["download"] == "off"       # reset keeps its preset


def test_a_source_that_already_sets_options_is_custom(env, client):
    """Nothing is migrated: a source stored with overrides, now or before
    "v", reads as Custom over Default."""
    defaults(client, "gallery-dl", "twitter", {"schedule": "weekly"})
    s = add(client, X)
    set_stored(s["id"], {"v": 2, "media": "images"})
    got = get(client, f"/api/sources/{s['id']}")
    assert (got["custom"], got["preset"], got["options"]["schedule"]) == (True, None, "weekly")
    set_stored(s["id"], {"media": "videos", "schedule": "off", "since": None, "full_history": False})
    got = get(client, f"/api/sources/{s['id']}")
    assert (got["custom"], got["overrides"], got["options"]["schedule"]) == (True, ["media"], "weekly")
    # Only whether it downloads, or a full history: not Custom.
    set_stored(s["id"], {"v": 2, "download": "off", "full_history": True})
    assert get(client, f"/api/sources/{s['id']}")["custom"] is False


@pytest.mark.parametrize("value", ["gone", "", 5, None, ["x"], "../../etc"])
def test_a_preset_that_is_not_there_reads_as_default(env, client, value):
    defaults(client, "gallery-dl", "twitter", {"media": "images"})
    s = add(client, X)
    set_stored(s["id"], {"v": 2, "preset": value})
    got = get(client, f"/api/sources/{s['id']}")
    assert (got["preset"], got["custom"], got["options"]["media"]) == (None, False, "images")
    assert include(build(s)) is None
    assert block(client)["default_sources"] == 1
    # Another platform's preset is none of its own either.
    other = preset(client, "Elsewhere", {"schedule": "daily"}, tool="yt-dlp", platform="youtube")
    set_stored(s["id"], {"v": 2, "preset": other["id"]})
    got = get(client, f"/api/sources/{s['id']}")
    assert (got["preset"], got["options"]["schedule"], got["options"]["media"]) == (None, "off", "images")


def test_hand_edited_presets_are_checked_again(env, client):
    cfg = config.load()
    cfg["source_presets"] = {"gallery-dl": {"twitter": [
        {"id": "good1", "name": " Good ", "options": {"media": "images or 1", "since": "2024-02-01", "argv": ["-x"]}},
        {"id": "good1", "name": "Same id"}, {"id": "BAD ID", "name": "Bad id"}, {"id": "noname", "name": ""},
        "nope", {"id": "plain1", "name": "Plain", "options": "nope"}], "../x": [{"id": "abcd", "name": "X"}]},
        "bogus": {"twitter": [{"id": "abcd", "name": "X"}]}, "yt-dlp": "nope"}
    config.save(cfg)
    b = block(client)
    assert [(p["id"], p["name"], p["set"]) for p in b["presets"]] == [("good1", "Good", ["since"]), ("plain1", "Plain", [])]
    s = add(client, X)
    set_stored(s["id"], {"v": 2, "preset": "good1"})
    o = get(client, f"/api/sources/{s['id']}")["options"]
    assert (o["media"], o["since"]) == ("all", "2024-02-01")
    # A save rewrites its platform's list with what is valid; the rest of the file stays.
    post(client, "/api/presets/plain1", {"name": "Plainer"})
    kept = config.load()["source_presets"]
    assert kept["gallery-dl"]["twitter"] == [{"id": "good1", "name": "Good", "options": {"since": "2024-02-01"}},
                                             {"id": "plain1", "name": "Plainer", "options": {}}]
    assert kept["bogus"] == {"twitter": [{"id": "abcd", "name": "X"}]}


def test_deleting_a_preset_sends_its_sources_back_to_default(env, client):
    defaults(client, "gallery-dl", "twitter", {"media": "videos"})
    p = preset(client, "Images", {"media": "images"})
    keep = preset(client, "Kept", {"since": "2024-01-01"})
    a = follow(client, add(client, X)["id"], p["id"], download="off")
    b = follow(client, add(client, "https://x.com/other")["id"], p["id"])
    c = follow(client, add(client, "https://x.com/third")["id"], keep["id"])
    d = add(client, "https://x.com/fourth", options={"media": "all"})
    blk = block(client)
    assert {x["name"]: x["sources"] for x in blk["presets"]} == {"Images": 2, "Kept": 1}
    assert (blk["sources"], blk["default_sources"], blk["custom"]) == (4, 0, 1)
    r = client.delete(f"/api/presets/{p['id']}", headers=H).get_json()
    assert r == {"ok": True, "sources": 2}
    # Its id is off them; what is their own (download) stays.
    assert stored(a["id"]) == {"v": 2, "download": "off"} and stored(b["id"]) == {"v": 2}
    for s in (a, b):
        got = get(client, f"/api/sources/{s['id']}")
        assert (got["preset"], got["custom"], got["options"]["media"]) == (None, False, "videos")
    assert get(client, f"/api/sources/{c['id']}")["preset"]["id"] == keep["id"]
    assert get(client, f"/api/sources/{d['id']}")["custom"] is True
    blk = block(client)
    assert (blk["default_sources"], blk["custom"], [x["sources"] for x in blk["presets"]]) == (2, 1, [1])


def test_a_new_source_follows_a_preset_or_default(env, client):
    defaults(client, "gallery-dl", "twitter", {"since": "2025-06-01"})
    p = preset(client, "Images", {"media": "images", "first_posts": 20})
    # What the Add box shows: each named preset with what this profile would download.
    r = get(client, "/api/sources/resolve?url=https://x.com/someone/media")
    assert r["defaults"]["since"] == "2025-06-01"
    [named] = r["presets"]
    assert (named["id"], named["name"], named["session"]) == (p["id"], "Images", {"mode": "none"})
    assert (named["options"]["media"], named["options"]["first_posts"], named["options"]["since"]) == ("images", 20, None)
    s = post(client, "/api/sources", {"target": X, "preset": p["id"]})["source"]
    assert (s["preset"]["id"], s["custom"]) == (p["id"], False) and stored(s["id"]) == {"v": 2, "preset": p["id"]}
    assert after(build(s), "--post-range") == "1-20"
    # null, or none sent: Default. With download, profile only from the start.
    s = post(client, "/api/sources", {"target": "https://x.com/other", "preset": None})["source"]
    assert (s["preset"], s["custom"]) == (None, False) and stored(s["id"]) == {"v": 2}
    s = post(client, "/api/sources", {"target": "https://x.com/third", "preset": p["id"],
                                      "options": {"download": "off"}})["source"]
    assert stored(s["id"]) == {"v": 2, "preset": p["id"], "download": "off"} and s["profile_only"] is True
    # Full history with a "last N" preset: the history, not the N.
    s = post(client, "/api/sources", {"target": "https://x.com/fourth", "preset": p["id"],
                                      "options": {"full_history": True}})["source"]
    assert (s["options"]["full_history"], s["options"]["first_posts"]) == (True, None)
    for body in ({"preset": "nope"}, {"preset": p["id"], "options": {"media": "videos"}}, {"preset": 5},
                 {"preset": p["id"], "options": "x"}):
        assert post(client, "/api/sources", {"target": "https://x.com/fifth", **body}, 400)["ok"] is False, body
    assert post(client, "/api/sources", {"target": "https://open.spotify.com/artist/abc", "tool": "none",
                                         "preset": p["id"]}, 400)["ok"] is False
    # Every other way a source is made: Default.
    post(client, "/api/people", {"name": "Someone", "profiles": ["https://x.com/sixth"]})
    [made] = [x for x in get(client, "/api/sources")["sources"] if x["target"].endswith("/sixth")]
    assert (made["preset"], made["custom"]) == (None, False)


def test_a_preset_that_needs_a_login_is_refused_where_there_is_none(env, client):
    """A source's own session is not a preset's: kinds that need a login
    cannot be followed when neither the preset nor the tool has one."""
    insta = {"tool": "instaloader", "platform": "instagram"}
    post(client, "/api/config", {"instaloader": {"session": {"mode": "login", "user": "me"}}})
    p = preset(client, "Stories", {"content": ["posts", "stories"]}, **insta)
    s = add(client, "carol.cooks", "instaloader")
    post(client, "/api/config", {"instaloader": {"session": {"mode": "none"}}})
    assert "logged-in session" in follow(client, s["id"], p["id"], 400)["error"]
    r = post(client, "/api/sources", {"target": "dave.draws", "tool": "instaloader", "preset": p["id"]}, 400)
    assert "logged-in session" in r["error"]
    assert get(client, f"/api/sources/{s['id']}")["preset"] is None


def test_not_while_its_sync_runs(env, client, monkeypatch):
    p = preset(client, "Images", {"media": "images"})
    s = add(client, X)
    monkeypatch.setattr(app_module, "_sources_active", lambda: {s["id"]: {"id": 1, "state": "running"}})
    assert "queued or running" in follow(client, s["id"], p["id"], 409)["error"]
    assert "queued or running" in post(client, f"/api/sources/{s['id']}", {"custom": True}, 409)["error"]
    assert stored(s["id"]) == {"v": 2}


# ---------------------------------------------------------------------------
# Syncs, schedules, user data
# ---------------------------------------------------------------------------

def test_a_sync_runs_what_its_preset_says_and_stays_on_it(env, client, tools):
    """"Last N" from a preset: the first sync gets N, its floor is kept
    beside the source's options, so it goes on following the preset."""
    account = x_account()
    for post_ in account["posts"]:
        post_.pop("in")
    put_x(tools, account)
    p = preset(client, "Last two", {"first_posts": 2, "media": "images"})
    s = follow(client, add(client, X)["id"], p["id"])
    job = sync_now(client, s["id"])
    assert job["state"] == "done", job["message"]
    assert after(job["argv"], "--post-range") == "1-2" and "extension in exts_image" in job["argv"]
    got = get(client, f"/api/sources/{s['id']}")
    assert (got["preset"]["id"], got["custom"], got["overrides"]) == (p["id"], False, [])
    assert stored(s["id"]) == {"v": 2, "preset": p["id"], "floor": "2024-06-03"}
    assert (got["options"]["first_posts"], got["options"]["since"]) == (None, "2024-06-03")
    job = sync_now(client, s["id"])
    assert "--post-range" not in job["argv"] and after(job["argv"], "--date-after") == "2024-06-02T23:59:59"
    # The floor is the least its date can be: a later one of the preset wins, an earlier one does not.
    post(client, f"/api/presets/{p['id']}", {"options": {"since": "2024-06-10"}})
    assert get(client, f"/api/sources/{s['id']}")["options"]["since"] == "2024-06-10"
    post(client, f"/api/presets/{p['id']}", {"options": {"since": "2024-01-01"}})
    assert get(client, f"/api/sources/{s['id']}")["options"]["since"] == "2024-06-03"
    # It stays with another preset, and Default; Custom takes it as its own date.
    assert follow(client, s["id"], None)["options"]["since"] == "2024-06-03"
    r = post(client, f"/api/sources/{s['id']}", {"custom": True})["source"]
    assert r["options"]["since"] == "2024-06-03" and stored(s["id"])["since"] == "2024-06-03"
    assert "floor" not in stored(s["id"])


def test_full_history_drops_the_floor_and_is_done_once(env, client, tools):
    account = x_account()
    for post_ in account["posts"]:
        post_.pop("in")
    put_x(tools, account)
    p = preset(client, "Last two", {"first_posts": 2})
    s = follow(client, add(client, X)["id"], p["id"])
    sync_now(client, s["id"])
    assert stored(s["id"]) == {"v": 2, "preset": p["id"], "floor": "2024-06-03"}
    # The ⋯ menu's "Sync full history…": the one-off, then the sync.
    r = post(client, f"/api/sources/{s['id']}", {"options": {"full_history": True}})["source"]
    assert (r["options"]["full_history"], r["options"]["since"], r["preset"]["id"]) == (True, None, p["id"])
    assert stored(s["id"]) == {"v": 2, "preset": p["id"], "full_history": True}
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and "skip=true" in job["argv"] and "--date-after" not in job["argv"]
    assert stored(s["id"]) == {"v": 2, "preset": p["id"]}


def test_last_n_of_a_preset_that_lists_nothing_known_is_for_the_next_sync_too(env, client, tools):
    tools["dl"].write_text(json.dumps({"accounts": {TIKTOK: yt_account(TIKTOK, [])}, "fail": None}))
    p = preset(client, "Last three", {"first_posts": 3}, tool="yt-dlp", platform="tiktok")
    s = follow(client, add(client, TIKTOK)["id"], p["id"])
    assert sync_now(client, s["id"])["state"] == "done"
    got = get(client, f"/api/sources/{s['id']}")
    assert (got["synced"], got["options"]["first_posts"], got["custom"]) == (True, 3, False)
    assert stored(s["id"]) == {"v": 2, "preset": p["id"], "first_pending": True}
    # Default's "last N" the same way.
    client.delete(f"/api/presets/{p['id']}", headers=H)
    defaults(client, "yt-dlp", "tiktok", {"first_posts": 5})
    got = get(client, f"/api/sources/{s['id']}")
    assert (got["options"]["first_posts"], got["preset"], got["custom"]) == (5, None, False)


def test_the_scheduler_follows_the_preset(env, client, queued):
    p = preset(client, "Hourly", {"schedule": "hourly"})
    off = add(client, "https://x.com/off")
    s = follow(client, add(client, X)["id"], p["id"])
    assert scheduler.tick() == [{"id": 1}] and queued == [s["id"]]
    synced(s["id"], NOW)
    assert scheduler.tick(NOW + 3599) == [] and len(scheduler.tick(NOW + 3600)) == 1
    # The preset's schedule changed: its sources' too, at once.
    synced(s["id"], NOW)
    post(client, f"/api/presets/{p['id']}", {"options": {"schedule": "daily"}})
    assert get(client, f"/api/sources/{s['id']}")["schedule"]["every"] == "daily"
    assert scheduler.tick(NOW + 3600) == [] and len(scheduler.tick(NOW + 86400)) == 1
    # Deleted: back to Default, which has no schedule.
    synced(s["id"], NOW)
    client.delete(f"/api/presets/{p['id']}", headers=H)
    assert scheduler.tick(NOW + 7 * 86400) == [] and off["id"] not in queued


def test_the_preset_followed_survives_export_and_restore(env, client):
    p = preset(client, "Images", {"media": "images"})
    s = follow(client, add(client, X)["id"], p["id"], download="off")
    gone = add(client, "https://x.com/other")
    set_stored(gone["id"], {"v": 2, "preset": "gone99"})
    conn = db.connect()
    _rebuild(conn, data_dir(env))
    back = {x["target"]: x for x in get(client, "/api/sources")["sources"]}
    a, b = back[X], back["https://x.com/other"]
    assert (a["preset"], a["options"]["media"], a["options"]["download"]) == ({"id": p["id"], "name": "Images"}, "images", "off")
    assert json.loads(conn.execute("SELECT options FROM sources WHERE id = ?", (a["id"],)).fetchone()[0]) == \
        {"v": 2, "preset": p["id"], "download": "off"}
    # An id with no preset behind it (another machine's config): Default.
    assert (b["preset"], b["custom"], b["options"]["media"]) == (None, False, "all")
