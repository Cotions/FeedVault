"""docs/API.md names every route the app serves, and nothing else.

A route is documented by a table row that starts with its method and its
path in backticks, as docs/API.md says at the top:

    | GET | `/api/posts?q=&…` | … |

The query string is left out of the match, and a placeholder matches any
placeholder (`<id>` in the doc, `<int:pid>` in the app). A path segment the
app takes as a plain string (`/api/collections/<int:cid>/<action>`) may be
documented once per value instead (`/api/collections/<id>/rename`).
"""
import os
import re

DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "API.md")
ROW = re.compile(r"^\| *(GET|POST|PUT|PATCH|DELETE) *\| *`(/[^`?]*)")
PLACEHOLDER = re.compile(r"^<(?:(\w+)(?:\([^)]*\))?:)?\w+>$")


def _doc_rows():
    rows = []
    with open(DOC, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
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
        elif not PLACEHOLDER.match(d) and m.group(1) not in (None, "string"):
            return False                     # a literal stands in only for a plain string segment
    return True


def test_the_row_format_finds_routes():
    assert len(_doc_rows()) > 50, "docs/API.md: no `| METHOD | `/path` |` rows found; did the format change?"


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


def test_matching():
    assert _matches("/api/people/<id>", "/api/people/<int:pid>")
    assert _matches("/api/collections/<id>/rename", "/api/collections/<int:cid>/<action>")
    assert not _matches("/api/people/suggestions", "/api/people/<int:pid>")
    assert not _matches("/api/people", "/api/people/<int:pid>")
    assert _matches("/<path>", "/<path:path>")
