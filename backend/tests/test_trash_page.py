"""The Trash page API: listing trashed entries, restore by key, purge."""
import json
import os
import threading

from conftest import H
from fakes import owner, write_post, png

import config
import scanner
import thumbs
import trash

ALICE = owner("alice.example", 111, "Alice Example")
BOB = owner("bob.example", 222, "Bob Example")


def scan(env):
    return scanner.scan(env["roots"])


def trash_root(env):
    return env["media"] / ".feedvault-trash"


def manifest(env):
    with open(trash_root(env) / ".manifest.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def items(client, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get(f"/api/trash/items?{qs}", headers=H)
    assert r.status_code == 200
    return r.get_json()


def media_of(client, post_id):
    return client.get(f"/api/posts/instagram/{post_id}", headers=H).get_json()["media"]


def data_dir():
    return config.load()["data_directory"]


# --- manifest lines and listing ----------------------------------------------

def test_delete_writes_page_fields(env, client):
    write_post(env["media"] / "alice", "P1", 1717243200, ALICE, "video", caption="hi")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    lines = manifest(env)
    assert {line["role"] for line in lines} == {"media", "poster", "meta", "side"}
    for line in lines:
        assert line["platform"] == "instagram" and line["kind"] == "video"
        assert line["author"] == {"id": "111", "handle": "alice.example"}
        assert line["posted_at"] == 1717243200 and line["items"] == 1 and line["partial"] is False
        assert line["size"] == os.lstat(line["to"]).st_size
    media = next(line for line in lines if line["role"] == "media")
    assert (media["idx"], media["media_kind"]) == (1, "video")


def test_items_groups_by_post_and_batch(env, client):
    write_post(env["media"], "C1", 1717243200, ALICE, "carousel", slides=[False, False, False])
    write_post(env["media"], "P2", 1717243300, BOB, "image")
    scan(env)
    mid = media_of(client, "C1")[1]["id"]
    client.post("/api/delete", json={"media": [mid]}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    r = items(client)
    assert r["total"] == 2 and r["trash"]["entries"] == 2
    assert r["files"] == r["trash"]["files"] == 3                     # 1 slide + image + json
    newest, partial = r["entries"]
    assert newest["post"] == "instagram:P2" and newest["partial"] is False and newest["files"] == 2
    assert (partial["post_id"], partial["partial"], partial["items"], partial["of"]) == ("C1", True, 1, 3)
    assert partial["author"] == {"id": "111", "handle": "alice.example"}
    assert partial["thumb_url"] == f"/trash/{partial['key']}/thumb" and partial["missing"] is False
    assert r["bytes"] == sum(e["bytes"] for e in r["entries"]) > 0
    by_size = sorted(r["entries"], key=lambda e: -e["bytes"])
    assert [a["handle"] for a in r["authors"]] == [e["author"]["handle"] for e in by_size]
    assert r["authors"][0]["entries"] == 1 and r["authors"][0]["platform"] == "instagram"
    assert "/" not in newest["key"] and str(env["media"]) not in json.dumps(r["entries"])

    only = items(client, author="222")
    assert only["total"] == 1 and only["entries"][0]["post"] == "instagram:P2"
    assert only["trash"]["entries"] == 2 and len(only["authors"]) == 2   # whole trash, whatever the filter
    assert items(client, since=2**40)["total"] == 0
    page = items(client, offset=1, limit=1)
    assert page["total"] == 2 and [e["post"] for e in page["entries"]] == ["instagram:C1"]


def test_items_empty_trash(env, client):
    r = items(client)
    assert r["total"] == 0 and r["entries"] == [] and r["trash"] == {"entries": 0, "files": 0, "bytes": 0}


def test_items_cache_follows_the_manifest(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    assert items(client)["total"] == 1
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    assert items(client)["total"] == 2


def test_missing_file_shows_and_purge_drops_its_lines(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    for line in manifest(env):
        if line["role"] == "media":
            os.remove(line["to"])                                     # taken out of the trash by hand
    e = items(client)["entries"][0]
    assert e["missing"] is True
    r = client.post("/api/trash/purge", json={"keys": [e["key"]]}, headers=H).get_json()
    assert r["ok"] and r["entries"] == 1 and r["dropped"] == 1 and r["files"] == 1
    assert manifest(env) == [] and items(client)["total"] == 0


# --- restore by key -------------------------------------------------------------

def test_partial_delete_then_restore_by_key(env, client):
    write_post(env["media"], "C1", 1717243200, ALICE, "carousel", slides=[False, True, False])
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    scan(env)
    media = media_of(client, "C1")
    client.post("/api/delete", json={"media": [media[1]["id"]]}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    entry = next(e for e in items(client)["entries"] if e["partial"])
    assert entry["kind"] == "carousel" and entry["files"] == 2       # the mp4 and its poster
    r = client.post("/api/trash/restore", json={"keys": [entry["key"]]}, headers=H).get_json()
    assert r["ok"] and r["posts"] == ["instagram:C1"] and r["files"] == 2 and r["errors"] == []
    assert [m["idx"] for m in media_of(client, "C1")] == [1, 2, 3]
    left = items(client)
    assert left["total"] == 1 and left["entries"][0]["post"] == "instagram:P2"
    assert scan(env)["added"] == 0


def test_restore_by_key_older_deletion(env, client):
    """Keys reach any deletion, not just a post's latest one."""
    write_post(env["media"], "C1", 1717243200, ALICE, "carousel", slides=[False, False, False])
    scan(env)
    media = media_of(client, "C1")
    client.post("/api/delete", json={"media": [media[0]["id"]]}, headers=H)
    client.post("/api/delete", json={"media": [media[1]["id"]]}, headers=H)
    older = items(client)["entries"][-1]
    client.post("/api/trash/restore", json={"keys": [older["key"]]}, headers=H)
    assert [m["idx"] for m in media_of(client, "C1")] == [1, 3]


def test_restore_moves_the_thumbnail_back(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scan(env)
    mid = media_of(client, "P1")[0]["id"]
    assert client.get(f"/media/{mid}/thumb").status_code == 200
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    line = next(line for line in manifest(env) if line["role"] == "media")
    assert os.path.exists(thumbs._cache_path(data_dir(), line["to"]))   # kept, under the trash path
    assert not os.path.exists(thumbs._cache_path(data_dir(), line["from"]))
    key = items(client)["entries"][0]["key"]
    client.post("/api/trash/restore", json={"keys": [key]}, headers=H)
    assert os.path.exists(thumbs._cache_path(data_dir(), line["from"]))
    assert not os.path.exists(thumbs._cache_path(data_dir(), line["to"]))


# --- purge ------------------------------------------------------------------------

def test_purge_only_touches_listed_files(env, client):
    write_post(env["media"] / "alice", "C1", 1717243200, ALICE, "carousel", slides=[False, False])
    write_post(env["media"] / "bob", "P2", 1717243300, BOB, "image")
    scan(env)
    mid = media_of(client, "C1")[0]["id"]
    client.post("/api/delete", json={"media": [mid]}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    entries = {e["post"]: e for e in items(client)["entries"]}
    bob_lines = [line for line in manifest(env) if line["post"] == "instagram:P2"]
    alice_lines = [line for line in manifest(env) if line["post"] == "instagram:C1"]
    assert client.get(entries["instagram:P2"]["thumb_url"]).status_code == 200
    bob_thumb = thumbs._cache_path(data_dir(), next(line["to"] for line in bob_lines if line["role"] == "media"))
    assert os.path.exists(bob_thumb)

    r = client.post("/api/trash/purge", json={"keys": [entries["instagram:P2"]["key"], "f" * 20]},
                    headers=H).get_json()
    assert r["ok"] and r["entries"] == 1 and r["files"] == 2 and r["errors"] == [] and r["dropped"] == 0
    assert r["keys"] == [entries["instagram:P2"]["key"]]
    assert r["bytes"] == entries["instagram:P2"]["bytes"]
    assert not any(os.path.lexists(line["to"]) for line in bob_lines)
    assert not (trash_root(env) / "bob").exists()                     # emptied folders go too
    assert not os.path.exists(bob_thumb)
    assert all(os.path.exists(line["to"]) for line in alice_lines)
    assert manifest(env) == alice_lines
    assert [m["idx"] for m in media_of(client, "C1")] == [2]          # the index is not touched
    assert client.get(entries["instagram:P2"]["thumb_url"]).status_code == 404


def _fake_line(env, to, post="instagram:EVIL"):
    trash_root(env).mkdir(exist_ok=True)
    with open(trash_root(env) / ".manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"from": str(env["media"] / "x.jpg"), "to": str(to), "post": post,
                            "batch": "b", "at": 1}) + "\n")
    return trash.entry_key(env["roots"][0], post, "b")


def test_purge_refuses_path_traversal(env, client, tmp_path):
    precious = tmp_path / "precious.jpg"
    png(str(precious))
    key = _fake_line(env, trash_root(env) / ".." / ".." / "precious.jpg")
    r = client.post("/api/trash/purge", json={"keys": [key]}, headers=H).get_json()
    assert r["entries"] == 0 and r["errors"][0]["error"] == "outside the trash folder"
    assert precious.exists() and len(manifest(env)) == 1               # line kept
    assert client.get(f"/trash/{key}/thumb").status_code == 404


def test_purge_refuses_symlink_out_of_the_trash(env, client, tmp_path):
    precious = tmp_path / "precious.jpg"
    png(str(precious))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    png(str(outside / "a.jpg"))
    trash_root(env).mkdir()
    (trash_root(env) / "link.jpg").symlink_to(precious)
    (trash_root(env) / "dir").symlink_to(outside, target_is_directory=True)
    k1 = _fake_line(env, trash_root(env) / "link.jpg", "instagram:A")
    k2 = _fake_line(env, trash_root(env) / "dir" / "a.jpg", "instagram:B")
    k3 = _fake_line(env, trash_root(env) / ".manifest.jsonl", "instagram:C")
    r = client.post("/api/trash/purge", json={"keys": [k1, k2, k3]}, headers=H).get_json()
    assert r["entries"] == 0 and len(r["errors"]) == 3
    assert precious.exists() and (outside / "a.jpg").exists()
    assert len(manifest(env)) == 3
    for k in (k1, k2, k3):
        assert client.get(f"/trash/{k}/thumb").status_code == 404


def test_roots_not_configured_are_never_touched(env, client, tmp_path):
    """A manifest outside the configured roots is not read at all."""
    other = tmp_path / "other"
    write_post(other, "P1", 1717243200, ALICE, "image")
    scanner.scan([str(other)])
    trash.delete(["instagram:P1"], [], [str(other)], data_dir())
    key = trash.items([str(other)])["entries"][0]["key"]
    r = client.post("/api/trash/purge", json={"keys": [key]}, headers=H).get_json()
    assert r["entries"] == 0 and r["files"] == 0
    assert any(n.endswith(".jpg") for n in os.listdir(other / ".feedvault-trash"))


def test_old_format_lines(env, client):
    """Lines from before the page fields: platform from the post id, kind from
    the extension, size from disk, a video's same-stem image is its poster."""
    write_post(env["media"], "V1", 1717243200, ALICE, "video")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:V1"]}, headers=H)
    old = [{k: line[k] for k in ("from", "to", "post", "batch", "at")} for line in manifest(env)]
    with open(trash_root(env) / ".manifest.jsonl", "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(line) + "\n" for line in old) + "not json\n")
    e = items(client)["entries"][0]
    assert (e["platform"], e["post_id"], e["author"], e["kind"]) == ("instagram", "V1", None, "video")
    assert (e["items"], e["of"], e["partial"], e["files"]) == (1, None, False, 3)
    assert e["bytes"] == sum(os.lstat(line["to"]).st_size for line in old)
    assert e["thumb_url"] and client.get(e["thumb_url"]).status_code == 200   # from the poster
    r = client.post("/api/trash/restore", json={"keys": [e["key"]]}, headers=H).get_json()
    assert r["files"] == 3 and media_of(client, "V1")[0]["kind"] == "video"


def test_old_format_purge(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    old = [{k: line[k] for k in ("from", "to", "post", "batch", "at")} for line in manifest(env)]
    with open(trash_root(env) / ".manifest.jsonl", "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(line) + "\n" for line in old))
    key = items(client)["entries"][0]["key"]
    r = client.post("/api/trash/purge", json={"keys": [key]}, headers=H).get_json()
    assert r["files"] == 2 and manifest(env) == []


def test_concurrent_delete_and_purge_keep_the_manifest_valid(env, client):
    for i in range(30):
        write_post(env["media"], f"P{i:02d}", 1717243200 + i, ALICE, "image")
    scan(env)
    for i in range(15):
        trash.delete([f"instagram:P{i:02d}"], [], env["roots"], data_dir())
    keys = [e["key"] for e in trash.items(env["roots"], limit=500)["entries"]]

    def deleter():
        for i in range(15, 30):
            trash.delete([f"instagram:P{i:02d}"], [], env["roots"], data_dir())

    def purger():
        for k in keys:
            trash.purge(env["roots"], [k], data_dir())

    threads = [threading.Thread(target=deleter), threading.Thread(target=purger)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = manifest(env)                                              # every line parses
    assert sorted({line["post"] for line in lines}) == [f"instagram:P{i:02d}" for i in range(15, 30)]
    assert all(os.path.exists(line["to"]) for line in lines) and len(lines) == 30
    assert items(client)["total"] == 15


# --- the API surface ----------------------------------------------------------------

def test_guard_header_and_bad_bodies(env, client):
    assert client.get("/api/trash/items").status_code == 403
    assert client.post("/api/trash/purge", json={"keys": ["a"]}).status_code == 403
    assert client.post("/api/trash/restore", json={"keys": ["a"]}).status_code == 403
    for body in ({}, {"keys": []}, {"keys": "abc"}, {"keys": [1]}):
        assert client.post("/api/trash/purge", json=body, headers=H).status_code == 400
    for body in ({}, {"keys": []}, {"posts": []}, {"keys": [1]}, {"posts": ["x"], "keys": "y"}):
        assert client.post("/api/trash/restore", json=body, headers=H).status_code == 400
    r = client.post("/api/trash/restore", json={"keys": ["0" * 20]}, headers=H).get_json()
    assert r["ok"] and r["files"] == 0


def test_thumb_url_shape(env, client):
    for bad in ("..", "xyz", "A" * 20, "0" * 19, "0" * 21):
        assert client.get(f"/trash/{bad}/thumb").status_code == 404
    assert client.get(f"/trash/{'0' * 20}/thumb").status_code == 404
