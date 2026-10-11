"""The frozen app (bundle.sh, packaging/feedvault.spec): what the backend
finds in the bundle rather than the repo, and the environment it leaves to
the programs it starts. Nothing here builds or runs a binary:
scripts/smoke_test.sh does that, in CI."""
import os
import re

import pytest

import config

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """config as PyInstaller's bootloader leaves it: sys.frozen, sys._MEIPASS."""
    monkeypatch.setattr(config, "FROZEN", True)
    monkeypatch.setattr(config, "BUNDLE_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Files that ship with the app
# ---------------------------------------------------------------------------

def test_from_source_the_repo_holds_them():
    assert os.path.samefile(config.bundled("userscript"), os.path.join(REPO, "userscript"))


def test_frozen_the_bundle_holds_them(frozen):
    assert config.bundled("userscript") == str(frozen / "userscript")


def test_frozen_ui_is_the_bundles(frozen):
    assert config.static_dir() != str(frozen / "static")       # nothing there yet
    (frozen / "static").mkdir()
    (frozen / "static" / "index.html").write_text("<!doctype html>bundled")
    assert config.static_dir() == str(frozen / "static")


def test_frozen_dashboard_and_userscript_are_served_from_the_bundle(frozen, client, monkeypatch):
    (frozen / "static" / "assets").mkdir(parents=True)
    (frozen / "static" / "index.html").write_text("<!doctype html>bundled")
    (frozen / "static" / "assets" / "font.woff2").write_bytes(b"wOF2")
    (frozen / "userscript").mkdir()
    (frozen / "userscript" / "feedvault.user.js").write_text(
        '// @updateURL    http://localhost:3380/userscript/feedvault.user.js\n'
        'const API_BASE = "http://localhost:3380";\n')
    monkeypatch.setattr(config, "PORT", 3392)
    assert client.get("/").data == b"<!doctype html>bundled"
    assert client.get("/people/1").data == b"<!doctype html>bundled"
    assert client.get("/assets/font.woff2").data == b"wOF2"
    text = client.get("/userscript/feedvault.user.js").get_data(as_text=True)
    assert text == ('// @updateURL    http://localhost:3392/userscript/feedvault.user.js\n'
                    'const API_BASE = "http://localhost:3392";\n')


def test_frozen_without_its_userscript_is_a_404(frozen, client):
    assert client.get("/userscript/feedvault.user.js").status_code == 404


def test_the_spec_bundles_what_the_backend_looks_for():
    """Every folder asked of the bundle (config.bundled, static_dir) is one
    of the spec's datas."""
    with open(os.path.join(REPO, "packaging", "feedvault.spec"), encoding="utf-8") as f:
        dests = set(re.findall(r'^    \(.*, "([a-z]+)"\),$', f.read(), re.M))
    asked = {"static"}
    for name in sorted(os.listdir(os.path.join(REPO, "backend"))):
        if name.endswith(".py"):
            with open(os.path.join(REPO, "backend", name), encoding="utf-8") as f:
                asked.update(re.findall(r'config\.bundled\("([^"]+)"\)', f.read()))
    assert "userscript" in asked
    assert asked == dests


# ---------------------------------------------------------------------------
# LD_LIBRARY_PATH
# ---------------------------------------------------------------------------

def test_from_source_the_environment_is_left_alone():
    env = {"LD_LIBRARY_PATH": "/opt/lib", "LD_LIBRARY_PATH_ORIG": "/x"}
    config.restore_library_path(env)
    assert env == {"LD_LIBRARY_PATH": "/opt/lib", "LD_LIBRARY_PATH_ORIG": "/x"}


def test_frozen_the_bundles_library_path_is_dropped(frozen):
    env = {"LD_LIBRARY_PATH": str(frozen), "PATH": "/usr/bin"}
    config.restore_library_path(env)
    assert env == {"PATH": "/usr/bin"}


def test_frozen_the_users_library_path_is_put_back(frozen):
    env = {"LD_LIBRARY_PATH": f"{frozen}:/opt/lib", "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
    config.restore_library_path(env)
    assert env == {"LD_LIBRARY_PATH": "/opt/lib"}


def test_frozen_an_empty_one_is_put_back_as_it_was(frozen):
    env = {"LD_LIBRARY_PATH": str(frozen), "LD_LIBRARY_PATH_ORIG": ""}
    config.restore_library_path(env)
    assert env == {"LD_LIBRARY_PATH": ""}


def test_frozen_a_job_starts_with_the_restored_path(frozen, monkeypatch):
    """Jobs, ffmpeg, zenity and notify-send all start from os.environ."""
    monkeypatch.setenv("LD_LIBRARY_PATH", f"{frozen}:/opt/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")
    config.restore_library_path()
    assert os.environ["LD_LIBRARY_PATH"] == "/opt/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in os.environ


# ---------------------------------------------------------------------------
# The version a release stamps
# ---------------------------------------------------------------------------

def test_the_version_line_is_the_one_a_release_rewrites():
    """release.yml's sed matches exactly one line of config.py."""
    with open(os.path.join(REPO, "backend", "config.py"), encoding="utf-8") as f:
        lines = [line for line in f.read().splitlines() if re.match(r"^__version__ = \"", line)]
    assert lines == [f'__version__ = "{config.__version__}"']
    with open(os.path.join(REPO, ".github", "workflows", "release.yml"), encoding="utf-8") as f:
        assert r"""s|^__version__ = .*|__version__ = \"$V\"|""" in f.read()
