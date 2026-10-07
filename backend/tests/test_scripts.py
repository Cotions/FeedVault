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
    monkeypatch.setattr(scripts, "_ancestors_refused", lambda *a, **k: None)
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
        self.echo = self.bin / "echo"
        self.install_as("echo")

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
    # Its folder stands in for /usr/bin, where an echo is one (scripts.SYSTEM_DIRS).
    monkeypatch.setattr(scripts, "SYSTEM_DIRS", (*scripts.SYSTEM_DIRS, str(rec.bin)))
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


# A program FeedVault does not read the arguments of may not take a
# placeholder (#103), so the recorder runs as one that only ever prints
# them (scripts.DATA_ONLY): a fake echo, as the downloaders are fakes, in
# a folder the runner fixture makes a system one.
def command(rec, *args, needs="target", rescan=None):
    return {"needs": needs, "rescan": rescan, "argv": [str(rec.echo), *args]}


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


LINK = "https://x.com/a/{_env[HOME]}"


@pytest.mark.parametrize("option", [["--print-to-file", "{id}"], ["--Print-to-file", "{id}"],
                                    ["--print-to-file={id}"], ["--Print-to-file={id}"],
                                    ["--print-to", "{id}"], ["--Print-to", "{id}"], ["--print-to-f={id}"]])
def test_a_link_in_a_print_to_file_name_is_escaped(env, option):
    """#66: gallery-dl formats FILE's name (after its last "/"), never its folder."""
    script = {"tool": "gallery-dl", "argv": ["gallery-dl", *option, "/tmp/{url}", "{url}"]}
    vals = scripts.values(script, config.load(), "/m/{x}", url=LINK)
    assert scripts.command(script, vals) == ["gallery-dl", *option, "/tmp/https://x.com/a/{{_env[HOME]}}", LINK]


@pytest.mark.parametrize("option", [["--print-to-file", "{id}-{url}"], ["--Print-to-file", "{id}-{url}"],
                                    ["--print-to-file={id}-{url}"], ["--print-to", "{id}-{url}"]])
def test_a_root_in_a_print_to_file_folder_stays_as_it_is(env, option):
    """The link brings its own "/", which moves the split: what of it comes before stays too."""
    script = {"tool": "gallery-dl", "argv": ["gallery-dl", *option, "{root}/out-{url}.txt", "--", "{url}"]}
    vals = scripts.values(script, config.load(), "/m/{x}", url=LINK)
    *_, fmt, file, dash, link = scripts.command(script, vals)
    assert fmt.endswith("{id}-https://x.com/a/{{_env[HOME]}}")
    assert (file, dash, link) == ("/m/{x}/out-https://x.com/a/{{_env[HOME]}}.txt", "--", LINK)


@pytest.mark.parametrize("file, want", [
    ("{id}-{url}", "{id}-https://x.com/a/{{_env[HOME]}}"),
    ("{url}/{url}", f"{LINK}/https://x.com/a/{{{{_env[HOME]}}}}"),
    ("{root}/{id}.txt", "/m/{x}/{id}.txt"),
    ("{url}/", f"{LINK}/"),
])
def test_only_what_lands_after_the_last_slash_is_escaped(env, file, want):
    script = {"tool": "gallery-dl", "argv": ["gallery-dl", "--print-to-file", "{id}", file]}
    vals = scripts.values(script, config.load(), "/m/{x}", url=LINK)
    assert scripts.command(script, vals)[-1] == want


@pytest.mark.parametrize("argv, want", [
    # Not FILE: one value only, or an option that is not --print-to-file.
    (["--print", "{url}", "{url}"], ["--print", "https://x.com/a/{{_env[HOME]}}", LINK]),
    # argparse refuses an option as FORMAT (nargs=2); read as FORMAT all the
    # same, the item after it as FILE, escaped either way.
    (["--print-to-file", "-f", "{url}", "{url}"], ["--print-to-file", "-f", "https://x.com/a/{{_env[HOME]}}", LINK]),
    (["-f", "{url}", "/tmp/{url}"], ["-f", "https://x.com/a/{{_env[HOME]}}", f"/tmp/{LINK}"]),
    (["--print-to-files", "{url}", "{url}"], ["--print-to-files", LINK, LINK]),
])
def test_only_print_to_file_takes_a_file(env, argv, want):
    script = {"tool": "gallery-dl", "argv": ["gallery-dl", *argv]}
    vals = scripts.values(script, config.load(), "/m/{x}", url=LINK)
    assert scripts.command(script, vals) == ["gallery-dl", *want]


@pytest.mark.parametrize("argv", [
    ["/usr/bin/env", "-i", "gallery-dl", "--print-to-file", "{id}", "/tmp/{url}", "{url}"],
    ["/usr/local/bin/gallery-dl", "--print-to-file", "{id}", "/tmp/{url}", "{url}"],
])
def test_a_downloader_by_its_path_or_behind_env_is_escaped_too(env, argv):
    """As _check_shell reads it (_walk); env's own items are not gallery-dl's."""
    script = {"id": "ids", "tool": argv[0], "argv": argv}
    vals = scripts.values(script, config.load(), "/m/{x}", url=LINK)
    assert scripts.command(script, vals) == [*argv[:-2], "/tmp/https://x.com/a/{{_env[HOME]}}", LINK]
    script = {"tool": "/usr/bin/env", "argv": ["/usr/bin/env", "-u", "-f", "gallery-dl", "-f", "{url}"]}
    assert scripts.command(script, vals) == ["/usr/bin/env", "-u", "-f", "gallery-dl", "-f",
                                             "https://x.com/a/{{_env[HOME]}}"]


@pytest.mark.parametrize("file, vals, why", [
    ("/tmp/{url}/ids.txt", {"url": "https://x.com/$HOME/a"}, "{url} puts a $ in --print-to-file's folder"),
    ("{root}.txt", {"root": "/m/\fE __import__('os').getpid()"}, "{root} puts \\f in --print-to-file's file name"),
])
def test_a_value_gallery_dl_would_expand_in_a_print_to_file_is_refused(env, file, vals, why):
    """Its folder expands $NAME (util.expand_path); \f starts another formatter in its name."""
    script = {"tool": "gallery-dl", "argv": ["gallery-dl", "--print-to-file", "{id}", file]}
    with pytest.raises(jobs.BadRequest, match=re.escape(why)):
        scripts.command(script, {**scripts.values(script, config.load(), "/m/x", url=LINK), **vals})
    # A $ in the file name is text: never expanded there.
    script["argv"][-1] = "/tmp/{url}"
    assert scripts.command(script, scripts.values(script, config.load(), "/m/x", url="https://x.com/$HOME"))[-1] \
        == "/tmp/https://x.com/$HOME"


def test_a_link_gallery_dl_would_expand_is_refused_and_never_run(client, folder, runner):
    runner.install_as("gallery-dl")
    write(folder, "ids.json", {"needs": "url", "argv": ["gallery-dl", "--print-to-file", "{id}",
                                                        "/tmp/{url}/ids.txt", "--", "{url}"]})
    error = run(client, "ids", status=400, url="https://x.com/$HOME")["error"]
    assert error == "{url} puts a $ in --print-to-file's folder, which gallery-dl would expand"
    assert runner.runs() == [] and jobs.active() == []


DOTDOT = "https://x.com/../../etc"
DOTDOT_WHY = "{url} puts a .. in --print-to-file's folder, which would lead out of the folder written there"
DOTDOT_IDS = {"needs": "url", "argv": ["gallery-dl", "--print-to-file", "{id}", "/tmp/{url}/out.txt", "{url}"]}


def test_a_link_s_dotdot_in_a_print_to_file_folder_is_refused_and_never_run(client, folder, runner):
    """#68: the issue's example, through Run. gallery-dl would make
    /tmp/https:/x.com/../../etc its base-directory and append to out.txt there."""
    runner.install_as("gallery-dl")
    write(folder, "ids.json", DOTDOT_IDS)
    assert run(client, "ids", status=400, url=DOTDOT)["error"] == DOTDOT_WHY
    assert runner.runs() == [] and jobs.active() == []


def test_a_source_s_dotdot_link_is_refused_and_never_run(client, folder, runner):
    """#68, through a source's sync. Its link is checked first (sources.parse_url
    refuses a .. segment, sources.json edited by hand too), so that refusal
    comes before command()'s."""
    runner.install_as("gallery-dl")
    write(folder, "ids.json", {**DOTDOT_IDS, "needs": "target",
                               "argv": ["gallery-dl", "--print-to-file", "{id}", "/tmp/{target}/out.txt", "{target}"]})
    src = client.post("/api/sources", json={"target": "https://x.com/someone"}, headers=H).get_json()["source"]
    assert src["tool"] == "gallery-dl"
    attach(client, src["id"], "ids")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE sources SET target = ? WHERE id = ?", (DOTDOT, src["id"]))
    assert "not a profile link" in sync_now(client, src["id"], status=400)["error"]
    assert runner.runs() == [] and jobs.active() == []
    # A link the source check takes as it is: command() refuses it all the same.
    script = scripts.get("ids")
    vals = scripts.values(script, config.load(), "/m/x", DOTDOT, DOTDOT)
    with pytest.raises(jobs.BadRequest, match=re.escape(DOTDOT_WHY.replace("{url}", "{target}"))):
        scripts.command(script, vals)


@pytest.mark.parametrize("file, url, want", [
    # The author's own .. stays.
    ("{root}/../x/out.txt", "https://x.com/a", "/m/x/../x/out.txt"),
    ("/tmp/../{url}/out.txt", "https://x.com/a", "/tmp/../https://x.com/a/out.txt"),
    # A link's .. in the file name only (after the last "/"): no folder, escaped as #67 does.
    ("{root}/{url}", "https://x.com/..", "/m/x/https://x.com/.."),
    ("{root}/{url}", "https://x.com/..{id}", "/m/x/https://x.com/..{{id}}"),
    ("{root}/out-{url}", "https://x.com/a..b/c", "/m/x/out-https://x.com/a..b/c"),
    # gallery-dl never decodes a path (os.path.split, util.expand_path, open):
    # %2e%2e is a folder with that name, not a step up.
    ("/tmp/{url}/out.txt", "https://x.com/%2e%2e/%2E%2E/etc", "/tmp/https://x.com/%2e%2e/%2E%2E/etc/out.txt"),
])
def test_a_print_to_file_folder_the_link_stays_in_is_accepted(env, file, url, want):
    script = {"tool": "gallery-dl", "argv": ["gallery-dl", "--print-to-file", "{id}", file, "{url}"]}
    vals = scripts.values(script, config.load(), "/m/x", url=url)
    assert scripts.command(script, vals)[-2:] == [want, url]


@pytest.mark.parametrize("option", [["--print-to-file", "{id}"], ["--Print-to-file", "{id}"],
                                    ["--print-to-file={id}"], ["--Print-to", "{id}"]])
@pytest.mark.parametrize("file, url", [
    ("/tmp/{url}/out.txt", DOTDOT),
    ("/tmp/{url}/out.txt", "https://x.com/a/.."),
    # Joined with the author's text up to the "/" around it.
    ("/tmp/{url}./out.txt", "https://x.com/a/."),
])
def test_a_link_s_dotdot_in_any_print_to_file_folder_is_refused(env, option, file, url):
    script = {"tool": "gallery-dl", "argv": ["gallery-dl", *option, file, "{url}"]}
    name = "--Print-to-file" if "--P" in option[0] else "--print-to-file"
    with pytest.raises(jobs.BadRequest, match=re.escape(DOTDOT_WHY.replace("--print-to-file", name))):
        scripts.command(script, scripts.values(script, config.load(), "/m/x", url=url))


def test_a_value_starting_a_print_to_file_with_tilde_is_refused(env):
    """gallery-dl runs expanduser on FILE's folder (util.expand_path). A link
    never starts with ~ (check_url, check_target), nor does {root},
    {data_dir} or {archive} (absolute paths): this is the check on its own."""
    script = {"tool": "gallery-dl", "argv": ["gallery-dl", "--print-to-file", "{id}", "{url}/.bashrc"]}
    with pytest.raises(jobs.BadRequest, match=re.escape("{url} starts --print-to-file's folder with ~, "
                                                        "which gallery-dl would expand")):
        scripts.command(script, scripts.values(script, config.load(), "/m/x", url="~"))
    # A ~ further on, or in the file name alone, is never expanded.
    script["argv"][-1] = "/tmp/{url}/x"
    assert scripts.command(script, scripts.values(script, config.load(), "/m/x", url="~a"))[-1] == "/tmp/~a/x"
    script["argv"][-1] = "{url}"
    assert scripts.command(script, scripts.values(script, config.load(), "/m/x", url="~a"))[-1] == "~a"


@pytest.mark.parametrize("tool, args, option", [
    ("gallery-dl", ["-d", "/tmp/{url}"], "-d"),
    ("gallery-dl", ["--destination=/tmp/{url}"], "--destination"),
    ("gallery-dl", ["-D", "/tmp/{url}"], "-D"),
    ("gallery-dl", ["-qD/tmp/{url}"], "-D"),
    ("gallery-dl", ["--directory", "/tmp/{url}"], "--directory"),
    ("gallery-dl", ["--dir=/tmp/{url}"], "--directory"),
    ("gallery-dl", ["--download-archive", "/tmp/{url}/a.sqlite3"], "--download-archive"),
    ("gallery-dl", ["-e", "/tmp/{url}/errors.txt"], "-e"),
    ("gallery-dl", ["--error-file", "/tmp/{url}/errors.txt"], "--error-file"),
    ("gallery-dl", ["--write-log", "/tmp/{url}/log.txt"], "--write-log"),
    ("gallery-dl", ["--write-unsupported=/tmp/{url}/u.txt"], "--write-unsupported"),
    ("gallery-dl", ["-c", "/tmp/{url}/c.json"], "-c"),
    ("gallery-dl", ["--config-json", "/tmp/{url}/c.json"], "--config-json"),
    ("gallery-dl", ["--config-yaml", "/tmp/{url}/c.yaml"], "--config-yaml"),
    ("gallery-dl", ["--config-toml", "/tmp/{url}/c.toml"], "--config-toml"),
    ("gallery-dl", ["-C", "/tmp/{url}/cookies.txt"], "-C"),
    ("gallery-dl", ["--cookies-export", "/tmp/{url}/cookies.txt"], "--cookies-export"),
    ("gallery-dl", ["-i", "/tmp/{url}/in.txt"], "-i"),
    ("gallery-dl", ["-I", "/tmp/{url}/in.txt"], "-I"),
    ("gallery-dl", ["--input-file-delete=/tmp/{url}/in.txt"], "--input-file-delete"),
    ("yt-dlp", ["-P", "/tmp/{url}"], "-P"),
    ("yt-dlp", ["--paths", "temp:/tmp/{url}"], "--paths"),
    ("yt-dlp", ["-o", "/tmp/{url}/%(id)s.%(ext)s"], "-o"),
    ("yt-dlp", ["--output=/tmp/{url}/%(id)s.%(ext)s"], "--output"),
    ("yt-dlp", ["--download-archive", "/tmp/{url}/a.txt"], "--download-archive"),
    ("yt-dlp", ["--cookies", "/tmp/{url}/cookies.txt"], "--cookies"),
    ("yt-dlp", ["-a", "/tmp/{url}/in.txt"], "-a"),
    ("yt-dlp", ["--batch-file=/tmp/{url}/in.txt"], "--batch-file"),
    ("yt-dlp", ["--load-info-json", "/tmp/{url}/i.json"], "--load-info-json"),
    # yt-dlp's optparse takes a unique prefix and flags before a short option too.
    ("yt-dlp", ["--load-info", "/tmp/{url}/i.json"], "--load-info-json"),
    ("yt-dlp", ["--batch=/tmp/{url}/in.txt"], "--batch-file"),
    ("yt-dlp", ["--pat", "/tmp/{url}"], "--paths"),
    ("yt-dlp", ["-iP", "/tmp/{url}"], "-P"),
    ("yt-dlp", ["-qio/tmp/{url}/%(id)s"], "-o"),
    ("yt-dlp", ["--cache-dir", "/tmp/{url}"], "--cache-dir"),
    ("yt-dlp", ["--config-locations", "/tmp/{url}"], "--config-locations"),
    ("yt-dlp", ["--netrc-location", "/tmp/{url}"], "--netrc-location"),
    ("yt-dlp", ["--plugin-dirs", "/tmp/{url}"], "--plugin-dirs"),
    ("yt-dlp", ["--ffmpeg-location", "/tmp/{url}/ffmpeg"], "--ffmpeg-location"),
    # TEMPLATE FILE: the path is the second value.
    ("yt-dlp", ["--print-to-file", "%(id)s", "/tmp/{url}/ids.txt"], "--print-to-file"),
    ("yt-dlp", ["--print-to-file=%(id)s", "/tmp/{url}/ids.txt"], "--print-to-file"),
])
def test_a_link_s_dotdot_in_a_path_option_is_refused(env, tool, args, option):
    """#68: the tool's other options whose value is a path (scripts.TOOLS' PATH),
    expanded and used as it is: a link's .. there leads out of the folder
    written, as in --print-to-file's; its $ is expanded (~ too)."""
    script = {"tool": tool, "argv": [tool, *args, "--", "{url}"]}
    for url, why in [(DOTDOT, f"{{url}} puts a .. in {option}'s path, which would lead out of the folder "
                              "written there"),
                     ("https://x.com/$HOME", f"{{url}} puts a $ in {option}'s path, which {tool} would expand")]:
        with pytest.raises(jobs.BadRequest, match=re.escape(why)):
            scripts.command(script, scripts.values(script, config.load(), "/m/x", url=url))
    # %2e%2e is no step up (neither tool decodes a path); the author's own .. stays.
    vals = scripts.values(script, config.load(), "/m/x", url="https://x.com/%2e%2e/a")
    assert scripts.command(script, vals)[-1] == "https://x.com/%2e%2e/a"
    script["argv"][1:-2] = [a.replace("/tmp/{url}", "{root}/../x") for a in args]
    assert "/m/x/../x" in "".join(scripts.command(script, scripts.values(script, config.load(), "/m/x",
                                                                           url=DOTDOT)))


