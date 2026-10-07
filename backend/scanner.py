"""Walks the media roots and brings the index in line with what is on disk.

A rescan never deletes a post. A post whose metadata file is gone is marked
missing and kept, because keeping what disappears is the point of the app.

A post the index did not have is new (posts.first_seen, see news.py),
except when a scan builds the index from nothing (a first run, a deleted or
replaced database), or builds a media root's part of it from nothing (a root
just added, with nothing indexed under it yet), or files come back from the
trash or a duplicate move: those were there before.

A file whose real path (symlinks followed) is outside every media root, or
in a root's trash, is never indexed: it is listed as unmatched with the
reason (LEADS_OUT), and /media never serves one (in_roots).
"""
import json
import os
import threading
import time

import db
import hashing
import parsers
import people
import userdata

# Folders that never hold posts but may hold images (icons in packages).
# (Dot-folders, including FeedVault's own .feedvault-trash, are skipped too.)
_SKIP_DIRS = {"venv", "node_modules", "__pycache__", "site-packages"}
TRASH_NAME = ".feedvault-trash"                # trash.py's trash folder, in each root
LEADS_OUT = "a symlink that leads out of the media roots (or into a trash): not indexed"

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


def run(roots):
    """Scan now, in the calling thread, as start() would in its own (the
    status and the hashing worker follow); waits for a scan already
    running. Returns the report."""
    while True:
        with _lock:
            if not _state["running"]:
                _state["running"] = True
                break
        time.sleep(0.5)
    _run(list(roots))
    return status()["last"]


def in_roots(path, roots):
    """Whether ``path``, symlinks followed, is inside one of ``roots`` (no
    root is inside another: config.clean_roots) and not in its trash."""
    real = os.path.realpath(path)
    for root in roots:
        r = os.path.realpath(root)
        if real.startswith(r.rstrip(os.sep) + os.sep):
            return not real.startswith(os.path.join(r, TRASH_NAME) + os.sep)
    return False


def _leading_out(roots, dirpath, names):
    """(``names`` in ``dirpath`` that stay in the media roots, [(path,
    LEADS_OUT)] for the symlinks among them that lead out of every root or
    into a trash). os.walk never enters a symlinked folder: only files are
    looked at."""
    kept, out = [], []
    for n in names:
        path = os.path.join(dirpath, n)
        if os.path.islink(path) and not in_roots(path, roots):
            out.append((path, LEADS_OUT))
        else:
            kept.append(n)
    return kept, out


def folders(top):
    """``top`` and its subfolders, skipping those a scan skips."""
    for dirpath, dirnames, filenames in os.walk(top):
        if "pyvenv.cfg" in filenames:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS)
        yield dirpath


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
        hashing.kick()                         # content hashes for the Duplicates page


def _stat(path):
    try:
        st = os.stat(path)
        return st.st_mtime, st.st_size
    except OSError:
        return None, None


_scan_lock = threading.Lock()                  # one scan at a time
# Folders whose files changed while a scan ran (files moved to or from the
# trash, a download, a save), for the scan to read again those it read
# already. None when no scan runs. Read and changed under db.write_lock.
_dirty = None


def touched(dirs):
    """Note folders whose files just changed (under db.write_lock), for a
    scan under way to read them again before it ends."""
    if _dirty is not None:
        _dirty.update(os.path.normpath(d) for d in dirs)


def scan(roots):
    """Scan synchronously and return a report.

    The scan holds db.write_lock one folder at a time (listed, parsed,
    indexed and committed under it), and again for its end, not for its
    whole run: a delete or a restore waits for one folder, not for the
    scan. Those note the folders whose files they move (touched); the scan
    reads them again at its end, under the lock, before it marks what it
    did not see as missing, so the index it leaves is the one a scan
    started after them would leave. db.scanning tells the background work
    that steps aside for the lock (hashing) to keep stepping aside between
    folders."""
    global _dirty
    with _scan_lock:
        db.scanning.set()
        try:
            with db.write_lock:
                _dirty = set()
            return _scan(roots)
        finally:
            with db.write_lock:
                _dirty = None
            db.scanning.clear()


class _Folder:
    """What a scan found in one folder."""
    __slots__ = ("unmatched", "copies", "profiles", "account_files", "errors")

    def __init__(self):
        self.unmatched, self.copies, self.profiles, self.account_files, self.errors = [], [], [], [], []


