import os

from fakes import owner, write_meta, write_post, png, post_node, base_name
from parsers import instaloader, parse_dir

ALICE = owner("alice.example", 111, "Alice Example")


def parse(folder):
    return parse_dir(str(folder), str(folder), os.listdir(folder))


def test_single_image(tmp_path):
    write_post(tmp_path, "AAA111", 1717243200, ALICE, "image", caption="Hello #World and #world")
    r = parse(tmp_path)
    assert len(r.posts) == 1
    p = r.posts[0]
    assert p.id == "instagram:AAA111"
    assert p.kind == "image"
    assert p.url == "https://www.instagram.com/p/AAA111/"
    assert (p.author_id, p.author_handle, p.author_name) == ("111", "alice.example", "Alice Example")
    assert p.posted_at == 1717243200
    assert p.text == "Hello #World and #world"
    assert p.hashtags == ["world"]
    assert p.likes == 10 and p.comments == 2
    assert [(m.idx, m.kind) for m in p.media] == [(1, "image")]
    # metadata, media and caption are all claimed
    assert r.claimed == set(os.listdir(tmp_path))


def test_video_has_poster(tmp_path):
    base = write_post(tmp_path, "VID1", 1717243200, ALICE, "video", views=500, product_type="clips")
    p = parse(tmp_path).posts[0]
    assert p.kind == "video"
    assert p.url == "https://www.instagram.com/reel/VID1/"
    assert p.views == 500
    assert p.media[0].kind == "video"
    assert p.media[0].path == base + ".mp4"
    assert p.media[0].poster_path == base + ".jpg"


def test_carousel_mixed(tmp_path):
    write_post(tmp_path, "CAR1", 1717243200, ALICE, "carousel", slides=[False, True, False])
    p = parse(tmp_path).posts[0]
    assert p.kind == "carousel"
    assert [(m.idx, m.kind, m.poster_path is not None) for m in p.media] == [
        (1, "image", False), (2, "video", True), (3, "image", False)]


def test_compressed_json(tmp_path):
    write_post(tmp_path, "XZ1", 1717243200, ALICE, "image", compress=True)
    r = parse(tmp_path)
    assert [p.post_id for p in r.posts] == ["XZ1"]
    assert r.posts[0].meta_path.endswith(".json.xz")


def test_two_posts_one_folder(tmp_path):
    write_post(tmp_path, "P1", 1717243200, ALICE, "image")
    write_post(tmp_path, "P2", 1717243260, ALICE, "carousel", slides=[False, False])
    r = parse(tmp_path)
    by_id = {p.post_id: p for p in r.posts}
    assert len(by_id["P1"].media) == 1
    assert len(by_id["P2"].media) == 2


def test_story_item(tmp_path):
    node = {"__typename": "GraphStoryImage", "id": "3300000000000000001",
            "taken_at_timestamp": 1717243200, "owner": ALICE}
    base = str(tmp_path / base_name(1717243200))
    png(base + ".jpg")
    write_meta(base, node, node_type="StoryItem")
    p = parse(tmp_path).posts[0]
    assert p.kind == "story"
    assert p.post_id == "3300000000000000001"
    assert p.url == "https://www.instagram.com/stories/alice.example/3300000000000000001/"


def test_profile_files_are_claimed_not_posts(tmp_path):
    write_meta(str(tmp_path / "alice.example_111"), {"id": "111", "username": "alice.example"},
               node_type="Profile")
    png(str(tmp_path / "2024-01-01_00-00-00_UTC_profile_pic.jpg"))
    (tmp_path / "id").write_text("111")
    r = parse(tmp_path)
    assert r.posts == []
    assert r.claimed == set(os.listdir(tmp_path))


def test_orphan_media_not_claimed(tmp_path):
    write_post(tmp_path, "P1", 1717243200, ALICE, "image")
    png(str(tmp_path / "random.jpg"))
    r = parse(tmp_path)
    assert "random.jpg" not in r.claimed


