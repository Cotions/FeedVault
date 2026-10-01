import json
import os

from conftest import H
from fakes import YT_DLP, yt_dlp_case
from parsers import parse_dir, yt_dlp

TIKTOK = "6800000000000000002-20190727-7100000000000000001"
SHORT = "@shortsmaker-20261001-BBBBBBBBBB2"
PLAYLIST = "@shortsmaker-NA-UCexampleChannelBBBBBBB2"
VIDEO = "@somechannel-20050424-AAAAAAAAAA1"


def parse(folder):
    return parse_dir(str(folder), str(folder), os.listdir(folder))


def edit(path, **changes):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d.update(changes)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)


# --- shapes -------------------------------------------------------------------

def test_tiktok_video(tmp_path):
    yt_dlp_case("tiktok/video", tmp_path)
    r = parse(tmp_path)
    assert r.errors == [] and r.skipped == []
    assert r.claimed == set(os.listdir(tmp_path))
    [p] = r.posts
    assert p.id == "tiktok:7100000000000000001"
    assert (p.tool, p.tool_version, p.kind) == ("yt-dlp", "2026.08.19", "video")
    assert p.url == "https://www.tiktok.com/@somebody_tt/video/7100000000000000001"
    # The numeric uploader id is the account; the handle is in the profile URL.
    assert (p.author_id, p.author_handle, p.author_name) == ("6800000000000000002", "somebody_tt", "Some Body")
    assert p.posted_at == 1564234358
    # The title is the description cut short: the text is the description alone.
    assert p.text == "Guess what this is 😍❤️ #foryoupage #petsoftiktok #aesthetic"
    assert p.hashtags == ["foryoupage", "petsoftiktok", "aesthetic"]
    assert (p.likes, p.comments, p.views) == (56, 7, 1234)
    [m] = p.media
    assert (m.idx, m.kind, os.path.basename(m.path)) == (1, "video", TIKTOK + ".mp4")
    # TikTok's thumbnail keeps the ".image" of its URL: still the poster.
    assert os.path.basename(m.poster_path) == TIKTOK + ".image"
    assert p.meta_path == str(tmp_path / (TIKTOK + ".info.json"))
    assert p.side_files == []


def test_youtube_short_and_playlist_json(tmp_path):
    yt_dlp_case("youtube/short", tmp_path)
    r = parse(tmp_path)
    # The tab's own JSON and picture are claimed, not a post and not unmatched.
    assert r.claimed == set(os.listdir(tmp_path))
    assert {PLAYLIST + ".info.json", PLAYLIST + ".jpg"} <= r.claimed
    [p] = r.posts
    assert p.id == "youtube:BBBBBBBBBB2"
    # A YouTube @handle can change; the channel id is the account.
    assert (p.author_id, p.author_handle, p.author_name) == ("UCexampleChannelBBBBBBB2", "shortsmaker", "Shorts Maker")
    assert p.text == "and so it begins #shorts" and p.hashtags == ["shorts"]
    assert [os.path.basename(m.path) for m in p.media] == [SHORT + ".webm"]
    assert os.path.basename(p.media[0].poster_path) == SHORT + ".webp"


def test_youtube_title_and_description(tmp_path):
    yt_dlp_case("youtube/video", tmp_path)
    [p] = parse(tmp_path).posts
    assert p.text.startswith("A walk in the park\n\nSome words about the walk\n")
    assert p.url == "https://www.youtube.com/watch?v=AAAAAAAAAA1"


def test_side_files_belong_to_the_post(tmp_path):
    yt_dlp_case("youtube/video", tmp_path)
    for n in (VIDEO + ".en.vtt", VIDEO + ".description", VIDEO + ".live_chat.json"):
        (tmp_path / n).write_text("x")
    r = parse(tmp_path)
    assert r.claimed == set(os.listdir(tmp_path))
    assert sorted(os.path.basename(f) for f in r.posts[0].side_files) == [
        VIDEO + ".description", VIDEO + ".en.vtt", VIDEO + ".live_chat.json"]


def test_date_from_upload_date_without_timestamp(tmp_path):
    yt_dlp_case("youtube/video", tmp_path)
    edit(tmp_path / (VIDEO + ".info.json"), timestamp=None)
    assert parse(tmp_path).posts[0].posted_at == 1114300800      # 2005-04-24 00:00 UTC


def test_video_not_there_shows_its_thumbnail(tmp_path):
    yt_dlp_case("youtube/video", tmp_path, media=False)
    [p] = parse(tmp_path).posts
    assert p.kind == "image"
    assert [(m.kind, os.path.basename(m.path)) for m in p.media] == [("image", VIDEO + ".webp")]


def test_tiktok_thumbnail_alone_is_not_shown(tmp_path):
    # ".image" is no name a browser shows: kept with the post, not a picture.
    yt_dlp_case("tiktok/video", tmp_path, media=False)
    [p] = parse(tmp_path).posts
    assert p.media == [] and p.kind == "text"
    assert [os.path.basename(f) for f in p.side_files] == [TIKTOK + ".image"]


