"""Downloaders (downloaders.py): detection, latest versions, login status,
the test and update jobs. Every tool here is a fake on a temporary PATH, in
fake virtualenv and pipx layouts: nothing real is run, updated or read."""
import os
import sys

import pytest

from conftest import H

VERSION_SCRIPT = """#!{python}
import os, sys
here = os.path.dirname(os.path.realpath(__file__))
if sys.argv[1:] in (["--version"], ["-version"]):
    print(open(os.path.join(here, {name!r} + ".version")).read().strip())
    sys.exit(0)
print("args:", sys.argv[1:])
"""


def fake_tool(folder, name, version="1.0.0"):
    """An executable ``name`` in ``folder`` printing ``version`` (kept in
    ``<name>.version`` beside it) for --version."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        f.write(VERSION_SCRIPT.format(python=sys.executable, name=name))
    os.chmod(path, 0o755)
    with open(path + ".version", "w") as f:
        f.write(version)
    return path


def fake_venv(folder):
    """A virtualenv layout: pyvenv.cfg and bin/."""
    os.makedirs(os.path.join(folder, "bin"), exist_ok=True)
    with open(os.path.join(folder, "pyvenv.cfg"), "w") as f:
        f.write("home = /usr/bin\n")
    return folder


@pytest.fixture
def layout(env, monkeypatch):
    """PATH = <tmp>/bin only, with:
    - gallery-dl: a pipx install (a symlink to <tmp>/pipx/venvs/gallery-dl/bin/gallery-dl)
    - yt-dlp: a symlink to a plain virtualenv's tool
    - ffmpeg: a system binary
    - instaloader: missing"""
    import downloaders
    tmp = env["tmp"]
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("PIPX_HOME", str(tmp / "pipx"))
    pipx_venv = fake_venv(str(tmp / "pipx" / "venvs" / "gallery-dl"))
    os.symlink(fake_tool(os.path.join(pipx_venv, "bin"), "gallery-dl", "1.30.0"), bin_dir / "gallery-dl")
    venv = fake_venv(str(tmp / "venv"))
    os.symlink(fake_tool(os.path.join(venv, "bin"), "yt-dlp", "2026.01.01"), bin_dir / "yt-dlp")
    fake_tool(str(bin_dir), "ffmpeg", "ffmpeg version 6.1.1 Copyright (c) 2000-2023 the FFmpeg developers")
    downloaders.forget()
    yield {"bin": bin_dir, "venv": venv, "pipx_venv": pipx_venv, "tmp": tmp}
    downloaders.forget()


def by_tool(status):
    return {t["tool"]: t for t in status["tools"]}


def test_install_kinds(layout):
    import downloaders
    tools = by_tool(downloaders.status())
    assert [t for t in tools] == ["instaloader", "gallery-dl", "yt-dlp", "ffmpeg"]
    assert tools["instaloader"]["install"] == "missing" and not tools["instaloader"]["found"]
    assert tools["instaloader"]["version"] is None and tools["instaloader"]["path"] is None
    g = tools["gallery-dl"]
    assert (g["install"], g["venv"], g["version"]) == ("pipx", layout["pipx_venv"], "1.30.0")
    assert g["path"] == str(layout["bin"] / "gallery-dl")
    assert g["real_path"] == os.path.join(layout["pipx_venv"], "bin", "gallery-dl")
    y = tools["yt-dlp"]                        # a symlinked venv tool
    assert (y["install"], y["venv"], y["version"]) == ("venv", layout["venv"], "2026.01.01")
    f = tools["ffmpeg"]
    assert (f["install"], f["venv"], f["real_path"]) == ("system", None, None)
    assert f["version"] == "ffmpeg version 6.1.1"


def test_a_folder_named_bin_without_pyvenv_cfg_is_system(layout):
    import downloaders
    folder = layout["tmp"] / "notvenv" / "bin"
    path = fake_tool(str(folder), "yt-dlp")
    assert downloaders.install_of(path) == ("system", None)
    # A virtualenv beside pipx's folder, not in it, is a plain venv.
    other = fake_venv(str(layout["tmp"] / "pipx" / "elsewhere"))
    assert downloaders.install_of(fake_tool(os.path.join(other, "bin"), "yt-dlp")) == ("venv", other)


def test_pipx_default_homes(layout, monkeypatch):
    import downloaders
    monkeypatch.delenv("PIPX_HOME")
    monkeypatch.setenv("HOME", str(layout["tmp"] / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(layout["tmp"] / "home" / ".local" / "share"))
    for base in (layout["tmp"] / "home" / ".local" / "share" / "pipx", layout["tmp"] / "home" / ".local" / "pipx"):
        venv = fake_venv(str(base / "venvs" / "yt-dlp"))
        assert downloaders.install_of(fake_tool(os.path.join(venv, "bin"), "yt-dlp")) == ("pipx", venv)


def test_configured_path_and_cache(layout, client):
    import downloaders
    first = by_tool(downloaders.status())
    assert first["yt-dlp"]["version"] == "2026.01.01"
    # Kept in memory: a new version on disk shows only once checked again.
    with open(os.path.join(layout["venv"], "bin", "yt-dlp.version"), "w") as f:
        f.write("2026.02.02")
    assert by_tool(downloaders.status())["yt-dlp"]["version"] == "2026.01.01"
    r = client.post("/api/downloaders/check", headers=H).get_json()
    assert r["ok"] and by_tool(r)["yt-dlp"]["version"] == "2026.02.02"
    # Setting a tool's path in Settings finds it again.
    other = fake_tool(str(layout["tmp"] / "elsewhere"), "instaloader", "4.15")
    assert client.post("/api/config", json={"tools": {"instaloader": other}}, headers=H).get_json()["ok"]
    got = by_tool(client.get("/api/downloaders", headers=H).get_json())["instaloader"]
    assert (got["found"], got["configured"], got["install"], got["version"]) == (True, other, "system", "4.15")
    # A set path that stops working: missing, not PATH's.
    os.remove(other)
    assert client.post("/api/downloaders/check", headers=H).get_json()["tools"][0]["install"] == "missing"


def test_version_errors(layout, monkeypatch):
    import downloaders
    hang = layout["bin"] / "instaloader"
    hang.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    hang.chmod(0o755)
    broken = layout["tmp"] / "broken"
    broken.mkdir()
    bad = broken / "yt-dlp"
    bad.write_text(f"#!{sys.executable}\nimport sys\nprint('boom')\nsys.exit(3)\n")
    bad.chmod(0o755)
    monkeypatch.setattr(downloaders, "VERSION_TIMEOUT", 0.5)
    assert downloaders.run_version(str(hang), "instaloader") == (None, "no answer to --version within 0.5 s")
    assert downloaders.run_version(str(bad), "yt-dlp") == (None, "--version failed (exit code 3): boom")
