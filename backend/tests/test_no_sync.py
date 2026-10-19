"""Don't sync (#189, nosync.py): a person or an account FeedVault never
downloads from. No source suggestion, no new source, no Sync, no Sync all,
no schedule; a source it has already is kept; the library is unchanged.
No tool runs here: jobs.submit is a recorder."""
import json
import os
import threading

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


def test_no_route_queues_the_sync_of_one_not_synced(library, client):
    """Every sync kind builds through sync.synced_source: POST /api/jobs,
    the only route that takes a kind and a source id, is refused too, and
    nothing is queued (each build refuses before any tool is looked for)."""
    pid = post(client, "/api/people", {"name": "Carol", "accounts": [CAROL]})["person"]["id"]
    insta = add_suggested(client, "carol.cooks")["source"]
    x = post(client, "/api/sources", {"target": "https://x.com/carol_cooks", "person": pid})["source"]
    tt = post(client, "/api/sources", {"target": "https://www.tiktok.com/@carol.cooks", "person": pid})["source"]
    assert (x["tool"], tt["tool"]) == ("gallery-dl", "yt-dlp")
    switch(client, {"person": pid, "no_sync": True, "confirm": True})
    for kind, s in (("instaloader-sync", insta), ("gallery-dl-sync", x), ("yt-dlp-sync", tt)):
        r = post(client, "/api/jobs", {"kind": kind, "params": {"source": str(s["id"])}}, 400)
        assert "Carol is set to Don't sync" in r["error"], kind
    assert jobs.active() == []
    # A source's own script: its build reads the source the same way.
    import scripts
    with pytest.raises(jobs.BadRequest, match="Don't sync"):
        scripts._source({"source": str(insta["id"]), "target": insta["target"]})


def test_an_account_taken_from_a_person_set_to_it_keeps_it(library, client):
    """Linked to someone else (add, a new person, a merge's accounts), an
    account of a person set to Don't sync gets the switch of its own."""
    carol = post(client, "/api/people", {"name": "Carol", "accounts": [CAROL, DANA]})["person"]["id"]
    switch(client, {"person": carol, "no_sync": True})
    # Added to another person.
    other = post(client, "/api/people", {"name": "Other"})["person"]["id"]
    post(client, f"/api/people/{other}/accounts", {"add": [DANA]})
    assert get(client, "/api/no-sync")["accounts"] == [DANA]
    assert account(client, DANA)["person"]["id"] == other and suggested(client) == []
    # A new person made with it.
    made = post(client, "/api/people", {"name": "New", "accounts": [CAROL]})["person"]
    assert made["no_sync"] is False and get(client, "/api/no-sync")["accounts"] == [CAROL, DANA]
    assert suggested(client) == []
    # A merge's accounts: back to Carol, then taken by a merge.
    switch(client, {"account": CAROL, "no_sync": False})
    post(client, f"/api/people/{carol}/accounts", {"add": [CAROL]})
    assert get(client, "/api/no-sync")["accounts"] == [DANA]
    a = post(client, "/api/people", {"name": "A"})["person"]["id"]
    b = post(client, "/api/people", {"name": "B"})["person"]["id"]
    post(client, "/api/people/merge", {"ids": [a, b], "accounts": [CAROL]})
    assert get(client, "/api/no-sync")["accounts"] == [CAROL, DANA]
    # Written to userdata: the switch survives as the account's own.
    conn = db.connect()
    userdata.export(conn, "no_sync_accounts", config.load()["data_directory"])
    with open(userdata.path(config.load()["data_directory"], "no_sync_accounts")) as f:
        assert len(json.load(f)["rows"]) == 2


def test_a_filename_only_account_is_refused_too(env, client):
    """An account known from file names alone (its id is its folder's
    name): a profile of that name is refused before its key is let go."""
    from fakes import write_filename_post
    write_filename_post(env["media"] / "erin.paints", "erin.paints", "EEEEEEEEEE1", TS)
    scanner.scan(env["roots"])
    erin = {"platform": "instagram", "id": "erin.paints"}
    assert account(client, erin)["id"] == "erin.paints"
    switch(client, {"account": erin, "no_sync": True})
    r = post(client, "/api/sources", {"tool": "instaloader", "target": "erin.paints",
                                      "folder": str(env["media"] / "elsewhere")}, 409)
    assert r["no_sync"] is True
    assert get(client, "/api/sources")["sources"] == []


def test_a_source_before_its_first_sync_stands_for_its_folders_account(env, client, submitted):
    """No account yet (author NULL, no person): its folder's account's switch
    covers it, counted when asked and refused when synced."""
    folder = env["media"] / "fern.knits"
    os.makedirs(folder)
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "fern.knits", "folder": str(folder)})["source"]
    assert s["account"] is None and s["person"] is None
    write_post(folder, "FERNPOST0001", TS, owner("fern.knits", 999))
    scanner.scan(env["roots"])
    fern = {"platform": "instagram", "id": "999"}
    r = switch(client, {"account": fern, "no_sync": True}, 409)
    assert r["sources"] == 1
    switch(client, {"account": fern, "no_sync": True, "confirm": True})
    assert get(client, f"/api/sources/{s['id']}")["no_sync"] is True
    assert post(client, f"/api/sources/{s['id']}/sync", status=409)["no_sync"] is True
    assert post(client, "/api/sources/sync-all")["not_synced"] == 1 and submitted == []


