"""The SQLite index.

Everything here is derived from the media folders and can be rebuilt by a
rescan, except the user's own tables (review decisions, later tags and
people). Those are mirrored to JSON files by userdata.py so a rebuild can
restore them.
"""
import glob
import json
import os
import sqlite3
import threading

import thumbs

# Version 1: the schema as first released. Its CREATE statements are kept as
# they were; every later change is a new function in MIGRATIONS below.
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    n             INTEGER PRIMARY KEY,       -- stable rowid, shared with posts_fts
    id            TEXT NOT NULL UNIQUE,      -- platform:post_id
    platform      TEXT NOT NULL,
    post_id       TEXT NOT NULL,
    url           TEXT,
    kind          TEXT NOT NULL,
    author_id     TEXT,
    author_handle TEXT,
    author_name   TEXT,
    posted_at     INTEGER,
    saved_at      INTEGER NOT NULL,          -- first time a scan saw it
    text          TEXT NOT NULL DEFAULT '',
    likes         INTEGER,
    comments      INTEGER,
    views         INTEGER,
    location      TEXT,
    album         TEXT,                      -- highlight title, collection
    hashtags      TEXT NOT NULL DEFAULT '[]',
    tool          TEXT NOT NULL,
    tool_version  TEXT,
    meta_path     TEXT NOT NULL UNIQUE,
    meta_mtime    REAL,
    meta_size     INTEGER,
    missing       INTEGER NOT NULL DEFAULT 0,
    indexed_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_posted ON posts(posted_at DESC);
CREATE INDEX IF NOT EXISTS posts_saved  ON posts(saved_at DESC);
CREATE INDEX IF NOT EXISTS posts_author ON posts(platform, author_id);

