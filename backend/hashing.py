"""Content hashes of media files, for finding exact duplicates.

Only files that could have a twin are read: those whose recorded size another
file shares (indexed media and the files of extra copies). Each gets a sha1 of
its first and last MiB (``partial``); a file of 2 MiB or less is read whole,
so that is its full hash as well. A full sha1 of a bigger file is computed
only where partial hashes collide between different posts.

Hashes are cached in the media_hash table and stay valid while a file's size
and mtime do not change, so a pass after a rescan only reads new or changed
files, and an interrupted pass resumes where it stopped.

The worker is one background thread at the lowest CPU and I/O priority. It
starts after every scan, steps aside while anything holds db.write_lock (a
scan, a delete, a restore), and commits in small batches so requests never
wait on it.
"""
import hashlib
import json
import os
import sys
import threading
import time

import db

CHUNK = 1 << 20                                  # 1 MiB
COMMIT_EVERY = 1.0                               # seconds between commits
ERRORS_KEPT = 20

_wake = threading.Event()
_lock = threading.Lock()
_thread = None
_state = {"running": False, "paused": False, "phase": None, "done": 0, "total": 0,
          "bytes": 0, "started_at": None, "finished_at": None, "errors": []}


def status(conn=None):
    with _lock:
        out = {**_state, "errors": list(_state["errors"])}
    conn = conn or db.connect()
    out["hashed"] = conn.execute("SELECT COUNT(*) FROM media_hash").fetchone()[0]
    return out


def kick():
    """Start a pass in the background (or restart the one under way, which
    then picks up what the last scan changed)."""
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, daemon=True, name="hashing")
            _thread.start()
    _wake.set()


def _loop():
    _lower_priority()
    while True:
        _wake.wait()
        _wake.clear()
        try:
            run_pass(db.connect(), restart=_wake)
        except Exception as e:                   # keep the thread alive; show it in the status
            _note_error("", f"hashing pass failed: {type(e).__name__}: {e}")
            _set(running=False, paused=False, phase=None)


def _lower_priority():
    """Nice 19 and the idle I/O class, for this thread only (Linux keeps both
    per thread). Elsewhere the worker runs at normal priority."""
    if not sys.platform.startswith("linux"):
        return
    tid = threading.get_native_id()
    try:
        os.setpriority(os.PRIO_PROCESS, tid, 19)
    except OSError:
        pass
    try:                                         # ioprio_set has no Python wrapper
        import ctypes
        import platform
        nr = {"x86_64": 251, "aarch64": 30}.get(platform.machine())
        if nr:
            ctypes.CDLL(None, use_errno=True).syscall(nr, 1, tid, 3 << 13)   # WHO_PROCESS, IDLE class
    except Exception:
        pass


def _set(**kw):
    with _lock:
        _state.update(kw)


def _note_error(path, message):
    with _lock:
        _state["errors"] = (_state["errors"] + [{"path": path, "error": message}])[-ERRORS_KEPT:]


# ---------------------------------------------------------------------------
# Hashing one file
# ---------------------------------------------------------------------------

class Changed(Exception):
    """The file did not read back at the size it was stat'ed at."""


def partial_hash(path, size):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        if size <= 2 * CHUNK:
            data = f.read()
            if len(data) != size:
                raise Changed("size changed while reading")
            h.update(data)
        else:
            head = f.read(CHUNK)
            f.seek(size - CHUNK)
            tail = f.read(CHUNK + 1)
            if len(head) != CHUNK or len(tail) != CHUNK:
                raise Changed("size changed while reading")
            h.update(head)
            h.update(tail)
    return h.hexdigest()


def full_hash(path, size):
    h = hashlib.sha1()
    n = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            n += len(chunk)
    if n != size:
        raise Changed("size changed while reading")
    return h.hexdigest()


