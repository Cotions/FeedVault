"""Scripts (#5): built-in templates, files on disk read strictly, runs."""
import json
import os

import pytest

from conftest import H

import scripts


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
