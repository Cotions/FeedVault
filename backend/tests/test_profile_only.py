"""Profile-only sources (#218): an account shown as theirs, never
downloaded. Per platform (Settings → Profile only, config
``profile_only_platforms``) and per source (its ``download`` option); a
profile of a site no downloader takes has the tool "none" and no folder.

Never synced: no Sync, no Sync all, no schedule, no job, no folder. No
tool runs here: jobs.submit is a recorder, and the tool guard is on."""
import json
import os

import pytest

from conftest import H
from fakes import owner, write_post

import biofetch
import config
import db
import jobs
import scanner
import scheduler
import sources
import sync
import userdata

TS = 1717243200


def get(client, url, status=200):
    r = client.get(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def post(client, url, body=None, status=200):
    r = client.post(url, json={} if body is None else body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


@pytest.fixture
def submitted(monkeypatch):
    """jobs.submit recording the source of each sync it is given: nothing runs."""
    calls = []

    def fake(kind, params):
        calls.append(int(params["source"]))
        return {"id": len(calls), "kind": kind, "params": params, "created_at": 1, "state": "queued"}
    monkeypatch.setattr(jobs, "submit", fake)
    monkeypatch.setattr(jobs, "get", lambda job_id: None)
    monkeypatch.setattr(sync, "_batch", None)
    return calls


@pytest.fixture
def schedule(monkeypatch):
    """scheduler.tick with every tool found (a path, never run) and no note,
    hold or spread left from another test."""
    monkeypatch.setattr(jobs, "tool_path", lambda name: f"/fake/{name}")
    monkeypatch.setattr(scheduler, "_notes", {})
    monkeypatch.setattr(scheduler, "_held", {})
    monkeypatch.setattr(scheduler, "_last", {})
    return lambda: scheduler.tick(TS)


def tree(root):
    """Every folder and file under ``root``, relative."""
    return sorted(os.path.relpath(os.path.join(d, n), root) for d, ds, fs in os.walk(root) for n in ds + fs)


def add(client, target, status=200, **body):
    return post(client, "/api/sources", {"target": target, **body}, status)


# ---------------------------------------------------------------------------
# Per platform
# ---------------------------------------------------------------------------

def test_a_profile_only_platform_is_never_synced(env, client, submitted, schedule):
    x = add(client, "https://x.com/someone")["source"]
    yt = add(client, "https://www.youtube.com/@someone")["source"]
    forced = add(client, "https://twitter.com/forced", options={"download": "on"})["source"]
    for s in (x, yt, forced):
        post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "hourly"}})
    assert get(client, "/api/sources/resolve?url=x.com/new")["profile_only"] is False
    r = post(client, "/api/config", {"profile_only_platforms": ["twitter"]})
    assert r["config"]["profile_only_platforms"] == ["twitter"]
    assert {"twitter", "youtube", "instagram"} <= set(r["config"]["platforms"])
    listed = {s["id"]: s for s in get(client, "/api/sources")["sources"]}
    assert (listed[x["id"]]["profile_only"], listed[yt["id"]]["profile_only"], listed[forced["id"]]["profile_only"]) \
        == (True, False, False)
    # Its schedule waits: no next time while it is profile only.
    assert listed[x["id"]]["schedule"]["next_at"] is None and listed[yt["id"]]["schedule"]["next_at"] is not None
    assert get(client, "/api/sources/resolve?url=x.com/new")["profile_only"] is True
    # The scheduler queues the others only, one per platform.
    assert sorted(j["params"]["source"] for j in schedule()) == sorted([str(yt["id"]), str(forced["id"])])
    assert x["id"] not in submitted
    # Sync all leaves it out, counted.
    submitted.clear()
    r = post(client, "/api/sources/sync-all")
    assert (r["profile_only"], r["not_synced"], sorted(submitted)) == (1, 0, sorted([yt["id"], forced["id"]]))
    # Sync: refused, with why.
    r = post(client, f"/api/sources/{x['id']}/sync", status=409)
    assert r["profile_only"] is True and "profile only" in r["error"]
    # Out of the list again: it syncs.
    post(client, "/api/config", {"profile_only_platforms": []})
    submitted.clear()
    post(client, f"/api/sources/{x['id']}/sync")
    assert submitted == [x["id"]]
    assert not os.path.exists(x["folder"])     # jobs.submit records: no tool made it


