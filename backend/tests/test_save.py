"""POST /api/save: one Instagram post by shortcode, with the fake instaloader."""
import os

import pytest

from conftest import H
from fakes import owner, write_post
from test_sync import DAY, TS, add_source, carol_archive, ended, fake, get, post, set_config, sync_now  # noqa: F401

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


# ---------------------------------------------------------------------------
# Out of _saved once the owner has a source (#38)
# ---------------------------------------------------------------------------

def stranger(saved=CODE, older=2, newer=1):
    """@stranger's profile: the saved post, ``older`` posts before it and ``newer`` after."""
    posts = [{"shortcode": saved, "ts": TS + 10 * DAY, "caption": "saved from the browser", "kind": "carousel",
              "slides": 2}]
    posts += [{"shortcode": f"COLDER0000{i}", "ts": TS + i * DAY, "caption": f"older {i}"} for i in range(older)]
    posts += [{"shortcode": f"CNEWER0000{i}", "ts": TS + (20 + i) * DAY, "caption": f"newer {i}"}
              for i in range(newer)]
    return {"stranger": {"id": 4242, "name": "Stranger", "posts": posts}}


def saved_with_user_data(client, fake):
    """CODE saved to _saved, tagged, kept in Review and in a collection."""
    fake.set(stranger())
    assert save_now(client)["result"]["folder"].endswith("_saved")
    post(client, "/api/tags/apply", {"posts": [f"instagram:{CODE}"], "add": ["keeper"], "remove": []})
    db.set_decision(db.connect(), [f"instagram:{CODE}"], "keep", 7)
    cid = post(client, "/api/collections", {"name": "Saved ones"})["collection"]["id"]
    post(client, f"/api/collections/{cid}/add", {"posts": [f"instagram:{CODE}"]})
    return db.connect().execute("SELECT first_seen FROM posts WHERE id = ?", (f"instagram:{CODE}",)).fetchone()[0]


