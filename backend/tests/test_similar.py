import os
import random
import shutil
import sqlite3
import time

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
    assert h[n + ".mp4"]["dhash"] is None and h[n + ".mp4"]["dhash_at"]       # tried, nothing to read
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
    assert hashing.status()["done"] == 12
