import os
import shutil
import sys
import tempfile

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# No test reaches a real program (#56)
# ---------------------------------------------------------------------------
# Where the backend finds or starts a program:
#   jobs.tool_path              the path set in Settings, else shutil.which (the four TOOLS, pipx)
#   jobs._run                   Popen([exe, *args]): every job (syncs, tool-version, test, update)
#   downloaders.run_version     Popen([path, --version]) of what tool_path found
#   downloaders.update_plan     shutil.which("pipx"); the update job runs pip or pipx through jobs
#   archives._read_formats      Popen([gallery-dl's own Python, -c]) from its shebang
#   notify.desktop              shutil.which("notify-send"), subprocess.run of it
#   app.browse                  shutil.which("zenity"), subprocess.run(["zenity", ...]) by name
#   app.main                    webbrowser.open (not reached from a test)
#   thumbs / hashing            shutil.which and subprocess.run of ffmpeg / ffprobe by name
#   sync.py, hashing.py         ctypes: statfs and ioprio_set syscalls, no program
# No os.exec*, os.spawn*, os.system or posix_spawn; gallery_dl is only
# imported by gallery-dl's own Python (archives), never in this process.
# Where it reads a path under HOME or the XDG folders:
#   config.py                   its config and data folders (XDG_CONFIG_HOME, XDG_DATA_HOME, else ~)
#   downloaders.pipx_venvs      PIPX_HOME, else XDG_DATA_HOME/pipx, ~/.local/pipx
#   downloaders.session_files   XDG_CONFIG_HOME/instaloader, the temp dir's .instaloader-<user>
#   the tools it runs           instaloader's session, gallery-dl's and yt-dlp's configs, the
#                               browser cookie databases --cookies-from-browser reads: all under HOME
#
# So for every test (tool_guard, autouse): PATH is an empty folder of its
# own (the fixtures that install a fake put theirs in front; no fake looks
# anything up on PATH: their shebangs are absolute), HOME, the XDG folders
# and the temp dir are in its tmp dir, and anything that finds or starts a
# program outside its tmp dir and the fakes here raises ToolGuardError and
# fails the test: jobs.tool_path (a path set in Settings too), shutil.which,
# and, through an audit hook, every Popen, exec, spawn and os.system (a
# script's shebang is checked as well). Importing a downloader's own Python
# package raises the same. A test that needs another program says so with
# tool_guard.allow(path, why).
TESTS = os.path.dirname(os.path.abspath(__file__))
# Run by the tests besides what is in their tmp dir, by real path:
ALLOWED = {
    os.path.realpath(sys.executable): "this Python: the shebang of every fake (#!sys.executable)",
    os.path.realpath("/bin/sh"): "the shebang of test_sync_tools' Python wrapper and test_jobs' empty tool",
}
EXEC_EVENTS = {"subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn", "os.system"}
TOOL_MODULES = {"instaloader", "gallery_dl", "yt_dlp"}
_which = shutil.which


class ToolGuardError(RuntimeError):
    """A test found or started a program outside its tmp dir and the fakes."""


class Guard:
    def __init__(self, roots):
        self.roots = [os.path.realpath(r) for r in roots]
        self.allowed = dict(ALLOWED)
        self.runs = []                         # every program started, allowed or not
        self.violations = []

    def allow(self, path, why):
        """Let this test run ``path`` too (``why`` is for the reader)."""
        self.allowed[os.path.realpath(path)] = why

    def allows(self, path):
        """In the test's tmp dir or the fakes', or allowed, by real path."""
        real = os.path.realpath(path)
        return real in self.allowed or any(real == r or real.startswith(r + os.sep) for r in self.roots)

    def refuse(self, what):
        self.violations.append(what)
        raise ToolGuardError(f"test guard (#56): {what}")

    def taken(self):
        """The refusals so far, which then no longer fail the test."""
        out, self.violations = self.violations, []
        return out

    def check_run(self, exe, env, depth=0):
        name = os.fsdecode(exe)
        path = name if os.sep in name else _which(name, path=os.pathsep.join(os.get_exec_path(env)))
        if path is None or not os.path.exists(path):
            return                             # nothing there: it fails as missing, nothing runs
        real = os.path.realpath(path)
        if depth == 0:
            self.runs.append(real)
        if not self.allows(real):
            self.refuse(f"run {path}" + (f" ({real})" if real != path else ""))
        try:
            with open(real, "rb") as f:
                first = f.readline(256)
        except OSError:
            return
        words = first[2:].decode("utf-8", "replace").split() if first.startswith(b"#!") else []
        if words and depth < 3:                # the kernel runs the interpreter: it must pass too
            self.check_run(words[0], env, depth + 1)
            rest = [w for w in words[1:] if not w.startswith("-")]
            if os.path.basename(words[0]) == "env" and rest:
                self.check_run(rest[0], env, depth + 1)


