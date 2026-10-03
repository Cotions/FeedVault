"""POST /api/save with an X or TikTok post's link: gallery-dl and yt-dlp fakes only."""
import os
import sqlite3

import pytest

from conftest import H
from test_sync_tools import (DAY, TS, USER, add, ended, fake, get, post, run_sync, set_config,  # noqa: F401
                             tt_account, x_account)

import archives
import config
import db
import jobs
import save
import save_tools

X_ID = "1800000000000000001"
TT_ID = "7300000000000000001"
X_LINK = f"https://x.com/someone/status/{X_ID}"
TT_LINK = f"https://www.tiktok.com/@someone/video/{TT_ID}"
X_PROFILE = "https://x.com/someone/media"
TT_PROFILE = "https://tiktok.com/@someone"


def save_link(client, url, status=200):
    return post(client, "/api/save", {"url": url}, status)


def save_now(client, url):
    r = save_link(client, url)
    assert r["have"] is False, r
    return ended(r["job"]["id"])


def data_dir():
    return config.load()["data_directory"]


def archive_entries(tool):
    p = archives.path(tool, data_dir())
    if not os.path.exists(p):
        return set()
    if tool == "yt-dlp":
        return set(open(p).read().splitlines())
    conn = sqlite3.connect(p)
    try:
        return {r[0] for r in conn.execute("SELECT entry FROM archive")}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The link
# ---------------------------------------------------------------------------

GOOD = [
    (X_LINK, {"platform": "twitter", "id": X_ID}),
    (f"https://twitter.com/someone/status/{X_ID}", {"platform": "twitter", "id": X_ID}),
    (f"https://www.x.com/someone/status/{X_ID}/", {"platform": "twitter", "id": X_ID}),
    (f"https://mobile.twitter.com/someone/status/{X_ID}", {"platform": "twitter", "id": X_ID}),
    (f"https://X.COM/someone/status/{X_ID}", {"platform": "twitter", "id": X_ID}),
    (f"https://x.com/i/web/status/{X_ID}", {"platform": "twitter", "id": X_ID}),
    (f"https://x.com/someone/status/{X_ID}/photo/2", {"platform": "twitter", "id": X_ID}),
    (f"https://x.com/someone/status/{X_ID}?s=20&t=abc#reply", {"platform": "twitter", "id": X_ID}),
    (f"https://x.com/someone/status/{X_ID}?url=$(id)", {"platform": "twitter", "id": X_ID}),
    (TT_LINK, {"platform": "tiktok", "id": TT_ID, "handle": "someone"}),
    (f"https://tiktok.com/@some.one_2/video/{TT_ID}?is_from_webapp=1&sender_device=pc",
     {"platform": "tiktok", "id": TT_ID, "handle": "some.one_2"}),
    (f"https://m.tiktok.com/@someone/video/{TT_ID}/", {"platform": "tiktok", "id": TT_ID, "handle": "someone"}),
]


@pytest.mark.parametrize("url,want", GOOD, ids=[u for u, _ in GOOD])
def test_post_links_parse(url, want):
    assert save_tools.parse_link(url) == want


