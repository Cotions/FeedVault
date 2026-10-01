import os

from conftest import H
from fakes import owner, write_post

import db
import scanner

ALICE = owner("alice.example", 111, "Alice Example")
BOB = owner("bob_example", 222, "Bob")

# 2023-06-01, 2024-06-01, 2024-07-01, 2024-08-01 (UTC)
T23, T24, T24B, T24C = 1685620800, 1717243200, 1719835200, 1722513600


def setup_posts(env, client):
    """Four posts over two authors and two years; A2 kept."""
    write_post(env["media"] / "alice", "A1", T23, ALICE, "image", caption="sunset")
    write_post(env["media"] / "alice", "A2", T24, ALICE, "video")
    write_post(env["media"] / "alice", "A3", T24C, ALICE, "carousel", slides=[False, True])
    write_post(env["media"] / "bob", "B1", T24B, BOB, "image", caption="sunset again")
    scanner.scan(env["roots"])
    client.post("/api/review", json={"posts": ["instagram:A2"], "decision": "keep"}, headers=H)


def sizes():
    """post id -> (media count, bytes), straight from the index."""
    out = {}
    for r in db.connect().execute("SELECT post_id, COUNT(*), SUM(size) FROM media WHERE missing = 0 GROUP BY post_id"):
        out[r[0]] = (r[1], r[2])
    return out


