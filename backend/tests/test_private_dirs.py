"""FeedVault's own folders under a umask of 002 (#75): made 0700 whatever the
umask, tightened on start when they are FeedVault's and too open, and a
refusal says the chmod to run. The conftest ``umask`` fixture pins 022:
each test here sets 002 itself."""
import json
import os
import stat

import pytest

import config
import db
import scripts
import thumbs
from conftest import H

COMMAND = {"name": "Mine", "description": "", "needs": "url", "rescan": None,
           "argv": ["yt-dlp", "--", "{url}"]}


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture
def umask002():
    old = os.umask(0o002)
    yield
    os.umask(old)


def test_make_private_dir_makes_each_missing_folder_0700_and_leaves_the_rest(tmp_path, umask002):
    there = tmp_path / "there"
    there.mkdir()
    there.chmod(0o755)
    made = config.make_private_dir(str(there / "a" / "b" / "c"))
    assert made == str(there / "a" / "b" / "c")
    assert [mode(there / p) for p in ("a", "a/b", "a/b/c")] == [0o700] * 3
    assert mode(there) == 0o755                    # already there: left as it is
    config.make_private_dir(str(there / "a"))      # twice: fine
    (there / "file").write_text("")
    with pytest.raises(FileExistsError):
        config.make_private_dir(str(there / "file"))


def test_a_fresh_config_folder_lists_scripts_with_no_chmod(env, client, tmp_path, monkeypatch, umask002):
    monkeypatch.setenv("FEEDVAULT_CONFIG", str(tmp_path / "fresh" / "feedvault" / "config.json"))
    config.save(config.load())
    assert mode(tmp_path / "fresh" / "feedvault") == 0o700
    # The Scripts page makes the folder, so the first script is listed as it is.
    body = client.get("/api/scripts", headers=H).get_json()
    folder = tmp_path / "fresh" / "feedvault" / "scripts"
    assert body["dir"] == str(folder) and body["dir_refused"] is None
    assert mode(folder) == 0o700
    (folder / "mine.json").write_text(json.dumps(COMMAND))
    (folder / "mine.json").chmod(0o644)
    assert scripts.get("mine")["refused"] is None


def test_the_data_directory_and_its_folders_are_private(env, tmp_path, umask002):
    data = tmp_path / "fresh-data"
    db.init(str(data / "feedvault.db"))
    assert mode(data) == 0o700
    src = env["media"] / "a.png"
    from PIL import Image
    Image.new("RGB", (8, 8)).save(src)
    out = thumbs.thumb_for(str(data), {"path": str(src), "kind": "image", "poster_path": None})
    assert mode(os.path.dirname(out)) == 0o700 and mode(data / "thumbs") == 0o700
    cfg = config.load()
    cfg["data_directory"] = str(data)
    config.save(cfg)
    folder = scripts.scripts_dir()
    os.makedirs(folder, exist_ok=True)
    os.chmod(folder, 0o755)
    with open(os.path.join(folder, "mine.json"), "w") as f:
        json.dump(COMMAND, f)
    os.chmod(os.path.join(folder, "mine.json"), 0o644)
    spec = scripts._build({"script": "mine", "url": "https://example.com/x"})
    assert spec["cwd"] == str(data / "scripts") and mode(data / "scripts") == 0o700


