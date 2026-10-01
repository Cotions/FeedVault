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


def _migrate_2(conn):
    """Storage view: size sums per post read from the index alone, and an
    index for the largest files. media_post is widened rather than joined by
    a second index on post_id, which slowed a first scan by another 10%."""
    conn.execute("DROP INDEX IF EXISTS media_post")
    conn.execute("CREATE INDEX IF NOT EXISTS media_post_size ON media(post_id, idx, missing, size)")
    # Not partial (WHERE missing = 0): the planner then picked it for every
    # missing = 0 filter, ten times slower than media_post_size.
    conn.execute("CREATE INDEX IF NOT EXISTS media_size ON media(size)")


def _migrate_3(conn):
    """Duplicates: the same post downloaded again into another folder. Only
    the first copy becomes a post; every other one is recorded here, with its
    files, so it can be compared and trashed. Derived from disk like posts:
    a rescan drops a copy whose metadata file is gone."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS copies (
            id         INTEGER PRIMARY KEY,
            post_id    TEXT NOT NULL,              -- the indexed post it is another copy of
            meta_path  TEXT NOT NULL UNIQUE,
            meta_mtime REAL,
            media      TEXT NOT NULL DEFAULT '[]', -- JSON [{idx, kind, path, poster_path, size}]
            first_seen INTEGER NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS copies_post ON copies(post_id)")


def _migrate_4(conn):
    """Exact duplicates by content. media_hash is a cache filled by the
    background worker in hashing.py, valid while a file's size and mtime are
    unchanged. dismissed_duplicates is user data ("not a duplicate"), never
    touched by scans and mirrored to JSON by userdata.py."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media_hash (
            path      TEXT PRIMARY KEY,
            size      INTEGER NOT NULL,
            mtime_ns  INTEGER NOT NULL,
            partial   TEXT NOT NULL,                -- sha1 of the first and last MiB
            full      TEXT,                         -- sha1 of the whole file, when needed
            width     INTEGER,                      -- images: read from the header while hashing
            height    INTEGER,
            hashed_at INTEGER NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dismissed_duplicates (
            key  TEXT PRIMARY KEY,                  -- JSON list, sorted: the group's members
            kind TEXT NOT NULL,                     -- copies | content
            at   INTEGER NOT NULL
        )""")


# Ordered: MIGRATIONS[i] takes a database from version i to version i + 1.
# Append only; never edit one that has shipped.
MIGRATIONS = [_migrate_1, _migrate_2, _migrate_3, _migrate_4]

BACKUPS_KEPT = 3

_local = threading.local()
# (path, key) -> (data_version, value); see _memo.
_cache = {}
_cache_lock = threading.Lock()
CACHE_MAX = 128
_watch = None                                 # (path, connection) that only reads data_version
_watch_lock = threading.Lock()
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
    with _cache_lock:
        _cache.clear()
    connect()


def _data_version():
    """Changes whenever any other connection commits to the database. Read
    on a connection of its own that never writes, since request threads come
    and go and each opens its own."""
    global _watch
    with _watch_lock:
        if _watch is None or _watch[0] != _path:
            if _watch:
                _watch[1].close()
            _watch = (_path, sqlite3.connect(_path, check_same_thread=False))
        return _watch[1].execute("PRAGMA data_version").fetchone()[0]


def schema_version(conn):
    """PRAGMA user_version, except for databases made before migrations
    existed: those say 0 there and keep their version in the meta table."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone():
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        # A meta table without a version row reads as 0, so migrate() treats
        # the file as new: no backup, and MIGRATIONS[0] runs on it. That is
        # safe only because _migrate_1 is all CREATE IF NOT EXISTS; keep it so.
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


def save_copies(conn, found, now, prune):
    """Record extra copies of indexed posts: ``found`` is [(parsed post,
    meta mtime)]. Rows are keyed by metadata path, so ids and first-seen
    times survive rescans. ``prune`` (a full scan) drops every copy not found
    this time: its files are gone, or it became the indexed post."""
    paths = []
    for p, mtime in found:
        media = []
        for m in p.media:
            try:
                size = os.path.getsize(m.path)
            except OSError:
                size = None
            media.append({"idx": m.idx, "kind": m.kind, "path": m.path,
                          "poster_path": m.poster_path, "size": size})
        conn.execute(
            "INSERT INTO copies(post_id, meta_path, meta_mtime, media, first_seen) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(meta_path) DO UPDATE SET post_id = excluded.post_id, "
            "meta_mtime = excluded.meta_mtime, media = excluded.media",
            (p.id, p.meta_path, mtime, json.dumps(media), now))
        paths.append(p.meta_path)
    if prune:
        keep = set(paths)
        gone = [(r[0],) for r in conn.execute("SELECT meta_path FROM copies") if r[0] not in keep]
        conn.executemany("DELETE FROM copies WHERE meta_path = ?", gone)


