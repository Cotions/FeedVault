"""People's profile pictures (avatars.py; docs/API.md "Profile pictures").

Every picture here is made by the test, in its tmp dir: nothing is fetched."""
import io
import os
import sqlite3
import struct
import zlib

from PIL import Image

from conftest import H
from fakes import owner, write_post
from test_people import create, get, post
from test_person_attributes import _rebuild

import avatars
import config
import db
import scanner
import userdata

ALICE = owner("alice.example", 111, "Alice Example")
BOB = owner("bob.example", 222, "Bob Example")
TS = 1717243200
RED, BLUE = (220, 20, 20), (20, 20, 220)


def picture(size=(1000, 500), fmt="PNG", halves=(RED, BLUE)):
    """A picture of two halves, left and right, as bytes."""
    img = Image.new("RGB", size, halves[0])
    img.paste(halves[1], (size[0] // 2, 0, size[0], size[1]))
    out = io.BytesIO()
    img.save(out, fmt)
    return out.getvalue()


def upload(client, pid, data, status=200, query="", kind="image/png"):
    r = client.post(f"/api/people/{pid}/avatar{query}", data=data, headers={**H, "Content-Type": kind})
    assert r.status_code == status, r.get_json()
    return r.get_json()


def files(env):
    folder = os.path.join(config.load()["data_directory"], "avatars")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def opened(env, name, small=False):
    return Image.open(avatars.path(config.load()["data_directory"], name, small))


def colour(img):
    return img.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))


def near(c, want, by=40):
    return all(abs(a - b) <= by for a, b in zip(c, want))


def archive(env):
    write_post(env["media"] / "alice.example", "A1", TS, ALICE, "image")
    write_post(env["media"] / "alice.example", "A2", TS + 100, ALICE, "video")
    write_post(env["media"] / "bob", "B1", TS + 200, BOB, "image")
    scanner.scan(env["roots"])


def person_with(client, name, handle):
    a = next(a for a in get(client, "/api/authors") if a["handle"] == handle)
    return create(client, name, a)["person"]["id"]