_guard = Guard([TESTS])                        # between tests: nothing but the fakes


def _audit(event, args):
    if event not in EXEC_EVENTS:
        return
    if event == "os.system":
        _guard.refuse(f"os.system({args[0]!r})")
    elif event == "os.spawn":                  # (mode, path, args, env)
        _guard.check_run(args[1], args[3])
    elif event == "subprocess.Popen":          # (executable, args, cwd, env)
        _guard.check_run(args[0], args[3])
    else:                                      # (path, args, env)
        _guard.check_run(args[0], args[2])


class _NoToolModules:
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in TOOL_MODULES:
            _guard.refuse(f"import {name}")
        return None


sys.addaudithook(_audit)                       # cannot be removed: _guard says what it allows
sys.meta_path.insert(0, _NoToolModules())


@pytest.fixture(autouse=True)
def tool_guard(tmp_path, tmp_path_factory, monkeypatch):
    global _guard
    own = tmp_path_factory.mktemp("guard")    # beside tmp_path: tests list what is in theirs
    home = own / "home"
    for sub in (".config", ".local/share", ".local/state", ".cache"):
        (home / sub).mkdir(parents=True)
    (own / "tmp").mkdir()
    (own / "bin").mkdir()
    monkeypatch.setenv("PATH", str(own / "bin"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("TMPDIR", str(own / "tmp"))
    monkeypatch.setattr(tempfile, "tempdir", str(own / "tmp"))
    # Nothing that points a tool at the user's installs, or a popup at the desktop.
    for var in ("PIPX_HOME", "PIPX_BIN_DIR", "VIRTUAL_ENV", "PYTHONPATH", "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    guard = Guard([tmp_path, own, TESTS])

    def which(cmd, mode=os.F_OK | os.X_OK, path=None):
        found = _which(cmd, mode, path)
        if found is not None and not guard.allows(found):
            guard.refuse(f"shutil.which({cmd!r}) found {found}")
        return found

    import jobs
    tool_path = jobs.tool_path

    def guarded_tool_path(name):
        found = tool_path(name)
        if found is not None and not guard.allows(found):
            guard.refuse(f"jobs.tool_path({name!r}) is {found}")
        return found

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(jobs, "tool_path", guarded_tool_path)
    _guard = guard
    yield guard
    _guard = Guard([TESTS])
    if guard.violations:
        pytest.fail("reached a program outside the fakes:\n" + "\n".join(guard.violations), pytrace=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated config, database and media root."""
    monkeypatch.setenv("FEEDVAULT_CONFIG", str(tmp_path / "config.json"))
    import config
    import db

    media = tmp_path / "media"
    media.mkdir()
    cfg = config.load()
    cfg["data_directory"] = str(tmp_path / "data")
    cfg["media_roots"] = [str(media)]
    config.save(cfg)
    db.init(config.db_path(cfg))
    # No background hashing: tests run passes themselves, on their own database.
    import hashing
    monkeypatch.setattr(hashing, "kick", lambda: None)
    yield {"tmp": tmp_path, "media": media, "roots": [str(media)]}
    # A userdata write still pending would fire after FEEDVAULT_CONFIG is
    # restored, into the real data directory.
    import userdata
    with userdata._lock:
        for timer in userdata._timers.values():
            timer.cancel()
        userdata._timers.clear()


@pytest.fixture
def client(env):
    import app as app_module
    app_module.app.testing = True
    c = app_module.app.test_client()
    c.environ_base["HTTP_HOST"] = "localhost:3380"
    return c


H = {"X-FeedVault": "1"}