def copy_row(conn, copy_id):
    """A copy with its media list decoded, or None."""
    row = conn.execute("SELECT * FROM copies WHERE id = ?", (copy_id,)).fetchone()
    return {**dict(row), "media": json.loads(row["media"])} if row else None


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
    count, size = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN missing = 0 THEN size END), 0) FROM media "
        "WHERE post_id = ?", (row["id"],)).fetchone()
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
        "bytes": size,
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


_FROM = "FROM posts p LEFT JOIN decisions d ON d.post_id = p.id"
_SELECT = f"SELECT p.*, d.decision AS decision {_FROM}"


def _memo(conn, key, compute):
    """``compute(conn)``, cached until anything is committed to the database
    (a scan, a delete, a restore, a decision), whichever code path wrote it.

    The version is read before the read transaction starts, so a commit
    landing in between can only make the cached value newer than its label,
    never older. Callers must not modify the result.
    """
    key = (_path, key)
    own = not conn.in_transaction
    gen = _data_version()
    with _cache_lock:
        hit = _cache.get(key)
    if own and hit and hit[0] == gen:
        return hit[1]
    if own:
        conn.execute("BEGIN")
    try:
        value = compute(conn)
    finally:
        if own:
            conn.commit()
    if own:                                   # never cache what an open write may still roll back
        with _cache_lock:
            if len(_cache) >= CACHE_MAX:
                _cache.clear()
            _cache[key] = (gen, value)
    return value


def post_filter(q=None, platform=None, author=None, kind=None, review=None):
    """WHERE clause and arguments for the /api/posts filters, over _FROM.
    None when the search text can match nothing. Shared by list_posts and
    post_summary so a count and its size can never disagree."""
    where, args = [], []
    if q:
        match = fts_query(q)
        if match is None:
            return None
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
    return ("WHERE " + " AND ".join(where)) if where else "", args


def list_posts(conn, q=None, platform=None, author=None, kind=None, sort="posted",
               offset=0, limit=60, review=None, order="desc"):
    f = post_filter(q, platform, author, kind, review)
    if f is None:
        return 0, []
    clause, args = f
    direction = "ASC" if order == "asc" else "DESC"
    first, second = ("saved_at", "posted_at") if sort == "saved" else ("posted_at", "saved_at")
    order_by = f"p.{first} {direction}, p.{second} {direction}, p.id {direction}"
    total = conn.execute(f"SELECT COUNT(*) {_FROM} {clause}", args).fetchone()[0]
    rows = conn.execute(f"{_SELECT} {clause} ORDER BY {order_by} LIMIT ? OFFSET ?",
                        (*args, limit, offset)).fetchall()
    return total, [summary(conn, r) for r in rows]


def post_summary(conn, q=None, platform=None, author=None, kind=None, review=None):
    """Posts, media and bytes matched by the /api/posts filters, all pages."""
    f = post_filter(q, platform, author, kind, review)
    if f is None:
        return {"posts": 0, "media": 0, "bytes": 0}
    clause, args = f

    def compute(conn):
        posts = conn.execute(f"SELECT COUNT(*) {_FROM} {clause}", args).fetchone()[0]
        media, size = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM media WHERE missing = 0 "
            f"AND post_id IN (SELECT p.id {_FROM} {clause})", args).fetchone()
        return {"posts": posts, "media": media, "bytes": size}

    return _memo(conn, ("summary", clause, tuple(args)), compute)


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


# Handle and name come from the most recent post, since handles change.
_NEWEST_NAMES = """
    (SELECT author_handle FROM posts p2 WHERE p2.platform = p.platform
       AND p2.author_id = p.author_id ORDER BY posted_at DESC LIMIT 1) AS handle,
    (SELECT author_name FROM posts p2 WHERE p2.platform = p.platform
       AND p2.author_id = p.author_id ORDER BY posted_at DESC LIMIT 1) AS name"""


def authors(conn):
    return _memo(conn, ("authors",), _authors)