def test_a_database_of_version_24_migrates(tmp_path, monkeypatch):
    path = str(tmp_path / "feedvault.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:24])
    db.migrate(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO people(name, notes, created_at, gender) VALUES ('Old', 'kept', 5, 'Male')")
    conn.commit()
    conn.close()
    monkeypatch.undo()
    db.migrate(path)
    assert os.path.exists(path + ".bak-v24")
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT name, gender, avatar FROM people").fetchall() == [("Old", "Male", "")]
    conn.close()


def test_new_people_have_none(env, client):
    p = create(client, "A")["person"]
    assert p["avatar"] is None
    assert get(client, "/api/people")[0]["avatar"] is None


def test_upload_is_cropped_square_and_saved_twice_as_webp(env, client):
    pid = create(client, "A")["person"]["id"]
    p = upload(client, pid, picture())["person"]
    name = p["avatar"]
    assert avatars.NAME_RE.fullmatch(name)
    assert files(env) == sorted([f"{name}.webp", f"{name}-96.webp"])
    big, small = opened(env, name), opened(env, name, small=True)
    assert (big.format, big.size, small.format, small.size) == ("WEBP", (500, 500), "WEBP", (96, 96))
    assert get(client, f"/api/people/{pid}")["avatar"] == name
    assert get(client, "/api/people")[0]["avatar"] == name
    # Larger than SIZE: brought down to it.
    name = upload(client, pid, picture((2400, 1600)))["person"]["avatar"]
    assert opened(env, name).size == (512, 512)
    # Never next to the media.
    assert not any(n.endswith(".webp") for _, _, ns in os.walk(env["media"]) for n in ns)


def test_the_default_crop_is_the_middle_and_a_crop_keeps_its_square(env, client):
    pid = create(client, "A")["person"]["id"]
    middle = opened(env, upload(client, pid, picture())["person"]["avatar"])
    assert near(middle.convert("RGB").getpixel((10, 250)), RED) and near(middle.convert("RGB").getpixel((490, 250)), BLUE)
    left = upload(client, pid, picture(), query="?x=0&y=0&size=1")["person"]["avatar"]
    assert near(colour(opened(env, left)), RED)
    right = upload(client, pid, picture(), query="?x=0.5&y=0&size=1")["person"]["avatar"]
    assert near(colour(opened(env, right)), BLUE)
    # Out of the picture: kept inside it.
    assert upload(client, pid, picture(), query="?x=1&y=1&size=1")["person"]["avatar"] == right
    # A quarter of the shorter side, from the top left: 125 px, all red.
    zoomed = upload(client, pid, picture(), query="?x=0&y=0&size=0.25")["person"]["avatar"]
    assert opened(env, zoomed).size == (125, 125) and near(colour(opened(env, zoomed)), RED, 5)
    for q in ("?x=0&y=0&size=2", "?x=-1&y=0&size=1", "?x=a&y=0&size=1", "?x=0&y=0&size=0", "?x=nan&y=0&size=1"):
        assert "crop" in upload(client, pid, picture(), 400, query=q)["error"]


def test_what_is_not_a_picture_is_refused(env, client):
    pid = create(client, "A")["person"]["id"]
    for data in (b"hello", b"%!PS-Adobe-3.0 EPSF-3.0\n", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, picture()[:60]):
        assert "not a picture" in upload(client, pid, data, 400)["error"]
    assert upload(client, pid, b"", 400)["error"]
    # JSON naming nothing.
    assert post(client, f"/api/people/{pid}/avatar", {}, 400)["error"]
    assert get(client, f"/api/people/{pid}")["avatar"] is None and files(env) == []
    upload(client, 999, picture(), 404)


def test_too_large_is_refused(env, client):
    pid = create(client, "A")["person"]["id"]
    assert "too large" in upload(client, pid, b"\0" * (avatars.MAX_UPLOAD + 1), 413)["error"]
    # Without a length (chunked): read up to the cap only.
    r = client.post(f"/api/people/{pid}/avatar", input_stream=io.BytesIO(b"\0" * (avatars.MAX_UPLOAD + 10)),
                    headers={**H, "Content-Type": "image/png", "Transfer-Encoding": "chunked"})
    assert r.status_code in (400, 413)
    assert files(env) == []


def _bomb(w, h):
    """A PNG whose header says w x h pixels, its data a few bytes: opening it
    must never decode it."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0" * 64)) + chunk(b"IEND", b""))


def test_a_decompression_bomb_is_refused_before_it_is_decoded(env, client):
    pid = create(client, "A")["person"]["id"]
    assert "too large" in upload(client, pid, _bomb(10000, 8000), 400)["error"]
    assert "too large" in upload(client, pid, _bomb(60000, 60000), 400)["error"]    # over Pillow's own limit too
    assert files(env) == []


def test_pick_from_a_post_and_from_a_videos_poster(env, client):
    archive(env)
    pid = person_with(client, "Alice", "alice.example")
    choices = get(client, f"/api/people/{pid}/avatar/choices")
    assert choices["profile_pics"] == []
    kinds = [m["kind"] for m in choices["media"]]
    assert kinds == ["video", "image"]                                 # newest first, the video by its poster
    assert all(m["thumb"] == f"/media/{m['id']}/thumb" for m in choices["media"])
    for m in choices["media"]:
        p = post(client, f"/api/people/{pid}/avatar", {"media": m["id"]})["person"]
        assert opened(env, p["avatar"]).format == "WEBP"
    # Bob's picture is not Alice's to take.
    bob = person_with(client, "Bob", "bob.example")
    bobs = get(client, f"/api/people/{bob}/avatar/choices")["media"][0]["id"]
    assert "no such picture" in post(client, f"/api/people/{pid}/avatar", {"media": bobs}, 400)["error"]
    post(client, f"/api/people/{pid}/avatar", {"media": "1"}, 400)
    post(client, f"/api/people/{pid}/avatar", {"media": 10 ** 9}, 400)
    assert get(client, f"/api/people/{bob}")["avatar"] is None


def test_profile_pictures_beside_their_posts(env, client):
    archive(env)
    folder = env["media"] / "alice.example"
    (folder / "2023-01-01_10-00-00_UTC_profile_pic.jpg").write_bytes(picture(halves=(RED, RED), fmt="JPEG"))
    (folder / "2024-05-01_10-00-00_UTC_profile_pic.jpg").write_bytes(picture(halves=(BLUE, BLUE), fmt="JPEG"))
    (folder / "2024-06-01_10-00-00_UTC_cover.jpg").write_bytes(picture(fmt="JPEG"))        # a highlight's cover
    (env["media"] / "bob" / "2025-01-01_10-00-00_UTC_profile_pic.jpg").write_bytes(picture(fmt="JPEG"))
    pid = person_with(client, "Alice", "alice.example")
    pics = get(client, f"/api/people/{pid}/avatar/choices")["profile_pics"]
    assert [p["name"] for p in pics] == ["2024-05-01_10-00-00_UTC_profile_pic.jpg",
                                          "2023-01-01_10-00-00_UTC_profile_pic.jpg"]
    assert pics[0]["account"]["handle"] == "alice.example" and pics[0]["at"] == "2024-05-01_10-00-00"
    r = client.get(pics[0]["url"])
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert client.get(f"/media/people/{pid}/profile-pic/{'0' * 20}").status_code == 404
    p = post(client, f"/api/people/{pid}/avatar", {"profile_pic": pics[0]["key"]})["person"]
    assert near(colour(opened(env, p["avatar"])), BLUE)
    assert "no such profile picture" in post(client, f"/api/people/{pid}/avatar", {"profile_pic": "x"}, 400)["error"]
    # A link out of the media roots is never read.
    os.remove(folder / "2024-05-01_10-00-00_UTC_profile_pic.jpg")
    outside = env["tmp"] / "outside.jpg"
    outside.write_bytes(picture(fmt="JPEG"))
    os.symlink(outside, folder / "2025-05-01_10-00-00_UTC_profile_pic.jpg")
    names = [p["name"] for p in get(client, f"/api/people/{pid}/avatar/choices")["profile_pics"]]
    assert names == ["2023-01-01_10-00-00_UTC_profile_pic.jpg"]


def test_a_source_folder_counts_before_any_post(env, client):
    folder = env["media"] / "instagram" / "carol"
    folder.mkdir(parents=True)
    (folder / "2024-05-01_10-00-00_UTC_profile_pic.jpg").write_bytes(picture(fmt="JPEG"))
    r = post(client, "/api/people", {"name": "Carol", "profiles": ["https://www.instagram.com/carol/"]})
    pid = r["person"]["id"]
    pics = get(client, f"/api/people/{pid}/avatar/choices")["profile_pics"]
    assert os.path.normpath(r["sources"][0]["folder"]) == str(folder)
    assert [p["name"] for p in pics] == ["2024-05-01_10-00-00_UTC_profile_pic.jpg"]


def test_remove_and_change_drop_the_files_no_one_has(env, client):
    a = create(client, "A")["person"]["id"]
    b = create(client, "B")["person"]["id"]
    one = upload(client, a, picture())["person"]["avatar"]
    assert upload(client, b, picture())["person"]["avatar"] == one    # the same picture: one pair of files
    two = upload(client, a, picture(halves=(BLUE, RED)))["person"]["avatar"]
    assert files(env) == sorted([f"{one}.webp", f"{one}-96.webp", f"{two}.webp", f"{two}-96.webp"])
    p = client.delete(f"/api/people/{b}/avatar", headers=H).get_json()["person"]
    assert p["avatar"] is None
    assert files(env) == sorted([f"{two}.webp", f"{two}-96.webp"])    # one is no one's now
    assert client.delete(f"/api/people/{a}", headers=H).status_code == 200
    assert files(env) == []
    assert client.delete(f"/api/people/{a}/avatar", headers=H).status_code == 404


def test_merge_keeps_the_first_picture(env, client):
    a = create(client, "A")["person"]["id"]
    b = create(client, "B")["person"]["id"]
    c = create(client, "C")["person"]["id"]
    first = upload(client, b, picture())["person"]["avatar"]
    other = upload(client, c, picture(halves=(BLUE, RED)))["person"]["avatar"]
    m = post(client, "/api/people/merge", {"ids": [a, b, c]})["person"]
    assert m["id"] == a and m["avatar"] == first
    assert files(env) == sorted([f"{first}.webp", f"{first}-96.webp"])
    assert other not in "".join(files(env))


def test_served_cached_for_good_and_only_to_this_dashboard(env, client):
    pid = create(client, "A")["person"]["id"]
    name = upload(client, pid, picture())["person"]["avatar"]
    for file, size in ((f"{name}.webp", 500), (f"{name}-96.webp", 96)):
        r = client.get(f"/media/avatars/{file}")
        assert r.status_code == 200 and r.mimetype == "image/webp"
        assert "immutable" in r.headers["Cache-Control"] and "max-age=31536000" in r.headers["Cache-Control"]
        assert Image.open(io.BytesIO(r.data)).size == (size, size)
        assert client.get(f"/media/avatars/{file}", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    for bad in (f"{name}.jpg", f"{name}-48.webp", "..%2Ffeedvault.db", f"{name[:-1]}.webp", f"{'0' * 20}.webp"):
        assert client.get(f"/media/avatars/{bad}").status_code == 404


def test_a_rebuild_keeps_the_picture(env, client):
    pid = create(client, "A")["person"]["id"]
    name = upload(client, pid, picture())["person"]["avatar"]
    _rebuild(env)
    assert get(client, f"/api/people/{pid}")["avatar"] == name
    assert client.get(f"/media/avatars/{name}.webp").status_code == 200
    # A name that cannot be one, edited into the file by hand: the person stays, without it.
    rows = [{"name": "A", "created_at": 1, "id": 4, "avatar": "../../etc/passwd"},
            {"name": "B", "created_at": 1, "id": 5, "avatar": name}]
    base = config.load()["data_directory"]
    import json
    with open(userdata.path(base, "people"), "w") as f:
        json.dump({"version": 1, "rows": rows}, f)
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM people")
    userdata.restore_all(conn, base)
    assert {p["name"]: p["avatar"] for p in get(client, "/api/people")} == {"A": None, "B": name}
