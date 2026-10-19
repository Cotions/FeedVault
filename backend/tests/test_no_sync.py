"""Don't sync (#189, nosync.py): a person or an account FeedVault never
downloads from. No source suggestion, no new source, no Sync, no Sync all,
no schedule; a source it has already is kept; the library is unchanged.
No tool runs here: jobs.submit is a recorder."""
import json
import os

import pytest

from conftest import H
from fakes import owner, write_post

import app as app_module
import config
import db
import jobs
import scanner
import scheduler
import sync
import userdata

TS = 1717243200
CAROL = {"platform": "instagram", "id": "777"}
DANA = {"platform": "instagram", "id": "888"}
INSTAGRAM = {**H, "Origin": "https://www.instagram.com"}


def get(client, url, status=200, headers=H):
    r = client.get(url, headers=headers)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def post(client, url, body=None, status=200, headers=H):
    r = client.post(url, json={} if body is None else body, headers=headers)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def switch(client, body, status=200, headers=H):
    return post(client, "/api/no-sync", body, status, headers)


@pytest.fixture
def library(env):
    """Two instaloader profile folders, carol.cooks (777) and dana.draws (888)."""
    for handle, uid in (("carol.cooks", 777), ("dana.draws", 888)):
        for i in range(2):
            write_post(env["media"] / handle, f"{handle[:4].upper()}POST{i:04d}", TS + i, owner(handle, uid))
    scanner.scan(env["roots"])
    return env


@pytest.fixture
def submitted(monkeypatch):
    """jobs.submit recording what it was given: nothing runs."""
    calls = []

    def fake(kind, params):
        calls.append(int(params["source"]))
        return {"id": len(calls), "kind": kind, "params": params, "created_at": 1, "state": "queued"}
    monkeypatch.setattr(jobs, "submit", fake)
    monkeypatch.setattr(jobs, "get", lambda job_id: None)
    monkeypatch.setattr(sync, "_batch", None)
    return calls


def suggested(client):
    return sorted(s["target"] for s in get(client, "/api/sources")["suggestions"])


def add_suggested(client, target, status=200):
    s = next(x for x in get(client, "/api/sources")["suggestions"] if x["target"] == target)
    return post(client, "/api/sources", {k: s[k] for k in ("tool", "target", "folder", "account")}, status)


def account(client, key):
    return next(a for a in get(client, "/api/authors") if a["id"] == key["id"])


def test_off_by_default(library, client):
    assert get(client, "/api/no-sync") == {"people": [], "accounts": []}
    assert {a["id"]: a["no_sync"] for a in get(client, "/api/authors")} == {"777": False, "888": False}
    pid = post(client, "/api/people", {"name": "Carol", "accounts": [CAROL]})["person"]["id"]
    assert get(client, f"/api/people/{pid}")["no_sync"] is False
    assert suggested(client) == ["carol.cooks", "dana.draws"]
    s = add_suggested(client, "carol.cooks")["source"]
    assert s["no_sync"] is False


def test_an_account_gets_no_suggestion_and_no_new_source(library, client):
    r = switch(client, {"account": DANA, "no_sync": True})
    assert r["no_sync"] == {"people": [], "accounts": [DANA]}
    assert account(client, DANA)["no_sync"] is True and account(client, CAROL)["no_sync"] is False
    assert suggested(client) == ["carol.cooks"]
    # Not by its folder, nor by its handle, from the dashboard.
    folder = str(library["media"] / "dana.draws")
    for body in ({"tool": "instaloader", "target": "dana.draws", "folder": folder},
                 {"tool": "instaloader", "target": "dana.draws"},
                 {"tool": "instaloader", "target": "dana.draws", "account": DANA,
                  "folder": str(library["media"] / "elsewhere")}):
        r = post(client, "/api/sources", body, 409)
        assert r["no_sync"] is True and "@dana.draws is set to Don't sync" in r["error"]
    assert get(client, "/api/sources")["sources"] == []
    assert get(client, "/api/sources/resolve?tool=instaloader&url=dana.draws")["no_sync"] is True
    assert get(client, "/api/sources/resolve?tool=instaloader&url=carol.cooks")["no_sync"] is False
    # Off again: as before.
    switch(client, {"account": DANA, "no_sync": False})
    assert suggested(client) == ["carol.cooks", "dana.draws"]
    assert add_suggested(client, "dana.draws")["source"]["no_sync"] is False


