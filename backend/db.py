"""The SQLite index.

Everything here is derived from the media folders and can be rebuilt by a
rescan, except the user's own tables (review decisions, tags,
collections, people, sources). Those are mirrored to JSON files by
userdata.py so a rebuild can restore them.
"""
import glob
import json
import os
import re
import sqlite3
import string
import threading
import time

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


def _migrate_5(conn):
    """Visually similar media: a 64-bit dHash per image and video, in the
    same cache row as the content hashes (valid while size and mtime are
    unchanged). Every image and video gets a row now, not only files with a
    twin, so ``partial`` becomes optional: SQLite cannot drop NOT NULL in
    place, so the table is rebuilt, keeping what was hashed."""
    conn.execute("""
        CREATE TABLE media_hash_v5 (
            path      TEXT PRIMARY KEY,
            size      INTEGER NOT NULL,
            mtime_ns  INTEGER NOT NULL,
            partial   TEXT,                         -- sha1 of the first and last MiB, files that may have a twin
            full      TEXT,                         -- sha1 of the whole file, when needed
            width     INTEGER,                      -- images: header; videos: ffprobe, 0 when it failed
            height    INTEGER,
            hashed_at INTEGER NOT NULL,
            dhash     INTEGER,                      -- 64-bit difference hash (signed), NULL if unreadable
            dhash_at  INTEGER                       -- when the dHash was attempted; NULL: not yet
        )""")
    conn.execute("""
        INSERT INTO media_hash_v5(path, size, mtime_ns, partial, full, width, height, hashed_at)
        SELECT path, size, mtime_ns, partial, full, width, height, hashed_at FROM media_hash""")
    conn.execute("DROP TABLE media_hash")
    conn.execute("ALTER TABLE media_hash_v5 RENAME TO media_hash")


def _migrate_6(conn):
    """Tags: user data, never touched by scans and mirrored to JSON by
    userdata.py. Keyed by post id, not removed with the post, so a post
    restored from the trash finds its tags again (organize.py)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
            color      TEXT,
            created_at INTEGER NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS post_tags (
            post_id TEXT NOT NULL,
            tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            at      INTEGER NOT NULL,
            PRIMARY KEY (post_id, tag_id)
        ) WITHOUT ROWID""")
    conn.execute("CREATE INDEX IF NOT EXISTS post_tags_tag ON post_tags(tag_id, post_id)")


def _migrate_7(conn):
    """Collections: named, ordered sets of posts. User data with the same
    rules as tags (organize.py)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
            cover_post TEXT,                        -- a post id, or NULL: the first post
            created_at INTEGER NOT NULL,
            position   INTEGER NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collection_posts (
            collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            post_id       TEXT NOT NULL,
            position      INTEGER NOT NULL,
            at            INTEGER NOT NULL,
            PRIMARY KEY (collection_id, post_id)
        ) WITHOUT ROWID""")
    conn.execute("CREATE INDEX IF NOT EXISTS collection_posts_order ON collection_posts(collection_id, position)")
    conn.execute("CREATE INDEX IF NOT EXISTS collection_posts_post ON collection_posts(post_id)")


def _migrate_8(conn):
    """Side files: files a post owns besides its media, posters and metadata
    path (gallery-dl's other per-file JSONs, music, subtitles), as a JSON list
    of paths, so the trash can move them with the post."""
    conn.execute("ALTER TABLE posts ADD COLUMN side_files TEXT NOT NULL DEFAULT '[]'")
    conn.execute("ALTER TABLE copies ADD COLUMN side_files TEXT NOT NULL DEFAULT '[]'")


