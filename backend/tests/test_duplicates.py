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
    h = {p: r for p, r in hashes().items() if r["partial"]}
    copy_paths = {m["path"] for m in copies()[0]["media"]}
    assert copy_paths <= set(h)
    bob = [p for p in hashes() if "/bob/" in p]
    assert bob and not set(bob) & set(h)                         # a size of its own: only a dHash
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
    assert hashes()[copy_base + ".jpg"]["size"] == before[copy_base + ".jpg"]["size"] + 1
    os.remove(copy_base + ".jpg")
    run_scan(env)
    hashing.run_pass(conn)
    assert copy_base + ".jpg" not in hashes()


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


def test_writes_are_batched_outside_file_reads(env, monkeypatch):
    two_folders(env, slides=[False, False, True])
    run_scan(env)
    writes = []
    real = hashing._write
    monkeypatch.setattr(hashing, "_write", lambda conn, phase, pending: (
        writes.append((phase, len(pending))), real(conn, phase, pending)))
    assert hashing.run_pass(db.connect())
    rows = hashes().values()
    assert [w for w in writes if w[1]] == [("partial", sum(1 for r in rows if r["partial"])),
                                           ("dhash", sum(1 for r in rows if r["dhash_at"]))]


def test_copy_of_a_big_file_is_confirmed_by_a_full_hash(env):
    """Same size, first and last MiB: only the whole file tells a copy
    damaged in the middle from a good one."""
    a, copy_base = two_folders(env, "video")
    big(copy_base + ".mp4", b"x")
    big(a + ".mp4", b"x")
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    h = hashes()
    assert h[a + ".mp4"]["full"] and h[a + ".mp4"]["full"] == h[copy_base + ".mp4"]["full"]
    [g] = duplicates.all_groups(conn, "copies")
    assert g["identical"] is True
    big(copy_base + ".mp4", b"y")
    t = time.time() + 5
    os.utime(copy_base + ".mp4", (t, t))
    run_scan(env)
    hashing.run_pass(conn)
    [g] = duplicates.all_groups(conn, "copies")
    assert g["identical"] is False and g["differs"][0]["reason"] == "content"


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


def test_suggestion_prefers_the_higher_resolution(env):
    """A copy that differs (here: a bigger picture) is suggested over the
    older, shorter-path post when its images have more pixels."""
    from fakes import png
    _, copy_base = two_folders(env, "image")
    png(copy_base + ".jpg", size=(128, 128))
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    h = hashes()
    assert (h[copy_base + ".jpg"]["width"], h[copy_base + ".jpg"]["height"]) == (128, 128)
    [g] = duplicates.all_groups(conn, "copies")
    assert g["identical"] is False and g["differs"][0]["reason"] == "size"
    assert g["suggested"] == g["members"][1]["id"]


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
    assert g["suggested"] == "instagram:P1"                        # posted first, even with fewer media


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


