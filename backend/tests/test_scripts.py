"""Scripts (#5): built-in templates, files on disk read strictly, runs."""
import collections
import json
import os
import re
import sys
import time

import pytest

from conftest import H

import config
import db
import jobs
import scheduler
import scripts
import sources
import sync


@pytest.fixture
def folder(env):
    """The scripts folder: beside the test's config.json."""
    d = env["tmp"] / "scripts"
    d.mkdir(mode=0o755)
    return d


def write(folder, name, content, mode=0o644):
    path = folder / name
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    path.chmod(mode)
    return path


COMMAND = {"name": "Mine", "needs": "target", "rescan": "{root}",
           "argv": ["instaloader", "--no-videos", "--dirname-pattern", "{root}", "--", "{target}"]}
SHELL = "#!/bin/sh\n# name: Echo\n# needs: url\n# rescan: {root}\necho \"$FV_URL\"\n"


def listed(client):
    r = client.get("/api/scripts", headers=H)
    assert r.status_code == 200
    return {s["id"]: s for s in r.get_json()["scripts"]}


def test_builtins_are_listed_read_only(client, env):
    got = listed(client)
    names = {"instaloader-profile", "instaloader-saved", "instaloader-post", "instaloader-stories",
             "gallery-dl-user", "gallery-dl-url", "yt-dlp-video", "yt-dlp-channel"}
    assert {i for i in got if i.startswith("builtin:")} == {"builtin:" + n for n in names}
    for name in names:
        s = got["builtin:" + name]
        assert s["builtin"] and s["kind"] == "command" and s["refused"] is None and s["path"] is None
        # Each one parses as the file a copy would be.
        fields, error = scripts.parse_command(scripts.template(name))
        assert error is None and fields["argv"] == s["argv"]
    one = client.get("/api/scripts/builtin:instaloader-profile", headers=H).get_json()
    assert json.loads(one["content"])["argv"][0] == "instaloader"
    assert client.get("/api/scripts/builtin:nope", headers=H).status_code == 404


def test_files_on_disk_are_listed(client, folder):
    write(folder, "mine.json", COMMAND)
    write(folder, "echo.sh", SHELL, 0o755)
    body = client.get("/api/scripts", headers=H).get_json()
    assert body["dir"] == str(folder) and body["dir_refused"] is None
    assert "FV_URL" in body["shell_template"]
    got = {s["id"]: s for s in body["scripts"]}
    assert got["mine"]["kind"] == "command" and got["mine"]["tool"] == "instaloader"
    assert got["mine"]["refused"] is None and got["mine"]["needs"] == "target"
    assert got["echo"]["kind"] == "shell" and got["echo"]["needs"] == "url" and got["echo"]["name"] == "Echo"
    assert got["echo"]["refused"] is None and got["echo"]["argv"] is None
    one = client.get("/api/scripts/echo", headers=H).get_json()
    assert one["content"] == SHELL


def test_no_folder_lists_the_builtins_only(client, env):
    body = client.get("/api/scripts", headers=H).get_json()
    assert body["dir_refused"] is None
    assert all(s["builtin"] for s in body["scripts"])


def test_an_edit_counts_at_once(client, folder):
    path = write(folder, "mine.json", COMMAND)
    assert listed(client)["mine"]["name"] == "Mine"
    path.write_text(json.dumps({**COMMAND, "name": "Changed"}))
    assert listed(client)["mine"]["name"] == "Changed"
    path.unlink()
    assert "mine" not in listed(client)


@pytest.mark.parametrize("content, reason", [
    ("{", "not valid JSON"),
    ([], "JSON object"),
    ({**COMMAND, "shell": True}, "unknown key"),
    ({**COMMAND, "argv": []}, "argv must be"),
    ({**COMMAND, "argv": ["bash", "-c", "x"]}, "argv[0] must be"),
    ({**COMMAND, "argv": ["{root}/x"]}, "argv[0] must be"),
    ({**COMMAND, "needs": "everything"}, "needs must be"),
    ({**COMMAND, "needs": "none"}, "uses {target} but needs none"),
    ({**COMMAND, "needs": "target", "argv": ["yt-dlp", "{url}"]}, "uses {url} but needs target"),
    ({**COMMAND, "name": "a\nb"}, "name must be"),
])
def test_malformed_commands_are_refused_with_the_reason(client, folder, content, reason):
    write(folder, "bad.json", content)
    assert reason in listed(client)["bad"]["refused"]


@pytest.mark.parametrize("content, reason", [
    ("echo hi\n", "#!"),
    ("#!sh\n# needs: none\n", "#!"),
    ("#!/bin/sh\necho hi\n", "needs"),
    ("#!/bin/sh\n# needs: none\n# needs: url\n", "twice"),
    ("#!/bin/sh\n# needs: none\n# rescan: {url}\n", "uses {url}"),
])
def test_malformed_shell_scripts_are_refused_with_the_reason(client, folder, content, reason):
    write(folder, "bad.sh", content, 0o755)
    assert reason in listed(client)["bad"]["refused"]


def test_the_header_ends_at_the_first_line_that_is_not_a_comment(client, folder):
    write(folder, "late.sh", "#!/bin/sh\n# needs: none\necho\n# needs: url\n", 0o755)
    assert listed(client)["late"]["needs"] == "none"


def test_two_files_with_one_id_are_both_refused(client, folder):
    write(folder, "twin.json", COMMAND)
    write(folder, "twin.sh", SHELL, 0o755)
    body = client.get("/api/scripts", headers=H).get_json()
    twins = [s for s in body["scripts"] if s["id"] == "twin"]
    assert len(twins) == 2 and all("two files" in s["refused"] for s in twins)


# ---------------------------------------------------------------------------
# Read strictly (step 3)
# ---------------------------------------------------------------------------

def refusal(client, sid):
    return listed(client)[sid]["refused"]


def test_a_symlinked_script_is_refused(client, folder, tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(COMMAND))
    (folder / "link.json").symlink_to(target)
    assert "symlink" in refusal(client, "link")
    # Its target's content is never read or shown.
    assert client.get("/api/scripts/link", headers=H).get_json()["content"] is None


@pytest.mark.parametrize("mode", [0o666, 0o646, 0o664, 0o624])
def test_a_script_writable_by_others_is_refused(client, folder, mode):
    write(folder, "open.json", COMMAND, mode)
    assert "writable by group or others" in refusal(client, "open")


def test_a_script_of_another_owner_is_refused(client, folder, monkeypatch):
    write(folder, "theirs.json", COMMAND)
    real = os.getuid()
    monkeypatch.setattr(scripts.os, "getuid", lambda: real + 1)
    body = client.get("/api/scripts", headers=H).get_json()
    # The folder is then someone else's too: nothing in it is listed.
    assert "belongs to another user" in body["dir_refused"]
    monkeypatch.setattr(scripts, "_folder_refused", lambda *a, **k: None)
    assert "belongs to another user" in refusal(client, "theirs")


@pytest.mark.parametrize("name", ["../evil.json", "Upper.json", "a b.sh", ".hidden.sh", "x.py", "x.json~",
                                  "x" * 65 + ".json"])
def test_names_outside_the_charset_are_refused(client, folder, name):
    if "/" not in name:
        write(folder, name, COMMAND)
    sid = name.rsplit(".", 1)[0]
    assert scripts.get(sid) is None
    if "/" not in name:
        got = {s["file"]: s for s in client.get("/api/scripts", headers=H).get_json()["scripts"] if not s["builtin"]}
        assert "the name must be" in got[name]["refused"] and got[name]["kind"] is None


def test_an_id_with_a_path_finds_nothing(client, folder, tmp_path):
    (tmp_path / "evil.json").write_text(json.dumps(COMMAND))
    for sid in ("../evil", "..%2Fevil", "builtin:../x", "/etc/passwd"):
        assert scripts.get(sid) is None
        assert client.get(f"/api/scripts/{sid}", headers=H).status_code == 404