BAD = [
    # other hosts, look-alikes and short links (refused, never followed: nothing is fetched)
    f"https://x.com.evil.com/someone/status/{X_ID}",
    f"https://evil.com/x.com/someone/status/{X_ID}",
    f"https://evilx.com/someone/status/{X_ID}",
    f"https://api.x.com/someone/status/{X_ID}",
    f"https://x.com@evil.com/someone/status/{X_ID}",
    f"https://someone@x.com/someone/status/{X_ID}",
    f"https://x.com:8443/someone/status/{X_ID}",
    "https://t.co/AbCdEf1234",
    "https://vm.tiktok.com/ZMabcdef/",
    "https://vt.tiktok.com/ZSabcdef/",
    f"https://tiktok.com.evil.com/@someone/video/{TT_ID}",
    f"https://www.instagram.com/p/{X_ID}/",
    f"ftp://x.com/someone/status/{X_ID}",
    f"javascript://x.com/someone/status/{X_ID}",
    f"x.com/someone/status/{X_ID}",
    f"//x.com/someone/status/{X_ID}",
    # not a post's path
    "https://x.com/someone",
    "https://x.com/someone/media",
    f"https://x.com/someone/likes/{X_ID}",
    f"https://x.com/some/one/status/{X_ID}",
    f"https://x.com/someone/status/{X_ID}/analytics",
    f"https://x.com/someone/status/{X_ID}/photo/9",
    f"https://x.com/a_name_far_too_long/status/{X_ID}",
    "https://tiktok.com/@someone",
    f"https://www.tiktok.com/someone/video/{TT_ID}",
    f"https://www.tiktok.com/@someone/photo/{TT_ID}",
    f"https://www.tiktok.com/@some-one/video/{TT_ID}",
    f"https://www.tiktok.com/@/video/{TT_ID}",
    f"https://www.tiktok.com/@{'a' * 25}/video/{TT_ID}",
    f"https://www.tiktok.com/@$(id)/video/{TT_ID}",
    f"https://www.tiktok.com/@someone/video/{TT_ID}/../../x",
    # ids that are not digits, or not a sane number
    "https://x.com/someone/status/abc",
    "https://x.com/someone/status/12a4",
    "https://x.com/someone/status/-1",
    "https://x.com/someone/status/0123",
    "https://x.com/someone/status/0",
    f"https://x.com/someone/status/{'9' * 21}",
    "https://x.com/someone/status/１２３",                       # fullwidth digits
    "https://x.com/someone/status/١٢٣",                         # Arabic-Indic digits
    "https://www.tiktok.com/@someone/video/7300x",
    "https://x.com/someone/status/$(id)",
    "https://x.com/someone/status/1;rm -rf ~",
    "https://x.com/someone/status/1%0a2",
    # flags and shells
    f"-https://x.com/someone/status/{X_ID}",
    f"--exec=id https://x.com/someone/status/{X_ID}",
    "--exec=id",
    "$(id)",
    f"$(id)https://x.com/someone/status/{X_ID}",
    f"https://x.com/someone/status/{X_ID}\n--exec=id",
    f" https://x.com/someone/status/{X_ID}",
    f"https://x.com/some one/status/{X_ID}",
    f"https://x.com/someone/status/{X_ID}\x00",
    f"https://x.com/someone/status/{X_ID}" + "?" + "a" * 500,
    "", None, 12, [X_LINK], {"url": X_LINK},
]


@pytest.mark.parametrize("url", BAD, ids=repr)
def test_other_links_are_refused(env, client, fake, url):
    with pytest.raises(save_tools.BadLink):
        save_tools.parse_link(url)
    r = save_link(client, url, 400)
    assert r["ok"] is False and r["error"]
    assert fake.runs() == [] and jobs.active() == []


def test_short_links_say_why(env, client, fake):
    for url, site in [("https://vm.tiktok.com/ZMabcdef/", "TikTok"), ("https://t.co/AbCdEf1234", "X")]:
        r = save_link(client, url, 400)
        assert "short link" in r["error"] and site in r["error"]
    assert fake.runs() == []


def test_only_the_url(env, client, fake):
    for body in [{"url": X_LINK, "platform": "twitter"}, {"url": X_LINK, "args": ["--exec", "id"]},
                 {"url": X_LINK, "folder": "/tmp"}, {"platform": "twitter", "shortcode": X_ID},
                 {"platform": "tiktok", "id": TT_ID}, {"link": X_LINK}, [X_LINK], X_LINK]:
        r = client.post("/api/save", json=body, headers=H)
        assert r.status_code == 400, body
    assert fake.runs() == [] and jobs.active() == []


def test_the_header_is_needed(env, client, fake):
    r = client.post("/api/save", json={"url": X_LINK})
    assert r.status_code == 403
    assert fake.runs() == []


def test_jobs_api_does_not_start_them(env, client, fake):
    for kind in ("gallery-dl-post", "yt-dlp-post"):
        r = post(client, "/api/jobs", {"kind": kind, "params": {"platform": "twitter", "id": X_ID}}, 400)
        assert "/api/save" in r["error"]
    assert fake.runs() == []