def test_a_link_s_dotdot_in_a_path_option_is_refused_and_never_run(client, folder, runner):
    runner.install_as("gallery-dl")
    write(folder, "dl.json", {"needs": "url", "argv": ["gallery-dl", "-D", "/tmp/{url}", "--", "{url}"]})
    assert run(client, "dl", status=400, url=DOTDOT)["error"] \
        == "{url} puts a .. in -D's path, which would lead out of the folder written there"
    assert runner.runs() == [] and jobs.active() == []


TEMPLATE_DOTDOT = "https://e.com/%(a|..)s/%(a|..)s/home/u/.bashrc"


@pytest.mark.parametrize("args, want", [
    (["--print-to-file", "%(title)s", "{root}/logs/{url}"], ["--print-to-file", "%(title)s", "/m/x/logs/{}"]),
    (["--print-to", "{url}", "{root}/{url}"], ["--print-to", "{}", "/m/x/{}"]),
    (["--print-to-file={url}", "{root}/{url}"], ["--print-to-file={}", "/m/x/{}"]),
])
def test_a_link_in_yt_dlp_s_print_to_file_is_escaped(env, args, want):
    """Security review of #68: yt-dlp reads --print-to-file's TEMPLATE and FILE as
    output templates (options.py); a missing field's default (%(a|..)s) is filled
    in as it is, so a link's would be a .. no check on the raw text sees."""
    script = {"tool": "yt-dlp", "argv": ["yt-dlp", *args, "--", "{url}"]}
    escaped = TEMPLATE_DOTDOT.replace("%", "%%")
    assert scripts.command(script, scripts.values(script, config.load(), "/m/x", url=TEMPLATE_DOTDOT)) \
        == ["yt-dlp", *[w.replace("{}", escaped) for w in want], "--", TEMPLATE_DOTDOT]


@pytest.mark.parametrize("args, option", [
    (["--dirname-pattern", "{root}/{target}"], "--dirname-pattern"),
    (["--dirname={root}/{target}/x"], "--dirname-pattern"),
    (["--filename-pattern", "{target}/{date_utc}"], "--filename-pattern"),
    (["--title-pattern", "{target}/{date_utc}"], "--title-pattern"),
    (["--resume-prefix", "{root}/{target}/r"], "--resume-prefix"),
    (["--latest-stamps", "{root}/{target}/stamps.ini"], "--latest-stamps"),
    (["-B", "{root}/{target}/cookies.sqlite"], "-B"),
    (["--cookiefile={root}/{target}/cookies.sqlite"], "--cookiefile"),
    (["-Ff", "{root}/{target}/session"], "-f"),
    (["--sessionfile", "{root}/{target}/session"], "--sessionfile"),
])
def test_an_instaloader_target_s_dotdot_in_a_path_option_is_refused(env, args, option):
    """instaloader's target is a profile name, which may be ".." (check_target);
    it sanitizes its own fields in a pattern, never FeedVault's text. No ~ or $
    expansion there: those stay."""
    script = {"tool": "instaloader", "argv": ["instaloader", *args, "--", "{target}"]}
    with pytest.raises(jobs.BadRequest, match=re.escape(f"{{target}} puts a .. in {option}'s path")):
        scripts.command(script, scripts.values(script, config.load(), "/m/x", ".."))
    assert scripts.command(script, scripts.values(script, config.load(), "/m/x", "a..b"))[-1] == "a..b"
    script["argv"][1:-2] = [a.replace("{root}/", "") for a in args]
    assert scripts.command(script, scripts.values(script, config.load(), "/m/x", "~$HOME"))[-1] == "~$HOME"


@pytest.mark.parametrize("argv, option", [
    (["/usr/bin/env", "-C", "/tmp/{url}", "gallery-dl", "{url}"], "-C"),
    (["env", "-i", "-u", "X", "-C/tmp/{url}", "yt-dlp", "{url}"], "-C"),
    (["env", "--chdir=/tmp/{url}", "instaloader", "x"], "--chdir"),
])
def test_a_link_s_dotdot_in_env_s_chdir_is_refused(env, argv, option):
    """The folder the program runs in, its relative paths (gallery-dl's
    ./gallery-dl/, yt-dlp's output) under it."""
    script = {"id": "mine", "tool": argv[0], "argv": argv}
    with pytest.raises(jobs.BadRequest, match=re.escape(f"{{url}} puts a .. in {option}'s path")):
        scripts.command(script, scripts.values(script, config.load(), "/m/x", url=DOTDOT))


@pytest.mark.parametrize("tool, args", [
    # Not a path option: a link's .. is text there, or a positional link.
    ("gallery-dl", ["--filename", "{url}", "{url}"]),
    ("gallery-dl", ["-D", "{root}", "--download-archive", "{archive}", "{url}"]),
    ("gallery-dl", ["--print-to-file", "-D", "/tmp/{root}", "{url}"]),
    ("yt-dlp", ["--paths", "{root}", "-o", "%(id)s.%(ext)s", "--", "{url}"]),
    # yt-dlp's own --print and --netrc: no prefix of --print-to-file or --netrc-location there.
    ("yt-dlp", ["--print", "{url}", "--netrc", "--", "{url}"]),
    ("yt-dlp", ["--print-to-file", "{url}", "{root}/ids.txt", "--", "{url}"]),
    # env's own -u takes the next item, -C as its value.
    ("/usr/bin/env", ["-u", "-C", "gallery-dl", "{url}"]),
])
def test_a_link_s_dotdot_elsewhere_is_left_as_it_is(env, tool, args):
    script = {"id": "mine", "tool": tool, "argv": [tool, *args]}
    vals = scripts.values(script, config.load(), "/m/x", url=DOTDOT)
    assert DOTDOT in scripts.command(script, vals)


def test_built_in_templates_are_filled_in_as_before(env):
    for name in scripts.BUILTINS:
        script = scripts.get("builtin:" + name)
        vals = scripts.values(script, config.load(), "/m/x", "carol", "https://example.com/v")
        assert scripts.command(script, vals) == [scripts.substitute(a, vals) for a in script["argv"]]
        # #68: a link reaches none of their path options, so a .. in it is never refused.
        vals = scripts.values(script, config.load(), "/m/x", DOTDOT, DOTDOT)
        assert scripts.command(script, vals) == [scripts.substitute(a, vals) for a in script["argv"]]


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
    ("/usr/bin/env", ["-u", "B", "A=1", "gallery-dl", "--exec", "echo {url}"], "--exec"),
    ("/usr/bin/nice", ["-n5", "timeout", "60", "yt-dlp", "--exec", "echo {url}"], "--exec"),
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
    # Not a downloader, no placeholder: its options are its own.
    ("/usr/local/bin/other", ["--exec", "x"]),
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
    monkeypatch.setattr(scripts, "_ancestors_refused", lambda *a, **k: None)
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


# #98: a downloader named however the command names it runs in its tool's
# lock group with its pause. Read only: none of these is run.
NAMED = [
    (["/usr/bin/instaloader"], "instaloader"),
    (["/opt/venv/bin/yt-dlp"], "yt-dlp"),
    (["/usr/bin/env", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/env", "-i", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/env", "LANG=C", "instaloader"], "instaloader"),
    (["/usr/bin/env", "-i", "-u", "HOME", "PATH=/opt/bin", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/env", "--", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/env", "./yt-dlp"], "yt-dlp"),
    (["/usr/bin/python3", "-m", "yt_dlp"], "yt-dlp"),
    (["/usr/bin/python3", "-m", "gallery_dl"], "gallery-dl"),
    (["/usr/bin/python3", "-m", "instaloader"], "instaloader"),
    (["/usr/bin/python3", "-m", "yt_dlp.__main__"], "yt-dlp"),
    (["/usr/bin/python3", "-myt_dlp"], "yt-dlp"),
    (["/usr/bin/python3", "-Im", "gallery_dl"], "gallery-dl"),
    (["/usr/bin/python3", "-X", "utf8", "-Wignore", "-u", "-m", "yt_dlp"], "yt-dlp"),
    (["/usr/bin/python3", "--check-hash-based-pycs", "never", "-m", "gallery_dl"], "gallery-dl"),
    (["/opt/venv/bin/python", "-m", "yt_dlp"], "yt-dlp"),
    (["/usr/bin/python3.12", "/opt/venv/bin/yt-dlp"], "yt-dlp"),
    (["/usr/bin/python3", "--", "/opt/venv/bin/gallery-dl"], "gallery-dl"),
    (["/usr/bin/env", "python3", "-m", "gallery_dl"], "gallery-dl"),
    (["/usr/bin/env", "-i", "PYTHONUTF8=1", "python3", "-u", "-m", "instaloader"], "instaloader"),
    (["/usr/bin/python3", "-m", "runpy", "yt_dlp"], "yt-dlp"),
    (["/usr/bin/python3", "/usr/lib/python3/dist-packages/yt_dlp/__main__.py"], "yt-dlp"),
    (["/usr/bin/python3", "/opt/src/gallery_dl"], "gallery-dl"),
    (["/usr/bin/python3", "/opt/src/gallery_dl/"], "gallery-dl"),
]
NOT_DOWNLOADERS = [
    ["/usr/bin/echo", "yt-dlp"],
    ["/usr/bin/env", "true", "yt-dlp"],
    ["/usr/bin/python3", "-c", "import yt_dlp"],
    ["/usr/bin/python3", "-m", "json.tool"],
    ["/usr/bin/python3", "-m", "yt_dlpx"],
    ["/usr/bin/python3", "--version", "-m", "yt_dlp"],
    ["/usr/bin/python3", "/opt/bin/other.py", "yt-dlp"],
    ["/usr/bin/python3", "-m", "runpy", "json.tool"],
    ["/usr/bin/python3", "/opt/src/other/__main__.py"],
    ["/usr/bin/env", "-S", "yt-dlp --version"],          # env -S: its text is not read (see API.md)
    # env reads no option after a variable: this runs a program named "-u" (env.c).
    ["/usr/bin/env", "-i", "PATH=/opt/bin", "-u", "HOME", "gallery-dl"],
]
PAUSES = {"instaloader": 41, "gallery-dl": 42, "yt-dlp": 43}


@pytest.fixture
def pauses(env):
    cfg = config.load()
    for tool, seconds in PAUSES.items():
        cfg[tool] = {"pause": seconds}
    config.save(cfg)


@pytest.mark.parametrize("argv, tool", NAMED)
def test_a_downloader_however_named_gets_its_lock_group_and_pause(client, folder, pauses, argv, tool):
    write(folder, "named.json", {"needs": "none", "argv": [*argv, "--version"]})
    script = scripts.get("named")
    assert script["refused"] is None
    assert scripts.program(script) == tool
    assert scripts.group(script) == tool
    assert scripts._pause({"script": "named"}) == PAUSES[tool]


@pytest.mark.parametrize("argv", NOT_DOWNLOADERS)
def test_another_program_stays_in_the_scripts_group_with_no_pause(client, folder, pauses, argv):
    write(folder, "other.json", {"needs": "none", "argv": argv})
    script = scripts.get("other")
    assert script["refused"] is None
    assert scripts.group(script) == "scripts" and scripts._pause({"script": "other"}) == 0
    write(folder, "echo.sh", SHELL, 0o755)
    assert scripts.group(scripts.get("echo")) == "scripts" and scripts._pause({"script": "echo"}) == 0


@pytest.mark.parametrize("argv", [
    ["/usr/bin/python3", "-m", "yt_dlp"],
    ["/usr/bin/env", "-i", "python3", "-Im", "yt_dlp"],
    ["/usr/bin/python3", "/opt/venv/bin/yt-dlp"],
    ["/usr/bin/python3", "-m", "runpy", "yt_dlp"],
    ["/usr/bin/python3", "/opt/src/yt_dlp/__main__.py"],
])
def test_a_downloader_run_by_python_gets_the_same_checks(client, folder, argv):
    """Its options are read as the tool's: a placeholder in --exec is refused,
    as for yt-dlp by name (before #98, Python hid them)."""
    write(folder, "notify.json", {"needs": "url", "argv": [*argv, "--exec", "echo {url}", "--", "{url}"]})
    assert listed(client)["notify"]["refused"].startswith("--exec's value can reach a shell")
    script = {"kind": "command", "tool": argv[0], "argv": [*argv, "-o", "{root}/%(id)s", "--", "{url}"]}
    vals = {"target": "", "url": "https://e.com/100%", "root": "/m/50%", "data_dir": "/d", "archive": "/a"}
    assert scripts.command(script, vals)[-4:] == ["-o", "/m/50%%/%(id)s", "--", "https://e.com/100%"]


def test_a_symlink_to_a_downloader_runs_in_its_lock_group(client, folder, runner, pauses, env):
    runner.install_as("yt-dlp")
    link = env["tmp"] / "links" / "ytdl"
    link.parent.mkdir()
    link.symlink_to(runner.bin / "yt-dlp")
    write(folder, "linked.json", {"needs": "url", "argv": [str(link), "--", "{url}"]})
    assert scripts._pause({"script": "linked"}) == PAUSES["yt-dlp"]
    job = run(client, "linked", url="https://www.youtube.com/watch?v=abc")["job"]
    assert job["group"] == "yt-dlp"
    assert ended(job["id"])["state"] == "done"
    assert runner.runs()[-1]["args"] == ["--", "https://www.youtube.com/watch?v=abc"]
    # Through a chain of links whose last file has another name (a snap's launcher):
    # the first known name along it.
    (env["tmp"] / "links" / "snap").write_text("")
    snap_yt = env["tmp"] / "links" / "yt-dlp"
    snap_yt.symlink_to(env["tmp"] / "links" / "snap")
    chained = env["tmp"] / "links" / "yt"
    chained.symlink_to(snap_yt)
    write(folder, "chained.json", {"needs": "none", "argv": [str(chained), "--version"]})
    assert scripts.group(scripts.get("chained")) == "yt-dlp"
    # A target is checked as yt-dlp's too: a link.
    write(folder, "linked-t.json", {"needs": "target", "argv": [str(link), "--", "{target}"]})
    assert "an http(s) link for yt-dlp" in run(client, "linked-t", status=400, target="carol")["error"]