def _migrate_9(conn):
    """Jobs (jobs.py): the queue and the last HISTORY_KEPT jobs, each with
    the last lines of its output once it has ended. Operational data, not
    the user's: not mirrored by userdata.py, and losing it loses nothing."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            kind       TEXT NOT NULL,
            params     TEXT NOT NULL,              -- JSON object, as checked by the kind
            argv       TEXT NOT NULL,              -- JSON list, for display: the tool's name, not its path
            cwd        TEXT NOT NULL,
            lock_group TEXT NOT NULL,
            state      TEXT NOT NULL,              -- queued running done failed cancelled interrupted
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            ended_at   INTEGER,
            exit_code  INTEGER,
            rescan     TEXT,                       -- folder indexed when it exits 0, or NULL
            full_scan  INTEGER NOT NULL DEFAULT 0,
            result     TEXT,                       -- JSON object, or NULL
            message    TEXT,
            tail       TEXT NOT NULL DEFAULT '[]'  -- JSON [[n, text]], once ended
        )""")


def _migrate_10(conn):
    """People (people.py): accounts, a platform and an author id as indexed,
    linked into one person. people and person_accounts are user data, never
    touched by scans and mirrored to JSON by userdata.py; an account belongs
    to one person at most. account_aliases is derived on every scan: a
    folder-name author id (posts rebuilt from file names) that is the same
    account as a numeric id found in the same folder's metadata."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
            notes      TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS person_accounts (
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            platform  TEXT NOT NULL,
            author_id TEXT NOT NULL,
            at        INTEGER NOT NULL,
            PRIMARY KEY (platform, author_id)
        ) WITHOUT ROWID""")
    conn.execute("CREATE INDEX IF NOT EXISTS person_accounts_person ON person_accounts(person_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_aliases (
            platform  TEXT NOT NULL,
            alias_id  TEXT NOT NULL,                -- the folder name, as filename-only posts carry it
            author_id TEXT NOT NULL,                -- the account's id from metadata
            PRIMARY KEY (platform, alias_id)
        ) WITHOUT ROWID""")


def _migrate_11(conn):
    """Link suggestions (people.suggestions). profiles is derived on every
    scan: an account's bio and links as its metadata has them (instaloader's
    Profile file, gallery-dl's author dict). dismissed_suggestions is user
    data, mirrored to JSON by userdata.py."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            platform  TEXT NOT NULL,
            author_id TEXT NOT NULL,
            handle    TEXT,
            bio       TEXT NOT NULL DEFAULT '',
            urls      TEXT NOT NULL DEFAULT '[]',   -- JSON list
            at        INTEGER,                      -- how recent (newest wins)
            source    TEXT NOT NULL,                -- the metadata file
            PRIMARY KEY (platform, author_id)
        ) WITHOUT ROWID""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dismissed_suggestions (
            key TEXT PRIMARY KEY,                   -- JSON list, sorted: the accounts, "platform:id"
            at  INTEGER NOT NULL
        )""")


def _migrate_12(conn):
    """Sources (sources.py): where a person's or an account's posts are
    downloaded from, a tool and its target, and the folder it writes to. User
    data, mirrored to JSON by userdata.py; one source per tool and target.
    person_id is set for a source added to a person before any of its posts
    is indexed; once the source has an account, the account's person wins
    (see sources.owner)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id           INTEGER PRIMARY KEY,
            person_id    INTEGER REFERENCES people(id) ON DELETE SET NULL,
            platform     TEXT NOT NULL,
            author_id    TEXT,                         -- the account, as indexed, or NULL before its first post
            tool         TEXT NOT NULL,
            target       TEXT NOT NULL,                -- what the tool is given: a handle, a URL
            folder       TEXT NOT NULL,                -- absolute, inside a media root
            options      TEXT NOT NULL DEFAULT '{}',   -- JSON object, see sources.clean_options
            created_at   INTEGER NOT NULL,
            last_sync_at INTEGER,                      -- when the last sync ended
            last_job_id  INTEGER,
            last_result  TEXT,                         -- JSON {state, error, message, added}, or NULL
            UNIQUE (tool, target)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS sources_person ON sources(person_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS sources_account ON sources(platform, author_id)")


def _migrate_13(conn):
    """A running job's process (jobs.py): pid, start time in clock ticks since
    boot (field 22 of /proc/<pid>/stat) and executable, so the next start
    can stop a process a killed FeedVault left behind, and nothing else."""
    for column in ("pid INTEGER", "pid_start INTEGER", "pid_exe TEXT"):
        conn.execute(f"ALTER TABLE jobs ADD COLUMN {column}")


def _migrate_14(conn):
    """New posts (news.py). posts.first_seen: when the index first had the
    post, set on insert only (0: known before this, or found by the scan
    that built the index). seen_at: the user's one high-water mark, user
    data mirrored by userdata.py; a post is new when first_seen is after it.
    A database already in use starts with it at now, so its whole archive
    is not new; a new one starts without, for userdata.py to restore it."""
    conn.execute("ALTER TABLE posts ADD COLUMN first_seen INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS posts_first_seen ON posts(first_seen)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_at (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            at INTEGER NOT NULL
        )""")
    conn.execute("INSERT INTO seen_at(id, at) SELECT 1, ? WHERE EXISTS (SELECT 1 FROM posts)", (int(time.time()),))


def _migrate_15(conn):
    """Posts the Save button added (save.py), user data mirrored by
    userdata.py. Saved one by one, not synced, so they never seed an
    instaloader stamp (sync.trusted_newest), whatever folder they are in;
    an entry goes once a stamp later than its post exists. Filled from the
    Save jobs kept, for the saves made before."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saved_posts (
            post_id  TEXT PRIMARY KEY,
            saved_at INTEGER NOT NULL
        )""")
    # Saves made before: the Save jobs still kept (the last 100 jobs) that added their post.
    for result, at in conn.execute("SELECT result, COALESCE(ended_at, created_at) FROM jobs "
                                   "WHERE kind = 'instaloader-post' AND state = 'done'").fetchall():
        try:
            r = json.loads(result or "null")
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("post"), str) and isinstance(r.get("added"), int) \
                and r["added"] > 0:
            conn.execute("INSERT OR IGNORE INTO saved_posts(post_id, saved_at) VALUES (?, ?)", (r["post"], at))


def _migrate_16(conn):
    """Stable account ids (people.refresh_aliases). account_files is derived
    on every scan: instaloader's id files (parsers.AccountFile), whose
    folder is which account, under the handle it had then."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_files (
            path      TEXT PRIMARY KEY,             -- the id file
            platform  TEXT NOT NULL,
            author_id TEXT NOT NULL,
            handle    TEXT NOT NULL,                -- lowercase, as instaloader names folders
            at        INTEGER                       -- the file's mtime
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS account_files_account ON account_files(platform, author_id)")


