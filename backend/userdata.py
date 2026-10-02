"""The user's own data, mirrored to JSON.

The index is derived from the media folders and rebuilds from a rescan. A few
tables are not: review decisions, tags, collections, people, sources, the
"new posts" mark, the posts the Save button added. Each one is
registered here once, and gets the same treatment:

- written to ``<data_dir>/userdata/<name>.json`` shortly after it changes
  (``changed(name)``, debounced), as ``{"version": 1, "rows": [...]}``
- read back on startup when its table is empty, so deleting feedvault.db
  loses nothing a rescan cannot find again

A file that cannot be read is logged and renamed to ``<name>.json.corrupt-<time>``
(so the next write cannot replace it); it never stops the app.
"""
import json
import os
import threading
import time
from dataclasses import dataclass

import config
import db

FORMAT_VERSION = 1
DELAY = 2.0                     # seconds of quiet before a change is written


@dataclass(frozen=True)
class Table:
    name: str                   # file name under userdata/
    table: str
    columns: tuple
    key: tuple                  # primary key columns, also the export order
    legacy: str = None          # older file, relative to the data dir, read when <name>.json is missing
    legacy_rows: str = "rows"   # the list's key in that older file
    # For rows that point at another user table by id: the export query (its
    # columns are ``columns``, ids replaced by names) and the statements that
    # put one row back, with :column parameters.
    select: str = None
    insert: tuple = ()


REGISTRY = {}


def register(name, table, columns, key, legacy=None, legacy_rows="rows", select=None, insert=()):
    key = (key,) if isinstance(key, str) else tuple(key)
    REGISTRY[name] = Table(name, table, tuple(columns), key, legacy, legacy_rows, select, tuple(insert))


register("decisions", "decisions", ("post_id", "decision", "at"), "post_id",
         legacy="decisions.json", legacy_rows="decisions")
# "Not a duplicate": the key names the group's members (post ids, and the
# metadata paths of extra copies), so a group that gains a member shows again.
register("dismissed_duplicates", "dismissed_duplicates", ("key", "kind", "at"), "key")
# Tags by name, not id: ids are not kept when the index is rebuilt. Order
# matters, tags load before the posts that use them (a tag missing from
# tags.json is created again from post_tags.json).
register("tags", "tags", ("name", "color", "created_at"), "name")
register("post_tags", "post_tags", ("post_id", "tag", "at"), ("post_id", "tag"),
         select="SELECT pt.post_id, t.name, pt.at FROM post_tags pt JOIN tags t ON t.id = pt.tag_id "
                "ORDER BY pt.post_id, t.name",
         insert=("INSERT OR IGNORE INTO tags(name, created_at) VALUES (:tag, COALESCE(:at, 0))",
                 "INSERT OR IGNORE INTO post_tags(post_id, tag_id, at) "
                 "SELECT :post_id, id, COALESCE(:at, 0) FROM tags WHERE name = :tag"))
# Collections the same way: by name, before the posts in them.
register("collections", "collections", ("name", "cover_post", "created_at", "position"), "name",
         insert=("INSERT OR IGNORE INTO collections(name, cover_post, created_at, position) "
                 "VALUES (:name, :cover_post, COALESCE(:created_at, 0), "
                 "COALESCE(:position, (SELECT COALESCE(MAX(position), 0) + 1 FROM collections)))",))
register("collection_posts", "collection_posts", ("collection", "post_id", "position", "at"),
         ("collection", "post_id"),
         select="SELECT c.name, cp.post_id, cp.position, cp.at FROM collection_posts cp "
                "JOIN collections c ON c.id = cp.collection_id ORDER BY c.name, cp.position",
         insert=("INSERT OR IGNORE INTO collections(name, created_at, position) "
                 "VALUES (:collection, COALESCE(:at, 0), (SELECT COALESCE(MAX(position), 0) + 1 FROM collections))",
                 "INSERT OR IGNORE INTO collection_posts(collection_id, post_id, position, at) "
                 "SELECT id, :post_id, COALESCE(:position, 0), COALESCE(:at, 0) FROM collections "
                 "WHERE name = :collection"))
# People by name, before the accounts linked to them, which are keyed by
# platform and author id (a person missing from people.json is created again).
register("people", "people", ("name", "notes", "created_at"), "name",
         insert=("INSERT OR IGNORE INTO people(name, notes, created_at) "
                 "VALUES (:name, COALESCE(:notes, ''), COALESCE(:created_at, 0))",))
register("person_accounts", "person_accounts", ("platform", "author_id", "person", "at"), ("platform", "author_id"),
         select="SELECT pa.platform, pa.author_id, p.name, pa.at FROM person_accounts pa "
                "JOIN people p ON p.id = pa.person_id ORDER BY pa.platform, pa.author_id",
         insert=("INSERT OR IGNORE INTO people(name, created_at) VALUES (:person, COALESCE(:at, 0))",
                 "INSERT OR IGNORE INTO person_accounts(person_id, platform, author_id, at) "
                 "SELECT id, :platform, :author_id, COALESCE(:at, 0) FROM people WHERE name = :person"))
