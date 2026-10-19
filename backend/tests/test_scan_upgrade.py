"""A post read from its metadata file and the same post rebuilt from a file
name (metadata mode "none"): one row, the metadata's, upgraded in place
(scanner._index_post); and the media file probe (yt-dlp's embedded tags)
run with db.write_lock let go (scanner._enrich)."""
import os
import threading

import pytest

from fakes import fake_video as _fake_video, yt_dlp_case

import db
import scanner
from parsers import yt_dlp

VIDEO = "@somechannel-20050424-AAAAAAAAAA1"                      # fixtures/yt_dlp/youtube/video
BARE = "@somechannel-20050424-AAAAAAAAAA1 [Youtube UCexampleChannelAAAAAAA1].mp4"   # sync.YT_DLP_BARE's name
ID = "youtube:AAAAAAAAAA1"


@pytest.fixture(autouse=True)
def no_ffprobe(monkeypatch):
    """No ffprobe: no tags (a test that wants some patches its own)."""
    monkeypatch.setattr(yt_dlp, "tags", lambda path, ffprobe=None: {})


def fake_video(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _fake_video(path)


def rows(conn):
    return conn.execute("SELECT n, id, first_seen, meta_path, tool FROM posts").fetchall()


def media(conn):
    return sorted(r[0] for r in conn.execute("SELECT path FROM media WHERE post_id = ?", (ID,)))


def copies(conn):
    return sorted(r[0] for r in conn.execute("SELECT meta_path FROM copies WHERE post_id = ?", (ID,)))


def keep(conn):
    """Mark the post as the user would (a tag, a save, a set first_seen)."""
    conn.execute("INSERT INTO tags(id, name, created_at) VALUES (1, 'kept', 0)")
    conn.execute("INSERT INTO post_tags(post_id, tag_id, at) VALUES (?, 1, 0)", (ID,))
    conn.execute("INSERT INTO saved_posts(post_id, saved_at) VALUES (?, 0)", (ID,))
    conn.execute("UPDATE posts SET first_seen = 12345 WHERE id = ?", (ID,))
    conn.commit()


def kept(conn):
    assert conn.execute("SELECT post_id FROM post_tags").fetchall()[0][0] == ID
    assert conn.execute("SELECT post_id FROM saved_posts").fetchall()[0][0] == ID


def test_metadata_appears_beside_the_named_file(env):
    folder = env["media"] / "youtube" / "somechannel"
    fake_video(str(folder / BARE))
    assert scanner.scan(env["roots"])["added"] == 1
    conn = db.connect()
    [before] = rows(conn)
    assert (before["tool"], before["meta_path"]) == ("yt-dlp (filenames)", str(folder / BARE))
    keep(conn)
    yt_dlp_case("youtube/video", folder)                          # a sync with metadata, later
    report = scanner.scan(env["roots"])
    assert (report["added"], report["updated"]) == (0, 1)
    [after] = rows(conn)
    assert (after["n"], after["id"], after["first_seen"]) == (before["n"], ID, 12345)
    assert (after["tool"], after["meta_path"]) == ("yt-dlp", str(folder / (VIDEO + ".info.json")))
    assert media(conn) == [str(folder / (VIDEO + ".webm"))]
    # The named file is another copy of it now, reported as one.
    assert copies(conn) == [str(folder / BARE)]
    kept(conn)
    assert conn.execute("SELECT text FROM posts").fetchone()[0].startswith("A walk in the park")
    assert scanner.scan(env["roots"])["updated"] == 0              # and it stays so
    assert rows(conn) == [after]


@pytest.mark.parametrize("bare_first", [True, False])
def test_both_in_one_scan(env, bare_first):
    """The named file and the metadata in two folders, either one walked
    first: one post, from the metadata."""
    named, meta = (env["media"] / "a", env["media"] / "b") if bare_first else (env["media"] / "b", env["media"] / "a")
    fake_video(str(named / BARE))
    yt_dlp_case("youtube/video", meta)
    assert scanner.scan(env["roots"])["added"] == 1
    conn = db.connect()
    [first] = rows(conn)
    assert (first["tool"], first["meta_path"]) == ("yt-dlp", str(meta / (VIDEO + ".info.json")))
    keep(conn)
    first = rows(conn)[0]
    scanner.scan(env["roots"])
    assert rows(conn) == [first]
    assert media(conn) == [str(meta / (VIDEO + ".webm"))]
    assert copies(conn) == [str(named / BARE)]
    kept(conn)


def test_named_file_never_downgrades(env):
    """Metadata first, then the named file (another folder, or index_dirs
    after a sync in "none"): a copy, the post as its metadata gives it."""
    meta, named = env["media"] / "meta", env["media"] / "named"
    yt_dlp_case("youtube/video", meta)
    scanner.scan(env["roots"])
    conn = db.connect()
    keep(conn)
    [first] = rows(conn)
    fake_video(str(named / BARE))
    assert scanner.index_dirs(env["roots"], [str(named)])["added"] == 0
    assert rows(conn) == [first] and first["tool"] == "yt-dlp"
    assert copies(conn) == [str(named / BARE)]
    # Its metadata file deleted: the post stays as it was read, missing, not rebuilt from the name.
    (meta / (VIDEO + ".info.json")).unlink()
    scanner.scan(env["roots"])
    [gone] = conn.execute("SELECT n, first_seen, meta_path, tool, missing, text FROM posts").fetchall()
    assert (gone["n"], gone["first_seen"], gone["tool"], gone["missing"]) == (first["n"], 12345, "yt-dlp", 1)
    assert gone["text"].startswith("A walk in the park")
    kept(conn)


# ---------------------------------------------------------------------------
# The probe, with the lock let go
# ---------------------------------------------------------------------------

def probing(monkeypatch, held):
    """yt_dlp.tags noting whether db.write_lock was held when called."""
    def tags(path, ffprobe=None):
        held.append(db.write_lock.locked())
        return {"title": "From the file", "description": "about #cats"}
    monkeypatch.setattr(yt_dlp, "tags", tags)


def test_probe_runs_without_the_lock(env, monkeypatch):
    held = []
    probing(monkeypatch, held)
    folder = env["media"] / "youtube" / "somechannel"
    fake_video(str(folder / BARE))
    assert scanner.scan(env["roots"])["added"] == 1
    assert held == [False]
    conn = db.connect()
    text, hashtags = conn.execute("SELECT text, hashtags FROM posts WHERE id = ?", (ID,)).fetchone()
    assert (text, hashtags) == ("From the file\n\nabout #cats", '["cats"]')
    hits = conn.execute("SELECT id FROM posts_fts WHERE posts_fts MATCH 'cats'").fetchall()
    assert [r[0] for r in hits] == [ID]
    # Written with the file as indexed: the next scan finds nothing changed, and probes nothing.
    assert scanner.scan(env["roots"])["updated"] == 0 and held == [False]
    # A file indexed after a sync (index_dirs): the same.
    fake_video(str(folder / BARE.replace("AAAAAAAAAA1", "AAAAAAAAAA2")))
    assert scanner.index_dirs(env["roots"], [str(folder)], new=True)["added"] == 1
    assert held == [False, False]
    assert conn.execute("SELECT text FROM posts WHERE id = 'youtube:AAAAAAAAAA2'").fetchone()[0] == \
        "From the file\n\nabout #cats"


def test_a_stop_before_the_probe_is_probed_on_the_next_scan(env, monkeypatch):
    """Indexed, then FeedVault stopped before the probe wrote: the post is
    kept marked (no size), and the next scan reads it again and probes it."""
    held = []
    probing(monkeypatch, held)
    folder = env["media"] / "youtube" / "somechannel"
    fake_video(str(folder / BARE))
    enrich = scanner._enrich
    monkeypatch.setattr(scanner, "_enrich", lambda conn, posts, now: None)   # the stop
    assert scanner.scan(env["roots"])["added"] == 1
    conn = db.connect()
    text, size = conn.execute("SELECT text, meta_size FROM posts WHERE id = ?", (ID,)).fetchone()
    assert held == [] and size is None and "From the file" not in (text or "")
    monkeypatch.setattr(scanner, "_enrich", enrich)
    assert scanner.scan(env["roots"])["updated"] == 1 and held == [False]
    text, size = conn.execute("SELECT text, meta_size FROM posts WHERE id = ?", (ID,)).fetchone()
    assert text == "From the file\n\nabout #cats" and size == os.path.getsize(folder / BARE)
    assert scanner.scan(env["roots"])["updated"] == 0 and held == [False]


def test_restore_probes_apart(env, monkeypatch):
    """index_dirs_locked, its caller holding the lock (a restore): the
    probe runs in a thread of its own, and writes once the lock is let go."""
    probed = threading.Event()

    def tags(path, ffprobe=None):
        probed.set()
        return {"title": "From the file"}
    monkeypatch.setattr(yt_dlp, "tags", tags)
    folder = env["media"] / "youtube" / "somechannel"
    fake_video(str(folder / BARE))
    with db.write_lock:
        scanner.index_dirs_locked(env["roots"], [str(folder)])
        # The probe does not wait for the lock, nor the lock for the probe.
        assert probed.wait(10)
    for t in threading.enumerate():
        if t.name == "enrich":
            t.join(10)
    assert db.connect().execute("SELECT text FROM posts WHERE id = ?", (ID,)).fetchone()[0] == "From the file"


def test_post_replaced_meanwhile_is_left_alone(env, monkeypatch):
    """The named post upgraded from its metadata between the index and the
    probe's write: the write is dropped."""
    folder = env["media"] / "youtube" / "somechannel"
    fake_video(str(folder / BARE))
    conn = db.connect()

    def tags(path, ffprobe=None):
        yt_dlp_case("youtube/video", folder)
        scanner.index_dirs_locked(env["roots"], [str(folder)], later=[])
        conn.commit()
        return {"title": "From the file"}
    monkeypatch.setattr(yt_dlp, "tags", tags)
    scanner.scan(env["roots"])
    [row] = rows(conn)
    assert row["tool"] == "yt-dlp"
    assert conn.execute("SELECT text FROM posts").fetchone()[0].startswith("A walk in the park")
