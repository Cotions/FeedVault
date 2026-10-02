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

Login status is what the settings say (sync.py), never read from a cookie
or a session file: for instaloader's saved login, only whether its session
file exists (os.path.exists on the path instaloader uses). The ``tool-test``
job runs a tool once against a fixed public item with the session flags a
sync would use, and says how it went in sync.py's terms (login required,
rate limited, not found, …).

The ``tool-update`` job updates a tool where it is installed, its command
picked here from how it is installed, never from the request:

    <the virtualenv's bin/python> -m pip install -U <name>      (venv)
    pipx upgrade <name>                                          (pipx)

Refused for system installs and missing tools: the card shows the command
to run instead. It shares its tool's lock group, so it never runs during a
sync of that tool; the tool is found again once it ends.
"""
import getpass
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

import config
import health
import jobs
import sync

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
    (``-version`` for ffmpeg), in a session of its own, killed with
    whatever it started after VERSION_TIMEOUT. A process that left the
    group and still holds the output is not waited for."""
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
        proc.stdout.close()
        proc.wait()
        return None, f"no answer to --version within {VERSION_TIMEOUT} s"
    finally:
        jobs._killpg(proc, signal.SIGKILL)     # anything it left behind in its group
    line = jobs.version_line(out.decode("utf-8", "replace").splitlines())[:VERSION_MAX]
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
    chunks, size = [], 0
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=PYPI_TIMEOUT) as r:
            while size <= PYPI_MAX:
                if time.monotonic() > deadline:
                    return None, f"PyPI did not answer within {PYPI_TIMEOUT} s"
                chunk = r.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
    except urllib.error.HTTPError as e:
        return None, f"PyPI answered {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"could not reach PyPI: {getattr(e, 'reason', None) or e}"
    if size > PYPI_MAX:
        return None, "PyPI's answer is too large"
    try:
        version = json.loads(b"".join(chunks))["info"]["version"]
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
# Login status, and the test job
# ---------------------------------------------------------------------------

def session_files(user):
    """Where instaloader keeps the session ``instaloader --login <user>``
    saved (instaloader.get_default_session_filename, then its legacy
    place); it lowercases the name it is given."""
    user = user.lower()
    config_dir = os.path.join(os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "instaloader")
    legacy = (tempfile.gettempdir() + "/.instaloader-" + getpass.getuser() + "/session-" + user).lower()
    return [os.path.join(config_dir, "session-" + user), legacy]


def login(tool, cfg=None):
    """The session a sync of ``tool`` uses, from the settings: {"mode":
    "none" | "cookies" (+ "browser") | "login" (+ "user", "session_file":
    whether instaloader's session file for it exists)}. None for ffmpeg.
    No file is opened: the session file is only looked for."""
    cfg = cfg or config.load()
    if tool == "instaloader":
        session = dict(sync.settings(cfg)["session"])
        if session["mode"] == "login":
            session["session_file"] = any(os.path.exists(p) for p in session_files(session["user"]))
        return session
    if tool in sync.KINDS:
        return dict(sync.tool_settings(tool, cfg)["session"])
    return None


# One public item per tool, cheap to fetch, unlikely to go away.
TEST_TARGETS = {
    "instaloader": "instagram",                                  # the profile's metadata only
    "gallery-dl": "https://x.com/jack/status/20",
    "yt-dlp": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
}
TEST_FAILURES = {"instaloader": sync.FAILURES, "gallery-dl": sync.GALLERY_DL_FAILURES,
                 "yt-dlp": sync.YT_DLP_FAILURES}
TEST_MESSAGES = {
    "login_required": "Login required: the site refused it without a session; set one in its sync settings",
    "rate_limited": "Rate limited: the site is limiting requests, try again later",
    "private": "The test item needs a login the session in use does not have",
    "not_found": "The test item was not found: the site may have changed, an update may fix it",
    "generic": "The test failed",
}


def _scratch(cfg):
    """Where a test runs, so whatever a tool writes stays out of the media."""
    return os.path.join(cfg["data_directory"], "downloaders", "test")


def _build_test(params):
    """The argument list: the tool's fixed test item and the session flags
    of its settings, nothing else from the request than which tool."""
    tool = params["tool"]
    cfg = config.load()
    folder = _scratch(cfg)
    os.makedirs(folder, exist_ok=True)
    if tool == "instaloader":
        args = ["--no-posts", "--no-profile-pic", "--no-metadata-json", "--dirname-pattern", sync._escape(folder),
                *sync.session_flags(sync.settings(cfg)["session"])]
    else:
        args = [*sync.config_flags(tool, cfg), "--simulate", *(["--no-playlist"] if tool == "yt-dlp" else []),
                *sync.cookie_flags(sync.tool_settings(tool, cfg)["session"])]
    return {"tool": tool, "cwd": folder, "args": [*args, "--", TEST_TARGETS[tool]]}


def _test_outcome(params, code, lines, index, note=None):
    if code == 0:
        return "done", {"ok": True, "error": None, "line": None}, "Works"
    error, line = sync.classify(lines, TEST_FAILURES[params["tool"]])
    line = health.scrub(line)
    message = TEST_MESSAGES[error]
    if error == "generic" and line:
        message = f"{message}: {line[:200]}"
    return "failed", {"ok": False, "error": error, "line": line}, message


TESTED = list(TEST_TARGETS)

def _test_pause(params):
    tool = params["tool"]
    return sync.settings()["pause"] if tool == "instaloader" else sync.tool_settings(tool)["pause"]


