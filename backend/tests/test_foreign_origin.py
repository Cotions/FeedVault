"""Another site reaches only what the userscript calls (#112).

A request whose Origin (or Sec-Fetch-Site) says another site sent it, as
the userscript's from instagram.com do, may reach the few /api routes in
app.FOREIGN_ALLOWED; every other /api route answers 403 before its view
runs. The routes come from app.url_map, so a route added later is refused
to another site until it is added to the allowlist.
"""
import os
import re

import pytest

from conftest import H
from fakes import gallery_dl_case

import app as app_module
import jobs
import scanner
import trash

VAR = re.compile(r"<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>")
USERSCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "userscript", "feedvault.user.js")

# What the userscript's requests carry, and what other sites' may.
FOREIGN = {
    "instagram": {**H, "Origin": "https://www.instagram.com"},
    "x": {**H, "Origin": "https://x.com", "Sec-Fetch-Site": "cross-site"},
    "cross-site": {**H, "Sec-Fetch-Site": "cross-site"},
    "same-site": {**H, "Sec-Fetch-Site": "same-site"},
    "null": {**H, "Origin": "null"},
    "https-localhost": {**H, "Origin": "https://localhost:3380"},
}
OURS = {
    "no-origin": H,
    "dashboard": {**H, "Origin": "http://localhost:3380", "Sec-Fetch-Site": "same-origin"},
    "dev-server": {**H, "Origin": "http://localhost:5173"},
    "typed": {**H, "Sec-Fetch-Site": "none"},
}

# The userscript's calls, as the issue lists them.
EXPECTED = {
    ("POST", "/api/saved"), ("POST", "/api/save"), ("GET", "/api/jobs/<int:job_id>"),
    ("GET", "/api/sources/resolve"), ("POST", "/api/sources"), ("GET", "/api/sources/<int:sid>"),
    ("POST", "/api/sources/<int:sid>/sync"),
}


def _url(rule):
    return VAR.sub(lambda m: "1" if m.group(1) == "int" else "x", rule.rule)


def _api_routes():
    """Every /api rule with every method it answers (HEAD and OPTIONS too)."""
    return [(m, rule) for rule in app_module.app.url_map.iter_rules() if rule.rule.startswith("/api/")
            for m in sorted(rule.methods)]


API_ROUTES = _api_routes()


def _allowed(method, rule):
    return (rule.endpoint, method) in app_module.FOREIGN_ALLOWED


@pytest.fixture
def spies(client, monkeypatch):
    """Every view replaced by one that records it ran: nothing real runs."""
    ran = []
    for endpoint in list(app_module.app.view_functions):
        def spy(*a, _endpoint=endpoint, **kw):
            ran.append(_endpoint)
            return "spied", 200
        monkeypatch.setitem(app_module.app.view_functions, endpoint, spy)
    return ran


def test_the_allowlist_is_the_issues_seven_routes():
    rules = {r.endpoint: r.rule for r in app_module.app.url_map.iter_rules()}
    assert {(m, rules[e]) for e, m in app_module.FOREIGN_ALLOWED} == EXPECTED


def test_the_walk_covers_the_routes_named_in_the_issue():
    walked = {(m, r.rule) for m, r in API_ROUTES}
    for route in (("POST", "/api/quit"), ("POST", "/api/trash/empty"), ("POST", "/api/trash/purge"),
                  ("POST", "/api/delete"), ("POST", "/api/yt-dlp/info-json-cookies"), ("POST", "/api/people/merge"),
                  ("POST", "/api/jobs"), ("DELETE", "/api/sources/<int:sid>"), ("POST", "/api/config"),
                  ("GET", "/api/scripts"), ("POST", "/api/scripts/<sid>/run")):
        assert route in walked, route
    assert len(walked) > 100


@pytest.mark.parametrize("origin", FOREIGN)
def test_another_site_reaches_only_the_allowlist(client, spies, origin):
    """Every rule × method, in one test per origin (the walk is ~250 requests)."""
    wrong, reached = [], set()
    for method, rule in API_ROUTES:
        spies.clear()
        r = client.open(_url(rule), method=method, json={}, headers=FOREIGN[origin])
        if _allowed(method, rule):
            if r.status_code != 200 or spies != [rule.endpoint]:
                wrong.append(f"{method} {rule.rule}: allowlisted, got {r.status_code}, ran {spies}")
            reached.add((method, rule.rule))
        elif r.status_code != 403 or spies != [] \
                or (method != "HEAD" and r.get_json() != {"ok": False, "error": app_module.FOREIGN_REFUSED}):
            wrong.append(f"{method} {rule.rule}: {r.status_code} {r.get_data(as_text=True)[:80]!r}, ran {spies}")
    assert not wrong, "\n".join(wrong)
    assert reached == EXPECTED
    assert "own dashboard" in app_module.FOREIGN_REFUSED