def test_feedvaults_own_folders_are_tightened_on_start(env, tmp_path, monkeypatch, capsys, umask002):
    home = tmp_path / "xdg"
    (home / "feedvault" / "scripts").mkdir(parents=True)  # as FeedVault made them under 002
    for d in (home, home / "feedvault", home / "feedvault" / "scripts"):
        d.chmod(0o775)
    monkeypatch.delenv("FEEDVAULT_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    assert "writable by group or others" in scripts.listing()["dir_refused"]
    changed = scripts.tighten()
    assert changed == [(str(home / "feedvault"), 0o775), (str(home / "feedvault" / "scripts"), 0o775)]
    assert mode(home / "feedvault") == 0o755 and mode(home / "feedvault" / "scripts") == 0o755
    assert mode(home) == 0o775                      # above them: never touched
    out = capsys.readouterr().out
    assert f"[scripts] {home / 'feedvault'} was writable by group or others (775): now 755" in out
    home.chmod(0o755)
    assert scripts.listing()["dir_refused"] is None
    assert scripts.tighten() == []                  # nothing left to do: no line


def test_a_folder_that_is_not_feedvaults_is_not_tightened(env, tmp_path, monkeypatch, umask002):
    # A config file set elsewhere: its folder may be the user's own, shared.
    mine = tmp_path / "users-folder"
    (mine / "scripts").mkdir(parents=True)
    mine.chmod(0o775)
    (mine / "scripts").chmod(0o775)
    monkeypatch.setenv("FEEDVAULT_CONFIG", str(mine / "config.json"))
    assert scripts.tighten() == [(str(mine / "scripts"), 0o775)]
    assert mode(mine) == 0o775
    # Still refused, with the exact command to run.
    assert scripts.listing()["dir_refused"] == \
        f"its parent folder ({mine}) is writable by group or others (chmod go-w '{mine}')"
    mine.chmod(0o755)

    # A scripts folder that is a symlink, or someone else's: left alone.
    (mine / "scripts").rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    elsewhere.chmod(0o777)
    (mine / "scripts").symlink_to(elsewhere)
    assert scripts.tighten() == [] and mode(elsewhere) == 0o777
    (mine / "scripts").unlink()
    (mine / "scripts").mkdir()
    (mine / "scripts").chmod(0o777)
    real = os.getuid()
    monkeypatch.setattr(config.os, "getuid", lambda: real + 1)
    assert scripts.tighten() == [] and mode(mine / "scripts") == 0o777


def test_a_refusal_says_the_chmod_to_run(env, client, tmp_path, umask002):
    folder = env["tmp"] / "scripts"
    folder.mkdir()
    folder.chmod(0o755)
    (folder / "open.json").write_text(json.dumps(COMMAND))   # 0664 under 002
    (folder / "noexec.sh").write_text("#!/bin/sh\n# needs: none\n")
    (folder / "noexec.sh").chmod(0o644)
    got = {s["id"]: s["refused"] for s in client.get("/api/scripts", headers=H).get_json()["scripts"]}
    assert got["open"] == f"writable by group or others (chmod go-w '{folder / 'open.json'}')"
    assert got["noexec"] == f"not executable (chmod u+x '{folder / 'noexec.sh'}')"
    # A tool path in Settings, in a folder FeedVault did not make (~/.local/bin, say).
    bin_dir = tmp_path / "it's bin"
    bin_dir.mkdir()                                # 0775 under 002
    tool = bin_dir / "yt-dlp"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    r = client.post("/api/config", json={"tools": {"yt-dlp": str(tool)}}, headers=H).get_json()
    assert r["error"] == (f"yt-dlp: its folder ({bin_dir}) is writable by group or others "
                          f"(chmod go-w '{tmp_path}/it'\\''s bin')")


def test_a_folder_that_cannot_be_chmodded_does_not_stop_feedvault(env, tmp_path, monkeypatch, umask002):
    """Review: a read-only mount, CIFS: start goes on, the folder is made all the same."""
    mine = tmp_path / "ro"
    (mine / "scripts").mkdir(parents=True)
    (mine / "scripts").chmod(0o775)
    monkeypatch.setenv("FEEDVAULT_CONFIG", str(mine / "config.json"))

    def refused(fd, mode):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(config.os, "fchmod", refused)
    assert scripts.tighten() == []
    mkdir = os.mkdir
    monkeypatch.setattr(config.os, "mkdir", lambda p, mode: mkdir(p, 0o777))   # a mode it does not keep
    config.make_private_dir(str(tmp_path / "cifs" / "data"))
    assert os.path.isdir(tmp_path / "cifs" / "data")


def test_a_root_folder_refusal_says_sudo(env, tmp_path, monkeypatch):
    top = tmp_path / "srv"
    (top / "tools").mkdir(parents=True)
    top.chmod(0o775)
    (top / "tools").chmod(0o755)
    tool = top / "tools" / "yt-dlp"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    real_stat = os.stat

    def roots(path, *a, **k):
        st = real_stat(path, *a, **k)
        if os.fspath(path) == str(top):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 0, *st[5:10]))
        return st

    monkeypatch.setattr(config.os, "stat", roots)
    assert config.tool_refused(str(tool)) == \
        f"its parent folder ({top}) is writable by group or others (sudo chmod go-w '{top}')"
    top.chmod(0o755)