def test_saved_post_moves_into_the_new_source_before_its_sync(env, client, fake):
    first_seen = saved_with_user_data(client, fake)
    saved = env["media"] / "_saved"
    s = add_source(client, target="stranger")
    job = ended(post(client, f"/api/sources/{s['id']}/sync")["job"]["id"])
    assert job["state"] == "done", job
    folder = env["media"] / "stranger"
    # Moved whole, named as the sync names its files (the target), nothing left behind.
    for suffix in ("_1.jpg", "_2.jpg", ".json", ".txt"):
        assert (folder / f"stranger-2024-06-11-{CODE}{suffix}").is_file()
    assert not [n for n in os.listdir(saved) if CODE in n]
    log = [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert any("1 saved post moved from _saved into" in t for t in log)
    # The sync walked the whole profile (a stamp before every post), skipped
    # the saved post's files and downloaded the others only.
    assert "--fast-update" not in job["argv"]
    assert any(f"{CODE}.jpg exists" in t for t in log)
    assert job["result"]["added"] == 3
    conn = db.connect()
    p = db.get_post(conn, "instagram", CODE)
    meta = conn.execute("SELECT meta_path FROM posts WHERE id = ?", (f"instagram:{CODE}",)).fetchone()[0]
    assert meta == str(folder / f"stranger-2024-06-11-{CODE}.json")
    paths = [r[0] for r in conn.execute("SELECT path FROM media WHERE post_id = ?", (f"instagram:{CODE}",))]
    assert p["media_count"] == 2 and all(path.startswith(str(folder)) for path in paths)
    # Same post id: first_seen, tags, Review decision and collections stay with it.
    row = conn.execute("SELECT first_seen FROM posts WHERE id = ?", (f"instagram:{CODE}",)).fetchone()
    assert row[0] == first_seen
    assert p["tags"] == ["keeper"] and p["decision"] == "keep"
    assert [c["name"] for c in p["collections"]] == ["Saved ones"]
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE meta_path LIKE ?", (f"{saved}%",)).fetchone()[0] == 0
    # The source has its account now, and the next sync finds nothing new.
    assert get(client, f"/api/sources/{s['id']}")["account"] == {"platform": "instagram", "id": "4242"}
    assert sync_now(client, s["id"])["result"]["added"] == 0


def test_saved_post_moves_when_the_source_has_older_posts(env, client, fake):
    """An archive folder already there: the stamp is its newest synced post,
    not the saved one, so the posts between them are still fetched."""
    fake.set(stranger(newer=0))
    save_now(client)                           # no folder yet: into _saved
    folder = env["media"] / "stranger"
    write_post(folder, "COLDER00001", TS + DAY, owner("stranger", "4242"))
    scanner.scan(env["roots"])
    profile = stranger(newer=0)
    profile["stranger"]["posts"].append({"shortcode": "CBETWEEN001", "ts": TS + 5 * DAY, "caption": "between"})
    fake.set(profile)
    s = add_source(client, target="stranger")
    job = sync_now(client, s["id"])
    log = [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert any("starting after its newest indexed post, 2024-06-02" in t for t in log)
    # The folder's layout ({date_utc}_UTC), and the post in between downloaded.
    assert (folder / "2024-06-11_12-00-00_UTC.json").is_file()
    assert (folder / "2024-06-06_12-00-00_UTC.json").is_file()
    assert job["result"]["added"] == 1
    assert db.saved_ids(db.connect(), ["instagram:CBETWEEN001"]) == ["instagram:CBETWEEN001"]


def test_saved_post_is_never_moved_over_a_file(env, client, fake):
    fake.set(stranger())
    save_now(client)
    folder = env["media"] / "stranger"
    folder.mkdir()
    taken = folder / f"stranger-2024-06-11-{CODE}_1.jpg"
    taken.write_bytes(b"mine")
    s = add_source(client, target="stranger")
    job = sync_now(client, s["id"])
    assert taken.read_bytes() == b"mine"
    # Left whole where it was: not split between the folders.
    assert len([n for n in os.listdir(env["media"] / "_saved") if CODE in n]) == 4
    assert not (folder / f"stranger-2024-06-11-{CODE}.json").exists()
    assert any("1 saved post left in _saved" in ln["text"] for ln in jobs.log(job["id"])["lines"])
    # Saved one by one, it sets no starting point: the sync walks the whole
    # profile, the posts older than it included.
    assert "--fast-update" not in job["argv"]
    assert any("saved one by one gives no starting point" in ln["text"] for ln in jobs.log(job["id"])["lines"])
    assert (folder / "stranger-2024-06-01-COLDER00000.json").is_file()


def test_saved_post_move_rolls_back_on_failure(env, client, fake, monkeypatch):
    fake.set(stranger())
    save_now(client)
    real = save.shutil.move
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 3:
            raise PermissionError(13, "Permission denied")
        return real(src, dst)
    monkeypatch.setattr(save.shutil, "move", flaky)
    conn = db.connect()
    src = {"tool": "instaloader", "platform": "instagram", "author_id": "4242", "target": "stranger",
           "folder": str(env["media"] / "stranger")}
    notes = []
    assert save.gather(conn, src, env["roots"], notes.append) == []
    assert len([n for n in os.listdir(env["media"] / "_saved") if CODE in n]) == 4
    assert os.listdir(env["media"] / "stranger") == []
    assert any("Permission denied" in n for n in notes)


def test_only_the_accounts_posts_and_only_from_saved(env, client, fake):
    """Another owner's saved post stays; so does a post of the account in another folder."""
    profiles = {**stranger(), **profile(code="COTHER00001", handle="other.person", uid=5151)}
    fake.set(profiles)
    save_now(client)
    save_now(client, "COTHER00001")
    elsewhere = env["media"] / "Stranger (old)"
    write_post(elsewhere, "CELSEWHERE1", TS, owner("stranger", "4242"))
    scanner.scan(env["roots"])
    conn = db.connect()
    src = {"tool": "instaloader", "platform": "instagram", "author_id": "4242", "target": "stranger",
           "folder": str(env["media"] / "stranger")}
    assert save.gather(conn, src, env["roots"], lambda t: None) == [f"instagram:{CODE}"]
    assert any("COTHER00001" in n for n in os.listdir(env["media"] / "_saved"))
    meta = conn.execute("SELECT meta_path FROM posts WHERE id = 'instagram:CELSEWHERE1'").fetchone()[0]
    assert meta.startswith(str(elsewhere))
    # Not for other tools, nor into _saved itself.
    assert save.gather(conn, {**src, "tool": "gallery-dl"}, env["roots"], lambda t: None) == []
    assert save.gather(conn, {**src, "author_id": "5151", "folder": str(env["media"] / "_saved")},
                       env["roots"], lambda t: None) == []


def test_saved_symlink_is_moved_as_a_link(env, client, fake, tmp_path):
    """A file in _saved that is a symlink moves as the link: what it points at is never touched."""
    fake.set(stranger())
    save_now(client)
    saved = env["media"] / "_saved"
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    link = saved / f"stranger-2024-06-11-{CODE}_2.jpg"
    link.unlink()
    link.symlink_to(outside)
    scanner.scan(env["roots"])
    src = {"tool": "instaloader", "platform": "instagram", "author_id": "4242", "target": "stranger",
           "folder": str(env["media"] / "stranger")}
    assert save.gather(db.connect(), src, env["roots"], lambda t: None) == [f"instagram:{CODE}"]
    moved = env["media"] / "stranger" / f"stranger-2024-06-11-{CODE}_2.jpg"
    assert moved.is_symlink() and os.readlink(moved) == str(outside)
    assert outside.read_bytes() == b"outside"


def test_saved_post_moves_once_the_first_sync_finds_the_account(env, client, fake):
    """Saved under an older handle: the source's target names no account
    before its first sync, which then finds it (adopt); the saved post
    (gone from the profile since) moves into the folder after it."""
    fake.set({"oldname": {"id": 4242, "posts": [{"shortcode": CODE, "ts": TS + 10 * DAY}]}})
    save_now(client)
    fake.set({"newname": {"id": 4242, "posts": [{"shortcode": "CNEWNAME001", "ts": TS + 20 * DAY}]}})
    s = add_source(client, target="newname")
    job = sync_now(client, s["id"])
    assert job["result"]["added"] == 1
    folder = env["media"] / "newname"
    assert (folder / f"newname-2024-06-11-{CODE}.json").is_file()
    assert not [n for n in os.listdir(env["media"] / "_saved") if CODE in n]
    meta = db.connect().execute("SELECT meta_path FROM posts WHERE id = ?", (f"instagram:{CODE}",)).fetchone()[0]
    assert meta.startswith(str(folder))


# ---------------------------------------------------------------------------
# A saved post never seeds a sync's stamp (#41)
# ---------------------------------------------------------------------------

MARCH, SEPTEMBER = 1709294400, 1725192000        # 2024-03-01, 2024-09-01 12:00 UTC


def dora(between=3):
    """@dora's profile: a March post, ``between`` posts after it, a September one."""
    posts = [{"shortcode": "CDORAMAR001", "ts": MARCH, "caption": "march"},
             {"shortcode": "CDORASEP001", "ts": SEPTEMBER, "caption": "september"}]
    posts += [{"shortcode": f"CDORABTW00{i}", "ts": MARCH + (i + 1) * 30 * DAY, "caption": f"between {i}"}
              for i in range(between)]
    return {"dora": {"id": 999, "name": "Dora", "posts": posts}}


def _save_september_into(env, client, fake, folder):
    fake.set(dora())
    job = save_now(client, "CDORASEP001")
    assert job["state"] == "done" and job["result"]["folder"] == str(folder)
    conn = db.connect()
    assert [r[0] for r in conn.execute("SELECT post_id FROM saved_posts")] == ["instagram:CDORASEP001"]


def _first_sync_fetches_between(env, client, fake, sid, folder):
    job = sync_now(client, sid)
    assert job["state"] == "done", job
    log = [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert any("starting after its newest indexed post, 2024-03-01" in t for t in log), log
    assert job["result"]["added"] == 3
    have = db.saved_ids(db.connect(), [f"instagram:CDORABTW00{i}" for i in range(3)])
    assert len(have) == 3
    # The walk passed the saved post: its entry is no longer needed.
    assert db.connect().execute("SELECT COUNT(*) FROM saved_posts").fetchone()[0] == 0


def test_saved_post_in_an_existing_folder_does_not_seed_the_first_sync(env, client, fake):
    """An old archive folder (no source) with a March post; a September post
    saved into it. The first sync starts after March, not September."""
    folder = env["media"] / "dora"
    write_post(folder, "CDORAMAR001", MARCH, owner("dora", "999"))
    scanner.scan(env["roots"])
    _save_september_into(env, client, fake, folder)
    s = add_source(client, target="dora")
    assert s["folder"] == str(folder)
    _first_sync_fetches_between(env, client, fake, s["id"], folder)


def test_saved_post_in_a_source_never_synced_does_not_seed_it(env, client, fake):
    folder = env["media"] / "dora"
    write_post(folder, "CDORAMAR001", MARCH, owner("dora", "999"))
    scanner.scan(env["roots"])
    s = add_source(client, target="dora")
    _save_september_into(env, client, fake, folder)
    _first_sync_fetches_between(env, client, fake, s["id"], folder)


def test_saved_posts_alone_walk_the_whole_profile(env, client, fake):
    """A handle folder with nothing but a saved post: the stamp goes before every post."""
    folder = env["media"] / "dora"
    folder.mkdir()
    _save_september_into(env, client, fake, folder)
    s = add_source(client, target="dora")
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and "--fast-update" not in job["argv"]
    log = [ln["text"] for ln in jobs.log(job["id"])["lines"]]
    assert any("downloading everything (the 1 post saved one by one gives no starting point)" in t for t in log)
    assert job["result"]["added"] == 4


def test_a_post_a_sync_got_first_is_not_noted_as_saved(env, client, fake, monkeypatch):
    """Save placed nothing new (the sync had it): not a saved post."""
    folder = env["media"] / "dora"
    write_post(folder, "CDORAMAR001", MARCH, owner("dora", "999"))
    scanner.scan(env["roots"])
    monkeypatch.setattr(save, "have", lambda conn, code: None)      # as if it arrived while the save ran
    fake.set(dora())
    assert save_now(client, "CDORAMAR001")["result"]["post"] == "instagram:CDORAMAR001"
    assert db.connect().execute("SELECT COUNT(*) FROM saved_posts").fetchone()[0] == 0


def test_saved_posts_are_user_data(env, client, fake):
    import userdata
    (env["media"] / "dora").mkdir()
    fake.set(dora())
    save_now(client, "CDORASEP001")
    conn = db.connect()
    assert userdata.export(conn, "saved_posts", config.load()["data_directory"]) == 1
    with conn:
        conn.execute("DELETE FROM saved_posts")
    assert userdata.load(conn, "saved_posts", config.load()["data_directory"]) == 1
    assert [r[0] for r in conn.execute("SELECT post_id FROM saved_posts")] == ["instagram:CDORASEP001"]