@pytest.mark.parametrize("origin", OURS)
def test_the_dashboard_still_reaches_every_route(client, spies, origin):
    wrong = []
    for method, rule in API_ROUTES:
        if method == "OPTIONS":                # Flask's own answer: no view runs
            continue
        spies.clear()
        r = client.open(_url(rule), method=method, json={}, headers=OURS[origin])
        if r.status_code != 200 or spies != [rule.endpoint]:
            wrong.append(f"{method} {rule.rule}: {r.status_code}, ran {spies}")
    assert not wrong, "\n".join(wrong)


def test_the_header_is_still_required_first(client, spies):
    # Without X-FeedVault, an allowlisted route is refused as before.
    r = client.post("/api/saved", json={"ids": []}, headers={"Origin": "https://www.instagram.com"})
    assert r.status_code == 403 and r.get_json()["error"] == "missing X-FeedVault header"
    assert spies == []


@pytest.mark.parametrize("method, url", [("GET", "/api/nope"), ("POST", "/api/jobs/1"), ("PUT", "/api/saved"),
                                         ("GET", "/api/jobs/" + "9" * 25)])
def test_an_unknown_path_or_method_is_refused_too(client, spies, method, url):
    # No endpoint matched (404, 405, an id out of range): refused, not let through.
    r = client.open(url, method=method, json={}, headers=FOREIGN["instagram"])
    assert r.status_code == 403 and r.get_json()["error"] == app_module.FOREIGN_REFUSED
    assert spies == []


def test_the_pages_and_media_rules_are_unchanged(env, client):
    # The dashboard's pages still open from a link anywhere (the userscript
    # links to them); media stays refused, as before.
    for url in ("/", "/settings", "/people/1", "/userscript/feedvault.user.js"):
        assert client.get(url, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200, url
    assert client.get("/media/1", headers={"Origin": "https://www.instagram.com"}).status_code == 403


# ---------------------------------------------------------------------------
# What the issue's repro did, with the real views: nothing happens now
# ---------------------------------------------------------------------------

def test_the_repro_changes_nothing(env, client, monkeypatch):
    gallery_dl_case("twitter/photo", env["media"])
    scanner.scan(env["roots"])
    m = client.get("/api/posts/twitter/565802608276047467", headers=H).get_json()["media"][0]
    files = sorted(os.listdir(env["media"]))
    called = []
    monkeypatch.setattr(app_module.threading, "Timer", lambda *a, **kw: called.append(("Timer", a)))
    monkeypatch.setattr(app_module.os, "_exit", lambda *a: called.append(("_exit", a)))
    monkeypatch.setattr(trash, "empty", lambda *a, **kw: called.append(("empty", a)) or {})
    monkeypatch.setattr(trash, "delete", lambda *a, **kw: called.append(("delete", a)) or {})
    monkeypatch.setattr(jobs, "submit", lambda *a, **kw: called.append(("submit", a)) or {"id": 0, "kind": a[0]})
    for headers in (FOREIGN["instagram"], FOREIGN["cross-site"]):
        for method, url, body in (("POST", "/api/quit", None),
                                  ("POST", "/api/trash/empty", None),
                                  ("POST", "/api/delete", {"media": [m["id"]]}),
                                  ("POST", "/api/jobs", {"kind": "tool-update", "params": {"tool": "yt-dlp"}}),
                                  ("POST", "/api/yt-dlp/info-json-cookies", {"apply": True})):
            r = client.open(url, method=method, json=body, headers=headers)
            assert r.status_code == 403, (url, r.status_code)
    assert called == [] and jobs.active() == []
    assert sorted(os.listdir(env["media"])) == files
    assert client.get("/api/posts/twitter/565802608276047467", headers=H).get_json()["media"][0]["id"] == m["id"]


# ---------------------------------------------------------------------------
# The allowlist is what the userscript calls
# ---------------------------------------------------------------------------

CALL = re.compile(r"""\bapi\(\s*"([A-Z]+)",\s*(?:"([^"]*)"|`([^`]*)`)""")


def _userscript_calls():
    with open(USERSCRIPT, encoding="utf-8") as f:
        src = f.read()
    # Every request goes through api(): one GM_xmlhttpRequest call, in it.
    assert len(re.findall(r"\bGM_xmlhttpRequest\(", src)) == 1
    calls = CALL.findall(src)
    assert len(calls) == len(re.findall(r"(?<!function )\bapi\(", src)), "an api(...) call this test cannot read"
    adapter = app_module.app.url_map.bind("localhost")
    out = set()
    for method, plain, template in calls:
        # The query string left out; each ${...} in the path an id.
        path = re.sub(r"\$\{.*?\}(?=/|$)", "1", (plain or template).split("?")[0])
        endpoint, _ = adapter.match(path, method=method)
        out.add((endpoint, method))
    return out


def test_the_allowlist_is_what_the_userscript_calls():
    got = _userscript_calls()
    assert len(got) == 7
    assert got == set(app_module.FOREIGN_ALLOWED)
