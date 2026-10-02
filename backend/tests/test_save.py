"""POST /api/save: one Instagram post by shortcode, with the fake instaloader."""
import os

import pytest

from conftest import H
from fakes import owner, write_post
from test_sync import DAY, TS, add_source, carol_archive, ended, fake, post, set_config  # noqa: F401

import config
import db
import jobs
import save
import scanner

CODE = "CSAVEME0001"


def profile(code=CODE, handle="carol.cooks", uid=777, kind="image", ts=TS + 10 * DAY, **extra):
    return {handle: {"id": uid, "name": handle.title(), "posts": [
        {"shortcode": code, "ts": ts, "caption": "saved from the browser", "kind": kind, **extra}]}}


def save_post(client, code=CODE, status=200, **extra):
    return post(client, "/api/save", {"platform": "instagram", "shortcode": code, **extra}, status)


def save_now(client, code=CODE):
    r = save_post(client, code)
    assert r["have"] is False
    return ended(r["job"]["id"])


# ---------------------------------------------------------------------------
# What the request may hold
# ---------------------------------------------------------------------------

BAD = ["-x", "abc", "a" * 41, "$(id)", "x; rm -rf ~", "CSAVE\nME01", "CSAVEME01\n", " CSAVEME01",
       "CSAVE/ME01", "../../etc", "CSAVEME01?x=1", "https://www.instagram.com/p/CSAVEME01/",
       "CSAVEМE01",                            # Cyrillic М
       "CSAVEME０1",                           # fullwidth zero
       "CSAVEME01​", "CSAVE\x00ME01", "", None, 12345, ["CSAVEME01"], {"code": "CSAVEME01"}]


@pytest.mark.parametrize("code", BAD, ids=repr)
def test_bad_shortcodes_are_refused(env, client, fake, code):
    r = save_post(client, code, 400)
    assert r["ok"] is False
    assert fake.runs() == [] and jobs.active() == []


def test_only_platform_and_shortcode(env, client, fake):
    for body in [{"platform": "instagram"}, {"shortcode": CODE}, {"platform": "twitter", "shortcode": CODE},
                 {"platform": "Instagram", "shortcode": CODE},
                 {"platform": "instagram", "shortcode": CODE, "folder": "/tmp"},
                 {"platform": "instagram", "shortcode": CODE, "args": ["--login", "me"]},
                 {"platform": "instagram", "shortcode": CODE, "url": "https://evil.example/"}, [CODE], "x"]:
        r = client.post("/api/save", json=body, headers=H)
        assert r.status_code == 400, body
    assert jobs.active() == []


def test_option_like_shortcodes_arrive_as_one_literal_argument(env, client, fake):
    """Valid by the pattern, yet shaped like flags: each is the last argument,
    after --, as instaloader's ``-<shortcode>`` target, and only that."""
    for code in ["--dirname-pattern", "-----", "--login", "-x-x-x"]:
        job = save_now(client, code)
        assert job["argv"][-2:] == ["--", "-" + code]
        assert job["argv"].count("--") == 1 and "--login" not in job["argv"][:-1]
        assert job["state"] == "failed" and job["result"]["error"] == "not_found"
    runs = fake.runs()
    assert [r["argv"][-1] for r in runs] == ["---dirname-pattern", "------", "---login", "--x-x-x"]


def test_jobs_api_does_not_start_saves(env, client, fake):
    r = post(client, "/api/jobs", {"kind": save.KIND, "params": {"shortcode": CODE}}, 400)
    assert "/api/save" in r["error"]
    assert fake.runs() == []


def test_guard_header_and_host(env, client, fake):
    body = {"platform": "instagram", "shortcode": CODE}
    assert client.post("/api/save", json=body).status_code == 403
    assert client.post("/api/save", json=body, headers={**H, "Host": "evil.example"}).status_code == 403
    assert client.post("/api/save", json=body, headers={**H, "Host": "localhost.evil.example:3380"}).status_code == 403
    assert jobs.active() == [] and fake.runs() == []