def test_too_large_or_not_text_or_not_a_file_is_refused(client, folder):
    write(folder, "big.json", " " * (scripts.SIZE_MAX + 1))
    (folder / "bin.json").write_bytes(b"\xff\xfe")
    (folder / "bin.json").chmod(0o644)
    os.mkfifo(folder / "pipe.json", 0o644)
    (folder / "dir.json").mkdir(mode=0o755)
    assert "larger than" in refusal(client, "big")
    assert "UTF-8" in refusal(client, "bin")
    assert "not a regular file" in refusal(client, "pipe")
    assert "not a regular file" in refusal(client, "dir")


def test_a_shell_script_must_be_executable(client, folder):
    write(folder, "noexec.sh", SHELL, 0o644)
    assert "not executable" in refusal(client, "noexec")


def test_a_symlinked_or_open_folder_is_refused(client, env, tmp_path):
    real = tmp_path / "real-scripts"
    real.mkdir(mode=0o755)
    write(real, "mine.json", COMMAND)
    (env["tmp"] / "scripts").symlink_to(real)
    body = client.get("/api/scripts", headers=H).get_json()
    assert "symlink" in body["dir_refused"] and all(s["builtin"] for s in body["scripts"])
    assert scripts.get("mine") is None
    (env["tmp"] / "scripts").unlink()
    real.rename(env["tmp"] / "scripts")
    (env["tmp"] / "scripts").chmod(0o777)
    body = client.get("/api/scripts", headers=H).get_json()
    assert "writable by group or others" in body["dir_refused"] and scripts.get("mine") is None
    (env["tmp"] / "scripts").chmod(0o755)
    assert scripts.get("mine")["refused"] is None
    os.chmod(env["tmp"], 0o777)
    try:
        assert "parent folder" in client.get("/api/scripts", headers=H).get_json()["dir_refused"]
    finally:
        os.chmod(env["tmp"], 0o700)


# ---------------------------------------------------------------------------
# Running (step 4)
# ---------------------------------------------------------------------------

TESTS = os.path.dirname(os.path.abspath(__file__))
# A program that writes down what it was given: its arguments and its
# environment's FV_* and FAKE_SECRET, one JSON line per run; with
# --post <folder>, an Instagram post there (fakes.write_post).
RECORDER = f"""#!{sys.executable}
import json, os, sys
sys.path.insert(0, {TESTS!r})
args = sys.argv[1:]
with open(os.environ["RECORDER_LOG"], "a") as f:
    f.write(json.dumps({{"args": args, "env": {{k: v for k, v in os.environ.items()
                                             if k.startswith("FV_") or k == "FAKE_SECRET"}}}}) + "\\n")
if "--hold" in args:
    open(os.environ["RECORDER_GATE"]).read()
if "--post" in args:
    import fakes
    fakes.write_post(args[args.index("--post") + 1], "CSCRIPT0001", 1717243200, fakes.owner("carol.cooks", "1001"))
print(os.environ.get("RECORDER_SAY") or f"recorded {{len(args)}} arguments")
sys.exit(int(os.environ.get("RECORDER_EXIT", "0")))
"""


