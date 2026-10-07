"""scripts/make_demo.py's root (#111): it builds only in a new or empty
folder or a vault it made (the .feedvault-demo marker), and chmods only the
folders it creates. Everything here is in the test's tmp dir."""
import importlib.util
import os
import stat
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "make_demo.py")


@pytest.fixture
def make_demo():
    spec = importlib.util.spec_from_file_location("make_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_a_folder_it_did_not_make_is_left_alone(make_demo, tmp_path, monkeypatch):
    root = tmp_path / "photos"
    (root / "media").mkdir(parents=True)
    (root / "media" / "keep").write_text("mine")
    (root / "notes.txt").write_text("mine too")
    root.chmod(0o755)
    monkeypatch.setattr(sys, "argv", ["make_demo.py", str(root)])
    with pytest.raises(SystemExit) as e:
        make_demo.main()
    assert "no .feedvault-demo marker" in str(e.value) and "touch" in str(e.value)
    assert (root / "media" / "keep").read_text() == "mine"
    assert sorted(os.listdir(root)) == ["media", "notes.txt"]
    assert mode(root) == 0o755


def test_a_file_is_refused(make_demo, tmp_path):
    (tmp_path / "f").write_text("x")
    assert "not a folder" in make_demo.prepare_root(str(tmp_path / "f"))
    assert (tmp_path / "f").read_text() == "x"


def test_a_new_root_is_made_private_and_marked(make_demo, tmp_path):
    root = tmp_path / "a" / "b" / "vault"
    assert make_demo.prepare_root(str(root)) is None
    for p in (tmp_path / "a", tmp_path / "a" / "b", root, root / "bin"):
        assert mode(p) == 0o700, p
    assert (root / ".feedvault-demo").is_file()
    # Its own vault again (a rebuild, as testapp.sh --demo or the browser tests do).
    (root / "media").mkdir()
    (root / "config.json").write_text("{}")
    assert make_demo.prepare_root(str(root)) is None


def test_an_empty_folder_is_taken_but_not_chmodded(make_demo, tmp_path, capsys):
    root = tmp_path / "empty"
    root.mkdir()
    root.chmod(0o775)
    assert make_demo.prepare_root(str(root)) is None
    assert mode(root) == 0o775
    assert (root / ".feedvault-demo").is_file() and mode(root / "bin") == 0o700
    assert "writable by group or others" in capsys.readouterr().err


def test_a_symlinked_marker_does_not_count(make_demo, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (tmp_path / "elsewhere").write_text("")
    (root / ".feedvault-demo").symlink_to(tmp_path / "elsewhere")
    assert "no .feedvault-demo marker" in make_demo.prepare_root(str(root))
