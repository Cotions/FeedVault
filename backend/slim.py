"""instaloader's metadata files, slimmed and compressed.

instaloader writes Instagram's whole answer beside each post: tens of
kilobytes, most of it nothing FeedVault reads (parsers.instaloader.slim says
what it keeps). A slim file holds what the parser reads and is
xz-compressed, a few hundred bytes.

- New downloads are compressed by instaloader itself (``.json.xz``, sync.py
  and save.py) and complete. With the metadata mode "slim" (instaloader's
  setting, or a source's own; "full" by default), the files a sync or a
  save wrote are slimmed as soon as it ends (after, files).
- The files already there are rewritten by a job the user starts (kind
  ``slim-metadata``): ``.json`` becomes ``.json.xz``, a ``.json.xz`` that
  is not slim is slimmed. Its ``mode`` "preview" changes nothing and counts
  what "apply" would do, with the exact sizes.

What was taken out cannot be put back: for a post Instagram no longer has,
it is gone. So one file at a time (rewrite):

1. read it, never through a symlink; it must be instaloader's and read as a
   post or a profile (parsers.instaloader.reads), else it is left alone;
2. write the slim form to a temporary file in the same folder, with the
   original's mode, owner and times, and fsync it;
3. read that file back from the disk: it must hold the slim form and read
   as the same post as the original;
4. put it in place: over a ``.json.xz``, or beside a ``.json`` under the
   name ``.json.xz``, never over another file;
5. only then remove the ``.json``.

A failure at any step leaves the original as it was (the temporary file is
removed), is counted and listed, and the run goes on. Stopped between 4 and
5, a post has both files: the parser reads the ``.json.xz`` and takes the
``.json`` as its side file, and the next run removes the ``.json`` once it
has read the ``.json.xz`` as the same post. A temporary file a killed
FeedVault left (``.<name>.<random>.feedvault-slim.tmp``, which no scan
reads) is removed by the next run that reads its folder.

Only regular files in the folders a scan walks under the media roots (no
dot-folder, so never the trash; a symlinked folder is not entered, a
symlinked file not read), whose content is instaloader's: gallery-dl's and
yt-dlp's JSONs, ``_comments.json`` and anything else are not opened for
writing at all.

The index follows as the files change, under db.write_lock a few files at a
time: ``meta_path`` of posts and copies, a profile's ``source``, the
Unmatched list and the dismissed duplicates that name a copy's file; then
each folder is indexed again.
"""
import errno
import json
import lzma
import os
import stat
import tempfile

import config
import db
import jobs
import scanner
import userdata
from parsers import instaloader as parser

KIND = "slim-metadata"
GROUP = "instaloader"                          # sync.GROUP: never while instaloader writes these files
MAX_BYTES = 64 * 2**20                         # bigger, compressed or not, is not a file instaloader wrote
FAILED_KEPT = 20                               # failures listed in a result
CHUNK = 50                                     # files rewritten per hold of db.write_lock
TMP = ".feedvault-slim.tmp"
INFO = ".info.json"                            # yt-dlp's: never instaloader's
# A slim file is a few kilobytes: a small dictionary compresses it as well as
# the default 8 MiB one, without that much memory set up for every file.
_FILTERS = [{"id": lzma.FILTER_LZMA2, "preset": 6, "dict_size": 1 << 20}]
# What one file can raise: a JSON too deep to parse, a cut xz stream.
FILE_ERRORS = (OSError, ValueError, RecursionError, lzma.LZMAError, EOFError)


# os.link's answer where the filesystem has no hard links.
_NO_LINKS = {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EMLINK}


class Failed(Exception):
    """A file could not be rewritten; it is as it was. ``placed``: except
    that its ``.json.xz`` is in place beside it (the ``.json`` could not be
    removed)."""

    def __init__(self, message, placed=None):
        super().__init__(message)
        self.placed = placed


def enabled(cfg=None):
    """Whether new instaloader metadata is slimmed after each sync and save
    whose source does not say otherwise: instaloader's metadata setting is
    slim (sync.settings)."""
    import sync                                # it imports this module
    return sync.settings(cfg)["metadata"] == "slim"


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------

