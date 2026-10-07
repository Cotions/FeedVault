"""Performance regressions, without wall-clock thresholds: what a request
or a scan does is counted (SQL statements, SQLite's own work, passes over
a folder's file list), and must not grow with the archive. The numbers
on an archive-sized vault are in docs/TESTING.md (frontend/e2e/perf.js)."""
import os
import random
import re
import shutil

import pytest

from fakes import fake_video, noise_png, owner, write_meta, post_node, base_name

import db
import duplicates
import hashing
import scanner
from parsers import instaloader, is_media

ACCOUNTS = [owner("alice.example", 111, "Alice"), owner("bob.example", 222, "Bob"), owner("carol", 333, "Carol")]
TS = 1_700_000_000


def write_posts(media, start, count):
    """``count`` Instagram posts over ACCOUNTS from post ``start`` on: images,
    carousels and videos, every file different."""
    for n in range(start, start + count):
        who = ACCOUNTS[n % len(ACCOUNTS)]
        folder = media / who["username"]
        folder.mkdir(exist_ok=True)
        ts = TS + n * 3600
        base = str(folder / base_name(ts))
        kind = ("image", "carousel", "video")[n % 3]
        if kind == "carousel":
            for i in (1, 2, 3):
                noise_png(f"{base}_{i}.jpg", n * 10 + i)
            node = post_node(f"P{n:05d}", ts, who, f"post {n} #tag{n % 5}", "GraphSidecar",
                             children=[False, False, False])
        else:
            noise_png(base + ".jpg", n * 10)
            if kind == "video":
                with open(base + ".mp4", "wb") as f:            # each one different
                    f.write(b"\x00\x00\x00\x18ftypmp42" + n.to_bytes(4, "big") + b"\x00" * 60)
            node = post_node(f"P{n:05d}", ts, who, f"post {n}", "GraphVideo" if kind == "video" else "GraphImage")
        write_meta(base, node)


def duplicates_of(media):
    """One of each kind of duplicate: a copy in a typo'd folder, a repost
    sharing a file, a resized repost."""
    shutil.copytree(media / "alice.example", media / "alice.examplee")
    bob, carol = ACCOUNTS[1], ACCOUNTS[2]
    base = str(media / "carol" / base_name(TS - 3600))
    shutil.copyfile(str(media / "alice.example" / base_name(TS)) + ".jpg", base + ".jpg")
    write_meta(base, post_node("REPOST1", TS - 3600, carol, "repost"))
    base = str(media / "bob.example" / base_name(TS - 7200))
    noise_png(base + ".jpg", 30, scale=3)                       # post 3's picture, resized
    write_meta(base, post_node("RESIZED1", TS - 7200, bob, "resized"))


def index(env):
    scanner.scan(env["roots"])
    hashing.run_pass(db.connect())


class Statements:
    """The SQL statements run on this thread's connection: the ones the
    code runs, not what SQLite runs inside them ("-- ..." lines, such as
    FTS5 reading its own index, which grows with it)."""

    def __init__(self):
        self.sql = []

    def _add(self, sql):
        if not sql.startswith("-- "):
            self.sql.append(sql)

    def __enter__(self):
        db.connect().set_trace_callback(self._add)
        return self

    def __exit__(self, *exc):
        db.connect().set_trace_callback(None)


HOT = [
    "/api/posts?limit=60", "/api/posts?limit=60&q=post", "/api/posts?limit=60&review=unreviewed",
    "/api/posts?limit=60&untagged=1", "/api/posts?limit=60&author=111&platform=instagram",
    "/api/posts?limit=60&offset=30", "/api/posts/summary", "/api/new", "/api/authors", "/api/people",
    "/api/stats", "/api/storage", "/api/tags", "/api/collections", "/api/links", "/api/trash",
    "/api/duplicates?kind=copies", "/api/duplicates?kind=content", "/api/duplicates?kind=similar",
]


def statements(client, url):
    db._cache.clear()                           # what a request does after any change (db._memo)
    with Statements() as s:
        r = client.get(url, headers={"X-FeedVault": "1"})
    assert r.status_code == 200, (url, r.status_code)
    return len(s.sql)


def test_hot_requests_do_not_grow_with_the_archive(env, client):
    """The same requests on an archive four times as large run the same
    number of statements: no query per post, per file or per row."""
    write_posts(env["media"], 0, 60)
    duplicates_of(env["media"])
    index(env)
    small = {url: statements(client, url) for url in HOT}
    write_posts(env["media"], 60, 180)
    index(env)
    large = {url: statements(client, url) for url in HOT}
    assert large == small
    # A page of the feed is a fixed number of statements, whatever its size.
    assert statements(client, "/api/posts?limit=10") == statements(client, "/api/posts?limit=200")


