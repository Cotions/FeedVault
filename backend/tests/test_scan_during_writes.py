"""#159: deletes and restores during a long scan.

The scan held db.write_lock for its whole run, so a Trash click during a big
rescan waited for it, and failed after 30 s. It now holds the lock folder by
folder; these check that a delete, a restore or a copy's delete made between
two folders goes through at once, that the index the scan leaves is the one
a scan after them would leave, and that a FeedVault killed mid-scan, after
such a delete, loses nothing.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time

from conftest import BACKEND, H
from fakes import owner, write_post

import db
import scanner
import trash

FOLDERS = ("a1", "a2", "a3", "a4")              # walked in this order


def vault(env):
    """Two posts in each folder, each folder its own account; indexed."""
    for n, folder in enumerate(FOLDERS):
        who = owner(f"{folder}.example", 100 + n)
        for i in (1, 2):
            write_post(env["media"] / folder, f"{folder.upper()}P{i}", 1717243200 + 10 * n + i, who, "image")
    scanner.scan(env["roots"])


def indexed():
    return sorted(r[0] for r in db.connect().execute("SELECT id FROM posts WHERE missing = 0"))


def everything(but=()):
    return sorted(f"instagram:{f.upper()}P{i}" for f in FOLDERS for i in (1, 2) if f"{f.upper()}P{i}" not in but)


def between(monkeypatch, start_in, check_in, work):
    """While the scan parses ``start_in``, run ``work`` in a thread (it
    waits for the lock); when it parses ``check_in``, note whether ``work``
    is done by then (waiting up to 5 s). Returns (results, the notes)."""
    parse_dir = scanner.parsers.parse_dir
    out, notes, done = {}, [], threading.Event()

    def run():
        try:
            out["result"] = work()
        finally:
            done.set()

    def parse(root, dirpath, names):
        name = os.path.basename(dirpath)
        if name == start_in and "thread" not in out:
            out["thread"] = threading.Thread(target=run)
            out["thread"].start()
            time.sleep(0.3)                     # it is waiting for the lock now
        if name == check_in:
            notes.append(done.wait(5))
        return parse_dir(root, dirpath, names)
    monkeypatch.setattr(scanner.parsers, "parse_dir", parse)
    return out, notes


def test_a_delete_during_a_scan_goes_between_two_folders(env, client, monkeypatch):
    vault(env)
    # A1P1 is in a folder the scan read already, A4P1 in one it has not.
    out, notes = between(monkeypatch, "a2", "a3", lambda: client.post(
        "/api/delete", json={"posts": ["instagram:A1P1", "instagram:A4P1"]}, headers=H).get_json())
    report = scanner.scan(env["roots"])
    out["thread"].join(30)
    assert notes == [True]                      # done before the scan read a3, not after it ended
    assert out["result"]["ok"] and sorted(out["result"]["posts"]) == ["instagram:A1P1", "instagram:A4P1"]
    assert (report["added"], report["missing"], report["unmatched"]) == (0, 0, 0)
    assert indexed() == everything(but=("A1P1", "A4P1"))
    assert db.connect().execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 6     # none kept as missing
    assert sorted(e["post"] for e in client.get("/api/trash/items", headers=H).get_json()["entries"]) == \
        ["instagram:A1P1", "instagram:A4P1"]
    again = scanner.scan(env["roots"])
    assert (again["added"], again["missing"]) == (0, 0)
    assert indexed() == everything(but=("A1P1", "A4P1"))


def test_a_restore_during_a_scan_is_not_marked_missing(env, client, monkeypatch):
    vault(env)
    client.post("/api/delete", json={"posts": ["instagram:A1P1"]}, headers=H)
    out, notes = between(monkeypatch, "a2", "a3", lambda: client.post(
        "/api/trash/restore", json={"posts": ["instagram:A1P1"]}, headers=H).get_json())
    report = scanner.scan(env["roots"])
    out["thread"].join(30)
    assert notes == [True]
    assert out["result"]["errors"] == [] and out["result"]["posts"] == ["instagram:A1P1"]
    # Back in a1, which the scan had read before: seen by the scan all the same.
    assert report["missing"] == 0
    assert indexed() == everything()
    assert client.get("/api/trash/items", headers=H).get_json()["entries"] == []


def test_a_copy_deleted_during_a_scan_is_not_recorded_again(env, client, monkeypatch):
    vault(env)
    who = owner("a1.example", 100)
    write_post(env["media"] / "a2", "A1P1", 1717243201, who, "image")     # a second copy of A1P1
    scanner.scan(env["roots"])
    conn = db.connect()
    [(cid, copy_meta)] = conn.execute("SELECT id, meta_path FROM copies").fetchall()
    assert os.path.dirname(copy_meta) == str(env["media"] / "a2")
    import config
    out, notes = between(monkeypatch, "a3", "a4", lambda: trash.delete(
        [], [], env["roots"], config.load()["data_directory"], copy_ids=[cid]))
    report = scanner.scan(env["roots"])
    out["thread"].join(30)
    assert notes == [True] and out["result"]["copies"] == [cid]
    assert conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0] == 0
    assert report["unmatched"] == 0
    assert conn.execute("SELECT COUNT(*) FROM unmatched").fetchone()[0] == 0
    assert indexed() == everything()


def test_hashing_still_steps_aside_for_the_whole_scan(env, monkeypatch):
    # The lock is let go between folders; the hashing worker waits for the scan all the same.
    vault(env)
    parse_dir = scanner.parsers.parse_dir
    seen = []

    def parse(root, dirpath, names):
        seen.append((db.write_lock.locked(), db.writes_busy()))
        return parse_dir(root, dirpath, names)
    monkeypatch.setattr(scanner.parsers, "parse_dir", parse)
    scanner.scan(env["roots"])
    assert seen and all(s == (True, True) for s in seen)
    assert not db.writes_busy()


SCAN_KILLED = r"""
import json, os, sys, threading, time
sys.path.insert(0, sys.argv[1])
import config, db, scanner, trash
cfg = config.load()
db.init(config.db_path(cfg))
done, started = threading.Event(), []