def test_forged_origin_gets_no_cors(env, client, fake):
    """A page on instagram.com cannot send the header: its preflight is
    answered without any Access-Control-Allow-* header, so the browser never
    sends the request."""
    origin = {"Origin": "https://www.instagram.com"}
    pre = client.options("/api/save", headers={**origin, "Access-Control-Request-Method": "POST",
                                                "Access-Control-Request-Headers": "x-feedvault, content-type"})
    assert not [k for k in pre.headers.keys() if k.lower().startswith("access-control-")]
    r = client.post("/api/save", json={"platform": "instagram", "shortcode": CODE}, headers={**H, **origin})
    assert not [k for k in r.headers.keys() if k.lower().startswith("access-control-")]
    ended(r.get_json()["job"]["id"])


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def test_argv(env, client, fake):
    fake.set(profile())
    set_config(instaloader={"pause": 0, "session": {"mode": "cookies", "browser": "firefox"}})
    job = save_now(client, CODE)
    stage = os.path.join(config.load()["data_directory"], "instaloader", "saving", CODE)
    assert job["argv"] == ["instaloader", "--no-compress-json", "--dirname-pattern", stage,
                           "--filename-pattern", "{shortcode}", "--load-cookies", "firefox", "--", "-" + CODE]
    assert (job["kind"], job["group"], job["label"], job["params"]) == \
        (save.KIND, "instaloader", f"Save post {CODE}", {"shortcode": CODE})
    assert not os.path.exists(stage)           # emptied and removed


def test_already_have_runs_nothing(env, client, fake):
    carol_archive(env)
    r = save_post(client, "CCCCCCCCCC1")
    assert r == {"ok": True, "have": True,
                 "post": {"id": "instagram:CCCCCCCCCC1", "path": "/p/instagram/CCCCCCCCCC1"}}
    assert fake.runs() == [] and jobs.active() == []


def test_saved_into_the_owners_source_folder(env, client, fake):
    """The owner has a source: its folder, named as that folder's files are."""
    folder = carol_archive(env)
    add_source(client)
    fake.set(profile(kind="carousel", slides=2))
    job = save_now(client)
    assert job["state"] == "done", job
    assert job["result"]["post"] == f"instagram:{CODE}" and job["result"]["folder"] == str(folder)
    assert job["result"]["added"] == 1
    day = "2024-06-11"
    for suffix in ("_1.jpg", "_2.jpg", ".json", ".txt"):
        assert (folder / f"carol.cooks-{day}-{CODE}{suffix}").is_file()
    p = db.get_post(db.connect(), "instagram", CODE)
    assert p["author"]["handle"] == "carol.cooks" and p["media_count"] == 2
    # The next click answers from the index.
    assert save_post(client)["have"] is True


def test_saved_into_the_folder_with_most_of_the_owners_posts(env, client, fake):
    """No source: the folder holding the owner's posts (by account id,
    whatever the folder's name and the handle then)."""
    folder = env["media"] / "Carol (old)"
    write_post(folder, "COLDPOST001", TS, owner("carol_old_name", "777"))
    scanner.scan(env["roots"])
    fake.set(profile())
    job = save_now(client)
    assert job["state"] == "done" and job["result"]["folder"] == str(folder)
    assert (folder / "2024-06-11_12-00-00_UTC.jpg").is_file()        # the folder's layout


def test_saved_into_an_existing_handle_folder(env, client, fake):
    (env["media"] / "dora").mkdir()
    fake.set(profile(handle="dora", uid=999))
    job = save_now(client)
    assert job["result"]["folder"] == str(env["media"] / "dora")


def test_owner_without_a_folder_goes_to_saved(env, client, fake):
    fake.set(profile(handle="stranger", uid=4242))
    job = save_now(client)
    saved = env["media"] / "_saved"
    assert job["state"] == "done" and job["result"]["folder"] == str(saved)
    assert (saved / f"stranger-2024-06-11-{CODE}.jpg").is_file()
    assert job["result"]["account"] == {"platform": "instagram", "id": "4242"}
    assert not (env["media"] / "stranger").exists()