def test_a_feed_page_is_what_one_post_at_a_time_gave(env):
    write_posts(env["media"], 0, 30)
    v = env["media"] / "alice.example" / "loose"                 # a video without a poster
    v.mkdir()
    fake_video(str(v / (base_name(TS - 99) + ".mp4")))
    write_meta(str(v / base_name(TS - 99)), post_node("NOPOSTER", TS - 99, ACCOUNTS[0], "", "GraphVideo"))
    index(env)
    conn = db.connect()
    conn.execute("UPDATE media SET missing = 1 WHERE id IN (SELECT id FROM media ORDER BY id LIMIT 3)")
    rows = conn.execute(f"{db._SELECT} ORDER BY p.posted_at").fetchall()
    assert db.summaries(conn, rows) == [db.summary(conn, r) for r in rows]
    assert any(s["cover"] and s["cover"]["kind"] == "video" for s in db.summaries(conn, rows))


def test_content_duplicates_use_their_index(env):
    """Only the digests more than one file has are read (migration 22's
    media_hash_full), not every hashed file."""
    plan = " ".join(r[3] for r in db.connect().execute("EXPLAIN QUERY PLAN " + duplicates.CONTENT_SQL))
    assert "media_hash_full" in plan
    assert "SCAN media_hash" not in plan and "SCAN h" not in plan


def test_similar_pairs_are_found_once_for_the_same_pictures(monkeypatch):
    calls = []
    real = duplicates.near_pairs_uncached
    monkeypatch.setattr(duplicates, "near_pairs_uncached", lambda v, t: calls.append(t) or real(v, t))
    monkeypatch.setattr(duplicates, "_near", {})
    rng = random.Random(5)
    values = [rng.getrandbits(64) for _ in range(500)]
    values[1] = values[0] ^ 0b111
    first = duplicates.near_pairs(values, 6)
    assert (0, 1) in first and duplicates.near_pairs(list(values), 6) is first
    assert calls == [6]
    duplicates.near_pairs(values, 4)
    values[2] = values[0] ^ 1
    assert (0, 2) in duplicates.near_pairs(values, 6)
    assert calls == [6, 4, 6]


class Counted(list):
    """A folder's file list that counts the passes made over it."""
    passes = 0

    def __iter__(self):
        Counted.passes += 1
        return super().__iter__()


def test_a_folder_is_read_a_fixed_number_of_times(tmp_path):
    """instaloader's parser goes over a folder's names a fixed number of
    times, not once per post (which made a rescan quadratic in the size of
    a profile's folder)."""
    write_posts(tmp_path, 0, 300)
    folder = tmp_path / "alice.example"
    Counted.passes = 0
    result = instaloader.parse_dir(str(tmp_path), str(folder), Counted(os.listdir(folder)))
    assert len(result.posts) == 100 and Counted.passes <= 10
    assert all(p.media for p in result.posts)


def old_media_names(base, names):
    """What _media_for matched before _media_index: {slot: {names}}."""
    pattern = re.compile(re.escape(base) + r"(?:_(\d+))?\.([A-Za-z0-9]+)$")
    slots = {}
    for n in names:
        m = pattern.fullmatch(n)
        if m and is_media(n):
            slots.setdefault(int(m.group(1)) if m.group(1) else 1, set()).add(n)
    return slots


@pytest.mark.parametrize("seed", range(5))
def test_media_index_matches_what_the_pattern_did(seed):
    rng = random.Random(seed)
    parts = ["a", "b", "_", "1", "2", "0", "٣", "²", ".", "jpg", "mp4", "JPG", "x", "\n", "é"]
    for _ in range(400):
        names = ["".join(rng.choice(parts) for _ in range(rng.randint(1, 7))) for _ in range(10)]
        names += [rng.choice(names) + rng.choice(["_1", "_12", "_٣", ""])
                  + rng.choice([".jpg", ".mp4", ".png", ".jpg\n", ".j-g"]) for _ in range(8)]
        names = list(dict.fromkeys(names))
        index = instaloader._media_index(names)
        for base in {n.rpartition(".")[0] for n in names} | {n.rpartition("_")[0] for n in names} | {"a", ""}:
            got = {}
            for idx, n in index.get(base, ()):
                got.setdefault(idx, set()).add(n)
            assert got == old_media_names(base, names), (base, names)
