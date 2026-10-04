"""config.json and userdata/*.json writes (#73): 0600, a unique temp file
in the same folder (never through a symlink), fsynced, renamed, the folder
fsynced."""
import json
import os
import stat

import pytest

import config
import db
import userdata


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def save_config(env):
    config.save(config.load())
    return config.config_path()


def export_decisions(env):
    data = str(env["tmp"] / "data")
    userdata.export(db.connect(), "decisions", data)
    return userdata.path(data, "decisions")


WRITERS = [save_config, export_decisions]


@pytest.mark.parametrize("write", WRITERS)
@pytest.mark.parametrize("umask", [0o002, 0o022, 0o000])
def test_written_owner_only_whatever_the_umask(env, write, umask):
    old = os.umask(umask)
    try:
        path = write(env)
    finally:
        os.umask(old)
    assert mode(path) == 0o600


@pytest.mark.parametrize("write", WRITERS)
def test_a_file_too_open_is_tightened_on_the_next_save(env, write):
    path = write(env)
    os.chmod(path, 0o664)
    write(env)
    assert mode(path) == 0o600


@pytest.mark.parametrize("write", WRITERS)
def test_a_symlink_at_the_old_temp_name_is_never_followed(env, write, tmp_path):
    path = write(env)
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    os.symlink(victim, path + ".tmp")
    write(env)
    assert victim.read_text() == "untouched"
    json.loads(open(path).read())             # the real file was written
    # No temp file is left beside it.
    left = [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp") and n != os.path.basename(path) + ".tmp"]
    assert left == []


@pytest.mark.parametrize("write", WRITERS)
def test_the_file_then_its_folder_are_fsynced_around_the_rename(env, write, monkeypatch):
    path = write(env)
    events = []
    fsync, replace = os.fsync, os.replace

    def logged_fsync(fd):
        events.append(("fsync", "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"))
        fsync(fd)

    def logged_replace(a, b):
        events.append(("replace", os.path.dirname(a) == os.path.dirname(b), b))
        replace(a, b)

    monkeypatch.setattr(os, "fsync", logged_fsync)
    monkeypatch.setattr(os, "replace", logged_replace)
    write(env)
    assert events == [("fsync", "file"), ("replace", True, path), ("fsync", "dir")]


def test_a_failed_write_leaves_the_old_file_and_no_temp_file(env, monkeypatch):
    path = save_config(env)
    before = open(path).read()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(OSError):
        config.save(config.load())
    assert open(path).read() == before
    assert [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")] == []


def test_two_settings_saves_at_once_keep_both_changes(env, client, monkeypatch):
    """POST /api/config: load, edit and save under one lock (#73)."""
    import threading

    import app as app_module
    from conftest import H
    both_loaded = threading.Barrier(2, timeout=1)
    load = config.load

    def slow_load():
        cfg = load()
        try:                                   # on main both get here with the same file
            both_loaded.wait()
        except threading.BrokenBarrierError:   # one at a time: the other never comes
            pass
        return cfg

    monkeypatch.setattr(config, "load", slow_load)
    results = {}

    def post(key, value):
        results[key] = app_module.app.test_client().post(
            "/api/config", json={key: value}, headers={**H, "Host": "localhost:3380"}).get_json()

    threads = [threading.Thread(target=post, args=a) for a in (("check_updates", True), ("youtube_max_seconds", 77))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results["check_updates"]["ok"] and results["youtube_max_seconds"]["ok"]
    monkeypatch.setattr(config, "load", load)
    saved = config.load()
    assert saved.get("check_updates") is True and saved.get("youtube_max_seconds") == 77
