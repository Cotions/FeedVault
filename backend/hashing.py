"""Content hashes of media files, for finding exact duplicates.

Only files that could have a twin are read: those whose recorded size another
file shares (indexed media and the files of extra copies). Each gets a sha1 of
its first and last MiB (``partial``); a file of 2 MiB or less is read whole,
so that is its full hash as well. A full sha1 of a bigger file is computed
only where partial hashes collide: between different posts, or between an
item of a post and the same item of its copy.

Hashes are cached in the media_hash table and stay valid while a file's size
and mtime do not change, so a pass after a rescan only reads new or changed
files, and an interrupted pass resumes where it stopped.

After the content hashes, every image and video gets a perceptual hash (a
64-bit dHash) for finding visually similar media: resized, recompressed or
reposted copies whose bytes differ. It is taken from the cached grid
thumbnail when there is one, else from the image itself or the video's
poster, else from a frame ffmpeg extracts (cached as the thumbnail, as the
grid would). Videos that end up in a group are then measured with ffprobe,
for the suggested keeper.

The worker is one background thread at the lowest CPU and I/O priority. It
starts after every scan, steps aside while anything holds db.write_lock (a
scan, a delete, a restore), and commits in small batches so requests never
wait on it. Each phase first stats its files and keeps those that are new
or changed: a pass after a rescan that changed nothing reads no file, and
its progress counts only what is left to do.

The worker also gives way to the dashboard: before each file it waits
while a request is being answered (app.py counts them, see
request_started), up to YIELD_MAX. Python runs one thread at a time, so a
request answered while the worker decoded pictures took several times as
long (a feed page: 3 ms idle, 17 ms during a pass, docs/TESTING.md); now
the files wait for the request instead.
"""
import hashlib
import json
import os
import queue
import stat as stat_module
import subprocess
import sys
import threading
import time

import config
import db
import thumbs
from parsers import IMAGE_EXT, ext_of

CHUNK = 1 << 20                                  # 1 MiB
# Seconds between writes. Results wait in memory and go in one short
# transaction, never held open while a file is read; every commit also
# drops the API's cached aggregates (db._memo), so not too often.
COMMIT_EVERY = 10.0
ERRORS_KEPT = 20
# Threads for perceptual hashes. Most of a file's time there is waiting: on
# the disk, on ffmpeg for a video frame (20k videos without a poster on the
# reference archive), or in Pillow's decoder, which releases the GIL.
PICTURE_WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))
# Seconds a file waits for the requests being answered to finish. Past
# that it is read anyway, so a dashboard that never stops asking (a feed
# loading thumbnails) slows the pass down without stopping it.
YIELD_MAX = 1.0

_wake = threading.Event()
_requests = 0                                    # being answered now, see request_started
_idle = threading.Condition()
_lock = threading.Lock()
_thread = None
_state = {"running": False, "paused": False, "phase": None, "done": 0, "total": 0,
          "bytes": 0, "started_at": None, "finished_at": None, "errors": []}


def status(conn=None):
    with _lock:
        out = {**_state, "errors": list(_state["errors"])}
    conn = conn or db.connect()
    out["hashed"], out["fingerprinted"] = conn.execute(
        "SELECT COUNT(partial), COUNT(dhash) FROM media_hash").fetchone()
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


def request_started():
    """A request is being answered (app.py, before_request): the worker
    waits before its next file. Every call is matched by one
    request_finished."""
    global _requests
    with _idle:
        _requests += 1


def request_finished():
    global _requests
    with _idle:
        _requests = max(0, _requests - 1)
        if _requests == 0:
            _idle.notify_all()


def _wait_idle(limit=None):
    """Until no request is being answered, at most ``limit`` seconds
    (YIELD_MAX)."""
    with _idle:
        _idle.wait_for(lambda: _requests == 0, YIELD_MAX if limit is None else limit)


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


