import os
import sys

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
