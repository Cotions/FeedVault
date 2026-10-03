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