def test_owner_folder_keeps_its_layout(env, client, fake):
    """A folder of {date_utc}_UTC files gets one more of those."""
    folder = env["media"] / "erin"
    folder.mkdir()
    for i in range(3):
        (folder / f"2024-0{i + 1}-01_12-00-00_UTC.jpg").write_bytes(b"x")
    fake.set(profile(handle="erin", uid=31))
    job = save_now(client)
    assert job["result"]["folder"] == str(folder)
    assert (folder / "2024-06-11_12-00-00_UTC.jpg").is_file()


def test_never_overwrites(env, client, fake):
    folder = env["media"] / "_saved"
    folder.mkdir()
    taken = folder / f"stranger-2024-06-11-{CODE}.jpg"
    taken.write_bytes(b"mine")
    fake.set(profile(handle="stranger", uid=4242))
    save_now(client)
    assert taken.read_bytes() == b"mine"
    assert (folder / f"stranger-2024-06-11-{CODE}.json").is_file()


@pytest.mark.parametrize("fail, error", [("login", "login_required"), ("429", "rate_limited"),
                                         ("private", "private")])
def test_failures(env, client, fake, fail, error):
    fake.set(profile(), fail=fail)
    job = save_now(client)
    assert (job["state"], job["result"]["error"]) == ("failed", error)
    assert job["result"]["post"] is None
    if error == "login_required":
        assert "Settings → Downloaders" in job["message"]


def test_post_not_found(env, client, fake):
    fake.set(profile())
    job = save_now(client, "CNOSUCHPOST")
    assert (job["state"], job["result"]["error"]) == ("failed", "not_found")
    assert job["message"].startswith("Post not found")


def test_missing_tool(env, client, fake, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    job = save_now(client)
    assert (job["state"], job["result"]) == ("failed", {"error": "missing"})


def test_shares_the_instaloader_lock_and_pause(env, client, fake):
    set_config(instaloader={"pause": 30})
    fake.set(profile())
    first = save_now(client)
    second = save_post(client, "CSAVEME0002")["job"]
    assert first["group"] == second["group"] == "instaloader"
    assert second["state"] == "queued" and second["waits_until"] is not None
    jobs.cancel(second["id"])


# ---------------------------------------------------------------------------
# Rate safety
# ---------------------------------------------------------------------------

def test_a_second_click_returns_the_same_job(env, client, fake):
    fake.set(profile(), delay=0.5)
    first = save_post(client)
    second = save_post(client)
    assert first["existing"] is False and second["existing"] is True
    assert second["job"]["id"] == first["job"]["id"]
    assert [j["id"] for j in jobs.active()] == [first["job"]["id"]]
    ended(first["job"]["id"])
    assert len(fake.runs()) == 1
    assert save_post(client)["have"] is True


def test_queue_cap(env, client, fake):
    set_config(instaloader={"pause": 3600}, save_queue_max=3)
    fake.set(profile())
    queued = [save_post(client, f"CSAVECAP0{i}")["job"] for i in range(3)]
    r = save_post(client, "CSAVECAP09", status=429)
    assert r["ok"] is False and "3 posts are already waiting" in r["error"]
    # A shortcode already queued still answers with its job.
    assert save_post(client, "CSAVECAP01")["job"]["id"] == queued[1]["id"]
    jobs.cancel(queued[2]["id"])
    assert save_post(client, "CSAVECAP09")["existing"] is False
    for j in jobs.active():
        if j["state"] == "queued":
            jobs.cancel(j["id"])


def test_queue_cap_default_and_bad_values(env):
    assert save.queue_max({}) == 20
    for bad in (0, -1, 501, "5", True, 2.5, None):
        assert save.queue_max({"save_queue_max": bad}) == 20
    assert save.queue_max({"save_queue_max": 1}) == 1
