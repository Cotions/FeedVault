import json
import os
import shutil

from fakes import owner, write_post

import db
import scanner
import trash

ALICE = owner("alice.example", 111, "Alice Example")
TS = 1717243200


def run_scan(env):
    return scanner.scan(env["roots"])


def copies(conn=None):
    conn = conn or db.connect()
    return [{**dict(r), "media": json.loads(r["media"])}
            for r in conn.execute("SELECT * FROM copies ORDER BY meta_path")]


def two_folders(env, kind="carousel", **kw):
    """The same post downloaded into alice/ and a typo'd alicee/."""
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, kind, **kw)
    shutil.copytree(env["media"] / "alice", env["media"] / "alicee")
    return a, str(env["media"] / "alicee" / os.path.basename(a))


# ---------------------------------------------------------------------------
# Step 1: copies are recorded by the scanner
# ---------------------------------------------------------------------------

def test_copy_is_recorded_with_its_files(env):
    _, copy_base = two_folders(env, slides=[False, True])
    r = run_scan(env)
    assert (r["added"], r["unmatched"]) == (1, 1)
    [c] = copies()
    assert c["post_id"] == "instagram:P1" and c["meta_path"] == copy_base + ".json"
    assert [(m["idx"], m["kind"]) for m in c["media"]] == [(1, "image"), (2, "video")]
    assert c["media"][1]["poster_path"] == copy_base + "_2.jpg"
    assert all(m["size"] == os.path.getsize(m["path"]) for m in c["media"])
    # the Unmatched line stays, meaning the same as before
    [u] = db.unmatched(db.connect())
    assert u["path"] == c["meta_path"] and u["reason"].startswith("duplicate of instagram:P1")


def test_copy_rows_are_stable_and_pruned(env):
    _, copy_base = two_folders(env, "image")
    run_scan(env)
    [first] = copies()
    run_scan(env)
    [again] = copies()
    assert (again["id"], again["first_seen"]) == (first["id"], first["first_seen"])
    for f in os.listdir(env["media"] / "alicee"):
        os.remove(env["media"] / "alicee" / f)
    run_scan(env)
    assert copies() == [] and db.unmatched(db.connect()) == []


def test_copy_media_list_follows_disk(env):
    _, copy_base = two_folders(env, slides=[False, False, False])
    run_scan(env)
    os.remove(copy_base + "_3.jpg")
    run_scan(env)
    assert [m["idx"] for m in copies()[0]["media"]] == [1, 2]


def test_copy_becomes_the_post_when_the_first_is_gone(env):
    a, copy_base = two_folders(env, "image")
    run_scan(env)
    shutil.rmtree(env["media"] / "alice")
    r = run_scan(env)
    assert r["updated"] == 1 and copies() == []
    row = db.connect().execute("SELECT meta_path FROM posts WHERE id = 'instagram:P1'").fetchone()
    assert row["meta_path"] == copy_base + ".json"


def test_restore_keeps_duplicate_lines_and_records_the_copy(env, client):
    """index_dirs (after a restore) used to wipe the folder's "duplicate of"
    lines until the next full scan, and never recorded copies."""
    from conftest import H
    _, copy_base = two_folders(env, "image")
    write_post(env["media"] / "alicee", "P2", TS + 60, ALICE, "image")
    run_scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P2"]}, headers=H).get_json()
    assert r["posts"] == ["instagram:P2"]
    assert [u["path"] for u in db.unmatched(db.connect())] == [copy_base + ".json"]
    assert len(copies()) == 1


def test_index_dirs_promotes_a_copy(env):
    a, copy_base = two_folders(env, "image")
    run_scan(env)
    conn = db.connect()
    trash.delete(["instagram:P1"], [], env["roots"], str(env["tmp"] / "data"))
    scanner.index_dirs(env["roots"], [str(env["media"] / "alicee")])
    row = conn.execute("SELECT meta_path FROM posts WHERE id = 'instagram:P1'").fetchone()
    assert row["meta_path"] == copy_base + ".json" and copies() == []