def test_unrelated_json_ignored(tmp_path):
    (tmp_path / "notes.json").write_text('{"hello": 1}')
    r = parse(tmp_path)
    assert r.posts == [] and r.claimed == set() and r.errors == []


def test_broken_json_next_to_media_is_an_error(tmp_path):
    base = str(tmp_path / base_name(1717243200))
    png(base + ".jpg")
    open(base + ".json", "w").write("{not json")
    r = parse(tmp_path)
    assert r.posts == []
    assert len(r.errors) == 1 and "unreadable" in r.errors[0][1]


def test_iphone_shape_fallbacks(tmp_path):
    node = {"__typename": "XDTGraphImage", "code": "IPH1", "id": "5",
            "owner": {"id": "111"},
            "caption": {"text": "from the v1 api"},
            "iphone_struct": {"taken_at": 1717243200, "like_count": 7, "comment_count": 1,
                              "user": {"pk": "111", "username": "alice.example", "full_name": "A"}}}
    base = str(tmp_path / "x")
    png(base + ".jpg")
    write_meta(base, node)
    p = parse(tmp_path).posts[0]
    assert (p.post_id, p.kind, p.text) == ("IPH1", "image", "from the v1 api")
    assert (p.author_handle, p.posted_at, p.likes, p.comments) == ("alice.example", 1717243200, 7, 1)


def test_metadata_without_media_is_not_a_post(tmp_path):
    # What --no-pictures (videos only) leaves of an image post: its JSON and caption.
    base = str(tmp_path / base_name(1717243200))
    write_meta(base, post_node("NOPIC000001", 1717243200, owner("alice.example", 111)))
    open(base + ".txt", "w").write("a caption")
    r = parse(tmp_path)
    assert r.posts == [] and r.errors == []
    assert r.claimed == {os.path.basename(base) + ".json", os.path.basename(base) + ".txt"}
    # Without a post id it is still reported.
    write_meta(base, {"__typename": "GraphImage", "owner": {"id": "111"}})
    r = parse(tmp_path)
    assert r.posts == [] and [e for _, e in r.errors] == ["no post id in metadata"]


def test_parser_never_needs_network():
    # The parser module must not import instaloader itself (which could log in).
    src = open(instaloader.__file__).read()
    assert "import instaloader" not in src


# --- filename-only output (save_metadata=False, custom filename_pattern) -----

def _touch_png(path, mtime=None):
    png(str(path))
    if mtime:
        os.utime(path, (mtime, mtime))


def test_filename_only_profile(tmp_path):
    prof = tmp_path / "some.handle"
    prof.mkdir()
    # 2020-04-21 13:00 UTC
    _touch_png(prof / "some.handle-2020-04-21-B_QcFdCp9iM_1.jpg", 1587474000)
    _touch_png(prof / "some.handle-2020-04-21-B_QcFdCp9iM_2.jpg", 1587474000)
    _touch_png(prof / "some.handle-2020-06-01-CA6LFs3IkFf.jpg", 1590969600)
    open(prof / "some.handle-2020-06-02-CA8sX9zIaUz.mp4", "wb").write(b"x")
    # a shortcode that itself ends in _<digits>
    _touch_png(prof / "some.handle-2018-08-24-Bm3Lr49F_10.jpg")
    _touch_png(prof / "2024-08-19_18-02-19_UTC_profile_pic.jpg")
    r = parse_dir(str(tmp_path), str(prof), os.listdir(prof))
    by = {p.post_id: p for p in r.posts}
    assert set(by) == {"B_QcFdCp9iM", "CA6LFs3IkFf", "CA8sX9zIaUz", "Bm3Lr49F_10"}
    car = by["B_QcFdCp9iM"]
    assert car.kind == "carousel" and [m.idx for m in car.media] == [1, 2]
    assert car.posted_at == 1587474000                 # exact time from mtime
    assert car.author_handle == "some.handle" and car.author_id == "some.handle"
    assert car.url == "https://www.instagram.com/p/B_QcFdCp9iM/"
    assert car.tool == "instaloader (filenames)"
    assert by["CA8sX9zIaUz"].kind == "video"
    assert by["Bm3Lr49F_10"].kind == "image"
    assert r.claimed == set(os.listdir(prof))


