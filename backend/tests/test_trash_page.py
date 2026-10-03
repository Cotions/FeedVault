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
    for k in (k1, k2, k3):                      # never read through a link out of the trash
        assert client.get(f"/trash/{k}/thumb").status_code == 404
    r = client.post("/api/trash/purge", json={"keys": [k1, k2, k3]}, headers=H).get_json()
    # The link itself is in the trash: purging removes the link, not its target.
    assert r["keys"] == [k1] and len(r["errors"]) == 2
    assert not os.path.lexists(trash_root(env) / "link.jpg")
    assert precious.exists() and (outside / "a.jpg").exists()
    assert (trash_root(env) / ".manifest.jsonl").exists() and len(manifest(env)) == 2


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


# --- purge by filter ------------------------------------------------------------------

def _age(env, post, at):
    """Pretend a post's deletion happened at ``at``."""
    lines = manifest(env)
    for line in lines:
        if line["post"] == post:
            line["at"] = at
    with open(trash_root(env) / ".manifest.jsonl", "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(line) + "\n" for line in lines))


def test_purge_by_filter(env, client):
    write_post(env["media"] / "alice", "OLD", 1717243200, ALICE, "image")
    write_post(env["media"] / "alice", "NEW", 1717243300, ALICE, "image")
    write_post(env["media"] / "bob", "BOLD", 1717243400, BOB, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:OLD", "instagram:NEW", "instagram:BOLD"]}, headers=H)
    _age(env, "instagram:OLD", 1000)
    _age(env, "instagram:BOLD", 1000)
    listed = items(client, author="111", before=2000)
    assert [e["post"] for e in listed["entries"]] == ["instagram:OLD"]
    r = client.post("/api/trash/purge", json={"filter": {"author": "111", "before": 2000}}, headers=H).get_json()
    assert r["ok"] and r["entries"] == 1 and r["keys"] == [listed["entries"][0]["key"]]
    assert sorted(line["post"] for line in manifest(env) if line["role"] == "media") \
        == ["instagram:BOLD", "instagram:NEW"]
    r = client.post("/api/trash/purge", json={"filter": {"before": 2000}}, headers=H).get_json()
    assert r["entries"] == 1
    assert [line["post"] for line in manifest(env) if line["role"] == "media"] == ["instagram:NEW"]


def test_purge_filter_validation(env, client):
    for body in ({"filter": {}}, {"filter": {"since": "1"}}, {"filter": {"before": -1}},
                 {"filter": {"author": ""}}, {"filter": {"oops": 1}}, {"filter": [1]},
                 {"filter": {"before": True}}, {"filter": {"before": 5}, "keys": ["a"]}):
        assert client.post("/api/trash/purge", json=body, headers=H).status_code == 400, body
    assert client.post("/api/trash/purge", json={"filter": {"before": 5}}).status_code == 403


def test_items_one_by_one_until_the_post_goes_is_not_partial(env, client):
    write_post(env["media"], "C1", 1717243200, ALICE, "carousel", slides=[False, False])
    scan(env)
    ids = [m["id"] for m in media_of(client, "C1")]
    client.post("/api/delete", json={"media": ids}, headers=H)       # the last one takes the post
    e = items(client)["entries"][0]
    assert e["partial"] is False and e["items"] == 2


def test_items_counts_files_still_in_the_trash(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    os.remove(next(line["to"] for line in manifest(env) if line["role"] == "media"))
    r = items(client)
    assert r["entries"][0]["files"] == r["files"] == r["trash"]["files"] == 1
    assert r["trash"]["files"] == client.get("/api/trash", headers=H).get_json()["files"]


def test_author_filter_is_per_platform(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    # The same author id on another platform (line edited: no second parser yet).
    lines = manifest(env)
    twin = [{**line, "post": "tiktok:T1", "platform": "tiktok", "batch": "other"} for line in lines]
    with open(trash_root(env) / ".manifest.jsonl", "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(line) + "\n" for line in twin))
    assert items(client, author="111")["total"] == 2
    assert [e["post"] for e in items(client, platform="tiktok", author="111")["entries"]] == ["tiktok:T1"]
    r = client.post("/api/trash/purge", json={"filter": {"platform": "instagram", "author": "111"}},
                    headers=H).get_json()
    assert r["entries"] == 1                                           # the twin lines point at the same files
    assert {line["post"] for line in manifest(env)} == {"tiktok:T1"}


# --- #17: the listing's cost, and purge by filter bound by the list ------------

def _big_trash(env, entries, per=2):
    """A generated trash: ``entries`` posts of ``per`` files each, every file
    there, lines as delete() writes them."""
    root = trash_root(env)
    lines = []
    for i in range(entries):
        folder = root / f"author{i % 50}"
        folder.mkdir(parents=True, exist_ok=True)
        for j in range(per):
            to = folder / f"P{i:06d}_{j}.jpg"
            to.write_bytes(b"x")
            lines.append({"from": str(env["media"] / f"author{i % 50}" / to.name), "to": str(to),
                          "post": f"instagram:P{i:06d}", "batch": f"b{i}", "platform": "instagram",
                          "author": {"id": str(i % 50), "handle": f"author{i % 50}"}, "kind": "image",
                          "posted_at": 1717243200, "items": 1, "partial": False,
                          "role": "media" if j == 0 else "meta", "idx": 0, "media_kind": "image",
                          "at": 1717243200 + i, "at_ms": (1717243200 + i) * 1000, "size": 1})
    with open(root / ".manifest.jsonl", "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(line) + "\n" for line in lines))


def test_listing_measures_only_the_page(env, client, monkeypatch):
    import time
    _big_trash(env, 25000)                                             # 50k files
    calls = []
    real = os.lstat
    monkeypatch.setattr(trash.os, "lstat", lambda p, *a, **kw: (calls.append(p), real(p, *a, **kw))[1])
    r = items(client, limit=60)
    assert (r["total"], r["files"], r["trash"]["files"]) == (25000, 50000, 50000)
    assert len(calls) == 120                                           # the page's files, nothing else
    calls.clear()
    times = []
    for _ in range(5):
        t = time.perf_counter()
        r = items(client, limit=60, author="7", offset=60)
        times.append(time.perf_counter() - t)
    assert r["total"] == 500 and len(r["entries"]) == 60
    assert len(calls) == 120                                           # that page once, then cached
    # Well under 100 ms on a desktop; the bound is loose for slow CI machines.
    assert sorted(times)[2] < 0.5, times


def test_a_file_moved_out_shows_on_the_page_once_its_measure_is_old(env, client, monkeypatch):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    assert [e["missing"] for e in items(client)["entries"]] == [False, False]
    gone = [line["to"] for line in manifest(env) if line["role"] == "media"]
    os.remove(gone[0])                                                  # P1's image, by hand
    assert items(client)["entries"][1]["missing"] is False             # measured a moment ago
    monkeypatch.setattr(trash, "MEASURE_TTL", 0)
    assert items(client, limit=1)["entries"][0]["post"] == "instagram:P2"
    r = items(client, limit=1, offset=1)                               # P1's page: measured again
    assert r["entries"][0]["missing"] is True and r["trash"]["files"] == 3
    # Off the page an entry keeps its last measure, until "Check for missing files".
    monkeypatch.setattr(trash, "MEASURE_TTL", 3600)
    os.remove(gone[1])
    assert items(client, limit=1, offset=1)["trash"]["files"] == 3
    r = client.post("/api/trash/check", headers=H).get_json()
    assert r == {"ok": True, "entries": 2, "files": 2, "bytes": r["bytes"], "missing": 2}
    assert items(client, limit=1, offset=1)["trash"]["files"] == 2
    assert client.post("/api/trash/check").status_code == 403


def test_stamps_only_go_up(env, client, monkeypatch):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    first = max(line["at_ms"] for line in manifest(env))
    monkeypatch.setattr(trash.time, "time", lambda: 1000.0)            # the clock went back
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    stamps = [line["at_ms"] for line in manifest(env)]
    assert stamps == sorted(set(stamps)) and min(s for s in stamps if s > first) == first + 1


def test_purge_by_filter_takes_only_what_the_list_showed(env, client):
    """#17.2: a deletion in the same second as the list, after it, is not purged with it."""
    for i in (1, 2, 3):
        write_post(env["media"] / "alice", f"P{i}", 1717243200 + i, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    listed = items(client, author="111")
    assert listed["total"] == 2 and listed["upto"] == max(line["at_ms"] for line in manifest(env))
    client.post("/api/delete", json={"posts": ["instagram:P3"]}, headers=H)
    # Same second, or not: the bound is the list's, not the clock's.
    lines = manifest(env)
    for line in lines:
        line["at"] = lines[0]["at"]
    with open(trash_root(env) / ".manifest.jsonl", "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(line) + "\n" for line in lines))
    more = items(client, author="111", upto=listed["upto"])
    assert more["total"] == 2 and more["upto"] == listed["upto"]
    r = client.post("/api/trash/purge", json={"filter": {"author": "111", "upto": listed["upto"]}},
                    headers=H).get_json()
    assert r["ok"] and sorted(r["keys"]) == sorted(e["key"] for e in listed["entries"])
    assert {line["post"] for line in manifest(env)} == {"instagram:P3"}
    for bad in ({"upto": 5}, {"author": "111", "upto": -1}, {"author": "111", "upto": "5"},
                {"author": "111", "upto": True}):
        assert client.post("/api/trash/purge", json={"filter": bad}, headers=H).status_code == 400, bad


def test_old_lines_without_stamps_sort_before_new_ones(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    write_post(env["media"], "P2", 1717243300, ALICE, "image")
    scan(env)
    client.post("/api/delete", json={"posts": ["instagram:P1"]}, headers=H)
    lines = manifest(env)
    for line in lines:
        del line["at_ms"]
    with open(trash_root(env) / ".manifest.jsonl", "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(line) + "\n" for line in lines))
    old = items(client)
    assert old["upto"] == lines[0]["at"] * 1000
    client.post("/api/delete", json={"posts": ["instagram:P2"]}, headers=H)
    assert items(client, upto=old["upto"])["total"] == 1
    assert items(client)["total"] == 2


def test_a_grown_manifest_is_read_from_where_it_stopped(env, client, monkeypatch):
    _big_trash(env, 300)
    first = items(client)
    write_post(env["media"], "NEW1", 1717243200, ALICE, "image")
    scan(env)
    parsed = []
    real = trash._parse
    monkeypatch.setattr(trash, "_parse", lambda data: (parsed.append(len(data)), real(data))[1])
    client.post("/api/delete", json={"posts": ["instagram:NEW1"]}, headers=H)
    r = items(client)
    assert r["total"] == first["total"] + 1 and r["entries"][0]["post"] == "instagram:NEW1"
    assert len(parsed) == 1 and parsed[0] < 5000                       # the appended lines only
    # The same entries as reading the whole file.
    trash._cache.clear()
    assert items(client, limit=500) == items(client, limit=500)
    # Edited in place (same file, the bytes read before changed): read whole again.
    lines = manifest(env)
    for line in lines:
        if line["post"] == lines[0]["post"]:
            line["at"] = 5
    with open(trash_root(env) / ".manifest.jsonl", "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(line) + "\n" for line in lines))
    parsed.clear()
    assert items(client, limit=500)["entries"][-1]["at"] == 5
    assert len(parsed) == 1 and parsed[0] > 50000
    # A line half written is read once it is whole.
    with open(trash_root(env) / ".manifest.jsonl", "a", encoding="utf-8") as f:
        whole = json.dumps({**lines[-1], "post": "instagram:HALF", "batch": "half"}) + "\n"
        f.write(whole[:40])
        f.flush()
        assert items(client)["total"] == r["total"]
        f.write(whole[40:])
    assert items(client)["total"] == r["total"] + 1
    # Unchanged since: not even opened.
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1])
    items(client)
    monkeypatch.undo()
    assert not [p for p in opened if p.endswith(".manifest.jsonl")]
