"""The user's own data, mirrored to JSON.

The index is derived from the media folders and rebuilds from a rescan. A few
tables are not: review decisions, tags, collections, people, sources, links, the
handles the user accepted for an account, the "new posts" marks, the posts
the Save button added, who is muted. Each one is
registered here once, and gets the same treatment:

- written to ``<data_dir>/userdata/<name>.json`` shortly after it changes
  (``changed(name)``, debounced), as ``{"version": 1, "rows": [...]}``
- read back on startup when its table is empty, so deleting feedvault.db
  loses nothing a rescan cannot find again
- written at once, not after the delay, when the change left the table
  empty: a file still listing the rows just removed would bring them back
  at the next start (FeedVault quit or killed within DELAY), since an empty
  table is read back

Tags, collections, people and links keep their id in the file and get it
back (the URLs of their pages are built from it, and it breaks ties in
their order); a file written before ids were kept loads as before, each row
under a new id.

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
import links

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
    # A row as read from the file, cleaned, or None to skip it.
    clean: object = None
    # Whether the table's id is kept in the file (its last column, "id"), and
    # inserted back when it is a usable one (_with_ids).
    ids: bool = False


REGISTRY = {}


def register(name, table, columns, key, legacy=None, legacy_rows="rows", select=None, insert=(), clean=None,
             ids=False):
    key = (key,) if isinstance(key, str) else tuple(key)
    columns = tuple(columns) + (("id",) if ids else ())
    REGISTRY[name] = Table(name, table, columns, key, legacy, legacy_rows, select, tuple(insert), clean, ids)


register("decisions", "decisions", ("post_id", "decision", "at"), "post_id",
         legacy="decisions.json", legacy_rows="decisions")
# "Not a duplicate": the key names the group's members (post ids, and the
# metadata paths of extra copies), so a group that gains a member shows again.
register("dismissed_duplicates", "dismissed_duplicates", ("key", "kind", "at"), "key")
# Tags by name: the posts' tags refer to them by name, so a tag file edited
# by hand (or one from before ids were kept) still matches. Each keeps its id
# too, for the URLs built from it. Order matters, tags load before the posts
# that use them (a tag missing from tags.json is created again, under a new
# id, from post_tags.json).
register("tags", "tags", ("name", "color", "created_at"), "name", ids=True)
register("post_tags", "post_tags", ("post_id", "tag", "at"), ("post_id", "tag"),
         select="SELECT pt.post_id, t.name, pt.at FROM post_tags pt JOIN tags t ON t.id = pt.tag_id "
                "ORDER BY pt.post_id, t.name",
         insert=("INSERT OR IGNORE INTO tags(name, created_at) VALUES (:tag, COALESCE(:at, 0))",
                 "INSERT OR IGNORE INTO post_tags(post_id, tag_id, at) "
                 "SELECT :post_id, id, COALESCE(:at, 0) FROM tags WHERE name = :tag"))
# Collections the same way: by name, before the posts in them.
register("collections", "collections", ("name", "cover_post", "created_at", "position"), "name",
         insert=("INSERT OR IGNORE INTO collections(id, name, cover_post, created_at, position) "
                 "VALUES (:id, :name, :cover_post, COALESCE(:created_at, 0), "
                 "COALESCE(:position, (SELECT COALESCE(MAX(position), 0) + 1 FROM collections)))",),
         ids=True)
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
         insert=("INSERT OR IGNORE INTO people(id, name, notes, created_at) "
                 "VALUES (:id, :name, COALESCE(:notes, ''), COALESCE(:created_at, 0))",),
         ids=True)
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

# Links (links.py) by URL, their person by name (after people; one missing
# from people.json is created again). Each row is cleaned as the API would
# (links.restore_row): the file may have been edited by hand, and a link is
# rendered as a link. A URL the API would refuse is not put back.
register("links", "links", ("url", "title", "notes", "person", "position", "created_at"), "url",
         select="SELECT l.url, l.title, l.notes, p.name, l.position, l.created_at, l.id FROM links l "
                "LEFT JOIN people p ON p.id = l.person_id ORDER BY l.url",
         insert=("INSERT OR IGNORE INTO people(name, created_at) SELECT :person, COALESCE(:created_at, 0) "
                 "WHERE :person IS NOT NULL",
                 "INSERT OR IGNORE INTO links(id, url, title, notes, person_id, position, created_at) "
                 "SELECT :id, :url, COALESCE(:title, ''), COALESCE(:notes, ''), p.id, "
                 "CASE WHEN p.id IS NULL THEN NULL ELSE :position END, COALESCE(:created_at, 0) "
                 "FROM (SELECT 1) LEFT JOIN people p ON p.name = :person "
                 "WHERE :url LIKE 'http://%' OR :url LIKE 'https://%'"),
         clean=links.restore_row, ids=True)

# New handles the user accepted for a source's account (sources.rename).
register("handle_renames", "handle_renames", ("platform", "author_id", "old", "new", "at"),
         ("platform", "author_id", "old", "new"))

# "Mark all seen" (news.py): one row, the time before which posts are not new.
register("seen_at", "seen_at", ("id", "at"), "id")
# "Mark seen" on a person or an account: a mark per account, by platform and id.
register("seen_marks", "seen_marks", ("platform", "author_id", "at"), ("platform", "author_id"))
# Mute (news.py): people by name (after people), accounts by platform and id.
register("muted_people", "muted_people", ("person", "at"), "person",
         select="SELECT p.name, m.at FROM muted_people m JOIN people p ON p.id = m.person_id ORDER BY p.name",
         insert=("INSERT OR IGNORE INTO muted_people(person_id, at) "
                 "SELECT id, COALESCE(:at, 0) FROM people WHERE name = :person",))
register("muted_accounts", "muted_accounts", ("platform", "author_id", "at"), ("platform", "author_id"))
# Posts the Save button added (save.py): they never seed a sync's stamp.
register("saved_posts", "saved_posts", ("post_id", "saved_at"), "post_id")

def path(data_dir, name):
    return os.path.join(data_dir, "userdata", f"{name}.json")


def export(conn, name, data_dir):
    """Write one table to its JSON file (atomically, 0600: config.write_private).
    Returns the row count."""
    t = REGISTRY[name]
    rows = conn.execute(t.select or f"SELECT {', '.join(t.columns)} FROM {t.table} "
                                    f"ORDER BY {', '.join(t.key)}").fetchall()
    out = path(data_dir, name)
    config.make_private_dir(os.path.dirname(out))
    config.write_private(out, lambda f: json.dump(
        {"version": FORMAT_VERSION, "rows": [dict(zip(t.columns, r)) for r in rows]}, f))
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
    if t.clean:
        rows = [r for r in map(t.clean, rows) if r is not None]
    if t.ids:
        rows = _with_ids(rows)
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


def _usable_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**53


def _with_ids(rows):
    """The rows of a table that keeps its ids, in the order to insert them:
    those with a usable id first, by id, so each gets its own back; then the
    rest (a file from before ids were kept, a row added by hand, an id
    given twice) in file order, their id None so the table picks a new one,
    above the others. The table is empty: no id is taken yet."""
    seen, kept, rest = set(), [], []
    for r in rows:
        rid = r.get("id")
        if _usable_id(rid) and rid not in seen:
            seen.add(rid)
            kept.append(r)
        else:
            rest.append({**r, "id": None})
    return sorted(kept, key=lambda r: r["id"]) + rest


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
_writing = threading.Lock()     # one export at a time


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
    with no further change, or now when the table is empty.

    An empty table is read back from its file at the next start, so a file
    still listing the rows just removed would bring them back if FeedVault
    stopped before the delay ran out. Called after the change is committed
    (in a transaction still open, the emptiness seen might not last: the
    delay then applies)."""
    if name not in REGISTRY:
        raise KeyError(name)
    now = False
    try:
        conn = db.connect()
        now = not conn.in_transaction and \
            conn.execute(f"SELECT 1 FROM {REGISTRY[name].table} LIMIT 1").fetchone() is None
    except Exception as e:                     # the timer still writes it
        print(f"[userdata] Could not look at {name}: {type(e).__name__}: {e}")
    with _lock:
        if name in _timers:
            _timers[name].cancel()
            del _timers[name]
        if not now:
            timer = threading.Timer(DELAY, _write, (name,))
            timer.daemon = True
            _timers[name] = timer
            timer.start()
    if now:
        _write(name)


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
