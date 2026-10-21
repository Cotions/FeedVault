"""The trash's History (trash_history, trash.history, GET /api/trash/history):
what was deleted from where, restored by what, deleted for good, and where
each post is now. Also the manifest cache a restore leaves (trash._write_manifest)."""
import json
import os
import threading

import pytest

from conftest import H
from fakes import owner, write_post

import config
import db
import scanner
import trash
import trash_history

ALICE = owner("alice.example", 111, "Alice Example")


def scan(env):
    return scanner.scan(env["roots"])


def posts(env, *codes, kind="image", at=1717243200, **kw):
    for i, code in enumerate(codes):
        write_post(env["media"] / "alice", code, at + i, ALICE, kind, **kw)
    scan(env)


def history(client, query=""):
    r = client.get(f"/api/trash/history{query}", headers=H)
    assert r.status_code == 200
    return r.get_json()


def events(client, query=""):
    return history(client, query)["events"]


def delete(client, *pids, page=None):
    body = {"posts": list(pids)}
    if page is not None:
        body["from"] = page
    r = client.post("/api/delete", json=body, headers=H).get_json()
    assert r["posts"] == list(pids), r
    return r


def trash_entries(client):
    return client.get("/api/trash/items", headers=H).get_json()["entries"]


def log_path():
    return trash_history.path(config.load()["data_directory"])


def log_lines():
    with open(log_path()) as f:
        return [json.loads(line) for line in f]


# ---------------------------------------------------------------------------
# Events and their states
# ---------------------------------------------------------------------------

def test_a_delete_and_its_undo_are_logged_with_their_page(env, client):
    posts(env, "P1", "P2", kind="video")
    delete(client, "instagram:P1", page="review")
    [e] = events(client)
    [entry] = trash_entries(client)
    assert (e["action"], e["from"], e["state"]) == ("deleted", "review", "in_trash")
    assert (e["post"], e["platform"], e["post_id"]) == ("instagram:P1", "instagram", "P1")
    assert e["author"] == {"id": "111", "handle": "alice.example"} and e["kind"] == "video"
    assert e["restore_key"] == entry["key"] and e["files"] == entry["files"] and e["bytes"] == entry["bytes"]
    assert e["thumb_url"] == f"/trash/{entry['key']}/thumb" and e["open"] is False
    assert client.get(e["thumb_url"]).status_code == 200

    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"], "from": "undo"}, headers=H).get_json()
    assert r["ok"] and r["posts"] == ["instagram:P1"]
    back, deleted = events(client)                              # newest first
    assert (back["action"], back["from"], back["state"]) == ("restored", "undo", "restored")
    assert back["files"] == e["files"] and back["open"] is True and back["restore_key"] is None
    assert (deleted["state"], deleted["open"]) == ("restored", True)
    # Back in the index: its cover, never the trash's thumbnail.
    media = client.get("/api/posts/instagram/P1", headers=H).get_json()["media"]
    assert deleted["thumb_url"] == back["thumb_url"] == f"/media/{media[0]['id']}/thumb"
    assert back["at_ms"] > deleted["at_ms"] and back["at"] == back["at_ms"] // 1000


def test_each_page_is_its_own_from(env, client):
    posts(env, "P1", "P2", "P3", "P4")
    for pid, page in (("P1", "feed"), ("P2", "post"), ("P3", "storage"), ("P4", None)):
        delete(client, f"instagram:{pid}", page=page)
    assert [e["from"] for e in events(client)] == [None, "storage", "post", "feed"]
    keys = [e["key"] for e in trash_entries(client)]
    client.post("/api/trash/restore", json={"keys": keys[:1], "from": "trash"}, headers=H)
    assert events(client)[0]["from"] == "trash"


@pytest.mark.parametrize("page", ["evil", "", "REVIEW", "scan", 5, None, ["review"], {"x": 1}, True])
def test_a_from_that_is_no_page_is_null_never_refused(env, client, page):
    posts(env, "P1")
    r = client.post("/api/delete", json={"posts": ["instagram:P1"], "from": page}, headers=H)
    assert r.status_code == 200 and r.get_json()["posts"] == ["instagram:P1"]
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"], "from": page}, headers=H)
    assert r.status_code == 200 and r.get_json()["posts"] == ["instagram:P1"]
    assert [e["from"] for e in events(client)] == [None, None]   # "scan" is the backend's own


