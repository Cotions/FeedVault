"""docs/API.md names every route the app serves, and nothing else.

A route is documented by a table row that starts with its method and its
path in backticks, as docs/API.md says at the top:

    | GET | `/api/posts?q=&…` | … |

The query string is left out of the match, and a placeholder matches any
placeholder (`<id>` in the doc, `<int:pid>` in the app). Only a segment
listed in ``BY_VALUE`` (`/api/collections/<int:cid>/<action>`) is
documented once per value instead (`/api/collections/<id>/rename`), and
each such value is checked against the app.
"""
import os
import re

from conftest import H

DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "API.md")
METHODS = "GET|POST|PUT|PATCH|DELETE"
ROW = re.compile(rf"^\| ({METHODS}) \| `(/[^`?]*)")
# A line that looks like a route row in any other shape: refused, so no row is silently skipped.
LOOSE = re.compile(rf"^\s*\|\s*`?\s*({METHODS})\b")
PLACEHOLDER = re.compile(r"^<(?:\w+(?:\([^)]*\))?:)?(\w+)>$")
BY_VALUE = {"action"}                        # placeholders documented as one row per value


def _doc_lines():
    with open(DOC, encoding="utf-8") as f:
        return list(enumerate(f, 1))


def _doc_rows():
    rows = []
    for n, line in _doc_lines():
        m = ROW.match(line)
        if m:
            rows.append((n, m.group(1), m.group(2)))
    return rows


def _app_routes():
    import app
    out = []
    for rule in app.app.url_map.iter_rules():
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            out.append((method, rule.rule))
    return out


def _matches(doc_path, rule):
    doc, app = doc_path.split("/"), rule.split("/")
    if len(doc) != len(app):
        return False
    for d, a in zip(doc, app):
        m = PLACEHOLDER.match(a)
        if m is None:
            if d != a:
                return False
        elif not PLACEHOLDER.match(d) and m.group(1) not in BY_VALUE:
            return False
    return True


def test_the_row_format_finds_routes():
    assert len(_doc_rows()) > 50, "docs/API.md: no `| METHOD | `/path` |` rows found; did the format change?"


def test_no_row_in_another_shape():
    loose = [f"docs/API.md:{n}: {line.strip()[:80]}" for n, line in _doc_lines()
             if LOOSE.match(line) and not ROW.match(line)]
    assert not loose, ("route rows must read `| METHOD | `/path` | … |`, one method per row:\n  "
                       + "\n  ".join(loose))


def test_every_route_is_documented(env):
    rows = _doc_rows()
    missing = [f"{method} {rule}" for method, rule in _app_routes()
               if not any(m == method and _matches(p, rule) for _, m, p in rows)]
    assert not missing, ("in the app's URL map but not in docs/API.md (add a row "
                         "`| METHOD | `/path` | … |`):\n  " + "\n  ".join(sorted(missing)))


def test_every_documented_route_exists(env):
    routes = _app_routes()
    stale = [f"docs/API.md:{n}: {method} {path}" for n, method, path in _doc_rows()
             if not any(m == method and _matches(path, rule) for m, rule in routes)]
    assert not stale, "documented in docs/API.md but not in the app's URL map:\n  " + "\n  ".join(stale)


def test_every_documented_collection_action_exists(client):
    """`<action>` rows: the app answers an unknown collection with its JSON
    404 for an action it has, and a bare 404 for one it does not."""
    stale = []
    for n, method, path in _doc_rows():
        m = re.fullmatch(r"/api/collections/<id>/(\w[\w-]*)", path)
        if m:
            r = client.open(f"/api/collections/999999999/{m.group(1)}", method=method, headers=H, json={})
            if r.status_code != 404 or (r.get_json(silent=True) or {}).get("error") != "no such collection":
                stale.append(f"docs/API.md:{n}: {method} {path}")
    assert not stale, "collection actions the app does not have:\n  " + "\n  ".join(stale)


def test_matching():
    assert _matches("/api/people/<id>", "/api/people/<int:pid>")
    assert _matches("/api/collections/<id>/rename", "/api/collections/<int:cid>/<action>")
    assert not _matches("/api/people/suggestions", "/api/people/<int:pid>")
    assert not _matches("/api/people", "/api/people/<int:pid>")
    assert not _matches("/api/scripts/anything", "/api/scripts/<sid>")
    assert not _matches("/api/posts/foo/bar", "/api/posts/<platform>/<post_id>")
    assert _matches("/<path>", "/<path:path>")
    assert LOOSE.match("| GET, POST | `/api/x` |") and not ROW.match("| GET, POST | `/api/x` |")