class Recorder:
    def __init__(self, tmp):
        self.bin = tmp / "bin"
        self.bin.mkdir(exist_ok=True)
        self.log = tmp / "recorder.log"
        self.path = self.bin / "recorder"
        self.path.write_text(RECORDER)
        self.path.chmod(0o755)

    def install_as(self, tool):
        exe = self.bin / tool
        exe.write_text(RECORDER)
        exe.chmod(0o755)

    def runs(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


@pytest.fixture
def runner(env, folder, monkeypatch):
    monkeypatch.setattr(jobs, "_active", collections.OrderedDict())
    monkeypatch.setattr(jobs, "_closing", False)
    monkeypatch.setattr(jobs, "_cool", {})
    monkeypatch.setattr(jobs, "_wake", None)
    monkeypatch.setattr(jobs, "KILL_AFTER", 0.5)
    monkeypatch.setattr(sync, "_batch", None)
    rec = Recorder(env["tmp"])
    monkeypatch.setenv("PATH", f"{rec.bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("RECORDER_LOG", str(rec.log))
    return rec


def wait_for(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("timed out")


def ended(job_id, timeout=10):
    wait_for(lambda: job_id not in jobs._active and jobs.get(job_id)["state"] not in ("queued", "running"), timeout)
    return jobs.get(job_id)


def run(client, sid, status=200, headers=None, **inputs):
    r = client.post(f"/api/scripts/{sid}/run", json=inputs, headers={**H, **(headers or {})})
    assert r.status_code == status, r.get_json()
    return r.get_json()


def log_of(client, job_id):
    return [ln["text"] for ln in client.get(f"/api/jobs/{job_id}/log", headers=H).get_json()["lines"]]


def command(rec, *args, needs="target", rescan=None):
    return {"needs": needs, "rescan": rescan, "argv": [str(rec.path), *args]}


def test_a_target_reaches_the_program_as_one_literal_argument(client, folder, runner, env):
    write(folder, "rec.json", command(runner, "--flag", "--", "{target}"))
    job = run(client, "rec", target="x; rm -rf ~")["job"]
    assert ended(job["id"])["state"] == "done"
    assert runner.runs()[-1]["args"] == ["--flag", "--", "x; rm -rf ~"]


def test_a_url_with_shell_syntax_reaches_the_program_as_one_argument(client, folder, runner):
    url = "https://example.com/$(echo pwned)/`id`/a b?x=1;y=$HOME"
    write(folder, "rec.json", command(runner, "--url={url}", "{url}", needs="url"))
    assert run(client, "rec", status=400, url=url)["error"].startswith("url must be")    # a space
    url = url.replace(" ", "%20")
    job = run(client, "rec", url=url)["job"]
    assert ended(job["id"])["state"] == "done"
    assert runner.runs()[-1]["args"] == ["--url=" + url, url]


def test_placeholders_are_replaced_in_their_element_once(client, folder, runner, env):
    write(folder, "rec.json", command(runner, "{root}/x", "{data_dir}", "{archive}", "{profile}", "{target}",
                                      "--", "{target}{target}"))
    job = run(client, "rec", target="{root}")["job"]
    assert ended(job["id"])["state"] == "done"
    data = str(env["tmp"] / "data")
    assert runner.runs()[-1]["args"] == [f"{env['media']}/x", data, f"{data}/scripts/rec.archive", "{profile}",
                                         "{root}", "--", "{root}{root}"]


@pytest.mark.parametrize("name, root, want", [
    ("instaloader-profile", "/m/{old}", "/m/{{old}}"),
    ("yt-dlp-video", "/m/50% clips", "/m/50%% clips/" + sync.YT_DLP_NAME),
])
def test_a_folder_in_an_option_its_tool_formats_is_escaped(env, name, root, want):
    """As the tool's own sync escapes it (sync._escape, _build_yt_dlp)."""
    script = scripts.get("builtin:" + name)
    vals = scripts.values(script, config.load(), root, "carol.cooks", "https://example.com/v")
    argv = scripts.command(script, vals)
    option = "--dirname-pattern" if name.startswith("instaloader") else "-o"
    assert argv[argv.index(option) + 1] == want
    # Anywhere else a value stays as it is.
    assert all("{{" not in a and "%%" not in a for a in scripts.command(
        {**script, "argv": [script["tool"], "{root}", "--", "{root}"]}, vals))


@pytest.mark.parametrize("tool, argv, want", [
    ("instaloader", ["--dirname-pattern", "{root}/{profile}"], ["--dirname-pattern", "/m/{{x}} 50%/{profile}"]),
    ("instaloader", ["--dirname-pattern={root}/{profile}"], ["--dirname-pattern=/m/{{x}} 50%/{profile}"]),
    ("instaloader", ["--filename-pattern", "{root}_{date_utc}"], ["--filename-pattern", "/m/{{x}} 50%_{date_utc}"]),
    ("instaloader", ["--filename-pattern={root}_{date_utc}"], ["--filename-pattern=/m/{{x}} 50%_{date_utc}"]),
    ("yt-dlp", ["-o", "{root}/%(id)s.%(ext)s"], ["-o", "/m/{x} 50%%/%(id)s.%(ext)s"]),
    ("yt-dlp", ["--output", "{root}/%(id)s.%(ext)s"], ["--output", "/m/{x} 50%%/%(id)s.%(ext)s"]),
    ("yt-dlp", ["--output={root}/%(id)s.%(ext)s"], ["--output=/m/{x} 50%%/%(id)s.%(ext)s"]),
    ("yt-dlp", ["-o{root}/%(id)s.%(ext)s"], ["-o/m/{x} 50%%/%(id)s.%(ext)s"]),
    ("instaloader", ["--title-pattern={root}_{date_utc}"], ["--title-pattern=/m/{{x}} 50%_{date_utc}"]),
    ("yt-dlp", ["--exec", "echo {root}/%(id)s"], ["--exec", "echo /m/{x} 50%%/%(id)s"]),
    ("yt-dlp", ["--exec=echo {root}"], ["--exec=echo /m/{x} 50%%"]),
    # argparse takes a unique prefix of a long option; yt-dlp's are never unique.
    ("instaloader", ["--dirname={root}", "--filename", "{root}"], ["--dirname=/m/{{x}} 50%", "--filename", "/m/{{x}} 50%"]),
    ("yt-dlp", ["--outp={root}", "--exe", "{root}"], ["--outp=/m/{x} 50%", "--exe", "/m/{x} 50%"]),
    # Not those options: as it is.
    ("instaloader", ["--dirname-patterns={root}", "-d{root}", "--={root}"], ["--dirname-patterns=/m/{x} 50%",
                                                                         "-d/m/{x} 50%", "--=/m/{x} 50%"]),
    ("yt-dlp", ["--output-na-placeholder={root}", "-P{root}"], ["--output-na-placeholder=/m/{x} 50%", "-P/m/{x} 50%"]),
    # An option as the value of a formatted one is that value.
    ("yt-dlp", ["-o", "-o", "{root}"], ["-o", "-o", "/m/{x} 50%"]),
    # gallery-dl's format strings (#64), its flags before a short option, prefixes.
    ("gallery-dl", ["-f", "{root}_{id}"], ["-f", "/m/{{x}} 50%_{id}"]),
    ("gallery-dl", ["-qN{root}"], ["-qN/m/{{x}} 50%"]),
    ("gallery-dl", ["--print=post:{root}"], ["--print=post:/m/{{x}} 50%"]),
    ("gallery-dl", ["--filen", "{root}", "--rename-to={root}"], ["--filen", "/m/{{x}} 50%", "--rename-to=/m/{{x}} 50%"]),
    ("gallery-dl", ["--print-to-file", "{root}", "{root}/out.txt", "-D", "{root}"],
     ["--print-to-file", "/m/{{x}} 50%", "/m/{x} 50%/out.txt", "-D", "/m/{x} 50%"]),
])
def test_a_folder_in_a_joined_option_is_escaped_too(env, tool, argv, want):
    """#59: --opt=value (and yt-dlp's -ovalue) as the separate form, for a root with {x} and %."""
    script = {"tool": tool, "argv": [tool, *argv]}
    vals = scripts.values(script, config.load(), "/m/{x} 50%")
    assert scripts.command(script, vals) == [tool, *want]


EXEC_URL = {"needs": "url", "argv": ["yt-dlp", "--exec", "notify-send done {url}", "--", "{url}"]}


def test_a_placeholder_in_a_shell_run_option_is_refused_and_never_run(client, folder, runner, source):
    """#62: the issue's example, through Run and through a source's Sync."""
    runner.install_as("yt-dlp")
    write(folder, "notify.json", EXEC_URL)
    refused = listed(client)["notify"]["refused"]
    assert refused.startswith("--exec's value can reach a shell") and "%(webpage_url)q" in refused \
        and "FV_*" in refused and "{url}" in refused
    error = run(client, "notify", status=400, url="https://example.com/$(touch pwned)")["error"]
    assert "is refused" in error and "--exec" in error
    assert "--exec" in attach(client, source["id"], "notify", status=400)["error"]
    assert runner.runs() == [] and jobs.active() == []


@pytest.mark.parametrize("tool, args, option", [
    ("yt-dlp", ["--exec", "echo {root}"], "--exec"),
    ("yt-dlp", ["--exec=echo {url}"], "--exec"),
    ("yt-dlp", ["--exec", "before_dl:echo {url}"], "--exec"),
    ("yt-dlp", ["--exec-before-download={archive}"], "--exec-before-download"),
    ("yt-dlp", ["--exec-b", "echo {data_dir}"], "--exec-before-download"),
    ("yt-dlp", ["--netrc-cmd", "pass {url}"], "--netrc-cmd"),
    ("yt-dlp", ["--use-postprocessor=Exec:exec_cmd=echo {url}"], "--use-postprocessor"),
    ("yt-dlp", ["--use-p", "Exec:exec_cmd=echo {url}"], "--use-postprocessor"),
    ("gallery-dl", ["--exec", "convert {} {root}/x.png"], "--exec"),
    ("gallery-dl", ["--exec-after={root}"], "--exec-after"),
    ("gallery-dl", ["--exec-a", "cd {root}"], "--exec-after"),
    ("gallery-dl", ["-o", "postprocessors=[{\"name\": \"exec\", \"command\": \"echo {url}\"}]"], "-o"),
    ("gallery-dl", ["-obase-directory={root}"], "-o"),
    ("gallery-dl", ["-qo", "x={root}"], "-o"),
    ("gallery-dl", ["--opt=x={root}"], "--option"),
    ("gallery-dl", ["-P", "exec", "-O", "command=echo {url}"], "-O"),
    ("gallery-dl", ["--postprocessor-option", "command=echo {url}"], "--postprocessor-option"),
    ("gallery-dl", ["-So", "x={url}"], "-o"),
    # Read as Python, or split into aria2c's or ffmpeg's arguments.
    ("gallery-dl", ["--filter", "'{url}' != ''"], "--filter"),
    ("gallery-dl", ["--chapter-filter={url}"], "--chapter-filter"),
    ("yt-dlp", ["--downloader-args", "aria2c:-d {root}"], "--downloader-args"),
    ("yt-dlp", ["--external-downloader-args={root}"], "--external-downloader-args"),
    ("yt-dlp", ["--ppa", "ffmpeg:-metadata url={url}"], "--ppa"),
    ("yt-dlp", ["--postprocessor-args", "{root}"], "--postprocessor-args"),
    # The downloader by its path, or behind env.
    ("/usr/local/bin/yt-dlp", ["--exec", "echo {url}"], "--exec"),
    ("/usr/bin/env", ["A=1", "-u", "B", "gallery-dl", "--exec", "echo {url}"], "--exec"),
])
def test_each_shell_run_option_and_form_is_refused(tool, args, option):
    _, error = scripts.parse_command(json.dumps({"needs": "url", "argv": [tool, *args, "--", "{url}"]}))
    assert error and error.startswith(f"{option}'s value can reach ")


@pytest.mark.parametrize("argv, reason", [
    (["/bin/sh", "-c", "notify-send {url}"], "a shell's -c text is read as code"),
    (["/bin/bash", "-ec", "echo {url}", "bash"], "a shell's -c text is read as code"),
    (["/bin/sh", "-o", "errexit", "-c", "echo {url}"], "a shell's -c text is read as code"),
    (["/bin/bash", "--rcfile", "x", "-c", "echo {url}"], "a shell's -c text is read as code"),
    (["/bin/sh", "-o", "{url}", "-c", "x"], "among a shell's options"),
    (["/usr/bin/env", "-S", "yt-dlp --exec x", "{url}"], "env -S"),
    (["/usr/bin/env", "--split-string=yt-dlp {url}"], "env -S"),
])
def test_a_placeholder_in_a_shells_code_is_refused(argv, reason):
    _, error = scripts.parse_command(json.dumps({"needs": "url", "argv": argv}))
    assert error and reason in error


@pytest.mark.parametrize("tool, args", [
    # The tool's own fields, not FeedVault's.
    ("yt-dlp", ["--exec", "notify-send done %(webpage_url)q"]),
    ("yt-dlp", ["--exec=echo %(filepath)q"]),
    ("gallery-dl", ["--exec", "convert {} {}.png && rm {_path}"]),
    ("gallery-dl", ["-o", "skip=abort:5", "-O", "command=echo {_path}"]),
    # Another option's whole name, though a prefix of one that runs a shell.
    ("yt-dlp", ["--netrc", "{url}"]),
    ("gallery-dl", ["--postprocessor", "metadata", "-D", "{root}"]),
    ("yt-dlp", ["--downloader", "aria2c", "--external-downloader", "{root}/aria2c"]),
    # Placeholders in options that never reach a shell.
    ("yt-dlp", ["-P", "{root}", "--download-archive", "{archive}", "--match-filters", "id!={url}"]),
    ("gallery-dl", ["-D", "{root}", "--download-archive", "{archive}", "-q"]),
    # What follows a short option that takes a value is that value.
    ("gallery-dl", ["-D{root}o{url}"]),
    # --alias without a placeholder anywhere.
    ("yt-dlp", ["--alias", "n", "--exec {0}", "https://example.com/a"]),
    # Not a downloader: its options are its own.
    ("/usr/local/bin/other", ["--exec", "{url}"]),
    # A shell given the placeholder as an argument after its -c text, or a script's path.
    ("/bin/sh", ["-ec", "notify-send done \"$1\"", "sh", "{url}"]),
    ("/bin/bash", ["/home/me/fetch.sh", "{url}"]),
])
def test_other_options_and_the_tools_own_fields_are_accepted(tool, args):
    needs = "url" if any("{url}" in a for a in args) else "none"
    _, error = scripts.parse_command(json.dumps({"needs": needs, "argv": [tool, *args]}))
    assert error is None


def test_an_alias_is_refused_beside_a_placeholder(client, folder):
    write(folder, "alias.json", {"needs": "url", "argv": ["yt-dlp", "--alias", "n", "--exec \"echo {0}\"",
                                                          "--n", "{url}"]})
    assert listed(client)["alias"]["refused"].startswith("--alias carries what follows it")


def test_exec_without_a_placeholder_is_accepted_and_runs(client, folder, runner):
    runner.install_as("yt-dlp")
    write(folder, "done.json", {"needs": "url", "argv": ["yt-dlp", "--exec", "notify-send done %(webpage_url)q",
                                                         "--", "{url}"]})
    assert listed(client)["done"]["refused"] is None
    job = run(client, "done", url="https://example.com/v")["job"]
    assert ended(job["id"])["state"] == "done"
    assert runner.runs()[-1]["args"] == ["--exec", "notify-send done %(webpage_url)q", "--",
                                         "https://example.com/v"]


def test_the_builtins_hold_no_placeholder_in_a_shell_run_option():
    for name in scripts.BUILTINS:
        assert scripts._check_shell(scripts.BUILTINS[name]["argv"]) is None


@pytest.mark.parametrize("args", [
    ["-iS", "yt-dlp --exec \"echo {url}\" {url}"],
    ["-vS", "yt-dlp {url}"],
    ["-0S", "yt-dlp {url}"],
    ["-i0S", "yt-dlp {url}"],
    ["-iSyt-dlp {url}"],
    ["-u", "NAME", "-S", "yt-dlp {url}"],
    ["-uNAME", "-iS", "yt-dlp {url}"],
    ["--split-string=yt-dlp {url}"],
    ["--split", "yt-dlp {url}"],
])
def test_env_splitting_a_text_in_a_cluster_is_refused(args):
    """#64: env reads its options as getopt does, so S in a cluster splits too."""
    _, error = scripts.parse_command(json.dumps({"needs": "url", "argv": ["/usr/bin/env", *args]}))
    assert error and error.startswith("env -S splits its text")


@pytest.mark.parametrize("args", [
    ["-i"],
    ["-u", "NAME"],
    ["-C", "/tmp"],
    ["-uS"],                                    # S is -u's value here
    ["-iu", "S", "--chdir", "/tmp"],
])
def test_env_options_that_split_nothing_reach_the_programs_checks(args):
    argv = ["/usr/bin/env", *args, "yt-dlp"]
    _, error = scripts.parse_command(json.dumps({"needs": "url", "argv": [*argv, "{url}"]}))
    assert error is None
    _, error = scripts.parse_command(json.dumps({"needs": "url", "argv": [*argv, "--exec", "echo {url}", "{url}"]}))
    assert error and error.startswith("--exec's value can reach a shell")


EVAL_URL = {"needs": "url", "argv": ["gallery-dl", "-f", "\fE '{url}'", "--", "{url}"]}


def test_a_placeholder_in_an_evaluated_format_is_refused_and_never_run(client, folder, runner, source):
    """#64: the issue's example, through Run and through a source's Sync."""
    runner.install_as("gallery-dl")
    write(folder, "eval.json", EVAL_URL)
    refused = listed(client)["eval"]["refused"]
    assert refused.startswith("-f's value is a format string that gallery-dl evaluates as Python") \
        and "{_path}" in refused and "FV_*" in refused and "{url}" in refused
    error = run(client, "eval", status=400, url="https://example.com/a")["error"]
    assert "is refused" in error and "-f's value" in error
    assert "-f's value" in attach(client, source["id"], "eval", status=400)["error"]
    assert runner.runs() == [] and jobs.active() == []


@pytest.mark.parametrize("args, option", [
    (["-f", "\fE '{url}'"], "-f's value"),
    (["-f\fE '{url}'"], "-f's value"),
    (["--filename=\fE '{url}'"], "--filename's value"),
    (["--filen", "\fE '{url}'"], "--filename's value"),
    (["-qf\fE '{url}'"], "-f's value"),
    (["-qf", "\fE '{url}'"], "-f's value"),
    (["-f", "\\fE '{url}'"], "-f's value"),           # "\\f" is read as \f too
    (["-f=\\fE '{url}'"], "-f's value"),           # argparse drops the "="
    (["-qf=\fE '{url}'"], "-f's value"),
    (["-N=\\fE '{url}'"], "-N's value"),
    (["-qN=\fE '{url}'"], "-N's value"),
    (["-f", "\fT {root}/name.txt"], "-f's value"),
    (["-N", "post:\fF {url}"], "-N's value"),
    (["-qNpost:\fF {url}"], "-N's value"),
    (["--print", "\fM {root}/mod.py:f"], "--print's value"),
    (["--Print", "\fE '{url}'"], "--Print's value"),
    (["--Print", "file:\\fE '{url}'"], "--Print's value"),
    (["--print-to-file", "\fE '{url}'", "out.txt"], "--print-to-file's value"),
    (["--Print-to-file", "after:\fJ {url}", "out.txt"], "--Print-to-file's value"),
    (["--print-to-file", "{id}", "/tmp/\fE {url}"], "--print-to-file's FILE"),
    (["--rename-to", "\fE '{url}'"], "--rename-to's value"),
])
def test_a_placeholder_in_a_format_another_formatter_reads_is_refused(args, option):
    _, error = scripts.parse_command(json.dumps({"needs": "url", "argv": ["gallery-dl", *args, "--", "{url}"]}))
    assert error and error.startswith(f"{option} is a format string that gallery-dl evaluates as Python")


@pytest.mark.parametrize("args", [
    ["-f", "{url}"],                            # a plain format string
    ["-f", "\fE title"],                        # no FeedVault placeholder
    ["-N", "post:{id} {url}"],
    ["--print-to-file", "{id}", "{root}/out.txt"],
    ["--print", "{url}", "-f", "\fF {title}"],
    ["--print-traffic", "-D", "{root}"],
    ["--mtime", "date", "-D", "{root}"],
])
def test_plain_formats_and_formats_without_a_placeholder_are_accepted(args):
    _, error = scripts.parse_command(json.dumps({"needs": "url", "argv": ["gallery-dl", *args, "--", "{url}"]}))
    assert error is None


def test_a_link_in_a_gallery_dl_format_is_escaped(client, folder, runner):
    """A link's braces are text to gallery-dl's formatter, never a field ({_env[…]})."""
    runner.install_as("gallery-dl")
    write(folder, "print.json", {"needs": "url", "argv": ["gallery-dl", "-N", "post:{url}", "--", "{url}"]})
    job = run(client, "print", url="https://example.com/{_env[HOME]}")["job"]
    assert ended(job["id"])["state"] == "done"
    assert runner.runs()[-1]["args"] == ["-N", "post:https://example.com/{{_env[HOME]}}", "--",
                                         "https://example.com/{_env[HOME]}"]


@pytest.mark.parametrize("argv", [
    ["{bin}/gallery-dl", "-f", "{target}"],
    ["/usr/bin/env", "-i", "{bin}/gallery-dl", "-N", "{target}"],
])
def test_a_downloader_by_its_path_takes_a_link_as_its_target(client, folder, runner, argv):
    """Else a target of "\\fE …" would start the format gallery-dl evaluates."""
    runner.install_as("gallery-dl")
    write(folder, "path.json", {"needs": "target", "argv": [a.format(bin=runner.bin, target="{target}")
                                                            for a in argv] + ["https://example.com/a"]})
    assert listed(client)["path"]["refused"] is None
    error = run(client, "path", status=400, target="\\fE __import__('os').getpid()")["error"]
    assert error == "target must be an http(s) link for gallery-dl"
    assert runner.runs() == [] and jobs.active() == []


def test_print_to_file_with_a_target_in_its_file_is_accepted():
    _, error = scripts.parse_command(json.dumps({"needs": "target", "argv": [
        "gallery-dl", "--print-to-file", "{id}", "{target}/out.txt", "https://example.com/a"]}))
    assert error is None


def test_a_folder_with_a_dollar_is_refused_to_gallery_dl_and_yt_dlp(client, folder, runner, env):
    write(folder, "gdl.json", {"needs": "url", "argv": ["gallery-dl", "-D", "{root}", "--", "{url}"]})
    error = run(client, "gdl", status=400, url="https://example.com/a", folder=f"{env['media']}/a$HOME")["error"]
    assert "holds a $" in error and runner.runs() == []


def test_the_cookie_sweep_waits_for_any_yt_dlp_run(client, monkeypatch):
    monkeypatch.setattr(jobs, "active", lambda: [{"kind": "script-sync", "group": "yt-dlp", "state": "running"}])
    r = client.post("/api/yt-dlp/info-json-cookies", json={"apply": True}, headers=H)
    assert r.status_code == 409 and "yt-dlp is running" in r.get_json()["error"]


def test_a_shell_script_gets_its_inputs_as_env_values_only(client, folder, runner, env, monkeypatch):
    monkeypatch.setenv("FAKE_SECRET", "kept out")
    out = env["tmp"] / "out.txt"
    # Shell builtins only (the guard cannot see what /bin/sh starts).
    write(folder, "env.sh", "#!/bin/sh\n# needs: url\n"
                            f"printf '%s\\n' \"$FV_URL\" \"$FV_ROOT\" \"$FV_DATA_DIR\" > {out}\n"
                            f"export -p >> {out}\n"
                            "echo \"$FV_URL\" | while read -r line; do echo \"got: $line\"; done\n", 0o755)
    url = "https://example.com/$(echo${IFS}pwned>pwned)/`echo${IFS}pwned2>pwned2`;echo${IFS}pwned3>pwned3"
    job = run(client, "env", url=url)["job"]
    assert ended(job["id"])["state"] == "done"
    lines = out.read_text().splitlines()
    assert lines[:3] == [url, str(env["media"]), str(env["tmp"] / "data")]
    exported = "\n".join(lines[3:])
    assert "FAKE_SECRET" not in exported and "FEEDVAULT_CONFIG" not in exported and "RECORDER_LOG" not in exported
    assert "FV_TARGET" in exported and "HOME" in exported
    assert not list(env["tmp"].rglob("pwned*"))
    shown = client.get(f"/api/jobs/{job['id']}", headers=H).get_json()
    assert shown["argv"] == [str(folder / "env.sh")] and shown["kind"] == "script"
    log = log_of(client, job["id"])
    assert any(t.startswith(f"[feedvault] script {folder / 'env.sh'} (sha256 ") for t in log)
    assert f"[feedvault] FV_URL={url}" in log and f"got: {url}" in log


def test_a_shell_target_is_one_env_value(client, folder, runner, env):
    out = env["tmp"] / "out.txt"
    write(folder, "t.sh", f"#!/bin/sh\n# needs: target\nprintf '%s' \"$FV_TARGET\" > {out}\n", 0o755)
    target = "x; echo pwned > pwned; `echo pwned2 > pwned2` $(echo pwned3 > pwned3)"
    assert ended(run(client, "t", target=target)["job"]["id"])["state"] == "done"
    assert out.read_text() == target
    assert not list(env["tmp"].rglob("pwned*"))


@pytest.mark.parametrize("sid, inputs, error", [
    ("rec", {}, "target must be"),
    ("rec", {"target": "-rf"}, "not starting with -"),
    ("rec", {"target": "a\nb"}, "target must be"),
    ("rec", {"target": "x", "url": "https://x.com/a"}, "takes no url"),
    ("rec", {"target": "x", "folder": "/etc"}, "inside a media root"),
    ("insta", {"target": "x; rm -rf ~"}, "profile name"),
    ("insta", {"target": "-x"}, "profile name"),
    ("ytdlp", {"target": "notalink"}, "http(s) link"),
    ("urlonly", {"url": "ftp://x.com/a"}, "url must be"),
    ("urlonly", {"url": "javascript:alert(1)"}, "url must be"),
    ("urlonly", {"url": "https://" + "a" * 600}, "url must be"),
])
def test_bad_inputs_are_refused_and_nothing_runs(client, folder, runner, sid, inputs, error):
    write(folder, "rec.json", command(runner, "{target}"))
    write(folder, "insta.json", {"needs": "target", "argv": ["instaloader", "--", "{target}"]})
    write(folder, "ytdlp.json", {"needs": "target", "argv": ["yt-dlp", "--", "{target}"]})
    write(folder, "urlonly.json", command(runner, "{url}", needs="url"))
    assert error in run(client, sid, status=400, **inputs)["error"]
    assert runner.runs() == [] and jobs.active() == []


def test_refused_scripts_are_never_run(client, folder, runner, env, tmp_path, monkeypatch):
    good = command(runner, "{target}")
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(good))
    (folder / "link.json").symlink_to(elsewhere)
    write(folder, "open.json", good, 0o666)
    write(folder, "noexec.sh", f"#!{runner.path}\n# needs: target\n", 0o644)
    for sid in ("link", "open", "noexec"):
        assert "is refused" in run(client, sid, status=400, target="x")["error"]
    for sid in ("Link", "builtin:nope", "builtin:..", ".."):
        assert run(client, sid, status=404, target="x")["ok"] is False
    # %2F is a / to the router: no route has a path in it.
    assert client.post("/api/scripts/..%2Felsewhere/run", json={"target": "x"}, headers=H).status_code in (404, 405)
    real = os.getuid()
    write(folder, "theirs.json", good)
    monkeypatch.setattr(scripts.os, "getuid", lambda: real + 1)
    # Its folder is refused: that is the reason given, never "no such script".
    assert "the scripts folder belongs to another user" in run(client, "theirs", status=400, target="x")["error"]
    got = client.get("/api/scripts/theirs", headers=H)
    assert got.status_code == 404 and "the scripts folder belongs to another user" in got.get_json()["error"]
    monkeypatch.setattr(scripts, "_folder_refused", lambda *a, **k: None)
    assert "belongs to another user" in run(client, "theirs", status=400, target="x")["error"]
    assert runner.runs() == [] and jobs.active() == []


def test_a_script_changed_after_it_was_queued_does_not_run(client, folder, runner, env):
    gate = env["tmp"] / "gate"
    os.mkfifo(gate)
    # Holds the "scripts" group (builtins only: read blocks on the fifo).
    write(folder, "hold.sh", f"#!/bin/sh\n# needs: none\nread -r x < {gate}\n", 0o755)
    path = write(folder, "rec.json", command(runner, "{target}"))
    holder = run(client, "hold")["job"]
    wait_for(lambda: jobs.get(holder["id"])["state"] == "running")
    queued = run(client, "rec", target="x")["job"]
    assert queued["state"] == "queued"
    path.write_text(json.dumps(command(runner, "--evil", "{target}")))
    with open(gate, "w") as f:
        f.write("go\n")
    assert ended(holder["id"])["state"] == "done"
    job = ended(queued["id"])
    assert job["state"] == "failed" and "changed since it was queued" in job["message"]
    assert runner.runs() == []
    assert any("changed since it was queued" in t for t in log_of(client, queued["id"]))


def test_a_downloaders_command_shares_its_lock_group(client, folder, runner):
    runner.install_as("instaloader")
    write(folder, "mine.json", {"needs": "target", "argv": ["instaloader", "--", "{target}"]})
    job = run(client, "mine", target="carol.cooks")["job"]
    assert job["group"] == "instaloader"
    assert ended(job["id"])["state"] == "done"
    assert runner.runs()[-1]["args"] == ["--", "carol.cooks"]


def test_a_builtin_runs_with_its_tools_found_as_in_settings(client, env, runner):
    runner.install_as("yt-dlp")
    job = run(client, "builtin:yt-dlp-video", url="https://www.youtube.com/watch?v=abc")["job"]
    assert ended(job["id"])["state"] == "done"
    assert runner.runs()[-1]["args"] == ["--write-info-json", "--write-thumbnail", "--no-playlist", "-o",
                                         f"{env['media']}/%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s",
                                         "--", "https://www.youtube.com/watch?v=abc"]


def test_the_declared_folder_is_indexed_after(client, folder, runner, env):
    write(folder, "post.json", command(runner, "--post", "{root}/carol.cooks", needs="none",
                                       rescan="{root}/carol.cooks"))
    job = ended(run(client, "post")["job"]["id"])
    assert job["state"] == "done" and job["rescan"] == str(env["media"] / "carol.cooks")
    assert job["result"]["added"] == 1
    sub = env["media"] / "inbox"
    sub.mkdir()
    job = ended(run(client, "post", folder=str(sub))["job"]["id"])
    # The same post again, in another folder: indexed there (the scanner keeps the first copy's id).
    assert job["state"] == "done" and job["rescan"] == str(sub / "carol.cooks")
    assert (sub / "carol.cooks").is_dir() and job["result"] is not None


def test_a_rescan_folder_outside_the_roots_is_refused(client, folder, runner):
    write(folder, "out.json", command(runner, needs="none", rescan="{data_dir}"))
    assert "not inside a media root" in run(client, "out", status=400)["error"]


def test_a_failing_script_fails_with_its_last_line(client, folder, runner, monkeypatch):
    monkeypatch.setenv("RECORDER_EXIT", "3")
    write(folder, "rec.json", command(runner, needs="none"))
    job = ended(run(client, "rec")["job"]["id"])
    assert job["state"] == "failed" and job["message"] == "recorded 0 arguments"


def test_argv_and_params_are_shown_scrubbed(client, folder, runner):
    url = "https://example.com/a?access_token=SECRETVALUE1234"
    write(folder, "rec.json", command(runner, "{url}", needs="url"))
    job = ended(run(client, "rec", url=url)["job"]["id"])
    assert runner.runs()[-1]["args"] == [url]                  # as given to the program
    assert "SECRETVALUE" not in json.dumps(job)
    assert job["argv"][-1] == "https://example.com/a?access_token=…"
    assert job["params"]["url"] == "https://example.com/a?access_token=…"
    listed_jobs = client.get("/api/jobs", headers=H).get_json()["jobs"]
    assert "SECRETVALUE" not in json.dumps(listed_jobs)
    row = db.connect().execute("SELECT params, argv FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    assert "SECRETVALUE" not in row["params"] + row["argv"]


def test_cancel_stops_a_running_script(client, folder, runner, env):
    gate = env["tmp"] / "gate"
    os.mkfifo(gate)
    write(folder, "hold.sh", f"#!/bin/sh\n# needs: none\nread -r x < {gate}\n", 0o755)
    job = run(client, "hold")["job"]
    wait_for(lambda: jobs.get(job["id"])["state"] == "running" and jobs._active[job["id"]].proc)
    assert client.post(f"/api/jobs/{job['id']}/cancel", headers=H).status_code == 200
    assert ended(job["id"])["state"] == "cancelled"


def test_post_api_jobs_cannot_start_a_script(client, folder, runner):
    write(folder, "rec.json", command(runner, "{target}"))
    for kind in scripts.JOB_KINDS:
        r = client.post("/api/jobs", json={"kind": kind, "params": {"script": "rec", "target": "x"}}, headers=H)
        assert r.status_code == 400
    assert runner.runs() == []


@pytest.mark.parametrize("headers", [{"Origin": "https://www.instagram.com"}, {"Origin": "null"},
                                     {"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://localhost:3380"}])
def test_another_origin_cannot_list_or_run_scripts(client, folder, runner, headers):
    write(folder, "rec.json", command(runner, "{target}"))
    assert client.get("/api/scripts", headers={**H, **headers}).status_code == 403
    assert client.get("/api/scripts/rec", headers={**H, **headers}).status_code == 403
    run(client, "rec", status=403, headers=headers, target="x")
    assert runner.runs() == []
    # Without the header nothing at all, as for every /api request.
    assert client.post("/api/scripts/rec/run", json={"target": "x"}).status_code == 403


def test_the_dashboard_and_the_dev_server_can(client, folder, runner):
    write(folder, "rec.json", command(runner, "{target}"))
    for headers in ({"Origin": "http://localhost:3380", "Sec-Fetch-Site": "same-origin"},
                    {"Origin": "http://localhost:5173"}, {}):
        assert ended(run(client, "rec", headers=headers, target="x")["job"]["id"])["state"] == "done"


# ---------------------------------------------------------------------------
# On a source (step 5)
# ---------------------------------------------------------------------------

INSTA = {"name": "Mine", "needs": "target", "rescan": "{root}",
         "argv": ["instaloader", "--no-videos", "--latest-stamps", "{archive}", "--dirname-pattern", "{root}",
                  "--", "{target}"]}


def add_source(client, target="carol.cooks", headers=None, **options):
    r = client.post("/api/sources", json={"tool": "instaloader", "target": target, "options": options},
                    headers={**H, **(headers or {})})
    return r


def attach(client, sid, script, status=200, headers=None):
    r = client.post(f"/api/sources/{sid}", json={"options": {"script": script}}, headers={**H, **(headers or {})})
    assert r.status_code == status, r.get_json()
    return r.get_json()


def sync_now(client, sid, status=200, headers=None):
    r = client.post(f"/api/sources/{sid}/sync", headers={**H, **(headers or {})})
    assert r.status_code == status, r.get_json()
    return r.get_json()


@pytest.fixture
def source(client, runner, folder):
    runner.install_as("instaloader")
    write(folder, "mine.json", INSTA)
    return add_source(client).get_json()["source"]


def test_a_source_runs_its_script_instead_of_the_command(client, folder, runner, env, source):
    assert source["options"]["script"] is None
    got = attach(client, source["id"], "mine")["source"]
    assert got["options"]["script"] == "mine"
    job = sync_now(client, source["id"])["job"]
    assert job["kind"] == "script-sync" and job["group"] == "instaloader"
    assert job["params"]["script"] == "mine" and job["params"]["target"] == "carol.cooks"
    done = ended(job["id"])
    assert done["state"] == "done", done
    data = env["tmp"] / "data"
    assert runner.runs()[-1]["args"] == ["--no-videos", "--latest-stamps", f"{data}/instaloader/stamps.ini",
                                         "--dirname-pattern", source["folder"], "--", "carol.cooks"]
    assert done["rescan"] == source["folder"] and done["label"] == "Sync carol.cooks with mine"
    s = client.get(f"/api/sources/{source['id']}", headers=H).get_json()
    assert s["last_result"]["state"] == "done" and s["last_job_id"] == job["id"] and s["health"]["state"] == "ok"
    # Detached: the tool's own command again, after instaloader's pause (one group, one pause).
    attach(client, source["id"], None)
    job = sync_now(client, source["id"])["job"]
    assert job["kind"] == "instaloader-sync" and jobs.get(job["id"])["waits_until"] is not None
    jobs._cool.clear()
    jobs._pump()
    job = ended(job["id"])
    assert job["kind"] == "instaloader-sync" and "--no-videos" not in runner.runs()[-1]["args"]


def test_a_source_gives_its_link_as_the_url(client, folder, runner, source, env):
    out = env["tmp"] / "out.txt"
    write(folder, "link.sh", f"#!/bin/sh\n# needs: url\nprintf '%s|%s|%s' \"$FV_URL\" \"$FV_TARGET\" \"$FV_ROOT\" > {out}\n",
          0o755)
    attach(client, source["id"], "link")
    assert ended(sync_now(client, source["id"])["job"]["id"])["state"] == "done"
    assert out.read_text() == f"https://www.instagram.com/carol.cooks/|carol.cooks|{source['folder']}"


def test_a_source_script_failure_is_read_as_the_tools(client, folder, runner, source, monkeypatch):
    attach(client, source["id"], "mine")
    monkeypatch.setenv("RECORDER_EXIT", "1")
    monkeypatch.setenv("RECORDER_SAY", "JSON Query to graphql/query: 429 Too Many Requests [retrying; skip with ^C]")
    job = ended(sync_now(client, source["id"])["job"]["id"])
    assert job["state"] == "failed" and job["result"]["error"] == "rate_limited"
    s = client.get(f"/api/sources/{source['id']}", headers=H).get_json()
    assert s["health"]["state"] == "rate_limited" and s["last_result"]["failures"] == 1


@pytest.mark.parametrize("breakage", ["removed", "refused", "changed-to-bad"])
def test_a_missing_or_refused_script_fails_the_run_never_falls_back(client, folder, runner, source, breakage):
    attach(client, source["id"], "mine")
    path = folder / "mine.json"
    if breakage == "removed":
        path.unlink()
    elif breakage == "refused":
        path.chmod(0o666)
    else:
        path.write_text("{")
    job = ended(sync_now(client, source["id"])["job"]["id"])
    assert job["state"] == "failed" and job["kind"] == "script-sync"
    assert job["message"].startswith("the source's script: ")
    # Why, as it was when the sync was queued.
    why = {"removed": "no script mine", "refused": "writable by group or others", "changed-to-bad": "mine.json is refused"}
    assert why[breakage] in job["message"]
    assert any("the source's script" in t for t in log_of(client, job["id"]))
    assert runner.runs() == []                 # neither the script nor instaloader's own command
    s = client.get(f"/api/sources/{source['id']}", headers=H).get_json()
    assert s["last_result"]["state"] == "failed" and s["last_result"]["message"] == job["message"]
    assert s["health"]["state"] == "error"


def test_a_script_broken_after_its_sync_was_queued_fails_it(client, folder, runner, source, env, monkeypatch):
    gate = env["tmp"] / "gate"
    os.mkfifo(gate)
    monkeypatch.setenv("RECORDER_GATE", str(gate))
    cfg = config.load()                        # no pause between the two instaloader runs
    cfg["instaloader"] = {"pause": 0}
    config.save(cfg)
    attach(client, source["id"], "mine")
    # The instaloader group held by a run that waits on the fifo.
    write(folder, "insta-hold.json", {"needs": "none", "argv": ["instaloader", "--hold"]})
    blocker = run(client, "insta-hold")["job"]
    wait_for(lambda: runner.runs())
    queued = sync_now(client, source["id"])["job"]
    assert jobs.get(queued["id"])["state"] == "queued"
    (folder / "mine.json").write_text(json.dumps({**INSTA, "argv": [*INSTA["argv"], "--extra"]}))
    with open(gate, "w") as f:
        f.write("go\n")
    ended(blocker["id"])
    job = ended(queued["id"])
    assert job["state"] == "failed" and "changed since it was queued" in job["message"]
    assert all("--extra" not in r["args"] for r in runner.runs())


def test_a_script_refused_when_queued_and_fixed_by_its_start_still_fails(client, folder, runner, source, env,
                                                                          monkeypatch):
    """Its params hold no SHA-256 to run: the run fails, says why it was
    refused then and that it can run now. Nothing is built or run for it."""
    gate = env["tmp"] / "gate"
    os.mkfifo(gate)
    monkeypatch.setenv("RECORDER_GATE", str(gate))
    cfg = config.load()
    cfg["instaloader"] = {"pause": 0}
    config.save(cfg)
    attach(client, source["id"], "mine")
    write(folder, "insta-hold.json", {"needs": "none", "argv": ["instaloader", "--hold"]})
    blocker = run(client, "insta-hold")["job"]
    wait_for(lambda: runner.runs())
    (folder / "mine.json").chmod(0o666)
    queued = sync_now(client, source["id"])["job"]
    assert "sha256" not in queued["params"] and "writable by group or others" in queued["params"]["why"]
    (folder / "mine.json").chmod(0o644)
    with open(gate, "w") as f:
        f.write("go\n")
    ended(blocker["id"])
    job = ended(queued["id"])
    assert job["state"] == "failed"
    assert "writable by group or others" in job["message"] and "it can run now: sync again" in job["message"]
    assert len(runner.runs()) == 1             # the blocker only


def test_a_refused_folder_is_the_reason_given(client, folder, runner, source):
    attach(client, source["id"], "mine")
    folder.chmod(0o777)
    try:
        job = ended(sync_now(client, source["id"])["job"]["id"])
    finally:
        folder.chmod(0o755)
    assert job["state"] == "failed" and "the scripts folder is writable by group or others" in job["message"]
    assert "no script" not in job["message"] and runner.runs() == []


def test_attaching_under_a_refused_folder_gives_its_reason(client, folder, runner, source):
    folder.chmod(0o777)
    try:
        error = attach(client, source["id"], "mine", status=400)["error"]
    finally:
        folder.chmod(0o755)
    assert "the scripts folder is writable by group or others" in error and "no script" not in error


def test_a_script_sync_whose_source_is_gone_is_cancelled(client, folder, runner, source, env, monkeypatch):
    gate = env["tmp"] / "gate"
    os.mkfifo(gate)
    monkeypatch.setenv("RECORDER_GATE", str(gate))
    cfg = config.load()
    cfg["instaloader"] = {"pause": 0}
    config.save(cfg)
    attach(client, source["id"], "mine")
    write(folder, "insta-hold.json", {"needs": "none", "argv": ["instaloader", "--hold"]})
    blocker = run(client, "insta-hold")["job"]
    wait_for(lambda: runner.runs())
    queued = sync_now(client, source["id"])["job"]
    # The API refuses while it is queued: gone as when the database is replaced.
    assert sources.delete(db.connect(), source["id"])
    with open(gate, "w") as f:
        f.write("go\n")
    ended(blocker["id"])
    job = ended(queued["id"])
    assert job["state"] == "cancelled" and "no longer exists" in job["message"]
    assert len(runner.runs()) == 1             # the blocker only


def test_a_script_unreadable_at_build_never_runs_its_fallback(client, folder, runner, source, monkeypatch):
    """Readable when its SHA-256 was taken, not when the command is built:
    the job's params lose the SHA-256, so its start fails, whatever the
    file is by then."""
    attach(client, source["id"], "mine")
    params = {"source": str(source["id"]), **scripts.sync_params("mine", sources.row(db.connect(), source["id"]))}
    assert "sha256" in params
    (folder / "mine.json").chmod(0o666)
    spec = scripts._sync_build(params)
    assert "sha256" not in params and "writable by group or others" in params["why"]
    (folder / "mine.json").chmod(0o644)
    with pytest.raises(jobs.BadRequest, match="it can run now"):
        scripts._sync_check(params, lambda text: None)
    assert spec["args"] == []


def test_attaching_checks_the_script(client, folder, runner, source):
    assert "no script nope" in attach(client, source["id"], "nope", status=400)["error"]
    write(folder, "open.json", INSTA, 0o666)
    assert "is refused" in attach(client, source["id"], "open", status=400)["error"]
    assert "script must be" in attach(client, source["id"], "../x", status=400)["error"]
    attach(client, source["id"], "builtin:instaloader-profile")
    r = add_source(client, "dana.draws", script="nope")
    assert r.status_code == 400
    r = add_source(client, "dana.draws", script="mine")
    assert r.status_code == 200 and r.get_json()["source"]["options"]["script"] == "mine"


def test_a_builtin_on_a_source(client, runner, source, env):
    attach(client, source["id"], "builtin:instaloader-profile")
    job = ended(sync_now(client, source["id"])["job"]["id"])
    assert job["state"] == "done"
    assert runner.runs()[-1]["args"][-2:] == ["--", "carol.cooks"]


def test_the_script_is_user_data(client, runner, source, env):
    import userdata
    attach(client, source["id"], "mine")
    userdata.flush()
    rows = json.loads((env["tmp"] / "data" / "userdata" / "sources.json").read_text())["rows"]
    options = rows[0]["options"]
    assert (json.loads(options) if isinstance(options, str) else options)["script"] == "mine"


def test_the_scheduler_runs_the_script(client, runner, source):
    attach(client, source["id"], "mine")
    client.post(f"/api/sources/{source['id']}", json={"options": {"schedule": "hourly"}}, headers=H)
    assert len(scheduler.tick()) == 1
    job = next(j for j in jobs.listing()["jobs"] if j["kind"] == "script-sync")
    assert job["params"]["scheduled"] == "1"
    assert ended(job["id"])["state"] == "done"
    assert "--no-videos" in runner.runs()[-1]["args"]


FOREIGN = [{"Origin": "https://www.instagram.com"}, {"Sec-Fetch-Site": "cross-site"}]


@pytest.mark.parametrize("headers", FOREIGN)
def test_the_userscripts_origin_cannot_run_a_script(client, folder, runner, source, headers):
    """The userscript (instagram.com) can sync and add sources as before,
    and nothing more: no script set, none run through a source."""
    assert add_source(client, "dana.draws", headers=headers, script="mine").status_code == 403
    attach(client, source["id"], "mine", status=403, headers=headers)
    attach(client, source["id"], "mine")
    assert "only FeedVault's own dashboard" in sync_now(client, source["id"], status=403, headers=headers)["error"]
    r = client.post("/api/sources/sync-all", headers={**H, **headers}).get_json()
    assert r["jobs"] == [] and r["errors"][0]["source"] == source["id"]
    assert runner.runs() == [] and jobs.active() == []
    # A source without a script still syncs from there, as before.
    other = add_source(client, "dana.draws", headers=headers).get_json()["source"]
    job = sync_now(client, other["id"], headers=headers)["job"]
    assert job["kind"] == "instaloader-sync"
    ended(job["id"])


def test_sync_all_reads_the_scripts_folder_once(client, folder, runner, source, monkeypatch):
    """#59: once per request, however many sources have a script; each start still checks its SHA-256."""
    cfg = config.load()                        # no pause between the instaloader runs
    cfg["instaloader"] = {"pause": 0}
    config.save(cfg)
    write(folder, "other.json", {**INSTA, "name": "Other"})
    ids = [source["id"]] + [add_source(client, t).get_json()["source"]["id"] for t in ("dana.draws", "eve.eats")]
    for sid, script in zip(ids, ("mine", "other", "mine")):
        attach(client, sid, script)
    reads, files = [], scripts._files
    monkeypatch.setattr(scripts, "_files", lambda: reads.append(1) or files())
    pump, held = jobs._pump, [True]
    monkeypatch.setattr(jobs, "_pump", lambda: None if held[0] else pump())   # no start reads it meanwhile
    r = client.post("/api/sources/sync-all", headers=H).get_json()
    assert len(r["jobs"]) == 3 and r["errors"] == [] and len(reads) == 1
    assert all(j["kind"] == "script-sync" and "sha256" in j["params"] for j in r["jobs"])
    held[0] = False
    jobs._pump()
    for j in r["jobs"]:
        assert ended(j["id"])["state"] == "done"
    assert len(reads) == 4                     # each start read it again (_sync_check)
    # One source's Sync: read once too.
    reads.clear()
    ended(sync_now(client, ids[1])["job"]["id"])
    assert len(reads) == 2                     # queued, then started


def test_a_script_changed_between_sync_all_and_its_start_does_not_run(client, folder, runner, source, monkeypatch):
    """The one read at Sync all gives the SHA-256; the start compares it with the file then."""
    attach(client, source["id"], "mine")
    pump, held = jobs._pump, [True]
    monkeypatch.setattr(jobs, "_pump", lambda: None if held[0] else pump())   # nothing starts yet
    job = client.post("/api/sources/sync-all", headers=H).get_json()["jobs"][0]
    write(folder, "mine.json", {**INSTA, "argv": [*INSTA["argv"], "--extra"]})
    held[0] = False
    jobs._pump()
    done = ended(job["id"])
    assert done["state"] == "failed" and "changed since it was queued" in done["message"]
    assert runner.runs() == []


def test_the_userscript_gains_nothing(client):
    text = open(os.path.join(os.path.dirname(TESTS), "..", "userscript", "feedvault.user.js")).read()
    assert "/api/scripts" not in text and "script-sync" not in text and '"script"' not in text


# ---------------------------------------------------------------------------
# Nothing writes a script
# ---------------------------------------------------------------------------

WRITES = {"os.rename", "os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.symlink",
          "os.link", "os.truncate", "os.utime", "shutil.rmtree", "shutil.move", "shutil.copyfile"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_watch = {"dir": None, "seen": []}


def _audit(event, args):
    folder = _watch["dir"]
    if folder is None or not (event == "open" or event in WRITES):
        return
    if event == "open":
        path, mode, flags = args
        if not (isinstance(mode, str) and set(mode) & set("wax+") or flags & WRITE_FLAGS):
            return
    paths = [a for a in args if isinstance(a, (str, bytes, os.PathLike))]
    for p in paths:
        p = os.fsdecode(p)
        if os.path.realpath(p) == folder or os.path.realpath(p).startswith(folder + os.sep):
            _watch["seen"].append((event, p))


sys.addaudithook(_audit)


def _snapshot(folder):
    return {name: (st.st_mode, st.st_size, st.st_mtime_ns, open(os.path.join(folder, name), "rb").read())
            for name in os.listdir(folder)
            for st in [os.lstat(os.path.join(folder, name))]}


def test_only_the_three_script_routes_exist(client):
    import app as app_module
    got = sorted((r.rule, m) for r in app_module.app.url_map.iter_rules() if r.rule.startswith("/api/scripts")
                 for m in r.methods - {"HEAD", "OPTIONS"})
    assert got == [("/api/scripts", "GET"), ("/api/scripts/<sid>", "GET"), ("/api/scripts/<sid>/run", "POST")]


def test_no_route_writes_under_the_scripts_folder(client, folder, runner, env):
    """Every rule, every method, with bodies naming the scripts folder and
    its files: nothing is created, changed, moved or removed there."""
    import app as app_module
    write(folder, "rec.json", command(runner, "{target}"))
    write(folder, "echo.sh", SHELL, mode=0o755)
    before = _snapshot(folder)
    inside = str(folder / "rec.json")
    junk = {"path": inside, "folder": str(folder), "file": inside, "name": "rec", "id": "rec", "script": "rec",
            "content": "{}", "argv": ["instaloader"], "target": "x", "url": "http://localhost/x",
            "root": str(folder), "dest": inside, "media_roots": [str(env["media"])], "paths": [inside],
            "keys": [inside], "posts": [1], "media": [1], "options": {"script": "rec"}}
    values = {"int": "1", "path": "../scripts/rec.json", "default": "rec"}
    _watch.update(dir=os.path.realpath(folder), seen=[])
    try:
        # The hook sees a write there (then the folder is as before).
        open(folder / "probe.json", "w").close()
        os.remove(folder / "probe.json")
        assert [e for e, _ in _watch["seen"]] == ["open", "os.remove"]
        _watch["seen"] = []
        for rule in app_module.app.url_map.iter_rules():
            if rule.rule in ("/api/quit", "/static/<path:filename>"):
                continue
            url = re.sub(r"<(?:(\w+):)?\w+>", lambda m: values.get(m.group(1), values["default"]), rule.rule)
            for method in rule.methods - {"HEAD", "OPTIONS"}:
                client.open(url, method=method, json=junk, headers=H)
        wait_for(lambda: not jobs.active(), timeout=20)
    finally:
        _watch["dir"] = None
    assert _watch["seen"] == []
    assert _snapshot(folder) == before