def _open(path):
    """(stat, bytes) of a regular file, or None when it is something else
    or too big. Never follows a symlink (OSError, ELOOP)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return None
        return st, f.read()


def _parse(path, raw):
    """The JSON of a metadata file's bytes (``.xz``: one whole xz stream of
    at most MAX_BYTES once unpacked)."""
    if path.endswith(".xz"):
        d = lzma.LZMADecompressor(lzma.FORMAT_XZ)
        raw = d.decompress(raw, MAX_BYTES + 1)
        if not d.eof or d.unused_data or len(raw) > MAX_BYTES:
            raise ValueError("not one xz stream of a metadata file's size")
    return json.loads(raw)


def pack(data):
    """The bytes of a ``.json.xz`` holding ``data``."""
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return lzma.compress(text.encode("utf-8"), format=lzma.FORMAT_XZ, filters=_FILTERS)


def _same_post(a, b):
    """Whether two readings (parser.reads) are of the same post, or of the
    same account's profile, whatever else differs (a like count)."""
    if a is None or b is None or a[0] != b[0]:
        return False
    if a[0] == "Profile":
        return a[1][0] == b[1][0]
    return all(a[1][k] == b[1][k] for k in ("platform", "post_id", "author_id", "posted_at", "kind"))


class _Todo:
    """What rewrite() does with one file. ``twin``: the ``.json`` of a post
    whose ``.json.xz`` is there too, only to be removed."""
    __slots__ = ("path", "target", "st", "slimmed", "reads", "payload", "twin")

    def __init__(self, path, target, st, slimmed=None, reads=None, payload=b"", twin=False):
        self.path, self.target, self.st = path, target, st
        self.slimmed, self.reads, self.payload, self.twin = slimmed, reads, payload, twin

    @property
    def before(self):
        return self.st.st_size

    @property
    def after(self):
        return 0 if self.twin else len(self.payload)


def examine(path):
    """What there is to do with a ``.json`` or ``.json.xz``: (True, a
    _Todo) for an instaloader file to rewrite, (True, None) for one that is
    slim and compressed already, (False, None) for a file that is not
    instaloader's, or not one FeedVault reads a post or a profile from.
    Changes nothing. Raises Failed, or one of FILE_ERRORS for a file that
    cannot be read."""
    try:
        found = _open(path)
    except OSError as e:
        if e.errno == errno.ELOOP:             # a symlink: left alone
            return False, None
        raise
    if found is None:
        return False, None
    st, raw = found
    xz = path.endswith(".xz")
    if not xz and b'"instaloader"' not in raw:
        return False, None                     # gallery-dl's, yt-dlp's: not worth parsing
    try:
        data = _parse(path, raw)
    except (ValueError, RecursionError, lzma.LZMAError, EOFError):
        return False, None                     # not known to be instaloader's: the scan says what it is
    slimmed = parser.slim(data)
    if slimmed is None:
        return False, None
    if xz:
        if slimmed == data:
            return True, None
        target = path
    else:
        target = path + ".xz"
        if os.path.lexists(target):
            return True, _twin(path, target, st, parser.reads(data))
    return True, _Todo(path, target, st, slimmed, parser.reads(data), pack(slimmed))


def _twin(path, target, st, reads):
    """The _Todo of a ``.json`` whose ``.json.xz`` exists: removed, when the
    ``.json.xz`` reads as the same post (the one the parser reads)."""
    try:
        found = _open(target)
        other = parser.reads(_parse(target, found[1])) if found else None
    except FILE_ERRORS:
        other = None
    if not _same_post(reads, other):
        raise Failed(f"{os.path.basename(target)} is there too and does not read as the same post: both left as they are")
    return _Todo(path, target, st, twin=True)


