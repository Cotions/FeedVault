"""Account health (health.py): the record kept on a source, its scrubbed
error line, the states read from each tool's output, renames, login state."""
import json
import os

import pytest

import config
import db
import health
import jobs
import userdata

from test_sync import (H, TS, add_source, carol_archive, carol_profile, fake, get, post, sync_now)  # noqa: F401


# ---------------------------------------------------------------------------
# The record: last good sync, last result, last error line, failures
# ---------------------------------------------------------------------------

def test_scrub():
    s = health.scrub
    assert s("Loaded session from /home/me/.config/instaloader/session-me.") == "Loaded session from <private path>"
    assert s("x: 400 - Cookie: sessionid=abc; csrftoken=def") == "x: 400 - Cookie: …"
    assert s("GET /api?access_token=abc123&x=1 sessionid=\"q w\" ct0=zz") == \
        "GET /api?access_token=…&x=1 sessionid=… ct0=…"
    assert "AAAA" not in s("Authorization: Bearer AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert s("auth: Bearer AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") == "auth: Bearer …"
    assert s("could not find firefox cookies database in /home/me/.mozilla/firefox/abc.default") == \
        "could not find firefox cookies database in <private path>"
    assert s("cookies from C:\\Users\\me\\AppData\\Local\\Google\\Chrome\\User Data") .startswith(
        "cookies from <private path>")
    assert s("token " + "f" * 64) == "token …"
    # Escape codes, controls and bidi overrides go; spaces collapse; the line is cut.
    assert s("\x1b[0;31mERROR:\x1b[0m  a\tb\u202ec\r\nd") == "ERROR: a b c d"
    assert len(s("x" * 20 + " " + "word " * 200)) == health.LINE_MAX
    # What is not a secret stays.
    assert s("Profile carol.cooks does not exist.") == "Profile carol.cooks does not exist."
    assert s("[feedvault] indexing /mnt/archive/carol") == "[feedvault] indexing /mnt/archive/carol"
    assert s(None) is None and s("   ") is None and s(5) is None


def test_health_follows_the_syncs(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile())
    s = add_source(client)
    h = s["health"]
    assert (h["state"], h["result"], h["ok_at"], h["last_sync_at"], h["line"], h["failures"]) == \
        (None, None, None, None, None, 0)
    good = sync_now(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (h["state"], h["result"], h["ok_at"], h["line"], h["failures"]) == ("ok", "done", good["ended_at"], None, 0)
    for n in (1, 2):
        fake.set(carol_profile(), fail="429")
        bad = sync_now(client, s["id"])
        h = get(client, f"/api/sources/{s['id']}")["health"]
        assert (h["state"], h["result"], h["ok_at"], h["last_sync_at"], h["failures"]) == \
            ("rate_limited", "failed", good["ended_at"], bad["ended_at"], n)
        assert h["line"] == "carol.cooks: Please wait a few minutes before you try again."
    fake.set(carol_profile(), fail="crash")
    sync_now(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (h["state"], h["failures"], h["ok_at"]) == ("error", 3, good["ended_at"])
    assert h["line"] == "RuntimeError: fake crash"


def test_error_lines_are_scrubbed_before_they_are_stored(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile(), fail="leak")
    s = add_source(client)
    job = sync_now(client, s["id"])
    assert job["state"] == "failed"
    userdata.flush()
    stored = [
        json.dumps(get(client, f"/api/sources/{s['id']}")),
        json.dumps(get(client, "/api/sources")),
        json.dumps({k: job[k] for k in ("result", "message")}),
        json.dumps(get(client, f"/api/jobs/{job['id']}")["result"]),
        db.connect().execute("SELECT last_result FROM sources").fetchone()[0],
        open(userdata.path(config.load()["data_directory"], "sources"), encoding="utf-8").read(),
    ]
    for text in stored:
        assert "FAKE-SECRET" not in text and "FAKESECRET" not in text and "session-carol" not in text, text
    line = get(client, f"/api/sources/{s['id']}")["health"]["line"]
    assert line.startswith("carol.cooks: JSON Query to api/v1/users: 400 Bad Request - cookie sessionid=…")


def test_hand_edited_results_are_no_health(env, client, fake):
    s = add_source(client, "nobody.here")
    conn = db.connect()
    for stored in (None, "not json", "[]", '"done"', '{"ok_at": "yesterday", "health": "exploded", "line": 5}',
                   '{"state": "done"}', '{"state": "failed", "error": "rate_limited", "failures": true}'):
        with conn:
            conn.execute("UPDATE sources SET last_result = ?, last_sync_at = ? WHERE id = ?", (stored, TS, s["id"]))
        h = get(client, f"/api/sources/{s['id']}")["health"]
        assert h["ok_at"] in (None, TS) and h["failures"] == 0 and h["line"] is None, stored
        assert h["state"] == {'{"state": "done"}': "ok",
                              '{"state": "failed", "error": "rate_limited", "failures": true}': "rate_limited"
                              }.get(stored), stored
        assert h["ok_at"] == (TS if stored == '{"state": "done"}' else None), stored
        assert get(client, "/api/sources")["sources"][0]["id"] == s["id"]


def test_a_cancelled_sync_keeps_the_last_state(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile(), fail="private")
    s = add_source(client)
    sync_now(client, s["id"])
    fake.set(carol_profile(), delay=5)
    job = post(client, f"/api/sources/{s['id']}/sync")["job"]
    from test_sync import ended, wait_for
    wait_for(lambda: jobs.get(job["id"])["state"] == "running")
    post(client, f"/api/jobs/{job['id']}/cancel")
    ended(job["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (h["state"], h["result"], h["failures"]) == ("private", "cancelled", 1)
    assert os.path.isdir(env["media"] / "carol.cooks")


# ---------------------------------------------------------------------------
# Detected states: each tool's table, and through the fake tools
# ---------------------------------------------------------------------------

# Lines as each tool prints them (health.py says where each was read).
RECORDED = {
    "instaloader": [
        ("JSON Query to graphql/query: 429 Too Many Requests [retrying; skip with ^C]", "rate_limited"),
        ("carol: Please wait a few minutes before you try again.", "rate_limited"),
        ("carol: Private but not followed.", "private"),
        ("Download profile carol: Profile carol: private but not followed.", "private"),
        ("carol: Login required.", "login_required"),
        ("carol: profile carol requires login", "login_required"),
        ("Redirected to login page. Use --login or --load-cookies.", "login_required"),
        ("Download aborted: Redirected to login page. You've been logged out, please wait some time, recreate "
         "the session and try again.", "login_required"),
        ("Session file does not exist yet - Logging in.", "login_required"),
        ('Login error: "fail" status, message "checkpoint_required".', "login_required"),
        ("No cookies found for Instagram in firefox, Are you logged in successfully in firefox?", "login_required"),
        ("Not logged in. Are you logged in successfully in firefox?", "login_required"),
        ("JSON Query to graphql/query: 403 Forbidden when accessing https://www.instagram.com/graphql/query "
         "[retrying; skip with ^C]", "login_required"),
        ("Profile carol does not exist.", "not_found"),
        ("carol: Profile carol does not exist.", "not_found"),
        ("carol: carol blocked you.", "error"),
        ("Fatal error: something new", "error"),
    ],
    "gallery-dl": [
        ("[twitter][error] HttpError: '429 Too Many Requests' for 'https://x.com/i/api/graphql'", "rate_limited"),
        ("[twitter][error] AuthorizationError: someone's Tweets are protected", "private"),
        ("[twitter][error] AuthRequired: 'auth_token' cookie needed", "login_required"),
        ("[instagram][error] AuthenticationError: Invalid or missing login credentials", "login_required"),
        ("[pixiv][error] AuthorizationError: Insufficient privileges to access the specified resource",
         "login_required"),
        ("[twitter][error] NotFoundError: Requested user could not be found", "not_found"),
        ("[gallery-dl][error] Unsupported URL 'https://example.com/a'", "error"),
        ("[twitter][error] HttpError: '500 Internal Server Error' for 'https://x.com/i/api'", "error"),
    ],
    "yt-dlp": [
        ("ERROR: [youtube:tab] @x: Unable to download webpage: HTTP Error 429: Too Many Requests "
         "(caused by <HTTPError 429: Too Many Requests>)", "rate_limited"),
        ("ERROR: [tiktok:user] x: This user's account is private. Log into an account that has access. Use "
         "--cookies-from-browser or --cookies for the authentication.", "private"),
        ("ERROR: [tiktok:user] x: This user's account is likely either private or all of their videos are "
         "private. Log into an account that has access", "private"),
        ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "private"),
        ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser", "login_required"),
        ("ERROR: [youtube] abc: Sign in to confirm you're not a bot.", "login_required"),
        ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate", "login_required"),
        ("ERROR: [tiktok:user] x: TikTok is requiring login for access to this content. Use "
         "--cookies-from-browser or --cookies for the authentication.", "login_required"),
        ("ERROR: [youtube:tab] @x: Unable to download webpage: HTTP Error 404: Not Found "
         "(caused by <HTTPError 404: Not Found>)", "not_found"),
        ("ERROR: [youtube:tab] @x: The channel/playlist does not exist and the URL redirected to youtube.com "
         "home page", "not_found"),
        ("ERROR: [youtube:tab] @x: YouTube said: This account has been terminated for a violation of YouTube's "
         "Terms of Service.", "not_found"),
        ("ERROR: [youtube] abc: Video unavailable", "not_found"),
        ("ERROR: [tiktok:user] x: Unable to extract secondary user ID. If you are able to get the channel_id",
         "error"),
        ("ERROR: [tiktok:user] x: This account does not have any videos posted", "error"),
    ],
}


def test_each_table_on_recorded_lines():
    for tool, rows in RECORDED.items():
        for line, state in rows:
            assert health.classify([(1, "[1/1] Downloading profile x"), (2, line)], health.TABLES[tool]) == \
                (state, line), (tool, line)
    assert health.classify([], health.INSTALOADER) == ("error", None)
    # instaloader's closing heading is never the line, nor FeedVault's own notes.
    assert health.classify([(1, "odd"), (2, "Errors or warnings occurred:"), (3, "[feedvault] indexing /m")],
                           health.INSTALOADER) == ("error", "odd")
    # Progress counters are not a rate limit.
    assert health.classify([(1, "[429/1200] carol-2024-06-01-C.jpg json")], health.INSTALOADER)[0] == "error"


@pytest.mark.parametrize("fail,state", [("429", "rate_limited"), ("login", "login_required"), ("private", "private"),
                                        ("notfound", "not_found"), ("crash", "error")])
def test_instaloader_states_through_the_fake(env, client, fake, fail, state):
    carol_archive(env)
    profiles = carol_profile()
    profiles["carol.cooks"]["fail"] = fail             # this profile only
    fake.set(profiles)
    s = add_source(client)
    sync_now(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (h["state"], h["result"], h["failures"]) == (state, "failed", 1)
    assert h["line"] and "Errors or warnings occurred" not in h["line"]


# ---------------------------------------------------------------------------
# Renamed (#11): a suggestion the user accepts or dismisses
# ---------------------------------------------------------------------------

def test_renamed_lines():
    lines = [(1, "Trying to find profile carol.cooks using its unique ID 777."),
             (2, "Profile carol.cooks has changed its name to Carol.Bakes."), (3, "[1/1] Downloading profile x"),
             (4, ""), (5, "Errors or warnings occurred:"), (6, "Profile carol.cooks has changed its name to Carol.Bakes."),
             (7, "[feedvault] indexing /media/carol.cooks")]
    assert health.renamed("instaloader", lines) == ("carol.cooks", "carol.bakes")
    assert health.only_renamed("instaloader", lines)
    assert not health.only_renamed("instaloader", lines + [(7, "carol.bakes: Login required.")])
    assert not health.only_renamed("instaloader", lines[:4])
    assert not health.only_renamed("yt-dlp", lines) and health.renamed("yt-dlp", lines) is None
    same = [(1, "Errors or warnings occurred:"),
            (2, "Warning: Profile carol could not be retrieved by its name, but by its ID.")]
    assert health.renamed("instaloader", same) is None and health.only_renamed("instaloader", same)
    # Only a handle: anything else in the line is not a name to suggest.
    assert health.renamed("instaloader", [(1, "Profile a has changed its name to ../b.")]) is None
    assert health.renamed("instaloader", [(1, "x: Profile a has changed its name to b.")]) is None


def files(folder):
    return sorted((os.path.relpath(os.path.join(d, f), folder), os.path.getmtime(os.path.join(d, f)))
                  for d, _, fs in os.walk(folder) for f in fs)


def test_a_renamed_profile_is_a_suggestion_the_user_accepts(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile())
    s = add_source(client)
    sync_now(client, s["id"])
    bakes = {"carol.bakes": carol_profile(new=3)["carol.cooks"]}  # the same id, 777
    fake.set(bakes)
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and "now called carol.bakes" in job["message"]
    src = get(client, f"/api/sources/{s['id']}")
    h = src["health"]
    assert (h["state"], h["failures"], h["line"]) == ("renamed", 0, None)
    assert h["rename"] == {"from": "carol.cooks", "to": "carol.bakes", "at": job["ended_at"]}
    assert src["target"] == "carol.cooks"                    # never renamed on its own
    # Not accepted yet: the first sync seeds the old name's stamps with the
    # account's id again (sync._seed_posts), so instaloader finds it by id.
    job = sync_now(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (job["state"], h["state"], h["rename"]["to"]) == ("done", "renamed", "carol.bakes")
    before = files(src["folder"])
    assert post(client, f"/api/sources/{s['id']}/rename", {"to": "someone.else"}, 400)
    r = post(client, f"/api/sources/{s['id']}/rename", {"to": "carol.bakes"})["source"]
    assert (r["target"], r["folder"], r["health"]["rename"]) == ("carol.bakes", src["folder"], None)
    assert files(src["folder"]) == before                  # nothing moved, renamed or touched
    assert post(client, f"/api/sources/{s['id']}/rename", {"to": "carol.bakes"}, 400)  # nothing left to accept
    userdata.flush()
    saved = json.load(open(userdata.path(config.load()["data_directory"], "sources"), encoding="utf-8"))
    assert [x["target"] for x in saved["rows"]] == ["carol.bakes"]
    job = sync_now(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (job["state"], h["state"]) == ("done", "ok")
    assert fake.runs()[-1]["argv"][-1] == "carol.bakes"


def test_a_rename_suggestion_dismissed_or_refused(env, client, fake):
    carol_archive(env)
    fake.set(carol_profile())
    s = add_source(client)
    sync_now(client, s["id"])
    fake.set({"carol.bakes": carol_profile()["carol.cooks"]})
    sync_now(client, s["id"])
    other = add_source(client, "carol.bakes", folder=str(env["media"] / "elsewhere"))
    assert post(client, f"/api/sources/{s['id']}/rename", {"to": "carol.bakes"}, 409)
    assert get(client, f"/api/sources/{s['id']}")["target"] == "carol.cooks"
    client.delete(f"/api/sources/{other['id']}", headers=H)
    fake.set({"carol.bakes": carol_profile()["carol.cooks"]}, delay=5)
    job = post(client, f"/api/sources/{s['id']}/sync")["job"]
    from test_sync import ended, wait_for
    wait_for(lambda: jobs.get(job["id"])["state"] == "running")
    assert post(client, f"/api/sources/{s['id']}/rename", {"to": "carol.bakes"}, 409)
    post(client, f"/api/jobs/{job['id']}/cancel")
    ended(job["id"])
    r = client.delete(f"/api/sources/{s['id']}/rename", headers=H).get_json()["source"]
    assert (r["target"], r["health"]["rename"]) == ("carol.cooks", None)
    assert post(client, f"/api/sources/{s['id']}/rename", {"to": "carol.bakes"}, 400)
    assert client.delete("/api/sources/999/rename", headers=H).status_code == 404


def test_a_hand_edited_rename_is_checked(env, client, fake):
    s = add_source(client, "nobody.here")
    conn = db.connect()
    for rename in ({"from": "nobody.here", "to": "../../etc"}, {"from": "nobody.here", "to": "A B"},
                   {"from": "nobody.here"}, "x", {"from": "someone", "to": "x"}):
        with conn:
            conn.execute("UPDATE sources SET last_result = ? WHERE id = ?",
                         (json.dumps({"state": "done", "rename": rename}), s["id"]))
        h = get(client, f"/api/sources/{s['id']}")["health"]
        assert h["rename"] is None, rename
        assert post(client, f"/api/sources/{s['id']}/rename", {"to": "x"}, 400), rename
    assert get(client, f"/api/sources/{s['id']}")["target"] == "nobody.here"


# ---------------------------------------------------------------------------
# Login state: the session the last sync used, as its output tells
# ---------------------------------------------------------------------------

def test_login_lines():
    login = health.login
    cookies, saved = {"mode": "cookies", "browser": "firefox"}, {"mode": "login", "user": "me"}
    assert login("instaloader", [(1, "x")], {"mode": "none"}, "ok") == {"mode": "none", "found": None, "accepted": None}
    assert login("instaloader", [(1, "Loaded session from /home/me/.config/instaloader/session-me."),
                                 (2, "Logged in as me.")], saved, "ok") == \
        {"mode": "login", "found": True, "accepted": True}
    assert login("instaloader", [(1, "Session file does not exist yet - Logging in."),
                                 (2, "Login error: no password")], saved, "login_required") == \
        {"mode": "login", "found": False, "accepted": False}
    assert login("instaloader", [(1, "Cookies loaded successfully from firefox"),
                                 (2, "Login error: Not logged in. Are you logged in successfully in firefox?")],
                 cookies, "error") == {"mode": "cookies", "found": True, "accepted": False}
    assert login("instaloader", [(1, "Login error: No cookies found for Instagram in firefox, Are you logged in "
                                     "successfully in firefox?")], cookies, "login_required")["found"] is False
    # Found, then the site wanted a login anyway: refused.
    assert login("instaloader", [(1, "Loaded session from /x/session-me."), (2, "me: Login required.")],
                 saved, "login_required") == {"mode": "login", "found": True, "accepted": False}
    assert login("yt-dlp", [(1, "Extracting cookies from firefox"), (2, "Extracted 52 cookies from firefox")],
                 cookies, "ok") == {"mode": "cookies", "found": True, "accepted": True}
    assert login("yt-dlp", [(1, "Extracted 52 cookies from firefox")], cookies, "rate_limited")["accepted"] is None
    assert login("yt-dlp", [(1, 'ERROR: could not find firefox cookies database in "/home/me/.mozilla"')],
                 cookies, "error") == {"mode": "cookies", "found": False, "accepted": False}
    assert login("gallery-dl", [(1, "[cookies][info] Extracted 0 cookies from Firefox")], cookies, "ok")["found"] is False
    assert login("gallery-dl", [(1, "[cookies][info] Extracted 9 cookies from Firefox")], cookies, "private") == \
        {"mode": "cookies", "found": True, "accepted": True}
    # Nothing said: unknown.
    assert login("gallery-dl", [(1, "x")], cookies, "ok") == {"mode": "cookies", "found": None, "accepted": None}


def test_login_state_of_the_last_sync(env, client, fake, monkeypatch):
    carol_archive(env)
    fake.set(carol_profile())
    s = add_source(client, options={"session": {"mode": "login", "user": "my.account"}})
    assert s["session"] == {"mode": "login", "user": "my.account", "session_file": False}
    assert s["health"]["login"] is None
    sync_now(client, s["id"])                          # no session saved: instaloader asks for a password
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (h["state"], h["login"]) == ("login_required", {"mode": "login", "found": False, "accepted": False})
    monkeypatch.setenv("FAKE_INSTALOADER_SESSION", "1")
    sync_now(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (h["state"], h["login"]) == ("ok", {"mode": "login", "found": True, "accepted": True})
    profiles = carol_profile()
    profiles["carol.cooks"]["fail"] = "login"
    fake.set(profiles)
    sync_now(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert h["login"] == {"mode": "login", "found": True, "accepted": False}
    assert "Loaded session" not in json.dumps(get(client, f"/api/sources/{s['id']}"))   # no path kept
    post(client, f"/api/sources/{s['id']}", {"options": {"session": {"mode": "none"}}})
    fake.set(carol_profile())
    sync_now(client, s["id"])
    assert get(client, f"/api/sources/{s['id']}")["health"]["login"] == {"mode": "none", "found": None,
                                                                         "accepted": None}