def test_the_platform_list_is_checked(env, client):
    assert get(client, "/api/config")["profile_only_platforms"] == []
    for bad in ["twitter", [3], ["Not a platform!"], [f"p{i}" for i in range(sources.PLATFORMS_MAX + 1)]]:
        assert post(client, "/api/config", {"profile_only_platforms": bad})["ok"] is False, bad
    r = post(client, "/api/config", {"profile_only_platforms": ["Spotify", "twitch", "spotify"]})
    assert r["config"]["profile_only_platforms"] == ["spotify", "twitch"]
    assert config.load()["profile_only_platforms"] == ["spotify", "twitch"]
    # Edited by hand: what is malformed is left out.
    cfg = config.load()
    cfg["profile_only_platforms"] = ["twitch", 3, "Bad One"]
    config.save(cfg)
    assert sources.profile_only_platforms() == ["twitch"]


# ---------------------------------------------------------------------------
# Per source
# ---------------------------------------------------------------------------

def test_download_off_on_a_normal_platform(env, client, submitted, schedule):
    s = add(client, "https://x.com/someone", options={"download": "off", "schedule": "daily"})["source"]
    assert (s["profile_only"], s["options"]["download"], s["overrides"]) == (True, "off", ["download", "schedule"])
    assert s["needs_tool"] is False and s["schedule"]["next_at"] is None
    r = post(client, f"/api/sources/{s['id']}/sync", status=409)
    assert r["profile_only"] is True and "x.com/someone is profile only" in r["error"]
    assert post(client, "/api/sources/sync-all")["profile_only"] == 1
    assert schedule() == [] and submitted == [] and tree(env["media"]) == []
    # Download always, on a profile-only platform: it syncs.
    post(client, "/api/config", {"profile_only_platforms": ["twitter"]})
    for value, only in (("platform", True), ("on", False)):
        got = post(client, f"/api/sources/{s['id']}", {"options": {"download": value}})["source"]
        assert got["profile_only"] is only, value
    assert [j["params"]["source"] for j in schedule()] == [str(s["id"])]
    assert post(client, f"/api/sources/{s['id']}", {"options": {"download": "maybe"}}, 400)["ok"] is False


def test_no_route_queues_the_sync_of_a_profile_only_source(env, client):
    """Every sync kind builds through sync.synced_source: POST /api/jobs
    is refused too, before any tool is looked for, and nothing is queued."""
    x = add(client, "https://x.com/someone", options={"download": "off"})["source"]
    tt = add(client, "https://www.tiktok.com/@someone", options={"download": "off"})["source"]
    insta = add(client, "someone", tool="instaloader", options={"download": "off"})["source"]
    none = add(client, "https://twitch.tv/someone", tool="none")["source"]
    for kind, s in (("instaloader-sync", insta), ("gallery-dl-sync", x), ("yt-dlp-sync", tt),
                    ("gallery-dl-sync", none)):
        r = post(client, "/api/jobs", {"kind": kind, "params": {"source": str(s["id"])}}, 400)
        assert "profile only" in r["error"] or "no downloader" in r["error"], kind
    assert jobs.active() == [] and tree(env["media"]) == []
    with pytest.raises(jobs.BadRequest, match="profile only"):
        sync.synced_source({"source": str(x["id"])})
    # A source's own script: its build reads the source the same way.
    import scripts
    with pytest.raises(jobs.BadRequest, match="profile only"):
        scripts._source({"source": str(insta["id"]), "target": insta["target"]})


def test_a_source_syncing_now_keeps_its_download(env, client, monkeypatch):
    s = add(client, "https://x.com/someone")["source"]
    monkeypatch.setattr(sync, "active", lambda: {s["id"]: {"id": 9, "state": "running", "waits_until": None}})
    r = post(client, f"/api/sources/{s['id']}", {"options": {"download": "off"}}, 409)
    assert "queued or running" in r["error"]


# ---------------------------------------------------------------------------
# No downloader: the tool "none"
# ---------------------------------------------------------------------------