def _open(path):
    """``path`` opened for reading, as a regular file: never a FIFO, whose
    open would wait for a writer and stall the pass, nor a device, which
    can be read forever (a file replaced by one since it was stat'ed)."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat_module.S_ISREG(os.fstat(fd).st_mode):
            raise Changed("not a regular file any more")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def partial_hash(path, size):
    h = hashlib.sha1()
    with _open(path) as f:
        if size <= 2 * CHUNK:
            data = f.read(size + 1)              # never more: the file may have grown since
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
    with _open(path) as f:
        # One byte past ``size`` at most: enough to tell it grew.
        while n <= size and (chunk := f.read(min(CHUNK, size + 1 - n))):
            h.update(chunk)
            n += len(chunk)
    if n != size:
        raise Changed("size changed while reading")
    return h.hexdigest()


def dimensions(path):
    """(width, height) of an image from its header, or (None, None)."""
    if ext_of(path) not in IMAGE_EXT:
        return None, None
    try:
        with thumbs.open_image(path) as im:
            return im.size
    except Exception:                            # not an image after all, or Pillow missing
        return None, None


def dhash(path):
    """64-bit difference hash: the picture shrunk to 9x8 grey pixels, one bit
    per pair of neighbours in a row (is the left one brighter). Resizing and
    recompression leave it nearly unchanged; a crop or a filter moves a few
    bits. The decoder skips detail it does not need (JPEG at 1/8 scale)."""
    from PIL import Image, ImageOps
    with thumbs.open_image(path) as im:
        im.draft("L", (64, 64))
        small = ImageOps.exif_transpose(im).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = small.tobytes()
    value = 0
    for row in range(0, 72, 9):
        for col in range(row, row + 8):
            value = value << 1 | (px[col] > px[col + 1])
    return value


def to_db(value):
    """A 64-bit hash as SQLite's signed INTEGER, and back."""
    return value - (1 << 64) if value is not None and value >= 1 << 63 else value


def from_db(value):
    return value + (1 << 64) if value is not None and value < 0 else value


def video_size(path, ffprobe=None):
    """(width, height) of a video's first video stream from the ffprobe at
    ``ffprobe`` (else thumbs.ffprobe_path's), or None (None too when there
    is no ffprobe)."""
    ffprobe = ffprobe or thumbs.ffprobe_path()
    if ffprobe is None:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "csv=p=0:s=x", path],
            capture_output=True, text=True, timeout=30)
        w, h = r.stdout.strip().splitlines()[0].split("x")[:2]
        return int(w), int(h)
    except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
        return None


def stat(path):
    """(size, mtime_ns), or None when the file is gone."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _file_stat(path):
    """stat(), or None for anything but a regular file (symlinks followed):
    a FIFO or a device in a media file's place is never read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns) if stat_module.S_ISREG(st.st_mode) else None


# ---------------------------------------------------------------------------
# A pass
# ---------------------------------------------------------------------------

def candidates(conn):
    """Paths whose recorded size another file shares, and every file of a
    post that has copies (compared item by item, and measured for the
    suggested keeper even when sizes differ). Those come first: the
    Duplicates page shows them right away."""
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
    out = [p for p, s in sizes.items() if count[s] > 1 or p in first]
    out.sort(key=lambda p: (p not in first, p))  # then by path, close together on disk
    return out