def test_dismissal_covers_a_smaller_group_not_a_bigger_one(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    reposts = []
    for n, who in enumerate(("bob", "carol")):
        r = write_post(env["media"] / who, f"R{n}", TS + 99, owner(who, 200 + n), "image")
        shutil.copyfile(a + ".jpg", r + ".jpg")
        reposts.append(r)
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    [g] = duplicates.all_groups(conn, "content")
    assert len(g["members"]) == 3
    conn.execute("INSERT INTO dismissed_duplicates(key, kind, at) VALUES (?, 'content', 1)", (g["key"],))
    conn.commit()
    for f in os.listdir(env["media"] / "carol"):                 # one member leaves: still dismissed
        os.remove(env["media"] / "carol" / f)
    run_scan(env)
    hashing.run_pass(conn)
    assert duplicates.all_groups(conn, "content") == []
    d = write_post(env["media"] / "dave", "R7", TS + 999, owner("dave", 444), "image")
    shutil.copyfile(a + ".jpg", d + ".jpg")                       # one joins: shows again
    run_scan(env)
    hashing.run_pass(conn)
    [g] = duplicates.all_groups(conn, "content")
    assert len(g["members"]) == 3


def test_a_file_shared_by_many_posts_links_nothing(env, monkeypatch):
    monkeypatch.setattr(duplicates, "MAX_SHARED", 2)
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    for n, who in enumerate(("bob", "carol")):
        r = write_post(env["media"] / who, f"R{n}", TS + 99, owner(who, 200 + n), "image")
        shutil.copyfile(a + ".jpg", r + ".jpg")
    run_scan(env)
    conn = db.connect()
    hashing.run_pass(conn)
    assert duplicates.all_groups(conn, "content") == []


# ---------------------------------------------------------------------------
# Step 3: the API, resolve through the trash, dismiss
# ---------------------------------------------------------------------------

from conftest import H  # noqa: E402


def hashed_copies(env, **kw):
    a, copy_base = two_folders(env, **kw)
    run_scan(env)
    hashing.run_pass(db.connect())
    return a, copy_base


def listing(client, kind="copies"):
    r = client.get(f"/api/duplicates?kind={kind}", headers=H)
    assert r.status_code == 200
    return r.get_json()


def test_api_needs_the_header_and_a_known_kind(env, client):
    assert client.get("/api/duplicates").status_code == 403
    assert client.post("/api/duplicates/resolve", json={}).status_code == 403
    assert client.post("/api/duplicates/dismiss", json={}).status_code == 403
    assert client.get("/api/duplicates?kind=nope", headers=H).status_code == 400
    for body in ({}, {"group": 1, "keep": "x"}, {"groups": []}, {"groups": [{"group": "g"}]},
                 {"groups": [{"group": "g", "keep": "k"}] * 501}):
        r = client.post("/api/duplicates/resolve", json=body, headers=H)
        assert r.status_code == 400 and r.get_json()["ok"] is False
    assert client.post("/api/duplicates/dismiss", json={"group": 5}, headers=H).status_code == 400
    assert client.post("/api/duplicates/dismiss", json={"group": "nope"}, headers=H).status_code == 404


def test_listing_and_status(env, client):
    hashed_copies(env, slides=[False, True])
    r = listing(client)
    assert (r["total"], r["identical"], r["pending"], r["dismissed"]) == (1, 1, 0, 0)
    g = r["groups"][0]
    assert r["frees"] == r["identical_frees"] == g["frees"]
    post, copy = g["members"]
    assert post["post"]["id"] == "instagram:P1" and copy["post"] is None
    assert copy["thumb_url"] == f"/media/copy/{copy['copy_id']}/thumb"
    assert client.get(copy["thumb_url"]).status_code == 200
    assert all("poster_path" not in i for i in copy["items"])
    assert listing(client, "content")["total"] == 0
    assert client.get("/api/duplicates?limit=0&offset=5", headers=H).get_json()["groups"] == []
    st = client.get("/api/duplicates/status", headers=H).get_json()
    assert st["running"] is False and st["hashed"] >= 4


def test_copy_thumb_only_serves_recorded_copies(env, client):
    assert client.get("/media/copy/999/thumb").status_code == 404


def test_resolve_keep_post_trashes_the_copy_and_restores(env, client):
    _, copy_base = hashed_copies(env, slides=[False, True])
    g = listing(client)["groups"][0]
    copy_files = sorted(os.listdir(env["media"] / "alicee"))
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": g["suggested"]}, headers=H).get_json()
    assert r["ok"] and r["resolved"] == [g["id"]] and r["skipped"] == [] and r["errors"] == []
    assert r["posts"] == [] and r["copies"] == [g["members"][1]["copy_id"]]
    assert r["files"] == len(copy_files) and os.listdir(env["media"] / "alicee") == []
    assert sorted(os.listdir(env["media"] / ".feedvault-trash" / "alicee")) == copy_files
    assert listing(client)["total"] == 0 and copies() == [] and db.unmatched(db.connect()) == []
    assert client.get("/api/posts/instagram/P1", headers=H).status_code == 200
    # the Trash page lists the copy on its own, and restores it as a copy
    items = client.get("/api/trash/items", headers=H).get_json()
    [e] = items["entries"]
    assert e["copy"] is True and e["post"] == "instagram:P1" and e["items"] == 2
    r = client.post("/api/trash/restore", json={"keys": [e["key"]]}, headers=H).get_json()
    assert r["files"] == len(copy_files) and r["errors"] == []
    assert len(copies()) == 1 and len(db.unmatched(db.connect())) == 1
    hashing.run_pass(db.connect())
    assert listing(client)["identical"] == 1


def test_resolve_keep_copy_promotes_it_with_the_decision(env, client):
    _, copy_base = hashed_copies(env, kind="image")
    db.set_decision(db.connect(), ["instagram:P1"], "keep", 1)
    g = listing(client)["groups"][0]
    copy = g["members"][1]
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": copy["id"]}, headers=H).get_json()
    assert r["ok"] and r["posts"] == ["instagram:P1"] and r["copies"] == []
    post = client.get("/api/posts/instagram/P1", headers=H).get_json()
    assert post["source"]["meta_path"] == copy_base + ".json" and post["decision"] == "keep"
    assert copies() == [] and listing(client)["total"] == 0
    assert run_scan(env)["added"] == 0                             # the scan agrees


def test_resolve_refuses_a_changed_file(env, client):
    _, copy_base = hashed_copies(env, kind="image")
    g = listing(client)["groups"][0]
    t = time.time() + 10
    os.utime(copy_base + ".jpg", (t, t))
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": g["suggested"]}, headers=H).get_json()
    assert r["ok"] is False and r["resolved"] == [] and r["files"] == 0
    assert r["skipped"] == [{"group": g["id"], "error": "a file changed since it was hashed; wait for the next pass"}]
    assert os.path.exists(copy_base + ".jpg")


def test_resolve_refuses_pending_stale_and_missing_keeper(env, client):
    a, copy_base = two_folders(env, kind="image")
    run_scan(env)
    g = listing(client)["groups"][0]
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": g["suggested"]}, headers=H).get_json()
    assert "still being hashed" in r["skipped"][0]["error"]
    hashing.run_pass(db.connect())
    g = listing(client)["groups"][0]
    r = client.post("/api/duplicates/resolve", json={"group": "0" * 20, "keep": g["suggested"]}, headers=H).get_json()
    assert "reload" in r["skipped"][0]["error"]
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": "copy:999"}, headers=H).get_json()
    assert "not in this group" in r["skipped"][0]["error"]
    os.remove(a + ".jpg")
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": "instagram:P1"}, headers=H).get_json()
    assert "member to keep is gone" in r["skipped"][0]["error"]
    assert os.path.exists(copy_base + ".jpg")


def test_bulk_resolve_skips_conflicts(env, client):
    """A post kept in one group must not be trashed by another in the same call."""
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    b = write_post(env["media"] / "bob", "R9", TS + 99, owner("bob", 222), "image")
    shutil.copyfile(a + ".jpg", b + ".jpg")
    shutil.copytree(env["media"] / "alice", env["media"] / "alicee")
    run_scan(env)
    hashing.run_pass(db.connect())
    cg = listing(client)["groups"][0]
    tg = listing(client, "content")["groups"][0]
    body = {"groups": [{"group": cg["id"], "keep": "instagram:P1"},      # keep P1, trash its copy
                       {"group": tg["id"], "keep": "instagram:R9"}]}     # would trash P1
    r = client.post("/api/duplicates/resolve", json=body, headers=H).get_json()
    assert r["resolved"] == [cg["id"]] and r["skipped"][0]["group"] == tg["id"]
    assert os.path.exists(a + ".jpg") and r["posts"] == []


def test_resolve_partial_failure_keeps_the_copy(env, client):
    _, copy_base = hashed_copies(env, kind="image")
    g = listing(client)["groups"][0]
    folder = env["media"] / "alicee"
    os.chmod(folder, 0o555)                                        # nothing can be moved out
    try:
        r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": g["suggested"]},
                        headers=H).get_json()
    finally:
        os.chmod(folder, 0o755)
    if os.geteuid() == 0:                                          # root ignores the mode
        return
    assert r["resolved"] == [] and r["errors"] and r["copies"] == []
    assert len(copies()) == 1