def delete():
    trash.delete(["instagram:A1P1"], [], cfg["media_roots"], cfg["data_directory"])
    done.set()

real = scanner.parsers.parse_dir

def parse(root, dirpath, names):
    name = os.path.basename(dirpath)
    if name == "a2" and not started:
        started.append(threading.Thread(target=delete, daemon=True))
        started[0].start()
        time.sleep(0.3)
    if name == "a3":
        with open(sys.argv[2], "w") as f:
            json.dump({"deleted": done.wait(10)}, f)
        time.sleep(600)                         # killed here, in the middle of the scan
    return real(root, dirpath, names)

scanner.parsers.parse_dir = parse
scanner.scan(cfg["media_roots"])
"""


def test_killed_mid_scan_after_a_delete_loses_nothing(env, client, tmp_path):
    vault(env)
    ready = tmp_path / "ready.json"
    errors = open(tmp_path / "child.err", "w")
    proc = subprocess.Popen([sys.executable, "-c", SCAN_KILLED, BACKEND, str(ready)], stderr=errors)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), open(tmp_path / "child.err").read()
        time.sleep(0.1)                         # the file written whole
    finally:
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(10)
        errors.close()
    assert json.loads(ready.read_text()) == {"deleted": True}
    conn = db.connect()
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert indexed() == everything(but=("A1P1",))
    report = scanner.scan(env["roots"])
    assert (report["added"], report["missing"]) == (0, 0)
    assert indexed() == everything(but=("A1P1",))
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE first_seen > 0").fetchone()[0] == 0
    [entry] = client.get("/api/trash/items", headers=H).get_json()["entries"]
    assert entry["post"] == "instagram:A1P1" and not entry["missing"]
    r = client.post("/api/trash/restore", json={"keys": [entry["key"]]}, headers=H).get_json()
    assert r["errors"] == [] and indexed() == everything()