def test_the_tool_gets_a_link_built_from_the_id(env, client, fake):
    """Query, fragment, the name on X and the host as written never reach
    the tool: the link after -- is built from the checked id."""
    fake.put(X_PROFILE, x_account((1, 1)))
    job = save_now(client, f"https://twitter.com/Someone/status/{X_ID}/photo/1?s=20&x=$(id)#--exec")
    argv = fake.runs("gallery-dl")[0]["argv"]
    assert argv[-2:] == ["--", f"https://x.com/i/web/status/{X_ID}"]
    assert argv.count("--") == 1
    stage = os.path.join(data_dir(), "gallery-dl", "saving", f"twitter-{X_ID}")
    assert argv[:3] == ["--write-metadata", "-D", stage]
    assert job["argv"][-1] == argv[-1]

    fake.put(TT_PROFILE, tt_account(1))
    save_now(client, f"https://tiktok.com/@someone/video/{TT_ID}?lang=en&q=$(id)")
    argv = fake.runs("yt-dlp")[0]["argv"]
    assert argv[-2:] == ["--", f"https://www.tiktok.com/@someone/video/{TT_ID}"]
    assert "--no-playlist" in argv and "--write-info-json" in argv
    o = argv[argv.index("-o") + 1]
    assert o.startswith(os.path.join(data_dir(), "yt-dlp", "saving", f"tiktok-{TT_ID}") + os.sep)


def test_a_hostile_name_or_id_never_reaches_a_job(env, client, fake):
    """The job's params are checked again when it is built: a forged job
    (not through the route) with a name or id shaped like a flag or a
    command is refused before anything runs."""
    for params in [{"platform": "tiktok", "id": TT_ID, "handle": "-x"}, {"platform": "tiktok", "id": "$(id)"},
                   {"platform": "tiktok", "id": TT_ID, "handle": "$(id)"},
                   {"platform": "twitter", "id": "-1"}, {"platform": "twitter", "id": X_ID, "handle": "someone"},
                   {"platform": "tiktok", "id": TT_ID}]:
        kind = save_tools.PLATFORMS[params["platform"]]["kind"]
        with pytest.raises(jobs.BadRequest):
            jobs.submit(kind, params)
    with pytest.raises(jobs.BadRequest):
        jobs.submit("gallery-dl-post", {"platform": "tiktok", "id": TT_ID, "handle": "someone"})
    assert fake.runs() == [] and jobs.active() == []


def test_a_dollar_in_the_data_directory_is_refused(env, client, fake):
    set_config(data_directory=str(env["tmp"] / "da$HOME"))
    r = save_link(client, X_LINK, 400)
    assert "$" in r["error"]
    assert fake.runs() == []


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def test_x_post_saved_into_a_new_folder_for_its_owner(env, client, fake):
    fake.put(X_PROFILE, x_account((1, 2), (2, 1)))
    job = save_now(client, X_LINK)
    assert job["state"] == "done", job
    folder = os.path.join(env["roots"][0], "twitter", "someone")
    assert job["result"]["post"] == f"twitter:{X_ID}" and job["result"]["folder"] == folder
    assert sorted(os.listdir(folder)) == [f"{X_ID}_1.jpg", f"{X_ID}_1.jpg.json", f"{X_ID}_2.jpg",
                                          f"{X_ID}_2.jpg.json"]
    assert job["result"]["account"] == {"platform": "twitter", "id": "900"}
    assert not os.path.exists(os.path.join(data_dir(), "gallery-dl", "saving", f"twitter-{X_ID}"))
    # gallery-dl's entries for both files, and yt-dlp's line: either tool skips it.
    assert archive_entries("gallery-dl") == {f"twitter{X_ID}_0_1", f"twitter{X_ID}_0_2"}
    assert archive_entries("yt-dlp") == {f"twitter {X_ID}"}
    assert job["result"]["archived"] == 3
    # Saved: answered without running anything, and shown as saved.
    assert post(client, "/api/saved", {"ids": [f"twitter:{X_ID}", "twitter:1800000000000000002"]})["saved"] \
        == [f"twitter:{X_ID}"]
    r = save_link(client, f"https://twitter.com/someone/status/{X_ID}")
    assert r == {"ok": True, "have": True, "post": {"id": f"twitter:{X_ID}", "path": f"/p/twitter/{X_ID}"}}
    assert len(fake.runs()) == 1
    assert get(client, f"/api/posts/twitter/{X_ID}")["id"] == f"twitter:{X_ID}"