def _migrate_17(conn):
    """Handle history (db._accounts). handle_renames is user data, mirrored
    by userdata.py: a new handle the user accepted for a source
    (sources.rename), the account's old and new handle and when."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS handle_renames (
            platform  TEXT NOT NULL,
            author_id TEXT NOT NULL,
            old       TEXT NOT NULL,
            new       TEXT NOT NULL,
            at        INTEGER NOT NULL,
            PRIMARY KEY (platform, author_id, old, new)
        ) WITHOUT ROWID""")


# Ordered: MIGRATIONS[i] takes a database from version i to version i + 1.
# Append only; never edit one that has shipped.
MIGRATIONS = [_migrate_1, _migrate_2, _migrate_3, _migrate_4, _migrate_5, _migrate_6, _migrate_7, _migrate_8,
              _migrate_9, _migrate_10, _migrate_11, _migrate_12,
              _migrate_13, _migrate_14, _migrate_15, _migrate_16, _migrate_17]

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

def upsert_post(conn, p, meta_mtime, meta_size, now, first_seen=None):
    """Insert or refresh one parsed post and its media. Returns "added" or
    "updated". ``first_seen`` (default ``now``) is kept on insert only: a
    post the index already has never becomes new again."""
    row = conn.execute("SELECT saved_at FROM posts WHERE id = ?", (p.id,)).fetchone()
    saved_at = min(row["saved_at"], int(meta_mtime)) if row else int(meta_mtime or now)
    values = dict(
        id=p.id, platform=p.platform, post_id=p.post_id, url=p.url, kind=p.kind,
        author_id=p.author_id, author_handle=p.author_handle, author_name=p.author_name,
        posted_at=p.posted_at, saved_at=saved_at, text=p.text or "",
        likes=p.likes, comments=p.comments, views=p.views, location=p.location, album=p.album,
        hashtags=json.dumps(p.hashtags), tool=p.tool, tool_version=p.tool_version,
        side_files=json.dumps(p.side_files),
        meta_path=p.meta_path, meta_mtime=meta_mtime, meta_size=meta_size,
        missing=0, indexed_at=now, first_seen=now if first_seen is None else first_seen,
    )
    cols = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    updates = ", ".join(f"{k} = excluded.{k}" for k in values if k not in ("id", "first_seen"))
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
            "INSERT INTO copies(post_id, meta_path, meta_mtime, media, side_files, first_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(meta_path) DO UPDATE SET post_id = excluded.post_id, "
            "meta_mtime = excluded.meta_mtime, media = excluded.media, side_files = excluded.side_files",
            (p.id, p.meta_path, mtime, json.dumps(media), json.dumps(p.side_files), now))
        paths.append(p.meta_path)
    if prune:
        keep = set(paths)
        gone = [(r[0],) for r in conn.execute("SELECT meta_path FROM copies") if r[0] not in keep]
        conn.executemany("DELETE FROM copies WHERE meta_path = ?", gone)