def _files_in(dirpath):
    """The names of the files in ``dirpath`` a scan reads, as os.walk lists
    them (a symlink to a folder is a folder), or None when it is gone."""
    try:
        with os.scandir(dirpath) as it:
            return [e.name for e in it if not e.name.startswith(".") and not e.is_dir()]
    except OSError:
        return None


def _read_folder(conn, roots, root, dirpath, names, fresh, started, report, seen_meta):
    """Parse and index one folder's files (under db.write_lock). Returns
    what it found besides the posts, as a _Folder."""
    out = _Folder()
    names, leading = _leading_out(roots, dirpath, names)
    out.unmatched.extend((path, *_size_mtime(path), reason) for path, reason in leading)
    if not names:
        return out
    result = parsers.parse_dir(root, dirpath, names)
    out.profiles.extend(result.profiles)
    out.account_files.extend(result.account_files)
    for path, message in result.errors:
        out.errors.append({"path": path, "error": message})
        out.unmatched.append((path, *_size_mtime(path), message))
    for path, reason in result.skipped:
        out.unmatched.append((path, *_size_mtime(path), reason))
    # Stamped per folder, right before its commit: a "Mark all seen" while
    # the scan runs leaves the folders committed after it new. Not new at
    # all while the root's part of the index is built from nothing.
    first_seen = 0 if fresh else int(time.time())
    for post in result.posts:
        _index_post(conn, post, started, report, seen_meta, out.unmatched, out.copies, first_seen)
    for n in names:
        if n not in result.claimed and parsers.is_media(n):
            path = os.path.join(dirpath, n)
            out.unmatched.append((path, *_size_mtime(path), "no metadata file for this media"))
    return out


def _walked(root, dirpath):
    """Whether a scan of ``root`` walks ``dirpath``, as _scan prunes os.walk:
    no dot-folder or _SKIP_DIRS on the way, no virtualenv above it."""
    rel = os.path.relpath(dirpath, root)
    parts = [] if rel == "." else rel.split(os.sep)
    if any(p == ".." or p.startswith(".") or p in _SKIP_DIRS for p in parts):
        return False
    d = root
    for p in [None, *parts]:
        d = d if p is None else os.path.join(d, p)
        if os.path.exists(os.path.join(d, "pyvenv.cfg")):
            return False
    return True


def _root_of(path, roots):
    """The root of ``roots`` that ``path`` is (or is under), as configured."""
    for r in roots:
        if path == os.path.normpath(r) or path.startswith(r.rstrip(os.sep) + os.sep):
            return r
    return None


def _scan(roots):
    conn = db.connect()
    started = int(time.time())
    report = {"started_at": started, "finished_at": None,
              "added": 0, "updated": 0, "missing": 0, "unmatched": 0, "errors": []}
    with db.write_lock:
        restored = _finish_restores(roots)
        # Building the index, or a root's part of it, from nothing: what it
        # finds is not new. Noted in the database until the build ends, so the
        # scan after one that was cut short (FeedVault killed, the machine off)
        # goes on building it, instead of finding the rest new.
        fresh = {root: nothing_under(conn, root) or _building(conn, root) for root in roots}
        with conn:
            conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, '1')",
                             [(BUILDING + root,) for root in roots if fresh[root]])
    seen_meta = set()
    found = {}                                 # folder -> _Folder

    def read(root, dirpath):
        """One folder, under db.write_lock, listed again there: a delete may
        have moved files since os.walk listed it."""
        names = _files_in(dirpath)
        if names is None:
            found.pop(dirpath, None)
            return
        found[dirpath] = _read_folder(conn, roots, root, dirpath, names, fresh[root], started, report, seen_meta)
        conn.commit()

    walked = []                                # the roots walked
    for root in roots:
        if not os.path.isdir(root):
            report["errors"].append({"path": root, "error": "media root not found"})
            continue
        walked.append(root)
        for dirpath, dirnames, filenames in os.walk(root):
            if "pyvenv.cfg" in filenames:     # a Python virtualenv parked in the folder
                dirnames[:] = []
                continue
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS)
            if all(n.startswith(".") for n in filenames):
                continue
            with db.write_lock:
                read(root, dirpath)
            db.write_lock.let_in()            # a delete or a restore waiting goes now

    with db.write_lock:
        # The folders others changed while it ran: read again (one made
        # after os.walk listed its parent is read for the first time).
        for d in sorted(_dirty):
            root = _root_of(d, walked)
            if root is not None and os.path.isdir(d) and _walked(root, d):
                read(root, d)
            elif d in found and not os.path.isdir(d):
                del found[d]
        unmatched, copies, profiles, account_files = [], [], [], []
        for f in found.values():
            unmatched += f.unmatched
            copies += f.copies
            profiles += f.profiles
            account_files += f.account_files
            report["errors"] += f.errors
        report["missing"], trashed = _mark_missing(conn, seen_meta, walked)
        db.save_copies(conn, copies, started, prune=True)
        db.save_profiles(conn, profiles, prune=True)
        db.save_account_files(conn, account_files, prune=walked)
        changed = people.refresh_aliases(conn)
        conn.execute("DELETE FROM unmatched")
        conn.executemany("INSERT OR REPLACE INTO unmatched(path, size, mtime, reason) VALUES (?, ?, ?, ?)",
                         unmatched)
        conn.executemany("DELETE FROM meta WHERE key = ?", [(BUILDING + root,) for root in walked])
        conn.commit()
    _changed(dict.fromkeys(restored + changed + (["decisions"] if trashed else [])))   # their decisions went too
    report["unmatched"] = len(unmatched)
    report["finished_at"] = int(time.time())
    return report