def test_a_profile_of_a_site_with_no_downloader(env, client, submitted, schedule):
    pid = post(client, "/api/people", {"name": "Alice"})["person"]["id"]
    # Not in the routing table: refused as a source, offered as a profile.
    r = get(client, "/api/sources/resolve?url=https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb")
    assert r["ok"] is False and "routing table" in r["error"]
    assert r["profile"] == {"tool": "none", "platform": "spotify",
                            "target": "https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb"}
    r = get(client, "/api/sources/resolve?tool=none&url=https://www.twitch.tv/someone/")
    assert (r["ok"], r["tool"], r["platform"], r["target"], r["folder"], r["profile_only"]) == (
        True, "none", "twitch", "https://twitch.tv/someone", None, True)
    add(client, "https://www.twitch.tv/someone", 400)
    s = add(client, "https://www.twitch.tv/someone/", tool="none", person=pid)["source"]
    assert (s["tool"], s["platform"], s["target"], s["url"], s["folder"]) == (
        "none", "twitch", "https://twitch.tv/someone", "https://twitch.tv/someone", "")
    assert (s["profile_only"], s["needs_tool"], s["person"]) == (True, False, {"id": pid, "name": "Alice"})
    # A normal row in sources: listed, by person, in the user data.
    assert [x["id"] for x in get(client, "/api/sources")["sources"]] == [s["id"]]
    assert sources.of_person(db.connect(), pid) == [s["id"]]
    assert [tuple(r) for r in db.connect().execute("SELECT tool, folder FROM sources")] == [("none", "")]
    # The same profile again, whatever the tool or spelling: refused.
    assert "already" in add(client, "twitch.tv/SomeOne", 400, tool="none")["error"]
    assert get(client, "/api/sources/resolve?tool=none&url=twitch.tv/someone")["source"] == s["id"]
    # Never synced, never a folder.
    assert post(client, f"/api/sources/{s['id']}/sync", status=409)["profile_only"] is True
    assert post(client, "/api/sources/sync-all")["profile_only"] == 1
    post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "hourly"}})
    assert schedule() == [] and submitted == [] and tree(env["media"]) == []
    # It cannot download until it has a tool: "on" says so; "platform" is kept.
    r = post(client, f"/api/sources/{s['id']}", {"options": {"download": "on"}}, 400)
    assert r["needs_tool"] is True and "pick a tool and a folder" in r["error"]
    got = post(client, f"/api/sources/{s['id']}", {"options": {"download": "platform"}})["source"]
    assert (got["profile_only"], got["needs_tool"]) == (True, True)
    post(client, "/api/config", {"profile_only_platforms": ["twitch"]})
    assert get(client, f"/api/sources/{s['id']}")["needs_tool"] is False
    # Bad ones.
    for body in [{"target": "https://twitch.tv/someone", "tool": "none", "folder": str(env["media"] / "t")},
                 {"target": "https://twitch.tv/", "tool": "none"}, {"target": "javascript:alert(1)", "tool": "none"},
                 {"target": "https://twitch.tv/other", "tool": "none", "options": {"download": "on"}}]:
        assert add(client, body.pop("target"), 400, **body)["ok"] is False
    # Adopting accounts after a scan never gives it one from everyone's posts.
    write_post(env["media"] / "carol.cooks", "CAROLPOST001", TS, owner("carol.cooks", 777))
    scanner.scan(env["roots"])
    assert get(client, f"/api/sources/{s['id']}")["account"] is None
    # Its empty folder takes no folder from the suggestions.
    assert [x["target"] for x in get(client, "/api/sources")["suggestions"]] == ["carol.cooks"]


def test_switching_a_profile_with_no_downloader_to_downloading(env, client, submitted):
    s = add(client, "https://x.com/Someone", tool="none")["source"]
    assert (s["tool"], s["platform"]) == ("none", "twitter")
    assert "already" in add(client, "https://x.com/someone", 400)["error"]
    # Download on without a folder: asked for one.
    assert post(client, f"/api/sources/{s['id']}", {"options": {"download": "on"}}, 400)["needs_tool"] is True
    # A folder that is not one: refused, nothing changes.
    for folder in ["/etc", str(env["media"]), 3]:
        post(client, f"/api/sources/{s['id']}", {"options": {"download": "on"}, "folder": folder}, 400)
    # Download must be on (or follow a platform that downloads).
    post(client, "/api/config", {"profile_only_platforms": ["twitter"]})
    r = post(client, f"/api/sources/{s['id']}", {"options": {}, "folder": None}, 400)
    assert "send download on" in r["error"]
    assert get(client, f"/api/sources/{s['id']}")["tool"] == "none"
    got = post(client, f"/api/sources/{s['id']}", {"options": {"download": "on", "media": "images"},
                                                   "folder": None})["source"]
    assert (got["tool"], got["target"], got["folder"]) == (
        "gallery-dl", "https://x.com/Someone", str(env["media"] / "twitter" / "someone"))
    assert (got["profile_only"], got["options"]["download"], got["options"]["media"]) == (False, "on", "images")
    assert not os.path.exists(got["folder"])
    # Given once: a source with a tool is not given another.
    post(client, f"/api/sources/{got['id']}", {"options": {}, "folder": None}, 400)
    post(client, f"/api/sources/{got['id']}/sync")
    assert submitted == [got["id"]]