def stat(path):
    """(size, mtime_ns), or None when the file is gone."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


# ---------------------------------------------------------------------------
# A pass
# ---------------------------------------------------------------------------

def candidates(conn):
    """Paths whose recorded size another file shares, files of posts that
    have copies first (the Duplicates page shows those right away)."""
    sizes = {}
    for path, size in conn.execute("SELECT path, size FROM media WHERE missing = 0 AND size > 0"):
        sizes[path] = size
    first = set()
    for post_id, media in conn.execute("SELECT post_id, media FROM copies"):
        for m in json.loads(media):
            if m.get("size"):
                sizes[m["path"]] = m["size"]
                first.add(m["path"])
        first.update(r[0] for r in conn.execute(
            "SELECT path FROM media WHERE post_id = ? AND missing = 0", (post_id,)))
    count = {}
    for size in sizes.values():
        count[size] = count.get(size, 0) + 1
    out = [p for p, s in sizes.items() if count[s] > 1]
    out.sort(key=lambda p: (p not in first, p))  # then by path, close together on disk
    return out


def _full_needed(conn):
    """Paths of indexed media without a full hash whose size and partial hash
    collide with a file of another post."""
    return [r[0] for r in conn.execute("""
        SELECT h.path FROM media_hash h JOIN media m ON m.path = h.path AND m.missing = 0
        WHERE h.full IS NULL AND (h.size, h.partial) IN (
            SELECT h2.size, h2.partial FROM media_hash h2
            JOIN media m2 ON m2.path = h2.path AND m2.missing = 0
            GROUP BY h2.size, h2.partial HAVING COUNT(DISTINCT m2.post_id) > 1)
        ORDER BY h.path""")]


def run_pass(conn, restart=None):
    """Bring media_hash up to date. Returns False when interrupted by
    ``restart`` (an Event) being set, True when done."""
    _set(running=True, paused=False, phase="partial", done=0, total=0, bytes=0,
         started_at=int(time.time()), finished_at=None)
    try:
        paths = candidates(conn)
        known = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT path, size, mtime_ns, full FROM media_hash")}
        if not _hash_all(conn, paths, known, "partial", restart):
            return False
        # Rows for files that are gone or no longer have a twin.
        keep = set(paths)
        conn.executemany("DELETE FROM media_hash WHERE path = ?", [(p,) for p in known if p not in keep])
        conn.commit()
        known = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT path, size, mtime_ns, full FROM media_hash")}
        if not _hash_all(conn, _full_needed(conn), known, "full", restart):
            return False
        _set(finished_at=int(time.time()))
        return True
    finally:
        conn.commit()
        _set(running=False, paused=False, phase=None)


def _hash_all(conn, paths, known, phase, restart):
    _set(phase=phase, done=0, total=len(paths), bytes=0)
    last_commit = time.monotonic()
    for i, path in enumerate(paths):
        if restart is not None and restart.is_set():
            return False
        if db.write_lock.locked():               # a scan or a delete: step aside
            conn.commit()
            _set(paused=True)
            while db.write_lock.locked():
                if restart is not None and restart.is_set():
                    return False
                time.sleep(0.5)
            _set(paused=False)
        st = stat(path)
        old = known.get(path)
        fresh = old is not None and st is not None and (old[0], old[1]) == st
        if st is not None and not (fresh and (phase == "partial" or old[2])):
            try:
                if phase == "partial":
                    digest = partial_hash(path, st[0])
                    full = digest if st[0] <= 2 * CHUNK else None
                    conn.execute(
                        "INSERT INTO media_hash(path, size, mtime_ns, partial, full, hashed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET size = excluded.size, "
                        "mtime_ns = excluded.mtime_ns, partial = excluded.partial, full = excluded.full, "
                        "hashed_at = excluded.hashed_at",
                        (path, st[0], st[1], digest, full, int(time.time())))
                    read = min(st[0], 2 * CHUNK)
                else:
                    full = full_hash(path, st[0])
                    if stat(path) != st:
                        raise Changed("modified while reading")
                    conn.execute("UPDATE media_hash SET full = ? WHERE path = ? AND size = ? AND mtime_ns = ?",
                                 (full, path, *st))
                    read = st[0]
                with _lock:
                    _state["bytes"] += read
            except (OSError, Changed) as e:
                _note_error(path, str(getattr(e, "strerror", None) or e))
        _set(done=i + 1)
        if time.monotonic() - last_commit >= COMMIT_EVERY:
            conn.commit()
            last_commit = time.monotonic()
    conn.commit()
    return True