def test_filename_only_mtime_off_day_falls_back_to_date(tmp_path):
    prof = tmp_path / "h"
    prof.mkdir()
    _touch_png(prof / "h-2020-04-21-AAAAAAAAAAA.jpg", 1700000000)   # copied later, mtime lost
    p = parse_dir(str(tmp_path), str(prof), os.listdir(prof)).posts[0]
    assert p.posted_at == 1587427200                                # 2020-04-21 00:00 UTC


def test_filename_only_highlights(tmp_path):
    prof = tmp_path / "somehandle"
    hl = prof / "#BARBIE"
    hl.mkdir(parents=True)
    _touch_png(hl / "#BARBIE-2025-07-09-DL5V1h4OX-O.jpg")
    _touch_png(hl / "2025-04-30_02-55-57_UTC_cover.jpg")
    r = parse_dir(str(tmp_path), str(hl), os.listdir(hl))
    p = r.posts[0]
    assert (p.kind, p.album, p.author_handle, p.url) == ("story", "#BARBIE", "somehandle", None)
    assert r.claimed == set(os.listdir(hl))
    # a highlight title that cannot be a handle, saved straight in the profile folder
    _touch_png(prof / "More food-2025-06-17-DLAPQ0fsLzE.jpg")
    p = parse_dir(str(tmp_path), str(prof), os.listdir(prof)).posts[0]
    assert (p.album, p.author_handle) == ("More food", "somehandle")


def test_filename_only_renamed_handle_keeps_folder_identity(tmp_path):
    prof = tmp_path / "feyafern"
    prof.mkdir()
    _touch_png(prof / "feya.fern-2026-01-25-DT835qwgqH_.jpg")
    p = parse_dir(str(tmp_path), str(prof), os.listdir(prof)).posts[0]
    assert (p.author_id, p.author_handle, p.post_id) == ("feyafern", "feya.fern", "DT835qwgqH_")


def test_metadata_wins_over_filename(tmp_path):
    # A folder with real metadata never double-indexes the same media.
    write_post(tmp_path, "AAA111", 1717243200, ALICE, "image")
    r = parse(tmp_path)
    assert [p.tool for p in r.posts] == ["instaloader"]


def test_filename_only_spaced_layout(tmp_path):
    prof = tmp_path / "somehandle2005"
    prof.mkdir()
    for i in (1, 2, 3):
        _touch_png(prof / f"somehandle2005 - C-GJ_y0vN0v - {i}.jpg", 1720000000)
    _touch_png(prof / "somehandle2005 - C-VnZi5PXXF.jpg", 1721000000)
    r = parse_dir(str(tmp_path), str(prof), os.listdir(prof))
    by = {p.post_id: p for p in r.posts}
    assert by["C-GJ_y0vN0v"].kind == "carousel" and len(by["C-GJ_y0vN0v"].media) == 3
    assert by["C-VnZi5PXXF"].posted_at == 1721000000
    assert by["C-VnZi5PXXF"].author_handle == "somehandle2005"
    assert r.claimed == set(os.listdir(prof))


# --- <root>/instagram/<name>: the platform's folder holds the profile folders --