def save_profiles(conn, found, prune):
    """Record what metadata says about accounts (parsers.Profile), the most
    recent per account. ``prune`` (a full scan): ``found`` is all there is."""
    best = {}
    for p in found:
        key = (p.platform, p.author_id)
        if key not in best or (p.at or 0) >= (best[key].at or 0):
            best[key] = p
    rows = {k: (p.platform, p.author_id, p.handle, p.bio or "", json.dumps(p.urls), p.at, p.source)
            for k, p in best.items()}
    if prune:
        have = {(r[0], r[1]): tuple(r) for r in conn.execute(
            "SELECT platform, author_id, handle, bio, urls, at, source FROM profiles")}
        if have == rows:
            return                             # unchanged: keep the caches
        conn.execute("DELETE FROM profiles")
    else:
        # A rescanned folder: keep a more recent profile seen elsewhere.
        rows = {k: r for k, r in rows.items() if not conn.execute(
            "SELECT 1 FROM profiles WHERE platform = ? AND author_id = ? AND COALESCE(at, 0) > ?",
            (r[0], r[1], r[5] or 0)).fetchone()}
    conn.executemany("INSERT OR REPLACE INTO profiles(platform, author_id, handle, bio, urls, at, source) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", list(rows.values()))


def save_account_files(conn, found, prune, dirs=()):
    """Record the id files found (parsers.AccountFile). ``prune`` (a full
    scan): ``found`` is all there is; else ``dirs`` were read again, and an
    id file no longer in them is gone."""
    rows = {a.path: (a.path, a.platform, a.author_id, a.handle, a.at) for a in found}
    have = {r[0]: tuple(r) for r in conn.execute("SELECT path, platform, author_id, handle, at FROM account_files")}
    if prune:
        gone = [p for p in have if p not in rows]
    else:
        gone = [p for p in have if os.path.dirname(p) in dirs and p not in rows]
    rows = [r for p, r in rows.items() if have.get(p) != r]
    if gone or rows:                           # an unchanged index stays unchanged (and cached)
        conn.executemany("DELETE FROM account_files WHERE path = ?", [(p,) for p in gone])
        conn.executemany("INSERT OR REPLACE INTO account_files(path, platform, author_id, handle, at) "
                         "VALUES (?, ?, ?, ?, ?)", rows)