def test_an_instagram_profile_with_no_downloader_becomes_an_instaloader_source(env, client):
    s = add(client, "https://www.instagram.com/some.body/", tool="none")["source"]
    assert (s["tool"], s["platform"], s["target"]) == ("none", "instagram", "https://instagram.com/some.body")
    # The same profile as instaloader's: one source.
    assert "already" in add(client, "some.body", 400, tool="instaloader")["error"]
    assert get(client, "/api/sources/resolve?tool=instaloader&url=some.body")["source"] == s["id"]
    folder = str(env["media"] / "elsewhere")
    got = post(client, f"/api/sources/{s['id']}", {"options": {"download": "on"}, "folder": folder})["source"]
    assert (got["tool"], got["target"], got["folder"]) == ("instaloader", "some.body", folder)


def test_a_site_still_not_routed_cannot_be_given_a_downloader(env, client):
    s = add(client, "https://twitch.tv/someone", tool="none")["source"]
    r = post(client, f"/api/sources/{s['id']}", {"options": {"download": "on"}, "folder": None}, 400)
    assert "no downloader takes this link" in r["error"]
    routes = {**get(client, "/api/config")["routes"], "twitch.tv": "yt-dlp"}
    post(client, "/api/config", {"routes": routes})
    got = post(client, f"/api/sources/{s['id']}", {"options": {"download": "on"}, "folder": None})["source"]
    assert (got["tool"], got["folder"]) == ("yt-dlp", str(env["media"] / "twitch" / "someone"))


def test_a_profile_only_source_is_user_data(env, client):
    pid = post(client, "/api/people", {"name": "Alice"})["person"]["id"]
    add(client, "https://twitch.tv/someone", tool="none", person=pid)
    add(client, "https://x.com/someone", person=pid, options={"download": "off"})
    userdata.flush()
    path = os.path.join(config.load()["data_directory"], "userdata", "sources.json")
    with open(path) as f:
        rows = {r["tool"]: r for r in json.load(f)["rows"]}
    assert (rows["none"]["folder"], rows["none"]["person"]) == ("", "Alice")
    assert json.loads(rows["gallery-dl"]["options"])["download"] == "off"


# ---------------------------------------------------------------------------
# Link-in-bio suggestions
# ---------------------------------------------------------------------------

LINKS = ["https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb", "https://www.twitch.tv/alice_live",
         "https://x.com/alice_x", "https://www.tiktok.com/@alice.tt"]


def test_bio_suggestions_on_profile_only_platforms(env, client):
    conn, table = db.connect(), sources.routes(config.load())
    pid = post(client, "/api/people", {"name": "Alice"})["person"]["id"]
    # Nothing listed: only what the routing table takes, each downloading.
    got = biofetch.suggest(conn, pid, LINKS, table, env["roots"])
    assert [(a["platform"], a["tool"], a["profile_only"]) for a in got] == [
        ("twitter", "gallery-dl", False), ("tiktok", "yt-dlp", False)]
    # Listed: their profiles whatever the table says, profile only.
    got = biofetch.suggest(conn, pid, LINKS, table, env["roots"], ["spotify", "twitch", "twitter"])
    assert [(a["platform"], a["handle"], a["tool"], a["status"], a["profile_only"]) for a in got] == [
        ("spotify", "4Z8W4fKeB5YxbusRsdQVPb", "none", "new", True), ("twitch", "alice_live", "none", "new", True),
        ("twitter", "alice_x", "gallery-dl", "new", True), ("tiktok", "alice.tt", "yt-dlp", "new", False)]
    # Added as the dashboard adds them (Add profile): linked to her the next time.
    post(client, "/api/config", {"profile_only_platforms": ["spotify", "twitch", "twitter"]})
    for a in got[:3]:
        body = {"target": a["url"], "person": pid, **({"tool": "none"} if a["tool"] == "none" else {})}
        s = add(client, body.pop("target"), **body)["source"]
        assert s["profile_only"] is True and s["person"]["id"] == pid
    got = biofetch.suggest(conn, pid, LINKS, table, env["roots"], ["spotify", "twitch", "twitter"])
    assert [a["status"] for a in got] == ["linked", "linked", "linked", "new"]
    assert tree(env["media"]) == []


