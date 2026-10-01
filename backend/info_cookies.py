"""Cookies out of yt-dlp's info JSONs.

yt-dlp writes the cookies it sent for a video into that video's info JSON
(``formats[].cookies``, and a ``Cookie`` line in ``http_headers``; its
sanitize_info keeps both). A sync with a browser's cookies would leave a
logged-in session in plaintext beside every video, in a folder people back
up and share. So:

- after a yt-dlp sync that used cookies, the info JSONs it wrote (in the
  source's folder, changed since the job started) are rewritten without
  them (sync.py), before the folder is indexed;
- Settings can do the same for every yt-dlp info JSON under the media roots
  (folders synced before this), counting first.

A rewrite drops every ``cookies`` key, at any depth, and every ``Cookie``
header inside an ``http_headers``; the rest stays as it was once parsed. It
is atomic (a temporary file in the same folder, renamed over the JSON),
keeps the file's mode and mtime, and only touches regular files that parse
as yt-dlp's (parsers.yt_dlp._is_ours).
"""
import errno
import json
import os
import stat
import tempfile
import threading

from parsers import yt_dlp

INFO = yt_dlp.INFO
MAX_BYTES = 64 * 2**20                         # bigger is not an info JSON FeedVault wrote
FAILED_KEPT = 20                               # failures listed in an answer

_sweeping = threading.Lock()


class Busy(Exception):
    """A sweep is already running."""


def strip(d):
    """(``d`` without cookies, whether anything was taken out)."""
    if isinstance(d, list):
        out, changed = [], False
        for v in d:
            v, c = strip(v)
            out.append(v)
            changed |= c
        return out, changed
    if not isinstance(d, dict):
        return d, False
    out, changed = {}, False
    for k, v in d.items():
        if k == "cookies":
            changed = True
            continue
        if k == "http_headers" and isinstance(v, dict):
            kept = {h: x for h, x in v.items() if not (isinstance(h, str) and h.lower() == "cookie")}
            changed |= len(kept) != len(v)
            v = kept
        v, c = strip(v)
        out[k] = v
        changed |= c
    return out, changed


def _read(path):
    """(stat, parsed JSON) of a regular file that is a yt-dlp info JSON,
    else None. Never follows a symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return None
        try:
            d = json.loads(f.read())
        except (ValueError, UnicodeDecodeError):
            return None
    return (st, d) if yt_dlp._is_ours(d) else None


def clean(path, apply=True):
    """Take the cookies out of one info JSON. True when it held some
    (``apply``: and they are gone now). Raises OSError."""
    try:
        found = _read(path)
    except OSError as e:
        if e.errno == errno.ELOOP:              # a symlink: left alone
            return False
        raise
    if found is None:
        return False
    st, d = found
    cleaned, changed = strip(d)
    if not changed or not apply:
        return changed
    folder, name = os.path.split(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{name[:100]}.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def after_sync(folder, since):
    """Clean the info JSONs right in ``folder`` (where the sync's output
    template puts them) changed since ``since``. Returns (cleaned,
    [(path, error)])."""
    cleaned, failed = 0, []
    try:
        entries = list(os.scandir(folder))
    except OSError as e:
        return 0, [(folder, e.strerror or str(e))]
    for e in entries:
        if not e.name.endswith(INFO):
            continue
        try:
            if not e.is_file(follow_symlinks=False) or e.stat(follow_symlinks=False).st_mtime < since:
                continue
            cleaned += clean(e.path)
        except OSError as err:
            failed.append((e.path, err.strerror or str(err)))
    return cleaned, failed


def sweep(roots, apply):
    """Every yt-dlp info JSON under the media roots (symlinked folders not
    followed): {"checked", "files" (holding cookies; cleaned when
    ``apply``), "failed": [{path, error}] (the first FAILED_KEPT),
    "failures"}."""
    if not _sweeping.acquire(blocking=False):
        raise Busy("already checking the info JSONs")
    try:
        return _sweep(roots, apply)
    finally:
        _sweeping.release()


def _sweep(roots, apply):
    checked = files = failures = 0
    failed = []
    seen = set()
    for root in roots:
        real = os.path.realpath(root)
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        for dirpath, dirnames, names in os.walk(root):
            dirnames.sort()
            for n in sorted(names):
                if not n.endswith(INFO):
                    continue
                path = os.path.join(dirpath, n)
                checked += 1
                try:
                    files += clean(path, apply)
                except OSError as e:
                    failures += 1
                    if len(failed) < FAILED_KEPT:
                        failed.append({"path": path, "error": e.strerror or str(e)})
    return {"checked": checked, "files": files, "failed": failed, "failures": failures}