def copy_row(conn, copy_id):
    """A copy with its media list decoded, or None."""
    row = conn.execute("SELECT * FROM copies WHERE id = ?", (copy_id,)).fetchone()
    return {**dict(row), "media": json.loads(row["media"]),
            "side_files": json.loads(row["side_files"])} if row else None


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


def summary(conn, row, tags=None):
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
        "tags": post_tags(conn, row["id"]) if tags is None else tags,
    }


def summaries(conn, rows):
    """summary() of each row, the tags of all of them read in one query."""
    tags = {}
    ids = [r["id"] for r in rows]
    if ids:
        for pid, name in conn.execute(
                "SELECT pt.post_id, t.name FROM post_tags pt JOIN tags t ON t.id = pt.tag_id "
                f"WHERE pt.post_id IN ({', '.join('?' for _ in ids)}) ORDER BY t.name COLLATE NOCASE", ids):
            tags.setdefault(pid, []).append(name)
    return [summary(conn, r, tags.get(r["id"], [])) for r in rows]


def post_tags(conn, post_id):
    return [r[0] for r in conn.execute(
        "SELECT t.name FROM post_tags pt JOIN tags t ON t.id = pt.tag_id "
        "WHERE pt.post_id = ? ORDER BY t.name COLLATE NOCASE", (post_id,))]


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
    out["collections"] = [{"id": r[0], "name": r[1]} for r in conn.execute(
        "SELECT c.id, c.name FROM collection_posts cp JOIN collections c ON c.id = cp.collection_id "
        "WHERE cp.post_id = ? ORDER BY c.name COLLATE NOCASE", (row["id"],))]
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


# tag:name or tag:"two words"; an unclosed quote runs to the end (still typing).
_ASCII_FOLD = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)   # what NOCASE folds
_TAG_TERM = re.compile(r'(?<!\S)tag:(?:"([^"]*)"?|(\S*))', re.IGNORECASE)
_IS_NEW = re.compile(r'(?<!\S)is:new(?!\S)', re.IGNORECASE)


def parse_search(q):
    """Split the search box text into (the words left, [tag names])."""
    tags = []

    def take(m):
        name = m.group(1) if m.group(1) is not None else m.group(2)
        name = " ".join(name.split())
        if name:
            tags.append(name)
        return " "

    return _TAG_TERM.sub(take, q).strip(), tags


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


# The accounts of a person (by id, three times): those linked, and their
# aliases either way (see account_aliases), so posts rebuilt from file names
# follow the account they belong to.
PERSON_ACCOUNTS = """
    SELECT platform, author_id FROM person_accounts WHERE person_id = ?
    UNION SELECT a.platform, a.alias_id FROM account_aliases a JOIN person_accounts pa
      ON pa.platform = a.platform AND pa.author_id = a.author_id WHERE pa.person_id = ?
    UNION SELECT a.platform, a.author_id FROM account_aliases a JOIN person_accounts pa
      ON pa.platform = a.platform AND pa.author_id = a.alias_id WHERE pa.person_id = ?"""


# Posts first indexed after the user last marked everything seen (news.py);
# none while there is no mark.
NEW = "p.first_seen > COALESCE((SELECT at FROM seen_at WHERE id = 1), 9223372036854775807)"