# ---------------------------------------------------------------------------
# Review of #224
# ---------------------------------------------------------------------------

def test_a_reset_keeps_a_source_profile_only(env, client, submitted, schedule):
    """Download is never reset: Reset all (true) leaves "off" as it is, and
    naming it is refused, so a source set never to download stays so."""
    s = add(client, "https://x.com/someone", options={"download": "off", "media": "images"})["source"]
    got = post(client, f"/api/sources/{s['id']}", {"reset": True})["source"]
    assert (got["profile_only"], got["options"]["download"], got["overrides"]) == (True, "off", ["download"])
    r = post(client, f"/api/sources/{s['id']}", {"reset": ["download"]}, 400)
    assert "reset must be" in r["error"] and "download" not in r["error"].split(":", 1)[1]
    assert get(client, f"/api/sources/{s['id']}")["profile_only"] is True
    post(client, f"/api/sources/{s['id']}/sync", status=409)
    assert schedule() == [] and submitted == [] and tree(env["media"]) == []


def test_giving_a_tool_keeps_what_the_profile_set(env, client):
    """The options a tool-none source set itself (a schedule) stay set once
    it is given a downloader, besides those sent."""
    s = add(client, "https://x.com/someone", tool="none")["source"]
    s = post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "weekly"}})["source"]
    assert s["options"]["schedule"] == "weekly" and "schedule" in s["overrides"]
    got = post(client, f"/api/sources/{s['id']}", {"options": {"download": "on", "media": "images"},
                                                   "folder": None})["source"]
    assert got["tool"] == "gallery-dl"
    assert (got["options"]["schedule"], got["options"]["media"], got["options"]["download"]) == (
        "weekly", "images", "on")
    assert {"schedule", "media", "download"} <= set(got["overrides"])


def test_listing_a_platform_waits_for_its_syncs(env, client, monkeypatch):
    """A platform made profile only while one of its sources syncs: 409, as
    a source's own download or Don't sync; nothing is saved. Another
    platform's sync does not hold it up, nor one profile only already."""
    x = add(client, "https://x.com/someone")["source"]
    tt = add(client, "https://www.tiktok.com/@someone")["source"]
    running = {x["id"]: {"id": 9, "state": "running", "waits_until": None}}
    monkeypatch.setattr(sync, "active", lambda: running)
    r = post(client, "/api/config", {"profile_only_platforms": ["twitter"]}, 409)
    assert "queued or running" in r["error"]
    assert get(client, "/api/config")["profile_only_platforms"] == []
    assert get(client, f"/api/sources/{x['id']}")["profile_only"] is False
    assert post(client, "/api/config", {"profile_only_platforms": ["tiktok"]})["ok"] is True
    assert get(client, f"/api/sources/{tt['id']}")["profile_only"] is True
    post(client, "/api/config", {"profile_only_platforms": []})
    monkeypatch.setattr(sync, "active", lambda: {})
    post(client, f"/api/sources/{x['id']}", {"options": {"download": "off"}})
    monkeypatch.setattr(sync, "active", lambda: running)
    assert post(client, "/api/config", {"profile_only_platforms": ["twitter"]})["ok"] is True


def test_one_profile_whatever_its_host(env, client):
    """x.com and twitter.com links to one profile are one source, a
    tool-none one and a downloading one alike, in any case."""
    s = add(client, "https://twitter.com/Someone", tool="none")["source"]
    assert "already" in add(client, "https://x.com/someone", 400)["error"]
    assert "already" in add(client, "https://x.com/SOMEONE", 400, tool="none")["error"]
    assert get(client, "/api/sources/resolve?url=https://x.com/someone")["source"] == s["id"]
    other = add(client, "https://x.com/someone_else")["source"]
    assert "already" in add(client, "https://twitter.com/someone_else", 400, tool="none")["error"]
    assert other["id"] != s["id"]


def test_no_folder_is_no_account(env, client):
    """A profile-only source's folder "" is no folder: it takes no account
    from the posts ("/" would be every post's)."""
    write_post(env["media"] / "carol.cooks", "CAROLPOST001", TS, owner("carol.cooks", 777))
    scanner.scan(env["roots"])
    conn = db.connect()
    assert sources._folder_account(conn, "instagram", "", env["roots"]) is None
    assert sources._folder_account(conn, "instagram", str(env["media"] / "carol.cooks"), env["roots"]) is not None
