"""What happened to trashed posts, kept after their trash entries are gone.

The trash manifests (trash.py) say what is in the trash now: an entry
restored or deleted for good leaves no line there. This log keeps one event
per post per action, so the Trash page's History can say what was deleted
from where, whether an Undo brought it back, and where it is now:

    deleted     a post, some items of one, or an extra copy moved to the
                trash; ``from``: the page that asked (SOURCES["deleted"])
    restored    an entry put back; ``from``: "trash" (the Trash page),
                "undo" (a toast's or Review's Undo), or "scan" (a restore
                cut short, finished by trash.finish_restores)
    purged      an entry deleted for good from the Trash page; a purge of
                more than PURGE_EACH entries is one event that counts them
                (and keeps their keys, for the states of the History)
    emptied     Empty trash: one event that counts what went, however big

Each event carries what a History row shows without the index (the post
may be gone from it): the post, its platform, author, kind, items, files
and bytes, and its trash entry's key (trash.entry_key), never a path. Where
the post is now is worked out when the History is listed (trash.history).
A sync putting back in the trash a post it brought back (sync.retrash) is
not logged: nothing the user did.

The log is ``<data_dir>/userdata/trash-history.jsonl``, beside the user's
other data, so rebuilding the index loses none of it: one JSON object per
line, appended (one write per action, through no symlink), readable by its
owner only. It keeps the last KEEP_DAYS days, at most MAX_EVENTS events:
the append that takes it SLACK events past that, or finds its oldest event
a day past KEEP_DAYS, rewrites it without the oldest (config.write_private),
so no append rewrites it in between. Every event is added under
db.write_lock (each caller holds it); this module's lock keeps the appends
and the reads' cache apart.
"""
import json
import os
import threading
import time

import config

NAME = "trash-history.jsonl"
KEEP_DAYS = 90
MAX_EVENTS = 10_000
SLACK = 1_000
PURGE_EACH = 100                # a purge of more entries is one event

ACTIONS = ("deleted", "restored", "purged", "emptied")
# What ``from`` may say, per action; anything else is null.
SOURCES = {
    "deleted": frozenset({"review", "feed", "post", "storage", "duplicates"}),
    "restored": frozenset({"trash", "undo", "scan"}),
}
# What a page may send as its ``from`` (the API's allowlists): "scan" is
# the backend's own.
SENT = {"deleted": SOURCES["deleted"], "restored": frozenset({"trash", "undo"})}


def path(data_dir):
    return os.path.join(data_dir, "userdata", NAME)


def source(value, action):
    """A ``from`` a page sent, or None when it is not one SENT allows."""
    return value if isinstance(value, str) and value in SENT[action] else None


# ---------------------------------------------------------------------------
# Events, cleaned
# ---------------------------------------------------------------------------

def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2**53 else None


def _str(v, most=500):
    return v if isinstance(v, str) and 0 < len(v) <= most else None


def _key(v):
    # Opaque, as trash.entry_key makes them: a key is never a path.
    return v if isinstance(v, str) and len(v) == 20 and v.isalnum() and v.isascii() else None


def clean(e):
    """An event as read back, every field checked (the file may have been
    edited by hand), or None to skip it."""
    if not isinstance(e, dict) or e.get("action") not in ACTIONS or _int(e.get("at_ms")) is None:
        return None
    a = e.get("author") if isinstance(e.get("author"), dict) else None
    author = {"id": _str(a.get("id")), "handle": _str(a.get("handle"))} if a else None
    said, keys = e.get("from"), e.get("keys")
    return {
        "at": _int(e.get("at")) or e["at_ms"] // 1000, "at_ms": e["at_ms"], "action": e["action"],
        "from": said if isinstance(said, str) and said in SOURCES.get(e["action"], ()) else None,
        "post": _str(e.get("post")), "platform": _str(e.get("platform"), 50), "post_id": _str(e.get("post_id")),
        "author": author if author and (author["id"] or author["handle"]) else None,
        "kind": _str(e.get("kind"), 50), "posted_at": _int(e.get("posted_at")),
        "items": _int(e.get("items")), "of": _int(e.get("of")),
        "partial": e.get("partial") is True, "copy": e.get("copy") is True,
        "files": _int(e.get("files")) or 0, "bytes": _int(e.get("bytes")) or 0,
        "entries": _int(e.get("entries")),
        "key": _key(e.get("key")), "batch": _str(e.get("batch"), 100),
        # A purge of more than PURGE_EACH entries: the keys of all of them.
        "keys": [k for k in map(_key, keys) if k] if isinstance(keys, list) else None,
    }


