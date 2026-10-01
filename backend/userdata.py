"""The user's own data, mirrored to JSON.

The index is derived from the media folders and rebuilds from a rescan. A few
tables are not: review decisions now, later tags, people, sources. Each one is
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


REGISTRY = {}


def register(name, table, columns, key, legacy=None, legacy_rows="rows"):
    key = (key,) if isinstance(key, str) else tuple(key)
    REGISTRY[name] = Table(name, table, tuple(columns), key, legacy, legacy_rows)


register("decisions", "decisions", ("post_id", "decision", "at"), "post_id",
         legacy="decisions.json", legacy_rows="decisions")
# "Not a duplicate": the key names the group's members (post ids, and the
# metadata paths of extra copies), so a group that gains a member shows again.
register("dismissed_duplicates", "dismissed_duplicates", ("key", "kind", "at"), "key")


def path(data_dir, name):
    return os.path.join(data_dir, "userdata", f"{name}.json")


def export(conn, name, data_dir):
    """Write one table to its JSON file (atomically). Returns the row count."""
    t = REGISTRY[name]
    rows = conn.execute(f"SELECT {', '.join(t.columns)} FROM {t.table} "
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
    values = [tuple(r.get(c) for c in t.columns) for r in rows
              if isinstance(r, dict) and all(r.get(k) is not None for k in t.key)]
    marks = ", ".join("?" for _ in t.columns)
    with conn:
        before = conn.total_changes
        conn.executemany(f"INSERT OR IGNORE INTO {t.table} ({', '.join(t.columns)}) "
                         f"VALUES ({marks})", values)
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