def test_one_item_of_a_post_then_restored_from_the_trash(env, client):
    posts(env, "C1", kind="carousel", slides=[False, False, False])
    media = client.get("/api/posts/instagram/C1", headers=H).get_json()["media"]
    client.post("/api/delete", json={"media": [media[1]["id"]], "from": "post"}, headers=H)
    [e] = events(client)
    assert (e["action"], e["from"], e["partial"], e["items"], e["of"]) == ("deleted", "post", True, 1, 3)
    assert e["state"] == "in_trash" and e["open"] is True        # the post itself never left
    [entry] = trash_entries(client)
    client.post("/api/trash/restore", json={"keys": [entry["key"]], "from": "trash"}, headers=H)
    back, e = events(client)
    assert (back["action"], back["from"], back["partial"]) == ("restored", "trash", True)
    assert e["state"] == back["state"] == "restored"


def test_purged_entries_are_gone(env, client):
    posts(env, "P1", "P2")
    delete(client, "instagram:P1", page="feed")
    delete(client, "instagram:P2", page="feed")
    p2 = next(e for e in trash_entries(client) if e["post"] == "instagram:P2")
    r = client.post("/api/trash/purge", json={"keys": [p2["key"]]}, headers=H).get_json()
    assert r["entries"] == 1
    purged, d2, d1 = events(client)
    assert (purged["action"], purged["from"], purged["post"], purged["state"]) == ("purged", None, "instagram:P2", "gone")
    assert purged["files"] == p2["files"] and purged["bytes"] == p2["bytes"] and purged["thumb_url"] is None
    assert (d2["state"], d2["restore_key"], d2["thumb_url"]) == ("gone", None, None)
    assert d1["state"] == "in_trash"


def test_a_big_purge_is_one_event(env, client, monkeypatch):
    monkeypatch.setattr(trash_history, "PURGE_EACH", 1)
    posts(env, "P1", "P2", "P3")
    for pid in ("P1", "P2", "P3"):
        delete(client, f"instagram:{pid}")
    keys = [e["key"] for e in trash_entries(client) if e["post"] != "instagram:P3"]
    r = client.post("/api/trash/purge", json={"keys": keys}, headers=H).get_json()
    big = events(client)[0]
    assert (big["action"], big["entries"], big["files"], big["bytes"]) == ("purged", 2, r["files"], r["bytes"])
    assert big["post"] is None and "keys" not in big           # kept in the log, not sent
    assert [e["state"] for e in events(client)[1:]] == ["in_trash", "gone", "gone"]


def test_empty_trash_is_one_event_that_counts(env, client):
    posts(env, "P1", "P2")
    delete(client, "instagram:P1", "instagram:P2", page="review")
    before = client.get("/api/trash", headers=H).get_json()
    assert client.post("/api/trash/empty", headers=H).get_json()["ok"]
    emptied, *deleted = events(client)
    assert (emptied["action"], emptied["entries"], emptied["files"]) == ("emptied", 2, before["files"])
    assert emptied["state"] == "gone" and [e["state"] for e in deleted] == ["gone", "gone"]
    assert client.post("/api/trash/empty", headers=H).get_json()["ok"]
    assert len(events(client)) == 3                              # an empty trash emptied: nothing to log


def test_a_post_deleted_again_restores_its_newest_deletion(env, client):
    posts(env, "P1")
    delete(client, "instagram:P1", page="review")
    client.post("/api/trash/restore", json={"posts": ["instagram:P1"], "from": "undo"}, headers=H)
    delete(client, "instagram:P1", page="feed")
    again, back, first = events(client)
    [entry] = trash_entries(client)
    assert again["state"] == back["state"] == first["state"] == "in_trash"
    assert again["restore_key"] == back["restore_key"] == first["restore_key"] == entry["key"]
    assert [e["open"] for e in (again, back, first)] == [False] * 3


def test_a_restore_cut_short_is_logged_from_scan(env, client):
    from test_trash import manifest, restore_killed
    posts(env, "P1")
    delete(client, "instagram:P1", page="review")
    restore_killed("instagram:P1", len(manifest(env)))          # every file back, then killed
    assert [e["action"] for e in events(client)] == ["deleted"]
    scan(env)
    back, e = events(client)
    assert (back["action"], back["from"], back["state"], e["state"]) == ("restored", "scan", "restored", "restored")


def test_duplicates_logs_itself(env, client):
    from test_duplicates import hashed_copies, listing
    hashed_copies(env, slides=[False, True])
    g = listing(client)["groups"][0]
    client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": g["suggested"], "from": "feed"}, headers=H)
    [e] = events(client)
    assert (e["action"], e["from"], e["copy"], e["state"], e["open"]) == ("deleted", "duplicates", True, "in_trash", True)