def post_filter(q=None, platform=None, author=None, kind=None, review=None, tags=(), untagged=False,
                person=None, new=False):
    """WHERE clause and arguments for the /api/posts filters, over _FROM.
    None when the search text can match nothing. Shared by list_posts,
    post_summary and storage so a count and its size can never disagree.

    ``tags``: the post must have every one; ``tag:`` terms in ``q`` add to them.
    ``author`` is the whole account: an id takes in its folder-name aliases,
    an alias its id (and the id's other aliases). ``person`` (an id) every
    account linked to that person. ``new``: only new posts; ``is:new`` in
    ``q`` too."""
    where, args = [], []
    tags = list(tags or ())
    if q:
        q, more = parse_search(q)
        tags += more
        # After the tags: an is:new inside tag:"…" is part of a tag name.
        new = new or bool(_IS_NEW.search(q))
        q = _IS_NEW.sub(" ", q).strip()
    if q:
        match = fts_query(q)
        if match is None:
            return None
        where.append("p.n IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?)")
        args.append(match)
    # With search text the matches drive the query and tags only filter them
    # (+ keeps SQLite from probing the index for every tag x match pair).
    col = "+p.id" if q else "p.id"
    for name in {t.translate(_ASCII_FOLD): t for t in tags}.values():
        # tags.name is COLLATE NOCASE, so = ignores case, of ASCII letters only (like UNIQUE).
        where.append(f"{col} IN (SELECT pt.post_id FROM post_tags pt JOIN tags t ON t.id = pt.tag_id "
                     "WHERE t.name = ?)")
        args.append(name)
    if untagged:
        where.append("p.id NOT IN (SELECT post_id FROM post_tags)")
    if new:
        where.append(NEW)
    if platform:
        where.append("p.platform = ?")
        args.append(platform)
    if author:
        where.append("(p.author_id = ? OR (p.platform, p.author_id) IN ("
                     "SELECT platform, alias_id FROM account_aliases WHERE author_id = ? "
                     "UNION SELECT platform, author_id FROM account_aliases WHERE alias_id = ? "
                     "UNION SELECT s.platform, s.alias_id FROM account_aliases s JOIN account_aliases t "
                     "ON t.platform = s.platform AND t.author_id = s.author_id WHERE t.alias_id = ?))")
        args += [author] * 4
    if person is not None:
        where.append(f"(p.platform, p.author_id) IN ({PERSON_ACCOUNTS})")
        args += [person] * 3
    if kind:
        where.append("p.kind = ?")
        args.append(kind)
    if review == "unreviewed":
        where.append("d.post_id IS NULL")
    elif review == "kept":
        where.append("d.decision = 'keep'")
    return ("WHERE " + " AND ".join(where)) if where else "", args


def list_posts(conn, q=None, platform=None, author=None, kind=None, sort="posted",
               offset=0, limit=60, review=None, order="desc", tags=(), untagged=False, person=None, new=False):
    f = post_filter(q, platform, author, kind, review, tags, untagged, person, new)
    if f is None:
        return 0, []
    clause, args = f
    direction = "ASC" if order == "asc" else "DESC"
    first, second = ("saved_at", "posted_at") if sort == "saved" else ("posted_at", "saved_at")
    order_by = f"p.{first} {direction}, p.{second} {direction}, p.id {direction}"
    total = conn.execute(f"SELECT COUNT(*) {_FROM} {clause}", args).fetchone()[0]
    rows = conn.execute(f"{_SELECT} {clause} ORDER BY {order_by} LIMIT ? OFFSET ?",
                        (*args, limit, offset)).fetchall()
    return total, summaries(conn, rows)


def post_summary(conn, q=None, platform=None, author=None, kind=None, review=None, tags=(), untagged=False,
                 person=None, new=False):
    """Posts, media and bytes matched by the /api/posts filters, all pages."""
    f = post_filter(q, platform, author, kind, review, tags, untagged, person, new)
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


# ---------------------------------------------------------------------------
# Accounts
#
# An account is a platform and an author id, as indexed. Posts rebuilt from
# file names carry their folder's name as author id; when the same folder's
# metadata names the account's own id, the folder name is an alias of it
# (account_aliases, see people.refresh_aliases) and both read as one account.
# ---------------------------------------------------------------------------

