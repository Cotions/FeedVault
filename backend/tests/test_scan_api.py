import os
import time

from conftest import H
from fakes import owner, write_post, png

import db
import scanner

ALICE = owner("alice.example", 111, "Alice Example")
BOB = owner("bob_example", 222, "Bob")


def run_scan(env):
    return scanner.scan(env["roots"])


def test_scan_indexes_and_is_idempotent(env):
    write_post(env["media"] / "alice.example", "P1", 1717243200, ALICE, "image", caption="sunset")
    write_post(env["media"] / "bob_example", "P2", 1717243300, BOB, "carousel", slides=[False, True])
    r = run_scan(env)
    assert (r["added"], r["updated"], r["unmatched"]) == (2, 0, 0)
    r = run_scan(env)
    assert (r["added"], r["updated"]) == (0, 0)


def test_media_ids_stable_across_rescans(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    run_scan(env)
    first = client.get("/api/posts/instagram/P1", headers=H).get_json()["media"][0]["id"]
    # touch the metadata so the post is re-upserted
    meta = next(f for f in os.listdir(env["media"]) if f.endswith(".json"))
    t = time.time() + 5
    os.utime(env["media"] / meta, (t, t))
    assert run_scan(env)["updated"] == 1
    again = client.get("/api/posts/instagram/P1", headers=H).get_json()["media"][0]["id"]
    assert first == again


def test_deleted_post_is_kept_as_missing(env, client):
    base = write_post(env["media"], "P1", 1717243200, ALICE, "image")
    run_scan(env)
    os.remove(base + ".json")
    os.remove(base + ".jpg")
    r = run_scan(env)
    assert r["missing"] == 1
    post = client.get("/api/posts/instagram/P1", headers=H).get_json()
    assert post["missing"] is True
    assert post["media"][0]["missing"] is True
    # and it comes back when the file does
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    run_scan(env)
    assert client.get("/api/posts/instagram/P1", headers=H).get_json()["missing"] is False


def test_duplicate_download_reported(env):
    write_post(env["media"] / "a", "P1", 1717243200, ALICE, "image")
    write_post(env["media"] / "b", "P1", 1717243200, ALICE, "image")
    r = run_scan(env)
    assert r["added"] == 1
    rows = db.unmatched(db.connect())
    assert len(rows) == 1 and rows[0]["reason"].startswith("duplicate of instagram:P1")


def test_orphan_media_listed_as_unmatched(env, client):
    png(str(env["media"] / "stray.png"))
    run_scan(env)
    rows = client.get("/api/unmatched", headers=H).get_json()
    assert [os.path.basename(r["path"]) for r in rows] == ["stray.png"]


def test_list_filter_search_and_sort(env, client):
    write_post(env["media"], "OLD", 1600000000, ALICE, "image", caption="Café au lait in Paris")
    write_post(env["media"], "NEW", 1700000000, BOB, "video", caption="skateboarding")
    run_scan(env)
    get = lambda qs: client.get("/api/posts" + qs, headers=H).get_json()  # noqa: E731
    assert [p["post_id"] for p in get("")["posts"]] == ["NEW", "OLD"]
    assert [p["post_id"] for p in get("?q=cafe")["posts"]] == ["OLD"]          # diacritics folded
    assert [p["post_id"] for p in get("?q=skate")["posts"]] == ["NEW"]        # prefix on last word
    assert [p["post_id"] for p in get("?q=bob_ex")["posts"]] == ["NEW"]       # author handle
    assert [p["post_id"] for p in get("?author=111")["posts"]] == ["OLD"]
    assert [p["post_id"] for p in get("?kind=video")["posts"]] == ["NEW"]
    assert get('?q="")(*')["total"] == 0                                    # hostile input is safe
    page = get("?limit=1&offset=1")
    assert page["total"] == 2 and [p["post_id"] for p in page["posts"]] == ["OLD"]


def test_summary_shape(env, client):
    write_post(env["media"], "V1", 1717243200, ALICE, "video", caption="clip")
    run_scan(env)
    p = client.get("/api/posts", headers=H).get_json()["posts"][0]
    assert p["cover"]["kind"] == "video" and p["cover"]["poster"] is True
    assert p["cover"]["url"].endswith("/thumb")
    assert p["author"] == {"id": "111", "handle": "alice.example", "name": "Alice Example"}
    assert p["media_count"] == 1
    full = client.get("/api/posts/instagram/V1", headers=H).get_json()
    assert full["source"]["tool"] == "instaloader"
    assert full["media"][0]["poster_url"] is not None


def test_media_served_and_range(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    run_scan(env)
    mid = client.get("/api/posts/instagram/P1", headers=H).get_json()["media"][0]["id"]
    r = client.get(f"/media/{mid}")                    # no header needed
    assert r.status_code == 200 and r.data.startswith(b"\x89PNG")
    r = client.get(f"/media/{mid}", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.data == b"\x89PNG"
    assert client.get("/media/999999").status_code == 404


def test_api_requires_header_and_local_host(client):
    assert client.get("/api/stats").status_code == 403
    assert client.get("/api/stats", headers=H).status_code == 200
    r = client.get("/api/stats", headers={**H, "Host": "evil.example:3380"})
    assert r.status_code == 403
    assert "Access-Control-Allow-Origin" not in client.get("/api/stats", headers=H).headers


def test_saved_endpoint(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    run_scan(env)
    r = client.post("/api/saved", json={"ids": ["instagram:P1", "instagram:NOPE"]}, headers=H)
    assert r.get_json() == {"saved": ["instagram:P1"]}


def test_config_validation(env, client):
    r = client.post("/api/config", json={"media_roots": ["/definitely/not/here"]}, headers=H).get_json()
    assert r["ok"] is False
    inner = env["media"] / "inner"
    inner.mkdir()
    r = client.post("/api/config", json={"media_roots": [str(env["media"]), str(inner)]},
                    headers=H).get_json()
    assert r["ok"] is False and "inside" in r["error"]


def test_stats_and_authors(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    write_post(env["media"], "P2", 1717243300, ALICE, "carousel", slides=[False, False])
    write_post(env["media"] / "b", "P3", 1717243400, BOB, "image")
    run_scan(env)
    s = client.get("/api/stats", headers=H).get_json()
    assert (s["posts"], s["media"], s["authors"]) == (3, 4, 2)
    assert s["by_kind"] == {"image": 2, "carousel": 1}
    a = client.get("/api/authors", headers=H).get_json()
    assert [(x["handle"], x["count"]) for x in a] == [("alice.example", 2), ("bob_example", 1)]


def test_userscript_served_without_header(client):
    r = client.get("/userscript/feedvault.user.js")
    assert r.status_code == 200 and b"==UserScript==" in r.data


USERSCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "userscript", "feedvault.user.js")


def _userscript_source():
    with open(USERSCRIPT, encoding="utf-8") as f:
        return f.read()


def _header_lines(text, key):
    return [l for l in text.splitlines() if l.startswith(f"// {key} ")]


def test_userscript_on_the_default_port_is_the_file(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "PORT", 3380)
    r = client.get("/userscript/feedvault.user.js")
    assert r.status_code == 200
    assert r.mimetype == "text/javascript"
    assert r.headers["Cache-Control"] == "no-cache"
    assert r.get_data(as_text=True) == _userscript_source()


def test_userscript_names_this_instances_port(client, monkeypatch):
    # Installed from another port (the demo on 3389), it talks to that
    # instance: API_BASE and both update URLs, and nothing else changes.
    import config
    monkeypatch.setattr(config, "PORT", 3389)
    text = client.get("/userscript/feedvault.user.js", headers={"Host": "localhost:3389"}).get_data(as_text=True)
    source = _userscript_source()
    assert 'const API_BASE = "http://localhost:3389";' in text
    assert "// @updateURL    http://localhost:3389/userscript/feedvault.user.js" in text
    assert "// @downloadURL  http://localhost:3389/userscript/feedvault.user.js" in text
    assert "3380" not in text
    assert text.count("3389") == 3
    assert text == source.replace("http://localhost:3380", "http://localhost:3389")
    for key in ("@match", "@connect", "@grant", "@name", "@namespace"):
        assert _header_lines(text, key) == _header_lines(source, key)
    assert _header_lines(text, "@connect") == ["// @connect      localhost", "// @connect      127.0.0.1"]


def test_userscript_port_never_comes_from_the_request(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "PORT", 3389)
    expected = _userscript_source().replace("http://localhost:3380", "http://localhost:3389")
    # A Host naming this machine on another port, forwarding headers, a
    # query: the script still names the port FeedVault listens on.
    for headers, url in (({"Host": "localhost:9999"}, "/userscript/feedvault.user.js"),
                         ({"Host": "127.0.0.1:1"}, "/userscript/feedvault.user.js?port=4444"),
                         ({"X-Forwarded-Host": "evil.example:4444", "X-Forwarded-Port": "4444",
                           "Forwarded": "host=evil.example:4444"}, "/userscript/feedvault.user.js")):
        r = client.get(url, headers=headers)
        assert r.status_code == 200
        assert r.get_data(as_text=True) == expected
    # A hostile Host is refused before anything is served.
    for host in ("evil.example", "evil.example:3389", "localhost.evil.example:3389"):
        r = client.get("/userscript/feedvault.user.js", headers={"Host": host})
        assert r.status_code == 403
        assert b"UserScript" not in r.data and b"evil" not in r.data


def test_spa_fallback_never_swallows_api(client):
    assert client.get("/api/nope", headers=H).status_code == 404


def test_thumbnails_cached_outside_media(env, client):
    write_post(env["media"], "P1", 1717243200, ALICE, "image")
    run_scan(env)
    post = client.get("/api/posts", headers=H).get_json()["posts"][0]
    assert post["cover"]["url"].endswith("/thumb")
    r = client.get(post["cover"]["url"])
    assert r.status_code == 200 and r.data[:2] == b"\xff\xd8"          # JPEG
    before = sorted(os.listdir(env["media"]))
    client.get(post["cover"]["url"])
    assert sorted(os.listdir(env["media"])) == before                  # media folder untouched
    assert os.path.isdir(env["tmp"] / "data" / "thumbs")


def test_venv_folders_skipped(env):
    venv = env["media"] / "venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin")
    png(str(venv / "lib" / "icon.png"))
    assert run_scan(env)["unmatched"] == 0


def test_highlight_album_searchable(env, client):
    hl = env["media"] / "somehandle" / "Summer trip"
    hl.mkdir(parents=True)
    png(str(hl / "Summer trip-2025-07-09-DL5V1h4OX-O.jpg"))
    run_scan(env)
    posts = client.get("/api/posts?q=summer", headers=H).get_json()["posts"]
    assert [p["kind"] for p in posts] == ["story"]
    full = client.get("/api/posts/instagram/DL5V1h4OX-O", headers=H).get_json()
    assert full["album"] == "Summer trip" and full["url"] is None