def _layout(base):
    """A filename-only library under ``base``: profile folders, highlight
    subfolders, a highlight saved in the profile folder, a renamed profile,
    the older spaced names, and loose files."""
    for d in ("alice/Trips", "alice/#BARBIE", "feyafern", "Old.One"):
        os.makedirs(base / d)
    _touch_png(base / "alice" / "alice-2024-05-01-Cabc1234567_1.jpg", 1714564800)
    _touch_png(base / "alice" / "alice-2024-05-01-Cabc1234567_2.jpg", 1714564800)
    _touch_png(base / "alice" / "More food-2025-06-17-DLAPQ0fsLzE.jpg")
    _touch_png(base / "alice" / "Trips" / "Trips-2025-07-09-DL5V1h4OX-O.jpg")
    _touch_png(base / "alice" / "Trips" / "2025-04-30_02-55-57_UTC_cover.jpg")
    _touch_png(base / "alice" / "#BARBIE" / "#BARBIE-2025-07-10-DL5V1h4OX-P.jpg")
    _touch_png(base / "feyafern" / "feya.fern-2026-01-25-DT835qwgqH_.jpg")
    _touch_png(base / "Old.One" / "old.one - C-VnZi5PXXF.jpg", 1721000000)
    _touch_png(base / "loose.one-2024-05-02-Cloose12345.jpg")
    _touch_png(base / "Loose title-2024-05-03-Cloose12346.jpg")


def _parsed(root, base):
    """{post id: what identifies the post} of every post under ``base``,
    paths as under it."""
    out = {}
    for dirpath, _, names in os.walk(base):
        r = parse_dir(str(root), dirpath, names)
        assert r.claimed == set(names), dirpath
        for p in r.posts:
            assert p.post_id not in out
            out[p.post_id] = (p.kind, p.author_id, p.author_handle, p.album, p.url, p.posted_at, p.tool,
                              os.path.relpath(p.meta_path, base),
                              [(m.idx, m.kind, os.path.relpath(m.path, base)) for m in p.media])
    return out


def test_filename_only_under_the_platform_folder_reads_as_under_the_root(tmp_path):
    flat, new, upper = tmp_path / "flat", tmp_path / "new", tmp_path / "upper"
    for base in (flat, new / "instagram", upper / "Instagram"):
        _layout(base)
    want = _parsed(flat, flat)
    assert want["Cabc1234567"][:5] == ("carousel", "alice", "alice", None, "https://www.instagram.com/p/Cabc1234567/")
    assert want["DL5V1h4OX-O"][:5] == ("story", "alice", "alice", "Trips", None)
    assert want["DL5V1h4OX-P"][:5] == ("story", "alice", "alice", "#BARBIE", None)
    assert want["DLAPQ0fsLzE"][:4] == ("story", "alice", "alice", "More food")
    assert want["DT835qwgqH_"][:4] == ("image", "feyafern", "feya.fern", None)
    assert want["C-VnZi5PXXF"][:4] == ("image", "old.one", "old.one", None)
    assert want["Cloose12345"][:4] == ("image", "loose.one", "loose.one", None)
    assert want["Cloose12346"][:4] == ("story", None, None, "Loose title")
    assert len(want) == 8
    assert _parsed(new, new / "instagram") == want
    assert _parsed(upper, upper / "Instagram") == want