PROFILE_URLS = {
    "instagram": "https://www.instagram.com/{}/",
    "twitter": "https://x.com/{}",
    "tiktok": "https://www.tiktok.com/@{}",
    "youtube": "https://www.youtube.com/@{}",
}


def profile_url(platform, handle):
    return PROFILE_URLS[platform].format(handle) if handle and platform in PROFILE_URLS else None


def _alias_map(conn):
    """{(platform, alias id): author id}, for ids still in the index: once
    every post of the id is gone (trashed), the folder name is its own
    account again."""
    return {(r[0], r[1]): r[2] for r in conn.execute("""
        SELECT platform, alias_id, author_id FROM account_aliases a
        WHERE EXISTS (SELECT 1 FROM posts p WHERE p.platform = a.platform AND p.author_id = a.author_id)""")}


def aliases(conn):
    return _memo(conn, ("aliases",), _alias_map)


def _seen(table, value, first, last):
    if value is None:
        return
    span = table.setdefault(value, [first, last])
    if first is not None and (span[0] is None or first < span[0]):
        span[0] = first
    if last is not None and (span[1] is None or last > span[1]):
        span[1] = last


def _history(conn):
    """Per account as indexed (aliases not merged), in one pass: post count,
    newest post time, the handle and name of the newest post (handles
    change), and every handle and display name seen with the first and last
    post time under it."""
    out = {}
    for platform, aid, handle, name, n, first, last in conn.execute("""
            SELECT platform, author_id, author_handle, author_name, COUNT(*), MIN(posted_at), MAX(posted_at)
            FROM posts WHERE author_id IS NOT NULL GROUP BY 1, 2, 3, 4"""):
        a = out.setdefault((platform, aid), {"count": 0, "newest": None, "handle": None, "name": None,
                                            "handles": {}, "names": {}, "top": None})
        a["count"] += n
        top = (last is not None, last or 0)
        if a["top"] is None or top > a["top"]:
            a["top"], a["newest"], a["handle"], a["name"] = top, last, handle, name
        _seen(a["handles"], handle, first, last)
        _seen(a["names"], name, first, last)
    return out


def _spans(table, key):
    """[{key, first, last}], the most recent first."""
    return [{key: v, "first": s[0], "last": s[1]}
            for v, s in sorted(table.items(), key=lambda kv: (kv[1][1] is None, -(kv[1][1] or 0), kv[0]))]