def _finish_restores(roots):
    """Restores cut short (trash.finish_restores), under db.write_lock.
    Returns the user tables they changed."""
    import config
    import trash                               # imports this module
    try:
        data_dir = config.load().get("data_directory")
    except Exception:                          # thumbnails and archives wait for the next restore
        data_dir = None
    return trash.finish_restores(roots, data_dir)


def _changed(tables):
    """Note the user tables a scan changed (links moved to an account's id,
    see people.refresh_aliases), for userdata.py to write."""
    for name in tables:
        userdata.changed(name)


BUILDING = "building:"                         # meta key prefix: a root's first scan under way


def _building(conn, root):
    """Whether a scan that was building ``root``'s part of the index from
    nothing did not finish."""
    return conn.execute("SELECT 1 FROM meta WHERE key = ?", (BUILDING + root,)).fetchone() is not None


def nothing_under(conn, root):
    """Whether no post, missing ones included, is indexed under ``root``
    (meta paths are stored under the root as configured)."""
    prefix = root.rstrip(os.sep) + os.sep
    # A range on the unique meta_path index: every path starting with the prefix.
    return conn.execute("SELECT 1 FROM posts WHERE meta_path >= ? AND meta_path < ? LIMIT 1",
                        (prefix, prefix[:-1] + chr(ord(os.sep) + 1))).fetchone() is None