# Its tool's lock group and pause: never beside a sync of the same tool (one
# instaloader session at a time), nor right before or after one: the site
# sees it as one more run.
jobs.register("tool-test", label="Test a downloader", params={"tool": {"type": "choice", "choices": TESTED}},
              build=_build_test, group=lambda p: p["tool"], outcome=_test_outcome, pause=_test_pause,
              describe=lambda params, argv: f"Test {params.get('tool', 'a downloader')}")


# ---------------------------------------------------------------------------
# Updating
# ---------------------------------------------------------------------------

INSTALL_HINTS = {"ffmpeg": "sudo apt install ffmpeg"}


def install_hint(tool):
    """The command to install a missing tool, shown with a copy button, never run."""
    return INSTALL_HINTS.get(tool) or f"pipx install {PACKAGES[tool]}"


def update_plan(info):
    """How the tool of ``info`` (a detect() answer) can be updated:
    {"argv": [...] or None, "command": text to show, "reason": why not, or None}."""
    tool, kind = info["tool"], info["install"]
    name = PACKAGES.get(tool)
    if name is None:
        return {"argv": None, "command": None,
                "reason": "ffmpeg is updated with your system's package manager" if info["found"]
                else "ffmpeg is installed with your system's package manager"}
    if kind == "venv":
        python = os.path.join(info["venv"], "bin", "python")
        command = f"{shlex.quote(python)} -m pip install -U {name}"
        if not config.is_executable(python):
            return {"argv": None, "command": command, "reason": f"no Python at {python}"}
        # A virtualenv made without pip (uv's) cannot run it.
        if not glob.glob(os.path.join(glob.escape(info["venv"]), "lib", "python*", "site-packages", "pip")):
            return {"argv": None, "command": None,
                    "reason": f"its virtualenv has no pip ({info['venv']}): update it with the tool that made it"}
        return {"argv": [python, "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "-U", name],
                "command": command, "reason": None}
    if kind == "pipx":
        installed = os.path.basename(info["venv"])
        if installed != name:                  # pipx install --suffix: pipx names it otherwise
            return {"argv": None, "command": f"pipx upgrade {shlex.quote(installed)}",
                    "reason": f"pipx knows it as {installed}, not {name}"}
        if shutil.which("pipx") is None:
            return {"argv": None, "command": f"pipx upgrade {name}", "reason": "pipx is not on the PATH"}
        return {"argv": ["pipx", "upgrade", name], "command": f"pipx upgrade {name}", "reason": None}
    if kind == "missing":
        return {"argv": None, "command": install_hint(tool), "reason": f"{tool} is not installed"}
    return {"argv": None, "command": f"pipx install {name}",
            "reason": f"{tool} is not in a virtualenv FeedVault can update: update it the way it was installed "
                      "(your package manager, pip), or install a copy with pipx and set its path here"}


def _build_update(params):
    """The argument list from where the tool is installed now (found again,
    not the cache): never from the request, which only names the tool."""
    tool = params["tool"]
    info = detect_install(tool)
    plan = update_plan(info)
    if plan["argv"] is None:
        raise jobs.BadRequest(f"{tool} cannot be updated from FeedVault: {plan['reason']}; run {plan['command']}")
    return {"tool": plan["argv"][0], "args": plan["argv"][1:]}


def detect_install(tool):
    """detect() without running the tool: where it is and how it is installed."""
    path = jobs.tool_path(tool)
    kind, venv = install_of(path)
    return {"tool": tool, "found": path is not None, "path": path, "install": kind, "venv": venv}


def _updated(job):
    """Once an update has run, its tool is found again (in the background:
    the queue does not wait for it), so the card shows its new version."""
    tool = job["params"].get("tool")
    if tool in jobs.TOOLS and job["started_at"] is not None and job["state"] != "interrupted":
        threading.Thread(target=refresh_tool, args=(tool,), daemon=True, name=f"refresh-{tool}").start()


def refresh_tool(tool):
    """Find one tool again, the others kept as they are. Nothing to do
    while nothing is cached: the next status() finds every tool."""
    cfg = config.load()
    with _finding:                             # a detection running now would overwrite it
        with _lock:
            if _cache["tools"] is None or _cache["key"] != _key(cfg):
                return
        info = detect(tool, cfg)
        with _lock:
            if _cache["tools"] is not None and _cache["key"] == _key(cfg):
                _cache["tools"] = {**_cache["tools"], tool: info}


jobs.register("tool-update", label="Update a downloader",
              params={"tool": {"type": "choice", "choices": list(PACKAGES)}},
              build=_build_update, group=lambda p: p["tool"], ended=_updated,
              describe=lambda params, argv: f"Update {params.get('tool', 'a downloader')}")


# ---------------------------------------------------------------------------
# Status (GET /api/downloaders)
# ---------------------------------------------------------------------------

def status(refresh=False):
    cfg = config.load()
    pypi = {}

    def ask():
        pypi.update(latest(cfg))
    asking = threading.Thread(target=ask, daemon=True)     # while the tools are found
    asking.start()
    tools, checked_at = found(refresh, cfg)
    asking.join()
    out = []
    for t in jobs.TOOLS:
        info = dict(tools[t])
        known = pypi.get(PACKAGES.get(t))
        info["latest"] = known
        info["outdated"] = newer(known["version"], info["version"]) if known and known["version"] else None
        info["login"] = login(t, cfg)
        plan = update_plan(info)
        info["update"] = {"possible": plan["argv"] is not None, "command": plan["command"], "reason": plan["reason"]}
        info["install_hint"] = install_hint(t) if not info["found"] else None
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
