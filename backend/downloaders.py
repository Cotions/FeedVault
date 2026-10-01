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
"""
import os
import re
import shutil
import signal
import subprocess
import threading
import time

import config
import jobs

VERSION_TIMEOUT = 10                           # seconds for a tool's --version
VERSION_MAX = 200                              # characters kept of its first line
INSTALLS = ("venv", "pipx", "system", "missing")


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
# Status (GET /api/downloaders)
# ---------------------------------------------------------------------------

def status(refresh=False):
    cfg = config.load()
    tools, checked_at = found(refresh, cfg)
    return {"checked_at": checked_at, "tools": [tools[t] for t in jobs.TOOLS]}