def test_a_sync_putting_a_post_back_in_the_trash_is_not_logged(env):
    posts(env, "P1")
    cfg = config.load()
    trash.delete(["instagram:P1"], [], cfg["media_roots"], cfg["data_directory"], history=False)
    assert trash_history.events(cfg["data_directory"]) == ([], frozenset())
    assert not os.path.exists(log_path())


# ---------------------------------------------------------------------------
# The route: paging, one post, the security rules
# ---------------------------------------------------------------------------

def test_paging_by_offset_and_upto(env, client):
    posts(env, "P1", "P2", "P3")
    for pid in ("P1", "P2", "P3"):
        delete(client, f"instagram:{pid}")
    first = history(client, "?limit=2")
    assert first["total"] == 3 and [e["post_id"] for e in first["events"]] == ["P3", "P2"]
    client.post("/api/trash/restore", json={"posts": ["instagram:P3"]}, headers=H)
    more = history(client, f"?offset=2&limit=2&upto={first['upto']}")
    assert more["total"] == 3 and [e["post_id"] for e in more["events"]] == ["P1"]   # the restore since: not there
    assert history(client)["total"] == 4
    assert [e["action"] for e in events(client, "?post=instagram:P3")] == ["restored", "deleted"]
    assert events(client, "?post=instagram:nope") == []
    assert history(client, "?limit=0&offset=-5")["events"][0]["post_id"] == "P3"   # clamped, never an error


def test_the_route_needs_the_header_and_sends_no_path(env, client):
    posts(env, "P1")
    delete(client, "instagram:P1", page="review")
    assert client.get("/api/trash/history").status_code == 403
    text = client.get("/api/trash/history", headers=H).get_data(as_text=True)
    assert str(env["media"]) not in text and str(env["tmp"]) not in text and ".feedvault-trash" not in text
    assert not any(c in line for line in open(log_path()) for c in (str(env["media"]), "alice/"))


def test_no_data_directory_no_history(env):
    assert trash.history(env["roots"], None) == {"total": 0, "upto": 0, "events": []}
    trash_history.add(None, [{"action": "deleted"}])             # nowhere to write: nothing, no error


# ---------------------------------------------------------------------------
# The log: kept whole, capped, read as written
# ---------------------------------------------------------------------------

def test_the_history_survives_rebuilding_the_index(env, client):
    posts(env, "P1", "P2")
    delete(client, "instagram:P1", page="review")
    delete(client, "instagram:P2", page="feed")
    client.post("/api/trash/restore", json={"posts": ["instagram:P2"], "from": "undo"}, headers=H)
    assert log_path().startswith(config.load()["data_directory"] + os.sep + "userdata" + os.sep)
    assert os.stat(log_path()).st_mode & 0o777 == 0o600
    db.init(str(env["tmp"] / "rebuilt.db"))
    scan(env)
    back, d2, d1 = events(client)
    assert (back["action"], back["state"], d2["state"], d1["state"]) == ("restored", "restored", "restored", "in_trash")
    assert d1["from"] == "review" and d2["from"] == "feed"


def test_the_log_keeps_the_last_events(env, monkeypatch):
    monkeypatch.setattr(trash_history, "MAX_EVENTS", 5)
    monkeypatch.setattr(trash_history, "SLACK", 2)
    data = config.load()["data_directory"]
    for i in range(7):
        trash_history.add(data, [{"action": "emptied", "entries": i}])
    assert len(log_lines()) == 7                                  # within the slack: only appended
    trash_history.add(data, [{"action": "emptied", "entries": 7}])
    assert [e["entries"] for e in log_lines()] == [3, 4, 5, 6, 7]
    assert [e["entries"] for e in trash_history.events(data)[0]] == [3, 4, 5, 6, 7]


def test_the_log_keeps_the_last_days(env):
    data = config.load()["data_directory"]
    now = 1_800_000_000
    trash_history.add(data, [{"action": "emptied", "entries": 1}], now=now - 100 * 86400)
    trash_history.add(data, [{"action": "emptied", "entries": 2}], now=now - 10 * 86400)
    trash_history.add(data, [{"action": "emptied", "entries": 3}], now=now)
    assert [e["entries"] for e in log_lines()] == [2, 3]
    assert [e["entries"] for e in trash_history.events(data)[0]] == [2, 3]


def test_stamps_always_rise(env):
    data = config.load()["data_directory"]
    trash_history.add(data, [{"action": "emptied"}] * 3, now=1_800_000_000)
    trash_history.add(data, [{"action": "emptied"}], now=1_700_000_000)   # the clock went back
    stamps = [e["at_ms"] for e in trash_history.events(data)[0]]
    assert stamps == sorted(set(stamps)) and len(stamps) == 4