def _full_needed(conn):
    """Paths without a full hash whose size and partial hash match another
    file's: of another post, or the same item of a post and its copy. A copy
    is only called identical after a whole-file match, so one corrupt in the
    middle is never trashed in place of a good one."""
    out = {r[0] for r in conn.execute("""
        SELECT h.path FROM media_hash h JOIN media m ON m.path = h.path AND m.missing = 0
        WHERE h.full IS NULL AND (h.size, h.partial) IN (
            SELECT h2.size, h2.partial FROM media_hash h2
            JOIN media m2 ON m2.path = h2.path AND m2.missing = 0
            WHERE h2.partial IS NOT NULL
            GROUP BY h2.size, h2.partial HAVING COUNT(DISTINCT m2.post_id) > 1)""")}
    rows = {r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT path, size, partial, full FROM media_hash")}
    for post_id, media in conn.execute("SELECT post_id, media FROM copies"):
        mine = {r[0]: r[1] for r in conn.execute(
            "SELECT idx, path FROM media WHERE post_id = ? AND missing = 0", (post_id,))}
        for m in json.loads(media):
            a, b = rows.get(mine.get(m["idx"])), rows.get(m["path"])
            if a and b and a[1] and a[:2] == b[:2]:
                out.update(p for p, h in ((mine[m["idx"]], a), (m["path"], b)) if h[2] is None)
    return sorted(out)


def pictures(conn):
    """path -> (kind, poster_path) of every image and video in the index:
    what gets a perceptual hash. Extra copies do not; the copies kind
    already compares them item by item."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT path, kind, poster_path FROM media WHERE missing = 0 AND kind IN ('image', 'video') "
        "ORDER BY path")}


def _known(conn):
    """path -> (size, mtime_ns, partial, full, dhash_at)."""
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT path, size, mtime_ns, partial, full, dhash_at FROM media_hash")}


def run_pass(conn, restart=None, data_dir=None):
    """Bring media_hash up to date. Returns False when interrupted by
    ``restart`` (an Event) being set, True when done."""
    _set(running=True, paused=False, phase="partial", done=0, total=0, bytes=0,
         started_at=int(time.time()), finished_at=None)
    data_dir = data_dir or config.load()["data_directory"]
    try:
        _wait_idle()                              # each step between phases reads the index whole
        paths = candidates(conn)
        known = _known(conn)
        if not _hash_all(conn, paths, "partial", restart, _fresh(known, 2), _partial_row):
            return False
        # Rows for files that are gone, or neither have a twin nor are pictures.
        _wait_idle()
        keep = set(paths) | set(pictures(conn))
        conn.executemany("DELETE FROM media_hash WHERE path = ?", [(p,) for p in known if p not in keep])
        conn.commit()
        known = _known(conn)
        if not _hash_all(conn, _full_needed(conn), "full", restart, _fresh(known, 3), _full_row):
            return False
        _wait_idle()
        pics = pictures(conn)
        known = _known(conn)
        if not _hash_all(conn, list(pics), "dhash", restart, _fresh(known, 4),
                         lambda path, st: _dhash_row(path, st, *pics[path], data_dir), PICTURE_WORKERS):
            return False
        ffprobe = thumbs.ffprobe_path()           # looked up once for the pass
        if ffprobe:
            import duplicates                     # it imports this module
            _wait_idle()
            if not _hash_all(conn, duplicates.videos_to_measure(conn, _wait_idle), "probe", restart,
                             lambda path, st: False, lambda path, st: _probe_row(path, st, ffprobe),
                             PICTURE_WORKERS):
                return False
        _set(finished_at=int(time.time()))
        return True
    finally:
        conn.commit()
        _set(running=False, paused=False, phase=None)


def _fresh(known, field):
    """Whether a file's row is up to date (same size and mtime) and has
    ``field`` (an index into the _known tuple) filled in."""
    def fresh(path, st):
        old = known.get(path)
        return old is not None and (old[0], old[1]) == st and old[field] is not None
    return fresh


def _partial_row(path, st):
    digest = partial_hash(path, st[0])
    size = dimensions(path)
    if stat(path) != st:                         # rewritten in place at the same size: not this hash
        raise Changed("modified while reading")
    full = digest if st[0] <= 2 * CHUNK else None
    return (path, *st, digest, full, *size, int(time.time())), min(st[0], 2 * CHUNK)


def _full_row(path, st):
    full = full_hash(path, st[0])
    if stat(path) != st:
        raise Changed("modified while reading")
    return (full, path, *st), st[0]


def _dhash_row(path, st, kind, poster, data_dir):
    """A row with the file's dHash, or a NULL one (tried, not retried until
    the file changes) when its picture cannot be decoded. None, to try again
    next pass, for a video without a poster while ffmpeg is not installed."""
    row = {"path": path, "kind": kind, "poster_path": poster}
    src = thumbs.cached(data_dir, row)
    if src is None and (kind == "image" or poster):
        src = poster or path
    elif src is None:
        if not thumbs.have_ffmpeg():
            return None, 0
        src = thumbs.thumb_for(data_dir, row)    # a frame from ffmpeg, kept as the grid's thumbnail
    value, read = None, 0
    if src is not None:
        try:
            read = os.path.getsize(src)
            value = dhash(src)
        except FileNotFoundError:
            raise
        except Exception as e:                   # any decoder or EXIF error: one bad file must not stop the pass
            _note_error(path, f"not a readable picture: {type(e).__name__}: {e}")
    if stat(path) != st:
        raise Changed("modified while reading")
    width, height = dimensions(path) if kind == "image" else (None, None)
    now = int(time.time())
    return (path, *st, width, height, now, to_db(value), now), read


def _probe_row(path, st, ffprobe):
    size = video_size(path, ffprobe) or (0, 0)            # 0: measured, unknown; not retried until the file changes
    return (*size, path, *st), 0


def _stale(paths, fresh):
    """The paths whose file is there and not ``fresh``: what a phase has to
    read. A stat each, no file opened."""
    out = []
    for path in paths:
        st = _file_stat(path)
        if st is not None and not fresh(path, st):
            out.append(path)
    return out


def _turn(restart):
    """Wait until the next file may be read: not while anything holds
    db.write_lock (a scan, a delete), nor while a request is being answered
    (up to YIELD_MAX). False when the pass is to stop."""
    if db.writes_busy():
        _set(paused=True)
        while db.writes_busy():
            if restart is not None and restart.is_set():
                return False
            time.sleep(0.5)
        _set(paused=False)
    _wait_idle()
    return restart is None or not restart.is_set()


_DONE = object()


def _results(paths, one, workers, restart):
    """``one(path)`` for each path, in the order they finish, from
    ``workers`` threads (this one when 1). Each thread takes the next file
    as soon as it is free, so one slow file (a video frame from ffmpeg)
    holds up only its own thread. The threads are daemons: shutting the
    app down never waits for a file, and what was not written is read
    again next pass."""
    if workers <= 1:
        for path in paths:
            if not _turn(restart):
                return
            yield one(path)
        return
    out = queue.Queue()
    todo = iter(paths)
    take = threading.Lock()
    stop = threading.Event()

    def worker():
        _lower_priority()
        try:
            while not stop.is_set():
                with take:
                    path = next(todo, None)
                if path is None or not _turn(restart):
                    return
                out.put(one(path))
        except BaseException as e:               # handed to the pass, which reports it
            out.put(e)
        finally:
            out.put(_DONE)

    threads = [threading.Thread(target=worker, daemon=True, name=f"hashing-{n}") for n in range(workers)]
    for t in threads:
        t.start()
    running = len(threads)
    try:
        while running:
            item = out.get()
            if item is _DONE:
                running -= 1
            elif isinstance(item, BaseException):
                raise item
            else:
                yield item
    finally:
        stop.set()


def _hash_all(conn, paths, phase, restart, fresh, work, workers=1):
    """Run ``work(path, (size, mtime_ns))`` -> (row or None, bytes read) on
    every path that is not ``fresh``, writing the rows in batches. With
    ``workers``, that many threads (at the same low priority) each take the
    next file when free. Returns False when stopped by ``restart`` before
    every file was read."""
    if restart is not None and restart.is_set():
        return False
    paths = _stale(paths, fresh)
    _set(phase=phase, done=0, total=len(paths), bytes=0)
    pending = []                                 # rows to write, see COMMIT_EVERY
    last_write = time.monotonic()

    def one(path):
        st = _file_stat(path)                    # again: it may have changed since _stale
        if st is None or fresh(path, st):
            return None, 0
        try:
            return work(path, st)
        except (OSError, Changed) as e:
            _note_error(path, str(getattr(e, "strerror", None) or e))
            return None, 0

    done = 0
    for row, read in _results(paths, one, workers, restart):
        done += 1
        if row is not None:
            pending.append(row)
        with _lock:
            _state["bytes"] += read
            _state["done"] = done
        # Not while a scan or a delete holds the index: the rows wait.
        if time.monotonic() - last_write >= COMMIT_EVERY and not db.writes_busy():
            _write(conn, phase, pending)
            last_write = time.monotonic()
    finished = done == len(paths)
    # Rows of files read before a stop are good too, unless a scan holds the
    # index: then they are read again next pass rather than wait for it.
    if finished or not db.writes_busy():
        _write(conn, phase, pending)
    return finished


# A row keeps what another phase stored only while it still describes the
# same file (size and mtime unchanged). SQLite evaluates every SET against
# the row as it was, so media_hash.size below is the old size.
_SAME = "media_hash.size = excluded.size AND media_hash.mtime_ns = excluded.mtime_ns"
_WRITES = {
    "partial":
        "INSERT INTO media_hash(path, size, mtime_ns, partial, full, width, height, hashed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET size = excluded.size, "
        "mtime_ns = excluded.mtime_ns, partial = excluded.partial, full = excluded.full, "
        f"width = COALESCE(excluded.width, CASE WHEN {_SAME} THEN media_hash.width END), "
        f"height = COALESCE(excluded.height, CASE WHEN {_SAME} THEN media_hash.height END), "
        f"dhash = CASE WHEN {_SAME} THEN media_hash.dhash END, "
        f"dhash_at = CASE WHEN {_SAME} THEN media_hash.dhash_at END, hashed_at = excluded.hashed_at",
    # Only if the file is still the one that was hashed.
    "full": "UPDATE media_hash SET full = ? WHERE path = ? AND size = ? AND mtime_ns = ?",
    "dhash":
        "INSERT INTO media_hash(path, size, mtime_ns, width, height, hashed_at, dhash, dhash_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET size = excluded.size, "
        "mtime_ns = excluded.mtime_ns, dhash = excluded.dhash, dhash_at = excluded.dhash_at, "
        f"partial = CASE WHEN {_SAME} THEN media_hash.partial END, "
        f"full = CASE WHEN {_SAME} THEN media_hash.full END, "
        f"width = COALESCE(excluded.width, CASE WHEN {_SAME} THEN media_hash.width END), "
        f"height = COALESCE(excluded.height, CASE WHEN {_SAME} THEN media_hash.height END)",
    "probe": "UPDATE media_hash SET width = ?, height = ? WHERE path = ? AND size = ? AND mtime_ns = ?",
}


def _write(conn, phase, pending):
    if not pending:
        return
    conn.executemany(_WRITES[phase], pending)
    conn.commit()
    pending.clear()
