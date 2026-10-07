import json
import os
import signal

from conftest import H
from fakes import owner, write_post, png

import scanner

ALICE = owner("alice.example", 111, "Alice Example")


def scan(env):
    return scanner.scan(env["roots"])


def trash_root(env):
    return env["media"] / ".feedvault-trash"


def test_delete_post_moves_every_file_to_trash(env, client):
    write_post(env["media"] / "alice", "P1", 1717243200, ALICE, "video", caption="hi")
    scan(env)
    before = sorted(os.listdir(env["media"] / "alice"))
    assert len(before) == 4                                          # jpg, mp4, txt, json
    r = client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["ok"] and r["posts"] == ["instagram:P1"] and r["files"] == 4 and r["errors"] == []
    assert os.listdir(env["media"] / "alice") == []
    assert sorted(os.listdir(trash_root(env) / "alice")) == before   # same layout in the trash
    assert client.get("/api/posts/instagram/P1", headers=H).status_code == 404
    assert client.get("/api/posts?q=hi", headers=H).get_json()["total"] == 0
    # a rescan does not bring it back from the trash
    r = scan(env)
    assert (r["added"], r["unmatched"]) == (0, 0)


def test_delete_one_carousel_item(env, client):
    write_post(env["media"], "C1", 1717243200, ALICE, "carousel", slides=[False, False, False])
    scan(env)
    media = client.get("/api/posts/instagram/C1", headers=H).get_json()["media"]
    r = client.post("/api/delete", json={"media": [media[1]["id"]]}, headers=H).get_json()
    assert r["media"] == [media[1]["id"]] and r["posts"] == [] and r["files"] == 1
    left = client.get("/api/posts/instagram/C1", headers=H).get_json()["media"]
    assert [m["idx"] for m in left] == [1, 3]
    assert scan(env)["added"] == 0