def test_platform_from_extractor_key(tmp_path):
    yt_dlp_case("youtube/video", tmp_path)
    edit(tmp_path / (VIDEO + ".info.json"), extractor_key="Instagram", id="C0FAKE00001")
    assert parse(tmp_path).posts[0].id == "instagram:C0FAKE00001"
    edit(tmp_path / (VIDEO + ".info.json"), extractor_key="SomeSite")
    assert parse(tmp_path).posts[0].platform == "somesite"


def test_other_json_is_not_ours(tmp_path):
    (tmp_path / "x.info.json").write_text(json.dumps({"id": "1", "title": "no extractor"}))
    (tmp_path / "x.mp4").write_bytes(b"\x00")
    r = parse(tmp_path)
    assert r.posts == [] and "x.info.json" not in r.claimed and r.errors == []


def test_unreadable_info_json_beside_media(tmp_path):
    (tmp_path / "a.info.json").write_text("{not json")
    (tmp_path / "a.mp4").write_bytes(b"\x00")
    r = parse(tmp_path)
    assert [os.path.basename(p) for p, _ in r.errors] == ["a.info.json"]


def test_parser_never_needs_network():
    src = open(yt_dlp.__file__).read()
    assert "import yt_dlp" not in src and "requests" not in src and "urllib" not in src


# --- long YouTube videos: ChannelVault's --------------------------------------

def test_long_youtube_video_is_claimed_and_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(yt_dlp, "youtube_max_seconds", lambda cfg=None: 180)
    yt_dlp_case("youtube/video", tmp_path)
    edit(tmp_path / (VIDEO + ".info.json"), duration=181)
    r = parse(tmp_path)
    assert r.posts == []
    assert r.claimed == set(os.listdir(tmp_path))
    assert r.skipped == [(str(tmp_path / (VIDEO + ".webm")),
                          "YouTube video longer than 3 min: left to ChannelVault")]
    edit(tmp_path / (VIDEO + ".info.json"), duration=180)
    assert [p.id for p in parse(tmp_path).posts] == ["youtube:AAAAAAAAAA1"]


def test_long_rule_is_youtube_only(tmp_path, monkeypatch):
    monkeypatch.setattr(yt_dlp, "youtube_max_seconds", lambda cfg=None: 180)
    yt_dlp_case("tiktok/video", tmp_path)
    edit(tmp_path / (TIKTOK + ".info.json"), duration=600)
    assert [p.id for p in parse(tmp_path).posts] == ["tiktok:7100000000000000001"]


def test_max_seconds_setting():
    assert yt_dlp.youtube_max_seconds({}) == 180
    assert yt_dlp.youtube_max_seconds({"youtube_max_seconds": 600}) == 600
    for bad in (0, -5, "600", True, 1.5):
        assert yt_dlp.youtube_max_seconds({"youtube_max_seconds": bad}) == 180


def test_scan_lists_long_video_on_unmatched(env, client):
    import config
    import scanner
    cfg = config.load()
    cfg["youtube_max_seconds"] = 60
    config.save(cfg)
    folder = env["media"] / "youtube"
    yt_dlp_case("youtube/video", folder)
    yt_dlp_case("youtube/short", folder)
    edit(folder / (VIDEO + ".info.json"), duration=61)
    r = scanner.scan(env["roots"])
    assert (r["added"], r["unmatched"], r["errors"]) == (1, 1, [])
    rows = client.get("/api/unmatched", headers=H).get_json()
    assert [(os.path.basename(u["path"]), u["reason"]) for u in rows] == [
        (VIDEO + ".webm", "YouTube video longer than 1 min: left to ChannelVault")]
    # Indexing the folder alone (after a sync) lists it the same way.
    scanner.index_dirs(env["roots"], [str(folder)])
    assert len(client.get("/api/unmatched", headers=H).get_json()) == 1


def test_scan_api_and_rescan(env, client):
    import scanner
    yt_dlp_case("tiktok/video", env["media"] / "tiktok")
    yt_dlp_case("youtube/short", env["media"] / "youtube")
    r = scanner.scan(env["roots"])
    assert (r["added"], r["unmatched"], r["errors"]) == (2, 0, [])
    full = client.get("/api/posts/tiktok/7100000000000000001", headers=H).get_json()
    assert full["source"]["tool"] == "yt-dlp"
    authors = {(a["platform"], a["handle"], a["url"]) for a in client.get("/api/authors", headers=H).get_json()}
    assert ("youtube", "shortsmaker", "https://www.youtube.com/@shortsmaker") in authors
    assert scanner.scan(env["roots"])["updated"] == 0


def test_poster_with_image_extension_is_served_as_jpeg(env, client):
    import scanner
    yt_dlp_case("tiktok/video", env["media"] / "tiktok")
    with open(env["media"] / "tiktok" / (TIKTOK + ".image"), "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 64)            # JPEG data, as TikTok sends it
    scanner.scan(env["roots"])
    m = client.get("/api/posts/tiktok/7100000000000000001", headers=H).get_json()["media"][0]
    r = client.get(f"/media/{m['id']}/poster", headers=H)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"


def test_fixtures_hold_no_real_urls():
    for dirpath, _, names in os.walk(YT_DLP):
        for n in names:
            if n.endswith(".json"):
                text = open(os.path.join(dirpath, n), encoding="utf-8").read()
                assert "googlevideo" not in text and "tiktokcdn" not in text and "ytimg" not in text
