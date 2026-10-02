"""Account health of gallery-dl and yt-dlp sources, through the fake tools."""
import pytest

from test_sync_tools import TT, X, YT, add, fake, get, run_sync, tt_account, x_account, yt_account  # noqa: F401

# yt-dlp's TikTok user that does not exist only says it found no user id, as
# a private profile with embedding off does: an error, not a guess.
EXPECTED = {
    X: {"429": "rate_limited", "login": "login_required", "private": "private", "notfound": "not_found"},
    TT: {"429": "rate_limited", "login": "login_required", "private": "private", "notfound": "error"},
    YT: {"429": "rate_limited", "login": "login_required", "private": "private", "notfound": "not_found"},
}
ACCOUNTS = {X: lambda: x_account((1, 1)), TT: lambda: tt_account(1), YT: lambda: yt_account((1, 30))}


@pytest.mark.parametrize("url", [X, TT, YT])
@pytest.mark.parametrize("fail", ["429", "login", "private", "notfound"])
def test_states_through_the_fakes(env, fake, client, url, fail):
    s = add(client, url)
    fake.put(url, {**ACCOUNTS[url](), "fail": fail})         # this account only
    job = run_sync(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (job["state"], h["state"], h["failures"]) == ("failed", EXPECTED[url][fail], 1), job["result"]
    assert h["line"] and h["line"].startswith(("ERROR: [", "[twitter][error] "))
    fake.put(url, ACCOUNTS[url]())
    run_sync(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert (h["state"], h["failures"], h["line"]) == ("ok", 0, None)


@pytest.mark.parametrize("url", [X, YT])
def test_login_state(env, fake, client, url):
    import config
    import sync
    s = add(client, url)
    cfg = config.load()
    tool = "gallery-dl" if url == X else "yt-dlp"
    cfg[tool] = {**sync.tool_settings(tool, cfg), "session": {"mode": "cookies", "browser": "firefox"}}
    config.save(cfg)
    fake.put(url, ACCOUNTS[url]())
    run_sync(client, s["id"])
    h = get(client, f"/api/sources/{s['id']}")["health"]
    assert h["login"] == {"mode": "cookies", "found": True, "accepted": True}
    fake.put(url, {**ACCOUNTS[url](), "fail": "login"})
    run_sync(client, s["id"])
    assert get(client, f"/api/sources/{s['id']}")["health"]["login"]["accepted"] is False
    if tool == "yt-dlp":
        fake.put(url, {**ACCOUNTS[url](), "fail": "cookies"})
        run_sync(client, s["id"])
        assert get(client, f"/api/sources/{s['id']}")["health"]["login"] == \
            {"mode": "cookies", "found": False, "accepted": False}