def get(client, path):
    r = client.get(path, headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_guard_header(client):
    for path in ("/api/storage", "/api/posts/summary", "/api/posts/summary?kind=video"):
        assert client.get(path).status_code == 403


def test_storage_empty(client):
    s = get(client, "/api/storage")
    assert s == {"totals": {"posts": 0, "media": 0, "bytes": 0}, "by_author": [], "by_kind": [],
                 "by_year": [], "largest": [], "trash": {"files": 0, "bytes": 0}}


def test_storage_shape_and_sums(env, client):
    setup_posts(env, client)
    sz = sizes()
    s = get(client, "/api/storage")
    total = sum(b for _, b in sz.values())
    assert s["totals"] == {"posts": 4, "media": sum(n for n, _ in sz.values()), "bytes": total}

    alice = sum(sz[f"instagram:{p}"][1] for p in ("A1", "A2", "A3"))
    a = next(r for r in s["by_author"] if r["id"] == "111")
    assert a == {"platform": "instagram", "id": "111", "handle": "alice.example", "name": "Alice Example",
                 "posts": 3, "media": sum(sz[f"instagram:{p}"][0] for p in ("A1", "A2", "A3")), "bytes": alice,
                 "kept_bytes": sz["instagram:A2"][1], "unreviewed_bytes": alice - sz["instagram:A2"][1]}
    assert [r["bytes"] for r in s["by_author"]] == sorted((r["bytes"] for r in s["by_author"]), reverse=True)

    kinds = {r["kind"]: r for r in s["by_kind"]}
    assert set(kinds) == {"image", "video", "carousel"}
    assert kinds["image"]["posts"] == 2
    assert kinds["image"]["bytes"] == sz["instagram:A1"][1] + sz["instagram:B1"][1]

    assert [(r["year"], r["posts"]) for r in s["by_year"]] == [(2023, 1), (2024, 3)]
    assert s["by_year"][0]["bytes"] == sz["instagram:A1"][1]

    largest = s["largest"]
    assert len(largest) == s["totals"]["media"]
    assert [m["bytes"] for m in largest] == sorted((m["bytes"] for m in largest), reverse=True)
    first = largest[0]
    assert set(first) == {"media_id", "post", "platform", "post_id", "author", "kind", "bytes", "thumb_url"}
    assert first["post"] == f"{first['platform']}:{first['post_id']}"
    images = [m for m in largest if m["kind"] == "image"]
    assert images[0]["thumb_url"] == f"/media/{images[0]['media_id']}/thumb"


def test_largest_capped(env, client, monkeypatch):
    setup_posts(env, client)
    monkeypatch.setattr(db, "LARGEST", 2)
    db._cache.clear()
    assert len(get(client, "/api/storage")["largest"]) == 2


def test_missing_media_excluded(env, client):
    base = write_post(env["media"], "GONE", T24, ALICE, "image")
    write_post(env["media"], "HERE", T23, BOB, "image")
    scanner.scan(env["roots"])
    os.remove(base + ".json")
    os.remove(base + ".jpg")
    scanner.scan(env["roots"])                  # GONE stays in the index, its media marked missing
    here = sizes()["instagram:HERE"]
    s = get(client, "/api/storage")
    assert s["totals"] == {"posts": 2, "media": 1, "bytes": here[1]}
    assert [m["post_id"] for m in s["largest"]] == ["HERE"]
    alice = next(r for r in s["by_author"] if r["id"] == "111")
    assert (alice["posts"], alice["media"], alice["bytes"]) == (1, 0, 0)
    assert next(a for a in get(client, "/api/authors") if a["id"] == "111")["bytes"] == 0
    assert get(client, "/api/posts/summary") == {"posts": 2, "media": 1, "bytes": here[1]}
    assert get(client, "/api/stats")["bytes"] == s["totals"]["bytes"]
    gone = get(client, "/api/posts?author=111")["posts"][0]
    assert (gone["media_count"], gone["bytes"]) == (1, 0)


def test_authors_have_bytes(env, client):
    setup_posts(env, client)
    sz = sizes()
    by_id = {a["id"]: a for a in get(client, "/api/authors")}
    assert by_id["222"]["bytes"] == sz["instagram:B1"][1]
    assert by_id["111"]["bytes"] == sum(sz[f"instagram:{p}"][1] for p in ("A1", "A2", "A3"))


def test_summary_matches_posts(env, client):
    setup_posts(env, client)
    sz = sizes()
    for qs in ("", "q=sunset", "platform=instagram", "author=111", "kind=image", "kind=video",
               "review=unreviewed", "review=kept", "author=111&kind=carousel&review=unreviewed",
               "q=nothingmatches", "q=%21%21", "review=bogus", "platform=x"):
        listed = get(client, f"/api/posts?{qs}&limit=200")
        summary = get(client, f"/api/posts/summary?{qs}")
        assert summary["posts"] == listed["total"], qs
        ids = [p["id"] for p in listed["posts"]]
        assert summary["media"] == sum(sz.get(i, (0, 0))[0] for i in ids), qs
        assert summary["bytes"] == sum(sz.get(i, (0, 0))[1] for i in ids), qs
        assert summary["bytes"] == sum(p["bytes"] for p in listed["posts"]), qs
    # paging and sort parameters change nothing
    assert get(client, "/api/posts/summary?limit=1&offset=3&sort=saved&order=asc") == get(client, "/api/posts/summary")


def test_cache_follows_changes(env, client):
    setup_posts(env, client)
    before = get(client, "/api/storage")
    summary = get(client, "/api/posts/summary?review=unreviewed")
    authors = {a["id"]: a["bytes"] for a in get(client, "/api/authors")}
    b1 = sizes()["instagram:B1"]

    r = client.post("/api/delete", json={"posts": ["instagram:B1"]}, headers=H).get_json()
    assert r["posts"] == ["instagram:B1"]
    after = get(client, "/api/storage")
    assert after["totals"]["posts"] == before["totals"]["posts"] - 1
    assert after["totals"]["bytes"] == before["totals"]["bytes"] - b1[1]
    assert after["trash"]["files"] >= 1 and after["trash"]["bytes"] >= b1[1]
    assert [a["id"] for a in after["by_author"]] == ["111"]
    assert get(client, "/api/posts/summary?review=unreviewed")["bytes"] == summary["bytes"] - b1[1]
    assert {a["id"]: a["bytes"] for a in get(client, "/api/authors")} == {"111": authors["111"]}

    # a decision moves bytes from unreviewed to kept
    client.post("/api/review", json={"posts": ["instagram:A1"], "decision": "keep"}, headers=H)
    a = get(client, "/api/storage")["by_author"][0]
    assert a["kept_bytes"] == sizes()["instagram:A1"][1] + sizes()["instagram:A2"][1]


def test_trash_largest_item(env, client):
    setup_posts(env, client)
    top = get(client, "/api/storage")["largest"][0]
    r = client.post("/api/delete", json={"media": [top["media_id"]]}, headers=H).get_json()
    assert r["media"] == [top["media_id"]] or r["posts"] == [top["post"]]
    s = get(client, "/api/storage")
    assert top["media_id"] not in [m["media_id"] for m in s["largest"]]
    assert s["trash"]["bytes"] >= top["bytes"]
