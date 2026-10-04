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
