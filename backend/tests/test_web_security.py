"""What a web page, a downloaded file or a trash manifest can make the
server do: the headers on what it serves, and the paths it moves."""
import os
import shutil

from conftest import H
from fakes import gallery_dl_case

import db
import scanner


def _html_as_image(media):
    """A gallery-dl photo whose file is HTML, next to a JSON that still
    says "extension": "jpg": the parser types it by the JSON, the file
    keeps its own name."""
    gallery_dl_case("twitter/photo", media)
    os.rename(media / "565802608276047467_1.jpg", media / "565802608276047467_1.html")
    os.rename(media / "565802608276047467_1.jpg.json", media / "565802608276047467_1.html.json")
    (media / "565802608276047467_1.html").write_text("<script>alert(document.domain)</script>")


def test_a_file_is_never_served_as_html(env, client):
    _html_as_image(env["media"])
    scanner.scan(env["roots"])
    m = client.get("/api/posts/twitter/565802608276047467", headers=H).get_json()["media"][0]
    assert m["kind"] == "image" and db.media_row(db.connect(), m["id"])["path"].endswith(".html")
    for url in (f"/media/{m['id']}", f"/media/{m['id']}/thumb"):
        r = client.get(url)
        assert r.status_code == 200
        assert r.mimetype == "application/octet-stream", url
        assert r.headers["Content-Disposition"].startswith("attachment")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in r.headers["Content-Security-Policy"]


def test_media_types_by_their_own_name(env, client):
    import app
    gallery_dl_case("twitter/photo", env["media"])
    scanner.scan(env["roots"])
    m = client.get("/api/posts/twitter/565802608276047467", headers=H).get_json()["media"][0]
    r = client.get(f"/media/{m['id']}")
    assert r.mimetype == "image/jpeg" and r.headers["Content-Disposition"].startswith("inline")
    assert "sandbox" in r.headers["Content-Security-Policy"]
    assert app._type_of("x.MP4") == "video/mp4" and app._type_of("x.opus") == "audio/ogg"
    assert app._type_of("x.svg") is None and app._type_of("x.html") is None
    # Every extension the scanner indexes plays in the dashboard.
    from parsers import MEDIA_EXT
    from parsers.yt_dlp import AUDIO_EXT
    assert all(app._type_of("x." + e) for e in MEDIA_EXT | AUDIO_EXT)


EPS = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nshowpage\n%%EOF\n"


def test_a_downloaded_picture_never_reaches_ghostscript(tmp_path, monkeypatch):
    # Pillow picks the format by content: an EPS saved as .jpg would be
    # rendered by Ghostscript (gs) on the thumbnail and on every hash pass.
    from PIL import EpsImagePlugin

    import hashing
    import thumbs
    calls = []

    def ghostscript(*a, **kw):
        calls.append(a)
        raise OSError("ghostscript")
    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", ghostscript)
    src = tmp_path / "x.jpg"
    src.write_bytes(EPS)
    for run in (lambda: thumbs._save_image(str(src), str(tmp_path / "t" / "x.jpg")),
                lambda: hashing.dhash(str(src))):
        try:
            run()
        except Exception:
            pass
    assert calls == []
    assert hashing.dimensions(str(src)) == (None, None)


def test_no_page_of_feedvault_can_be_framed(env, client):
    # Framed under another site's page, the dashboard's own clicks (Empty
    # trash, Run) would pass every origin check.
    gallery_dl_case("twitter/photo", env["media"])
    scanner.scan(env["roots"])
    for r in (client.get("/"), client.get("/settings"), client.get("/api/stats", headers=H),
              client.get("/userscript/feedvault.user.js"), client.get("/media/1")):
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_the_dashboard_loads_its_fonts_from_feedvault_only(env, client, monkeypatch):
    # #148: the fonts came from Google Fonts. Now the build's woff2 files,
    # served from dist/assets as fonts, and the pages' policy allows no other
    # origin's font. A file's own stricter policy (default-src 'none') is kept.
    import config
    static = env["tmp"] / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<p>dashboard</p>")
    (static / "assets" / "jetbrains-mono-latin-500-normal-abc.woff2").write_bytes(b"wOF2font")
    monkeypatch.setattr(config, "static_dir", lambda: str(static))
    font = client.get("/assets/jetbrains-mono-latin-500-normal-abc.woff2")
    assert (font.status_code, font.mimetype, font.data) == (200, "font/woff2", b"wOF2font")
    for r in (client.get("/"), client.get("/settings"), font):
        csp = [d.strip() for d in r.headers["Content-Security-Policy"].split(";")]
        assert "font-src 'self'" in csp and "frame-ancestors 'none'" in csp
    gallery_dl_case("twitter/photo", env["media"])
    scanner.scan(env["roots"])
    media = client.get("/media/1").headers["Content-Security-Policy"]
    assert "default-src 'none'" in media and "font-src" not in media


