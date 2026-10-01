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


# ---------------------------------------------------------------------------
# Step 2: hashes and exact matching
# ---------------------------------------------------------------------------

import threading  # noqa: E402
import time  # noqa: E402

import duplicates  # noqa: E402
import hashing  # noqa: E402
import userdata  # noqa: E402

MIB = 1 << 20


def hashes():
    return {r["path"]: dict(r) for r in db.connect().execute("SELECT * FROM media_hash")}


def big(path, middle=b"m", size=3 * MIB):
    """A file over 2 MiB whose first and last MiB are fixed; ``middle``
    changes only what is in between."""
    with open(path, "wb") as f:
        f.write(b"h" * MIB + middle * (size - 2 * MIB) + b"t" * MIB)


def test_partial_and_full_hash(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    big(a, b"x")
    big(b, b"y")
    assert hashing.partial_hash(a, 3 * MIB) == hashing.partial_hash(b, 3 * MIB)
    assert hashing.full_hash(a, 3 * MIB) != hashing.full_hash(b, 3 * MIB)
    small = tmp_path / "s"
    small.write_bytes(b"abc")
    assert hashing.partial_hash(small, 3) == hashing.full_hash(small, 3)
    try:
        hashing.full_hash(small, 4)
        assert False, "a short read must not pass as a hash"
    except hashing.Changed:
        pass


def test_only_files_sharing_a_size_are_hashed(env):
    two_folders(env, slides=[False, True])
    write_post(env["media"] / "bob", "P2", TS + 60, ALICE, "image")
    with open(env["media"] / "bob" / os.listdir(env["media"] / "bob")[0], "ab"):
        pass
    run_scan(env)
    conn = db.connect()
    assert hashing.run_pass(conn)
    h = hashes()
    copy_paths = {m["path"] for m in copies()[0]["media"]}
    assert copy_paths <= set(h)
    # small files: the partial hash is the whole file, so it is the full one too
    assert all(r["full"] == r["partial"] for r in h.values())
    st = hashing.status()
    assert (st["running"], st["done"], st["errors"]) == (False, st["total"], []) and st["hashed"] == len(h)


def test_pass_is_resumable_and_follows_changes(env):
    _, copy_base = two_folders(env, "image")
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    before = hashes()
    stop = threading.Event()
    stop.set()
    assert hashing.run_pass(conn, restart=stop) is False       # interrupted: nothing lost
    assert hashes() == before
    # a file changed on disk is hashed again, a vanished one is dropped
    with open(copy_base + ".jpg", "ab") as f:
        f.write(b"x")
    t = time.time() + 5
    os.utime(copy_base + ".jpg", (t, t))
    run_scan(env)
    hashing.run_pass(conn)
    assert copy_base + ".jpg" not in hashes()                 # its size is unique now
    os.remove(copy_base + ".jpg")
    run_scan(env)
    hashing.run_pass(conn)
    assert len(hashes()) == 0 or all(os.path.exists(p) for p in hashes())


def test_worker_steps_aside_while_the_write_lock_is_held(env):
    two_folders(env, "image")
    run_scan(env)
    db.write_lock.acquire()
    try:
        t = threading.Thread(target=lambda: hashing.run_pass(db.connect()))
        t.start()
        for _ in range(50):
            if hashing.status()["paused"]:
                break
            time.sleep(0.05)
        assert hashing.status()["paused"] and hashes() == {}
    finally:
        db.write_lock.release()
    t.join(5)
    assert not t.is_alive() and len(hashes()) == 2


def test_copies_group_identical(env):
    two_folders(env, slides=[False, True])
    run_scan(env)
    conn = db.connect()
    [g] = duplicates.all_groups(conn, "copies")
    assert g["identical"] is None and g["pending"]              # not hashed yet
    hashing.run_pass(conn)
    [g] = duplicates.all_groups(conn, "copies")
    assert g["identical"] is True and g["differs"] == [] and not g["pending"]
    post, copy = g["members"]
    assert (post["type"], copy["type"]) == ("post", "copy")
    assert post["folder"].endswith("alice") and copy["folder"].endswith("alicee")
    assert g["suggested"] == "instagram:P1"                       # same everything: the shorter path
    assert g["frees"] == copy["bytes"] > 0


def test_copies_group_differs(env):
    _, copy_base = two_folders(env, slides=[False, False, False])
    os.remove(copy_base + "_3.jpg")
    with open(copy_base + "_2.jpg", "r+b") as f:                  # same size, other bytes
        f.seek(20)
        f.write(b"\xff\xfe")
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    [g] = duplicates.all_groups(conn, "copies")
    cid = g["members"][1]["id"]
    assert g["identical"] is False
    assert g["differs"] == [{"member": cid, "idx": 2, "reason": "content"},
                            {"member": cid, "idx": 3, "reason": "missing"}]
    assert g["suggested"] == "instagram:P1"                       # more media


def test_suggestion_prefers_kept_then_more_media_then_oldest(env):
    _, copy_base = two_folders(env, slides=[False, False])
    run_scan(env)
    conn = db.connect()
    db.set_decision(conn, ["instagram:P1"], "keep", 1)
    [g] = duplicates.all_groups(conn, "copies")
    assert all(m["kept"] for m in g["members"])                   # the decision is the post's
    m = [{"id": "a", "kept": False, "files": 2, "saved_at": 5, "meta_path": "/x/a"},
         {"id": "b", "kept": True, "files": 1, "saved_at": 9, "meta_path": "/x/bbbb"},
         {"id": "c", "kept": False, "files": 3, "saved_at": 9, "meta_path": "/x/c"}]
    assert duplicates.suggest(m)["id"] == "b"
    m[1]["kept"] = False
    assert duplicates.suggest(m)["id"] == "c"
    m[2]["files"] = 2
    assert duplicates.suggest(m)["id"] == "a"


def test_content_group_across_post_ids(env):
    """A repost: the same picture saved under another post id and creator."""
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    b = write_post(env["media"] / "bob", "R9", TS + 99, owner("bob", 222), "image")
    shutil.copyfile(a + ".jpg", b + ".jpg")
    write_post(env["media"] / "carol", "C3", TS + 999, owner("carol", 333), "carousel", slides=[False, False])
    shutil.copyfile(a + ".jpg", os.path.join(env["media"], "carol", os.path.basename(b) + "_x.jpg"))
    t = time.time() + 60                                          # saved a minute later
    os.utime(b + ".json", (t, t))
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    [g] = duplicates.all_groups(conn, "content")
    assert [m["id"] for m in g["members"]] == ["instagram:P1", "instagram:R9"]
    assert g["identical"] is True and g["suggested"] == "instagram:P1"   # older
    assert duplicates.all_groups(conn, "copies") == []


def test_content_full_hash_breaks_a_partial_tie(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "video")
    b = write_post(env["media"] / "bob", "R9", TS + 99, owner("bob", 222), "video")
    big(a + ".mp4", b"x")
    big(b + ".mp4", b"y")
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    h = hashes()
    assert h[a + ".mp4"]["partial"] == h[b + ".mp4"]["partial"]
    assert h[a + ".mp4"]["full"] != h[b + ".mp4"]["full"]
    assert duplicates.all_groups(conn, "content") == []


def test_content_group_that_differs(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    b = write_post(env["media"] / "bob", "C2", TS + 99, owner("bob", 222), "carousel", slides=[False, False])
    shutil.copyfile(a + ".jpg", b + "_2.jpg")
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    [g] = duplicates.all_groups(conn, "content")
    assert g["identical"] is False
    assert g["differs"] == [{"member": "instagram:C2", "idx": 1, "reason": "only here"}]
    assert g["suggested"] == "instagram:C2"                        # more media


def test_dismissals_are_user_data(env):
    two_folders(env, "image")
    run_scan(env)
    conn = db.connect()
    [g] = duplicates.all_groups(conn, "copies")
    assert "dismissed_duplicates" in userdata.REGISTRY
    conn.execute("INSERT INTO dismissed_duplicates(key, kind, at) VALUES (?, 'copies', 1)", (g["key"],))
    conn.commit()
    assert duplicates.all_groups(conn, "copies") == []
    data = str(env["tmp"] / "data")
    assert userdata.export(conn, "dismissed_duplicates", data) == 1
    conn.execute("DELETE FROM dismissed_duplicates")
    conn.commit()
    assert userdata.load(conn, "dismissed_duplicates", data) == 1
    assert duplicates.all_groups(conn, "copies") == []
    # the key names members by post id and metadata path, not by row ids
    assert json.loads(g["key"]) == sorted(["instagram:P1", g["members"][1]["meta_path"]])
