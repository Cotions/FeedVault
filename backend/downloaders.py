"""Downloaders: is each tool there, which version, how it was installed.

For instaloader, gallery-dl, yt-dlp and ffmpeg: the executable FeedVault
would run (jobs.tool_path: the path set in Settings, else PATH), its version
(its own ``--version``, argv only, a short timeout) and how it is installed,
read from where the file really is (symlinks followed, as pipx links its
tools into ~/.local/bin):

- ``venv``: in the ``bin/`` folder of a virtualenv (``pyvenv.cfg`` beside it)
- ``pipx``: the same, the virtualenv under pipx's ``venvs`` folder
- ``system``: anything else (/usr/bin, ``pip install --user``, a binary)
- ``missing``: not found

Found once and kept in memory; found again when the tools set in Settings
change, or on request (Check again).

The latest version of instaloader, gallery-dl and yt-dlp comes from PyPI,
only when Settings turn the check on (``check_updates``, off by default): the
only time the server itself reaches the network. One fixed URL per package,
no redirects, a timeout and a size cap; at most once a day per package, the
answers kept in ``<data_dir>/downloaders/pypi.json`` across restarts.
ffmpeg is not on PyPI: no check.
"""
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request

import config
import jobs

VERSION_TIMEOUT = 10                           # seconds for a tool's --version
VERSION_MAX = 200                              # characters kept of its first line
INSTALLS = ("venv", "pipx", "system", "missing")

PACKAGES = {"instaloader": "instaloader", "gallery-dl": "gallery-dl", "yt-dlp": "yt-dlp"}   # tool -> PyPI name
PYPI_URL = "https://pypi.org/pypi/{}/json"
PYPI_TIMEOUT = 10                              # seconds for the whole answer
PYPI_MAX = 8 * 1024 * 1024                     # bytes; yt-dlp's page lists every release
PYPI_EVERY = 86400                             # seconds between two checks of a package
_PYPI_VERSION_RE = re.compile(r"[0-9][0-9A-Za-z.+!_-]{0,63}")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def pipx_venvs():
    """The folders pipx keeps its virtualenvs in: $PIPX_HOME/venvs, else
    its default, which moved from ~/.local/pipx to the XDG data folder."""
    home = os.path.expanduser("~")
    if os.environ.get("PIPX_HOME"):
        return [os.path.join(os.environ["PIPX_HOME"], "venvs")]
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return [os.path.join(data, "pipx", "venvs"), os.path.join(home, ".local", "pipx", "venvs")]


def install_of(path):
    """(install kind, virtualenv folder or None) of an executable path."""
    if path is None:
        return "missing", None
    real = os.path.realpath(path)
    bin_dir = os.path.dirname(real)
    venv = os.path.dirname(bin_dir)
    if os.path.basename(bin_dir) != "bin" or not os.path.isfile(os.path.join(venv, "pyvenv.cfg")):
        return "system", None
    if any(os.path.dirname(venv) == os.path.realpath(d) for d in pipx_venvs()):
        return "pipx", venv
    return "venv", venv