def test_a_person_covers_each_of_their_accounts(library, client):
    pid = post(client, "/api/people", {"name": "Carol", "accounts": [CAROL]})["person"]["id"]
    switch(client, {"person": pid, "no_sync": True})
    assert get(client, f"/api/people/{pid}")["no_sync"] is True
    assert next(p for p in get(client, "/api/people") if p["id"] == pid)["no_sync"] is True
    # The account's own switch stays off: its person's covers it.
    assert account(client, CAROL)["no_sync"] is False
    assert suggested(client) == ["dana.draws"]
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "carol.cooks"}, 409)
    assert "Carol is set to Don't sync" in r["error"]
    # Nor a source added to the person, for a profile not downloaded yet.
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "someone.new", "person": pid}, 409)
    assert r["no_sync"] is True


def test_an_existing_source_is_kept_and_never_runs(library, client, submitted, monkeypatch):
    s = add_suggested(client, "carol.cooks")["source"]
    other = add_suggested(client, "dana.draws")["source"]
    post(client, f"/api/sources/{s['id']}", {"options": {"schedule": "hourly"}})
    # Asked first: it has a source that would stop syncing.
    r = switch(client, {"account": CAROL, "no_sync": True}, 409)
    assert (r["confirm"], r["sources"]) == (True, 1) and "kept, not deleted" in r["error"]
    assert get(client, "/api/no-sync")["accounts"] == []
    switch(client, {"account": CAROL, "no_sync": True, "confirm": True})
    got = get(client, f"/api/sources/{s['id']}")
    assert got["no_sync"] is True and got["schedule"]["every"] == "hourly" and got["schedule"]["next_at"] is None
    assert [x["id"] for x in get(client, "/api/sources")["sources"]] == [s["id"], other["id"]]
    # Sync: refused, with why.
    r = post(client, f"/api/sources/{s['id']}/sync", status=409)
    assert r["no_sync"] is True and "@carol.cooks is set to Don't sync" in r["error"]
    # Sync all: left out, counted.
    r = post(client, "/api/sources/sync-all")
    assert ([j["params"]["source"] for j in r["jobs"]], r["not_synced"], r["errors"]) == ([str(other["id"])], 1, [])
    # A person's Sync too.
    pid = post(client, "/api/people", {"name": "Carol", "accounts": [CAROL]})["person"]["id"]
    r = post(client, f"/api/people/{pid}/sync")
    assert (r["sources"], r["jobs"], r["not_synced"]) == (1, [], 1)
    # The scheduler passes it by.
    monkeypatch.setattr(jobs, "tool_path", lambda name: f"/fake/{name}")
    monkeypatch.setattr(scheduler, "_notes", {})
    monkeypatch.setattr(scheduler, "_held", {})
    monkeypatch.setattr(scheduler, "_last", {})
    before = list(submitted)
    assert scheduler.tick(TS) == [] and submitted == before
    # Already the switch: no question the second time, nothing changes.
    switch(client, {"account": CAROL, "no_sync": True})
    # Off: it syncs again, its schedule included.
    switch(client, {"account": CAROL, "no_sync": False})
    assert get(client, f"/api/sources/{s['id']}")["no_sync"] is False
    assert len(scheduler.tick(TS)) == 1 and submitted[-1] == s["id"]
    post(client, f"/api/sources/{s['id']}/sync")
    assert submitted[-1] == s["id"]


def test_a_source_syncing_now_is_not_switched_under_it(library, client, monkeypatch):
    s = add_suggested(client, "carol.cooks")["source"]
    monkeypatch.setattr(sync, "active", lambda: {s["id"]: {"id": 9, "state": "running", "waits_until": None}})
    r = switch(client, {"account": CAROL, "no_sync": True, "confirm": True}, 409)
    assert "queued or running" in r["error"]
    assert get(client, "/api/no-sync")["accounts"] == []


