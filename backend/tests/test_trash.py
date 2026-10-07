import os

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