def _write(todo):
    """Step 2: the slim file under a temporary name beside the original,
    with its mode, owner and times, on the disk. Returns its path."""
    folder, name = os.path.split(todo.path)
    st = todo.st
    fd, tmp = tempfile.mkstemp(prefix=f".{name[:100]}.", suffix=TMP, dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(todo.payload)
            f.flush()
            try:
                os.fchown(f.fileno(), st.st_uid, st.st_gid)
            except PermissionError:            # another user's file: it becomes FeedVault's
                pass
            try:
                os.fchmod(f.fileno(), stat.S_IMODE(st.st_mode))
            except PermissionError:            # exFAT, FAT: every file has the mount's mode
                pass
            os.utime(f.fileno(), ns=(st.st_atime_ns, st.st_mtime_ns))
            os.fsync(f.fileno())
    except BaseException:
        _unlink(tmp)
        raise
    return tmp


def _verify(tmp, todo):
    """Step 3: what the disk holds under the temporary name is the slim
    form, and reads as the post the original read as."""
    found = _open(tmp)
    data = _parse(todo.target, found[1]) if found else None
    if data != todo.slimmed or parser.reads(data) != todo.reads:
        raise Failed("what was written does not read back as the same post")


def _unchanged(todo):
    """The original is still the file that was read (instaloader, or the
    user, may be writing in the folder)."""
    now = os.lstat(todo.path)
    was = todo.st
    if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) != \
            (was.st_dev, was.st_ino, was.st_size, was.st_mtime_ns):
        raise Failed("it changed while it was being rewritten")


def _place(tmp, todo):
    """Step 4: the temporary file under its name. Over the ``.json.xz`` it
    replaces, in one step; beside a ``.json``, never over a file that is
    there (a hard link fails then; where the filesystem has none, exFAT,
    a rename after a look)."""
    if todo.target == todo.path:
        os.replace(tmp, todo.target)
    else:
        try:
            os.link(tmp, todo.target)
        except FileExistsError:
            raise Failed(f"{os.path.basename(todo.target)} is there now: left as it was") from None
        except OSError as e:
            if e.errno not in _NO_LINKS:
                raise
            if os.path.lexists(todo.target):
                raise Failed(f"{os.path.basename(todo.target)} is there now: left as it was") from None
            os.rename(tmp, todo.target)
        else:
            os.unlink(tmp)
    _sync_dir(os.path.dirname(todo.target))


def _remove(path):
    """Step 5: the ``.json`` its ``.json.xz`` replaced."""
    os.unlink(path)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _sync_dir(folder):
    """The folder's entry for a file just renamed, on the disk (where the
    filesystem can)."""
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def rewrite(todo):
    """Do what examine() found (steps 2 to 5 above). Raises Failed or one
    of FILE_ERRORS: the original is then as it was."""
    if todo.twin:
        _unchanged(todo)
        _remove(todo.path)
        return
    tmp = _write(todo)
    try:
        _verify(tmp, todo)
        _unchanged(todo)
        _place(tmp, todo)
    except BaseException:
        _unlink(tmp)
        raise
    if todo.target != todo.path:
        try:
            _remove(todo.path)
        except OSError as e:
            raise Failed(f"its .json.xz is written, but it could not be removed: {e.strerror or e}",
                         placed=todo.target) from e


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def _inside(folder, roots):
    """Whether ``folder``, symlinks followed, is a media root or inside
    one, and not in its trash."""
    real = os.path.realpath(folder)
    for root in roots:
        r = os.path.realpath(root)
        if real == r or real.startswith(r.rstrip(os.sep) + os.sep):
            return not (real + os.sep).startswith(os.path.join(r, scanner.TRASH_NAME) + os.sep)
    return False


def _listing(folder, since=None):
    """(the files right in ``folder`` that may be instaloader metadata,
    sorted; the temporary files left there). Regular files only, no
    symlink. ``since``: only those changed at that time or later."""
    names, stale = [], []
    with os.scandir(folder) as it:
        for e in it:
            if e.name.startswith("."):
                if e.name.endswith(TMP) and e.is_file(follow_symlinks=False):
                    stale.append(e.path)
                continue
            if parser._meta_base(e.name) is None or e.name.endswith(INFO) or not e.is_file(follow_symlinks=False):
                continue
            if since is not None:
                st = e.stat(follow_symlinks=False)
                if max(st.st_mtime, st.st_ctime) < since:
                    continue
            names.append(e.name)
    return sorted(names), stale