def test_media_refused_to_other_sites(env, client):
    # <img src="http://localhost:3380/media/N/thumb"> on any site: the Host
    # is ours, so only the browser's own word on who asks tells them apart.
    # Refused before any work: no thumbnail made, nothing to time or measure.
    gallery_dl_case("twitter/photo", env["media"])
    scanner.scan(env["roots"])
    thumbs_dir = env["tmp"] / "data" / "thumbs"
    for url in ("/media/1/thumb", "/media/1", "/media/1/poster", "/media/copy/1/thumb",
                "/trash/0123456789abcdef0123/thumb"):
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                        {"Origin": "https://evil.example"}):
            assert client.get(url, headers=headers).status_code == 403, (url, headers)
    assert not thumbs_dir.exists()
    for headers in ({}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}):
        r = client.get("/media/1/thumb", headers=headers)
        assert r.status_code == 200
        assert r.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    # The userscript is installed from a link anywhere.
    assert client.get("/userscript/feedvault.user.js", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    # The dashboard's own pages still open from a link anywhere.
    assert client.get("/trash", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert client.get("/trash/x", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_the_bookmarklet_page_opens_from_anywhere_but_saves_only_from_feedvault(env, client, monkeypatch):
    # #165 C: the bookmarklet opens /links/add from any site, in a window of
    # its own. The page is the dashboard (never framed); the save is its own
    # same-origin call, which another site still cannot make.
    import config
    static = env["tmp"] / "dist"
    static.mkdir()
    (static / "index.html").write_text("<p>dashboard</p>")
    monkeypatch.setattr(config, "static_dir", lambda: str(static))
    page = client.get("/links/add?popup=1&url=https%3A%2F%2Fexample.org%2F&title=x",
                      headers={"Sec-Fetch-Site": "cross-site"})
    assert (page.status_code, page.data) == (200, b"<p>dashboard</p>")
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    body = {"url": "https://example.org/", "title": "x", "person": None}
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://example.org"}):
        assert client.post("/api/links", json=body, headers={**H, **headers}).status_code == 403
        assert client.get("/api/people", headers={**H, **headers}).status_code == 403
    assert client.get("/api/links", headers=H).get_json()["links"] == []
    saved = client.post("/api/links", json=body, headers={**H, "Sec-Fetch-Site": "same-origin"})
    assert saved.status_code == 200 and saved.get_json()["ok"]


def test_restore_moves_nothing_a_manifest_line_points_outside(env):
    # A manifest line is only read back, never trusted: restore moves a file
    # from inside the root's trash to a place inside the root, or not at all.
    import json

    import trash
    root = env["media"]
    tdir = root / trash.TRASH_NAME
    tdir.mkdir()
    (tdir / "a.jpg").write_bytes(b"a")
    outside = env["tmp"] / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"s")
    lines = [
        {"from": str(outside / "autostart.desktop"), "to": str(tdir / "a.jpg"), "post": "p1", "batch": "b"},
        {"from": str(root / "stolen.jpg"), "to": str(outside / "secret"), "post": "p2", "batch": "b"},
        {"from": str(tdir / "again.jpg"), "to": str(tdir / "a.jpg"), "post": "p3", "batch": "b"},
        {"from": str(root / "a\0b.jpg"), "to": str(tdir / "a.jpg"), "post": "p4", "batch": "b"},
        {"from": str(root / "all"), "to": str(tdir), "post": "p5", "batch": "b"},
        {"from": str(root / "sub"), "to": str(tdir / "sub"), "post": "p6", "batch": "b"},
    ]
    (tdir / "sub").mkdir()
    (tdir / "sub" / "b.jpg").write_bytes(b"b")
    (tdir / trash.MANIFEST).write_text("".join(json.dumps(x) + "\n" for x in lines))
    report = trash.restore(["p1", "p2", "p3", "p4", "p5", "p6"], env["roots"])
    assert not (outside / "autostart.desktop").exists()
    assert (outside / "secret").read_bytes() == b"s" and not (root / "stolen.jpg").exists()
    assert (tdir / "a.jpg").exists()
    assert (tdir / "sub" / "b.jpg").exists() and not (root / "sub").exists() and not (root / "all").exists()
    assert report["files"] == 0 and len(report["errors"]) == 6
    assert len(trash._read_manifest(str(root))) == 6                   # kept, as any line that failed


def test_dashboard_fallback_says_nothing_of_files_outside_it(env, client, monkeypatch):
    # The catch-all checked os.path.join(static, path) before any safe join:
    # /assets%2f..%2f..%2f<file> (a browser does not resolve %2f) answered 404 for
    # a file that exists and the dashboard for one that does not.
    import config
    static = env["tmp"] / "dist"
    static.mkdir()
    (static / "index.html").write_text("<p>dashboard</p>")
    (static / "assets").mkdir()
    (static / "assets" / "app.js").write_text("1")
    monkeypatch.setattr(config, "static_dir", lambda: str(static))
    (env["tmp"] / "here").write_text("x")
    up = "assets%2f..%2f..%2f"
    there, missing = client.get(f"/{up}here"), client.get(f"/{up}not-here")
    assert (there.status_code, there.data) == (missing.status_code, missing.data) == (200, b"<p>dashboard</p>")
    assert client.get("/assets/app.js").data == b"1"


# ---------------------------------------------------------------------------
# /media and symlinks (#73)
# ---------------------------------------------------------------------------

def _media_of(client, post_id):
    return client.get(f"/api/posts/{post_id}", headers=H).get_json()["media"]


def _one_post(env, kind="image"):
    from fakes import owner, write_post
    base = write_post(env["media"] / "alice", "P1", 1717243200, owner("alice.example", 111), kind)
    scanner.scan(env["roots"])
    return base


def _swap_for_link(path, target):
    os.remove(path)
    os.symlink(target, path)


def test_a_recorded_file_swapped_for_a_symlink_out_of_the_roots_is_never_served(env, client, tmp_path):
    base = _one_post(env)
    [m] = _media_of(client, "instagram/P1")
    assert client.get(f"/media/{m['id']}").status_code == 200
    secret = tmp_path / "secret.jpg"
    shutil.copyfile(base + ".jpg", secret)
    _swap_for_link(base + ".jpg", secret)
    shutil.rmtree(env["tmp"] / "data" / "thumbs", ignore_errors=True)
    for url in (f"/media/{m['id']}", f"/media/{m['id']}/thumb"):
        assert client.get(url).status_code == 404, url
    assert not os.path.exists(env["tmp"] / "data" / "thumbs")     # no thumbnail made of it either


def test_a_poster_swapped_for_a_symlink_out_of_the_roots_is_never_served(env, client, tmp_path):
    base = _one_post(env, "video")
    [m] = _media_of(client, "instagram/P1")
    assert db.media_row(db.connect(), m["id"])["poster_path"] == base + ".jpg"
    assert client.get(f"/media/{m['id']}/poster").status_code == 200
    secret = tmp_path / "secret.jpg"
    shutil.copyfile(base + ".jpg", secret)
    _swap_for_link(base + ".jpg", secret)
    assert client.get(f"/media/{m['id']}/poster").status_code == 404
    assert client.get(f"/media/{m['id']}/thumb").status_code == 404
    assert client.get(f"/media/{m['id']}").status_code == 200       # the video itself is in the root


def test_a_symlink_into_the_trash_is_never_served(env, client):
    base = _one_post(env)
    [m] = _media_of(client, "instagram/P1")
    trashed = env["media"] / ".feedvault-trash" / "alice" / "x.jpg"
    trashed.parent.mkdir(parents=True)
    shutil.copyfile(base + ".jpg", trashed)
    _swap_for_link(base + ".jpg", trashed)
    assert client.get(f"/media/{m['id']}").status_code == 404


def test_a_symlink_that_stays_in_the_root_is_indexed_and_served(env, client):
    from fakes import owner, write_post
    base = write_post(env["media"] / "alice", "P1", 1717243200, owner("alice.example", 111), "image")
    kept = env["media"] / "store" / "p1.jpg"
    kept.parent.mkdir()
    shutil.move(base + ".jpg", kept)
    os.symlink(kept, base + ".jpg")
    scanner.scan(env["roots"])
    [m] = _media_of(client, "instagram/P1")
    r = client.get(f"/media/{m['id']}")
    assert r.status_code == 200 and r.data == kept.read_bytes()
    assert client.get(f"/media/{m['id']}/thumb").status_code == 200


def test_the_scanner_skips_a_symlink_that_leads_out_of_the_root(env, client, tmp_path):
    from fakes import owner, write_post
    base = write_post(env["media"] / "alice", "P1", 1717243200, owner("alice.example", 111), "image")
    secret = tmp_path / "secret.jpg"
    shutil.move(base + ".jpg", secret)
    os.symlink(secret, base + ".jpg")
    # A whole post (its metadata) through a link too.
    other = write_post(tmp_path / "elsewhere", "P2", 1717243300, owner("alice.example", 111), "image")
    for suffix in (".json", ".jpg"):
        os.symlink(other + suffix, str(env["media"] / "alice" / os.path.basename(other)) + suffix)
    report = scanner.scan(env["roots"])
    conn = db.connect()
    paths = {r[0] for r in conn.execute("SELECT path FROM media")}
    assert base + ".jpg" not in paths
    assert conn.execute("SELECT 1 FROM posts WHERE id = 'instagram:P2'").fetchone() is None
    unmatched = dict(conn.execute("SELECT path, reason FROM unmatched").fetchall())
    for p in (base + ".jpg", str(env["media"] / "alice" / os.path.basename(other)) + ".json"):
        assert unmatched[p] == scanner.LEADS_OUT
    assert report["unmatched"] >= 3
    # The same through a re-index of the folder (after a download).
    scanner.index_dirs(env["roots"], [str(env["media"] / "alice")], new=True)
    assert conn.execute("SELECT 1 FROM posts WHERE id = 'instagram:P2'").fetchone() is None
    assert base + ".jpg" not in {r[0] for r in conn.execute("SELECT path FROM media")}


def test_a_file_swapped_for_a_symlink_after_the_check_is_not_served(env, client, tmp_path, monkeypatch):
    """#73 review: what is checked is what was opened, never the path again."""
    import app
    base = _one_post(env)
    [m] = _media_of(client, "instagram/P1")
    original = open(base + ".jpg", "rb").read()
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"secret")
    in_roots = scanner.in_roots

    def swap_after_check(path, roots):
        ok = in_roots(path, roots)
        if os.path.exists(base + ".jpg") and not os.path.islink(base + ".jpg"):
            os.rename(base + ".jpg", tmp_path / "was.jpg")
            os.symlink(secret, base + ".jpg")
        return ok

    monkeypatch.setattr(app.scanner, "in_roots", swap_after_check)
    r = client.get(f"/media/{m['id']}")
    assert r.status_code == 200 and r.data == original


def test_a_range_request_still_works(env, client):
    base = _one_post(env)
    [m] = _media_of(client, "instagram/P1")
    data = open(base + ".jpg", "rb").read()
    r = client.get(f"/media/{m['id']}", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206 and r.data == data[2:6]
    assert r.headers["Content-Range"] == f"bytes 2-5/{len(data)}"
    etag = client.get(f"/media/{m['id']}").headers["ETag"]
    assert client.get(f"/media/{m['id']}", headers={"If-None-Match": etag}).status_code == 304


def test_a_symlink_into_another_media_root_is_indexed_and_served(env, client, tmp_path):
    """#73 review: one root's symlink into another root is in the media roots."""
    import config
    from fakes import owner, write_post
    other = tmp_path / "other-root"
    other.mkdir()
    cfg = config.load()
    cfg["media_roots"] = [str(env["media"]), str(other)]
    config.save(cfg)
    base = write_post(env["media"] / "alice", "P1", 1717243200, owner("alice.example", 111), "image")
    shutil.move(base + ".jpg", other / "p1.jpg")
    os.symlink(other / "p1.jpg", base + ".jpg")
    scanner.scan(cfg["media_roots"])
    [m] = _media_of(client, "instagram/P1")
    assert client.get(f"/media/{m['id']}").status_code == 200