def _authors(conn):
    rows = conn.execute(f"""
        SELECT platform, author_id, COUNT(*) AS count, {_NEWEST_NAMES},
               (SELECT COALESCE(SUM(m.size), 0) FROM posts p3
                  JOIN media m ON m.post_id = p3.id AND m.missing = 0
                  WHERE p3.platform = p.platform AND p3.author_id = p.author_id) AS bytes
        FROM posts p WHERE author_id IS NOT NULL
        GROUP BY platform, author_id
        ORDER BY count DESC, handle
    """).fetchall()
    return [{"platform": r["platform"], "id": r["author_id"], "handle": r["handle"],
             "name": r["name"], "count": r["count"], "bytes": r["bytes"]} for r in rows]


LARGEST = 100


def storage(conn):
    """Disk use by creator, kind and year, and the largest files. Media
    marked missing are left out. The trash total is added by the caller."""
    return _memo(conn, ("storage",), _storage)


def _storage(conn):
    # One pass over the posts, grouped as finely as any table below needs and
    # rolled up here: three separate GROUP BY queries took three times as long.
    rows = conn.execute(f"""
        SELECT p.platform, p.author_id, p.kind,
               CAST(strftime('%Y', p.posted_at, 'unixepoch') AS INTEGER) AS year,
               d.decision, COUNT(*) AS posts,
               COALESCE(SUM(pm.media), 0) AS media, COALESCE(SUM(pm.bytes), 0) AS bytes
        {_FROM}
        LEFT JOIN (SELECT post_id, COUNT(*) AS media, SUM(size) AS bytes FROM media
                   WHERE missing = 0 GROUP BY post_id) pm ON pm.post_id = p.id
        GROUP BY 1, 2, 3, 4, 5
    """).fetchall()

    def bucket(table, key, **extra):
        if key not in table:
            table[key] = {**extra, "posts": 0, "media": 0, "bytes": 0}
        return table[key]

    totals = {"posts": 0, "media": 0, "bytes": 0}
    by_author, by_kind, by_year = {}, {}, {}
    # Not authors(): its byte sums would add half again to a cold load.
    names = {(r["platform"], r["author_id"]): r for r in conn.execute(f"""
        SELECT platform, author_id, {_NEWEST_NAMES} FROM posts p
        WHERE author_id IS NOT NULL GROUP BY platform, author_id""")}
    for r in rows:
        targets = [totals, bucket(by_kind, r["kind"], kind=r["kind"]),
                   bucket(by_year, r["year"], year=r["year"])]
        if r["author_id"] is not None:
            a = names[(r["platform"], r["author_id"])]
            row = bucket(by_author, (r["platform"], r["author_id"]),
                         platform=r["platform"], id=r["author_id"], handle=a["handle"], name=a["name"])
            row.setdefault("kept_bytes", 0)
            row.setdefault("unreviewed_bytes", 0)
            if r["decision"] == "keep":
                row["kept_bytes"] += r["bytes"]
            elif r["decision"] is None:
                row["unreviewed_bytes"] += r["bytes"]
            targets.append(row)
        for t in targets:
            t["posts"] += r["posts"]
            t["media"] += r["media"]
            t["bytes"] += r["bytes"]

    largest = []
    for m in conn.execute("""
            SELECT m.id, m.post_id, m.kind, m.size, m.poster_path,
                   p.platform, p.post_id AS short_id, p.author_id, p.author_handle
            FROM media m JOIN posts p ON p.id = m.post_id
            WHERE m.missing = 0 AND m.size IS NOT NULL
            ORDER BY m.size DESC LIMIT ?""", (LARGEST,)):
        thumb = m["kind"] != "video" or m["poster_path"] or thumbs.have_ffmpeg()
        largest.append({
            "media_id": m["id"], "post": m["post_id"], "platform": m["platform"],
            "post_id": m["short_id"], "author": {"id": m["author_id"], "handle": m["author_handle"]},
            "kind": m["kind"], "bytes": m["size"],
            "thumb_url": f"/media/{m['id']}/thumb" if thumb else None,
        })

    by_size = lambda t: sorted(t.values(), key=lambda r: -r["bytes"])  # noqa: E731
    return {
        "totals": totals,
        "by_author": by_size(by_author),
        "by_kind": by_size(by_kind),
        "by_year": sorted(by_year.values(), key=lambda r: (r["year"] is None, r["year"] or 0)),
        "largest": largest,
    }


def stats(conn):
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "posts": one("SELECT COUNT(*) FROM posts"),
        "media": one("SELECT COUNT(*) FROM media"),
        "authors": one("SELECT COUNT(DISTINCT platform || ':' || author_id) FROM posts "
                       "WHERE author_id IS NOT NULL"),
        "bytes": one("SELECT COALESCE(SUM(size), 0) FROM media WHERE missing = 0"),
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
