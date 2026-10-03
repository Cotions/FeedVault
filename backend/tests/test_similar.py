import os
import random
import shutil
import sqlite3
import time
import types

from PIL import Image, ImageDraw, ImageFilter

from fakes import owner, write_post

import db
import hashing
import scanner
import thumbs

ALICE = owner("alice.example", 111, "Alice Example")
BOB = owner("bob", 222, "Bob")
TS = 1717243200


def photo(path, seed, size=(640, 800), quality=90):
    """A picture with structure (blurred shapes), unlike the flat test PNGs."""
    rng = random.Random(seed)
    img = Image.new("RGB", size, tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    w, h = size
    for _ in range(40):
        x, y = rng.randrange(w), rng.randrange(h)
        r = rng.randrange(w // 20, w // 4)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=tuple(rng.randrange(256) for _ in range(3)))
    img.filter(ImageFilter.GaussianBlur(2)).save(path, "JPEG", quality=quality)


def resized(src, dst, scale=0.5, quality=60):
    with Image.open(src) as im:
        im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.BICUBIC).save(
            dst, "JPEG", quality=quality)


def distance(a, b):
    return (a ^ b).bit_count()


def hashes():
    return {r["path"]: dict(r) for r in db.connect().execute("SELECT * FROM media_hash")}


# ---------------------------------------------------------------------------
# Step 1: perceptual hashes
# ---------------------------------------------------------------------------

def test_dhash_survives_resizing_not_another_picture(tmp_path):
    a, b, c = tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "c.jpg"
    photo(a, 1)
    resized(a, b)
    photo(c, 2)
    ha, hb, hc = hashing.dhash(a), hashing.dhash(b), hashing.dhash(c)
    assert 0 <= ha < 1 << 64
    assert distance(ha, hb) <= 4
    assert distance(ha, hc) > 16


def test_dhash_follows_exif_orientation(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    photo(a, 3)
    with Image.open(a) as im:                     # stored rotated, with a tag saying so
        exif = Image.Exif()
        exif[0x0112] = 6                          # rotate 90 degrees clockwise to display
        im.transpose(Image.Transpose.ROTATE_90).save(b, "JPEG", quality=90, exif=exif)
    assert distance(hashing.dhash(a), hashing.dhash(b)) <= 4


def test_signed_storage_round_trip():
    for v in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1):
        stored = hashing.to_db(v)
        assert -(1 << 63) <= stored < 1 << 63 and hashing.from_db(stored) == v
    assert hashing.to_db(None) is None and hashing.from_db(None) is None


def test_pass_fingerprints_every_picture(env, monkeypatch):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    photo(a + ".jpg", 1)
    v = write_post(env["media"] / "alice", "V1", TS + 60, ALICE, "video")       # poster beside it
    n = write_post(env["media"] / "alice", "V2", TS + 120, ALICE, "video")
    os.remove(n + ".jpg")                                                     # no poster
    monkeypatch.setattr(thumbs, "have_ffmpeg", lambda: False)
    scanner.scan(env["roots"])
    assert hashing.run_pass(db.connect())
    h = hashes()
    assert hashing.from_db(h[a + ".jpg"]["dhash"]) == hashing.dhash(a + ".jpg")
    assert (h[a + ".jpg"]["width"], h[a + ".jpg"]["height"]) == (640, 800)
    assert h[v + ".mp4"]["dhash"] is not None                                  # from the poster
    assert n + ".mp4" not in h or h[n + ".mp4"]["dhash_at"] is None          # waits for ffmpeg
    assert h[a + ".jpg"]["partial"] is None                                    # no twin: no content hash
    st = hashing.status()
    assert (st["fingerprinted"], st["errors"]) == (2, [])


def test_pass_prefers_the_cached_thumbnail(env, monkeypatch):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    photo(a + ".jpg", 1)
    scanner.scan(env["roots"])
    row = db.connect().execute("SELECT * FROM media").fetchone()
    data_dir = str(env["tmp"] / "data")
    thumb = thumbs.thumb_for(data_dir, row)
    seen = []
    real = hashing.dhash
    monkeypatch.setattr(hashing, "dhash", lambda p: (seen.append(str(p)), real(p))[1])
    hashing.run_pass(db.connect(), data_dir=data_dir)
    assert seen == [thumb]
    assert distance(hashing.from_db(hashes()[a + ".jpg"]["dhash"]), real(a + ".jpg")) <= 4


def test_video_without_poster_uses_an_ffmpeg_frame(env, monkeypatch):
    n = write_post(env["media"] / "alice", "V2", TS, ALICE, "video")
    os.remove(n + ".jpg")
    scanner.scan(env["roots"])
    data_dir = str(env["tmp"] / "data")

    def frame(src, out):                                                   # what ffmpeg would write
        os.makedirs(os.path.dirname(out), exist_ok=True)
        photo(out, 5)
        return True
    monkeypatch.setattr(thumbs, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(thumbs, "_video_frame", frame)
    hashing.run_pass(db.connect(), data_dir=data_dir)
    row = db.connect().execute("SELECT * FROM media").fetchone()
    assert thumbs.cached(data_dir, row)                                    # kept for the grid too
    assert hashes()[n + ".mp4"]["dhash"] is not None


def test_dhash_follows_changes_and_survives_content_hashing(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    b = write_post(env["media"] / "bob", "R9", TS + 99, BOB, "image")
    photo(a + ".jpg", 1)
    shutil.copyfile(a + ".jpg", b + ".jpg")                    # same size: content hashed as well
    scanner.scan(env["roots"])
    conn = db.connect()
    hashing.run_pass(conn)
    h = hashes()
    assert h[a + ".jpg"]["partial"] and h[a + ".jpg"]["dhash"] is not None
    first = hashing.from_db(h[b + ".jpg"]["dhash"])
    hashing.run_pass(conn)                                     # nothing changed: nothing recomputed
    assert hashes() == h
    photo(b + ".jpg", 2)
    t = time.time() + 5
    os.utime(b + ".jpg", (t, t))
    scanner.scan(env["roots"])
    hashing.run_pass(conn)
    h = hashes()
    assert distance(hashing.from_db(h[b + ".jpg"]["dhash"]), first) > 16
    size = os.path.getsize(b + ".jpg")
    assert h[b + ".jpg"]["size"] == size and h[b + ".jpg"]["partial"] in (None, hashing.partial_hash(b + ".jpg", size))
    assert h[a + ".jpg"]["partial"] and h[a + ".jpg"]["dhash"] is not None


def test_unreadable_picture_is_tried_once(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    with open(a + ".jpg", "wb") as f:
        f.write(b"not a picture")
    scanner.scan(env["roots"])
    conn = db.connect()
    hashing.run_pass(conn)
    st = hashing.status()
    assert hashes()[a + ".jpg"]["dhash"] is None and "not a readable picture" in st["errors"][0]["error"]
    at = hashes()[a + ".jpg"]["dhash_at"]
    hashing._state["errors"] = []
    hashing.run_pass(conn)
    assert hashes()[a + ".jpg"]["dhash_at"] == at and hashing.status()["errors"] == []


def test_any_decoder_error_is_noted_not_fatal(env, monkeypatch):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    b = write_post(env["media"] / "alice", "P2", TS + 1, ALICE, "image")
    photo(a + ".jpg", 1)
    photo(b + ".jpg", 2)
    scanner.scan(env["roots"])
    real = hashing.dhash

    def flaky(p):
        if str(p) == a + ".jpg":
            raise ZeroDivisionError("bad EXIF")                 # not an OSError: Pillow plugins raise anything
        return real(p)
    monkeypatch.setattr(hashing, "dhash", flaky)
    assert hashing.run_pass(db.connect())
    h = hashes()
    assert h[a + ".jpg"]["dhash"] is None and h[a + ".jpg"]["dhash_at"]
    assert h[b + ".jpg"]["dhash"] is not None
    assert "ZeroDivisionError" in hashing.status()["errors"][0]["error"]


def test_video_waits_for_ffmpeg(env, monkeypatch):
    n = write_post(env["media"] / "alice", "V2", TS, ALICE, "video")
    os.remove(n + ".jpg")
    scanner.scan(env["roots"])
    data_dir = str(env["tmp"] / "data")
    monkeypatch.setattr(thumbs, "have_ffmpeg", lambda: False)
    hashing.run_pass(db.connect(), data_dir=data_dir)
    assert hashes().get(n + ".mp4", {}).get("dhash") is None

    def frame(src, out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        photo(out, 5)
        return True
    monkeypatch.setattr(thumbs, "have_ffmpeg", lambda: True)    # installed later: picked up next pass
    monkeypatch.setattr(thumbs, "_video_frame", frame)
    hashing.run_pass(db.connect(), data_dir=data_dir)
    assert hashes()[n + ".mp4"]["dhash"] is not None


def test_worker_steps_aside_during_the_dhash_phase(env, monkeypatch):
    import threading
    monkeypatch.setattr(hashing, "PICTURE_WORKERS", 1)
    for i in range(3):
        photo(write_post(env["media"] / "alice", f"P{i}", TS + i, ALICE, "image") + ".jpg", i)
    scanner.scan(env["roots"])
    real = hashing.dhash
    started = []

    def slow(p):
        if not started:                                        # a scan starts during the first file
            started.append(db.write_lock.acquire())
        return real(p)
    monkeypatch.setattr(hashing, "dhash", slow)
    t = threading.Thread(target=lambda: hashing.run_pass(db.connect()))
    t.start()
    try:
        for _ in range(50):
            if hashing.status()["paused"]:
                break
            time.sleep(0.05)
        st = hashing.status()
        assert st["paused"] and st["phase"] == "dhash" and st["done"] == 1
    finally:
        db.write_lock.release()
    t.join(5)
    assert not t.is_alive() and hashing.status()["fingerprinted"] == 3


def test_migration_5_keeps_content_hashes(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:4])
    db.init(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO media_hash(path, size, mtime_ns, partial, full, width, height, hashed_at) "
                 "VALUES ('/m/a.jpg', 3, 4, 'p', 'f', 10, 20, 5)")
    conn.commit()
    conn.close()
    monkeypatch.undo()
    db.init(path)
    row = dict(db.connect().execute("SELECT * FROM media_hash").fetchone())
    assert row == {"path": "/m/a.jpg", "size": 3, "mtime_ns": 4, "partial": "p", "full": "f",
                   "width": 10, "height": 20, "hashed_at": 5, "dhash": None, "dhash_at": None}
    assert os.path.exists(path + ".bak-v4")
    db.connect().execute("INSERT INTO media_hash(path, size, mtime_ns, hashed_at, dhash, dhash_at) "
                         "VALUES ('/m/b.jpg', 1, 1, 1, -5, 1)")              # partial is optional now


def test_worker_threads_give_the_same_hashes(env, monkeypatch):
    for i in range(12):
        photo(write_post(env["media"] / "alice", f"P{i}", TS + i, ALICE, "image") + ".jpg", i)
    scanner.scan(env["roots"])
    monkeypatch.setattr(hashing, "PICTURE_WORKERS", 1)
    hashing.run_pass(db.connect())
    serial = {p: r["dhash"] for p, r in hashes().items()}
    db.connect().execute("DELETE FROM media_hash")
    db.connect().commit()
    monkeypatch.setattr(hashing, "PICTURE_WORKERS", 3)
    hashing.run_pass(db.connect())
    assert {p: r["dhash"] for p, r in hashes().items()} == serial and len(serial) == 12
    assert hashing.status()["fingerprinted"] == 12


# ---------------------------------------------------------------------------
# Step 2: near matches
# ---------------------------------------------------------------------------

import json  # noqa: E402

import config  # noqa: E402
import duplicates  # noqa: E402
import userdata  # noqa: E402
from conftest import H  # noqa: E402


def test_near_pairs_finds_exactly_the_close_ones():
    rng = random.Random(7)
    values = [rng.getrandbits(64) for _ in range(3000)]
    for n in range(0, 400, 2):                                    # plant pairs 0 to 12 bits apart
        v = values[n]
        for bit in rng.sample(range(64), n // 2 % 13):
            v ^= 1 << bit
        values[n + 1] = v
    for t in (0, 3, 4, 6, 7, 10):
        brute = {(i, j) for i in range(len(values)) for j in range(i + 1, len(values))
                 if (values[i] ^ values[j]).bit_count() <= t} if t in (4, 10) else None
        got = duplicates.near_pairs(values, t)
        assert all((values[i] ^ values[j]).bit_count() <= t and i < j for i, j in got)
        planted = {(n, n + 1) for n in range(0, 400, 2) if (values[n] ^ values[n + 1]).bit_count() <= t}
        assert planted <= got
        if brute is not None:
            assert got == brute


def set_dhash(path, value):
    db.connect().execute("UPDATE media_hash SET dhash = ? WHERE path = ?", (hashing.to_db(value), path))
    db.connect().commit()


def repost(env):
    """An original (posted first, saved last) and a smaller, recompressed
    repost of it by another account."""
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    photo(a + ".jpg", 1)
    b = write_post(env["media"] / "bob", "R9", TS + 3600, BOB, "image")
    resized(a + ".jpg", b + ".jpg")
    t = time.time() + 60
    os.utime(a + ".json", (t, t))
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    return a, b


def test_similar_group_for_a_resized_repost(env):
    a, b = repost(env)
    conn = db.connect()
    assert duplicates.all_groups(conn, "content") == []
    [g] = duplicates.all_groups(conn, "similar")
    assert [m["id"] for m in g["members"]] == ["instagram:P1", "instagram:R9"]
    assert g["identical"] is False and g["pending"] is False and g["differs"] == []
    assert g["repost"] is True and 0 <= g["distance"] <= 4
    p1, r9 = g["members"]
    assert (p1["match"], p1["items"][0]["width"], p1["items"][0]["height"]) == (1, 640, 800)
    assert (r9["items"][0]["width"], r9["items"][0]["height"]) == (320, 400)
    assert p1["thumb_url"] == p1["items"][0]["thumb_url"] and p1["thumb_url"].startswith("/media/")
    assert g["suggested"] == "instagram:P1"                        # posted first, larger


def test_similar_excludes_content_groups_and_flat_pictures(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    photo(a + ".jpg", 1)
    b = write_post(env["media"] / "bob", "R9", TS + 99, BOB, "image")
    shutil.copyfile(a + ".jpg", b + ".jpg")                        # byte for byte: a content group
    write_post(env["media"] / "alice", "F1", TS + 5, ALICE, "image")   # solid colours: flat
    write_post(env["media"] / "bob", "F2", TS + 6, BOB, "image")
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    conn = db.connect()
    assert len(duplicates.all_groups(conn, "content")) == 1
    flat = [r["dhash"] for p, r in hashes().items() if "F" in os.path.basename(p) or True]
    assert any(hashing.from_db(v).bit_count() <= duplicates.FLAT_BITS
               or hashing.from_db(v).bit_count() >= 64 - duplicates.FLAT_BITS for v in flat)
    assert duplicates.all_groups(conn, "similar") == []


def test_threshold_and_carousel_items(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    c = write_post(env["media"] / "bob", "C2", TS + 99, BOB, "carousel", slides=[False, False])
    photo(a + ".jpg", 1)
    photo(c + "_1.jpg", 2)
    photo(c + "_2.jpg", 3)
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    base = 0x0F0F_3C3C_5A5A_6969
    set_dhash(a + ".jpg", base)
    set_dhash(c + "_1.jpg", base ^ 0xFFFF_0000_0000_0000)        # 16 bits off: unrelated
    set_dhash(c + "_2.jpg", base ^ 0b11111)                        # 5 bits off
    conn = db.connect()
    assert duplicates.all_groups(conn, "similar", threshold=4) == []
    [g] = duplicates.all_groups(conn, "similar", threshold=6)
    assert g["distance"] == 5
    c2, p1 = g["members"]
    assert (p1["match"], c2["match"]) == (1, 2)
    assert g["differs"] == [{"member": "instagram:C2", "idx": 1, "reason": "only here"}]


def test_a_picture_near_too_many_posts_links_nothing(env):
    paths = []
    for i in range(duplicates.MAX_SHARED + 2):
        paths.append(write_post(env["media"] / f"u{i}", f"P{i}", TS + i, owner(f"u{i}", 1000 + i), "image") + ".jpg")
        photo(paths[-1], i)                                        # no two byte-identical
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    base = 0x0F0F_3C3C_5A5A_6969
    for p in paths:
        set_dhash(p, base)
    assert duplicates.all_groups(db.connect(), "similar") == []
    rng = random.Random(3)
    for p in paths[2:]:
        set_dhash(p, rng.getrandbits(64))
    [g] = duplicates.all_groups(db.connect(), "similar")
    assert len(g["members"]) == 2


def test_a_chain_is_split_around_centres(env):
    """A near B near C near D, 5 bits per step: not one group of pictures
    that do not look alike end to end, but groups around a centre."""
    paths = []
    for i in range(4):
        paths.append(write_post(env["media"] / f"u{i}", f"P{i}", TS + i, owner(f"u{i}", 1000 + i), "image") + ".jpg")
        photo(paths[-1], i)
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    base = 0x0F0F_3C3C_5A5A_6969
    for n, p in enumerate(paths):
        set_dhash(p, base ^ ((1 << (5 * n)) - 1))
    [g] = duplicates.all_groups(db.connect(), "similar", threshold=6)
    assert [m["id"] for m in g["members"]] == ["instagram:P0", "instagram:P1", "instagram:P2"]   # around P1
    assert g["distance"] == 5                                      # P0 and P2 are 10 apart, 5 from P1
    [g] = duplicates.all_groups(db.connect(), "similar", threshold=10)
    assert len(g["members"]) == 4 and g["distance"] == 10          # P1 is within 10 of every other


def test_videos_in_groups_are_measured_once(env, monkeypatch):
    a = write_post(env["media"] / "alice", "V1", TS, ALICE, "video")
    b = write_post(env["media"] / "bob", "V2", TS + 99, BOB, "video")
    c = write_post(env["media"] / "carol", "V3", TS + 999, owner("carol", 333), "video")
    for base, seed in ((a, 1), (b, 1), (c, 2)):
        photo(base + ".jpg", seed)                                 # a and b: the same poster
    for base, extra in ((b, b"x"), (c, b"yy")):                    # no byte-identical videos
        with open(base + ".mp4", "ab") as f:
            f.write(extra)
    calls = []
    monkeypatch.setattr(hashing, "video_size", lambda p: calls.append(p) or ((720, 1280) if p == a + ".mp4" else None))
    # hashing's own shutil: the global shutil.which stays the test guard's.
    monkeypatch.setattr(hashing, "shutil", types.SimpleNamespace(which=lambda name: "/usr/bin/" + name))
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    assert sorted(calls) == sorted([a + ".mp4", b + ".mp4"])     # carol's is in no group
    h = hashes()
    assert (h[a + ".mp4"]["width"], h[a + ".mp4"]["height"]) == (720, 1280)
    assert (h[b + ".mp4"]["width"], h[b + ".mp4"]["height"]) == (0, 0)  # tried: not again
    hashing.run_pass(db.connect())
    assert len(calls) == 2
    [g] = duplicates.all_groups(db.connect(), "similar")
    assert g["members"][0]["items"][0]["width"] == 720 and g["members"][1]["items"][0]["width"] is None


def similar(client, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get(f"/api/duplicates?kind=similar&{q}", headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_api_threshold(env, client):
    repost(env)
    r = similar(client)
    assert (r["threshold"], r["total"], r["reposts"], r["identical"]) == (6, 1, 1, 0)
    g = r["groups"][0]
    assert all("author" not in m for m in g["members"])
    assert similar(client, threshold=0)["total"] in (0, 1)
    for bad in ("11", "-1", "x", "6.5", "%C2%B2", "%EF%BC%96"):     # not ASCII digits
        assert client.get(f"/api/duplicates?kind=similar&threshold={bad}", headers=H).status_code == 400
    assert client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": "x", "threshold": 99},
                       headers=H).status_code == 400
    assert client.post("/api/duplicates/dismiss", json={"group": g["id"], "threshold": True},
                       headers=H).status_code == 400
    cfg = config.load()
    cfg["similar_threshold"] = 0
    config.save(cfg)
    assert similar(client)["threshold"] == 0
    cfg["similar_threshold"] = "lots"                              # unusable: the default
    config.save(cfg)
    assert similar(client)["threshold"] == duplicates.SIMILAR_DEFAULT
    assert client.get("/api/duplicates?kind=copies", headers=H).get_json()["threshold"] is None


def test_resolve_similar_one_at_a_time_and_restore(env, client):
    a, b = repost(env)
    g = similar(client, threshold=8)["groups"][0]
    other = {"group": "0" * 20, "keep": "x"}
    r = client.post("/api/duplicates/resolve", json={"groups": [{"group": g["id"], "keep": g["suggested"]}, other],
                                                     "threshold": 8}, headers=H).get_json()
    assert r["ok"] is False and "one at a time" in r["skipped"][0]["error"] and os.path.exists(b + ".jpg")
    r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": g["suggested"], "threshold": 0},
                    headers=H).get_json()
    if g["distance"] > 0:                                          # rebuilt at 0 bits: not the same group
        assert "reload" in r["skipped"][0]["error"] and os.path.exists(b + ".jpg")
        r = client.post("/api/duplicates/resolve", json={"group": g["id"], "keep": g["suggested"], "threshold": 8},
                        headers=H).get_json()
    assert r["ok"] and r["resolved"] == [g["id"]] and r["posts"] == ["instagram:R9"]
    assert not os.path.exists(b + ".jpg") and os.path.exists(a + ".jpg")
    assert similar(client, threshold=8)["total"] == 0
    [e] = client.get("/api/trash/items", headers=H).get_json()["entries"]
    r = client.post("/api/trash/restore", json={"keys": [e["key"]]}, headers=H).get_json()
    assert r["errors"] == [] and os.path.exists(b + ".jpg")
    hashing.run_pass(db.connect())
    assert similar(client, threshold=8)["total"] == 1


def test_dismiss_similar(env, client):
    repost(env)
    g = similar(client)["groups"][0]
    assert client.post("/api/duplicates/dismiss", json={"group": g["id"]}, headers=H).get_json() == {"ok": True}
    r = similar(client)
    assert (r["total"], r["dismissed"]) == (0, 1)
    userdata.flush()
    saved = json.load(open(userdata.path(str(env["tmp"] / "data"), "dismissed_duplicates")))
    assert [row["kind"] for row in saved["rows"]] == ["similar"]


def test_dismissals_of_another_kind_do_not_hide_a_similar_group(env, client):
    repost(env)
    conn = db.connect()
    [g] = duplicates.all_groups(conn, "similar")
    conn.execute("INSERT INTO dismissed_duplicates(key, kind, at) VALUES (?, 'content', 0)", (g["key"],))
    conn.commit()
    assert similar(client)["total"] == 1


# ---------------------------------------------------------------------------
# Step 3: keeper rule and reposts (#19)
# ---------------------------------------------------------------------------

def test_content_keeper_is_the_original_not_the_first_saved(env, client):
    """The repost was downloaded first (older saved_at), the original later."""
    b = write_post(env["media"] / "bob", "R9", TS + 3600, BOB, "image")
    photo(b + ".jpg", 1)
    scanner.scan(env["roots"])
    time.sleep(1.1)                                                # saved_at is in seconds
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    shutil.copyfile(b + ".jpg", a + ".jpg")
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    [g] = duplicates.all_groups(db.connect(), "content")
    by_id = {m["id"]: m for m in g["members"]}
    assert by_id["instagram:R9"]["saved_at"] < by_id["instagram:P1"]["saved_at"]
    assert g["identical"] is True and g["repost"] is True
    assert g["suggested"] == "instagram:P1"
    assert duplicates.suggest(g["members"], duplicates._hashes(db.connect()))["id"] == "instagram:R9"   # the copies rule
    r = client.get("/api/duplicates?kind=content", headers=H).get_json()
    assert (r["reposts"], r["groups"][0]["repost"]) == (1, True)


def test_same_account_twice_is_not_a_repost(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    b = write_post(env["media"] / "alice", "C2", TS + 99, ALICE, "carousel", slides=[False, False])
    photo(a + ".jpg", 1)
    shutil.copyfile(a + ".jpg", b + "_2.jpg")
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    [g] = duplicates.all_groups(db.connect(), "content")
    assert g["repost"] is False
    shutil.copytree(env["media"] / "alice", env["media"] / "alicee")         # copies: one post, never a repost
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    assert [g["repost"] for g in duplicates.all_groups(db.connect(), "copies")] == [False, False]


def member(id, kept=False, posted_at=None, size=100, width=None, height=None, path=None):
    item = {"idx": 1, "size": size, "width": width, "height": height}
    return {"id": id, "kept": kept, "posted_at": posted_at, "match": 1, "items": [item],
            "meta_path": path or f"/m/{id}.json"}


def test_keeper_rule_for_different_posts():
    pick = lambda *ms: duplicates.suggest_original(list(ms))["id"]      # noqa: E731
    # kept, then earliest posted (unknown last)
    assert pick(member("a", posted_at=5), member("b", kept=True, posted_at=9)) == "b"
    assert pick(member("a", posted_at=9), member("b", posted_at=5)) == "b"
    assert pick(member("a"), member("b", posted_at=9)) == "b"
    # then resolution, when known for everyone
    assert pick(member("a", 0, 5, width=10, height=10), member("b", 0, 5, width=20, height=20)) == "b"
    assert pick(member("a", 0, 5, size=900, width=10, height=10), member("b", 0, 5, width=20)) == "a"
    # then the largest file, then the shortest path
    assert pick(member("a", 0, 5, size=100), member("b", 0, 5, size=200)) == "b"
    assert pick(member("a", 0, 5, path="/m/long/a.json"), member("b", 0, 5, path="/m/b.json")) == "b"


def test_similar_keeper_prefers_higher_resolution_at_the_same_date(env):
    a = write_post(env["media"] / "alice", "P1", TS, ALICE, "image")
    b = write_post(env["media"] / "bob", "R9", TS, BOB, "image")             # posted the same second
    photo(b + ".jpg", 1)
    resized(b + ".jpg", a + ".jpg", scale=0.5, quality=95)
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())
    [g] = duplicates.all_groups(db.connect(), "similar")
    assert g["suggested"] == "instagram:R9" and g["repost"] is True
