"""Download defaults per tool and platform (config ``source_defaults``): what
a source inherits for each option it does not set itself, the API that
edits them, resets a source's own choices and applies a default to every
source (sources.py, app.py)."""
import json

import pytest

from conftest import H
from test_source_options import X, after, build, post, set_stored

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


def test_saving_a_value_equal_to_the_default_follows_it(env, client):
    s = add(client, X, options={"media": "videos"})
    url = f"/api/sources/{s['id']}"
    defaults(client, "gallery-dl", "twitter", {"media": "videos"})
    # It was set before the default: it stays set until saved again equal to it.
    assert get(client, url)["overrides"] == ["media"]
    r = post(client, url, {"options": {"media": "videos", "since": "2024-01-01"}})
    assert r["source"]["overrides"] == ["since"]
    defaults(client, "gallery-dl", "twitter", {"media": "images"})
    assert get(client, url)["options"]["media"] == "images"


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
    with conn:
        conn.execute("UPDATE sources SET last_sync_at = 1 WHERE id = ?", (s["id"],))
    assert "--post-range" not in build(s)


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