# "Not the same person": keyed by the group's accounts, like duplicates.
register("dismissed_suggestions", "dismissed_suggestions", ("key", "at"), "key")
# Sources by tool and target, their person by name (after people). The last
# sync's outcome travels with them; the job it points at does not (jobs are
# not user data). A row without its platform or folder is skipped.
register("sources", "sources", ("tool", "target", "platform", "author_id", "person", "folder", "options",
                                "created_at", "last_sync_at", "last_result"), ("tool", "target"),
         select="SELECT s.tool, s.target, s.platform, s.author_id, p.name, s.folder, s.options, s.created_at, "
                "s.last_sync_at, s.last_result FROM sources s LEFT JOIN people p ON p.id = s.person_id "
                "ORDER BY s.tool, s.target",
         insert=("INSERT OR IGNORE INTO sources(person_id, platform, author_id, tool, target, folder, options, "
                 "created_at, last_sync_at, last_result) "
                 "SELECT (SELECT id FROM people WHERE name = :person), :platform, :author_id, :tool, :target, "
                 ":folder, COALESCE(:options, '{}'), COALESCE(:created_at, 0), :last_sync_at, :last_result "
                 "WHERE :platform IS NOT NULL AND :folder IS NOT NULL",))

# "Mark all seen" (news.py): one row, the time before which posts are not new.
register("seen_at", "seen_at", ("id", "at"), "id")
# Posts the Save button added (save.py): they never seed a sync's stamp.
register("saved_posts", "saved_posts", ("post_id", "saved_at"), "post_id")

def path(data_dir, name):
    return os.path.join(data_dir, "userdata", f"{name}.json")


def export(conn, name, data_dir):
    """Write one table to its JSON file (atomically). Returns the row count."""
    t = REGISTRY[name]
    rows = conn.execute(t.select or f"SELECT {', '.join(t.columns)} FROM {t.table} "
                                    f"ORDER BY {', '.join(t.key)}").fetchall()
    out = path(data_dir, name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": FORMAT_VERSION, "rows": [dict(zip(t.columns, r)) for r in rows]}, f)
    os.replace(tmp, out)
    return len(rows)


def load(conn, name, data_dir):
    """Fill an empty table from its JSON file. Returns the rows inserted.

    Never overwrites: a table that already has rows is left alone. Rows
    missing a key column are skipped; unknown keys are ignored.
    """
    t = REGISTRY[name]
    if conn.execute(f"SELECT 1 FROM {t.table} LIMIT 1").fetchone():
        return 0
    src, rows_key = _source(t, data_dir)
    if src is None:
        return 0
    with open(src, encoding="utf-8") as f:
        rows = json.load(f).get(rows_key)
    if not isinstance(rows, list):
        raise ValueError(f"no {rows_key!r} list")
    rows = [r for r in rows if isinstance(r, dict) and all(r.get(k) is not None for k in t.key)]
    with conn:
        if t.insert:
            for r in rows:
                for stmt in t.insert:
                    conn.execute(stmt, {c: r.get(c) for c in t.columns})
            return conn.execute(f"SELECT COUNT(*) FROM {t.table}").fetchone()[0]
        before = conn.total_changes
        marks = ", ".join("?" for _ in t.columns)
        conn.executemany(f"INSERT OR IGNORE INTO {t.table} ({', '.join(t.columns)}) "
                         f"VALUES ({marks})", [tuple(r.get(c) for c in t.columns) for r in rows])
        return conn.total_changes - before


def _source(t, data_dir):
    """The file to import from and the key of its row list, or (None, None)."""
    src = path(data_dir, t.name)
    if os.path.exists(src):
        return src, "rows"
    if t.legacy and os.path.exists(os.path.join(data_dir, t.legacy)):
        return os.path.join(data_dir, t.legacy), t.legacy_rows
    return None, None


def restore_all(conn, data_dir):
    """Startup: import every empty table, and write the file for any table
    that has rows but no file yet (first run after an upgrade)."""
    for name, t in REGISTRY.items():
        src, _ = _source(t, data_dir)
        try:
            n = load(conn, name, data_dir)
            if n:
                print(f"[userdata] Restored {n} {name} rows from {src}")
            if not os.path.exists(path(data_dir, name)) \
                    and conn.execute(f"SELECT 1 FROM {t.table} LIMIT 1").fetchone():
                export(conn, name, data_dir)
        except Exception as e:  # a bad file must never stop startup
            conn.rollback()
            print(f"[userdata] Skipped {name}: {type(e).__name__}: {e}")
            # Out of the way, or the next change would overwrite what may
            # still be recoverable by hand.
            if src == path(data_dir, name) and os.path.exists(src):
                aside = f"{src}.corrupt-{int(time.time())}"
                os.replace(src, aside)
                print(f"[userdata] Moved the unreadable file to {aside}")


# ---------------------------------------------------------------------------
# Debounced writes
# ---------------------------------------------------------------------------

_timers = {}
_lock = threading.Lock()
_writing = threading.Lock()     # one export at a time: they share the .tmp name


def _write(name):
    with _lock:
        _timers.pop(name, None)
    try:
        with _writing:
            export(db.connect(), name, config.load()["data_directory"])
    except Exception as e:  # on a timer thread: log, keep going
        print(f"[userdata] Could not write {name}.json: {type(e).__name__}: {e}")


def changed(name):
    """Note that a table changed; its file is rewritten after DELAY seconds
    with no further change."""
    if name not in REGISTRY:
        raise KeyError(name)
    with _lock:
        if name in _timers:
            _timers[name].cancel()
        timer = threading.Timer(DELAY, _write, (name,))
        timer.daemon = True
        _timers[name] = timer
        timer.start()


def flush():
    """Write every pending change now, and wait for a write already under
    way (before quitting)."""
    with _lock:
        pending = list(_timers)
        for name in pending:
            _timers[name].cancel()
    for name in pending:
        _write(name)
    with _writing:
        pass
