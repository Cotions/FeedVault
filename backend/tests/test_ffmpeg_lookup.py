"""ffmpeg and ffprobe are found as the downloaders are (#75): the path set in
Settings, else PATH's absolute folders only (jobs.tool_path, jobs._which),
never a bare name run through PATH, nor a relative result."""
import os
import sys

from PIL import Image

import config
import hashing
import thumbs
from conftest import H


def fake(path, body):
    path.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    path.chmod(0o755)
    return path


def video_row(env):
    src = env["media"] / "clip.mp4"
    src.write_bytes(b"not really a video")
    return {"path": str(src), "kind": "video", "poster_path": None}


def test_ffmpeg_and_ffprobe_in_a_relative_path_folder_are_not_found(env, tmp_path, monkeypatch):
    here = tmp_path / "cwd"
    here.mkdir()
    fake(here / "ffmpeg", "sys.exit(0)")
    fake(here / "ffprobe", "print('640x360')")
    monkeypatch.chdir(here)
    monkeypatch.setenv("PATH", os.pathsep.join([".", ""]))
    assert not thumbs.have_ffmpeg() and thumbs.ffmpeg_path() is None
    assert hashing.video_size(str(here / "x.mp4")) is None      # nothing run: no ffprobe
    assert thumbs.ffprobe_path() is None
    assert thumbs.thumb_for(str(tmp_path / "data"), video_row(env)) is None


def test_the_ffmpeg_set_in_settings_makes_the_frame(env, client, tmp_path):
    png = tmp_path / "frame.png"
    Image.new("RGB", (16, 9), "red").save(png)
    tools = tmp_path / "tools"
    tools.mkdir(mode=0o755)
    exe = fake(tools / "ffmpeg", f"import shutil\nshutil.copy({str(png)!r}, sys.argv[-1])")
    assert client.post("/api/config", json={"tools": {"ffmpeg": str(exe)}}, headers=H).get_json()["ok"]
    out = thumbs.thumb_for(str(tmp_path / "data"), video_row(env))
    assert out is not None and Image.open(out).size[0] > 0
    assert thumbs.ffmpeg_path() == str(exe)


def test_a_refused_ffmpeg_in_settings_is_not_run(env, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir(mode=0o755)
    exe = fake(tools / "ffmpeg", "sys.exit(0)")
    cfg = config.load()
    cfg["tools"] = {"ffmpeg": str(exe)}
    config.save(cfg)
    exe.chmod(0o777)                           # someone else could swap it by now
    assert not thumbs.have_ffmpeg() and thumbs.ffmpeg_path() is None
    assert thumbs.thumb_for(str(tmp_path / "data"), video_row(env)) is None


def test_ffprobe_on_path_is_run_by_its_absolute_path(env, tool_guard, tmp_path):
    exe = fake(tool_guard.dir / "bin" / "ffprobe", "print('640x360')")
    assert thumbs.ffprobe_path() == str(exe)
    assert hashing.video_size(str(tmp_path / "x.mp4")) == (640, 360)
    assert os.path.realpath(exe) in tool_guard.runs


def test_no_ffmpeg_now_is_not_a_clip_it_failed_on(env, tmp_path, monkeypatch):
    """Review: the lookup is made once; none found leaves no .failed marker."""
    row = video_row(env)
    data = str(tmp_path / "data")
    assert thumbs.thumb_for(data, row) is None
    out = thumbs._cache_path(data, row["path"])
    assert not os.path.exists(out + ".failed")
    monkeypatch.setattr(thumbs, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(thumbs, "_video_frame", lambda src, out, ffmpeg: False)
    assert thumbs.thumb_for(data, row) is None and os.path.exists(out + ".failed")


def test_ffprobe_beside_the_ffmpeg_set_in_settings_is_used(env, client, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir(mode=0o755)
    exe = fake(tools / "ffmpeg", "sys.exit(0)")
    probe = fake(tools / "ffprobe", "print('320x240')")
    assert client.post("/api/config", json={"tools": {"ffmpeg": str(exe)}}, headers=H).get_json()["ok"]
    assert thumbs.ffprobe_path() == str(probe)
    assert hashing.video_size(str(tmp_path / "x.mp4")) == (320, 240)
    probe.chmod(0o777)                         # someone else could swap it: not that one
    assert thumbs.ffprobe_path() is None