def test_the_library_is_unchanged(library, client):
    before = get(client, "/api/posts?author=777")["total"]
    stats = get(client, "/api/stats")
    switch(client, {"account": CAROL, "no_sync": True})
    assert get(client, "/api/posts?author=777")["total"] == before == 2
    assert get(client, "/api/stats") == stats


def test_bad_bodies(library, client):
    pid = post(client, "/api/people", {"name": "Carol", "accounts": [CAROL]})["person"]["id"]
    for bad in ({"person": pid}, {"person": pid, "no_sync": 1}, {"no_sync": True},
                {"person": pid, "account": DANA, "no_sync": True}, {"person": pid, "no_sync": True, "x": 1},
                {"person": pid, "no_sync": True, "confirm": "yes"}, [1]):
        switch(client, bad, 400)
    assert "no such person" in switch(client, {"person": 999, "no_sync": True}, 400)["error"]
    assert "indexed" in switch(client, {"account": {"platform": "instagram", "id": "404"}, "no_sync": True},
                               400)["error"]


def test_another_site_can_neither_read_nor_flip_it(library, client):
    switch(client, {"account": DANA, "no_sync": True, "confirm": True}, 403, INSTAGRAM)
    get(client, "/api/no-sync", 403, INSTAGRAM)
    assert ("set_no_sync", "POST") not in app_module.FOREIGN_ALLOWED
    assert get(client, "/api/no-sync")["accounts"] == []
    # Set from the dashboard: the userscript's add and sync are refused.
    s = add_suggested(client, "carol.cooks")["source"]
    switch(client, {"account": DANA, "no_sync": True})
    switch(client, {"account": CAROL, "no_sync": True, "confirm": True})
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "dana.draws"}, 409, INSTAGRAM)
    assert r["no_sync"] is True
    r = post(client, f"/api/sources/{s['id']}/sync", status=409, headers=INSTAGRAM)
    assert r["no_sync"] is True
    assert get(client, "/api/sources/resolve?tool=instaloader&url=dana.draws", headers=INSTAGRAM)["no_sync"] is True
    assert get(client, f"/api/sources/{s['id']}", headers=INSTAGRAM)["no_sync"] is True


def test_nothing_starts_syncing_when_a_person_goes(library, client):
    pid = post(client, "/api/people", {"name": "Carol", "accounts": [CAROL, DANA]})["person"]["id"]
    switch(client, {"person": pid, "no_sync": True})
    # Unlinked from them: the account keeps it.
    post(client, f"/api/people/{pid}/accounts", {"remove": [DANA]})
    assert account(client, DANA)["no_sync"] is True
    assert suggested(client) == []
    # Merged: the one kept has it.
    other = post(client, "/api/people", {"name": "Other"})["person"]["id"]
    merged = post(client, "/api/people/merge", {"ids": [other, pid]})["person"]
    assert merged["no_sync"] is True and get(client, "/api/no-sync")["people"] == [other]
    # Deleted: their accounts keep it.
    r = client.delete(f"/api/people/{other}", headers=H)
    assert r.status_code == 200
    assert get(client, "/api/no-sync") == {"people": [], "accounts": [CAROL, DANA]}
    assert suggested(client) == []


def test_it_is_user_data_and_survives_a_rebuild(library, client):
    pid = post(client, "/api/people", {"name": "Carol", "accounts": [CAROL]})["person"]["id"]
    switch(client, {"person": pid, "no_sync": True})
    switch(client, {"account": DANA, "no_sync": True})
    conn = db.connect()
    data_dir = config.load()["data_directory"]
    for name in ("people", "person_accounts", "no_sync_people", "no_sync_accounts"):
        userdata.export(conn, name, data_dir)
    with open(userdata.path(data_dir, "no_sync_people")) as f:
        assert [r["person"] for r in json.load(f)["rows"]] == ["Carol"]
    path = config.db_path(config.load())
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db.init(path)
    conn = db.connect()
    userdata.restore_all(conn, data_dir)
    scanner.scan(library["roots"])
    pid = get(client, "/api/people")[0]["id"]
    assert get(client, "/api/no-sync") == {"people": [pid], "accounts": [DANA]}
    assert suggested(client) == []