def test_tiktok_video_saved_without_its_cookies(env, client, fake):
    set_config(**{"yt-dlp": {"pause": 0, "session": {"mode": "cookies", "browser": "firefox"}}})
    fake.put(TT_PROFILE, tt_account(1, 2))
    job = save_now(client, TT_LINK)
    assert job["state"] == "done", job
    argv = fake.runs("yt-dlp")[0]["argv"]
    assert argv[argv.index("--cookies-from-browser") + 1] == "firefox"
    folder = os.path.join(env["roots"][0], "tiktok", "someone")
    base = f"6800000000000000009-20240602-{TT_ID}"
    assert sorted(os.listdir(folder)) == [f"{base}.image", f"{base}.info.json", f"{base}.mp4"]
    assert "FAKE-SECRET" not in open(os.path.join(folder, f"{base}.info.json")).read()
    assert archive_entries("yt-dlp") == {f"tiktok {TT_ID}"}
    assert post(client, "/api/saved", {"ids": [f"tiktok:{TT_ID}"]})["saved"] == [f"tiktok:{TT_ID}"]


def test_saved_into_the_folder_of_the_owners_source_and_the_next_sync_skips_it(env, client, fake):
    """X: a gallery-dl source synced once; a newer post saved from the
    browser lands in its folder, and the next sync does not fetch it."""
    fake.put(X_PROFILE, x_account((1, 1)))
    src = add(client, X_PROFILE)
    assert run_sync(client, src["id"])["state"] == "done"
    new = "1800000000000000002"
    fake.put(X_PROFILE, x_account((1, 1), (2, 2)))
    job = save_now(client, f"https://x.com/someone/status/{new}")
    assert job["state"] == "done" and job["result"]["folder"] == src["folder"]
    before = {n: os.stat(os.path.join(src["folder"], n)).st_mtime_ns for n in os.listdir(src["folder"])}
    sync = run_sync(client, src["id"])
    assert sync["state"] == "done" and sync["result"]["added"] == 0, sync
    after = {n: os.stat(os.path.join(src["folder"], n)).st_mtime_ns for n in os.listdir(src["folder"])}
    assert after == before                     # nothing written again
    assert get(client, f"/api/posts/twitter/{new}")["id"] == f"twitter:{new}"


def test_tiktok_saved_then_synced_is_not_fetched_again(env, client, fake):
    fake.put(TT_PROFILE, tt_account(1))
    src = add(client, TT_PROFILE)
    assert run_sync(client, src["id"])["state"] == "done"
    new = "7300000000000000002"
    fake.put(TT_PROFILE, tt_account(1, 2))
    job = save_now(client, f"https://www.tiktok.com/@someone/video/{new}")
    assert job["state"] == "done" and job["result"]["folder"] == src["folder"]
    sync = run_sync(client, src["id"])
    assert sync["state"] == "done" and sync["result"]["added"] == 0, sync
    log = jobs.log(sync["id"])["lines"]
    assert any(f"{new}: has already been recorded in the archive" in line["text"] for line in log)
    assert not any("Destination:" in line["text"] for line in log)


def test_a_saved_post_seeds_nothing_wrong_for_a_new_source(env, client, fake):
    """Saved first, the source added after: its folder is the one the save
    made, and the first sync (seeded) does not download the post again."""
    fake.put(X_PROFILE, x_account((1, 1), (2, 1)))
    assert save_now(client, X_LINK)["state"] == "done"
    src = add(client, X_PROFILE)
    assert src["folder"] == os.path.join(env["roots"][0], "twitter", "someone")
    sync = run_sync(client, src["id"])
    assert sync["state"] == "done" and sync["result"]["added"] == 1, sync