CREATE TABLE IF NOT EXISTS media (
    id          INTEGER PRIMARY KEY,
    post_id     TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL UNIQUE,
    poster_path TEXT,
    size        INTEGER,
    missing     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS media_post ON media(post_id, idx);

CREATE TABLE IF NOT EXISTS unmatched (
    path   TEXT PRIMARY KEY,
    size   INTEGER,
    mtime  REAL,
    reason TEXT NOT NULL
);

-- Review decisions are the user's own data, not derived from disk: rescans
-- never touch this table, and it is mirrored to decisions.json.
CREATE TABLE IF NOT EXISTS decisions (
    post_id  TEXT PRIMARY KEY,
    decision TEXT NOT NULL,                  -- "keep"
    at       INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    id UNINDEXED, text, author_handle, author_name, album,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


class SchemaTooNew(RuntimeError):
    """The database was written by a newer FeedVault than this one."""


def _statements(sql):
    """Split a script into statements. executescript() would commit halfway
    through a migration, so migrations run statement by statement."""
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _migrate_1(conn):
    for stmt in _statements(SCHEMA_V1):
        conn.execute(stmt)


# Ordered: MIGRATIONS[i] takes a database from version i to version i + 1.
# Append only; never edit one that has shipped.
MIGRATIONS = [_migrate_1]

BACKUPS_KEPT = 3

_local = threading.local()
# Held by a scan for its whole run and by deletions, so a scan never re-adds a
# post that is halfway through being deleted.
write_lock = threading.Lock()
_path = None


def init(path):
    """Open (and create or migrate) the database at ``path`` for this process."""
    global _path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    migrate(path)
    _path = path
    _local.__dict__.clear()
    connect()


def schema_version(conn):
    """PRAGMA user_version, except for databases made before migrations
    existed: those say 0 there and keep their version in the meta table."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone():
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        version = int(row[0]) if row and str(row[0]).isdigit() else 0
    return version


def migrate(path):
    """Bring the file at ``path`` up to len(MIGRATIONS), one transaction per step.

    An existing database is copied to ``<path>.bak-v<old>`` first. A failed
    step rolls back, so the file stays at the last version that succeeded.
    """
    target = len(MIGRATIONS)
    # Autocommit mode: transactions below are opened and closed explicitly.
    # foreign_keys stays off here, as SQLite recommends for table rebuilds.
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        current = schema_version(conn)
        if current > target:
            raise SchemaTooNew(
                f"{path} is at schema version {current}, but this FeedVault only knows "
                f"up to {target}. It was opened by a newer FeedVault; update this one, "
                f"or restore a backup ({os.path.basename(path)}.bak-v*).")
        if current == 1 and conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            # Made before migrations. Older builds re-ran the v1 script on
            # every start, so re-run it once (it only adds what is missing,
            # e.g. the decisions table on a very early file), then stamp it.
            _step(conn, _migrate_1, 1)
        if current == target:
            return
        if current > 0:
            backup(conn, f"{path}.bak-v{current}")
        for version in range(current, target):
            _step(conn, MIGRATIONS[version], version + 1)
            print(f"[db] Schema migrated to version {version + 1}")
    finally:
        conn.close()


def _step(conn, fn, version):
    """Run one migration and record ``version``, all in one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        fn(conn)
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                     (str(version),))
        conn.execute(f"PRAGMA user_version = {version}")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:                # some errors already rolled back
            conn.execute("ROLLBACK")
        raise


def backup(conn, dest):
    """Consistent copy through SQLite's backup API (safe with a WAL file
    alongside), then prune all but the newest BACKUPS_KEPT copies."""
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
    finally:
        out.close()
    print(f"[db] Backup before migrating → {dest}")
    base = dest.rsplit(".bak-v", 1)[0]
    old = sorted(glob.glob(glob.escape(base) + ".bak-v*"), key=os.path.getmtime, reverse=True)
    for stale in old[BACKUPS_KEPT:]:
        os.remove(stale)


def connect():
    """One connection per thread, reused."""
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != _path:
        conn = sqlite3.connect(_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _local.path = _path
    return conn


# ---------------------------------------------------------------------------
# Writes (used by the scanner)
# ---------------------------------------------------------------------------

def upsert_post(conn, p, meta_mtime, meta_size, now):
    """Insert or refresh one parsed post and its media. Returns "added" or "updated"."""
    row = conn.execute("SELECT saved_at FROM posts WHERE id = ?", (p.id,)).fetchone()
    saved_at = min(row["saved_at"], int(meta_mtime)) if row else int(meta_mtime or now)
    values = dict(
        id=p.id, platform=p.platform, post_id=p.post_id, url=p.url, kind=p.kind,
        author_id=p.author_id, author_handle=p.author_handle, author_name=p.author_name,
        posted_at=p.posted_at, saved_at=saved_at, text=p.text or "",
        likes=p.likes, comments=p.comments, views=p.views, location=p.location, album=p.album,
        hashtags=json.dumps(p.hashtags), tool=p.tool, tool_version=p.tool_version,
        meta_path=p.meta_path, meta_mtime=meta_mtime, meta_size=meta_size,
        missing=0, indexed_at=now,
    )
    cols = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    updates = ", ".join(f"{k} = excluded.{k}" for k in values if k != "id")
    conn.execute(f"INSERT INTO posts ({cols}) VALUES ({marks}) "
                 f"ON CONFLICT(id) DO UPDATE SET {updates}", values)

    # FTS rows share the post's rowid; looking them up by the UNINDEXED id
    # column would scan the whole table for every post.
    rowid = conn.execute("SELECT n FROM posts WHERE id = ?", (p.id,)).fetchone()[0]
    conn.execute("DELETE FROM posts_fts WHERE rowid = ?", (rowid,))
    conn.execute("INSERT INTO posts_fts(rowid, id, text, author_handle, author_name, album) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (rowid, p.id, p.text or "", p.author_handle or "", p.author_name or "", p.album or ""))

    # Upsert media by path so ids (and the URLs built from them) stay stable
    # across rescans; drop rows for files the post no longer lists.
    keep = []
    for m in p.media:
        try:
            size = os.path.getsize(m.path)
        except OSError:
            size = None
        conn.execute(
            "INSERT INTO media(post_id, idx, kind, path, poster_path, size, missing) "
            "VALUES (?, ?, ?, ?, ?, ?, 0) "
            "ON CONFLICT(path) DO UPDATE SET post_id = excluded.post_id, idx = excluded.idx, "
            "kind = excluded.kind, poster_path = excluded.poster_path, size = excluded.size, missing = 0",
            (p.id, m.idx, m.kind, m.path, m.poster_path, size),
        )
        keep.append(m.path)
    marks = ", ".join("?" for _ in keep)
    conn.execute(f"DELETE FROM media WHERE post_id = ? AND path NOT IN ({marks or 'NULL'})",
                 (p.id, *keep))
    return "updated" if row else "added"


# ---------------------------------------------------------------------------
# Reads (used by the API)
# ---------------------------------------------------------------------------

def _cover(conn, post_id):
    """Grid cover: a thumbnail of the first media item.

    A video with no poster file only gets a thumbnail if ffmpeg can make one;
    otherwise the cover is the video itself and ``poster`` is False.
    """
    m = conn.execute("SELECT id, kind, poster_path FROM media WHERE post_id = ? "
                     "ORDER BY idx LIMIT 1", (post_id,)).fetchone()
    if m is None:
        return None
    if m["kind"] == "video":
        if m["poster_path"] or thumbs.have_ffmpeg():
            return {"kind": "video", "url": f"/media/{m['id']}/thumb", "poster": True}
        return {"kind": "video", "url": f"/media/{m['id']}", "poster": False}
    return {"kind": "image", "url": f"/media/{m['id']}/thumb"}


def summary(conn, row):
    count = conn.execute("SELECT COUNT(*) FROM media WHERE post_id = ?", (row["id"],)).fetchone()[0]
    return {
        "id": row["id"],
        "platform": row["platform"],
        "post_id": row["post_id"],
        "url": row["url"],
        "kind": row["kind"],
        "author": {"id": row["author_id"], "handle": row["author_handle"], "name": row["author_name"]},
        "posted_at": row["posted_at"],
        "saved_at": row["saved_at"],
        "text": row["text"],
        "stats": {"likes": row["likes"], "comments": row["comments"], "views": row["views"]},
        "media_count": count,
        "cover": _cover(conn, row["id"]),
        "missing": bool(row["missing"]),
        "decision": row["decision"] if "decision" in row.keys() else None,
    }


def full(conn, row):
    out = summary(conn, row)
    out["media"] = [
        {
            "id": m["id"], "idx": m["idx"], "kind": m["kind"],
            "url": f"/media/{m['id']}",
            "poster_url": f"/media/{m['id']}/poster" if m["poster_path"] else None,
            "thumb_url": f"/media/{m['id']}/thumb",
            "size": m["size"], "missing": bool(m["missing"]),
        }
        for m in conn.execute("SELECT * FROM media WHERE post_id = ? ORDER BY idx", (row["id"],))
    ]
    out["location"] = row["location"]
    out["album"] = row["album"]
    out["hashtags"] = json.loads(row["hashtags"] or "[]")
    out["source"] = {"tool": row["tool"], "version": row["tool_version"], "meta_path": row["meta_path"]}
    return out


def fts_query(q):
    """Turn free text into a safe FTS5 query: every word must match, the last
    one as a prefix so results update while typing."""
    words = [w for w in "".join(c if c.isalnum() or c in "_" else " " for c in q).split() if w]
    if not words:
        return None
    terms = [f'"{w}"' for w in words]
    terms[-1] += "*"
    return " AND ".join(terms)


_SELECT = ("SELECT p.*, d.decision AS decision FROM posts p "
           "LEFT JOIN decisions d ON d.post_id = p.id")


def list_posts(conn, q=None, platform=None, author=None, kind=None, sort="posted",
               offset=0, limit=60, review=None, order="desc"):
    where, args = [], []
    if q:
        match = fts_query(q)
        if match is None:
            return 0, []
        where.append("p.n IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?)")
        args.append(match)
    if platform:
        where.append("p.platform = ?")
        args.append(platform)
    if author:
        where.append("p.author_id = ?")
        args.append(author)
    if kind:
        where.append("p.kind = ?")
        args.append(kind)
    if review == "unreviewed":
        where.append("d.post_id IS NULL")
    elif review == "kept":
        where.append("d.decision = 'keep'")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    direction = "ASC" if order == "asc" else "DESC"
    first, second = ("saved_at", "posted_at") if sort == "saved" else ("posted_at", "saved_at")
    order_by = f"p.{first} {direction}, p.{second} {direction}, p.id {direction}"
    total = conn.execute(f"SELECT COUNT(*) FROM posts p LEFT JOIN decisions d ON d.post_id = p.id {clause}",
                         args).fetchone()[0]
    rows = conn.execute(f"{_SELECT} {clause} ORDER BY {order_by} LIMIT ? OFFSET ?",
                        (*args, limit, offset)).fetchall()
    return total, [summary(conn, r) for r in rows]


def get_post(conn, platform, post_id):
    row = conn.execute(f"{_SELECT} WHERE p.platform = ? AND p.post_id = ?",
                       (platform, post_id)).fetchone()
    return full(conn, row) if row else None


def set_decision(conn, post_ids, decision, now):
    """Mark posts kept, or clear their decision. Returns the ids that exist."""
    if not post_ids:
        return []
    ids = [r[0] for r in conn.execute(
        f"SELECT id FROM posts WHERE id IN ({', '.join('?' for _ in post_ids)})", post_ids)]
    if decision is None:
        conn.executemany("DELETE FROM decisions WHERE post_id = ?", [(i,) for i in ids])
    else:
        conn.executemany("INSERT OR REPLACE INTO decisions(post_id, decision, at) VALUES (?, ?, ?)",
                         [(i, decision, now) for i in ids])
    conn.commit()
    return ids


def authors(conn):
    # Handle and name come from the most recent post, since handles change.
    rows = conn.execute("""
        SELECT platform, author_id, COUNT(*) AS count,
               (SELECT author_handle FROM posts p2 WHERE p2.platform = p.platform
                  AND p2.author_id = p.author_id ORDER BY posted_at DESC LIMIT 1) AS handle,
               (SELECT author_name FROM posts p2 WHERE p2.platform = p.platform
                  AND p2.author_id = p.author_id ORDER BY posted_at DESC LIMIT 1) AS name
        FROM posts p WHERE author_id IS NOT NULL
        GROUP BY platform, author_id
        ORDER BY count DESC, handle
    """).fetchall()
    return [{"platform": r["platform"], "id": r["author_id"], "handle": r["handle"],
             "name": r["name"], "count": r["count"]} for r in rows]


def stats(conn):
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "posts": one("SELECT COUNT(*) FROM posts"),
        "media": one("SELECT COUNT(*) FROM media"),
        "authors": one("SELECT COUNT(DISTINCT platform || ':' || author_id) FROM posts "
                       "WHERE author_id IS NOT NULL"),
        "bytes": one("SELECT COALESCE(SUM(size), 0) FROM media"),
        "missing": one("SELECT COUNT(*) FROM posts WHERE missing = 1"),
        "kept": one("SELECT COUNT(*) FROM decisions d JOIN posts p ON p.id = d.post_id "
                    "WHERE d.decision = 'keep'"),
        "unreviewed": one("SELECT COUNT(*) FROM posts p LEFT JOIN decisions d ON d.post_id = p.id "
                          "WHERE d.post_id IS NULL"),
        "unmatched": one("SELECT COUNT(*) FROM unmatched"),
        "by_platform": dict(conn.execute("SELECT platform, COUNT(*) FROM posts GROUP BY platform").fetchall()),
        "by_kind": dict(conn.execute("SELECT kind, COUNT(*) FROM posts GROUP BY kind").fetchall()),
    }


def unmatched(conn):
    return [dict(r) for r in conn.execute("SELECT path, size, mtime, reason FROM unmatched ORDER BY path")]


def saved_ids(conn, ids):
    ids = [i for i in ids if isinstance(i, str)][:500]
    if not ids:
        return []
    marks = ", ".join("?" for _ in ids)
    return [r[0] for r in conn.execute(f"SELECT id FROM posts WHERE id IN ({marks})", ids)]


def media_row(conn, media_id):
    return conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()


def remove_post(conn, post_id):
    """Drop a post, its media rows and its search entry from the index."""
    row = conn.execute("SELECT n FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM posts_fts WHERE rowid = ?", (row["n"],))
    conn.execute("DELETE FROM media WHERE post_id = ?", (post_id,))
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.execute("DELETE FROM decisions WHERE post_id = ?", (post_id,))