def test_deleting_last_item_removes_post(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scan(env)
    mid = client.get("/api/posts/instagram/P1", headers=H).get_json()["media"][0]["id"]
    r = client.post("/api/delete", json={"media": [mid]}, headers=H).get_json()
    assert r["posts"] == ["instagram:P1"] and r["media"] == [mid]
    assert not any(f.endswith(".json") for f in os.listdir(env["media"]))   # metadata went too


def test_filename_only_first_item_delete_keeps_post(env, client):
    prof = env["media"] / "somehandle"
    prof.mkdir()
    for i in (1, 2):
        png(str(prof / f"somehandle-2024-01-01-AAAAAAAAAAA_{i}.jpg"))
    scan(env)
    media = client.get("/api/posts/instagram/AAAAAAAAAAA", headers=H).get_json()["media"]
    client.post("/api/delete", json={"media": [media[0]["id"]]}, headers=H)
    r = scan(env)
    assert (r["added"], r["missing"]) == (0, 0)
    post = client.get("/api/posts/instagram/AAAAAAAAAAA", headers=H).get_json()
    assert [m["idx"] for m in post["media"]] == [2] and post["missing"] is False


def test_name_clash_in_trash_keeps_both(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    write_post(env["media"], "P1", 1717243200, ALICE, "image")      # downloaded again
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    names = sorted(os.listdir(trash_root(env)))
    assert any("(1)" in n for n in names) and len([n for n in names if n.endswith(".jpg")]) == 2


def test_refuses_files_outside_roots(env, client, tmp_path):
    outside = tmp_path / "elsewhere"
    write_post(outside, "P1", 1717243200, ALICE, "image")
    scanner.scan([str(outside)])                   # indexed from a root no longer configured
    r = client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["posts"] == [] and r["errors"] and r["errors"][0]["error"] == "outside the media roots"
    assert os.path.exists(next(outside.glob("*.jpg")))


def test_missing_post_is_just_dropped(env, client):
    base = write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scan(env)
    for ext in (".jpg", ".json"):
        os.remove(base + ext)
    scan(env)
    r = client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["posts"] == ["instagram:P1"] and r["files"] == 0


def test_trash_usage_and_empty(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "carousel", slides=[False, False])
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    u = client.get("/api/trash", headers=H).get_json()
    assert u["files"] == 3 and u["bytes"] > 0                        # manifest not counted
    r = client.post("/api/trash/empty", headers=H).get_json()
    assert r["ok"] and r["files"] == 3
    assert not trash_root(env).exists()
    assert client.get("/api/trash", headers=H).get_json()["files"] == 0


def test_delete_requires_header_and_valid_body(client):
    assert client.post("/api/delete", json={"posts": ["x"]}).status_code == 403
    assert client.post("/api/delete", json={"posts": "x"}, headers=H).status_code == 400
    assert client.post("/api/delete", json={}, headers=H).status_code == 400
    assert client.post("/api/trash/empty").status_code == 403


# --- review decisions and undo ------------------------------------------------

def test_keep_decision_filters_and_survives_rescan(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    scan(env)
    r = client.post("/api/review", json={"posts": ["instagram:P1", "instagram:NOPE"], "decision": "keep"},
                    headers=H).get_json()
    assert r["posts"] == ["instagram:P1"]
    ids = lambda qs: [p["post_id"] for p in client.get("/api/posts" + qs, headers=H).get_json()["posts"]]  # noqa: E731
    assert ids("?review=unreviewed") == ["P2"]
    assert ids("?review=kept") == ["P1"]
    assert ids("?order=asc") == ["P1", "P2"]
    scan(env)
    assert ids("?review=kept") == ["P1"]
    s = client.get("/api/stats", headers=H).get_json()
    assert (s["kept"], s["unreviewed"]) == (1, 1)
    post = client.get("/api/posts/instagram/P1", headers=H).get_json()
    assert post["decision"] == "keep"
    client.post("/api/review", json={"posts": ["instagram:P1"], "decision": None}, headers=H)
    assert ids("?review=kept") == []
    assert client.post("/api/review", json={"posts": ["x"], "decision": "maybe"}, headers=H).status_code == 400


def test_undo_trash_restores_post(env, client):
    write_post(env["media"] / "alice", "P1", 1717243200, ALICE, "carousel", slides=[False, True], caption="hi")
    scan(env)
    before = sorted(os.listdir(env["media"] / "alice"))
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["ok"] and r["posts"] == ["instagram:P1"] and r["errors"] == []
    assert sorted(os.listdir(env["media"] / "alice")) == before
    post = client.get("/api/posts/instagram/P1", headers=H).get_json()     # indexed again at once
    assert len(post["media"]) == 2 and post["text"] == "hi"
    assert client.get("/api/trash", headers=H).get_json()["files"] == 0
    assert scan(env)["added"] == 0


def test_a_kept_post_trashed_and_restored_is_kept_again(env, client, monkeypatch):
    # QA pass 4: the trash dropped the post's "keep", so a kept post trashed
    # from the Feed or its page came back from the Trash page unreviewed.
    import userdata
    written = []
    monkeypatch.setattr(userdata, "changed", written.append)
    write_post(env["media"], "P1", 1717243200, ALICE, "carousel", slides=[False, False])
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    write_post(env["media"], "P3", 1717243400, ALICE, "image")
    scan(env)
    client.post("/api/review", json={"posts": ["instagram:P1", "instagram:P3"], "decision": "keep"}, headers=H)
    kept_at = client.get("/api/posts/instagram/P1", headers=H).get_json()
    assert kept_at["decision"] == "keep"
    media = kept_at["media"]
    # P1 item by item (its last item takes the post), P2 never decided, P3 whole
    client.post("/api/delete", json={"media": [media[0]["id"]]}, headers=H)
    client.post("/api/delete", json={"media": [media[1]["id"]]}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P2", "instagram:P3"]}, headers=H)
    assert client.get("/api/stats", headers=H).get_json()["kept"] == 0
    written.clear()
    keys = [e["key"] for e in client.get("/api/trash/items", headers=H).get_json()["entries"]]
    r = client.post("/api/trash/restore", json={"keys": keys}, headers=H).get_json()
    assert r["errors"] == [] and "decided" not in r
    decision = lambda pid: client.get(f"/api/posts/instagram/{pid}", headers=H).get_json()["decision"]  # noqa: E731
    assert [decision(p) for p in ("P1", "P2", "P3")] == ["keep", None, "keep"]
    assert len(client.get("/api/posts/instagram/P1", headers=H).get_json()["media"]) == 2
    assert "decisions" in written                    # decisions.json follows
    # by post id too (the Review screen's undo), and a decision made since stays
    client.post("/api/delete", json={"posts": ["instagram:P3"]}, headers=H)
    write_post(env["media"], "P3", 1717243400, ALICE, "image")     # a sync brought it back
    scan(env)
    client.post("/api/review", json={"posts": ["instagram:P3"], "decision": "keep"}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P3"]}, headers=H)
    client.post("/api/trash/restore", json={"posts": ["instagram:P3"]}, headers=H)
    assert decision("P3") == "keep"


def test_a_deletion_cut_short_is_finished_by_the_next_scan(env, client, monkeypatch):
    # QA pass 4: FeedVault killed during a big Delete: the files of the posts
    # done so far were in the trash, but the index (one commit at the end)
    # still had every post, and the next scans kept them as missing posts.
    import db
    import thumbs
    for i in (1, 2, 3):
        write_post(env["media"] / "alice", f"P{i}", 1717243200 + i, ALICE, "image")
    write_post(env["media"] / "alice", "P4", 1717243300, ALICE, "image")
    scan(env)
    client.post("/api/review", json={"posts": ["instagram:P1"], "decision": "keep"}, headers=H)
    real, moved = thumbs.forget, []

    def killed(data_dir, path):
        if path.endswith("_UTC.json"):            # the metadata, not a side file
            moved.append(path)
            if len(moved) == 2:
                raise SystemExit("killed")      # the second post's files are in the trash
        return real(data_dir, path)
    monkeypatch.setattr(thumbs, "forget", killed)
    try:
        client.post("/api/delete", json={"posts": ["instagram:P1", "instagram:P2", "instagram:P3"]}, headers=H)
    except SystemExit:
        pass
    db.connect().rollback()                     # what was not committed is lost
    monkeypatch.setattr(thumbs, "forget", real)
    assert client.get("/api/stats", headers=H).get_json()["posts"] == 4
    r = scan(env)
    assert r["missing"] == 0
    s = client.get("/api/stats", headers=H).get_json()
    assert (s["posts"], s["missing"], s["kept"]) == (2, 0, 0)
    assert sorted(p["post_id"] for p in client.get("/api/posts", headers=H).get_json()["posts"]) == ["P3", "P4"]
    entries = client.get("/api/trash/items", headers=H).get_json()["entries"]
    assert sorted(e["post"] for e in entries) == ["instagram:P1", "instagram:P2"]
    client.post("/api/trash/restore", json={"keys": [e["key"] for e in entries]}, headers=H)
    s = client.get("/api/stats", headers=H).get_json()
    assert (s["posts"], s["missing"], s["kept"]) == (4, 0, 1)
    # A post merely gone from its folder is still kept as missing.
    for name in os.listdir(env["media"] / "alice"):
        if name.startswith("2024-06-01_12-01-40"):
            os.remove(env["media"] / "alice" / name)
    assert scan(env)["missing"] == 1


def test_undo_single_item(env, client):
    write_post(env["media"], "C1", 1717243200, ALICE, "carousel", slides=[False, False, False])
    scan(env)
    mid = client.get("/api/posts/instagram/C1", headers=H).get_json()["media"][2]["id"]
    client.post("/api/delete", json={"media": [mid]}, headers=H)
    client.post("/api/trash/restore", json={"posts": ["instagram:C1"]}, headers=H)
    assert len(client.get("/api/posts/instagram/C1", headers=H).get_json()["media"]) == 3


def test_restore_only_latest_deletion(env, client):
    write_post(env["media"], "C1", 1717243200, ALICE, "carousel", slides=[False, False, False])
    scan(env)
    media = client.get("/api/posts/instagram/C1", headers=H).get_json()["media"]
    client.post("/api/delete", json={"media": [media[0]["id"]]}, headers=H)
    client.post("/api/delete", json={"media": [media[1]["id"]]}, headers=H)
    client.post("/api/trash/restore", json={"posts": ["instagram:C1"]}, headers=H)
    left = client.get("/api/posts/instagram/C1", headers=H).get_json()["media"]
    assert [m["idx"] for m in left] == [2, 3]                  # the second delete undone, not the first


def _trash_twice(env, client, slides):
    """P1 (two images) trashed, then a copy with ``slides`` trashed too."""
    write_post(env["media"] / "alice", "P1", 1717243200, ALICE, "carousel", slides=[False, False])
    scan(env)
    assert client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H).get_json()["posts"]
    write_post(env["media"] / "alice", "P1", 1717243200, ALICE, "carousel", slides=slides)
    scan(env)
    assert client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H).get_json()["posts"]


def entries(client):
    return client.get("/api/trash/items", headers=H).get_json()["entries"]


def test_trashed_again_is_one_entry(env, client):
    import config
    import trash
    _trash_twice(env, client, [False, False])
    first, _ = reversed(entries(client))                       # listed newest first
    assert trash.merge_again(env["roots"], ["instagram:P1"], config.load()["data_directory"]) == ["instagram:P1"]
    [left] = entries(client)
    assert left["key"] == first["key"] and left["items"] == 2 and not left["missing"]
    # Only the first entry's files are left in the trash, and they come back.
    names = sorted(n for n in os.listdir(trash_root(env) / "alice"))
    assert names == ["2024-06-01_12-00-00_UTC.json", "2024-06-01_12-00-00_UTC_1.jpg", "2024-06-01_12-00-00_UTC_2.jpg"]
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["posts"] == ["instagram:P1"] and r["errors"] == [] and r["files"] == 3
    assert entries(client) == []
    assert client.get("/api/posts/instagram/P1", headers=H).status_code == 200


def test_trashed_again_with_other_items_keeps_both(env, client):
    import config
    import trash
    _trash_twice(env, client, [False, False, False])
    assert trash.merge_again(env["roots"], ["instagram:P1"], config.load()["data_directory"]) == []
    assert len(entries(client)) == 2


def test_merge_again_leaves_other_posts_and_single_entries(env, client):
    import config
    import trash
    write_post(env["media"] / "alice", "P2", 1717243300, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    _trash_twice(env, client, [False, False])
    assert trash.merge_again(env["roots"], ["instagram:P2"], config.load()["data_directory"]) == []
    assert len(entries(client)) == 3


# --- #159: restore into a renamed folder, a restore cut short, older lines ---

def manifest(env):
    with open(trash_root(env) / ".manifest.jsonl") as f:
        return [json.loads(line) for line in f]


def write_manifest(env, lines):
    with open(trash_root(env) / ".manifest.jsonl", "w") as f:
        f.writelines(json.dumps(line) + "\n" for line in lines)


def meta_path(pid):
    import db
    row = db.connect().execute("SELECT meta_path FROM posts WHERE id = ?", (pid,)).fetchone()
    return row and row[0]


def test_restore_after_the_account_folder_was_renamed_goes_to_its_new_folder(env, client):
    # It made the old folder again, with that one post in it: the account
    # was split in two folders.
    for i in (1, 2, 3):
        write_post(env["media"] / "alice", f"P{i}", 1717243200 + i, ALICE, "image", caption="hi")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    os.rename(env["media"] / "alice", env["media"] / "alice.new")
    scan(env)
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["errors"] == [] and r["files"] == 3 and r["posts"] == ["instagram:P1"]
    assert r["moved"] == [{"post": "instagram:P1", "from": "alice", "to": "alice.new"}] and r["recreated"] == []
    assert not (env["media"] / "alice").exists()
    assert len(os.listdir(env["media"] / "alice.new")) == 9
    assert os.path.dirname(meta_path("instagram:P1")) == str(env["media"] / "alice.new")
    assert entries(client) == []
    assert (scan(env)["added"], scan(env)["missing"]) == (0, 0)


def test_restore_says_which_folder_it_made_again_when_the_account_has_no_one_folder(env, client):
    write_post(env["media"] / "alice", "P1", 1717243201, ALICE, "image")
    write_post(env["media"] / "alice", "P2", 1717243202, ALICE, "image")
    write_post(env["media"] / "alice-old", "P3", 1717243203, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    os.rename(env["media"] / "alice", env["media"] / "alice.new")
    scan(env)                                   # alice.new and alice-old: which one is not clear
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["errors"] == [] and r["moved"] == []
    assert r["recreated"] == [{"post": "instagram:P1", "folder": "alice"}]
    assert os.path.dirname(meta_path("instagram:P1")) == str(env["media"] / "alice")


def test_restore_never_moves_a_post_into_saved(env, client):
    # #163: the account's only other posts are saved ones. The post goes
    # back to its own folder, not among them.
    saved = env["media"] / "_saved" / "alice"
    write_post(env["media"] / "alice", "P1", 1717243201, ALICE, "image")
    write_post(saved, "P2", 1717243202, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    os.rmdir(env["media"] / "alice")
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["errors"] == [] and r["files"] == 2 and r["moved"] == []
    assert r["recreated"] == [{"post": "instagram:P1", "folder": "alice"}]
    assert os.path.dirname(meta_path("instagram:P1")) == str(env["media"] / "alice")
    assert len(os.listdir(saved)) == 2                               # P2's own files, nothing more


def _restore_when_the_other_folder_leads_to(env, client, target):
    """alice holds P1, alice.new P2. P1 is deleted and alice is gone; then
    alice.new, still indexed, becomes a symlink to ``target`` (made by
    ``target(env)``), its files moved there. Restore P1."""
    write_post(env["media"] / "alice", "P1", 1717243201, ALICE, "image")
    write_post(env["media"] / "alice.new", "P2", 1717243202, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    os.rmdir(env["media"] / "alice")
    dest = target(env)
    dest.mkdir(parents=True)
    for f in os.listdir(env["media"] / "alice.new"):
        os.rename(env["media"] / "alice.new" / f, dest / f)
    os.rmdir(env["media"] / "alice.new")
    os.symlink(dest, env["media"] / "alice.new")
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["errors"] == [] and r["files"] == 2 and r["moved"] == []
    assert r["recreated"] == [{"post": "instagram:P1", "folder": "alice"}]
    assert os.path.dirname(meta_path("instagram:P1")) == str(env["media"] / "alice")
    assert len(os.listdir(dest)) == 2                                # P2's own files, nothing more


def test_restore_never_moves_a_post_into_saved_through_a_symlink(env, client):
    _restore_when_the_other_folder_leads_to(env, client, lambda env: env["media"] / "_saved" / "alice")


def test_restore_never_moves_a_post_into_the_trash_through_a_symlink(env, client):
    _restore_when_the_other_folder_leads_to(env, client, lambda env: trash_root(env) / "elsewhere")


def test_restore_never_moves_a_post_out_of_the_root_through_a_symlink(env, client):
    _restore_when_the_other_folder_leads_to(env, client, lambda env: env["tmp"] / "outside" / "alice")


def test_restore_into_the_new_folder_never_replaces_a_file(env, client):
    write_post(env["media"] / "alice", "P1", 1717243201, ALICE, "image")
    write_post(env["media"] / "alice", "P2", 1717243202, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    os.rename(env["media"] / "alice", env["media"] / "alice.new")
    base = write_post(env["media"] / "alice.new", "P1", 1717243201, ALICE, "image", caption="downloaded again")
    scan(env)
    with open(base + ".json", "rb") as f:
        again = f.read()
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P1"]}, headers=H).get_json()
    assert r["files"] == 0 and len(r["errors"]) == 2
    assert all("already in" in e["error"] for e in r["errors"])
    with open(base + ".json", "rb") as f:
        assert f.read() == again
    assert not (env["media"] / "alice").exists()
    [entry] = entries(client)                   # still in the trash, whole
    assert entry["post"] == "instagram:P1" and not entry["missing"]
    assert not any("restoring" in line for line in manifest(env))


RESTORE_KILLED = r"""
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import config, db, trash
cfg = config.load()
db.init(config.db_path(cfg))
real, moved, after = os.rename, [0], int(sys.argv[3])
mark = os.sep + ".feedvault-trash" + os.sep

def rename(src, dst):
    out = mark in str(src) and mark not in str(dst)
    if out and after == 0:
        os.kill(os.getpid(), signal.SIGKILL)
    real(src, dst)
    moved[0] += out
    if out and moved[0] == after:
        os.kill(os.getpid(), signal.SIGKILL)

os.rename = rename
trash.restore([sys.argv[2]], cfg["media_roots"], cfg["data_directory"])
"""


def restore_killed(pid, after):
    """Restore ``pid`` in another FeedVault, killed by its PID once
    ``after`` files are back."""
    import subprocess
    import sys
    from conftest import BACKEND
    proc = subprocess.run([sys.executable, "-c", RESTORE_KILLED, BACKEND, pid, str(after)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == -signal.SIGKILL, proc.stderr


def test_a_restore_cut_short_is_finished_by_the_next_scan(env, client):
    # Killed after its renames, before the manifest was rewritten: the lines
    # stayed, the post came back at the next scan new and without its Keep,
    # and the Trash page listed it with its files missing.
    import db
    write_post(env["media"] / "alice", "P1", 1717243201, ALICE, "image", caption="hi")
    write_post(env["media"] / "alice", "P2", 1717243202, ALICE, "image")
    scan(env)
    client.post("/api/review", json={"posts": ["instagram:P1"], "decision": "keep"}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    restore_killed("instagram:P1", len(manifest(env)))      # every file back, then killed
    assert len(os.listdir(env["media"] / "alice")) == 5
    assert scan(env)["added"] == 0              # indexed by the restore finished, not found new
    assert entries(client) == [] and manifest(env) == []
    post = client.get("/api/posts/instagram/P1", headers=H).get_json()
    assert post["decision"] == "keep"
    assert db.connect().execute("SELECT first_seen FROM posts WHERE id = 'instagram:P1'").fetchone()[0] == 0


def test_a_restore_killed_halfway_keeps_the_rest_in_the_trash(env, client):
    write_post(env["media"] / "alice", "P1", 1717243201, ALICE, "image", caption="hi")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    restore_killed("instagram:P1", 0)           # killed before any file moved
    assert os.listdir(env["media"] / "alice") == []
    assert all("restoring" in line for line in manifest(env))     # marked, not dropped
    scan(env)                                   # the next scan (one at startup) clears the marks
    assert not any("restoring" in line for line in manifest(env)) and len(manifest(env)) == 3
    [entry] = entries(client)
    assert not entry["missing"] and entry["items"] == 1
    restore_killed("instagram:P1", 1)           # one file back, then killed
    scan(env)
    assert len(manifest(env)) == 2 and not any("restoring" in line for line in manifest(env))
    [entry] = entries(client)
    assert not entry["missing"]
    r = client.post("/api/trash/restore", json={"keys": [entry["key"]]}, headers=H).get_json()
    assert r["errors"] == [] and r["files"] == 2
    assert entries(client) == [] and len(os.listdir(env["media"] / "alice")) == 3
    assert client.get("/api/posts/instagram/P1", headers=H).status_code == 200


def test_restore_says_when_a_posts_decision_is_not_known(env, client):
    # Lines written before #155 carry no decision: such a post comes back
    # undecided, and may have been a Keep. The report says so.
    for i in (1, 2, 3, 4):
        write_post(env["media"], f"P{i}", 1717243200 + i, ALICE, "image")
    scan(env)
    client.post("/api/review", json={"posts": ["instagram:P1", "instagram:P3"], "decision": "keep"}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P1", "instagram:P2", "instagram:P3", "instagram:P4"]},
                headers=H)
    old = ("instagram:P3", "instagram:P4")     # as an older FeedVault wrote them
    write_manifest(env, [{k: v for k, v in line.items() if k not in ("decision", "decided_at")}
                         if line["post"] in old else line for line in manifest(env)])
    keys = [e["key"] for e in entries(client)]
    r = client.post("/api/trash/restore", json={"keys": keys}, headers=H).get_json()
    assert r["errors"] == [] and len(r["posts"]) == 4
    assert r["decision_unknown"] == ["instagram:P3", "instagram:P4"]
    decision = lambda pid: client.get(f"/api/posts/instagram/{pid}", headers=H).get_json()["decision"]  # noqa: E731
    assert [decision(p) for p in ("P1", "P2", "P3", "P4")] == ["keep", None, None, None]
    # One decided since it was trashed is not unknown.
    client.post("/api/delete", json={"posts": ["instagram:P3"]}, headers=H)
    write_manifest(env, [{k: v for k, v in line.items() if k != "decision"} for line in manifest(env)])
    write_post(env["media"], "P3", 1717243203, ALICE, "image")        # a sync brought it back
    scan(env)
    client.post("/api/review", json={"posts": ["instagram:P3"], "decision": "keep"}, headers=H)
    os.remove(next(p for p in (env["media"]).iterdir() if p.name.endswith("12-00-03_UTC.json")))
    os.remove(next(p for p in (env["media"]).iterdir() if p.name.endswith("12-00-03_UTC.jpg")))
    r = client.post("/api/trash/restore", json={"posts": ["instagram:P3"]}, headers=H).get_json()
    assert r["files"] == 2 and r["decision_unknown"] == []