def test_a_downloader_by_absolute_path_runs_in_its_lock_group(client, folder, runner):
    runner.install_as("instaloader")
    write(folder, "abs.json", {"needs": "target", "argv": [str(runner.bin / "instaloader"), "--", "{target}"]})
    job = run(client, "abs", target="carol.cooks")["job"]
    assert job["group"] == "instaloader"
    assert ended(job["id"])["state"] == "done"
    assert runner.runs()[-1]["args"] == ["--", "carol.cooks"]


# ---------------------------------------------------------------------------
# #103: a placeholder only where FeedVault can tell it stays data. Every
# program on the way to the one that reads the arguments is one it parses,
# and that one a downloader, a shell or a data-only program. Parsing only:
# nothing here is run.
# ---------------------------------------------------------------------------

def why_refused(argv):
    used = scripts._used(argv)
    needs = "url" if "url" in used else "target" if "target" in used else "none"
    return scripts.parse_command(json.dumps({"needs": needs, "argv": argv}))[1]


# A launcher (coreutils' env, nice, nohup, timeout, stdbuf; util-linux's
# ionice, taskset) is seen through, with and without its options:
# the downloader it runs gets its checks, its lock group and its pause.
LAUNCHED = [
    (["/usr/bin/nice", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/nice", "-n", "5", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/nice", "-n5", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/nice", "-10", "instaloader"], "instaloader"),
    (["/usr/bin/nice", "--5", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/nice", "-+5", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/nice", "--adjustment=5", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/nice", "--adj", "5", "--", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/nohup", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/nohup", "--", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/timeout", "60", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/timeout", "-k", "5", "-s", "TERM", "1h", "instaloader"], "instaloader"),
    (["/usr/bin/timeout", "-vk5", "--signal=INT", "--foreground", "--preserve-status", "60", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/timeout", "--kill", "5", "--", "60", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/stdbuf", "-oL", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/stdbuf", "-o", "L", "-e0", "--input=0", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/env", "nice", "yt-dlp"], "yt-dlp"),      # its bare name, as the job's PATH finds it
    (["/usr/local/bin/timeout", "5", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/ionice", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/ionice", "-c3", "-t", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/ionice", "-c", "2", "-n", "7", "--", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/ionice", "--class", "idle", "instaloader"], "instaloader"),
    (["/usr/bin/taskset", "0x3", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/taskset", "-c", "0-3", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/taskset", "--cpu-list", "--", "1", "instaloader"], "instaloader"),
    (["/usr/bin/env", "-", "LANG=C", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/env", "--ignore-environment", "--unset=HOME", "--block-signal=INT", "yt-dlp"], "yt-dlp"),
    # One in another, any order, any depth.
    (["/usr/bin/nice", "timeout", "60", "env", "X=1", "yt-dlp"], "yt-dlp"),
    (["/usr/bin/env", "-i", "nice", "-n", "19", "ionice", "-c3", "stdbuf", "-oL", "taskset", "-c", "0", "nohup",
      "timeout", "-s", "KILL", "2h", "gallery-dl"], "gallery-dl"),
    (["/usr/bin/timeout", "60", "/usr/bin/nice", "/usr/bin/python3", "-m", "yt_dlp"], "yt-dlp"),
    (["/usr/bin/nice", "/opt/venv/bin/instaloader"], "instaloader"),
]


@pytest.mark.parametrize("argv, tool", LAUNCHED)
def test_a_launcher_is_seen_through_to_the_downloader_it_runs(client, folder, pauses, argv, tool):
    arg = "{target}" if tool == "instaloader" else "{url}"
    write(folder, "launched.json", {"needs": arg[1:-1], "argv": [*argv, "--", arg]})
    script = scripts.get("launched")
    assert script["refused"] is None
    assert scripts.program(script) == tool and scripts.group(script) == tool
    assert scripts._pause({"script": "launched"}) == PAUSES[tool]
    # The downloader's own checks apply behind it: code in its options.
    code = "--post-filter" if tool == "instaloader" else "--exec"
    assert why_refused([*argv, code, "x == {url}", "--", "{url}"]).startswith(f"{code}'s value can reach ")


@pytest.mark.parametrize("argv, why", [
    # Its options as given: one it does not have, a value missing.
    (["/usr/bin/nice", "-x", "yt-dlp", "{url}"], "nice reads '-x' in a way FeedVault does not follow"),
    (["/usr/bin/timeout", "--verbose=1", "60", "yt-dlp", "{url}"], "timeout reads '--verbose=1'"),
    (["/usr/bin/nice", "-{url}", "yt-dlp"], "nice reads '-{url}'"),
    (["/usr/bin/nohup", "--fork", "yt-dlp", "{url}"], "nohup reads '--fork'"),
    (["/usr/bin/stdbuf", "--", "{url}"], "a placeholder may not name the program to run"),
    (["/usr/bin/env", "--ig", "yt-dlp", "{url}"], "env reads '--ig'"),    # --ignore-environment or -signal?
    # It runs no program: --help, --version, a pid's options.
    (["/usr/bin/nice", "--help", "yt-dlp", "{url}"], "nice --help runs no program"),
    (["/usr/bin/timeout", "--vers", "60", "yt-dlp", "{url}"], "timeout --vers runs no program"),
    (["/usr/bin/ionice", "-c3", "-p", "1", "{url}"], "ionice -p runs no program"),
    (["/usr/bin/ionice", "--pid=1", "{url}"], "ionice --pid=1 runs no program"),
    (["/usr/bin/taskset", "-p", "0x1", "{url}"], "taskset -p runs no program"),
    (["/usr/bin/taskset", "-V", "{url}"], "taskset -V runs no program"),
    (["/usr/bin/env", "X={url}"], "env runs no program here"),
    (["/usr/bin/timeout", "60"], None),                                           # nothing to refuse: no placeholder
])
def test_a_launcher_read_otherwise_is_refused_with_a_placeholder(argv, why):
    error = why_refused(argv)
    assert (error is None) if why is None else why in error


@pytest.mark.parametrize("argv, item, launcher", [
    (["/usr/bin/nice", "-n", "{target}", "yt-dlp", "{url}"], "{target}", "nice"),
    (["/usr/bin/timeout", "{target}", "yt-dlp", "{url}"], "{target}", "timeout"),
    (["/usr/bin/timeout", "-s", "{target}", "60", "yt-dlp", "{url}"], "{target}", "timeout"),
    (["/usr/bin/timeout", "--kil={target}", "60", "yt-dlp", "{url}"], "--kil={target}", "timeout"),
    (["/usr/bin/stdbuf", "-o{target}", "yt-dlp", "{url}"], "-o{target}", "stdbuf"),
    (["/usr/bin/ionice", "--class={target}", "yt-dlp", "{url}"], "--class={target}", "ionice"),
    (["/usr/bin/taskset", "{target}", "yt-dlp", "{url}"], "{target}", "taskset"),
    (["/usr/bin/env", "-u", "{target}", "yt-dlp", "{url}"], "{target}", "env"),
    (["/usr/bin/env", "-a", "{target}", "yt-dlp", "{url}"], "{target}", "env"),
    # A variable can be code to the program (LD_PRELOAD, BASH_ENV, BASH_FUNC_x%%).
    (["/usr/bin/env", "X={target}", "yt-dlp", "{url}"], "X={target}", "env"),
    (["/usr/bin/env", "BASH_ENV={root}/x", "/bin/bash", "-c", "true"], "BASH_ENV={root}/x", "env"),
    (["/usr/bin/nice", "env", "LD_PRELOAD={archive}", "yt-dlp"], "LD_PRELOAD={archive}", "env"),
    # The folder it runs in decides what relative names, configs (yt-dlp.conf) and modules load.
    (["/usr/bin/env", "-C", "{root}", "yt-dlp", "{url}"], "{root}", "env"),
    (["/usr/bin/env", "-iC{target}", "/bin/sh", "fetch.sh"], "-iC{target}", "env"),
    (["/usr/bin/nice", "env", "--chdir={root}/x", "gallery-dl", "{url}"], "--chdir={root}/x", "env"),
])
def test_a_placeholder_among_a_launchers_items_is_refused(argv, item, launcher):
    error = why_refused([a.replace("{target}", "{url}") for a in argv])
    item = item.replace("{target}", "{url}")
    assert error.startswith(f"{item!r}: a placeholder may not be among {launcher}'s options or operands")


def test_env_s_chdir_is_still_checked_as_a_path_when_filled_in(env):
    """A command with a placeholder there is refused; command() still keeps
    a link's .. out of it (as before #103)."""
    script = {"kind": "command", "tool": "/usr/bin/nice", "argv": ["/usr/bin/nice", "env", "-C", "/tmp/{url}",
                                                                   "yt-dlp", "{url}"]}
    vals = {"target": "", "url": "https://x.com/../../etc", "root": "/m", "data_dir": "/d", "archive": "/a"}
    with pytest.raises(jobs.BadRequest, match="-C's path"):
        scripts.command(script, vals)


@pytest.mark.parametrize("argv", [
    ["/usr/bin/xargs", "yt-dlp", "{url}"],
    ["/usr/bin/sudo", "yt-dlp", "{url}"],
    ["/usr/bin/doas", "yt-dlp", "{url}"],
    ["/usr/bin/su", "-c", "yt-dlp", "me", "{url}"],
    ["/usr/sbin/runuser", "-u", "me", "--", "yt-dlp", "{url}"],
    ["/usr/bin/ssh", "host", "yt-dlp", "{url}"],
    ["/usr/bin/watch", "yt-dlp", "{url}"],
    ["/usr/bin/script", "-c", "yt-dlp", "{url}"],
    ["/usr/bin/parallel", "yt-dlp", ":::", "{url}"],
    ["/usr/bin/find", "{root}", "-exec", "yt-dlp", "{}", ";"],
    ["/usr/bin/chrt", "-o", "0", "yt-dlp", "{url}"],
    ["/usr/bin/flock", "/tmp/lock", "yt-dlp", "{url}"],
    ["/usr/bin/flock", "/tmp/lock", "-c", "yt-dlp {url}"],
    # A job leads its process group: setsid forks and exits, the program out of the job's reach.
    ["/usr/bin/setsid", "yt-dlp", "{url}"],
    ["/usr/bin/setsid", "-w", "yt-dlp", "{url}"],
    # Behind a launcher seen through, too.
    ["/usr/bin/nice", "xargs", "yt-dlp", "{url}"],
    ["/usr/bin/env", "-i", "sudo", "-u", "me", "yt-dlp", "{url}"],
])
def test_a_runner_feedvault_does_not_follow_is_refused_with_a_placeholder(argv):
    name = next(os.path.basename(a) for a in argv if os.path.basename(a) in scripts.RUNNERS)
    assert why_refused(argv).startswith(f"{name} runs a command FeedVault does not follow")
    # Without a placeholder it is the user's own command, as before.
    plain = [re.sub(r"\{(url|root)\}", "x", a) for a in argv]
    assert why_refused(plain) is None


@pytest.mark.parametrize("argv, name", [
    (["/usr/bin/python3", "-c", "{url}"], "python3"),
    (["/usr/bin/python3", "-c", "import sys; print(sys.argv[1])", "{url}"], "python3"),
    (["/usr/bin/python3", "/home/me/fetch.py", "{url}"], "python3"),
    (["/usr/bin/python3", "-m", "json.tool", "{url}"], "python3"),
    (["/usr/bin/env", "python3", "-I", "-c", "x", "{url}"], "python3"),
    (["/usr/bin/perl", "-e", "print 1", "{url}"], "perl"),
    (["/usr/bin/perl", "{url}"], "perl"),
    (["/usr/bin/ruby", "-e", "{url}"], "ruby"),
    (["/usr/bin/node", "-e", "x", "{url}"], "node"),
    (["/usr/bin/awk", "{url}"], "awk"),
    (["/usr/bin/gawk", "-f", "/x.awk", "{url}"], "gawk"),
    (["/usr/bin/php", "-r", "{url}"], "php"),
    (["/usr/bin/lua", "-e", "x", "{url}"], "lua"),
    (["/usr/bin/Rscript", "-e", "x", "{url}"], "Rscript"),
    (["/usr/bin/fish", "-c", "echo $argv", "{url}"], "fish"),
    (["/usr/local/bin/mytool", "--url", "{url}"], "mytool"),
    (["/usr/bin/env", "./mytool", "{url}"], "mytool"),
    (["/usr/bin/nice", "/home/me/bin/wrap.sh", "{url}"], "wrap.sh"),
    (["/usr/bin/timeout", "60", "../bin/x", "{root}"], "x"),
    # A program of the user's under a launcher's or echo's name is no launcher, nor echo.
    (["/home/me/bin/echo", "{url}"], "echo"),
    (["/usr/bin/env", "./printf", "%s", "{url}"], "printf"),
    (["/home/me/bin/nice", "yt-dlp", "{url}"], "nice"),
    (["/usr/bin/env", "/opt/bin/timeout", "60", "yt-dlp", "{url}"], "timeout"),
])
def test_an_interpreter_or_an_unknown_program_is_refused_with_a_placeholder(argv, name):
    error = why_refused(argv)
    assert error.startswith(f"{name} ") and "use a shell script instead (its inputs are FV_* variables" in error
    assert why_refused([re.sub(r"\{(url|root)\}", "x", a) for a in argv]) is None


@pytest.mark.parametrize("argv, item", [
    (["/usr/bin/env", "{url}"], "{url}"),
    (["/usr/bin/env", "-i", "X=1", "{root}/yt-dlp", "{url}"], "{root}/yt-dlp"),
    (["/usr/bin/nice", "{root}/bin/yt-dlp", "{url}"], "{root}/bin/yt-dlp"),
    (["/usr/bin/timeout", "60", "{target}"], "{target}"),
    (["/usr/bin/nohup", "--", "{data_dir}/x"], "{data_dir}/x"),
])
def test_a_placeholder_never_names_the_program(argv, item):
    assert why_refused(argv) == f"{item!r}: a placeholder may not name the program to run"


def test_a_placeholder_as_argv0_is_refused():
    for argv0 in ("{root}/yt-dlp", "{target}", "/opt/{data_dir}/yt-dlp"):
        assert why_refused([argv0, "{url}"]).startswith("argv[0] must be one of")


@pytest.mark.parametrize("argv", [
    ["/usr/bin/python3", "-W", "{url}", "-m", "yt_dlp", "{url}"],
    ["/usr/bin/python3", "-X{root}", "-m", "yt_dlp"],
    ["/usr/bin/python3", "-m", "runpy", "{url}"],
    ["/usr/bin/python3", "{root}/yt-dlp", "{url}"],
])
def test_a_placeholder_among_pythons_options_or_naming_what_it_runs_is_refused(argv):
    error = why_refused(argv)
    assert "a placeholder may not be among Python's options" in error or error.startswith("python3 runs Python code")


@pytest.mark.parametrize("argv, why", [
    (["/bin/sh", "-c", 'yt-dlp -- "$1"', "sh", "{url}"], None),
    (["/bin/bash", "-euc", 'gallery-dl -D "$2" -- "$1"', "bash", "{url}", "{root}"], None),
    (["/usr/bin/nice", "timeout", "1h", "/bin/sh", "-c", 'exec yt-dlp "$@"', "sh", "{url}"], None),
    (["/bin/sh", "-s", "{url}"], None),                       # commands from stdin; the rest its arguments
    (["/bin/bash", "/home/me/fetch.sh", "{url}"], None),
    (["/bin/sh", "{root}/fetch.sh"], "a shell runs the file its first argument names"),
    (["/bin/bash", "-e", "{url}"], "a shell runs the file its first argument names"),
    (["/bin/sh", "-c", "yt-dlp {url}"], "a shell's -c text is read as code"),
    # mksh -T takes a value: what follows it is not the script, so the item after is.
    (["/bin/mksh", "-T", "x", "{url}"], "a shell runs the file its first argument names"),
    (["/bin/ksh", "-R", "x", "-c", "{url}"], "a shell's -c text is read as code"),
    (["/bin/zsh", "--emulate", "{url}", "-c", "x"], "among a shell's options"),
    # bash's and zsh's -T is a flag (functrace): what follows is the file.
    (["/bin/bash", "-eT", "/home/me/fetch.sh", "{url}"], None),
    (["/bin/sh", "-T", "x", "{url}"], "a shell runs the file its first argument names"),     # sh may be mksh
])
def test_a_shell_keeps_its_checks_and_its_file_is_never_a_placeholder(argv, why):
    error = why_refused(argv)
    assert (error is None) if why is None else why in error


@pytest.mark.parametrize("argv, why", [
    (["/usr/bin/echo", "{url}"], None),
    (["/bin/echo", "-n", "done:", "{url}", "{root}"], None),
    (["/usr/bin/nice", "echo", "{target}"], None),
    (["/usr/bin/printf", "%s\\n", "{url}"], None),
    (["/usr/bin/printf", "--", "got %s in %s\\n", "{url}", "{root}"], None),
    (["/usr/bin/printf", "{url}"], "printf's format may not hold"),
    (["/usr/bin/printf", "--", "%s{url}"], "printf's format may not hold"),
])
def test_a_data_only_program_takes_a_placeholder_as_data(argv, why):
    error = why_refused(argv)
    assert (error is None) if why is None else why in error


# #105: a shell or Python counts only by its bare name or by a path in
# SYSTEM_DIRS (as a launcher), its symlinks ending at one of the same kind.
# A file of the user's named sh or python3 elsewhere may be anything.
NOT_SYSTEM = "is not a program FeedVault reads the arguments of: a shell or Python counts only by its bare name"


@pytest.mark.parametrize("argv, item", [
    (["/home/x/bin/sh", "-c", "x", "{url}"], "/home/x/bin/sh"),
    (["/home/x/bin/sh", "-c", 'yt-dlp -- "$1"', "sh", "{url}"], "/home/x/bin/sh"),
    (["/usr/bin/env", "/home/x/bin/bash", "-s", "{url}"], "/home/x/bin/bash"),
    (["/usr/bin/nice", "/opt/bin/dash", "/home/me/fetch.sh", "{url}"], "/opt/bin/dash"),
    (["/home/x/bin/python3", "-m", "yt_dlp", "{url}"], "/home/x/bin/python3"),
    (["/opt/venv/bin/python", "-m", "gallery_dl", "--", "{url}"], "/opt/venv/bin/python"),
    (["/usr/bin/env", "-i", "/home/x/bin/pypy3", "-m", "yt_dlp", "{url}"], "/home/x/bin/pypy3"),
    (["/usr/bin/env", "./sh", "-s", "{url}"], "./sh"),
    # A system folder only as normpath reads it: past a link, the kernel goes elsewhere.
    (["/home/x/link/../../../usr/bin/sh", "-s", "{url}"], "/home/x/link/../../../usr/bin/sh"),
])
def test_a_shell_or_python_outside_the_system_folders_is_refused_with_a_placeholder(argv, item):
    assert why_refused(argv).startswith(f"{item} {NOT_SYSTEM}")
    # Without a placeholder it is the user's own command, as before.
    assert why_refused([re.sub(r"\{(url|root)\}", "x", a) for a in argv]) is None


def test_a_dotdot_path_is_no_launcher_nor_echo(client, folder):
    for argv in (["/home/x/link/../../../usr/bin/env", "yt-dlp", "{url}"], ["/home/x/l/../../../bin/echo", "{url}"],
                 ["/usr/bin/../bin/nice", "yt-dlp", "{url}"]):
        assert " is not a program FeedVault reads the arguments of" in why_refused(argv)
    # With no placeholder it is accepted, and its lock group still the downloader's.
    write(folder, "dl.json", {"needs": "none", "argv": ["/usr/bin/../bin/env", "yt-dlp", "--version"]})
    assert scripts.group(scripts.get("dl")) == "yt-dlp"


@pytest.fixture
def system(env, monkeypatch):
    """A folder that stands in for /usr/bin (scripts.SYSTEM_DIRS) and one of
    the user's, holding empty fake programs: nothing in them is run."""
    sysdir, home = env["tmp"] / "sys", env["tmp"] / "home-bin"
    for d in (sysdir, home):
        d.mkdir()
        for name in ("perl", "dash", "bash", "zsh", "ksh93", "python3.12", "python3.13t", "busybox", "yt-dlp"):
            (d / name).write_text("")
    monkeypatch.setattr(scripts, "SYSTEM_DIRS", (*scripts.SYSTEM_DIRS, str(sysdir)))
    return collections.namedtuple("System", "sys home")(sysdir, home)


def test_a_fake_shell_or_python_of_the_users_is_refused(system):
    for name in ("sh", "bash", "python3"):
        (system.home / name).write_text("")
    assert why_refused([str(system.home / "sh"), "-c", "x", "{url}"]).startswith(f"{system.home / 'sh'} {NOT_SYSTEM}")
    assert why_refused(["/usr/bin/env", str(system.home / "bash"), "-s", "{url}"]) \
        .startswith(f"{system.home / 'bash'} {NOT_SYSTEM}")
    assert why_refused([str(system.home / "python3"), "-m", "yt_dlp", "{url}"]) \
        .startswith(f"{system.home / 'python3'} {NOT_SYSTEM}")
    # A link of the user's whose name is unknown, to a system shell: still the user's path.
    (system.home / "mysh").symlink_to("/bin/sh")
    assert why_refused([str(system.home / "mysh"), "-s", "{url}"]).startswith(f"{system.home / 'mysh'} {NOT_SYSTEM}")


# A shell or Python in a system folder is followed through its symlinks
# even though its own name is a known one: it is trusted only when the
# file they end at is one of the same kind (/bin/sh to dash, /usr/bin/python3
# to python3.12). Downloaders (#98) and launchers are not affected.
@pytest.mark.parametrize("link, to, argv, refused", [
    ("sh", "perl", ["-c", 'x "$1"', "sh", "{url}"], True),
    ("sh", "../home-bin/perl", ["-s", "{url}"], True),
    ("mksh", "busybox", ["-s", "{url}"], True),            # busybox picks its applet by argv[0]: mksh is none
    ("ksh", "perl", ["-s", "{url}"], True),
    ("python3", "perl", ["-m", "yt_dlp", "{url}"], True),
    ("sh", "dash", ["-c", 'x "$1"', "sh", "{url}"], False),
    ("sh", "../home-bin/bash", ["-s", "{url}"], False),   # a system folder's link is root's choice
    ("python3", "python3.12", ["-m", "yt_dlp", "{url}"], False),
    ("python3", "python3.13t", ["-m", "yt_dlp", "{url}"], False),  # free-threaded
    ("ksh", "ksh93", ["-c", 'x "$1"', "ksh", "{url}"], False),       # Debian's alternatives
    ("sh", "busybox", ["-s", "{url}"], False),             # Alpine: busybox runs as ash for argv[0] sh
])
def test_a_system_shell_or_python_is_followed_through_its_symlinks(system, link, to, argv, refused):
    (system.sys / link).symlink_to(to)
    error = why_refused([str(system.sys / link), *argv])
    assert error.startswith(f"{system.sys / link} {NOT_SYSTEM}") if refused else error is None


def test_a_system_shell_linked_to_another_is_read_with_both_grammars(system):
    """bash -O takes the next item; dash's -O is a flag: a dash linked to
    bash reads `-O x {url}` as bash does, running {url} as its file."""
    argv = [str(system.sys / "dash"), "-O", "x", "{url}"]
    assert why_refused(argv) is None                       # dash alone: -O a flag, x its file
    (system.sys / "dash").unlink()
    (system.sys / "dash").symlink_to("bash")
    assert why_refused(argv).startswith("'{url}': a shell runs the file its first argument names")


def test_a_downloader_symlink_is_not_checked_as_a_shell(system, folder):
    """#98: a downloader by any path and through symlinks keeps its tool's
    lock group and checks, wherever it is."""
    (system.home / "ytdl").symlink_to(system.home / "yt-dlp")
    for item in (system.home / "yt-dlp", system.home / "ytdl", "/home/x/bin/yt-dlp"):
        write(folder, "dl.json", {"needs": "url", "argv": [str(item), "--", "{url}"]})
        script = scripts.get("dl")
        assert script["refused"] is None and scripts.group(script) == "yt-dlp"


@pytest.mark.parametrize("argv, group", [
    (["/bin/sh", "-c", 'yt-dlp -- "$1"', "sh", "{url}"], "scripts"),
    (["/usr/bin/env", "python3", "-m", "yt_dlp", "{url}"], "yt-dlp"),
    (["/usr/bin/python3", "-m", "gallery_dl", "{url}"], "gallery-dl"),
    (["/usr/bin/env", "bash", "-s", "{url}"], "scripts"),
    (["/home/x/bin/yt-dlp", "--", "{url}"], "yt-dlp"),
    (["/home/x/bin/gallery-dl", "{url}"], "gallery-dl"),
])
def test_a_shell_python_or_downloader_feedvault_reads_stays_accepted(client, folder, argv, group):
    write(folder, "ok.json", {"needs": "url", "argv": argv})
    script = scripts.get("ok")
    assert script["refused"] is None
    assert scripts.group(script) == group


def test_a_saved_path_named_sh_is_listed_never_run_and_can_be_fixed(client, folder, env, runner):
    fake = env["tmp"] / "mine-bin" / "sh"
    fake.parent.mkdir()
    fake.write_text("")
    saved = {"name": "Mine", "needs": "url", "argv": [str(fake), "-c", 'yt-dlp -- "$1"', "sh", "{url}"]}
    write(folder, "mine.json", saved)
    why = f"{fake} {NOT_SYSTEM}"

    item = listed(client)["mine"]
    assert item["refused"].startswith(why) and item["path"] == str(folder / "mine.json")
    got = client.get("/api/scripts/mine", headers=H)
    assert got.status_code == 200 and json.loads(got.get_json()["content"]) == saved
    assert f"mine.json is refused: {why}" in run(client, "mine", status=400, url="https://x.com/a")["error"]
    assert jobs.active() == [] and runner.runs() == []

    # Edited at its path to name the system's shell, it is accepted; deleted, it is gone.
    write(folder, "mine.json", {**saved, "argv": ["/bin/sh", *saved["argv"][1:]]})
    assert listed(client)["mine"]["refused"] is None
    (folder / "mine.json").unlink()
    assert "mine" not in listed(client)


def test_a_shell_script_with_fv_variables_is_still_accepted(client, folder):
    write(folder, "fetch.sh", "#!/bin/sh\n# needs: url\n# rescan: {root}\nexec yt-dlp -P \"$FV_ROOT\" -- \"$FV_URL\"\n",
          0o755)
    assert listed(client)["fetch"]["refused"] is None


def test_the_rule_does_not_read_archive(monkeypatch):
    """{archive} still follows argv[0] (#102); the rule never asks what it is."""
    monkeypatch.setattr(scripts, "archive", lambda *a: pytest.fail("the rule read {archive}"))
    assert why_refused(["/usr/bin/nice", "yt-dlp", "--download-archive", "{archive}", "{url}"]) is None
    assert why_refused(["/usr/bin/perl", "{archive}"]).startswith("perl ")


def test_every_builtin_is_accepted_by_the_rule():
    for name, t in scripts.BUILTINS.items():
        assert scripts._check_program(t["argv"]) is None, name
        assert scripts.parse_command(scripts.template(name))[1] is None, name


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


@pytest.mark.parametrize("breakage", ["refused", "removed", "other-tool"])
def test_a_script_that_cannot_run_when_queued_fails_at_once(client, folder, runner, source, env, monkeypatch,
                                                            breakage):
    """#138: its params hold no SHA-256 to run, so it fails as it is asked
    for, with the reason: it never waits out instaloader's pause, nor for
    the instaloader run that holds the lock group, and takes no slot. Its
    Options can be changed right away. Nothing is built or run for it."""
    gate = env["tmp"] / "gate"
    os.mkfifo(gate)
    monkeypatch.setenv("RECORDER_GATE", str(gate))
    attach(client, source["id"], "mine")
    write(folder, "insta-hold.json", {"needs": "none", "argv": ["instaloader", "--hold"]})
    blocker = run(client, "insta-hold")["job"]
    wait_for(lambda: runner.runs())
    pause = time.time() + 600                  # instaloader's pause, ten minutes left of it
    jobs._cool["instaloader"] = pause
    if breakage == "refused":
        (folder / "mine.json").chmod(0o666)
        why = "mine.json is refused: writable by group or others"
    elif breakage == "removed":
        (folder / "mine.json").unlink()
        why = "no script mine"
    else:                                      # stored before the check (sources.json edited by hand)
        write(folder, "mine.json", {"needs": "url", "argv": ["yt-dlp", "{url}"]})
        why = "mine.json runs yt-dlp, not instaloader"
    try:
        job = sync_now(client, source["id"])["job"]
        assert job["state"] == "failed" and job["kind"] == "script-sync" and job["started_at"] is not None
        assert job["message"].startswith(f"the source's script: {why}")
        assert "sha256" not in job["params"] and why in job["params"]["why"]
        assert job["id"] not in jobs._active and jobs.get(job["id"])["state"] == "failed"
        assert jobs._cool["instaloader"] == pause                       # no pause of its own either
        assert [j["id"] for j in jobs.active()] == [blocker["id"]]      # the lock group's run, still running
        assert any(why in t for t in log_of(client, job["id"]))
        s = client.get(f"/api/sources/{source['id']}", headers=H).get_json()
        assert s["job"] is None and s["last_result"]["state"] == "failed" and s["last_result"]["message"] == job["message"]
        attach(client, source["id"], None)     # its Options: never "its sync is queued or running"
    finally:
        with open(gate, "w") as f:
            f.write("go\n")
    ended(blocker["id"])
    assert len(runner.runs()) == 1             # the blocker only


def test_a_script_attached_to_another_tools_source_is_refused(client, folder, runner, source):
    """#138: a yt-dlp command on an instaloader source would run yt-dlp in
    instaloader's lock group, beside yt-dlp's own syncs and without their
    pause: refused when attached, and listed as running yt-dlp."""
    write(folder, "video.json", {"needs": "url", "argv": ["yt-dlp", "{url}"]})
    error = attach(client, source["id"], "video", status=400)["error"]
    assert error.startswith("video.json runs yt-dlp, not instaloader")
    assert add_source(client, "dana.draws", script="video").status_code == 400
    assert attach(client, source["id"], "builtin:yt-dlp-video", status=400)["error"].startswith(
        "builtin:yt-dlp-video runs yt-dlp, not instaloader")
    got = listed(client)
    assert (got["video"]["program"], got["video"]["group"]) == ("yt-dlp", "yt-dlp")
    assert (got["mine"]["program"], got["mine"]["group"]) == ("instaloader", "instaloader")
    # A program that is not a downloader stays the source's to run.
    write(folder, "greet.sh", "#!/bin/sh\n# needs: target\necho hi\n", 0o755)
    assert (listed(client)["greet"]["program"], listed(client)["greet"]["group"]) == (None, "scripts")
    attach(client, source["id"], "greet")
    attach(client, source["id"], "mine")


@pytest.mark.parametrize("kind", ["shell", "command"])
def test_a_failed_script_sync_names_the_program_that_ran(client, folder, runner, source, monkeypatch, kind):
    """#138: not "instaloader failed" for what instaloader never ran."""
    if kind == "shell":
        write(folder, "greet.sh", "#!/bin/sh\n# needs: target\necho 'something odd happened'\nexit 1\n", 0o755)
        sid, name = "greet", "greet.sh"
    else:
        write(folder, "greet.json", command(runner, "{target}"))
        sid, name = "greet", "echo"
        monkeypatch.setenv("RECORDER_EXIT", "1")
        monkeypatch.setenv("RECORDER_SAY", "something odd happened")
    attach(client, source["id"], sid)
    job = ended(sync_now(client, source["id"])["job"]["id"])
    assert job["state"] == "failed" and job["result"]["error"] == "generic"
    assert job["message"] == f"{name} failed: something odd happened"
    assert "ran" not in job["params"]          # the hooks' only


def warning_of(client, sid):
    one = client.get(f"/api/sources/{sid}", headers=H).get_json()
    every = {s["id"]: s for s in client.get("/api/sources", headers=H).get_json()["sources"]}
    assert every[sid]["script_warning"] == one["script_warning"]
    return one["script_warning"]


def test_a_source_says_why_its_script_would_fail(client, folder, runner, source):
    """#138: GET /api/sources (and /<id>) carry script_warning, for the row
    to warn before the next sync fails."""
    assert warning_of(client, source["id"]) is None                  # no script
    attach(client, source["id"], "mine")
    assert warning_of(client, source["id"]) is None                  # one that can run
    path = folder / "mine.json"
    path.chmod(0o666)
    w = warning_of(client, source["id"])
    assert w["state"] == "refused" and w["reason"].startswith("mine.json is refused: writable by group or others")
    assert "chmod go-w" in w["reason"]
    path.chmod(0o644)
    path.unlink()
    assert warning_of(client, source["id"]) == {"state": "missing", "reason": f"no script mine (in {folder})"}
    write(folder, "mine.json", {"needs": "url", "argv": ["yt-dlp", "{url}"]})
    w = warning_of(client, source["id"])
    assert w["state"] == "other_tool" and w["reason"].startswith("mine.json runs yt-dlp, not instaloader")
    write(folder, "mine.json", INSTA)
    folder.chmod(0o777)
    try:
        w = warning_of(client, source["id"])
    finally:
        folder.chmod(0o755)
    assert w["state"] == "refused" and "the scripts folder is writable by group or others" in w["reason"]
    assert warning_of(client, source["id"]) is None


def test_a_refused_file_s_text_is_readable_when_it_could_be_read(client, folder):
    """#138: what the Scripts page offers View for."""
    write(folder, "bad.json", {"needs": "target", "argv": ["sh", "-c", "{target}"]})
    write(folder, "open.json", INSTA, 0o666)
    write(folder, "good.json", INSTA)
    got = listed(client)
    assert got["bad"]["refused"] and got["bad"]["readable"] is True
    assert got["open"]["refused"] and got["open"]["readable"] is False
    assert got["good"]["readable"] is True and got["builtin:instaloader-profile"]["readable"] is True
    assert '"sh"' in client.get("/api/scripts/bad", headers=H).get_json()["content"]
    assert client.get("/api/scripts/open", headers=H).get_json()["content"] is None


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


# #103: a script saved before the rule that the rule refuses now. It stays
# listed with the reason and its path (edited or deleted there: the app
# never writes it), its text can be read, a run is refused with the
# reason, and a schedule that names it records a failed run and moves on.
REFUSED_NOW = {"name": "Mine", "needs": "target", "argv": ["/usr/bin/perl", "/home/me/fetch.pl", "{target}"]}


def test_a_saved_script_the_rule_refuses_is_listed_never_run_and_fails_its_schedule(client, folder, runner, source,
                                                                                  monkeypatch):
    for state in ("_notes", "_held", "_last"):         # no earlier test's spread between a platform's syncs
        monkeypatch.setattr(scheduler, state, {})
    attach(client, source["id"], "mine")
    client.post(f"/api/sources/{source['id']}", json={"options": {"schedule": "hourly"}}, headers=H)
    runner.install_as("gallery-dl")
    other = client.post("/api/sources", json={"tool": "gallery-dl", "target": "https://x.com/carol",
                                              "options": {"schedule": "hourly"}}, headers=H).get_json()["source"]
    write(folder, "mine.json", REFUSED_NOW)
    why = "perl is not a program FeedVault reads the arguments of"

    item = listed(client)["mine"]
    assert item["refused"].startswith(why) and item["path"] == str(folder / "mine.json")
    got = client.get("/api/scripts/mine", headers=H)
    assert got.status_code == 200 and json.loads(got.get_json()["content"]) == REFUSED_NOW
    assert f"mine.json is refused: {why}" in run(client, "mine", status=400, target="carol.cooks")["error"]
    assert jobs.active() == []

    queued = scheduler.tick()
    assert sorted(j["kind"] for j in queued) == ["gallery-dl-sync", "script-sync"]
    failed = ended(next(j for j in queued if j["kind"] == "script-sync")["id"])
    assert failed["state"] == "failed" and failed["message"].startswith("the source's script: mine.json is refused: ")
    assert why in failed["message"]
    assert any(why in t for t in log_of(client, failed["id"]))
    s = client.get(f"/api/sources/{source['id']}", headers=H).get_json()
    assert s["last_result"]["state"] == "failed" and why in s["last_result"]["message"]
    # The other source's sync ran in the same tick, and the script never did.
    assert ended(next(j for j in queued if j["kind"] == "gallery-dl-sync")["id"])["state"] == "done"
    assert all(r["args"][-1] != "carol.cooks" for r in runner.runs())
    assert client.get(f"/api/sources/{other['id']}", headers=H).get_json()["last_result"]["state"] == "done"

    # Edited at its path into a form the rule takes, it is listed and runs again; deleted, it is gone.
    write(folder, "mine.json", INSTA)
    assert listed(client)["mine"]["refused"] is None
    (folder / "mine.json").unlink()
    assert "mine" not in listed(client)


FOREIGN = [{"Origin": "https://www.instagram.com"}, {"Sec-Fetch-Site": "cross-site"}]


@pytest.mark.parametrize("headers", FOREIGN)
def test_the_userscripts_origin_cannot_run_a_script(client, folder, runner, source, headers):
    """The userscript (instagram.com) can sync and add sources as before,
    and nothing more: no script set, none run through a source."""
    assert add_source(client, "dana.draws", headers=headers, script="mine").status_code == 403
    attach(client, source["id"], "mine", status=403, headers=headers)
    attach(client, source["id"], "mine")
    assert "only FeedVault's own dashboard" in sync_now(client, source["id"], status=403, headers=headers)["error"]
    # Sync all is the dashboard's alone (#112): refused before it runs.
    r = client.post("/api/sources/sync-all", headers={**H, **headers})
    assert r.status_code == 403 and "own dashboard" in r.get_json()["error"]
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


# ---------------------------------------------------------------------------
# Every argv form, as read before one walker reads them all (#69)
# ---------------------------------------------------------------------------

# The links each accepted form is filled in with (and a root holding "{"):
# what would be escaped (braces, %), refused (.., $) or read as another
# formatter (\f) in an option's value.
PIN_LINKS = {"env": "https://x.com/a/{_env[HOME]}", "template": "https://e.com/%(a|..)s/%(a|..)s/home",
             "dotdot": "https://x.com/../../etc", "dollar": "https://x.com/$HOME", "formfeed": "https://x.com/\fE x"}


def pin_vals(key):
    link, root = (PIN_LINKS[key], "/m/x") if key in PIN_LINKS else ("https://x.com/a", "/m/{x")
    return {"target": link, "url": link, "root": root, "data_dir": "/d", "archive": "/d/archive"}


def pin_needs(argv):
    used = scripts._used(argv)
    return "url" if "url" in used else "target" if "target" in used else "none"


# (argv, parse_command's refusal or None, command() for each of PIN_LINKS
# and "brace_root": its argv, "400: <why>", or "plain" when every value is
# put in as it is; "plain" alone when all are). Written down from the code
# as it was before the walker, never computed here: every form this file
# runs through parse_command or command() (a recorder's path as
# /opt/bin/<name>), and options after "--": positional since the walker,
# as the tools read them (#69).
PINNED = [
    (['gallery-dl', '--write-metadata', '--download-archive', '{archive}', '-o', 'skip=abort:5', '-D', '{root}',
      '--', '{url}'],
     None, 'plain'),
    (['instaloader', '--no-compress-json', '--dirname-pattern', '{root}/{profile}', '--', '-{target}'], None,
     {'brace_root': ['instaloader', '--no-compress-json', '--dirname-pattern', '/m/{{x/{profile}', '--',
                     '-https://x.com/a'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--no-posts', '--no-profile-pic', '--stories', '--highlights', '--no-compress-json',
      '--dirname-pattern', '{root}', '--', '{target}'],
     None,
     {'brace_root': ['instaloader', '--no-posts', '--no-profile-pic', '--stories', '--highlights',
                     '--no-compress-json', '--dirname-pattern', '/m/{{x', '--', 'https://x.com/a'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--write-info-json', '--write-thumbnail', '--download-archive', '{archive}', '--break-on-existing',
      '-o', '{root}/%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s', '--', '{url}'],
     None, 'plain'),
    (['gallery-dl', '--write-metadata', '-D', '{root}', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--write-info-json', '--write-thumbnail', '--no-playlist', '-o',
      '{root}/%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s', '--', '{url}'],
     None, 'plain'),
    (['instaloader', '--latest-stamps', '{archive}', '--no-compress-json', '--dirname-pattern', '{root}', '--',
      '{target}'],
     None,
     {'brace_root': ['instaloader', '--latest-stamps', '/d/archive', '--no-compress-json', '--dirname-pattern',
                     '/m/{{x', '--', 'https://x.com/a'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--no-compress-json', '--dirname-pattern', '{root}/{profile}', '--', ':saved'], None,
     {'brace_root': ['instaloader', '--no-compress-json', '--dirname-pattern', '/m/{{x/{profile}', '--', ':saved'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--no-videos', '--dirname-pattern', '{root}', '--', '{target}'], None,
     {'brace_root': ['instaloader', '--no-videos', '--dirname-pattern', '/m/{{x', '--', 'https://x.com/a'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['bash', '-c', 'x'],
     'argv[0] must be one of instaloader, gallery-dl, yt-dlp, ffmpeg (found as in Settings → Downloaders), or an '
     'absolute path to a program',
     None),
    (['{root}/x'],
     'argv[0] must be one of instaloader, gallery-dl, yt-dlp, ffmpeg (found as in Settings → Downloaders), or an '
     'absolute path to a program',
     None),
    (['yt-dlp', '{url}'], None, 'plain'),
    (['/opt/bin/recorder', '--flag', '--', '{target}'],
     'recorder is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/usr/bin/echo', '--flag', '--', '{target}'], None, 'plain'),
    (['/opt/bin/recorder', '--url={url}', '{url}'],
     'recorder is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/usr/bin/echo', '--url={url}', '{url}'], None, 'plain'),
    (['/opt/bin/recorder', '{root}/x', '{data_dir}', '{archive}', '{profile}', '{target}', '--', '{target}{target}'],
     'recorder is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/usr/bin/echo', '{root}/x', '{data_dir}', '{archive}', '{profile}', '{target}', '--', '{target}{target}'],
     None, 'plain'),
    (['instaloader', '{root}', '--', '{root}'], None, 'plain'),
    (['yt-dlp', '{root}', '--', '{root}'], None, 'plain'),
    (['instaloader', '--dirname-pattern', '{root}/{profile}'], None,
     {'brace_root': ['instaloader', '--dirname-pattern', '/m/{{x/{profile}'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--dirname-pattern={root}/{profile}'], None,
     {'brace_root': ['instaloader', '--dirname-pattern=/m/{{x/{profile}'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--filename-pattern', '{root}_{date_utc}'], None,
     {'brace_root': ['instaloader', '--filename-pattern', '/m/{{x_{date_utc}'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--filename-pattern={root}_{date_utc}'], None,
     {'brace_root': ['instaloader', '--filename-pattern=/m/{{x_{date_utc}'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '-o', '{root}/%(id)s.%(ext)s'], None, 'plain'),
    (['yt-dlp', '--output', '{root}/%(id)s.%(ext)s'], None, 'plain'),
    (['yt-dlp', '--output={root}/%(id)s.%(ext)s'], None, 'plain'),
    (['yt-dlp', '-o{root}/%(id)s.%(ext)s'], None, 'plain'),
    (['instaloader', '--title-pattern={root}_{date_utc}'], None,
     {'brace_root': ['instaloader', '--title-pattern=/m/{{x_{date_utc}'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--exec', 'echo {root}/%(id)s'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {root} in 'echo "
     "{root}/%(id)s'",
     None),
    (['yt-dlp', '--exec=echo {root}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {root} in 'echo "
     "{root}'",
     None),
    (['instaloader', '--dirname={root}', '--filename', '{root}'], None,
     {'brace_root': ['instaloader', '--dirname=/m/{{x', '--filename', '/m/{{x'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--outp={root}', '--exe', '{root}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {root} in '{root}'",
     None),
    (['instaloader', '--dirname-patterns={root}', '-d{root}', '--={root}'], None, 'plain'),
    (['yt-dlp', '--output-na-placeholder={root}', '-P{root}'], None, 'plain'),
    (['yt-dlp', '-o', '-o', '{root}'], None, 'plain'),
    (['gallery-dl', '-f', '{root}_{id}'], None,
     {'brace_root': ['gallery-dl', '-f', '/m/{{x_{id}'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-qN{root}'], None,
     {'brace_root': ['gallery-dl', '-qN/m/{{x'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print=post:{root}'], None,
     {'brace_root': ['gallery-dl', '--print=post:/m/{{x'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--filen', '{root}', '--rename-to={root}'], None,
     {'brace_root': ['gallery-dl', '--filen', '/m/{{x', '--rename-to=/m/{{x'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{root}', '{root}/out.txt', '-D', '{root}'], None,
     {'brace_root': ['gallery-dl', '--print-to-file', '/m/{{x', '/m/{x/out.txt', '-D', '/m/{x'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '{id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--Print-to-file', '{id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --Print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--Print-to-file', '{id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --Print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file={id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file={id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--Print-to-file={id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --Print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--Print-to-file={id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --Print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to', '{id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to', '{id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--Print-to', '{id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --Print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--Print-to', '{id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --Print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-f={id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-f={id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}-{url}', '{root}/out-{url}.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '{id}-https://x.com/a/{{_env[HOME]}}',
              '/m/x/out-https://x.com/a/{{_env[HOME]}}.txt', '--', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--Print-to-file', '{id}-{url}', '{root}/out-{url}.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --Print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--Print-to-file', '{id}-https://x.com/a/{{_env[HOME]}}',
              '/m/x/out-https://x.com/a/{{_env[HOME]}}.txt', '--', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --Print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file={id}-{url}', '{root}/out-{url}.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file={id}-https://x.com/a/{{_env[HOME]}}',
              '/m/x/out-https://x.com/a/{{_env[HOME]}}.txt', '--', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to', '{id}-{url}', '{root}/out-{url}.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to', '{id}-https://x.com/a/{{_env[HOME]}}',
              '/m/x/out-https://x.com/a/{{_env[HOME]}}.txt', '--', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{id}-{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '{id}', '{id}-https://x.com/a/{{_env[HOME]}}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{url}/{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '{id}',
              'https://x.com/a/{_env[HOME]}/https://x.com/a/{{_env[HOME]}}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{root}/{id}.txt'], None, 'plain'),
    (['gallery-dl', '--print-to-file', '{id}', '{url}/'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print', '{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['gallery-dl', '--print', 'https://x.com/a/{{_env[HOME]}}', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '-f', '{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '-f', 'https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '-f', '{url}', '/tmp/{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['gallery-dl', '-f', 'https://x.com/a/{{_env[HOME]}}', '/tmp/https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-files', '{url}', '{url}'], None, 'plain'),
    (['/usr/bin/env', '-i', 'gallery-dl', '--print-to-file', '{id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['/usr/bin/env', '-i', 'gallery-dl', '--print-to-file', '{id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['/usr/bin/env', '-u', '-f', 'gallery-dl', '-f', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['/usr/bin/env', '-u', '-f', 'gallery-dl', '-f', 'https://x.com/a/{{_env[HOME]}}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['/usr/local/bin/gallery-dl', '--print-to-file', '{id}', '/tmp/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['/usr/local/bin/gallery-dl', '--print-to-file', '{id}', '/tmp/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/{url}/ids.txt'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '{id}', '/tmp/https://x.com/a/{{_env[HOME]}}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{root}.txt'], None,
     {'brace_root': ['gallery-dl', '--print-to-file', '{id}', '/m/{{x.txt'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/{url}/ids.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/{url}/out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/{target}/out.txt', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {target} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {target} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{root}/../x/out.txt', '{url}'], None, 'plain'),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/../{url}/out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{root}/{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '{id}', '/m/x/https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{root}/out-{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '{id}', '/m/x/out-https://x.com/a/{{_env[HOME]}}',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '--Print-to-file', '{id}', '/tmp/{url}/out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --Print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --Print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file={id}', '/tmp/{url}/out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--Print-to', '{id}', '/tmp/{url}/out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --Print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --Print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/{url}./out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--Print-to-file', '{id}', '/tmp/{url}./out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --Print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --Print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file={id}', '/tmp/{url}./out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--Print-to', '{id}', '/tmp/{url}./out.txt', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --Print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --Print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{url}/.bashrc'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/{url}/x'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {url} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': ['gallery-dl', '--print-to-file', '{id}', 'https://x.com/a/{{_env[HOME]}}'],
      'formfeed': "400: {url} puts \\f in --print-to-file's file name, which gallery-dl may evaluate as Python",
      'template': 'plain'}),
    (['gallery-dl', '-d', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -d's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -d's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-d', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--destination=/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --destination's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --destination's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--destination={root}/../x', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-D', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -D's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -D's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-D', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-qD/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -D's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -D's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-qD{root}/../x', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--directory', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --directory's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --directory's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--directory', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--dir=/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --directory's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --directory's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--dir={root}/../x', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--download-archive', '/tmp/{url}/a.sqlite3', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --download-archive's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --download-archive's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--download-archive', '{root}/../x/a.sqlite3', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-e', '/tmp/{url}/errors.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -e's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -e's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-e', '{root}/../x/errors.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--error-file', '/tmp/{url}/errors.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --error-file's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --error-file's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--error-file', '{root}/../x/errors.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--write-log', '/tmp/{url}/log.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --write-log's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --write-log's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--write-log', '{root}/../x/log.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--write-unsupported=/tmp/{url}/u.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --write-unsupported's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --write-unsupported's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--write-unsupported={root}/../x/u.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-c', '/tmp/{url}/c.json', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -c's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -c's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-c', '{root}/../x/c.json', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--config-json', '/tmp/{url}/c.json', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --config-json's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --config-json's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--config-json', '{root}/../x/c.json', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--config-yaml', '/tmp/{url}/c.yaml', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --config-yaml's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --config-yaml's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--config-yaml', '{root}/../x/c.yaml', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--config-toml', '/tmp/{url}/c.toml', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --config-toml's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --config-toml's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--config-toml', '{root}/../x/c.toml', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-C', '/tmp/{url}/cookies.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -C's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -C's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-C', '{root}/../x/cookies.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--cookies-export', '/tmp/{url}/cookies.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --cookies-export's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --cookies-export's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--cookies-export', '{root}/../x/cookies.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-i', '/tmp/{url}/in.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -i's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -i's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-i', '{root}/../x/in.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-I', '/tmp/{url}/in.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -I's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -I's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-I', '{root}/../x/in.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--input-file-delete=/tmp/{url}/in.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --input-file-delete's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in --input-file-delete's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--input-file-delete={root}/../x/in.txt', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '-P', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -P's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in -P's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '-P', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--paths', 'temp:/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --paths's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --paths's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--paths', 'temp:{root}/../x', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '-o', '/tmp/{url}/%(id)s.%(ext)s', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -o's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in -o's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '-o', '/tmp/https://e.com/%%(a|..)s/%%(a|..)s/home/%(id)s.%(ext)s', '--',
                   'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['yt-dlp', '-o', '{root}/../x/%(id)s.%(ext)s', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--output=/tmp/{url}/%(id)s.%(ext)s', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --output's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --output's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '--output=/tmp/https://e.com/%%(a|..)s/%%(a|..)s/home/%(id)s.%(ext)s', '--',
                   'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['yt-dlp', '--output={root}/../x/%(id)s.%(ext)s', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--download-archive', '/tmp/{url}/a.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --download-archive's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --download-archive's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--download-archive', '{root}/../x/a.txt', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--cookies', '/tmp/{url}/cookies.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --cookies's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --cookies's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--cookies', '{root}/../x/cookies.txt', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '-a', '/tmp/{url}/in.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -a's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in -a's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '-a', '{root}/../x/in.txt', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--batch-file=/tmp/{url}/in.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --batch-file's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --batch-file's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--batch-file={root}/../x/in.txt', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--load-info-json', '/tmp/{url}/i.json', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --load-info-json's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --load-info-json's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--load-info-json', '{root}/../x/i.json', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--load-info', '/tmp/{url}/i.json', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --load-info-json's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --load-info-json's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--load-info', '{root}/../x/i.json', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--batch=/tmp/{url}/in.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --batch-file's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --batch-file's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--batch={root}/../x/in.txt', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--pat', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --paths's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --paths's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--pat', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '-iP', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -P's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in -P's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '-iP', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '-qio/tmp/{url}/%(id)s', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -o's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in -o's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '-qio/tmp/https://e.com/%%(a|..)s/%%(a|..)s/home/%(id)s', '--',
                   'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['yt-dlp', '-qio{root}/../x/%(id)s', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--cache-dir', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --cache-dir's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --cache-dir's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--cache-dir', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--config-locations', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --config-locations's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --config-locations's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--config-locations', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--netrc-location', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --netrc-location's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --netrc-location's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--netrc-location', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--plugin-dirs', '/tmp/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --plugin-dirs's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --plugin-dirs's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--plugin-dirs', '{root}/../x', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--ffmpeg-location', '/tmp/{url}/ffmpeg', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --ffmpeg-location's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --ffmpeg-location's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--ffmpeg-location', '{root}/../x/ffmpeg', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--print-to-file', '%(id)s', '/tmp/{url}/ids.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '--print-to-file', '%(id)s', '/tmp/https://e.com/%%(a|..)s/%%(a|..)s/home/ids.txt',
                   '--', 'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['yt-dlp', '--print-to-file', '%(id)s', '{root}/../x/ids.txt', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--print-to-file=%(id)s', '/tmp/{url}/ids.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '--print-to-file=%(id)s', '/tmp/https://e.com/%%(a|..)s/%%(a|..)s/home/ids.txt', '--',
                   'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['yt-dlp', '--print-to-file=%(id)s', '{root}/../x/ids.txt', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--print-to-file', '%(title)s', '{root}/logs/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '--print-to-file', '%(title)s', '/m/x/logs/https://e.com/%%(a|..)s/%%(a|..)s/home',
                   '--', 'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['yt-dlp', '--print-to', '{url}', '{root}/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '--print-to', 'https://e.com/%%(a|..)s/%%(a|..)s/home',
                   '/m/x/https://e.com/%%(a|..)s/%%(a|..)s/home', '--', 'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['yt-dlp', '--print-to-file={url}', '{root}/{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in --print-to-file's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in --print-to-file's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '--print-to-file=https://e.com/%%(a|..)s/%%(a|..)s/home',
                   '/m/x/https://e.com/%%(a|..)s/%%(a|..)s/home', '--', 'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['instaloader', '--dirname-pattern', '{root}/{target}', '--', '{target}'], None,
     {'brace_root': ['instaloader', '--dirname-pattern', '/m/{{x/https://x.com/a', '--', 'https://x.com/a'],
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --dirname-pattern's path, which would lead out of the folder written "
                'there',
      'env': ['instaloader', '--dirname-pattern', '/m/x/https://x.com/a/{{_env[HOME]}}', '--',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--dirname-pattern', '{target}', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --dirname-pattern's path, which would lead out of the folder written "
                'there',
      'env': ['instaloader', '--dirname-pattern', 'https://x.com/a/{{_env[HOME]}}', '--',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--dirname={root}/{target}/x', '--', '{target}'], None,
     {'brace_root': ['instaloader', '--dirname=/m/{{x/https://x.com/a/x', '--', 'https://x.com/a'],
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --dirname-pattern's path, which would lead out of the folder written "
                'there',
      'env': ['instaloader', '--dirname=/m/x/https://x.com/a/{{_env[HOME]}}/x', '--',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--dirname={target}/x', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --dirname-pattern's path, which would lead out of the folder written "
                'there',
      'env': ['instaloader', '--dirname=https://x.com/a/{{_env[HOME]}}/x', '--', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--filename-pattern', '{target}/{date_utc}', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --filename-pattern's path, which would lead out of the folder written "
                'there',
      'env': ['instaloader', '--filename-pattern', 'https://x.com/a/{{_env[HOME]}}/{date_utc}', '--',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--title-pattern', '{target}/{date_utc}', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --title-pattern's path, which would lead out of the folder written "
                'there',
      'env': ['instaloader', '--title-pattern', 'https://x.com/a/{{_env[HOME]}}/{date_utc}', '--',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--resume-prefix', '{root}/{target}/r', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --resume-prefix's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--resume-prefix', '{target}/r', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --resume-prefix's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--latest-stamps', '{root}/{target}/stamps.ini', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --latest-stamps's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--latest-stamps', '{target}/stamps.ini', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --latest-stamps's path, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '-B', '{root}/{target}/cookies.sqlite', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in -B's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '-B', '{target}/cookies.sqlite', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in -B's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--cookiefile={root}/{target}/cookies.sqlite', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --cookiefile's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--cookiefile={target}/cookies.sqlite', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --cookiefile's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '-Ff', '{root}/{target}/session', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in -f's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '-Ff', '{target}/session', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in -f's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--sessionfile', '{root}/{target}/session', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --sessionfile's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['instaloader', '--sessionfile', '{target}/session', '--', '{target}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': "400: {target} puts a .. in --sessionfile's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['/usr/bin/env', '-C', '/tmp/{url}', 'gallery-dl', '{url}'],
     "'/tmp/{url}': a placeholder may not be among env's options or operands (a variable can be code to "
     'the program, LD_PRELOAD or BASH_ENV, and -C picks the folder its relative names, configs and '
     'modules are found in): pass it to the program it runs',
     None),
    (['env', '-i', '-u', 'X', '-C/tmp/{url}', 'yt-dlp', '{url}'],
     'argv[0] must be one of instaloader, gallery-dl, yt-dlp, ffmpeg (found as in Settings → Downloaders), or an '
     'absolute path to a program',
     None),
    (['env', '--chdir=/tmp/{url}', 'instaloader', 'x'],
     'argv[0] must be one of instaloader, gallery-dl, yt-dlp, ffmpeg (found as in Settings → Downloaders), or an '
     'absolute path to a program',
     None),
    (['gallery-dl', '--filename', '{url}', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['gallery-dl', '--filename', 'https://x.com/a/{{_env[HOME]}}', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-D', '{root}', '--download-archive', '{archive}', '{url}'], None, 'plain'),
    (['gallery-dl', '--print-to-file', '-D', '/tmp/{root}', '{url}'], None,
     {'brace_root': ['gallery-dl', '--print-to-file', '-D', '/tmp//m/{{x', 'https://x.com/a'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--paths', '{root}', '-o', '%(id)s.%(ext)s', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--print', '{url}', '--netrc', '--', '{url}'], None, 'plain'),
    (['yt-dlp', '--print-to-file', '{url}', '{root}/ids.txt', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': ['yt-dlp', '--print-to-file', 'https://e.com/%%(a|..)s/%%(a|..)s/home', '/m/x/ids.txt', '--',
                   'https://e.com/%(a|..)s/%(a|..)s/home']}),
    (['/usr/bin/env', '-u', '-C', 'gallery-dl', '{url}'], None, 'plain'),
    (['instaloader', '--no-videos', '--latest-stamps', '{archive}', '--dirname-pattern', '{root}', '--',
      '{target}'],
     None,
     {'brace_root': ['instaloader', '--no-videos', '--latest-stamps', '/d/archive', '--dirname-pattern', '/m/{{x',
                     '--', 'https://x.com/a'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--exec', 'notify-send done {url}', '--', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     '(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in '
     "'notify-send done {url}'",
     None),
    (['yt-dlp', '--exec', 'echo {root}', '--', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {root} in 'echo "
     "{root}'",
     None),
    (['yt-dlp', '--exec=echo {url}', '--', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in 'echo "
     "{url}'",
     None),
    (['yt-dlp', '--exec', 'before_dl:echo {url}', '--', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     '(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in '
     "'before_dl:echo {url}'",
     None),
    (['yt-dlp', '--exec-before-download={archive}', '--', '{url}'],
     "--exec-before-download's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's "
     'own fields (%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found '
     "{archive} in '{archive}'",
     None),
    (['yt-dlp', '--exec-b', 'echo {data_dir}', '--', '{url}'],
     "--exec-before-download's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's "
     'own fields (%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found '
     "{data_dir} in 'echo {data_dir}'",
     None),
    (['yt-dlp', '--netrc-cmd', 'pass {url}', '--', '{url}'],
     "--netrc-cmd's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in 'pass "
     "{url}'",
     None),
    (['yt-dlp', '--use-postprocessor=Exec:exec_cmd=echo {url}', '--', '{url}'],
     "--use-postprocessor's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own "
     'fields (%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in '
     "'Exec:exec_cmd=echo {url}'",
     None),
    (['yt-dlp', '--use-p', 'Exec:exec_cmd=echo {url}', '--', '{url}'],
     "--use-postprocessor's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own "
     'fields (%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in '
     "'Exec:exec_cmd=echo {url}'",
     None),
    (['gallery-dl', '--exec', 'convert {} {root}/x.png', '--', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     "variables); found {root} in 'convert {} {root}/x.png'",
     None),
    (['gallery-dl', '--exec-after={root}', '--', '{url}'],
     "--exec-after's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own "
     'fields ({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are '
     "FV_* variables); found {root} in '{root}'",
     None),
    (['gallery-dl', '--exec-a', 'cd {root}', '--', '{url}'],
     "--exec-after's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own "
     'fields ({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are '
     "FV_* variables); found {root} in 'cd {root}'",
     None),
    (['gallery-dl', '-o', 'postprocessors=[{"name": "exec", "command": "echo {url}"}]', '--', '{url}'],
     "-o's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     'variables); found {url} in \'postprocessors=[{"name": "exec", "command": "echo {url}"}]\'',
     None),
    (['gallery-dl', '-obase-directory={root}', '--', '{url}'],
     "-o's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     "variables); found {root} in 'base-directory={root}'",
     None),
    (['gallery-dl', '-qo', 'x={root}', '--', '{url}'],
     "-o's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     "variables); found {root} in 'x={root}'",
     None),
    (['gallery-dl', '--opt=x={root}', '--', '{url}'],
     "--option's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     "variables); found {root} in 'x={root}'",
     None),
    (['gallery-dl', '-P', 'exec', '-O', 'command=echo {url}', '--', '{url}'],
     "-O's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     "variables); found {url} in 'command=echo {url}'",
     None),
    (['gallery-dl', '--postprocessor-option', 'command=echo {url}', '--', '{url}'],
     "--postprocessor-option's value can reach a shell, so it may not hold a FeedVault placeholder: use "
     "gallery-dl's own fields ({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script "
     "(its inputs are FV_* variables); found {url} in 'command=echo {url}'",
     None),
    (['gallery-dl', '-So', 'x={url}', '--', '{url}'],
     "-o's value can reach a shell, so it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     "variables); found {url} in 'x={url}'",
     None),
    (['gallery-dl', '--filter', "'{url}' != ''", '--', '{url}'],
     "--filter's value can reach Python (gallery-dl evaluates it), so it may not hold a FeedVault placeholder: use "
     "gallery-dl's own fields ({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script "
     '(its inputs are FV_* variables); found {url} in "\'{url}\' != \'\'"',
     None),
    (['gallery-dl', '--chapter-filter={url}', '--', '{url}'],
     "--chapter-filter's value can reach Python (gallery-dl evaluates it), so it may not hold a FeedVault "
     "placeholder: use gallery-dl's own fields ({_path}, {_directory}), -D {root}, --download-archive {archive}, "
     "or a shell script (its inputs are FV_* variables); found {url} in '{url}'",
     None),
    (['yt-dlp', '--downloader-args', 'aria2c:-d {root}', '--', '{url}'],
     "--downloader-args's value can reach another program's arguments, split at spaces, so it may not hold a "
     "FeedVault placeholder: use yt-dlp's own fields (%(webpage_url)q, %(filepath)q), or a shell script (its "
     "inputs are FV_* variables); found {root} in 'aria2c:-d {root}'",
     None),
    (['yt-dlp', '--external-downloader-args={root}', '--', '{url}'],
     "--external-downloader-args's value can reach another program's arguments, split at spaces, so it may not "
     "hold a FeedVault placeholder: use yt-dlp's own fields (%(webpage_url)q, %(filepath)q), or a shell script "
     "(its inputs are FV_* variables); found {root} in '{root}'",
     None),
    (['yt-dlp', '--ppa', 'ffmpeg:-metadata url={url}', '--', '{url}'],
     "--ppa's value can reach another program's arguments, split at spaces, so it may not hold a FeedVault "
     "placeholder: use yt-dlp's own fields (%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* "
     "variables); found {url} in 'ffmpeg:-metadata url={url}'",
     None),
    (['yt-dlp', '--postprocessor-args', '{root}', '--', '{url}'],
     "--postprocessor-args's value can reach another program's arguments, split at spaces, so it may not hold a "
     "FeedVault placeholder: use yt-dlp's own fields (%(webpage_url)q, %(filepath)q), or a shell script (its "
     "inputs are FV_* variables); found {root} in '{root}'",
     None),
    (['/usr/local/bin/yt-dlp', '--exec', 'echo {url}', '--', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in 'echo "
     "{url}'",
     None),
    (['/usr/bin/env', 'A=1', '-u', 'B', 'gallery-dl', '--exec', 'echo {url}', '--', '{url}'],
     '-u is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/bin/sh', '-c', 'notify-send {url}'],
     "a shell's -c text is read as code, so it may not hold a FeedVault placeholder: pass it after the text (sh -c "
     '\'… "$1"\' sh {url}), or use a shell script (its inputs are FV_* variables)',
     None),
    (['/bin/bash', '-ec', 'echo {url}', 'bash'],
     "a shell's -c text is read as code, so it may not hold a FeedVault placeholder: pass it after the text (sh -c "
     '\'… "$1"\' sh {url}), or use a shell script (its inputs are FV_* variables)',
     None),
    (['/bin/sh', '-o', 'errexit', '-c', 'echo {url}'],
     "a shell's -c text is read as code, so it may not hold a FeedVault placeholder: pass it after the text (sh -c "
     '\'… "$1"\' sh {url}), or use a shell script (its inputs are FV_* variables)',
     None),
    (['/bin/bash', '--rcfile', 'x', '-c', 'echo {url}'],
     "a shell's -c text is read as code, so it may not hold a FeedVault placeholder: pass it after the text (sh -c "
     '\'… "$1"\' sh {url}), or use a shell script (its inputs are FV_* variables)',
     None),
    (['/bin/sh', '-o', '{url}', '-c', 'x'], "'{url}': a placeholder may not be among a shell's options", None),
    (['/usr/bin/env', '-S', 'yt-dlp --exec x', '{url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '--split-string=yt-dlp {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['yt-dlp', '--exec', 'notify-send done %(webpage_url)q'], None, 'plain'),
    (['yt-dlp', '--exec=echo %(filepath)q'], None, 'plain'),
    (['gallery-dl', '--exec', 'convert {} {}.png && rm {_path}'], None, 'plain'),
    (['gallery-dl', '-o', 'skip=abort:5', '-O', 'command=echo {_path}'], None, 'plain'),
    (['yt-dlp', '--netrc', '{url}'], None, 'plain'),
    (['gallery-dl', '--postprocessor', 'metadata', '-D', '{root}'], None, 'plain'),
    (['yt-dlp', '--downloader', 'aria2c', '--external-downloader', '{root}/aria2c'], None, 'plain'),
    (['yt-dlp', '-P', '{root}', '--download-archive', '{archive}', '--match-filters', 'id!={url}'], None, 'plain'),
    (['gallery-dl', '-D', '{root}', '--download-archive', '{archive}', '-q'], None, 'plain'),
    (['gallery-dl', '-D{root}o{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -D's path, which gallery-dl would expand",
      'dotdot': "400: {url} puts a .. in -D's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--alias', 'n', '--exec {0}', 'https://example.com/a'], None, 'plain'),
    (['/usr/local/bin/other', '--exec', '{url}'],
     'other is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/bin/sh', '-ec', 'notify-send done "$1"', 'sh', '{url}'], None, 'plain'),
    (['/bin/bash', '/home/me/fetch.sh', '{url}'], None, 'plain'),
    (['yt-dlp', '--alias', 'n', '--exec "echo {0}"', '--n', '{url}'],
     "--alias carries what follows it into the options it expands to, a shell's too: not in a command with a "
     'FeedVault placeholder (use a shell script)',
     None),
    (['yt-dlp', '--exec', 'notify-send done %(webpage_url)q', '--', '{url}'], None, 'plain'),
    (['/usr/bin/env', '-iS', 'yt-dlp --exec "echo {url}" {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '-vS', 'yt-dlp {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '-0S', 'yt-dlp {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '-i0S', 'yt-dlp {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '-iSyt-dlp {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '-u', 'NAME', '-S', 'yt-dlp {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '-uNAME', '-iS', 'yt-dlp {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '--split', 'yt-dlp {url}'],
     'env -S splits its text into a command: not in a command with a FeedVault placeholder', None),
    (['/usr/bin/env', '-i', 'yt-dlp', '{url}'], None, 'plain'),
    (['/usr/bin/env', '-i', 'yt-dlp', '--exec', 'echo {url}', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in 'echo "
     "{url}'",
     None),
    (['/usr/bin/env', '-u', 'NAME', 'yt-dlp', '{url}'], None, 'plain'),
    (['/usr/bin/env', '-u', 'NAME', 'yt-dlp', '--exec', 'echo {url}', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in 'echo "
     "{url}'",
     None),
    (['/usr/bin/env', '-C', '/tmp', 'yt-dlp', '{url}'], None, 'plain'),
    (['/usr/bin/env', '-C', '/tmp', 'yt-dlp', '--exec', 'echo {url}', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in 'echo "
     "{url}'",
     None),
    (['/usr/bin/env', '-uS', 'yt-dlp', '{url}'], None, 'plain'),
    (['/usr/bin/env', '-uS', 'yt-dlp', '--exec', 'echo {url}', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in 'echo "
     "{url}'",
     None),
    (['/usr/bin/env', '-iu', 'S', '--chdir', '/tmp', 'yt-dlp', '{url}'], None, 'plain'),
    (['/usr/bin/env', '-iu', 'S', '--chdir', '/tmp', 'yt-dlp', '--exec', 'echo {url}', '{url}'],
     "--exec's value can reach a shell, so it may not hold a FeedVault placeholder: use yt-dlp's own fields "
     "(%(webpage_url)q, %(filepath)q), or a shell script (its inputs are FV_* variables); found {url} in 'echo "
     "{url}'",
     None),
    (['gallery-dl', '-f', "\x0cE '{url}'", '--', '{url}'],
     "-f's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', "-f\x0cE '{url}'", '--', '{url}'],
     "-f's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', "--filename=\x0cE '{url}'", '--', '{url}'],
     "--filename's value is a format string that gallery-dl evaluates as Python or reads as a template file when "
     "it starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', '--filen', "\x0cE '{url}'", '--', '{url}'],
     "--filename's value is a format string that gallery-dl evaluates as Python or reads as a template file when "
     "it starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', "-qf\x0cE '{url}'", '--', '{url}'],
     "-f's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', '-qf', "\x0cE '{url}'", '--', '{url}'],
     "-f's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', '-f', "\\fE '{url}'", '--', '{url}'],
     "-f's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\\\fE \'{url}\'"',
     None),
    (['gallery-dl', "-f=\\fE '{url}'", '--', '{url}'],
     "-f's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\\\fE \'{url}\'"',
     None),
    (['gallery-dl', "-qf=\x0cE '{url}'", '--', '{url}'],
     "-f's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', "-N=\\fE '{url}'", '--', '{url}'],
     "-N's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\\\fE \'{url}\'"',
     None),
    (['gallery-dl', "-qN=\x0cE '{url}'", '--', '{url}'],
     "-N's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', '-f', '\x0cT {root}/name.txt', '--', '{url}'],
     "-f's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     "found {root} in '\\x0cT {root}/name.txt'",
     None),
    (['gallery-dl', '-N', 'post:\x0cF {url}', '--', '{url}'],
     "-N's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     "found {url} in 'post:\\x0cF {url}'",
     None),
    (['gallery-dl', '-qNpost:\x0cF {url}', '--', '{url}'],
     "-N's value is a format string that gallery-dl evaluates as Python or reads as a template file when it starts "
     "with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     "found {url} in 'post:\\x0cF {url}'",
     None),
    (['gallery-dl', '--print', '\x0cM {root}/mod.py:f', '--', '{url}'],
     "--print's value is a format string that gallery-dl evaluates as Python or reads as a template file when it "
     "starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     "found {root} in '\\x0cM {root}/mod.py:f'",
     None),
    (['gallery-dl', '--Print', "\x0cE '{url}'", '--', '{url}'],
     "--Print's value is a format string that gallery-dl evaluates as Python or reads as a template file when it "
     "starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', '--Print', "file:\\fE '{url}'", '--', '{url}'],
     "--Print's value is a format string that gallery-dl evaluates as Python or reads as a template file when it "
     "starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "file:\\\\fE \'{url}\'"',
     None),
    (['gallery-dl', '--print-to-file', "\x0cE '{url}'", 'out.txt', '--', '{url}'],
     "--print-to-file's value is a format string that gallery-dl evaluates as Python or reads as a template file "
     "when it starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     'variables); found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', '--Print-to-file', 'after:\x0cJ {url}', 'out.txt', '--', '{url}'],
     "--Print-to-file's value is a format string that gallery-dl evaluates as Python or reads as a template file "
     "when it starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     "variables); found {url} in 'after:\\x0cJ {url}'",
     None),
    (['gallery-dl', '--print-to-file', '{id}', '/tmp/\x0cE {url}', '--', '{url}'],
     "--print-to-file's FILE is a format string that gallery-dl evaluates as Python or reads as a template file "
     "when it starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields "
     '({_path}, {_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* '
     "variables); found {url} in '/tmp/\\x0cE {url}'",
     None),
    (['gallery-dl', '--rename-to', "\x0cE '{url}'", '--', '{url}'],
     "--rename-to's value is a format string that gallery-dl evaluates as Python or reads as a template file when "
     "it starts with \\f, so there it may not hold a FeedVault placeholder: use gallery-dl's own fields ({_path}, "
     '{_directory}), -D {root}, --download-archive {archive}, or a shell script (its inputs are FV_* variables); '
     'found {url} in "\\x0cE \'{url}\'"',
     None),
    (['gallery-dl', '-f', '{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['gallery-dl', '-f', 'https://x.com/a/{{_env[HOME]}}', '--', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-f', '\x0cE title', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-N', 'post:{id} {url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['gallery-dl', '-N', 'post:{id} https://x.com/a/{{_env[HOME]}}', '--', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{root}/out.txt', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--print', '{url}', '-f', '\x0cF {title}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['gallery-dl', '--print', 'https://x.com/a/{{_env[HOME]}}', '-f', '\x0cF {title}', '--',
              'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-traffic', '-D', '{root}', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '--mtime', 'date', '-D', '{root}', '--', '{url}'], None, 'plain'),
    (['gallery-dl', '-N', 'post:{url}', '--', '{url}'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['gallery-dl', '-N', 'post:https://x.com/a/{{_env[HOME]}}', '--', 'https://x.com/a/{_env[HOME]}'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['/opt/bin/gallery-dl', '-f', '{target}', 'https://example.com/a'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['/opt/bin/gallery-dl', '-f', 'https://x.com/a/{{_env[HOME]}}', 'https://example.com/a'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['/usr/bin/env', '-i', '/opt/bin/gallery-dl', '-N', '{target}', 'https://example.com/a'], None,
     {'brace_root': 'plain',
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': ['/usr/bin/env', '-i', '/opt/bin/gallery-dl', '-N', 'https://x.com/a/{{_env[HOME]}}',
              'https://example.com/a'],
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--print-to-file', '{id}', '{target}/out.txt', 'https://example.com/a'], None,
     {'brace_root': 'plain',
      'dollar': "400: {target} puts a $ in --print-to-file's folder, which gallery-dl would expand",
      'dotdot': "400: {target} puts a .. in --print-to-file's folder, which would lead out of the folder written "
                'there',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '-D', '{root}', '--', '{url}'], None, 'plain'),
    (['instaloader', '--', '{target}'], None, 'plain'),
    (['/opt/bin/recorder', '{target}'],
     'recorder is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/usr/bin/echo', '{target}'], None, 'plain'),
    (['/opt/bin/recorder', '{url}'],
     'recorder is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/usr/bin/echo', '{url}'], None, 'plain'),
    (['yt-dlp', '--', '{target}'], None, 'plain'),
    (['/opt/bin/recorder', '--evil', '{target}'],
     'recorder is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/usr/bin/echo', '--evil', '{target}'], None, 'plain'),
    (['/opt/bin/recorder', '--post', '{root}/carol.cooks'],
     'recorder is not a program FeedVault reads the arguments of (a downloader, a shell, Python running a '
     'downloader, echo, printf), so it may read a FeedVault placeholder as code: use a shell script '
     'instead (its inputs are FV_* variables, never pasted into code)',
     None),
    (['/usr/bin/echo', '--post', '{root}/carol.cooks'], None, 'plain'),
    (['/opt/bin/recorder'], None, 'plain'),
    (['/usr/bin/echo'], None, 'plain'),
    (['instaloader', '--hold'], None, 'plain'),
    (['instaloader', '--no-videos', '--latest-stamps', '{archive}', '--dirname-pattern', '{root}', '--', '{target}',
      '--extra'],
     None,
     {'brace_root': ['instaloader', '--no-videos', '--latest-stamps', '/d/archive', '--dirname-pattern', '/m/{{x',
                     '--', 'https://x.com/a', '--extra'],
      'dollar': 'plain',
      'dotdot': 'plain',
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['yt-dlp', '--', '-P', '/tmp/{url}'], None, 'plain'),
    (['yt-dlp', '--', '--exec', 'echo {url}'], None, 'plain'),
    (['yt-dlp', '-P', '--', '-P', '/tmp/{url}'], None,
     {'brace_root': 'plain',
      'dollar': "400: {url} puts a $ in -P's path, which yt-dlp would expand",
      'dotdot': "400: {url} puts a .. in -P's path, which would lead out of the folder written there",
      'env': 'plain',
      'formfeed': 'plain',
      'template': 'plain'}),
    (['gallery-dl', '--', '-D', '/tmp/{url}'], None, 'plain'),
    (['gallery-dl', '--', '-f', "\x0cE '{url}'"], None, 'plain'),
    (['gallery-dl', '-D', '{root}', '--', '--', '-D', '/tmp/{url}'], None, 'plain'),
    (['instaloader', '--', '--dirname-pattern', '{root}/{target}'], None, 'plain'),
]


@pytest.mark.parametrize("argv, why, got", PINNED, ids=range(len(PINNED)))
def test_every_argv_form_reads_as_pinned(argv, why, got):
    _, error = scripts.parse_command(json.dumps({"needs": pin_needs(argv), "argv": argv}))
    assert error == why
    if why is not None:
        return
    for key in [*PIN_LINKS, "brace_root"]:
        vals = pin_vals(key)
        want = got if got == "plain" else got[key]
        try:
            out = scripts.command({"tool": argv[0], "argv": argv}, vals)
        except jobs.BadRequest as e:
            out = f"400: {e}"
        if want == "plain":
            want = [scripts.substitute(a, vals) for a in argv]
        assert out == want, key


def walked(argv):
    start, _, found = scripts._walk(argv)
    return start, [(f.index, f.given, [o for o, _ in f.options], f.values, f.maybe) for f in found]


def probe(argv, item, at, text):
    """``argv`` with item ``item``'s text from ``at`` on replaced by ``text``,
    and FeedVault's placeholders anywhere else made plain text: the walker
    must read it as it reads ``argv``."""
    out = [a if n == item else re.sub(r"\{(target|url|root|data_dir|archive)\}", "x", a) for n, a in enumerate(argv)]
    out[item] = argv[item][:at] + text
    assert walked(out) == walked(argv)
    return out


@pytest.mark.parametrize("argv", [row[0] for row in PINNED], ids=range(len(PINNED)))
def test_the_check_and_command_agree_on_every_pinned_form(argv):
    """#69: each value or positional item of a pinned form (env's option
    values too) holding a link in turn: command() escapes it exactly when
    the walker reads it as a value its tool formats (and gallery-dl's
    check reads it as a format string: the same items), refuses a link's
    .. there exactly when either reading has it a path (or FILE) value,
    and _check_shell refuses it as code or a format string exactly when
    either reading has it one. An item naming an option is never given a
    link: that would change how the command reads."""
    start, tool, found = scripts._walk(argv)
    if start is None or tool not in scripts.TOOLS:
        return
    claims = collections.defaultdict(list)
    for f in found:
        for n, (item, at) in enumerate(f.values):
            claims[item].append((f, n, at))
    items = {item for item, c in claims.items() if c[0][2] or not argv[item].startswith("-")}
    items |= {n for n in range(start + 1, len(argv)) if n not in claims and not argv[n].startswith("-")}
    vals = {"target": "https://x.com/{a}%", "url": "https://x.com/{a}%", "root": "/m/x", "data_dir": "/d",
            "archive": "/d/a"}
    # yt-dlp's --alias refuses any placeholder: no item of the check's own.
    alias = any(scripts.ALIAS in f.kinds(0) for f in found)
    escaped, paths, formats, code = set(), set(), set(), set()
    for item in sorted(items):
        at = claims[item][0][2] if item in claims else 0
        argv2 = probe(argv, item, at, "{url}")
        if scripts.command({"tool": argv[0], "argv": argv2}, vals)[item] != scripts.substitute(argv2[item], vals):
            escaped.add(item)
        try:
            scripts.command({"tool": argv[0], "argv": argv2}, {**vals, "url": DOTDOT})
        except jobs.BadRequest as e:
            assert re.fullmatch(r"\{url\} puts a \.\. in \S+'s (path|folder), .*", str(e))
            paths.add(item)
        if alias:
            continue
        why = scripts._check_shell(probe(argv, item, at, "\fE {url}"))
        if why and "is a format string" in why:
            formats.add(item)
        elif why:
            code.add(item)
    marked = {i: set().union(*(f.kinds(n) for f, n, _ in claims[i])) for i in items & set(claims)}
    sure = {i: f.sure(n) for i in items & set(claims) for f, n, _ in claims[i] if f.index > start and not f.maybe}
    assert escaped == {i for i, k in sure.items() if k & (scripts.ESCAPED | {scripts.FILE})}
    assert paths == {i for i, k in marked.items() if k & {scripts.PATH, scripts.FILE}}
    if alias:
        return
    assert code == {i for i, k in marked.items() if k & scripts.CODE}
    assert formats == {i for i, k in marked.items() if k & scripts.FORMATS} - code
    if tool == "gallery-dl":
        # What the check reads as a format string (on the one reading
        # command() escapes by) is what command() escapes, and the reverse.
        read = {i for i, k in sure.items() if k & scripts.FORMATS}
        assert read == escaped and read <= formats


@pytest.mark.parametrize("args, why", [
    # An option the table lacks may take the next item as its value
    # (optparse takes any item): -P, --paths, -o are then that value, and
    # --exec an option.
    (["--referer", "-P", "--exec", "echo {url}"], "--exec's value can reach a shell"),
    (["--referer", "--paths", "--exec", "echo {url}"], "--exec's value can reach a shell"),
    (["--print", "-o", "--exec", "echo {url}"], "--exec's value can reach a shell"),
    # A "--" yt-dlp may read as such an option's value ends nothing.
    (["--referer", "--", "--exec", "echo {url}"], "--exec's value can reach a shell"),
    (["--no-playlist", "--", "--exec", "echo {url}"], "--exec's value can reach a shell"),
    (["--replace-in-metadata", "title", "a", "--", "--exec", "echo {url}"], "--exec's value can reach a shell"),
    # Nor one after such an option, further back, put the walker out of step.
    (["--referer", "-P", "--user-agent", "--", "--exec", "echo {url}"], "--exec's value can reach a shell"),
    (["--referer", "--cookies", "--print-to-file", "A", "--", "--exec", "echo {url}"],
     "--exec's value can reach a shell"),
    (["-O", "-P", "--user-agent", "--", "--exec", "echo {url}"], "--exec's value can reach a shell"),
    # After the program, a value or flags it is one.
    (["--", "--exec", "echo {url}"], None),
    (["-iq", "--", "--exec", "echo {url}"], None),
    (["-o", "%(id)s", "--", "--exec", "echo {url}"], None),
    (["--referer=x", "-P", "/d", "--", "--exec", "echo {url}"], None),
])
def test_an_option_the_table_lacks_never_hides_one_it_has(args, why):
    """Review of #69: the checks read every item naming an option as one."""
    _, error = scripts.parse_command(json.dumps({"needs": "url", "argv": ["yt-dlp", *args, "--", "{url}"]}))
    assert error == why if why is None else error.startswith(why)


@pytest.mark.parametrize("tool, args, why", [
    ("yt-dlp", ["--referer", "-P", "-o", "/tmp/{url}/%(id)s"], "{url} puts a .. in -o's path"),
    ("yt-dlp", ["--js-runtimes", "deno:{root}/{url}"], "{url} puts a .. in --js-runtimes's path"),
    ("gallery-dl", ["-X", "{root}/{url}"], "{url} puts a .. in -X's path"),
    ("gallery-dl", ["--extractors={root}/{url}"], "{url} puts a .. in --extractors's path"),
    ("gallery-dl", ["--cache-file", "{root}/{url}/c.sqlite3"], "{url} puts a .. in --cache-file's path"),
])
def test_a_link_s_dotdot_in_a_path_option_on_either_reading_is_refused(env, tool, args, why):
    script = {"tool": tool, "argv": [tool, *args, "--", "{url}"]}
    assert scripts.parse_command(json.dumps({"needs": "url", "argv": script["argv"]}))[1] is None
    with pytest.raises(jobs.BadRequest, match=re.escape(why)):
        scripts.command(script, scripts.values(script, config.load(), "/m/x", url=DOTDOT))


def test_instaloader_s_latest_stamps_takes_a_value_only_when_one_follows(env):
    """argparse's nargs="?": an option or "--" after it is no value of its."""
    script = {"tool": "instaloader", "argv": ["instaloader", "--latest-stamps", "--dirname-pattern",
                                              "{root}/{target}", "--", "{target}"]}
    with pytest.raises(jobs.BadRequest, match=re.escape("{target} puts a .. in --dirname-pattern's path")):
        scripts.command(script, scripts.values(script, config.load(), "/m/x", ".."))
    vals = scripts.values(script, config.load(), "/m/{x}", "carol")
    assert scripts.command(script, vals)[3] == "/m/{{x}}/carol"
    script["argv"] = ["instaloader", "--latest-stamps", "--", "--dirname-pattern", "{root}/{target}"]
    assert scripts.command(script, vals)[-1] == "/m/{x}/carol"
    script["argv"] = ["instaloader", "--latest-stamps", "{root}/{target}", "--", "x"]
    with pytest.raises(jobs.BadRequest, match=re.escape("{target} puts a .. in --latest-stamps's path")):
        scripts.command(script, scripts.values(script, config.load(), "/m/x", ".."))


@pytest.mark.parametrize("args", [["--post-filter", "{target}"], ["--only-if={target}"],
                                  ["--storyitem-filter", "likes > 0 and {target}"]])
def test_a_placeholder_in_an_instaloader_filter_is_refused(args):
    """instaloader compiles and evaluates the filter against each post."""
    _, error = scripts.parse_command(json.dumps({"needs": "target", "argv": ["instaloader", *args, "--", "{target}"]}))
    assert error.startswith(f"{args[0].partition('=')[0]}'s value can reach Python (instaloader evaluates it)")
    assert "the post's own attributes" in error


# ---------------------------------------------------------------------------
# Hardening (#73)
# ---------------------------------------------------------------------------

def test_a_shell_script_runs_the_bytes_that_were_checked(client, folder, runner, env, monkeypatch):
    """Swapped after its SHA-256 was checked, right before it starts: what
    was checked runs, never the new file."""
    out = env["tmp"] / "out.txt"
    path = write(folder, "swap.sh", f"#!/bin/sh\n# needs: none\necho checked > {out}\n", 0o755)
    say = scripts._say

    def swap_after_check(script, vals, note):
        got = say(script, vals, note)
        swapped = folder / "swapped.tmp"
        swapped.write_text(f"#!/bin/sh\n# needs: none\necho swapped > {out}\n")
        swapped.chmod(0o755)
        os.replace(swapped, path)
        return got

    monkeypatch.setattr(scripts, "_say", swap_after_check)
    job = run(client, "swap")["job"]
    assert ended(job["id"])["state"] == "done"
    assert out.read_text() == "checked\n"


def test_a_shell_scripts_dollar_zero_and_fv_script(client, folder, runner, env):
    out = env["tmp"] / "out.txt"
    path = write(folder, "who.sh", f"#!/bin/sh\n# needs: none\nprintf '%s\\n' \"$0\" \"$FV_SCRIPT\" > {out}\n", 0o755)
    job = run(client, "who")["job"]
    assert ended(job["id"])["state"] == "done"
    zero, fv_script = out.read_text().splitlines()
    assert re.fullmatch(r"/dev/fd/\d+", zero) and fv_script == str(path)
    assert f"[feedvault] FV_SCRIPT={path}" in log_of(client, job["id"])


def test_the_kernels_reading_of_a_shebang():
    assert jobs._interpreter(b"#!/bin/sh\necho") == ["/bin/sh"]
    assert jobs._interpreter(b"#! /usr/bin/env  python3 -u \n") == ["/usr/bin/env", "python3 -u"]
    assert jobs._interpreter(b"#!/bin/sh\t-e\n") == ["/bin/sh", "-e"]
    for bad in (b"echo hi\n", b"#!sh\n", b"#!\n", b"#!/bin/sh\0x\n"):
        assert jobs._interpreter(bad) is None


@pytest.fixture
def deep(tmp_path, monkeypatch):
    """A config folder two levels below ``tmp_path/top``, its scripts folder
    holding one command."""
    top = tmp_path / "top"
    (top / "mid" / "cfg" / "scripts").mkdir(parents=True)
    for d in (top, top / "mid", top / "mid" / "cfg", top / "mid" / "cfg" / "scripts"):
        d.chmod(0o755)
    monkeypatch.setenv("FEEDVAULT_CONFIG", str(top / "mid" / "cfg" / "config.json"))
    write(top / "mid" / "cfg" / "scripts", "mine.json", COMMAND)
    yield top
    top.chmod(0o755)


def test_a_folder_above_the_scripts_folder_writable_by_others_is_refused(env, deep):
    assert scripts.get("mine")["refused"] is None
    deep.chmod(0o777)
    refused = scripts.listing()["dir_refused"]
    assert refused == f"a folder above it ({deep}) is writable by group or others (chmod go-w '{deep}')"
    assert scripts.get("mine") is None
    with pytest.raises(jobs.BadRequest, match="writable by group or others"):
        scripts.runnable("mine")
    deep.chmod(0o1777)                         # sticky: nobody else can swap what is ours in it
    assert scripts.get("mine")["refused"] is None


def test_a_folder_above_the_scripts_folder_of_another_user_is_refused(env, deep, monkeypatch):
    real_stat = os.stat

    def theirs(path, *a, **k):
        st = real_stat(path, *a, **k)
        if os.fspath(path) == str(deep):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, os.getuid() + 1, *st[5:10]))
        return st

    monkeypatch.setattr(scripts.os, "stat", theirs)
    assert scripts.listing()["dir_refused"] == f"a folder above it ({deep}) belongs to another user"


def test_a_folder_above_the_scripts_folder_through_a_symlink_is_checked_where_it_leads(env, deep, tmp_path,
                                                                                      monkeypatch):
    # cfg is reached through a link in a safe folder; where it leads is open to others.
    (tmp_path / "safe").mkdir(mode=0o755)
    (tmp_path / "safe" / "cfg").symlink_to(deep / "mid" / "cfg")
    monkeypatch.setenv("FEEDVAULT_CONFIG", str(tmp_path / "safe" / "cfg" / "config.json"))
    assert scripts.get("mine")["refused"] is None
    (deep / "mid").chmod(0o777)
    try:
        assert scripts.listing()["dir_refused"] == \
            f"a folder above it ({deep / 'mid'}) is writable by group or others (chmod go-w '{deep / 'mid'}')"
    finally:
        (deep / "mid").chmod(0o755)


@pytest.mark.parametrize("headers", FOREIGN)
def test_the_userscripts_origin_cannot_schedule_a_script(client, folder, runner, source, headers, monkeypatch):
    """Audit 3: a schedule set from another site would run the source's
    script at the next tick. A source is changed from the dashboard only,
    with a script or without (#112: the userscript never changes one)."""
    for state in ("_notes", "_held", "_last"):
        monkeypatch.setattr(scheduler, state, {})
    attach(client, source["id"], "mine")
    for options in ({"schedule": "hourly"}, {"full_history": True}, {"script": None}):
        r = client.post(f"/api/sources/{source['id']}", json={"options": options}, headers={**H, **headers})
        assert r.status_code == 403 and "own dashboard" in r.get_json()["error"], options
    assert client.get(f"/api/sources/{source['id']}", headers=H).get_json()["options"]["schedule"] in (None, "off")
    assert scheduler.tick() == []
    assert runner.runs() == [] and jobs.active() == []
    other = add_source(client, "dana.draws").get_json()["source"]
    r = client.post(f"/api/sources/{other['id']}", json={"options": {"schedule": "daily"}}, headers={**H, **headers})
    assert r.status_code == 403 and "own dashboard" in r.get_json()["error"]
    assert client.get(f"/api/sources/{other['id']}", headers=H).get_json()["options"]["schedule"] in (None, "off")


@pytest.mark.parametrize("headers", FOREIGN)
def test_the_userscripts_origin_cannot_rename_or_delete_a_script_source(client, folder, runner, source, headers):
    """Review of audit 3: a rename changes the target the script gets (and
    drops the scheduler's hold); a delete removes it. Dashboard only, for
    a source without a script too (#112)."""
    attach(client, source["id"], "mine")
    for method, url in (("post", f"/api/sources/{source['id']}/rename"), ("delete", f"/api/sources/{source['id']}/rename"),
                        ("delete", f"/api/sources/{source['id']}")):
        r = getattr(client, method)(url, json={"to": "x"}, headers={**H, **headers})
        assert r.status_code == 403 and "own dashboard" in r.get_json()["error"], url
    assert client.get(f"/api/sources/{source['id']}", headers=H).status_code == 200
    other = add_source(client, "dana.draws").get_json()["source"]
    r = client.delete(f"/api/sources/{other['id']}", headers={**H, **headers})
    assert r.status_code == 403 and "own dashboard" in r.get_json()["error"]
    assert client.get(f"/api/sources/{other['id']}", headers=H).status_code == 200