def _error(err):
    return getattr(err, "strerror", None) or str(err) or type(err).__name__


def _new_report(apply):
    return {"mode": "apply" if apply else "preview", "checked": 0, "files": 0, "clean": 0,
            "bytes_before": 0, "bytes_after": 0, "failures": 0, "failed": [], "stopped": False}


def _fail(out, path, err, note):
    out["failures"] += 1
    note(f"could not slim {path}: {_error(err)}")
    if len(out["failed"]) < FAILED_KEPT:
        out["failed"].append({"path": path, "error": _error(err)})


def _one(path, apply, out, note):
    """examine() and, with ``apply``, rewrite() one file, counted in
    ``out``, a failure logged too. Returns (old path, new path) when the index names a file that
    is gone or has another name now, else None."""
    try:
        ours, todo = examine(path)
        if not ours:
            return None
        out["checked"] += 1
        if todo is None:
            out["clean"] += 1
            return None
        if apply:
            rewrite(todo)
    except Failed as e:
        _fail(out, path, e, note)
        return (path, e.placed) if e.placed else None
    except FILE_ERRORS as e:
        _fail(out, path, e, note)
        return None
    out["files"] += 1
    out["bytes_before"] += todo.before
    out["bytes_after"] += todo.after
    return (path, todo.target) if apply and todo.target != path else None


def _renamed(conn, pairs):
    """Tell the index the metadata files that have another name now
    ((old, new) paths), under db.write_lock: posts and copies, profiles,
    the Unmatched list, and the dismissed duplicates naming a copy's file.
    Returns the user tables changed."""
    copies = {}
    with conn:
        for old, new in pairs:
            conn.execute("UPDATE OR IGNORE posts SET meta_path = ? WHERE meta_path = ?", (new, old))
            if conn.execute("UPDATE OR IGNORE copies SET meta_path = ? WHERE meta_path = ?", (new, old)).rowcount:
                copies[old] = new
            conn.execute("DELETE FROM copies WHERE meta_path = ?", (old,))     # its .json.xz was a copy already
            conn.execute("UPDATE profiles SET source = ? WHERE source = ?", (new, old))
            conn.execute("UPDATE OR REPLACE unmatched SET path = ? WHERE path = ?", (new, old))
        if not copies:
            return []
        changed = []
        for (key,) in conn.execute("SELECT key FROM dismissed_duplicates").fetchall():
            try:
                members = json.loads(key)
            except ValueError:
                continue
            if not isinstance(members, list) or not any(isinstance(m, str) and m in copies for m in members):
                continue
            now = json.dumps(sorted(copies.get(m, m) if isinstance(m, str) else m for m in members))
            conn.execute("UPDATE OR REPLACE dismissed_duplicates SET key = ? WHERE key = ?", (now, key))
            changed = ["dismissed_duplicates"]
    return changed


def _folder(folder, roots, apply, out, note, stopped, since=None, index=True):
    """One folder's files. With ``apply``: its temporary files left behind
    go, its files are rewritten CHUNK at a time under db.write_lock with
    the index told of each new name, and the folder is indexed again when
    anything in it changed (``index``)."""
    if not _inside(folder, roots):
        return
    try:
        names, stale = _listing(folder, since)
    except OSError as e:
        _fail(out, folder, e, note)
        return
    if not apply:
        for n in names:
            if stopped():
                break
            _one(os.path.join(folder, n), False, out, note)
        return
    for path in stale:
        _unlink(path)
    touched = False
    for i in range(0, len(names), CHUNK):
        if stopped():
            break
        with db.write_lock:
            before = out["files"]
            pairs = [p for p in (_one(os.path.join(folder, n), True, out, note) for n in names[i:i + CHUNK]) if p]
            if pairs or out["files"] != before:
                touched = True
                scanner.touched([folder])      # a scan under way reads it again
            changed = _renamed(db.connect(), pairs) if pairs else []
        for table in changed:
            userdata.changed(table)
        db.write_lock.let_in()                 # a delete or a restore waiting goes now
    if touched and index:
        scanner.index_dirs(roots, [folder])


