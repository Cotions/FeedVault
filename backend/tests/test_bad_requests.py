"""Bodies that are not a JSON object, and ids out of SQLite's range (#109):
a 400 or a 404, never a 500.

The routes come from app.url_map, so a route added later is covered without
being listed here: every POST, PUT, PATCH or DELETE whose view reads a body
(``_body()`` or ``get_json``), and every route with an int in its path.
"""
import inspect
import re

import pytest

from conftest import H

import app as app_module

WRITES = {"POST", "PUT", "PATCH", "DELETE"}
VAR = re.compile(r"<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>")
NOT_OBJECTS = [[1], "x", 5, True]
HUGE = "9" * 25                                # > 2**63 - 1: SQLite cannot take it


def _url(rule, number="1"):
    """The rule with each int variable as ``number`` and anything else as "x"."""
    return VAR.sub(lambda m: number if m.group(1) == "int" else "x", rule.rule)


def _reads_body(view):
    src = inspect.getsource(view)
    return "_body()" in src or "get_json" in src


def _body_routes():
    out = []
    for rule in app_module.app.url_map.iter_rules():
        if _reads_body(app_module.app.view_functions[rule.endpoint]):
            out += [(m, rule) for m in sorted(rule.methods & WRITES)]
    return out


def _id_routes():
    out = []
    for rule in app_module.app.url_map.iter_rules():
        if any(m.group(1) == "int" for m in VAR.finditer(rule.rule)):
            out += [(m, rule) for m in sorted(rule.methods - {"HEAD", "OPTIONS"})]
    return out


BODY_ROUTES = _body_routes()
ID_ROUTES = _id_routes()


def test_the_lists_cover_the_routes_named_in_the_issue():
    body = {(m, r.rule) for m, r in BODY_ROUTES}
    for route in ("/api/saved", "/api/delete", "/api/trash/restore", "/api/trash/purge", "/api/duplicates/resolve",
                  "/api/duplicates/dismiss", "/api/review", "/api/tags/apply", "/api/collections",
                  "/api/collections/<int:cid>/<action>", "/api/people", "/api/people/merge",
                  "/api/people/<int:pid>", "/api/people/<int:pid>/accounts", "/api/sources", "/api/config",
                  "/api/jobs", "/api/yt-dlp/info-json-cookies"):
        assert ("POST", route) in body, route
    assert ("POST", "/api/quit") not in body   # no body: never sent one here
    ids = {(m, r.rule) for m, r in ID_ROUTES}
    for route in (("GET", "/api/collections/<int:cid>"), ("DELETE", "/api/sources/<int:sid>"),
                  ("GET", "/api/jobs/<int:job_id>"), ("GET", "/api/jobs/<int:job_id>/log"),
                  ("POST", "/api/jobs/<int:job_id>/cancel"), ("GET", "/media/<int:media_id>"),
                  ("GET", "/media/<int:media_id>/thumb"), ("GET", "/media/<int:media_id>/poster"),
                  ("GET", "/media/copy/<int:copy_id>/thumb")):
        assert route in ids, route


@pytest.mark.parametrize("body", NOT_OBJECTS, ids=["list", "string", "number", "true"])
@pytest.mark.parametrize("method, rule", BODY_ROUTES, ids=[f"{m} {r.rule}" for m, r in BODY_ROUTES])
def test_a_body_that_is_not_an_object_is_a_400(client, method, rule, body):
    r = client.open(_url(rule), method=method, json=body, headers=H)
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:300])
    assert r.get_json()["ok"] is False


@pytest.mark.parametrize("method, rule", ID_ROUTES, ids=[f"{m} {r.rule}" for m, r in ID_ROUTES])
def test_an_id_out_of_range_is_a_404(client, method, rule):
    r = client.open(_url(rule, HUGE), method=method, json={}, headers=H)
    assert r.status_code == 404, (r.status_code, r.get_data(as_text=True)[:300])


@pytest.mark.parametrize("number", ["9223372036854775807", "9223372036854775808", "9" * 5000])
def test_the_largest_id_is_a_lookup_and_one_more_is_a_404(client, number):
    # 2**63 - 1 reaches the route (no such job); 2**63 and a number too long for int() do not.
    r = client.get(f"/api/jobs/{number}", headers=H)
    assert r.status_code == 404
    assert (r.get_json() or {}).get("error") == ("no such job" if number == str(2**63 - 1) else None)


@pytest.mark.parametrize("url, body", [
    ("/api/delete", {"media": [10**30]}),
    ("/api/delete", {"media": [-(10**30)]}),
    ("/api/collections/reorder", {"ids": [1, 2**63]}),
])
def test_an_id_out_of_range_in_a_body_is_a_400(client, url, body):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:300])
    assert r.get_json()["ok"] is False


def test_a_person_link_order_with_an_id_out_of_range_is_a_400(client):
    pid = client.post("/api/people", json={"name": "Ann"}, headers=H).get_json()["person"]["id"]
    r = client.post(f"/api/people/{pid}/links/order", json={"ids": [10**30]}, headers=H)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


@pytest.mark.parametrize("body", ["tools", "media_roots", ["tools"]])
def test_config_with_a_body_that_names_a_key_is_a_400(client, body):
    r = client.post("/api/config", json=body, headers=H)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


@pytest.mark.parametrize("kind", [[], {}, ["sync"], 5, None])
def test_a_job_kind_that_is_not_text_is_a_400(client, kind):
    r = client.post("/api/jobs", json={"kind": kind}, headers=H)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_routes_without_a_body_still_take_none(client):
    # A body is optional where it always was: nothing sent is not an error.
    assert client.post("/api/config", headers=H).get_json()["ok"] is True
    r = client.post("/api/yt-dlp/info-json-cookies", headers=H)
    assert r.status_code == 200 and r.get_json()["applied"] is False
