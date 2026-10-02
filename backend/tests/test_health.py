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

from test_sync import (TS, add_source, carol_archive, carol_profile, fake, get, post, sync_now)  # noqa: F401


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