def sweep(roots, apply, note=lambda text: None, stopped=lambda: False):
    """Every instaloader metadata file under the media roots. Returns the
    report: {"mode", "checked" (instaloader files read), "clean" (slim and
    compressed already), "files" (to rewrite; rewritten with ``apply``),
    "bytes_before" and "bytes_after" (of those files, exact), "failures",
    "failed": [{path, error}] (the first FAILED_KEPT; ``note`` is told of
    each), "stopped"}."""
    out = _new_report(apply)
    seen = set()
    for root in roots:
        real = os.path.realpath(root)
        if real in seen or not os.path.isdir(root):
            continue
        seen.add(real)
        for folder in scanner.folders(root):
            if stopped():
                out["stopped"] = True
                return out
            before = out["files"]
            _folder(folder, roots, apply, out, note, stopped)
            if out["files"] != before:
                note(f"{folder}: {_count(out['files'] - before)} {'slimmed' if apply else 'to slim'}")
    out["stopped"] = stopped()
    return out


def after(folder, roots, since, note, on=None):
    """After a download into ``folder`` (inside a media root) that started
    at ``since``: slim the instaloader metadata written since, in it and
    its subfolders, when ``on`` (None: the setting, enabled) says so. The
    job indexes the folder next. Returns the report, or None when off."""
    if not (enabled() if on is None else on):
        return None
    out = _new_report(True)
    for d in scanner.folders(folder):
        _folder(d, roots, True, out, note, lambda: False, since=since, index=False)
    _noted(out, note)
    return out


def files(paths, roots, note, on=None):
    """Slim these metadata files (a Save's, just moved into a media root),
    when ``on`` (None: the setting, enabled) says so. The caller indexes
    their folder next."""
    if not (enabled() if on is None else on):
        return None
    out = _new_report(True)
    with db.write_lock:
        pairs = [p for p in (_one(path, True, out, note) for path in paths
                             if _inside(os.path.dirname(path), roots) and parser._meta_base(os.path.basename(path)))
                 if p]
        changed = _renamed(db.connect(), pairs) if pairs else []
    for table in changed:
        userdata.changed(table)
    _noted(out, note)
    return out


def _noted(out, note):
    if out["files"]:
        note(f"{_count(out['files'])} of metadata slimmed, {fmt_bytes(out['bytes_before'] - out['bytes_after'])} freed")


def _count(n):
    return f"{n} file{'' if n == 1 else 's'}"


def fmt_bytes(n):
    """1536 -> "1.5 KB" (powers of 1024, as the dashboard writes sizes)."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# ---------------------------------------------------------------------------
# The job kind
# ---------------------------------------------------------------------------

def _build(params):
    if not config.load()["media_roots"]:
        raise jobs.BadRequest("add a media root in Settings first")
    return {"tool": "feedvault", "args": [KIND, params["mode"]]}


def _run(params, note, stopped):
    apply = params["mode"] == "apply"
    roots = config.load()["media_roots"]
    out = sweep(roots, apply, note, stopped)
    freed = out["bytes_before"] - out["bytes_after"]
    failed = f"; {_count(out['failures'])} failed and {'was' if out['failures'] == 1 else 'were'} left as " \
             f"{'it was' if out['failures'] == 1 else 'they were'}" if out["failures"] else ""
    if apply:
        message = f"{_count(out['files'])} slimmed, {fmt_bytes(freed)} freed{failed}"
        print(f"[slim] {message}")
    else:
        message = f"{_count(out['files'])} to slim: {fmt_bytes(out['bytes_before'])} now, " \
                  f"{fmt_bytes(out['bytes_after'])} after{failed}"
    if out["stopped"]:
        message = f"stopped: {message}"
    return "done", out, message


jobs.register(KIND, label="Slim instaloader metadata",
              params={"mode": {"type": "choice", "choices": ["preview", "apply"]}},
              build=_build, group=GROUP, run=_run,
              describe=lambda params, argv: "Slim instaloader metadata" if params.get("mode") == "apply"
              else "Count the instaloader metadata to slim")