def _written_since(path, since):
    """Whether the file was written at ``since`` or later. Its ctime too, not
    only its mtime: gallery-dl (and yt-dlp with --mtime) set the mtime to the
    server's Last-Modified, which can be years old for a file just written."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return max(st.st_mtime, st.st_ctime) >= since


def _size_mtime(path):
    mtime, size = _stat(path)
    return size, mtime


def _index_post(conn, post, now, report, seen_meta, unmatched, copies, first_seen):
    mtime, size = _stat(post.meta_path)
    existing = conn.execute("SELECT meta_path, meta_mtime, meta_size, missing, side_files FROM posts WHERE id = ?",
                            (post.id,)).fetchone()

    # The same post downloaded twice into different folders: first one wins
    # while its file still exists; the copy is reported, not indexed, and
    # recorded with its files for the Duplicates page.
    if (existing and existing["meta_path"] != post.meta_path
            and (existing["meta_path"] in seen_meta or os.path.exists(existing["meta_path"]))):
        unmatched.append((post.meta_path, size, mtime,
                          f"duplicate of {post.id} ({existing['meta_path']})"))
        copies.append((post, mtime))
        return

    # A metadata file that used to describe a different post id.
    conn.execute("DELETE FROM posts_fts WHERE rowid IN (SELECT n FROM posts WHERE meta_path = ? AND id != ?)",
                 (post.meta_path, post.id))
    conn.execute("DELETE FROM posts WHERE meta_path = ? AND id != ?", (post.meta_path, post.id))

    seen_meta.add(post.meta_path)
    if existing and existing["meta_path"] == post.meta_path and not existing["missing"] \
            and existing["meta_mtime"] == mtime and existing["meta_size"] == size \
            and existing["side_files"] == json.dumps(post.side_files):
        have = {r[0] for r in conn.execute("SELECT path FROM media WHERE post_id = ? AND missing = 0",
                                           (post.id,))}
        if have == {m.path for m in post.media}:
            return                             # nothing changed
    outcome = db.upsert_post(conn, post, mtime, size, now, first_seen)
    report[outcome] += 1


def _mark_missing(conn, seen_meta, roots=()):
    """Flag posts whose metadata was not found this scan. Returns how many
    are newly missing, and the posts dropped as in the trash.

    A post whose metadata file is in a trash folder, moved there as this
    post's (its manifest line says so), is dropped from the index instead,
    as the deletion would have done: FeedVault was stopped halfway through
    one, after the files moved but before the index was told."""
    newly, trashed = 0, []
    unseen = [r for r in conn.execute("SELECT id, meta_path, missing FROM posts").fetchall()
              if r["meta_path"] not in seen_meta]
    if unseen and roots:
        import trash                           # imports this module
        there = trash.in_trash(roots, {(r["id"], r["meta_path"]) for r in unseen})
        for row in unseen:
            if (row["id"], row["meta_path"]) in there:
                db.remove_post(conn, row["id"])
                trashed.append(row["id"])
        if trashed:
            print(f"[scan] {len(trashed)} posts were in the trash but still indexed "
                  f"(a deletion cut short): dropped from the index")
    for row in unseen:
        if row["id"] in trashed:
            continue
        if not row["missing"]:
            newly += 1
            conn.execute("UPDATE posts SET missing = 1 WHERE id = ?", (row["id"],))
        for m in conn.execute("SELECT id, path FROM media WHERE post_id = ?", (row["id"],)).fetchall():
            gone = 0 if os.path.exists(m["path"]) else 1
            conn.execute("UPDATE media SET missing = ? WHERE id = ?", (gone, m["id"]))
    return newly, trashed


def index_dirs(roots, dirs, new=False, since=None):
    """Re-index just these folders, right away: used after a restore from the
    trash, where a full rescan would be far too slow for an undo key, and
    after a download (``new``: the posts it adds are new). With ``since`` (a
    download's start), the report also counts the media files changed since
    that no parser could read (``unread``)."""
    with db.write_lock:
        report, changed = index_dirs_locked(roots, dirs, new, since)
    _changed(changed)
    hashing.kick()                             # files back from the trash may need hashing again
    return report


def index_dirs_locked(roots, dirs, new=False, since=None):
    """index_dirs, under db.write_lock held by the caller (a restore).
    Returns (report, the user tables changed): the caller hands those to
    _changed, and calls hashing.kick, once the lock is released."""
    touched(dirs)                              # a scan under way reads them again
    conn = db.connect()
    now = int(time.time())
    first_seen = now if new else 0
    report = {"added": 0, "updated": 0, "unread": 0}
    unmatched, copies, indexed, profiles, account_files, read = [], [], [], [], [], set()
    for d in sorted(set(dirs)):
        real = os.path.realpath(d)
        root = next((r for r in roots
                     if real == os.path.realpath(r)
                     or real.startswith(os.path.realpath(r).rstrip(os.sep) + os.sep)), None)
        if root is None or not os.path.isdir(d):
            continue
        names = [n for n in os.listdir(d) if not n.startswith(".") and os.path.isfile(os.path.join(d, n))]
        names, out = _leading_out(roots, d, names)
        unmatched.extend((path, *_size_mtime(path), reason) for path, reason in out)
        result = parsers.parse_dir(root, d, names)
        profiles.extend(result.profiles)
        account_files.extend(result.account_files)
        read.add(d)
        seen = set()
        for post in result.posts:
            _index_post(conn, post, now, report, seen, unmatched, copies, first_seen)
        indexed.extend(seen)
        conn.execute(f"DELETE FROM unmatched WHERE path IN ({', '.join('?' for _ in result.claimed) or 'NULL'})",
                     [os.path.join(d, n) for n in result.claimed])
        unmatched.extend((path, *_size_mtime(path), reason) for path, reason in result.skipped)
        if since is not None:
            report["unread"] += sum(1 for n in names if n not in result.claimed and parsers.is_media(n)
                                    and _written_since(os.path.join(d, n), since))
    # A copy that is back keeps its "duplicate of" line; one that became
    # the post (the first copy was trashed) is no longer a copy.
    conn.executemany("INSERT OR REPLACE INTO unmatched(path, size, mtime, reason) VALUES (?, ?, ?, ?)",
                     unmatched)
    conn.executemany("DELETE FROM copies WHERE meta_path = ?", [(p,) for p in indexed])
    db.save_copies(conn, copies, now, prune=False)
    db.save_profiles(conn, profiles, prune=False)
    db.save_account_files(conn, account_files, prune=False, dirs=read)
    changed = people.refresh_aliases(conn)
    conn.commit()
    return report, changed