# ---------------------------------------------------------------------------
# Reading, cached
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cache = {}                     # path -> (inode, (size, mtime_ns) read, bytes read up to, events, restored keys)


def _parse(data):
    out = []
    for raw in data.splitlines():
        try:
            e = clean(json.loads(raw))
        except ValueError:
            continue
        if e is not None:
            out.append(e)
    return out


def _read(p):
    """(events oldest first, the keys a restore was logged for), under _lock.
    A log that only grew (an append: same file, longer) is read from where
    the last read stopped. Shared: never mutate them."""
    hit = _cache.get(p)
    try:
        st = os.stat(p)
    except OSError:
        return [], frozenset()
    if hit and hit[0] == st.st_ino and hit[1] == (st.st_size, st.st_mtime_ns):
        return hit[3], hit[4]
    start = hit[2] if hit and hit[0] == st.st_ino and hit[2] <= st.st_size else 0
    try:
        with open(p, "rb") as f:
            f.seek(start)
            data = f.read()
    except OSError:
        return [], frozenset()
    end = data.rfind(b"\n") + 1               # a line half written is read next time
    new = _parse(data[:end])
    events = hit[3] + new if start else new
    restored = (hit[4] if start else frozenset()) | {e["key"] for e in new if e["action"] == "restored" and e["key"]}
    seen = (st.st_size, st.st_mtime_ns) if end == len(data) else None
    _cache[p] = (st.st_ino, seen, start + end, events, frozenset(restored))
    return events, _cache[p][4]


def events(data_dir):
    """(every event, oldest first; the keys of the entries a restore was
    logged for)."""
    if not data_dir:
        return [], frozenset()
    with _lock:
        return _read(path(data_dir))


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------

def add(data_dir, new, now=None):
    """Append events (dicts of the fields clean reads), each stamped with
    the time: ``at_ms``, in milliseconds but above every stamp before it,
    so the newest is always last and no two are equal. Never raises: a
    deletion or a restore goes on without its history (logged)."""
    if not data_dir or not new:
        return
    now = time.time() if now is None else now
    p = path(data_dir)
    try:
        with _lock:
            log, _ = _read(p)
            last = log[-1]["at_ms"] if log else 0
            lines = []
            for e in new:
                last = max(int(now * 1000), last + 1)
                lines.append(json.dumps({**{k: v for k, v in e.items() if v is not None},
                                         "at": last // 1000, "at_ms": last}))
            config.make_private_dir(os.path.dirname(p))
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                os.write(fd, ("\n".join(lines) + "\n").encode("ascii"))
            finally:
                os.close(fd)
            log, _ = _read(p)
            if len(log) > MAX_EVENTS + SLACK or log and log[0]["at"] < now - (KEEP_DAYS + 1) * 86400:
                _prune(p, log, now)
    except (OSError, ValueError) as e:
        print(f"[trash] could not write the trash history: {type(e).__name__}: {e}")


def _prune(p, log, now):
    """Rewrite the log with its last MAX_EVENTS events of the last
    KEEP_DAYS days (under _lock)."""
    cut = now - KEEP_DAYS * 86400
    keep = [e for e in log[-MAX_EVENTS:] if e["at"] >= cut]
    config.write_private(p, lambda f: f.writelines(
        json.dumps({k: v for k, v in e.items() if v is not None}) + "\n" for e in keep))
    _cache.pop(p, None)
