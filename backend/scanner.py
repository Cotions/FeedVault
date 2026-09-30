"""Walks the media roots and brings the index in line with what is on disk.

A rescan never deletes a post. A post whose metadata file is gone is marked
missing and kept, because keeping what disappears is the point of the app.
"""
import os
import threading
import time

import db
import parsers

# Folders that never hold posts but may hold images (icons in packages).
# (Dot-folders, including FeedVault's own .feedvault-trash, are skipped too.)
_SKIP_DIRS = {"venv", "node_modules", "__pycache__", "site-packages"}

_lock = threading.Lock()
_state = {"running": False, "last": None}


def status():
    with _lock:
        return {"running": _state["running"], "last": _state["last"]}


def start(roots):
    """Run a scan in a background thread. False if one is already running."""
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_run, args=(list(roots),), daemon=True, name="scan").start()
    return True


def _run(roots):
    report = None
    try:
        report = scan(roots)
    except Exception as e:                     # keep the app alive; show it in the UI
        report = {"started_at": int(time.time()), "finished_at": int(time.time()),
                  "added": 0, "updated": 0, "missing": 0, "unmatched": 0,
                  "errors": [{"path": "", "error": f"scan failed: {e}"}]}
    finally:
        with _lock:
            _state["running"] = False
            _state["last"] = report


def _stat(path):
    try:
        st = os.stat(path)
        return st.st_mtime, st.st_size
    except OSError:
        return None, None


def scan(roots):
    """Scan synchronously and return a report."""
    with db.write_lock:
        return _scan(roots)


def _scan(roots):
    conn = db.connect()
    started = int(time.time())
    report = {"started_at": started, "finished_at": None,
              "added": 0, "updated": 0, "missing": 0, "unmatched": 0, "errors": []}
    seen_meta = set()
    unmatched = []                             # (path, size, mtime, reason)

    for root in roots:
        if not os.path.isdir(root):
            report["errors"].append({"path": root, "error": "media root not found"})
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if "pyvenv.cfg" in filenames:     # a Python virtualenv parked in the folder
                dirnames[:] = []
                continue
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS)
            names = [n for n in filenames if not n.startswith(".")]
            if not names:
                continue
            result = parsers.parse_dir(root, dirpath, names)
            for path, message in result.errors:
                report["errors"].append({"path": path, "error": message})
                unmatched.append((path, *_size_mtime(path), message))
            for post in result.posts:
                _index_post(conn, post, started, report, seen_meta, unmatched)
            for n in names:
                if n not in result.claimed and parsers.is_media(n):
                    path = os.path.join(dirpath, n)
                    unmatched.append((path, *_size_mtime(path), "no metadata file for this media"))
            conn.commit()

    report["missing"] = _mark_missing(conn, seen_meta)
    conn.execute("DELETE FROM unmatched")
    conn.executemany("INSERT OR REPLACE INTO unmatched(path, size, mtime, reason) VALUES (?, ?, ?, ?)",
                     unmatched)
    conn.commit()
    report["unmatched"] = len(unmatched)
    report["finished_at"] = int(time.time())
    return report


def _size_mtime(path):
    mtime, size = _stat(path)
    return size, mtime


def _index_post(conn, post, now, report, seen_meta, unmatched):
    mtime, size = _stat(post.meta_path)
    existing = conn.execute("SELECT meta_path, meta_mtime, meta_size, missing FROM posts WHERE id = ?",
                            (post.id,)).fetchone()

    # The same post downloaded twice into different folders: first one wins
    # while its file still exists; the copy is reported, not indexed.
    if (existing and existing["meta_path"] != post.meta_path
            and (existing["meta_path"] in seen_meta or os.path.exists(existing["meta_path"]))):
        unmatched.append((post.meta_path, size, mtime,
                          f"duplicate of {post.id} ({existing['meta_path']})"))
        return

    # A metadata file that used to describe a different post id.
    conn.execute("DELETE FROM posts_fts WHERE rowid IN (SELECT n FROM posts WHERE meta_path = ? AND id != ?)",
                 (post.meta_path, post.id))
    conn.execute("DELETE FROM posts WHERE meta_path = ? AND id != ?", (post.meta_path, post.id))

    seen_meta.add(post.meta_path)
    if existing and existing["meta_path"] == post.meta_path and not existing["missing"] \
            and existing["meta_mtime"] == mtime and existing["meta_size"] == size:
        have = {r[0] for r in conn.execute("SELECT path FROM media WHERE post_id = ? AND missing = 0",
                                           (post.id,))}
        if have == {m.path for m in post.media}:
            return                             # nothing changed
    outcome = db.upsert_post(conn, post, mtime, size, now)
    report[outcome] += 1


def _mark_missing(conn, seen_meta):
    """Flag posts whose metadata was not found this scan. Returns how many are newly missing."""
    newly = 0
    for row in conn.execute("SELECT id, meta_path, missing FROM posts").fetchall():
        if row["meta_path"] in seen_meta:
            continue
        if not row["missing"]:
            newly += 1
            conn.execute("UPDATE posts SET missing = 1 WHERE id = ?", (row["id"],))
        for m in conn.execute("SELECT id, path FROM media WHERE post_id = ?", (row["id"],)).fetchall():
            gone = 0 if os.path.exists(m["path"]) else 1
            conn.execute("UPDATE media SET missing = ? WHERE id = ?", (gone, m["id"]))
    return newly


def index_dirs(roots, dirs):
    """Re-index just these folders, right away: used after a restore from the
    trash, where a full rescan would be far too slow for an undo key."""
    with db.write_lock:
        conn = db.connect()
        now = int(time.time())
        report = {"added": 0, "updated": 0}
        for d in sorted(set(dirs)):
            real = os.path.realpath(d)
            root = next((r for r in roots
                         if real == os.path.realpath(r)
                         or real.startswith(os.path.realpath(r).rstrip(os.sep) + os.sep)), None)
            if root is None or not os.path.isdir(d):
                continue
            names = [n for n in os.listdir(d) if not n.startswith(".") and os.path.isfile(os.path.join(d, n))]
            result = parsers.parse_dir(root, d, names)
            for post in result.posts:
                _index_post(conn, post, now, report, set(), [])
            conn.execute(f"DELETE FROM unmatched WHERE path IN ({', '.join('?' for _ in result.claimed) or 'NULL'})",
                         [os.path.join(d, n) for n in result.claimed])
        conn.commit()
        return report
