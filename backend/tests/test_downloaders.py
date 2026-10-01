"""Downloaders (downloaders.py): detection, latest versions, login status,
the test and update jobs. Every tool here is a fake on a temporary PATH, in
fake virtualenv and pipx layouts: nothing real is run, updated or read."""
import json
import os
import sys
import threading

import pytest

from conftest import H
from test_jobs import ended, wait_for

import jobs

# Prints <name>.version for --version. Any other run appends its arguments
# to <name>.argv, waits while <name>.hold exists, then prints <name>.fail
# and exits 1 if that exists, else prints "ok".
VERSION_SCRIPT = """#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.realpath(__file__))
base = os.path.join(here, {name!r})
if sys.argv[1:] in (["--version"], ["-version"]):
    print(open(base + ".version").read().strip())
    sys.exit(0)
with open(base + ".argv", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
while os.path.exists(base + ".hold"):
    time.sleep(0.02)
if os.path.exists(base + ".fail"):
    print(open(base + ".fail").read())
    sys.exit(1)
print("ok")
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


def fake_venv(folder, pip=True):
    """A virtualenv layout: pyvenv.cfg, bin/, and pip in its site-packages."""
    os.makedirs(os.path.join(folder, "bin"), exist_ok=True)
    if pip:
        os.makedirs(os.path.join(folder, "lib", "python3.12", "site-packages", "pip"))
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
    monkeypatch.setattr(jobs, "_active", jobs.collections.OrderedDict())
    monkeypatch.setattr(jobs, "_closing", False)
    monkeypatch.setattr(jobs, "_cool", {})     # no pause left over from another test
    monkeypatch.setattr(jobs, "KILL_AFTER", 0.5)
    yield {"bin": bin_dir, "venv": venv, "pipx_venv": pipx_venv, "tmp": tmp}
    jobs.shutdown()
    for t in threading.enumerate():
        if t.name.startswith("job-") and not t.name.endswith("-log"):
            t.join(10)
    downloaders.forget()


def runs(path):
    """The argument lists a fake tool was run with (--version aside)."""
    try:
        with open(os.path.realpath(path) + ".argv") as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


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
    # A child that leaves the group and keeps the output open is not waited for.
    pid_file = layout["tmp"] / "escaped.pid"
    escape = broken / "gallery-dl"
    escape.write_text(f"#!{sys.executable}\nimport subprocess, time\n"
                      f"p = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(30)'],"
                      f" start_new_session=True)\n"
                      f"open({str(pid_file)!r}, 'w').write(str(p.pid))\ntime.sleep(30)\n")
    escape.chmod(0o755)
    started = __import__("time").monotonic()
    try:
        assert downloaders.run_version(str(escape), "gallery-dl") == (None, "no answer to --version within 0.5 s")
        assert __import__("time").monotonic() - started < 5
    finally:
        if pid_file.exists():
            os.kill(int(pid_file.read_text()), 9)
    assert downloaders.run_version(str(bad), "yt-dlp") == (None, "--version failed (exit code 3): boom")


# ---------------------------------------------------------------------------
# Latest versions: a local server stands in for PyPI
# ---------------------------------------------------------------------------

@pytest.fixture
def pypi(layout, monkeypatch):
    """A fake PyPI on 127.0.0.1. ``answers[name]`` is (status, body bytes)
    or a callable(handler); ``asked`` lists the names requested."""
    import http.server
    import json as _json
    import threading
    import downloaders
    state = {"asked": [], "answers": {}}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.split("/")[2]
            state["asked"].append(name)
            answer = state["answers"].get(name) or (200, _json.dumps({"info": {"version": "2099.1.1"}}).encode())
            if callable(answer):
                return answer(self)
            code, body = answer
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(downloaders, "PYPI_URL", f"http://127.0.0.1:{server.server_port}/pypi/{{}}/json")
    yield state
    server.shutdown()
    server.server_close()


def check_updates(client, on=True):
    assert client.post("/api/config", json={"check_updates": on}, headers=H).get_json()["ok"]


def test_latest_is_off_by_default(pypi, client):
    r = client.get("/api/downloaders", headers=H).get_json()
    assert r["check_updates"] is False
    assert all(t["latest"] is None and t["outdated"] is None for t in r["tools"])
    assert client.get("/api/config", headers=H).get_json()["check_updates"] is False
    assert pypi["asked"] == []
    assert not client.post("/api/config", json={"check_updates": "yes"}, headers=H).get_json()["ok"]


def test_latest_once_a_day(pypi, client, env, monkeypatch):
    import downloaders
    import json as _json
    pypi["answers"]["gallery-dl"] = (200, _json.dumps({"info": {"version": "1.30.0"}}).encode())
    check_updates(client)
    tools = by_tool(client.get("/api/downloaders", headers=H).get_json())
    assert sorted(pypi["asked"]) == ["gallery-dl", "instaloader", "yt-dlp"]     # never ffmpeg
    assert tools["ffmpeg"]["latest"] is None
    assert tools["yt-dlp"]["latest"]["version"] == "2099.1.1" and tools["yt-dlp"]["outdated"] is True
    assert tools["gallery-dl"]["outdated"] is False                            # 1.30.0 installed
    assert tools["instaloader"]["latest"]["version"] == "2099.1.1" and tools["instaloader"]["outdated"] is None
    # Asked again neither on the next load, nor on Check again, nor after a restart.
    client.get("/api/downloaders", headers=H)
    client.post("/api/downloaders/check", headers=H)
    assert len(pypi["asked"]) == 3
    saved = _json.load(open(env["tmp"] / "data" / "downloaders" / "pypi.json"))
    assert saved["yt-dlp"]["version"] == "2099.1.1"
    # A day later: asked again.
    later = int(downloaders.time.time()) + downloaders.PYPI_EVERY + 1
    monkeypatch.setattr(downloaders.time, "time", lambda: later)
    client.get("/api/downloaders", headers=H)
    assert len(pypi["asked"]) == 6
    # Turned off: nothing shown, nothing asked.
    check_updates(client, False)
    monkeypatch.setattr(downloaders.time, "time", lambda: later + 10 * downloaders.PYPI_EVERY)
    assert all(t["latest"] is None for t in client.get("/api/downloaders", headers=H).get_json()["tools"])
    assert len(pypi["asked"]) == 6


@pytest.mark.parametrize("answer, error", [
    ((200, b"not json"), "PyPI's answer is not what was expected"),
    ((200, b'{"info": []}'), "PyPI's answer is not what was expected"),
    ((200, b'{"info": {"version": 5}}'), "PyPI's answer is not what was expected"),
    ((200, b'{"info": {"version": "1.0; rm -rf ~"}}'), "PyPI's answer is not what was expected"),
    ((200, b'{"info": {"version": "' + b"1" * 100 + b'"}}'), "PyPI's answer is not what was expected"),
    ((200, b"[" * 2000 + b"]" * 2000), "PyPI's answer is not what was expected"),
    ((404, b"{}"), "PyPI answered 404"),
    ((200, b'{"info": {"version": "1.0"}, "pad": "' + b"x" * 5000 + b'"}'), "PyPI's answer is too large"),
])
def test_bad_pypi_answers(pypi, monkeypatch, answer, error):
    import downloaders
    monkeypatch.setattr(downloaders, "PYPI_MAX", 4096)
    pypi["answers"]["yt-dlp"] = answer
    assert downloaders.fetch_latest("yt-dlp") == (None, error)


def test_pypi_redirect_and_slow_answer(pypi, monkeypatch):
    import time
    import downloaders

    def redirect(h):
        h.send_response(302)
        h.send_header("Location", "http://127.0.0.1:1/elsewhere")
        h.send_header("Content-Length", "0")
        h.end_headers()

    def drip(h):
        h.send_response(200)
        h.send_header("Content-Length", "100000")
        h.end_headers()
        try:
            for _ in range(25):
                h.wfile.write(b" " * 10)
                h.wfile.flush()
                time.sleep(0.05)
        except OSError:
            pass
    pypi["answers"]["yt-dlp"] = redirect
    assert downloaders.fetch_latest("yt-dlp") == (None, "PyPI answered 302")
    monkeypatch.setattr(downloaders, "PYPI_TIMEOUT", 0.5)
    pypi["answers"]["gallery-dl"] = drip
    assert downloaders.fetch_latest("gallery-dl") == (None, "PyPI did not answer within 0.5 s")


def test_unreachable_pypi_is_shown_and_not_retried_today(pypi, client, monkeypatch):
    import downloaders
    monkeypatch.setattr(downloaders, "PYPI_URL", "http://127.0.0.1:1/pypi/{}/json")
    check_updates(client)
    t = by_tool(client.get("/api/downloaders", headers=H).get_json())["yt-dlp"]
    assert t["latest"]["version"] is None and t["latest"]["error"].startswith("could not reach PyPI")
    assert t["outdated"] is None


@pytest.mark.parametrize("latest, installed, result", [
    ("2026.08.06", "2026.01.01", True), ("2026.08.06", "2026.08.06", False), ("2026.8.19", "2026.08.19", False),
    ("2026.08.06", "2026.08.06.232211", False), ("1.30", "1.30.0", False), ("1.31.0", "1.30.10", True),
    ("1.30.10", "1.30.9", True), ("4.15", "instaloader 4.14.2", True), ("1.0", "no digits", None),
])
def test_newer(latest, installed, result):
    import downloaders
    assert downloaders.newer(latest, installed) is result


# ---------------------------------------------------------------------------
# Login status and the test job
# ---------------------------------------------------------------------------

def test_login_status_from_the_settings(layout, client, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(layout["tmp"] / "xdg"))

    def logins():
        return {t["tool"]: t["login"] for t in client.get("/api/downloaders", headers=H).get_json()["tools"]}
    assert logins() == {"instaloader": {"mode": "none"}, "gallery-dl": {"mode": "none"},
                        "yt-dlp": {"mode": "none"}, "ffmpeg": None}
    for body in ({"instaloader": {"session": {"mode": "cookies", "browser": "firefox"}}},
                 {"yt-dlp": {"session": {"mode": "cookies", "browser": "brave"}}}):
        assert client.post("/api/config", json=body, headers=H).get_json()["ok"]
    got = logins()
    assert got["instaloader"] == {"mode": "cookies", "browser": "firefox"}
    assert got["yt-dlp"] == {"mode": "cookies", "browser": "brave"} and got["gallery-dl"] == {"mode": "none"}
    assert client.post("/api/config", json={"instaloader": {"session": {"mode": "login", "user": "Some.One"}}},
                       headers=H).get_json()["ok"]
    assert logins()["instaloader"] == {"mode": "login", "user": "Some.One", "session_file": False}
    # instaloader's own place for it, the name lowercased as instaloader does.
    # Unreadable: only its existence is looked at.
    session = layout["tmp"] / "xdg" / "instaloader" / "session-some.one"
    session.parent.mkdir(parents=True)
    session.write_text("secret")
    session.chmod(0)
    real_open = open

    def no_reading(path, *args, **kwargs):
        assert "session-" not in str(path), f"{path} was opened"
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", no_reading)
    assert logins()["instaloader"]["session_file"] is True


def test_test_job_argv(layout, client, monkeypatch):
    import downloaders
    import sync
    fake_tool(str(layout["bin"]), "instaloader", "4.15")
    data = str(layout["tmp"] / "data")
    for body in ({"instaloader": {"session": {"mode": "login", "user": "me"}}},
                 {"gallery-dl": {"session": {"mode": "cookies", "browser": "firefox"}}}):
        assert client.post("/api/config", json=body, headers=H).get_json()["ok"]
    got = {}
    for tool in ("instaloader", "gallery-dl", "yt-dlp"):
        r = client.post("/api/jobs", json={"kind": "tool-test", "params": {"tool": tool}}, headers=H).get_json()
        job = ended(r["job"]["id"])
        assert (job["state"], job["result"], job["message"], job["group"]) == \
            ("done", {"ok": True, "error": None, "line": None}, "Works", tool)
        assert job["label"] == f"Test {tool}" and job["cwd"] == os.path.join(data, "downloaders", "test")
        got[tool] = runs(layout["bin"] / tool)[-1]
    assert got["instaloader"] == ["--no-posts", "--no-profile-pic", "--no-metadata-json", "--dirname-pattern",
                                  os.path.join(data, "downloaders", "test"), "--login", "me", "--", "instagram"]
    assert got["gallery-dl"] == ["--simulate", "--cookies-from-browser", "firefox", "--", "https://x.com/jack/status/20"]
    assert got["yt-dlp"] == ["--simulate", "--no-playlist", "--", "https://www.youtube.com/watch?v=jNQXAC9IVRw"]
    # Each in its tool's lock group: the same as that tool's syncs.
    assert all(jobs._kinds[sync.KINDS[t]].group == t for t in downloaders.TESTED)


@pytest.mark.parametrize("params", [
    {"tool": "ffmpeg"}, {"tool": "yt-dlp; rm -rf ~"}, {"tool": "$(id)"}, {},
    {"tool": "yt-dlp", "url": "https://evil.example"}, {"tool": "yt-dlp", "args": ["--exec", "id"]},
])
def test_nothing_from_the_request_reaches_the_test_argv(layout, client, params):
    r = client.post("/api/jobs", json={"kind": "tool-test", "params": params}, headers=H)
    assert r.status_code == 400 and runs(layout["bin"] / "yt-dlp") == []


@pytest.mark.parametrize("tool, output, error", [
    ("yt-dlp", "ERROR: [youtube] jNQXAC9IVRw: Sign in to confirm you're not a bot. Use --cookies-from-browser",
     "login_required"),
    ("yt-dlp", "ERROR: [youtube] jNQXAC9IVRw: HTTP Error 429: Too Many Requests", "rate_limited"),
    ("yt-dlp", "ERROR: [youtube] jNQXAC9IVRw: Video unavailable", "not_found"),
    ("gallery-dl", "[twitter][error] AuthRequired: 'authenticated cookies' needed", "login_required"),
    ("instaloader", "Login error: \"fail\" status, message \"checkpoint_required\".", "login_required"),
    ("instaloader", "JSON Query to graphql/query: 403 Forbidden", "login_required"),
    ("instaloader", "Profile instagram does not exist.", "not_found"),
    ("gallery-dl", "something odd happened", "generic"),
])
def test_test_job_classification(layout, client, tool, output, error):
    import downloaders
    fake_tool(str(layout["bin"]), "instaloader")
    with open(os.path.realpath(layout["bin"] / tool) + ".fail", "w") as f:
        f.write(output)
    job = ended(jobs.submit("tool-test", {"tool": tool})["id"])
    assert job["state"] == "failed" and job["result"] == {"ok": False, "error": error, "line": output}
    expected = downloaders.TEST_MESSAGES[error] + (f": {output}" if error == "generic" else "")
    assert job["message"] == expected


def test_test_job_of_a_missing_tool(layout):
    job = ended(jobs.submit("tool-test", {"tool": "instaloader"})["id"])
    assert job["state"] == "failed" and job["message"] == "instaloader not found; set its path in Settings"


# ---------------------------------------------------------------------------
# Updating: a fake pip in a fake virtualenv, a fake pipx
# ---------------------------------------------------------------------------

# A virtualenv's python that only knows "-m pip install ... -U <name>": it
# writes <name>.version beside it from python.next. pipx: "upgrade <name>"
# writes $PIPX_HOME/venvs/<name>/bin/<name>.version from pipx.next. Both log
# their arguments (<self>.argv) and wait while <self>.hold exists.
FAKE_UPDATER = """#!{python}
import json, os, sys, time
me = os.path.abspath(__file__)
here = os.path.dirname(me)
with open(me + ".argv", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
while os.path.exists(me + ".hold"):
    time.sleep(0.02)
args = sys.argv[1:]
if {pipx}:
    assert args[0] == "upgrade"
    target = os.path.join(os.environ["PIPX_HOME"], "venvs", args[1], "bin", args[1] + ".version")
else:
    assert args[:3] == ["-m", "pip", "install"] and "-U" in args
    target = os.path.join(here, args[-1] + ".version")
new = open(me + ".next").read().strip()
print("Successfully installed", args[-1], new)
with open(target, "w") as f:
    f.write(new)
"""


def fake_updater(path, new, pipx=False):
    with open(path, "w") as f:
        f.write(FAKE_UPDATER.format(python=sys.executable, pipx=pipx))
    os.chmod(path, 0o755)
    with open(f"{path}.next", "w") as f:
        f.write(new)
    return path


def updater_runs(path):
    try:
        with open(f"{path}.argv") as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


def version_of(client, tool):
    return by_tool(client.get("/api/downloaders", headers=H).get_json())[tool]["version"]


def test_update_a_venv_tool(layout, client):
    python = fake_updater(os.path.join(layout["venv"], "bin", "python"), "2026.09.09")
    t = by_tool(client.get("/api/downloaders", headers=H).get_json())["yt-dlp"]
    assert t["update"] == {"possible": True, "command": f"{python} -m pip install -U yt-dlp", "reason": None}
    r = client.post("/api/jobs", json={"kind": "tool-update", "params": {"tool": "yt-dlp"}}, headers=H).get_json()
    assert r["job"]["argv"] == [python, "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
                                "-U", "yt-dlp"]
    assert r["job"]["group"] == "yt-dlp" and r["job"]["label"] == "Update yt-dlp"
    job = ended(r["job"]["id"])
    assert job["state"] == "done"
    assert updater_runs(python) == [r["job"]["argv"][1:]]
    # Found again once it ended: the new version shows without Check again.
    assert version_of(client, "yt-dlp") == "2026.09.09"


def test_update_a_pipx_tool(layout, client):
    pipx = fake_updater(str(layout["bin"] / "pipx"), "1.31.0", pipx=True)
    assert version_of(client, "gallery-dl") == "1.30.0"
    t = by_tool(client.get("/api/downloaders", headers=H).get_json())["gallery-dl"]
    assert t["update"] == {"possible": True, "command": "pipx upgrade gallery-dl", "reason": None}
    job = ended(jobs.submit("tool-update", {"tool": "gallery-dl"})["id"])
    assert job["state"] == "done" and job["argv"] == ["pipx", "upgrade", "gallery-dl"]
    assert updater_runs(pipx) == [["upgrade", "gallery-dl"]]
    assert version_of(client, "gallery-dl") == "1.31.0"


def test_update_refused_for_system_missing_and_without_pipx(layout, client):
    def refused(tool):
        r = client.post("/api/jobs", json={"kind": "tool-update", "params": {"tool": tool}}, headers=H)
        assert r.status_code == 400
        return r.get_json()["error"]
    tools = by_tool(client.get("/api/downloaders", headers=H).get_json())
    # Missing: the install command, shown, never run.
    assert tools["instaloader"]["update"]["possible"] is False
    assert tools["instaloader"]["install_hint"] == "pipx install instaloader"
    assert "run pipx install instaloader" in refused("instaloader")
    # ffmpeg: never updated from here, not even a choice.
    assert tools["ffmpeg"]["update"]["possible"] is False and tools["ffmpeg"]["install_hint"] is None
    assert "must be one of" in refused("ffmpeg")
    # A system install.
    fake_tool(str(layout["bin"]), "instaloader")
    client.post("/api/downloaders/check", headers=H)
    t = by_tool(client.get("/api/downloaders", headers=H).get_json())["instaloader"]
    assert (t["install"], t["update"]["possible"], t["update"]["command"]) == ("system", False, "pipx install instaloader")
    assert "not in a virtualenv" in refused("instaloader")
    # pipx install, but no pipx to run.
    assert "pipx is not on the PATH" in refused("gallery-dl")
    # A venv without its python.
    assert "no Python at" in refused("yt-dlp")
    assert all(not updater_runs(p) for p in (os.path.join(layout["venv"], "bin", "python"),))


@pytest.mark.parametrize("params", [
    {"tool": "yt-dlp", "package": "evil"}, {"tool": "yt-dlp", "command": "pip install evil"},
    {"tool": "yt-dlp; pip install evil"}, {"tool": ["yt-dlp"]}, {"tool": "pip"},
])
def test_nothing_from_the_request_reaches_the_update_argv(layout, client, params):
    python = fake_updater(os.path.join(layout["venv"], "bin", "python"), "2026.09.09")
    r = client.post("/api/jobs", json={"kind": "tool-update", "params": params}, headers=H)
    assert r.status_code == 400 and updater_runs(python) == []


def test_update_refused_without_pip_or_under_another_pipx_name(layout, client, monkeypatch):
    # A virtualenv without pip (uv makes them): no Update button.
    uv = fake_venv(str(layout["tmp"] / "uv" / "tools" / "yt-dlp"), pip=False)
    fake_updater(os.path.join(uv, "bin", "python"), "2026.09.09")
    tool = fake_tool(os.path.join(uv, "bin"), "yt-dlp")
    assert client.post("/api/config", json={"tools": {"yt-dlp": tool}}, headers=H).get_json()["ok"]
    t = by_tool(client.get("/api/downloaders", headers=H).get_json())["yt-dlp"]
    assert (t["install"], t["update"]["possible"]) == ("venv", False) and "no pip" in t["update"]["reason"]
    r = client.post("/api/jobs", json={"kind": "tool-update", "params": {"tool": "yt-dlp"}}, headers=H)
    assert r.status_code == 400 and updater_runs(os.path.join(uv, "bin", "python")) == []
    # pipx install --suffix: pipx knows it by another name; upgrading "gallery-dl" would miss it.
    fake_updater(str(layout["bin"] / "pipx"), "1.31.0", pipx=True)
    other = fake_venv(str(layout["tmp"] / "pipx" / "venvs" / "gallery-dl@nightly"))
    tool = fake_tool(os.path.join(other, "bin"), "gallery-dl")
    assert client.post("/api/config", json={"tools": {"gallery-dl": tool}}, headers=H).get_json()["ok"]
    t = by_tool(client.get("/api/downloaders", headers=H).get_json())["gallery-dl"]
    assert (t["install"], t["update"]) == ("pipx", {"possible": False, "command": "pipx upgrade gallery-dl@nightly",
                                                    "reason": "pipx knows it as gallery-dl@nightly, not gallery-dl"})
    assert client.post("/api/jobs", json={"kind": "tool-update", "params": {"tool": "gallery-dl"}},
                       headers=H).status_code == 400
    assert updater_runs(str(layout["bin"] / "pipx")) == []


def test_test_jobs_keep_their_tools_pause(layout, client):
    assert client.post("/api/config", json={"yt-dlp": {"pause": 600}}, headers=H).get_json()["ok"]
    first = ended(jobs.submit("tool-test", {"tool": "yt-dlp"})["id"])
    assert first["state"] == "done"
    # The next run of yt-dlp, a test or a sync, waits out the pause; gallery-dl does not.
    nxt = jobs.submit("tool-test", {"tool": "yt-dlp"})
    assert nxt["state"] == "queued" and nxt["waits_until"] >= first["ended_at"] + 599
    assert ended(jobs.submit("tool-test", {"tool": "gallery-dl"})["id"])["state"] == "done"
    assert jobs.get(nxt["id"])["state"] == "queued"


def test_update_and_sync_never_run_at_once(layout, client):
    python = fake_updater(os.path.join(layout["venv"], "bin", "python"), "2026.09.09")
    assert client.post("/api/config", json={"yt-dlp": {"pause": 0}}, headers=H).get_json()["ok"]
    hold = os.path.realpath(layout["bin"] / "yt-dlp") + ".hold"
    open(hold, "w").close()
    src = client.post("/api/sources", json={"target": "https://www.youtube.com/@someone"}, headers=H).get_json()
    sync_job = client.post(f"/api/sources/{src['source']['id']}/sync", json={}, headers=H).get_json()["job"]
    wait_for(lambda: runs(layout["bin"] / "yt-dlp"))           # the sync's yt-dlp is running
    update = jobs.submit("tool-update", {"tool": "yt-dlp"})
    other = jobs.submit("tool-test", {"tool": "gallery-dl"})   # another tool's group is not held
    assert ended(other["id"])["state"] == "done"
    assert jobs.get(update["id"])["state"] == "queued" and updater_runs(python) == []
    os.remove(hold)
    assert ended(sync_job["id"])["state"] == "done"
    assert ended(update["id"])["state"] == "done"
    # And the other way round: a sync waits for the update.
    open(python + ".hold", "w").close()
    update = jobs.submit("tool-update", {"tool": "yt-dlp"})
    wait_for(lambda: len(updater_runs(python)) == 2)
    sync_job = client.post(f"/api/sources/{src['source']['id']}/sync", json={}, headers=H).get_json()["job"]
    before = len(runs(layout["bin"] / "yt-dlp"))
    assert jobs.get(sync_job["id"])["state"] == "queued"
    os.remove(python + ".hold")
    assert ended(update["id"])["state"] == "done"
    assert ended(sync_job["id"])["state"] == "done"
    assert len(runs(layout["bin"] / "yt-dlp")) == before + 1


# ---------------------------------------------------------------------------
# Sync failures that point to the Downloaders card
# ---------------------------------------------------------------------------

def sync_once(client, url):
    src = client.post("/api/sources", json={"target": url}, headers=H).get_json()["source"]
    job = client.post(f"/api/sources/{src['id']}/sync", json={}, headers=H).get_json()["job"]
    ended(job["id"])
    return client.get(f"/api/sources/{src['id']}", headers=H).get_json()["last_result"]


def test_sync_of_a_missing_tool(layout, client):
    r = sync_once(client, "https://www.instagram.com/someone")
    assert (r["state"], r["error"], r["outdated"]) == ("failed", "missing", False)
    assert r["message"] == "instaloader not found; set its path in Settings"


def test_failed_sync_says_the_tool_is_outdated(layout, client, env):
    with open(os.path.realpath(layout["bin"] / "yt-dlp") + ".fail", "w") as f:
        f.write("ERROR: [youtube:tab] @someone: Sign in to confirm you're not a bot")
    assert client.post("/api/config", json={"yt-dlp": {"pause": 0}}, headers=H).get_json()["ok"]
    # Off: nothing said, nothing asked.
    r = sync_once(client, "https://www.youtube.com/@someone")
    assert (r["error"], r["outdated"]) == ("login_required", False) and "out of date" not in r["message"]
    # On, with today's answer already known: said, without asking PyPI again.
    folder = env["tmp"] / "data" / "downloaders"
    folder.mkdir(parents=True, exist_ok=True)
    now = int(__import__("time").time())
    (folder / "pypi.json").write_text(json.dumps({n: {"version": "2026.08.06", "error": None, "checked_at": now}
                                                 for n in ("instaloader", "gallery-dl", "yt-dlp")}))
    check_updates(client)
    r = sync_once(client, "https://www.youtube.com/@other")
    assert (r["state"], r["error"], r["outdated"]) == ("failed", "login_required", True)
    assert r["message"].endswith(". yt-dlp 2026.01.01 is out of date (2026.08.06 is out): "
                                 "update it in Settings → Downloaders")
    # Up to date: not said.
    with open(os.path.join(layout["venv"], "bin", "yt-dlp.version"), "w") as f:
        f.write("2026.08.06")
    client.post("/api/downloaders/check", headers=H)
    r = sync_once(client, "https://www.youtube.com/@third")
    assert r["outdated"] is False and "out of date" not in r["message"]