def test_resolve_never_follows_a_symlink_out_of_the_roots(env, client, tmp_path):
    outside = tmp_path / "outside.jpg"
    _, copy_base = two_folders(env, kind="image")
    shutil.copyfile(copy_base + ".jpg", outside)
    os.remove(copy_base + ".jpg")
    os.symlink(outside, copy_base + ".jpg")
    run_scan(env)
    hashing.run_pass(db.connect())
    g = listing(client)["groups"][0]
    assert g["identical"] is True
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": "instagram:P1"}, headers=H).get_json()
    assert any("outside the media roots" in e["error"] for e in r["errors"])
    assert outside.exists() and os.path.islink(copy_base + ".jpg")


def test_dismiss(env, client):
    hashed_copies(env, kind="image")
    g = listing(client)["groups"][0]
    assert client.post("/api/duplicates/dismiss", json={"group": g["id"]}, headers=H).get_json() == {"ok": True}
    r = listing(client)
    assert (r["total"], r["dismissed"]) == (0, 1)
    userdata.flush()
    saved = json.load(open(userdata.path(str(env["tmp"] / "data"), "dismissed_duplicates")))
    [row] = saved["rows"]
    assert row["kind"] == "copies" and json.loads(row["key"]) == sorted(
        ["instagram:P1", g["members"][1]["meta_path"]])
