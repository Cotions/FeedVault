import os
import shutil
import sys
import tempfile

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toolguard  # noqa: E402

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
# and the temp dir are beside its tmp dir, and toolguard.Guard fails the test
# on anything that finds or starts a program outside those and the fakes
# here: jobs.tool_path (a path set in Settings too), shutil.which, and,
# through an audit hook, every Popen, exec, spawn and os.system, a script's
# shebang included; on an import of a downloader's or pip's package from
# anywhere else; on a connection to anything but this machine. Every Python
# a test starts gets the same guard (guard_site/sitecustomize.py on
# PYTHONPATH), so a fake or a script cannot go around it either. A test that
# needs another program says so with tool_guard.allow(path, why).
TESTS = os.path.dirname(os.path.abspath(__file__))
# Started by the tests besides what is in their tmp dir, by real path. Each
# Python a test starts runs under the guard.
ALLOWED = {sys.executable: "this Python: every fake's shebang (#!sys.executable), and test_jobs' job kinds"}
# Only as the shebang of a script a test wrote: they run whatever they are given.
INTERPRETERS = {"/bin/sh": "test_sync_tools' Python wrapper (exec this Python with a PYTHONPATH)"}

toolguard.install(toolguard.Guard([TESTS], ALLOWED, INTERPRETERS))    # between tests: nothing but the fakes


@pytest.fixture(autouse=True)
def tool_guard(tmp_path, tmp_path_factory, monkeypatch):
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
    import config
    monkeypatch.setattr(config, "DEFAULT_DATA", str(home / ".local/share/feedvault"))   # read at import
    # Nothing that points a tool at the user's installs, or a popup at the desktop.
    for var in ("PIPX_HOME", "PIPX_BIN_DIR", "VIRTUAL_ENV", "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PYTHONPATH", os.path.join(TESTS, "guard_site"))
    guard = toolguard.Guard([tmp_path, own, TESTS], ALLOWED, INTERPRETERS, log=str(own / "refused"))
    guard.dir = own
    monkeypatch.setenv(toolguard.ENV, guard.settings())

    def which(cmd, mode=os.F_OK | os.X_OK, path=None):
        found = toolguard._which(cmd, mode, path)
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
    between = toolguard.current()
    toolguard.use(guard)
    yield guard
    toolguard.use(between)
    if guard.violations:
        pytest.fail("reached a program or the network outside the fakes:\n" + "\n".join(guard.violations),
                    pytrace=False)


def pytest_sessionfinish(session, exitstatus):
    # Refused outside a test (a thread a test left running, say): the run fails too.
    stray = toolguard.current().stray
    if stray:
        print("\ntest guard (#56), outside any test:\n" + "\n".join(stray))
        session.exitstatus = 1


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