def test_a_line_edited_by_hand_is_skipped_or_cleaned(env, client):
    posts(env, "P1")
    delete(client, "instagram:P1", page="review")
    with open(log_path(), "a") as f:
        f.write("not json\n[1, 2]\n")
        f.write(json.dumps({"action": "nope", "at_ms": 5}) + "\n")
        f.write(json.dumps({"action": "deleted", "at_ms": 10**13, "from": "/etc/passwd", "key": "../../etc/passwd/x",
                            "author": "x", "files": -1, "keys": ["a" * 20, "/x"]}) + "\n")
        f.write('{"action": "deleted", "at_ms": ')               # half written: read once whole
    odd, e = events(client)
    assert (odd["from"], odd["author"], odd["files"]) == (None, None, 0) and odd["state"] == "gone"
    assert e["from"] == "review" and e["state"] == "in_trash"
    assert trash_history.events(config.load()["data_directory"])[0][-1]["keys"] == ["a" * 20]


def test_concurrent_deletes_and_restores_keep_the_log_whole(env, client):
    codes = [f"P{i}" for i in range(8)]
    posts(env, *codes)
    cfg = config.load()
    roots, data = cfg["media_roots"], cfg["data_directory"]
    errors = []

    def work(pid):
        try:
            for _ in range(3):
                assert trash.delete([pid], [], roots, data, source="feed")["posts"] == [pid]
                assert trash.restore([pid], roots, data, source="undo")["posts"] == [pid]
        except Exception as e:                  # noqa: BLE001 (reported below)
            errors.append(e)

    threads = [threading.Thread(target=work, args=(f"instagram:{c}",)) for c in codes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    lines = log_lines()
    assert len(lines) == len(codes) * 6
    stamps = [e["at_ms"] for e in lines]
    assert stamps == sorted(set(stamps))
    for c in codes:
        mine = [e["action"] for e in lines if e["post"] == f"instagram:{c}"]
        assert mine == ["deleted", "restored"] * 3
    assert all(e["state"] == "restored" for e in events(client, "?limit=200"))


# ---------------------------------------------------------------------------
# Restore: the lock, the manifest cache it leaves
# ---------------------------------------------------------------------------

def test_a_restore_waiting_on_a_scan_gives_up_with_a_message(env, client, monkeypatch):
    posts(env, "P1")
    delete(client, "instagram:P1")
    with monkeypatch.context() as m:            # never undo(): that would undo env's config too
        m.setattr(db.write_lock, "acquire", lambda *a, **k: False)
        r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"], "from": "trash"}, headers=H).get_json()
    assert r["ok"] is False and r["error"] == "a scan is running; try again in a moment" and r["posts"] == []
    assert [e["action"] for e in events(client)] == ["deleted"]
    assert len(trash_entries(client)) == 1


def _fresh(root):
    lines, entries, _ = trash._load(root)
    trash._cache.clear()
    again, fresh, _ = trash._load(root)
    assert lines == again

    def shape(gs):
        return sorted((g["key"], g["seq"], g["at"], g["stamp"], g["paths"], g["recorded"], tuple(g["roles"]),
                       json.dumps(g["public"], sort_keys=True)) for g in gs)
    assert shape(entries) == shape(fresh)


def test_the_manifest_cache_a_rewrite_leaves_is_what_reading_it_gives(env, client):
    posts(env, "P1", "P2", "P3", "P4")
    posts(env, "C1", kind="carousel", slides=[False, True, False], at=1717250000)
    for pid in ("P1", "P2", "P3", "P4"):
        delete(client, f"instagram:{pid}")
    media = client.get("/api/posts/instagram/C1", headers=H).get_json()["media"]
    client.post("/api/delete", json={"media": [media[0]["id"]]}, headers=H)
    root = env["roots"][0]
    trash._load(root)
    client.post("/api/trash/restore", json={"posts": ["instagram:P2"]}, headers=H)
    _fresh(root)
    key = next(e["key"] for e in trash_entries(client) if e["post"] == "instagram:P3")
    client.post("/api/trash/purge", json={"keys": [key]}, headers=H)
    _fresh(root)
    client.post("/api/trash/restore", json={"keys": [e["key"] for e in trash_entries(client)
                                                     if e["post"] == "instagram:C1"]}, headers=H)
    _fresh(root)
    delete(client, "instagram:P2")                              # appended after a rewrite: read on from there
    _fresh(root)
    assert sorted(e["post"] for e in trash_entries(client)) == ["instagram:P1", "instagram:P2", "instagram:P4"]
