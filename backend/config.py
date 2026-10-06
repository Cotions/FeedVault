"""Paths and the user's config file.

The config lives in ~/.config/feedvault/config.json (or FEEDVAULT_CONFIG), never
next to the code, so the repo holds the app only and a frozen binary works too.
"""
import errno
import json
import os
import re
import stat
import sys
import tempfile
import threading

FROZEN     = getattr(sys, "frozen", False)
BUNDLE_DIR = getattr(sys, "_MEIPASS", "")
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_DIR   = os.path.dirname(BASE_DIR)



def _port(value):
    """FEEDVAULT_PORT: unset is 3380; anything but a number from 1 to 65535
    (empty too) stops FeedVault, as it stops run.sh and Vite (#100)."""
    if value is None:
        return 3380
    if not re.fullmatch(r"[0-9]{1,5}", value) or not 1 <= int(value) <= 65535:
        raise SystemExit(f"FEEDVAULT_PORT must be a port number from 1 to 65535, not {value!r}")
    return int(value)


PORT = _port(os.environ.get("FEEDVAULT_PORT"))
# Release builds rewrite this line with the tag being built.
__version__ = "0.0.0-dev"

DEFAULT_DATA = os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
    "feedvault",
)

_lock = threading.Lock()
# Held over a whole load, edit and save of config.json (POST /api/config).
editing = threading.Lock()


def config_path():
    override = os.environ.get("FEEDVAULT_CONFIG")
    if override:
        return os.path.abspath(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "feedvault", "config.json")


def static_dir():
    """Bundled UI when frozen, freshly built UI when running from source."""
    for candidate in (
        os.path.join(BUNDLE_DIR, "static") if FROZEN else "",
        os.path.join(REPO_DIR, "frontend", "dist"),
    ):
        if candidate and os.path.exists(os.path.join(candidate, "index.html")):
            return candidate
    return None


def load():
    cfg = {}
    path = config_path()
    if os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)
    cfg.setdefault("data_directory", DEFAULT_DATA)
    cfg.setdefault("media_roots", [])
    cfg.setdefault("similar_threshold", 6)        # dHash bits that may differ, see duplicates.py
    return cfg


def save(cfg):
    path = config_path()
    make_private_dir(os.path.dirname(path))
    with _lock:
        write_private(path, lambda f: json.dump(cfg, f, indent=2))


def make_private_dir(path):
    """Create the folder ``path`` and its missing parents, each 0700 whatever
    the umask (002 would leave them group-writable, and then refused: a
    scripts folder, a folder above it). A folder already there is left as
    it is, and so is one another process makes at the same time. Returns
    ``path``."""
    missing, p = [], os.path.abspath(path)
    while not os.path.isdir(p):
        missing.append(p)
        up = os.path.dirname(p)
        if up == p:
            break
        p = up
    for p in reversed(missing):
        try:
            os.mkdir(p, 0o700)
        except FileExistsError:
            if not os.path.isdir(p):
                raise
            continue
        # mkdir's mode is masked by the umask: one that drops the owner's bits too.
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                os.fchmod(fd, 0o700)
        except OSError:
            pass                               # a file system that keeps no modes (CIFS, FUSE): its own
        finally:
            os.close(fd)
    return path


def tighten_private_dir(path):
    """``chmod go-w`` the folder ``path`` when it is ours, a real folder (not
    a symlink) and writable by group or others. Nothing else, nothing above
    it. Returns the mode it had when it was changed, else None (a file
    system that refuses it too: read-only, CIFS; the folder's refusal then
    says the chmod to run)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or not st.st_mode & 0o022:
            return None
        os.fchmod(fd, stat.S_IMODE(st.st_mode) & ~0o022)
        return stat.S_IMODE(st.st_mode)
    except OSError:
        return None
    finally:
        os.close(fd)


def chmod_hint(path, how="go-w", uid=None):
    """The command that fixes ``path``'s mode, its path single-quoted for a
    shell; with sudo for a folder of root's (``uid`` 0) when we are not root."""
    sudo = "sudo " if uid == 0 and os.getuid() != 0 else ""
    return sudo + "chmod " + how + " '" + path.replace("'", "'\\''") + "'"


