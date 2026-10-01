"""Profile links: the routing table, normalization, and adding a source by link."""
import os

from conftest import H

import config
import db
import sources

T = dict(sources.ROUTES)


def get(client, url, status=200):
    r = client.get(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def post(client, url, body, status=200):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def test_hosts_route_to_their_tool():
    for text, tool, host in [
            ("https://www.instagram.com/somebody/", "instaloader", "instagram.com"),
            ("https://x.com/someone/media", "gallery-dl", "x.com"),
            ("https://mobile.twitter.com/someone", "gallery-dl", "twitter.com"),
            ("https://old.reddit.com/user/someone/submitted", "gallery-dl", "reddit.com"),
            ("https://bsky.app/profile/someone.bsky.social", "gallery-dl", "bsky.app"),
            ("https://www.pixiv.net/en/users/123456", "gallery-dl", "pixiv.net"),
            ("https://www.youtube.com/@someone/shorts", "yt-dlp", "youtube.com"),
            ("https://m.youtube.com/@someone", "yt-dlp", "youtube.com"),
            ("https://www.tiktok.com/@someone", "yt-dlp", "tiktok.com")]:
        parsed, error = sources.parse_url(text, T)
        assert error is None, text
        assert parsed[1:] == (host, tool), text


def test_lookalike_hosts_refused():
    for text in ["https://x.com.evil.example/someone", "https://evilx.com/someone", "https://x.co/someone",
                 "https://tiktok.com.evil.example/@someone", "https://notyoutube.com/@x",
                 "https://youtube.com@evil.example/@x", "https://user:pass@x.com/someone",
                 "https://x.com:8080/someone", "https://evil.example/x.com/someone",
                 "https://x%2ecom/someone", "https://xn--x-com.example/a", "https://127.0.0.1/someone",
                 "https://[::1]/someone", "https://localhost/someone"]:
        parsed, error = sources.parse_url(text, T)
        assert parsed is None and error, text


def test_bad_links_refused():
    for text in [None, 3, "", "   ", "javascript:alert(1)", "file:///etc/passwd", "ftp://x.com/someone",
                 "https://x.com", "https://x.com/", "https://x.com/some one", "https://x.com/a\nb",
                 "https://x.com/a\x00b", "https://x.com/../etc", "https://x.com/a/./b", 'https://x.com/a"b',
                 "https://x.com/$(id)", "https://x.com/a;rm", "https://x.com/a`id`", "https://x.com/a\\b",
                 "https://x.com/" + "a" * 600]:
        parsed, error = sources.parse_url(text, T)
        assert parsed is None and error, repr(text)


def test_normalization():
    for text, want in [
            ("https://www.x.com/Someone/media/", "https://x.com/Someone/media"),
            ("x.com/someone", "https://x.com/someone"),
            ("http://X.COM//someone?s=20#top", "https://x.com/someone"),
            ("https://x.com:443/someone", "https://x.com/someone"),
            ("  https://www.tiktok.com/@some.one?lang=en  ", "https://tiktok.com/@some.one"),
            ("https://www.youtube.com/@some-one", "https://youtube.com/@some-one")]:
        assert sources.parse_url(text, T)[0][0] == want, text


def test_targets_start_with_https_never_a_dash():
    # The argv gets the target after "--" anyway; a link cannot look like a flag.
    for text in ["-x.com/someone", "--exec=id", "--cookies-from-browser firefox"]:
        parsed, _ = sources.parse_url(text, T)
        assert parsed is None or parsed[0].startswith("https://"), text


def test_clean_routes():
    table, error = sources.clean_routes({"www.TikTok.com": "gallery-dl", "patreon.com": "gallery-dl",
                                         "instagram.com": "instaloader"})
    assert error is None and table == {"tiktok.com": "gallery-dl", "patreon.com": "gallery-dl",
                                       "instagram.com": "instaloader"}
    for bad in [None, [], {}, {"x.com": "sh"}, {"x.com": None}, {"x.com/a": "gallery-dl"}, {"x": "gallery-dl"},
                {"evil.example": "instaloader"}, {"x.com.": "yt-dlp"}, {"-x.com": "yt-dlp"}, {3: "yt-dlp"},
                {f"h{i}.com": "yt-dlp" for i in range(101)}]:
        assert sources.clean_routes(bad)[0] is None, bad
    assert sources.routes({}) == sources.ROUTES
    assert sources.routes({"routes": {"x.com": "sh"}}) == sources.ROUTES       # a broken table: the defaults
    assert sources.routes({"routes": {"x.com": "yt-dlp"}}) == {"x.com": "yt-dlp"}


def test_longest_entry_wins():
    table = {"x.com": "gallery-dl", "media.x.com": "yt-dlp"}
    assert sources.route("media.x.com", table) == ("media.x.com", "yt-dlp")
    assert sources.route("www.x.com", table) == ("x.com", "gallery-dl")
    assert sources.route("x.com.evil.example", table) is None


def test_folder_names():
    for url, want in [("https://x.com/Someone/media", "someone"), ("https://tiktok.com/@some.one", "some.one"),
                      ("https://reddit.com/user/someone/submitted", "someone"),
                      ("https://bsky.app/profile/someone.bsky.social", "someone.bsky.social"),
                      ("https://pixiv.net/en/users/123456", "123456"), ("https://youtube.com/@a%20b", "a_20b"),
                      ("https://youtube.com/channel/UCabc", "ucabc"), ("https://x.com/media", "profile")]:
        assert sources.folder_name(url) == want, url


def test_resolve_endpoint(env, client):
    r = get(client, "/api/sources/resolve?url=https://www.tiktok.com/@someone")
    assert r == {"ok": True, "tool": "yt-dlp", "platform": "tiktok", "target": "https://tiktok.com/@someone",
                 "folder": str(env["media"] / "tiktok" / "someone"), "source": None}
    r = get(client, "/api/sources/resolve?url=x.com/someone/media")
    assert (r["tool"], r["platform"], r["target"]) == ("gallery-dl", "twitter", "https://x.com/someone/media")
    r = get(client, "/api/sources/resolve?url=https://instagram.com/Some.Body/")
    assert (r["tool"], r["target"], r["folder"]) == ("instaloader", "some.body", str(env["media"] / "some.body"))
    assert "routing" in get(client, "/api/sources/resolve?url=https://x.com.evil.example/a", 400)["error"]
    get(client, "/api/sources/resolve?url=https://www.instagram.com/p/C8xYzAbCdEf/", 400)
    get(client, "/api/sources/resolve", 400)


def test_create_by_link(env, client):
    s = post(client, "/api/sources", {"target": "https://www.x.com/Someone/media/"})["source"]
    assert (s["tool"], s["platform"], s["target"], s["url"]) == (
        "gallery-dl", "twitter", "https://x.com/Someone/media", "https://x.com/Someone/media")
    assert s["folder"] == str(env["media"] / "twitter" / "someone")
    s = post(client, "/api/sources", {"target": "https://www.youtube.com/@someone", "tool": "yt-dlp"})["source"]
    assert (s["tool"], s["platform"]) == ("yt-dlp", "youtube")
    s = post(client, "/api/sources", {"target": "https://www.instagram.com/somebody/"})["source"]
    assert (s["tool"], s["target"]) == ("instaloader", "somebody")
    # Same link written differently: the same source.
    assert "already" in post(client, "/api/sources", {"target": "x.com/Someone/media?s=1"}, 400)["error"]
    # The tool comes from the table, not the request.
    assert "gallery-dl" in post(client, "/api/sources", {"target": "https://x.com/other", "tool": "yt-dlp"},
                                400)["error"]
    for body in [{"target": "https://x.com.evil.example/a"}, {"target": "x.com/a; rm -rf ~"},
                 {"target": "https://x.com/$(id)"}, {"target": ["https://x.com/a"]}, {"target": "https://x.com/"},
                 {"target": "https://x.com/a", "tool": "sh"}]:
        assert post(client, "/api/sources", body, 400)["ok"] is False, body
    assert db.connect().execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 3


def test_create_refuses_a_stored_target_that_is_not_normalized(env):
    for tool, target in [("gallery-dl", "https://www.x.com/someone"), ("gallery-dl", "https://x.com/someone/"),
                         ("gallery-dl", "https://evil.example/someone"), ("yt-dlp", "--exec=id"),
                         ("instaloader", "https://x.com/someone")]:
        try:
            sources.create(db.connect(), env["roots"], tool, target, None, None, None,
                           sources.clean_options(None), 0)
        except sources.Refused:
            continue
        raise AssertionError(target)


def test_routes_in_settings(env, client):
    assert get(client, "/api/config")["routes"] == sources.ROUTES
    r = post(client, "/api/config", {"routes": {**sources.ROUTES, "tiktok.com": "gallery-dl"}})
    assert r["ok"] and r["config"]["routes"]["tiktok.com"] == "gallery-dl"
    assert config.load()["routes"]["tiktok.com"] == "gallery-dl"
    assert get(client, "/api/sources/resolve?url=https://www.tiktok.com/@someone")["tool"] == "gallery-dl"
    for bad in [{"x.com": "sh"}, {"evil.example": "instaloader"}, "x", {}]:
        r = post(client, "/api/config", {"routes": bad})
        assert r["ok"] is False, bad
    assert config.load()["routes"]["tiktok.com"] == "gallery-dl"
    # A host taken out of the table: its links are refused.
    post(client, "/api/config", {"routes": {"instagram.com": "instaloader"}})
    get(client, "/api/sources/resolve?url=https://x.com/someone", 400)