def _accounts(conn, sizes=True):
    """{(platform, id): account row}, aliases merged into the account they
    stand for. ``sizes``: add up the bytes of every account's media too."""
    hist, alias = _history(conn), aliases(conn)
    people = {(r[0], r[1]): {"id": r[2], "name": r[3]} for r in conn.execute(
        "SELECT pa.platform, pa.author_id, p.id, p.name FROM person_accounts pa JOIN people p ON p.id = pa.person_id")}
    size = {}
    if sizes:
        # Per account through posts_author: twice as fast as one join over every post.
        size = {(r[0], r[1]): r[2] for r in conn.execute("""
            SELECT platform, author_id,
                   (SELECT SUM(m.size) FROM posts p2 JOIN media m ON m.post_id = p2.id AND m.missing = 0
                    WHERE p2.platform = p.platform AND p2.author_id = p.author_id)
            FROM posts p WHERE author_id IS NOT NULL GROUP BY 1, 2""")}
    out = {}
    # Own ids first, so a merged row takes its handle from the account's own posts.
    for key in sorted(hist, key=lambda k: k in alias):
        h = hist[key]
        canon = (key[0], alias[key]) if key in alias and (key[0], alias[key]) in hist else key
        a = out.get(canon)
        if a is None:
            a = out[canon] = {"platform": canon[0], "id": canon[1], "handle": h["handle"], "name": h["name"],
                              "count": 0, "bytes": 0, "newest": None, "aliases": [], "person": None,
                              "handles": {}, "names": {}}
        if key != canon:
            a["aliases"].append(key[1])
        a["count"] += h["count"]
        a["bytes"] += size.get(key) or 0
        if h["newest"] is not None and (a["newest"] is None or h["newest"] > a["newest"]):
            a["newest"] = h["newest"]
        a["person"] = a["person"] or people.get(key)
        for table in ("handles", "names"):
            for v, (first, last) in h[table].items():
                _seen(a[table], v, first, last)
    # Handles known besides the posts': instaloader's id files (the folder's
    # name when written) and renames the user accepted (old, then new: the
    # handle now, unless a post was seen under another one since).
    renamed = {}
    for platform, aid, handle, first, last, new in conn.execute("""
            SELECT platform, author_id, handle, at, at, 0 FROM account_files
            UNION ALL SELECT platform, author_id, old, NULL, at, 0 FROM handle_renames
            UNION ALL SELECT platform, author_id, new, at, at, 1 FROM handle_renames ORDER BY 4"""):
        a = out.get((platform, alias.get((platform, aid), aid)))
        if a is None:
            continue
        handle = next((h for h in a["handles"] if h.lower() == handle.lower()), handle)
        _seen(a["handles"], handle, first, last)
        if new:
            renamed[id(a)] = (a, handle, first)
    for a, handle, at in renamed.values():
        if at >= (a["handles"].get(a["handle"], [None, None])[1] or 0):
            a["handle"] = handle
    for a in out.values():
        a["handle"] = a["handle"] or next(iter(a["handles"]), None)
        a["url"] = profile_url(a["platform"], a["handle"])
        a["handles"] = _spans(a["handles"], "handle")
        a["names"] = _spans(a["names"], "name")
        if not sizes:
            del a["bytes"]
    return out


def accounts(conn):
    """_accounts(), cached. Rows are shared: callers must not modify them."""
    return _memo(conn, ("accounts",), _accounts)


def authors(conn):
    return sorted(accounts(conn).values(), key=lambda a: (-a["count"], a["handle"] or ""))


LARGEST = 100


def storage(conn, person=None):
    """Disk use by creator, kind and year, and the largest files, of every
    post or of one person's. Media marked missing are left out. The trash
    total is added by the caller."""
    return _memo(conn, ("storage", person), lambda c: _storage(c, person))


def _storage(conn, person=None):
    # The same filter as /api/posts, so a person's totals match the Feed's.
    clause, args = post_filter(person=person)
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
        {clause}
        GROUP BY 1, 2, 3, 4, 5
    """, args).fetchall()

    def bucket(table, key, **extra):
        if key not in table:
            table[key] = {**extra, "posts": 0, "media": 0, "bytes": 0}
        return table[key]

    totals = {"posts": 0, "media": 0, "bytes": 0}
    by_author, by_kind, by_year = {}, {}, {}
    # Without byte sums: they would add half again to a cold load.
    names, alias = _accounts(conn, sizes=False), aliases(conn)
    for r in rows:
        targets = [totals, bucket(by_kind, r["kind"], kind=r["kind"]),
                   bucket(by_year, r["year"], year=r["year"])]
        if r["author_id"] is not None:
            key = (r["platform"], r["author_id"])
            key = (key[0], alias[key]) if key in alias and (key[0], alias[key]) in names else key
            a = names[key]
            row = bucket(by_author, key, platform=key[0], id=key[1], handle=a["handle"], name=a["name"],
                         aliases=a["aliases"], person=a["person"])
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
    for m in conn.execute(f"""
            SELECT m.id, m.post_id, m.kind, m.size, m.poster_path,
                   p.platform, p.post_id AS short_id, p.author_id, p.author_handle
            FROM media m JOIN posts p ON p.id = m.post_id LEFT JOIN decisions d ON d.post_id = p.id
            WHERE m.missing = 0 AND m.size IS NOT NULL {clause.replace("WHERE", "AND", 1)}
            ORDER BY m.size DESC LIMIT ?""", (*args, LARGEST)):
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