def test_filename_only_profile_named_instagram(tmp_path):
    # An older library: <root>/instagram is the profile's own folder (files
    # right in it are named after it), its subfolders its highlights, as
    # under any other profile folder.
    old = tmp_path / "old"
    prof = old / "instagram"
    os.makedirs(prof / "trips")
    os.makedirs(prof / "alice")
    _touch_png(prof / "instagram-2024-05-01-Cabc1234567.jpg")
    _touch_png(prof / "trips" / "trips-2025-07-09-DL5V1h4OX-O.jpg")
    _touch_png(prof / "alice" / "alice-2025-07-10-DL5V1h4OX-P.jpg")     # a highlight called "alice"
    got = _parsed(old, prof)
    assert got["Cabc1234567"][:4] == ("image", "instagram", "instagram", None)
    assert got["DL5V1h4OX-O"][:4] == ("story", "instagram", "instagram", "trips")
    assert got["DL5V1h4OX-P"][:4] == ("story", "instagram", "instagram", "alice")
    # The older spaced names say so as well, in any case.
    os.remove(prof / "instagram-2024-05-01-Cabc1234567.jpg")
    _touch_png(prof / "Instagram - C-VnZi5PXXF.jpg")
    assert _parsed(old, prof)["DL5V1h4OX-O"][:4] == ("story", "instagram", "instagram", "trips")

    # The same profile in the new layout: <root>/instagram/instagram.
    new = tmp_path / "new"
    prof = new / "instagram" / "instagram"
    os.makedirs(prof / "trips")
    os.makedirs(new / "instagram" / "alice")
    _touch_png(prof / "instagram-2024-05-01-Cabc1234567.jpg")
    _touch_png(prof / "trips" / "trips-2025-07-09-DL5V1h4OX-O.jpg")
    _touch_png(new / "instagram" / "alice" / "alice-2025-07-10-DL5V1h4OX-P.jpg")
    got = _parsed(new, new / "instagram")
    assert got["Cabc1234567"][:4] == ("image", "instagram", "instagram", None)
    assert got["DL5V1h4OX-O"][:4] == ("story", "instagram", "instagram", "trips")
    assert got["DL5V1h4OX-P"][:4] == ("image", "alice", "alice", None)


def test_filename_only_instagram_folder_with_no_file_named_after_it(tmp_path):
    # Nothing right in <root>/instagram says it is a profile's: it is the
    # platform's folder, and a folder in it a profile's, whatever is in it
    # (the highlights of a profile named "instagram" that kept no post of
    # its own read as profiles: nothing tells them apart).
    plat = tmp_path / "instagram"
    os.makedirs(plat / "trips")
    os.makedirs(plat / "bob")
    _touch_png(plat / "trips" / "trips-2025-07-09-DL5V1h4OX-O.jpg")
    _touch_png(plat / "bob" / "Summer 24-2025-07-10-DL5V1h4OX-P.jpg")
    _touch_png(plat / "2024-08-19_18-02-19_UTC_profile_pic.jpg")       # not a post's name
    got = _parsed(tmp_path, plat)
    assert got["DL5V1h4OX-O"][:4] == ("image", "trips", "trips", None)
    assert got["DL5V1h4OX-P"][:4] == ("story", "bob", "bob", "Summer 24")
    # A loose file of another profile there does not make it one either.
    _touch_png(plat / "carol-2024-05-01-Cabc1234567.jpg")
    got = _parsed(tmp_path, plat)
    assert got["Cabc1234567"][:4] == ("image", "carol", "carol", None)
    assert got["DL5V1h4OX-O"][:4] == ("image", "trips", "trips", None)


def test_filename_only_beside_metadata_under_the_platform_folder(tmp_path):
    # A profile folder with both: metadata keeps its files, the others are
    # the folder's (alice.example), not the platform folder's.
    prof = tmp_path / "instagram" / "alice.example"
    write_post(prof, "AAA111", 1717243200, ALICE, "image")
    _touch_png(prof / "alice.example-2024-05-01-Cabc1234567.jpg")
    r = parse_dir(str(tmp_path), str(prof), os.listdir(prof))
    assert sorted((p.tool, p.post_id, p.author_id, p.album) for p in r.posts) == [
        ("instaloader", "AAA111", "111", None),
        ("instaloader (filenames)", "Cabc1234567", "alice.example", None)]
    # A folder of another platform's name is a profile folder like any other.
    hl = tmp_path / "twitter" / "Trips"
    os.makedirs(hl)
    _touch_png(hl / "Trips-2025-07-09-DL5V1h4OX-O.jpg")
    p = parse_dir(str(tmp_path), str(hl), os.listdir(hl)).posts[0]
    assert (p.author_id, p.author_handle, p.album) == ("twitter", "twitter", "Trips")
