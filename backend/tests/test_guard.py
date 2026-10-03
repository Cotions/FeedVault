"""The test guard (conftest.py, #56): no test finds or starts a program
outside its tmp dir and the fakes, nor reads under the user's HOME.

The "real" tools here are stand-ins too, outside the test's tmp dir: each
writes a file when it runs, so a run that got through would show.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

import pytest

import jobs
from conftest import ToolGuardError
from test_sync import add_source, fake, sync_now  # noqa: F401  (fake: the fixture)


def children():
    """The pids this process has started and not reaped yet."""
    pids = set()
    for path in glob.glob(f"/proc/{os.getpid()}/task/*/children"):
        with open(path) as f:
            pids.update(f.read().split())
    return pids


@pytest.fixture
def elsewhere(tmp_path_factory):
    """A folder outside the test's tmp dir, with stand-ins for real tools that
    leave ``ran`` beside them when they run."""
    folder = tmp_path_factory.mktemp("elsewhere")
    for name in ("instaloader", "yt-dlp", "true"):
        tool = folder / name
        tool.write_text(f"#!{sys.executable}\nopen({str(folder / 'ran')!r}, 'a').write({name!r} + '\\n')\n")
        tool.chmod(0o755)
    return folder


def test_a_removed_fake_starts_nothing_else(env, client, fake, elsewhere, tool_guard, monkeypatch):  # noqa: F811
    # #55's case: the fake gone, a real instaloader further down PATH.
    monkeypatch.setenv("PATH", f"{os.environ['PATH']}{os.pathsep}{elsewhere}")
    (env["tmp"] / "bin" / "instaloader").unlink()
    before = children()
    job = sync_now(client, add_source(client, "carol.cooks")["id"])
    assert job["state"] == "failed" and "test guard (#56)" in job["message"]
    assert tool_guard.taken() == [f"shutil.which('instaloader') found {elsewhere / 'instaloader'}"]
    assert tool_guard.runs == [] and children() <= before and not (elsewhere / "ran").exists()
    assert not fake.runs()


def test_a_tool_path_set_in_settings_outside_the_fakes_is_refused(env, elsewhere, tool_guard):
    import config
    import downloaders
    cfg = config.load()
    cfg["tools"] = {"yt-dlp": str(elsewhere / "yt-dlp")}     # as a hand-edited config.json would
    config.save(cfg)
    with pytest.raises(ToolGuardError, match="tool_path"):
        downloaders.detect("yt-dlp")
    assert tool_guard.taken() == [f"jobs.tool_path('yt-dlp') is {elsewhere / 'yt-dlp'}"]
    assert tool_guard.runs == [] and not (elsewhere / "ran").exists()


def test_every_way_to_start_a_program_is_checked(env, elsewhere, tool_guard, monkeypatch):
    ran = elsewhere / "ran"
    with pytest.raises(ToolGuardError):
        subprocess.run([str(elsewhere / "true")])
    monkeypatch.setenv("PATH", str(elsewhere))             # by name, through PATH
    with pytest.raises(ToolGuardError):
        subprocess.run(["true"])
    with pytest.raises(ToolGuardError):
        shutil.which("true")
    with pytest.raises(ToolGuardError):              # os.exec* is checked the same way
        os.posix_spawn(str(elsewhere / "true"), ["true"], os.environ)
    with pytest.raises(ToolGuardError):
        os.system("true")
    # A symlink or a script in the tmp dir does not make the program it reaches allowed.
    link = env["tmp"] / "true"
    link.symlink_to(elsewhere / "true")
    script = env["tmp"] / "script"
    script.write_text(f"#!{elsewhere / 'true'}\n")
    script.chmod(0o755)
    with_env = env["tmp"] / "with-env"
    with_env.write_text("#!/usr/bin/env true\n")
    with_env.chmod(0o755)
    for path in (link, script, with_env):
        with pytest.raises(ToolGuardError):
            subprocess.run([str(path)])
    assert len(tool_guard.taken()) == 8 and not ran.exists()
    with pytest.raises(ToolGuardError, match="import yt_dlp"):
        import yt_dlp  # noqa: F401
    assert tool_guard.taken() == ["import yt_dlp"]
    # What a test writes in its tmp dir runs; another program only once it is allowed, visibly.
    ok = env["tmp"] / "ok"
    ok.write_text(f"#!{sys.executable}\nprint('ok')\n")
    ok.chmod(0o755)
    assert subprocess.run([str(ok)], capture_output=True, text=True).stdout == "ok\n"
    tool_guard.allow(elsewhere / "true", "this test only: shows what allow() does")
    assert subprocess.run([str(elsewhere / "true")]).returncode == 0
    assert ran.read_text() == "true\n" and tool_guard.violations == []


def test_home_and_xdg_folders_are_the_tests(env, tool_guard):
    import downloaders
    tmp_dirs = [os.path.realpath(r) for r in tool_guard.roots[:2]]

    def under_tmp(path):
        return any(os.path.realpath(path).startswith(r + os.sep) for r in tmp_dirs)

    assert all(under_tmp(p) for p in downloaders.session_files("Carol.Cooks"))
    assert all(under_tmp(p) for p in downloaders.pipx_venvs())
    # The tools started see the same: where instaloader keeps its session,
    # gallery-dl and yt-dlp their configs, and the browsers their cookies.
    tool, seen = env["tmp"] / "yt-dlp", env["tmp"] / "seen.json"
    tool.write_text(f"#!{sys.executable}\nimport json, os\n"
                    "paths = [os.path.expanduser('~/.mozilla'), os.path.expanduser('~/.config/yt-dlp'),"
                    " *(os.environ[v] for v in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'TMPDIR'))]\n"
                    f"open({str(seen)!r}, 'w').write(json.dumps(paths))\nprint('2026.01.01')\n")
    tool.chmod(0o755)
    assert downloaders.run_version(str(tool), "yt-dlp") == ("2026.01.01", None)
    assert all(under_tmp(p) for p in json.loads(seen.read_text()))
    assert os.environ["PATH"] == os.path.join(tool_guard.roots[1], "bin") and not os.listdir(os.environ["PATH"])
    assert jobs.tool_path("instaloader") is None
