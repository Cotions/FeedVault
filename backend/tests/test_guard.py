"""The test guard (conftest.py, #56): no test finds or starts a program
outside its tmp dir and the fakes, nor reads under the user's HOME.

The "real" tools here are stand-ins too, outside the test's tmp dir: each
writes a file when it runs, so a run that got through would show.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys

import pytest

import jobs
from toolguard import ToolGuardError
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


def refused(guard, run):
    """What the guard refused while ``run`` ran: in this process (it raised)
    or in a child (from the guard's log)."""
    try:
        run()
    except ToolGuardError:
        pass
    return guard.taken()


def test_every_way_to_start_a_program_is_checked(env, elsewhere, tool_guard, monkeypatch):
    true, ran = str(elsewhere / "true"), elsewhere / "ran"
    py = sys.executable
    site, package = str(elsewhere / "site"), elsewhere / "site" / "yt_dlp"     # a downloader's package
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f"open({str(ran)!r}, 'a').write('yt_dlp')\n")
    for run, says in [
        (lambda: subprocess.run([true]), f"run {true}"),
        (lambda: subprocess.run(["./true"], cwd=elsewhere), "run "),
        (lambda: subprocess.run(["true"], cwd=elsewhere, env={**os.environ, "PATH": ""}), "run "),
        (lambda: os.posix_spawn(true, ["true"], os.environ), f"run {true}"),       # os.exec*: the same check
        (lambda: os.spawnv(os.P_WAIT, true, ["true"]), "[child "),                 # refused in the forked child
        (lambda: os.system("true"), "os.system('true')"),
        (lambda: subprocess.run("true", shell=True), "run /bin/sh"),               # sh only as a shebang
        # A Python the test starts has the same guard: its program, its import, its connection.
        (lambda: subprocess.run([py, "-c", f"import subprocess; subprocess.run([{true!r}])"]), f"run {true}"),
        (lambda: subprocess.run([py, "-c", f"import sys; sys.path.insert(0, {site!r}); import yt_dlp"]),
         "import yt_dlp"),
        (lambda: subprocess.run([py, "-c", "import socket; socket.create_connection(('192.0.2.1', 80), 1)"]),
         "look up 192.0.2.1"),
        (lambda: socket.getaddrinfo("pypi.org", 443), "look up pypi.org"),
        (lambda: socket.socket().connect(("192.0.2.1", 9)), "connect to 192.0.2.1"),
    ]:
        out = refused(tool_guard, run)
        assert len(out) == 1 and says in out[0], (says, out)
    monkeypatch.setenv("PATH", str(elsewhere))             # by name, through PATH
    assert refused(tool_guard, lambda: subprocess.run(["true"])) == [f"run {true}"]
    assert refused(tool_guard, lambda: shutil.which("true")) == [f"shutil.which('true') found {true}"]
    # A symlink or a script in the tmp dir does not make the program it reaches allowed.
    link = env["tmp"] / "true"
    link.symlink_to(true)
    script = env["tmp"] / "script"
    script.write_text(f"#!{true}\n")
    script.chmod(0o755)
    with_env = env["tmp"] / "with-env"
    with_env.write_text("#!/usr/bin/env python3\n")       # env is no interpreter of the allowed ones
    with_env.chmod(0o755)
    assert refused(tool_guard, lambda: subprocess.run([str(link)])) == [f"run {link} ({true})"]
    assert refused(tool_guard, lambda: subprocess.run([str(script)])) == [f"run as an interpreter {true}"]
    assert refused(tool_guard, lambda: subprocess.run([str(with_env)]))[0].startswith("run as an interpreter")
    monkeypatch.syspath_prepend(site)
    assert refused(tool_guard, lambda: __import__("yt_dlp")) == [f"import yt_dlp ({package / '__init__.py'})"]
    assert not ran.exists()
    # This machine is fine.
    server = socket.create_server(("127.0.0.1", 0))
    with server, socket.create_connection(server.getsockname(), 1):
        pass
    # What a test writes in its tmp dir runs; another program only once it is allowed, visibly.
    ok = env["tmp"] / "ok"
    ok.write_text(f"#!{sys.executable}\nprint('ok')\n")
    ok.chmod(0o755)
    assert subprocess.run([str(ok)], capture_output=True, text=True).stdout == "ok\n"
    tool_guard.allow(true, "this test only: shows what allow() does")
    assert subprocess.run([true]).returncode == 0
    assert ran.read_text() == "true\n" and tool_guard.violations == []


def test_home_and_xdg_folders_are_the_tests(env, tool_guard):
    import downloaders
    tmp_dirs = [os.path.realpath(env["tmp"]), os.path.realpath(tool_guard.dir)]

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
    assert os.environ["PATH"] == str(tool_guard.dir / "bin") and not os.listdir(os.environ["PATH"])
    assert jobs.tool_path("instaloader") is None


def test_a_script_run_from_a_memfd_is_checked_as_its_file_is(env, elsewhere, tool_guard):
    """jobs.Script (#73): the guard reads the memfd's name and #! line."""
    ran = elsewhere / "ran"

    def run_memfd(path, data, exe):
        fd = jobs._sealed(jobs.Script(str(path), data))
        try:
            return subprocess.run([exe, f"/dev/fd/{fd}"], pass_fds=(fd,), capture_output=True, text=True)
        finally:
            os.close(fd)

    mark = f"open({str(ran)!r}, 'a').write('memfd')\n".encode()
    # Read from a file outside the tmp dir: refused, as that file would be.
    out = refused(tool_guard, lambda: run_memfd(elsewhere / "x.sh", b"#!" + sys.executable.encode() + b"\n" + mark,
                                                sys.executable))
    assert out == [f"run a memfd script read from {elsewhere / 'x.sh'}"]
    # An interpreter its #! does not name: refused.
    out = refused(tool_guard, lambda: run_memfd(env["tmp"] / "x.sh", b"#!/bin/true\n" + mark, "/bin/sh"))
    assert out and "whose #! is not it" in out[0]
    assert not ran.exists()
    # From a file in the tmp dir, its own #!: runs, /bin/sh as an interpreter only.
    got = run_memfd(env["tmp"] / "ok.sh", b"#!/bin/sh\necho \"ok $0\"\n", "/bin/sh")
    assert got.stdout.startswith("ok /dev/fd/") and tool_guard.violations == []