def write_private(path, dump, private=True):
    """Write ``path`` atomically, readable by its owner only (0600): ``dump(f)``
    writes into a temp file of a unique name in its folder (mkstemp:
    O_CREAT|O_EXCL, so never through a symlink left there), fsynced, renamed
    over ``path``, then the folder fsynced. A file that was more open is
    replaced by a 0600 one. ``private=False``, for a file in a media root
    (others may read it, its file system may keep no modes: CIFS, FUSE):
    the mode of the file it replaces (0644 for a new one), a refused
    fchmod let go."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)[:100]}.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            try:
                os.fchmod(f.fileno(), 0o600 if private else _mode_of(path, 0o644))
            except OSError:
                if private:
                    raise
            dump(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # The rename made durable. Some file systems (FUSE, CIFS, 9p) refuse to
    # fsync a folder: the file is written all the same.
    try:
        dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EACCES):
            raise


def _mode_of(path, default):
    """``path``'s permission bits (not a symlink's target's), else ``default``."""
    try:
        st = os.lstat(path)
    except OSError:
        return default
    return stat.S_IMODE(st.st_mode) & 0o777 if stat.S_ISREG(st.st_mode) else default


def clean_roots(roots):
    """Normalize a list of media roots. Returns (roots, error)."""
    if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
        return None, "media_roots must be a list of paths"
    out = []
    for r in roots:
        r = r.strip()
        if not r:
            continue
        r = os.path.abspath(os.path.expanduser(r))
        if not os.path.isdir(r):
            return None, f"not a folder: {r}"
        if r not in out:
            out.append(r)
    # A root inside another root would index the same files twice.
    for a in out:
        for b in out:
            if a != b and b.startswith(a.rstrip(os.sep) + os.sep):
                return None, f"{b} is inside {a}; keep only one"
    return out, None


def is_executable(path):
    return os.path.isabs(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def ancestors_refused(path):
    """Why a folder above ``path`` (each one up to /, along its path as
    written and as resolved) lets someone else swap what is below it: not
    root's nor ours, or writable by group or others and not sticky. Else
    None."""
    seen, out = set(), []
    for start in (os.path.dirname(os.path.abspath(path)), os.path.realpath(os.path.dirname(path))):
        p = start
        while p not in seen:
            seen.add(p)
            out.append(p)
            up = os.path.dirname(p)
            if up == p:
                break
            p = up
    parent = os.path.dirname(os.path.abspath(path))
    for p in out:
        try:
            st = os.stat(p)
        except OSError as e:
            return f"cannot read {p}: {e.strerror or e}"
        what = f"its parent folder ({p})" if p == parent else f"a folder above it ({p})"
        if not stat.S_ISDIR(st.st_mode):
            return f"{what} is not a folder"
        if st.st_uid not in (0, os.getuid()):
            return f"{what} belongs to another user"
        # A sticky folder (/tmp) lets nobody else rename or remove what is ours.
        if st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX:
            return f"{what} is writable by group or others ({chmod_hint(p, uid=st.st_uid)})"
    return None


def tool_refused(path):
    """Why a tool's path from Settings may not run, else None: it must be an
    executable file, and the file and its folder (where the path leads, and
    the folder it is written in) root's or ours, not writable by group or
    others, and the folders above those as ancestors_refused says: someone
    else could swap the program FeedVault runs."""
    if not is_executable(path):
        return f"not an executable file: {path}"
    real = os.path.realpath(path)
    checks = [(real, "the file"), (os.path.dirname(real), "its folder")]
    if os.path.dirname(path) != os.path.dirname(real):
        checks.append((os.path.dirname(path), "its folder"))
    for p, what in checks:
        try:
            st = os.stat(p)
        except OSError as e:
            return f"cannot read {p}: {e.strerror or e}"
        if st.st_uid not in (0, os.getuid()):
            return f"{what} ({p}) belongs to another user"
        if st.st_mode & 0o022:
            return f"{what} ({p}) is writable by group or others ({chmod_hint(p, uid=st.st_uid)})"
    return ancestors_refused(os.path.dirname(real)) or ancestors_refused(os.path.dirname(os.path.abspath(path)))


def clean_tools(tools, known):
    """Check {tool: path} from Settings: each a known tool, and a path to an
    executable file named after it (``yt-dlp``, ``yt-dlp_linux``) that
    nobody else can swap (tool_refused), or empty to use PATH again.
    Returns (tools, error)."""
    if not isinstance(tools, dict) or not all(isinstance(v, str) or v is None for v in tools.values()):
        return None, "tools must map a tool name to a path"
    out = {}
    for name, path in tools.items():
        if name not in known:
            return None, f"unknown tool: {name}"
        path = (path or "").strip()
        if not path:
            continue
        path = os.path.abspath(os.path.expanduser(path))
        if not is_executable(path):
            return None, f"{name}: not an executable file: {path}"
        if not os.path.basename(path).lower().startswith(name):
            return None, f"{name}: the file must be named {name} (or start with it): {path}"
        refused = tool_refused(path)
        if refused:
            return None, f"{name}: {refused}"
        out[name] = path
    return out, None


def db_path(cfg=None):
    cfg = cfg or load()
    return os.path.join(cfg["data_directory"], "feedvault.db")
