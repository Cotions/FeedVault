"""Paths and the user's config file.

The config lives in ~/.config/feedvault/config.json (or FEEDVAULT_CONFIG), never
next to the code, so the repo holds the app only and a frozen binary works too.
"""
import json
import os
import sys
import tempfile
import threading

FROZEN     = getattr(sys, "frozen", False)
BUNDLE_DIR = getattr(sys, "_MEIPASS", "")
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_DIR   = os.path.dirname(BASE_DIR)

PORT = int(os.environ.get("FEEDVAULT_PORT", "3380"))
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _lock:
        write_private(path, lambda f: json.dump(cfg, f, indent=2))


def write_private(path, dump):
    """Write ``path`` atomically, readable by its owner only (0600): ``dump(f)``
    writes into a temp file of a unique name in its folder (mkstemp:
    O_CREAT|O_EXCL, so never through a symlink left there), fsynced, renamed
    over ``path``, then the folder fsynced. A file that was more open is
    replaced by a 0600 one."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)[:100]}.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
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
    dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


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


def tool_refused(path):
    """Why a tool's path from Settings may not run, else None: it must be an
    executable file, and the file and its folder (where the path leads, and
    the folder it is written in) root's or ours, not writable by group or
    others: someone else could swap the program FeedVault runs."""
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
            return f"{what} ({p}) is writable by group or others (chmod go-w)"
    return None


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