def test_a_file_already_there_is_kept(env, client, fake):
    fake.put(X_PROFILE, x_account((1, 1)))
    folder = env["media"] / "twitter" / "someone"
    folder.mkdir(parents=True)
    (folder / f"{X_ID}_1.jpg").write_bytes(b"mine")
    job = save_now(client, X_LINK)
    assert (folder / f"{X_ID}_1.jpg").read_bytes() == b"mine"
    assert any("1 already there" in line["text"] for line in jobs.log(job["id"])["lines"])


def test_failures_are_named(env, client, fake):
    job = save_now(client, "https://x.com/someone/status/1899999999999999999")
    assert job["state"] == "failed" and job["result"]["error"] == "not_found", job
    assert job["message"].startswith("Post not found")
    job = save_now(client, "https://www.tiktok.com/@someone/video/7399999999999999999")
    assert job["state"] == "failed" and job["result"]["error"] == "not_found", job
    fake.put(X_PROFILE, x_account((1, 1)), fail="429")
    job = save_now(client, X_LINK)
    assert job["result"]["error"] == "rate_limited" and job["message"].startswith("X is limiting")
    fake.put(TT_PROFILE, tt_account(1), fail="login")
    job = save_now(client, TT_LINK)
    assert job["result"]["error"] == "login_required" and "Settings" in job["message"]
    for tool in ("gallery-dl", "yt-dlp"):
        assert os.listdir(os.path.join(data_dir(), tool, "saving")) == []


def test_tool_missing(env, client, monkeypatch):
    monkeypatch.setattr(jobs, "tool_path", lambda name: None)
    r = save_link(client, X_LINK)
    job = ended(r["job"]["id"])
    assert job["state"] == "failed" and job["result"]["error"] == "missing"


def test_one_job_per_post_and_one_limit_for_all(env, client, fake, monkeypatch):
    set_config(save_queue_max=2, **{"gallery-dl": {"pause": 60}, "yt-dlp": {"pause": 60}})
    monkeypatch.setitem(jobs._cool, "gallery-dl", 2e9)          # held: nothing starts
    monkeypatch.setitem(jobs._cool, "yt-dlp", 2e9)
    first = save_link(client, X_LINK)
    again = save_link(client, f"https://twitter.com/x/status/{X_ID}")
    assert again["existing"] is True and again["job"]["id"] == first["job"]["id"]
    save_link(client, TT_LINK)
    r = save_link(client, "https://x.com/someone/status/1800000000000000002", 429)
    assert "2 posts are already waiting" in r["error"]
    assert {j["kind"] for j in jobs.active()} == {"gallery-dl-post", "yt-dlp-post"}
    assert save.KINDS >= {"instaloader-post", "gallery-dl-post", "yt-dlp-post"}
    assert fake.runs() == []


def test_owner_folder_order(env, client, fake):
    conn = db.connect()
    roots = env["roots"]
    root = roots[0]
    assert save_tools.owner_folder(conn, roots, "twitter", "gallery-dl", None, None) == os.path.join(root, "_saved")
    assert save_tools.owner_folder(conn, roots, "twitter", "gallery-dl", "900", "Some.One") \
        == os.path.join(root, "twitter", "some.one")
    assert save_tools.owner_folder(conn, roots, "twitter", "gallery-dl", "900", "..") == os.path.join(root, "_saved")
    fake.put("https://x.com/someone", x_account((1, 1)))
    src = add(client, "https://x.com/someone", folder=str(env["media"] / "x-folder"))
    # By the link's name, before any sync knows the account.
    assert save_tools.owner_folder(conn, roots, "twitter", "gallery-dl", "900", "SomeOne") == src["folder"]
    assert save_tools.owner_folder(conn, roots, "twitter", "gallery-dl", "901", "other") \
        == os.path.join(root, "twitter", "other")
    assert USER["id"] == 900