def run_version(path, tool):
    """(version, error): the first line ``<path> --version`` prints
    (``-version`` for ffmpeg, its copyright notice cut), in a session of its
    own, killed with whatever it started after VERSION_TIMEOUT."""
    try:
        proc = subprocess.Popen([path, "-version" if tool == "ffmpeg" else "--version"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
    except OSError as e:
        return None, f"could not start: {e.strerror or e}"
    try:
        out, _ = proc.communicate(timeout=VERSION_TIMEOUT)
    except subprocess.TimeoutExpired:
        jobs._killpg(proc, signal.SIGKILL)
        proc.communicate()
        return None, f"no answer to --version within {VERSION_TIMEOUT} s"
    finally:
        jobs._killpg(proc, signal.SIGKILL)     # anything it left behind in its group
    line = next((t.strip() for t in out.decode("utf-8", "replace").splitlines() if t.strip()), "")
    line = line.split(" Copyright")[0][:VERSION_MAX]
    if proc.returncode != 0:
        return None, f"--version failed (exit code {proc.returncode}){': ' + line if line else ''}"
    return (line, None) if line else (None, "no version printed")


def detect(tool, cfg=None):
    """What there is of one tool, found now."""
    configured = ((cfg or config.load()).get("tools") or {}).get(tool)
    path = jobs.tool_path(tool)
    kind, venv = install_of(path)
    version, error = run_version(path, tool) if path else (None, None)
    real = os.path.realpath(path) if path else None
    return {"tool": tool, "found": path is not None, "path": path, "real_path": real if real != path else None,
            "configured": configured or None, "install": kind, "venv": venv,
            "version": version, "version_error": error}


_lock = threading.Lock()                       # the cache
_finding = threading.Lock()                    # one detection at a time
_cache = {"key": None, "tools": None, "checked_at": None}


def _key(cfg):
    """What detection depends on: the tools set in Settings and PATH."""
    return repr((sorted((cfg.get("tools") or {}).items()), os.environ.get("PATH")))


def found(refresh=False, cfg=None):
    """{tool: detect()} and when it was found: from the cache, unless it is
    empty, the tools' settings changed since, or ``refresh``. Each tool's
    --version runs in a thread of its own."""
    cfg = cfg or config.load()
    key = _key(cfg)
    with _finding:
        with _lock:
            if not refresh and _cache["key"] == key:
                return dict(_cache["tools"]), _cache["checked_at"]
        out = {}

        def one(t):
            out[t] = detect(t, cfg)
        threads = [threading.Thread(target=one, args=(t,), daemon=True) for t in jobs.TOOLS]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        now = int(time.time())
        with _lock:
            _cache.update(key=key, tools={t: out[t] for t in jobs.TOOLS}, checked_at=now)
            return dict(_cache["tools"]), now


def forget():
    """Drop the cache: the next status() finds every tool again."""
    with _lock:
        _cache.update(key=None, tools=None, checked_at=None)


# ---------------------------------------------------------------------------
# Latest versions (PyPI)
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The URL is fixed: a redirect is an error, never followed."""

    def redirect_request(self, *args, **kwargs):
        return None


def fetch_latest(name):
    """(version, error): PyPI's latest release of a package, ``name`` one of
    PACKAGES' values. The answer must come within PYPI_TIMEOUT, be at most
    PYPI_MAX bytes of JSON, and its ``info.version`` look like a version."""
    request = urllib.request.Request(PYPI_URL.format(name), headers={
        "Accept": "application/json", "User-Agent": f"FeedVault/{config.__version__}"})
    deadline = time.monotonic() + PYPI_TIMEOUT
    body = b""
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=PYPI_TIMEOUT) as r:
            while len(body) <= PYPI_MAX:
                if time.monotonic() > deadline:
                    return None, f"PyPI did not answer within {PYPI_TIMEOUT} s"
                chunk = r.read(65536)
                if not chunk:
                    break
                body += chunk
    except urllib.error.HTTPError as e:
        return None, f"PyPI answered {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"could not reach PyPI: {getattr(e, 'reason', None) or e}"
    if len(body) > PYPI_MAX:
        return None, "PyPI's answer is too large"
    try:
        version = json.loads(body)["info"]["version"]
    except (ValueError, KeyError, TypeError, RecursionError):
        version = None
    if not isinstance(version, str) or not _PYPI_VERSION_RE.fullmatch(version):
        return None, "PyPI's answer is not what was expected"
    return version, None


_pypi_lock = threading.Lock()


def _pypi_path(cfg):
    return os.path.join(cfg["data_directory"], "downloaders", "pypi.json")


def _read_pypi(cfg):
    """{name: {version, error, checked_at}} as last saved; what does not fit is dropped."""
    try:
        with open(_pypi_path(cfg), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for name in PACKAGES.values():
        e = saved.get(name) if isinstance(saved, dict) else None
        if (isinstance(e, dict) and isinstance(e.get("checked_at"), int)
                and (e.get("version") is None or isinstance(e["version"], str))
                and (e.get("error") is None or isinstance(e["error"], str))):
            out[name] = {"version": e.get("version"), "error": e.get("error"), "checked_at": e["checked_at"]}
    return out


def latest(cfg=None):
    """{PyPI name: {version, error, checked_at}}, asking PyPI for each
    package not asked in the last PYPI_EVERY seconds (a failed ask counts:
    no retry before then either). Empty, and nothing asked, while
    ``check_updates`` is off."""
    cfg = cfg or config.load()
    if cfg.get("check_updates") is not True:
        return {}
    with _pypi_lock:
        saved = _read_pypi(cfg)
        now = int(time.time())
        due = [n for n in PACKAGES.values() if n not in saved or not 0 <= now - saved[n]["checked_at"] < PYPI_EVERY]
        if not due:
            return saved
        got = {}

        def one(name):
            version, error = fetch_latest(name)
            got[name] = {"version": version, "error": error, "checked_at": now}
        threads = [threading.Thread(target=one, args=(n,), daemon=True) for n in due]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for name, e in got.items():
            print(f"[downloaders] PyPI {name}: {e['version'] or e['error']}")
        saved.update(got)
        path = _pypi_path(cfg)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(saved, f, indent=1)
            os.replace(path + ".tmp", path)
        except OSError as e:                   # still shown; asked again on the next start
            print(f"[downloaders] could not save {path}: {e}")
        return saved


def _release(text):
    """A version's release numbers, trailing zeros dropped (``1.30.0`` is
    ``1.30``): the first run of dot-separated numbers in ``text``."""
    m = re.search(r"\d+(?:\.\d+)*", text or "")
    if m is None:
        return None
    parts = [int(p) for p in m.group().split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def newer(latest_version, installed):
    """Whether PyPI's version is newer than the installed one; None when
    either cannot be read. A nightly yt-dlp (``2026.08.06.232211``) is not
    older than the release it follows."""
    a, b = _release(latest_version), _release(installed)
    return None if a is None or b is None else a > b


# ---------------------------------------------------------------------------
# Status (GET /api/downloaders)
# ---------------------------------------------------------------------------

def status(refresh=False):
    cfg = config.load()
    tools, checked_at = found(refresh, cfg)
    pypi = latest(cfg)
    out = []
    for t in jobs.TOOLS:
        info = dict(tools[t])
        known = pypi.get(PACKAGES.get(t))
        info["latest"] = known
        info["outdated"] = newer(known["version"], info["version"]) if known and known["version"] else None
        out.append(info)
    return {"checked_at": checked_at, "check_updates": cfg.get("check_updates") is True, "tools": out}


def outdated(tool):
    """(installed, latest) when the latest-version check is on and ``tool``
    is older than PyPI's latest release, from what is already known (no
    PyPI request, no --version run unless the tools were never found)."""
    cfg = config.load()
    if cfg.get("check_updates") is not True or tool not in PACKAGES:
        return None
    known = _read_pypi(cfg).get(PACKAGES[tool])
    info = found(cfg=cfg)[0][tool]
    if known and known["version"] and newer(known["version"], info["version"]):
        return info["version"], known["version"]
    return None
