import json
import os

from conftest import H
from fakes import GALLERY_DL, gallery_dl_case, png, write_post, owner
from parsers import gallery_dl, parse_dir


def parse(folder):
    return parse_dir(str(folder), str(folder), os.listdir(folder))


def jdump(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def fixture(case, n=0):
    """The n-th JSON of a fixture case, by name."""
    folder = os.path.join(GALLERY_DL, case)
    name = sorted(x for x in os.listdir(folder) if x.endswith(".json"))[n]
    with open(os.path.join(folder, name), encoding="utf-8") as f:
        return json.load(f)


# --- recognizing and grouping -------------------------------------------------

def test_single_photo_default_names(tmp_path):
    gallery_dl_case("twitter/photo", tmp_path)
    r = parse(tmp_path)
    assert len(r.posts) == 1
    p = r.posts[0]
    assert p.id == "twitter:565802608276047467"
    assert p.tool == "gallery-dl"
    assert p.kind == "image"
    assert [(m.idx, m.kind, os.path.basename(m.path)) for m in p.media] == [
        (1, "image", "565802608276047467_1.jpg")]
    assert p.meta_path == str(tmp_path / "565802608276047467_1.jpg.json")
    assert r.claimed == set(os.listdir(tmp_path))
    assert r.errors == []


def test_extension_format_json_names(tmp_path):
    # The metadata postprocessor with "extension-format": "json": x_1.json for x_1.jpg.
    gallery_dl_case("twitter/json_mode", tmp_path)
    assert sorted(os.listdir(tmp_path)) == ["565802608276047467_1.jpg", "565802608276047467_1.json"]
    r = parse(tmp_path)
    assert [p.id for p in r.posts] == ["twitter:565802608276047467"]
    assert [os.path.basename(m.path) for m in r.posts[0].media] == ["565802608276047467_1.jpg"]
    assert r.claimed == set(os.listdir(tmp_path))


def test_files_group_into_one_post_in_num_order(tmp_path):
    gallery_dl_case("twitter/four_photos", tmp_path)
    # gallery-dl names are not what orders a post: num is.
    for n in os.listdir(tmp_path):
        if n.endswith(".json"):
            d = json.load(open(tmp_path / n))
            d["num"] = 5 - d["num"]
            jdump(tmp_path / n, d)
    r = parse(tmp_path)
    assert len(r.posts) == 1
    p = r.posts[0]
    assert p.kind == "carousel"
    assert [(m.idx, os.path.basename(m.path)) for m in p.media] == [
        (1, "491623932184993703_4.jpg"), (2, "491623932184993703_3.jpg"),
        (3, "491623932184993703_2.jpg"), (4, "491623932184993703_1.jpg")]
    # The first file's JSON is the post's metadata path; the others go with it.
    assert p.meta_path.endswith("491623932184993703_4.jpg.json")
    assert sorted(os.path.basename(f) for f in p.side_files) == [
        "491623932184993703_1.jpg.json", "491623932184993703_2.jpg.json", "491623932184993703_3.jpg.json"]


def test_recognized_by_keys_not_folder(tmp_path):
    folder = tmp_path / "not-twitter" / "whatever"
    gallery_dl_case("twitter/photo", folder)
    r = parse_dir(str(tmp_path), str(folder), os.listdir(folder))
    assert [p.platform for p in r.posts] == ["twitter"]


def test_json_without_gallery_dl_keys_is_ignored(tmp_path):
    png(str(tmp_path / "a.jpg"))
    jdump(tmp_path / "a.jpg.json", {"id": 1, "date": "2024-01-01 00:00:00", "filename": "a", "extension": "jpg"})
    r = parse(tmp_path)
    assert r.posts == [] and r.claimed == set() and r.errors == []


def test_unreadable_json_next_to_its_file_is_an_error(tmp_path):
    png(str(tmp_path / "1_1.jpg"))
    (tmp_path / "1_1.jpg.json").write_text("{not json")
    r = parse(tmp_path)
    assert r.posts == []
    assert [os.path.basename(p) for p, _ in r.errors] == ["1_1.jpg.json"]
    assert "1_1.jpg" not in r.claimed           # the media goes to Unmatched


def test_media_missing_keeps_the_post(tmp_path):
    gallery_dl_case("twitter/four_photos", tmp_path)
    os.remove(tmp_path / "491623932184993703_2.jpg")
    p = parse(tmp_path).posts[0]
    assert p.kind == "carousel"
    assert [(m.idx, os.path.basename(m.path)) for m in p.media] == [
        (1, "491623932184993703_1.jpg"), (3, "491623932184993703_3.jpg"), (4, "491623932184993703_4.jpg")]


# --- unknown categories -----------------------------------------------------

def _generic(**kw):
    d = {"category": "examplesite", "subcategory": "post", "id": 42, "date": "2024-06-01 12:00:00",
         "user": {"id": 7, "name": "someone", "nick": "Some One"},
         "content": "hello #There", "num": 1, "filename": "abc", "extension": "jpg"}
    d.update(kw)
    return {k: v for k, v in d.items() if v is not None}


def test_unknown_category_uses_generic_keys(tmp_path):
    png(str(tmp_path / "42_1.jpg"))
    jdump(tmp_path / "42_1.jpg.json", _generic())
    png(str(tmp_path / "42_2.jpg"))
    jdump(tmp_path / "42_2.jpg.json", _generic(num=2, filename="def"))
    r = parse(tmp_path)
    assert len(r.posts) == 1
    p = r.posts[0]
    assert (p.platform, p.post_id, p.kind, p.url) == ("examplesite", "42", "carousel", None)
    assert (p.author_id, p.author_handle, p.author_name) == ("7", "someone", "Some One")
    assert p.posted_at == 1717243200
    assert p.text == "hello #There" and p.hashtags == ["there"]
    assert r.claimed == set(os.listdir(tmp_path))


def test_unknown_category_without_date_goes_to_unmatched(tmp_path):
    png(str(tmp_path / "42_1.jpg"))
    jdump(tmp_path / "42_1.jpg.json", _generic(date=None))
    r = parse(tmp_path)
    assert r.posts == []
    assert "42_1.jpg" not in r.claimed


def test_unknown_category_without_id_goes_to_unmatched(tmp_path):
    png(str(tmp_path / "x.jpg"))
    jdump(tmp_path / "x.jpg.json", _generic(id=None))
    r = parse(tmp_path)
    assert r.posts == [] and "x.jpg" not in r.claimed


def test_info_json_is_claimed_not_a_post(tmp_path):
    jdump(tmp_path / "info.json", {"category": "twitter", "subcategory": "user"})
    r = parse(tmp_path)
    assert r.posts == [] and r.claimed == {"info.json"} and r.errors == []


def test_platform_urls(tmp_path):
    gallery_dl_case("twitter/photo", tmp_path / "tw")
    gallery_dl_case("tiktok/video", tmp_path / "tt")
    gallery_dl_case("tiktok/photos", tmp_path / "tp")
    assert parse(tmp_path / "tw").posts[0].url == "https://x.com/example_user1/status/565802608276047467"
    assert parse(tmp_path / "tt").posts[0].url == "https://www.tiktok.com/@example_user6/video/3914719032600086255"
    assert parse(tmp_path / "tp").posts[0].url == "https://www.tiktok.com/@example_user8/photo/5228323407601338200"


# --- living next to instaloader ---------------------------------------------

ALICE = owner("alice.example", 111, "Alice Example")


def test_instaloader_and_gallery_dl_in_one_folder(tmp_path):
    write_post(tmp_path, "AAA111", 1717243200, ALICE, "carousel", slides=[False, True])
    gallery_dl_case("twitter/four_photos", tmp_path)
    r = parse(tmp_path)
    assert sorted(p.id for p in r.posts) == ["instagram:AAA111", "twitter:491623932184993703"]
    assert r.claimed == set(os.listdir(tmp_path))


def test_filename_fallback_leaves_gallery_dl_files_alone(tmp_path):
    # A TikTok title can look like instaloader's "{profile} - {shortcode}.ext".
    name = "7400000000000000001 Funny cats - compilation1.mp4"
    open(tmp_path / name, "wb").write(b"x")
    d = fixture("tiktok/video", 1)
    assert d["type"] == "video"
    d["id"] = "7400000000000000001"
    jdump(tmp_path / (name + ".json"), d)
    r = parse(tmp_path)
    assert [p.id for p in r.posts] == ["tiktok:7400000000000000001"]
    assert [os.path.basename(m.path) for m in r.posts[0].media] == [name]


def test_parser_never_needs_network():
    src = open(gallery_dl.__file__).read()
    assert "import gallery_dl" not in src and "requests" not in src and "urllib" not in src


# --- scanning, the API and the trash -----------------------------------------

def test_scan_api_and_platform_filter(env, client):
    import scanner
    write_post(env["media"] / "alice", "AAA111", 1717243200, ALICE, "image")
    gallery_dl_case("twitter/four_photos", env["media"] / "twitter" / "example_user2")
    gallery_dl_case("tiktok/video", env["media"] / "tiktok" / "example_user6")
    r = scanner.scan(env["roots"])
    assert (r["added"], r["unmatched"], r["errors"]) == (3, 0, [])
    stats = client.get("/api/stats", headers=H).get_json()
    assert stats["by_platform"] == {"instagram": 1, "twitter": 1, "tiktok": 1}
    tw = client.get("/api/posts?platform=twitter", headers=H).get_json()
    assert [p["id"] for p in tw["posts"]] == ["twitter:491623932184993703"]
    assert tw["posts"][0]["media_count"] == 4
    full = client.get("/api/posts/twitter/491623932184993703", headers=H).get_json()
    assert full["source"]["tool"] == "gallery-dl"
    assert full["url"] == "https://x.com/example_user2/status/491623932184993703"
    assert {(a["platform"], a["handle"]) for a in client.get("/api/authors", headers=H).get_json()} == {
        ("instagram", "alice.example"), ("twitter", "example_user2"), ("tiktok", "example_user6")}
    assert scanner.scan(env["roots"])["updated"] == 0      # a rescan changes nothing


def test_trash_takes_every_json_of_the_post(env, client):
    import scanner
    folder = env["media"] / "twitter" / "example_user2"
    gallery_dl_case("twitter/four_photos", folder)
    scanner.scan(env["roots"])
    before = sorted(os.listdir(folder))
    r = client.post("/api/delete", json={"posts": ["twitter:491623932184993703"]}, headers=H).get_json()
    assert r["ok"] and r["files"] == 8 and r["errors"] == []
    assert os.listdir(folder) == []
    # Left behind, the other three JSONs would bring the post back.
    r = scanner.scan(env["roots"])
    assert (r["added"], r["unmatched"]) == (0, 0)
    r = client.post("/api/trash/restore", json={"posts": ["twitter:491623932184993703"]}, headers=H).get_json()
    assert r["ok"] and r["files"] == 8
    assert sorted(os.listdir(folder)) == before
    assert len(client.get("/api/posts/twitter/491623932184993703", headers=H).get_json()["media"]) == 4


def test_trash_one_item_keeps_the_rest(env, client):
    import scanner
    folder = env["media"] / "twitter" / "example_user2"
    gallery_dl_case("twitter/four_photos", folder)
    scanner.scan(env["roots"])
    media = client.get("/api/posts/twitter/491623932184993703", headers=H).get_json()["media"]
    r = client.post("/api/delete", json={"media": [media[0]["id"]]}, headers=H).get_json()
    assert r["media"] == [media[0]["id"]] and r["files"] == 1
    r = scanner.scan(env["roots"])
    assert (r["added"], r["missing"], r["unmatched"]) == (0, 0, 0)
    post = client.get("/api/posts/twitter/491623932184993703", headers=H).get_json()
    assert [m["idx"] for m in post["media"]] == [2, 3, 4] and post["missing"] is False