def test_the_person_a_source_was_added_to_counts(library, client, submitted):
    """A source added to a person set to Don't sync is not synced even when
    its account is linked to someone else, who is not."""
    p = post(client, "/api/people", {"name": "P"})["person"]["id"]
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "carol.cooks", "person": p})["source"]
    q = post(client, "/api/people", {"name": "Q", "accounts": [CAROL]})["person"]["id"]
    assert get(client, f"/api/sources/{s['id']}")["person"]["id"] == q
    assert switch(client, {"person": p, "no_sync": True}, 409)["sources"] == 1
    switch(client, {"person": p, "no_sync": True, "confirm": True})
    assert get(client, f"/api/sources/{s['id']}")["no_sync"] is True
    r = post(client, f"/api/sources/{s['id']}/sync", status=409)
    assert "P is set to Don't sync" in r["error"]
    assert post(client, f"/api/people/{q}/sync")["not_synced"] == 1 and submitted == []


def test_merging_into_a_person_set_to_it_asks_first(library, client):
    q = post(client, "/api/people", {"name": "Q", "accounts": [CAROL]})["person"]["id"]
    s = add_suggested(client, "carol.cooks")["source"]
    p = post(client, "/api/people", {"name": "P"})["person"]["id"]
    switch(client, {"person": p, "no_sync": True})
    r = post(client, "/api/people/merge", {"ids": [q, p]}, 409)
    assert (r["confirm"], r["sources"]) == (True, 1)
    assert {x["id"] for x in get(client, "/api/people")} == {p, q}
    post(client, "/api/people/merge", {"ids": [q, p], "confirm": "yes"}, 400)
    merged = post(client, "/api/people/merge", {"ids": [q, p], "confirm": True})["person"]
    assert merged["no_sync"] is True and get(client, f"/api/sources/{s['id']}")["no_sync"] is True


def test_the_switch_is_set_while_no_sync_can_be_queued(library, client, monkeypatch):
    """The busy check and the switch under sync._submitting: no sync of
    theirs is queued in between."""
    import nosync
    held = []
    real = nosync.set_on
    monkeypatch.setattr(nosync, "set_on", lambda *a, **k: held.append(taken_elsewhere(sync._submitting)) or real(*a, **k))
    switch(client, {"account": CAROL, "no_sync": True})
    assert held == [True]


def taken_elsewhere(lock):
    """Whether another thread is kept out of ``lock`` now: it tries for it."""
    got = []

    def probe():
        ok = lock.acquire(timeout=0.2)
        got.append(ok)
        if ok:
            lock.release()
    t = threading.Thread(target=probe)
    t.start()
    t.join(5)
    return got == [False]


def test_a_sync_from_the_jobs_route_is_checked_and_queued_as_one_step(library, client):
    """POST /api/jobs builds a sync kind under sync._submitting, as Sync
    does: the switch set meanwhile (under the same lock) is seen by its
    check, and nothing is queued."""
    import nosync
    s = add_suggested(client, "carol.cooks")["source"]
    out = []
    body = {"kind": "instaloader-sync", "params": {"source": str(s["id"])}}
    with sync._submitting:
        t = threading.Thread(target=lambda: out.append(client.post("/api/jobs", json=body, headers=H)))
        t.start()
        t.join(0.3)
        assert t.is_alive() and out == []       # it waits for the lock
        nosync.set_on(db.connect(), True, account=(CAROL["platform"], CAROL["id"]))
    t.join(10)
    assert not t.is_alive()
    assert out[0].status_code == 400 and "Don't sync" in out[0].get_json()["error"]
    assert jobs.active() == []
    assert not taken_elsewhere(sync._submitting)


def test_a_source_whose_folder_is_still_empty_stands_for_its_handles_account(env, client, submitted):
    """Added before its account was known, into a folder still empty: the
    account with its handle (as create() would find it now) covers it."""
    s = post(client, "/api/sources", {"tool": "instaloader", "target": "fern.knits",
                                      "folder": str(env["media"] / "fern_new")})["source"]
    assert s["account"] is None
    write_post(env["media"] / "fern.knits", "FERNPOST0001", TS, owner("fern.knits", 999))
    scanner.scan(env["roots"])
    fern = {"platform": "instagram", "id": "999"}
    assert get(client, f"/api/sources/{s['id']}")["no_sync"] is False
    assert switch(client, {"account": fern, "no_sync": True}, 409)["sources"] == 1
    switch(client, {"account": fern, "no_sync": True, "confirm": True})
    assert get(client, f"/api/sources/{s['id']}")["no_sync"] is True
    assert post(client, f"/api/sources/{s['id']}/sync", status=409)["no_sync"] is True
    assert post(client, "/api/sources/sync-all")["not_synced"] == 1 and submitted == []
    # Every sync kind's build (POST /api/jobs's too; jobs.submit is a recorder here).
    with pytest.raises(jobs.BadRequest, match="@fern.knits is set to Don't sync"):
        sync.synced_source({"source": str(s["id"])})


def test_sources_before_their_first_sync_cost_one_lookup_each(env, client, monkeypatch):
    """Going through many (the listing, Sync all, the scheduler): the media
    roots read once, each source's folder looked up once."""
    import sources
    ids = [post(client, "/api/sources", {"tool": "instaloader", "target": f"new.{i}"})["source"]["id"]
           for i in range(4)]
    conn = db.connect()
    rows = [sources.row(conn, i) for i in ids]
    loads, lookups = [], []
    real_load, real_folder = config.load, sources._folder_account
    monkeypatch.setattr(sources.config, "load", lambda *a, **k: loads.append(1) or real_load(*a, **k))
    monkeypatch.setattr(sources, "_folder_account", lambda *a, **k: lookups.append(a[2]) or real_folder(*a, **k))
    cache = {}
    for _ in range(2):
        assert [sources.not_synced(conn, r, cache=cache) for r in rows] == [False] * 4
    assert len(loads) == 1 and len(lookups) == 4
    loads.clear(), lookups.clear()
    assert len(get(client, "/api/sources")["sources"]) == 4
    assert len(lookups) == 4
