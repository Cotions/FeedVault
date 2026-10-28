"""FeedVault backend: a catalog of downloaded social posts.

Run from source with ./run.sh, or directly: python backend/app.py
"""
import atexit
import io
import os
import stat
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser

from flask import Flask, Response, abort, g, jsonify, make_response, request, send_file, send_from_directory
from werkzeug.exceptions import NotFound
from werkzeug.routing import IntegerConverter
from werkzeug.security import safe_join
from zlib import adler32

import archives
import avatars
import biofetch
import browsers
import config
import db
import downloaders
import duplicates
import hashing
import info_cookies
import jobs
import library
import linklabels
import links
import news
import nosync
import notify
import organize
import people
import save
import save_tools
import scanner
import scheduler
import scripts
import slim
import sources
import sync
import thumbs
import trash
import trash_history
import userdata
from parsers import yt_dlp

MAX_ID = 2**63 - 1                             # SQLite's largest integer


class IdConverter(IntegerConverter):
    """``<int:...>`` in every route: an id SQLite can hold, else a 404 (no
    such item) whatever the method, rather than an OverflowError."""

    def to_python(self, value):
        if len(value) > len(str(MAX_ID)) or int(value) > MAX_ID:
            raise NotFound()
        return super().to_python(value)


app = Flask(__name__, static_folder=None)
# Before any route is added: each rule takes its converter when it is.
app.url_map.converters["int"] = IdConverter

# ---------------------------------------------------------------------------
# Origin lockdown (same design as ChannelVault)
#
# The server binds to 127.0.0.1, but any website open in the same browser can
# still script requests to localhost. Two rules close that off without a login:
#
#   1. The Host header must name this machine, which blocks DNS rebinding.
#   2. Every /api request must carry X-FeedVault, whatever the method. A
#      cross-origin page cannot add a custom header without a CORS preflight,
#      and no CORS is ever granted. The dashboard is same-origin and the
#      userscript uses GM_xmlhttpRequest, so both can send it.
#
# /media is exempt because <img> and <video> cannot send headers; it is
# refused instead when the browser says another site asks (_foreign_origin).
#
# The header does not say who sends it: any userscript can, from any site.
# So an /api request from another site (the userscript's, from instagram.com)
# reaches only what the userscript calls (FOREIGN_ALLOWED); every other
# route, a new one included, answers it 403 before it runs (#112).
# ---------------------------------------------------------------------------

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
CSRF_HEADER = "X-FeedVault"

# (endpoint, method) another site may reach: exactly the userscript's calls
# (userscript/feedvault.user.js; test_foreign_origin checks the two agree).
FOREIGN_ALLOWED = frozenset({
    ("saved", "POST"),                 # POST /api/saved
    ("save_post", "POST"),             # POST /api/save
    ("get_job", "GET"),                # GET /api/jobs/<id>
    ("resolve_source", "GET"),         # GET /api/sources/resolve
    ("create_source", "POST"),         # POST /api/sources (never with a script, nor a login)
    ("get_source", "GET"),             # GET /api/sources/<id>
    ("sync_source", "POST"),           # POST /api/sources/<id>/sync (never a script, nor a login)
    ("link_people", "GET"),            # GET /api/links/people (ids and names only)
    ("import_links", "POST"),          # POST /api/links/import (a link-in-bio page's links, insert-only)
    ("create_link", "POST"),           # POST /api/links (the "Add this page" menu command)
})
FOREIGN_REFUSED = "this can only be called from FeedVault's own dashboard"
FOREIGN_LOGIN = "this source syncs with a login, which only FeedVault's own dashboard can start"
FOREIGN_LINK = "from another site, a link is saved with a url and a title only, to no one"

# The profile a page was loaded in (its id). A tab left open across a profile
# switch must not write into the other library with ids from the old one:
# a request that changes something and names another profile than the active
# one is answered 409 with profile_switched, and the dashboard reloads. The
# userscript sends none: what it saves goes to the active profile.
PROFILE_HEADER = "X-FeedVault-Profile"
PROFILE_SWITCHED = "another profile is active now; reload the page"
# Fine from any tab: the switch itself, and quitting.
PROFILE_FREE = frozenset({"activate_profile", "quit_app"})


def _host_only(host_header):
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def _foreign_origin():
    """Whether the request comes from a page that is not FeedVault's: an
    Origin not on this machine, or a browser saying it is cross-site. The
    userscript's requests from instagram.com are; the dashboard's are not
    (nor the Vite dev server's, on another port of this machine)."""
    origin = request.headers.get("Origin")
    if origin is not None:
        scheme, _, host = origin.partition("://")
        if scheme != "http" or _host_only(host) not in ALLOWED_HOSTS:
            return True
    return request.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")


@app.before_request
def _origin_guard():
    if _host_only(request.headers.get("Host")) not in ALLOWED_HOSTS:
        return jsonify({"ok": False, "error": "forbidden host"}), 403
    if request.path.startswith("/api/") and request.method != "OPTIONS" \
            and not request.headers.get(CSRF_HEADER):
        return jsonify({"ok": False, "error": f"missing {CSRF_HEADER} header"}), 403
    # Default-deny for another site: by the matched endpoint and the method
    # (an unmatched path has no endpoint, so it is refused too).
    if request.path.startswith("/api/") and _foreign_origin() \
            and (request.endpoint, request.method) not in FOREIGN_ALLOWED:
        return jsonify({"ok": False, "error": FOREIGN_REFUSED}), 403
    # Media cannot send a header, so another site's <img> or link reaches it:
    # refused when the browser says the page is not ours, before any work.
    media = request.path.startswith("/media/")
    trash_thumb = request.path.startswith("/trash/") and request.path.endswith("/thumb")
    if (media or trash_thumb) and _foreign_origin():
        abort(403)
    return None


# No page of FeedVault's may be framed: under another site's page, a click
# on the dashboard (Empty trash, Run) would pass every check above.
NO_FRAMES = "frame-ancestors 'none'"
# And the dashboard's fonts come from FeedVault only (#148: they came from
# Google Fonts, which saw every page load). A response with a policy of its
# own (a file's: default-src 'none') keeps it, stricter already.
OWN_FONTS = "font-src 'self'"


@app.after_request
def _no_frames(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    csp = resp.headers.get("Content-Security-Policy")
    resp.headers["Content-Security-Policy"] = f"{csp}; {NO_FRAMES}" if csp else f"{NO_FRAMES}; {OWN_FONTS}"
    return resp


# The hashing worker waits before its next file while a request is being
# answered (hashing.request_started). Counted until the response is made,
# not while a file is streamed: that is reading, not Python.
@app.before_request
def _answering():
    hashing.request_started()
    g.answering = True


def _answered():
    if g.pop("answering", False):
        hashing.request_finished()
    if g.pop("in_library", False):
        library.leave()


# Every request is answered inside the library's gate (library.py): a profile
# switch waits for those under way, and those that arrive wait for it. Only
# then is the page's profile compared: a write that waited for a switch is
# refused like one sent after it.
@app.before_request
def _in_library():
    library.enter()
    g.in_library = True
    page = request.headers.get(PROFILE_HEADER)
    if page and request.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS") \
            and request.endpoint not in PROFILE_FREE and page != config.load()["active_profile"]:
        return jsonify({"ok": False, "error": PROFILE_SWITCHED, "profile_switched": True}), 409
    return None


@app.after_request
def _answered_response(resp):
    _answered()
    return resp


@app.teardown_request
def _answered_teardown(_exc):
    _answered()                                # when an error skipped after_request


def _roots():
    return config.load()["media_roots"]


NOT_OBJECT = "the body must be a JSON object"


def _body():
    """The JSON body as a dict: {} when there is none (or it is not JSON),
    as a route that takes nothing gets. JSON of another shape (a list, a
    string, a number) is answered 400 here, before the route reads it."""
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        abort(make_response(jsonify({"ok": False, "error": NOT_OBJECT}), 400))
    return body


def _ids(value):
    """Whether ``value`` is a list of ids: whole numbers from 0 below 2**53,
    so none is out of SQLite's range."""
    return isinstance(value, list) and all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i < 2**53
                                           for i in value)


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

def _int_arg(name, default, lo, hi):
    try:
        v = int(request.args.get(name, default))
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def _person_arg(name="person"):
    """The ``person`` parameter (or another id's): an id, or None when
    absent. A value that cannot be an id names nobody, so it matches
    nothing (-1)."""
    v = request.args.get(name)
    if not v:
        return None
    return int(v) if v.isascii() and v.isdigit() and len(v) < 16 else -1


def _post_filters():
    """The /api/posts filter parameters, shared with /api/posts/summary."""
    return dict(
        q=request.args.get("q", "").strip() or None,
        platform=request.args.get("platform") or None,
        author=request.args.get("author") or None,
        kind=request.args.get("kind") or None,
        review=request.args.get("review") if request.args.get("review") in ("unreviewed", "kept") else None,
        # spaces collapsed only: a name that cannot be a tag matches nothing
        tags=[" ".join(t.split()) for t in request.args.getlist("tag") if t.strip()],
        untagged=request.args.get("untagged") == "1",
        person=_person_arg(),
        new=request.args.get("new") == "1",
        collection=_person_arg("collection"),
        notification=_person_arg("notification"),
    )


@app.get("/api/posts")
def list_posts():
    sort = request.args.get("sort", "posted")
    total, posts = db.list_posts(
        db.connect(),
        **_post_filters(),
        sort=sort if sort in ("posted", "saved") else "posted",
        order="asc" if request.args.get("order") == "asc" else "desc",
        offset=_int_arg("offset", 0, 0, 10**9),
        limit=_int_arg("limit", 60, 1, 200),
    )
    return jsonify({"total": total, "posts": posts})


@app.get("/api/posts/summary")
def posts_summary():
    return jsonify(db.post_summary(db.connect(), **_post_filters()))


# ---------------------------------------------------------------------------
# New posts (news.py)
# ---------------------------------------------------------------------------

@app.get("/api/new")
def new_posts():
    return jsonify(news.summary(db.connect()))


def _whom(body):
    """The person (an id) or account ((platform, id)) a body names, as
    ``person`` or ``account``: (person, account, error)."""
    person, account = body.get("person"), body.get("account")
    if person is not None and account is not None:
        return None, None, "send person or account, not both"
    if person is not None and (not isinstance(person, int) or isinstance(person, bool)
                               or not people.exists(db.connect(), person)):
        return None, None, "no such person"
    if account is not None:
        found = people.clean_accounts([account])
        if not found or people.canonical(db.connect(), *found[0]) not in db.accounts(db.connect()):
            return None, None, "account must be an indexed { platform, id }"
        account = found[0]
    return person, account, None


@app.post("/api/new/seen")
def mark_seen():
    body = request.get_json(silent=True)
    body = {} if body is None else body
    at = body.get("at") if isinstance(body, dict) else None
    if not isinstance(body, dict) or set(body) - {"at", "person", "account"} \
            or (at is not None and (not isinstance(at, int) or isinstance(at, bool) or not 0 <= at < 2**53)):
        return jsonify({"ok": False, "error": "send { at } (unix seconds), or nothing for now, "
                                              "and person or account for theirs only"}), 400
    person, account, error = _whom(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    if person is None and account is None:
        return jsonify({"ok": True, "since": news.mark_seen(conn, at)})
    mark = news.mark_seen(conn, at, person=person, account=account)
    return jsonify({"ok": True, "since": news.seen_at(conn), "at": mark})


@app.post("/api/new/mute")
def mute():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"muted", "person", "account"} \
            or not isinstance(body.get("muted"), bool) or (body.get("person") is None) == (body.get("account") is None):
        return jsonify({"ok": False, "error": "send { muted: true or false } and person or account"}), 400
    person, account, error = _whom(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    if account is not None and body["muted"]:
        # Unmuting one is always allowed: it may have been muted before it was linked.
        owner = db.accounts(conn)[people.canonical(conn, *account)]["person"]
        if owner:
            return jsonify({"ok": False, "error": f"that account is {owner['name']}'s: mute the person"}), 400
    news.mute(conn, body["muted"], person=person, account=account)
    return jsonify({"ok": True, "muted": news.muted(conn)})


# ---------------------------------------------------------------------------
# Don't sync (nosync.py)
# ---------------------------------------------------------------------------

NO_SYNC_BUSY = "a sync of theirs is queued or running: wait for it to end, or cancel it, first"


@app.get("/api/no-sync")
def no_sync_list():
    return jsonify(nosync.listing(db.connect()))


@app.post("/api/no-sync")
def set_no_sync():
    """Turn Don't sync on or off for a person or an account. Turned on for
    one whose sources would stop syncing, it asks first: 409 with
    ``confirm`` and how many, until sent again with ``"confirm": true``.
    Their sources stay stored. Never from another site (FOREIGN_ALLOWED)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"no_sync", "person", "account", "confirm"} \
            or not isinstance(body.get("no_sync"), bool) or not isinstance(body.get("confirm", False), bool) \
            or (body.get("person") is None) == (body.get("account") is None):
        return jsonify({"ok": False, "error": "send { no_sync: true or false } and person or account "
                                              "(and confirm: true once asked)"}), 400
    person, account, error = _whom(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    # Held from the busy check to the switch: no sync of theirs is queued in between.
    with sync._submitting:
        if body["no_sync"]:
            ids = sources.of_person_all(conn, person) if person is not None else sources.of_account(conn, *account)
            active = _sources_active()
            if any(sid in active for sid in ids):
                return jsonify({"ok": False, "error": NO_SYNC_BUSY}), 409
            asked = _no_sync_ask(conn, ids, body)
            if asked:
                return asked
        changed = nosync.set_on(conn, body["no_sync"], person=person, account=account)
    if changed:
        userdata.changed(changed)
    return jsonify({"ok": True, "no_sync": nosync.listing(conn)})


def _no_sync_ask(conn, ids, body):
    """The 409 that asks before sources ``ids`` stop syncing (those not
    stopped already), until the body has ``"confirm": true``; else None."""
    accounts, flags, cache = db.accounts(conn), nosync.flags(conn), {}
    stopping = [sid for sid in ids if not sources.not_synced(conn, sources.row(conn, sid), accounts, flags, cache)]
    if not stopping or body.get("confirm") is True:
        return None
    n = len(stopping)
    return jsonify({"ok": False, "confirm": True, "sources": n,
                    "error": f"{n} source{'' if n == 1 else 's'} would stop syncing (kept, not deleted): "
                             "send confirm: true"}), 409


# ---------------------------------------------------------------------------
# Notifications (notify.py)
# ---------------------------------------------------------------------------

@app.get("/api/notifications")
def notifications():
    return jsonify(notify.listing(db.connect()))


@app.post("/api/notifications/read")
def notifications_read():
    body = request.get_json(silent=True)
    body = {} if body is None else body
    upto = body.get("upto") if isinstance(body, dict) else None
    if not isinstance(body, dict) or set(body) - {"upto"} \
            or (upto is not None and (not isinstance(upto, int) or isinstance(upto, bool) or not 0 <= upto < 2**53)):
        return jsonify({"ok": False, "error": "send { upto } (an entry's id), or nothing for all"}), 400
    return jsonify({"ok": True, "read": notify.read(db.connect(), upto)})


@app.get("/api/posts/<platform>/<post_id>")
def get_post(platform, post_id):
    post = db.get_post(db.connect(), platform, post_id)
    if post is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify(post)


@app.get("/api/authors")
def list_authors():
    return jsonify(db.authors(db.connect()))


@app.get("/api/stats")
def get_stats():
    return jsonify(db.stats(db.connect(), _person_arg()))


@app.get("/api/storage")
def get_storage():
    t = trash.usage(_roots())
    # The cached result is shared: extend a copy.
    return jsonify({**db.storage(db.connect(), _person_arg()), "trash": {"files": t["files"], "bytes": t["bytes"]}})


@app.get("/api/unmatched")
def list_unmatched():
    conn = db.connect()
    hidden = duplicates.dismissed_copies(conn)
    return jsonify([r | {"dismissed": r["path"] in hidden} for r in db.unmatched(conn)])


@app.post("/api/saved")
def saved():
    body = _body()
    ids = body.get("ids")
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "ids must be a list"}), 400
    return jsonify({"saved": db.saved_ids(db.connect(), ids)})


@app.post("/api/save")
def save_post():
    """The userscript's Save button: one Instagram post by shortcode only
    (save.py), or one X or TikTok post by its link only, parsed strictly
    (save_tools.py). A post FeedVault already has is answered without
    running anything."""
    body = request.get_json(silent=True)
    if isinstance(body, dict) and set(body) == {"url"}:
        try:
            params = save_tools.parse_link(body["url"])
        except save_tools.BadLink as e:
            return jsonify({"ok": False, "error": f"{e}; {save_tools.LINK_HELP}"}), 400
        post = save.have_id(db.connect(), save_tools.post_id(params))
        submit = lambda: save_tools.submit(params)      # noqa: E731
    elif not isinstance(body, dict) or set(body) != {"platform", "shortcode"} or body["platform"] != "instagram" \
            or not save.valid_shortcode(body["shortcode"]):
        return jsonify({"ok": False, "error": 'send { "platform": "instagram", "shortcode": "<5 to 40 of '
                        'A-Z a-z 0-9 _ ->" } or { "url": "<an X or TikTok post\'s link>" }, and nothing else'}), 400
    else:
        code = body["shortcode"]
        post = save.have(db.connect(), code)
        submit = lambda: save.submit(code)              # noqa: E731
    if post is not None:
        return jsonify({"ok": True, "have": True, "post": post})
    try:
        job, existing = submit()
    except save.Full as e:
        return jsonify({"ok": False, "error": str(e)}), 429
    except jobs.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not existing:
        print(f"[jobs] #{job['id']} {job['kind']} queued")
    return jsonify({"ok": True, "have": False, "job": job, "existing": existing})


# ---------------------------------------------------------------------------
# Delete and trash
# ---------------------------------------------------------------------------

@app.post("/api/delete")
def delete_items():
    body = _body()
    posts = body.get("posts") or []
    media = body.get("media") or []
    if not isinstance(posts, list) or not all(isinstance(p, str) for p in posts) \
            or not _ids(media):
        return jsonify({"ok": False, "error": "posts must be a list of ids, media a list of numbers"}), 400
    if not posts and not media:
        return jsonify({"ok": False, "error": "nothing to delete"}), 400
    cfg = config.load()
    # ``from``: the page that asked, for the trash's History; anything else is no page.
    report = trash.delete(posts[:5000], media[:5000], cfg["media_roots"], cfg["data_directory"],
                          source=trash_history.source(body.get("from"), "deleted"))
    if report["posts"]:
        userdata.changed("decisions")          # a removed post takes its decision with it
    if report["posts"] or report["media"]:
        print(f"[trash] removed {len(report['posts'])} posts, {len(report['media'])} items, "
              f"{report['files']} files → trash")
    return jsonify(report)


def _str_list(value):
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) for v in value)


@app.post("/api/trash/restore")
def trash_restore():
    body = _body()
    posts, keys = body.get("posts"), body.get("keys")
    if posts is not None and not _str_list(posts) or keys is not None and not _str_list(keys) \
            or posts is None and keys is None:
        return jsonify({"ok": False, "error": "posts or keys must be a non-empty list of ids"}), 400
    cfg = config.load()
    report = trash.restore((posts or [])[:500], cfg["media_roots"], cfg["data_directory"],
                           keys=(keys or [])[:5000],     # decisions.json follows a Keep put back
                           source=trash_history.source(body.get("from"), "restored"))
    return jsonify(report)


@app.get("/api/trash/history")
def trash_history_list():
    cfg = config.load()
    return jsonify(trash.history(
        cfg["media_roots"], cfg["data_directory"],
        offset=_int_arg("offset", 0, 0, 10**9),
        limit=_int_arg("limit", 60, 1, 200),
        post=request.args.get("post") or None,
        upto=_int_arg("upto", 0, 0, 2**53) if request.args.get("upto") else None,
    ))


def _accounts_of(pid):
    """A person's accounts for the trash filters, or None for no person."""
    return None if pid is None else people.account_set(db.connect(), pid)


@app.get("/api/trash/items")
def trash_items():
    return jsonify(trash.items(
        _roots(),
        author=request.args.get("author") or None,
        platform=request.args.get("platform") or None,
        since=_int_arg("since", 0, 0, 2**53) if request.args.get("since") else None,
        before=_int_arg("before", 0, 0, 2**53) if request.args.get("before") else None,
        offset=_int_arg("offset", 0, 0, 10**9),
        limit=_int_arg("limit", 60, 1, 500),
        accounts=_accounts_of(_person_arg()),
        upto=_int_arg("upto", 0, 0, 2**53) if request.args.get("upto") else None,
    ))


@app.post("/api/trash/check")
def trash_check():
    return jsonify({"ok": True, **trash.check(_roots())})


def _purge_filter(f):
    """A purge filter, checked: {platform, author, person, since, before,
    upto} with at least one of the first five set; ``person`` becomes that
    person's ``accounts``."""
    keys = ("platform", "author", "person", "since", "before", "upto")
    if not isinstance(f, dict) or set(f) - set(keys):
        return None
    out = {k: f.get(k) for k in keys}
    if any(out[k] is not None and not (isinstance(out[k], str) and out[k]) for k in ("platform", "author")):
        return None
    for k in ("person", "since", "before", "upto"):
        if out[k] is not None and (not isinstance(out[k], int) or isinstance(out[k], bool)
                                   or not 0 <= out[k] < 2**53):
            return None
    if all(out[k] is None for k in keys[:5]):
        return None                            # upto alone would be the whole trash: that is Empty trash
    out["accounts"] = _accounts_of(out.pop("person"))
    return out


def _forgotten(result):
    """Tags of posts gone for good were dropped: write those files."""
    for name in result.pop("forgotten", []):
        userdata.changed(name)


@app.post("/api/trash/purge")
def trash_purge():
    body = _body()
    keys, match = body.get("keys"), body.get("filter")
    if match is not None:
        match = _purge_filter(match)
        if match is None or keys is not None:
            return jsonify({"ok": False, "error": "filter needs platform, author, person, since or before "
                                                  "(upto optional, no keys)"}), 400
    elif not _str_list(keys):
        return jsonify({"ok": False, "error": "keys must be a non-empty list"}), 400
    cfg = config.load()
    result = trash.purge(cfg["media_roots"], (keys or [])[:5000], cfg["data_directory"], match=match)
    _forgotten(result)
    print(f"[trash] purged {result['entries']} entries: {result['files']} files, "
          f"{result['bytes']} bytes deleted permanently")
    return jsonify(result)


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

def _threshold(value):
    """The similar kind's threshold from a request, else the config's (or the
    default, if the config's is not usable); None when the request's is not
    a whole number of bits in range."""
    def bits(v):
        if isinstance(v, str) and v.isascii() and v.isdigit():
            v = int(v)
        ok = isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= duplicates.SIMILAR_MAX
        return v if ok else None
    if value is None:
        value = bits(config.load()["similar_threshold"])
        return duplicates.SIMILAR_DEFAULT if value is None else value
    return bits(value)


THRESHOLD_ERROR = f"threshold must be a whole number from 0 to {duplicates.SIMILAR_MAX}"


@app.get("/api/duplicates")
def list_duplicates():
    kind = request.args.get("kind") or "copies"
    if kind not in duplicates.KINDS:
        return jsonify({"ok": False, "error": "kind must be copies, content or similar"}), 400
    threshold = _threshold(request.args.get("threshold"))
    if threshold is None:
        return jsonify({"ok": False, "error": THRESHOLD_ERROR}), 400
    return jsonify(duplicates.listing(db.connect(), kind,
                                      offset=_int_arg("offset", 0, 0, 10**9),
                                      limit=_int_arg("limit", 50, 1, 500), threshold=threshold))


@app.get("/api/duplicates/status")
def duplicates_status():
    return jsonify(hashing.status())


def _trash_entry(t):
    """A member to trash: its id (the whole member), or { member, items: [idx] }
    (just those items of a post) → (member id, None or [idx]), or None."""
    if isinstance(t, str):
        return t, None
    if (isinstance(t, dict) and isinstance(t.get("member"), str) and isinstance(t.get("items"), list)
            and 0 < len(t["items"]) <= 1000
            and all(isinstance(i, int) and not isinstance(i, bool) for i in t["items"])):
        return t["member"], t["items"]
    return None


def _choice(c):
    """{ group, keep } or { group, trash: [entry] } → what duplicates.resolve takes, or None."""
    if not isinstance(c, dict) or not isinstance(c.get("group"), str):
        return None
    if "trash" not in c:
        return {"group": c["group"], "keep": c["keep"]} if isinstance(c.get("keep"), str) else None
    if not isinstance(c["trash"], list) or len(c["trash"]) > 1000:
        return None
    entries = [_trash_entry(t) for t in c["trash"]]
    if None in entries or len({mid for mid, _ in entries}) != len(entries):
        return None
    return {"group": c["group"], "trash": dict(entries)}


@app.post("/api/duplicates/resolve")
def duplicates_resolve():
    body = _body()
    choices = body.get("groups") if "groups" in body else [body]
    choices = [_choice(c) for c in choices] if isinstance(choices, list) else None
    if not choices or len(choices) > 500 or None in choices:
        return jsonify({"ok": False, "error": "send { group, keep } or { group, trash: [member id or "
                        "{ member, items }] }, or groups: [those] (at most 500)"}), 400
    threshold = _threshold(body.get("threshold"))
    if threshold is None:
        return jsonify({"ok": False, "error": THRESHOLD_ERROR}), 400
    cfg = config.load()
    report = duplicates.resolve(choices, cfg["media_roots"], cfg["data_directory"], threshold=threshold)
    if report.get("posts"):
        userdata.changed("decisions")          # a trashed post takes its decision along, or hands it on
    for name in report.pop("carried", []):
        userdata.changed(name)
    if report.get("posts") or report.get("copies") or report.get("media"):
        print(f"[duplicates] resolved {len(report['resolved'])} groups: {len(report['posts'])} posts, "
              f"{len(report['copies'])} copies, {len(report['media'])} items, {report['files']} files → trash")
    return jsonify(report)


@app.post("/api/duplicates/dismiss")
def duplicates_dismiss():
    body = _body()
    if not isinstance(body.get("group"), str):
        return jsonify({"ok": False, "error": "group must be a group id"}), 400
    threshold = _threshold(body.get("threshold"))
    if threshold is None:
        return jsonify({"ok": False, "error": THRESHOLD_ERROR}), 400
    if not duplicates.dismiss(db.connect(), body["group"], threshold):
        return jsonify({"ok": False, "error": "no such group; reload"}), 404
    userdata.changed("dismissed_duplicates")
    return jsonify({"ok": True})


@app.delete("/api/duplicates/dismiss")
def duplicates_undismiss():
    body = _body()
    if not isinstance(body.get("group"), str):
        return jsonify({"ok": False, "error": "group must be a dismissed group's id"}), 400
    if not duplicates.undismiss(db.connect(), body["group"]):
        return jsonify({"ok": False, "error": "no such dismissal; reload"}), 404
    userdata.changed("dismissed_duplicates")
    return jsonify({"ok": True})


@app.get("/api/duplicates/dismissed")
def duplicates_dismissed():
    kind = request.args.get("kind")
    if kind is not None and kind not in duplicates.KINDS:
        return jsonify({"ok": False, "error": "kind must be copies, content or similar"}), 400
    return jsonify({"dismissed": duplicates.dismissed(db.connect(), kind)})


# ---------------------------------------------------------------------------
# Review decisions
# ---------------------------------------------------------------------------

@app.post("/api/review")
def review():
    body = _body()
    posts = body.get("posts")
    decision = body.get("decision")
    if not isinstance(posts, list) or not all(isinstance(p, str) for p in posts):
        return jsonify({"ok": False, "error": "posts must be a list of ids"}), 400
    if decision not in ("keep", None):
        return jsonify({"ok": False, "error": 'decision must be "keep" or null'}), 400
    ids = db.set_decision(db.connect(), posts[:5000], decision, int(time.time()))
    userdata.changed("decisions")
    return jsonify({"ok": True, "posts": ids})


@app.get("/api/trash")
def trash_usage():
    return jsonify(trash.usage(_roots()))


@app.post("/api/trash/empty")
def trash_empty():
    cfg = config.load()
    result = trash.empty(cfg["media_roots"], cfg["data_directory"])
    _forgotten(result)
    print(f"[trash] emptied: {result['files']} files, {result['bytes']} bytes deleted permanently")
    return jsonify(result)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def _names(value):
    """A list of tag names, cleaned, or None when any is not a valid name."""
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    names = [organize.clean_name(v) for v in value]
    return None if None in names else names


def _post_ids(body):
    """The body's ``posts`` if it is a list of 1 to MAX_POSTS ids, else None."""
    posts = body.get("posts")
    return posts if _str_list(posts) and len(posts) <= organize.MAX_POSTS else None


@app.get("/api/tags")
def list_tags():
    return jsonify(organize.tags(db.connect()))


@app.post("/api/tags/apply")
def tags_apply():
    body = _body()
    posts, add, remove = _post_ids(body), _names(body.get("add")), _names(body.get("remove"))
    if posts is None:
        return jsonify({"ok": False, "error": f"posts must be a list of 1 to {organize.MAX_POSTS} ids"}), 400
    if add is None or remove is None or not (add or remove):
        return jsonify({"ok": False, "error": "add or remove must be a list of tag names "
                        f"(1 to {organize.MAX_NAME} characters, no quotes)"}), 400
    r = organize.apply(db.connect(), posts, add, remove, int(time.time()))
    if r["created"]:
        userdata.changed("tags")
    if r["added"] or r["removed"]:
        userdata.changed("post_tags")
    return jsonify({"ok": True, **r})


@app.post("/api/tags/rename")
def tags_rename():
    body = _body()
    old, new = organize.clean_name(body.get("from")), organize.clean_name(body.get("to"))
    if old is None or new is None:
        return jsonify({"ok": False, "error": "from and to must be tag names "
                        f"(1 to {organize.MAX_NAME} characters, no quotes)"}), 400
    r = organize.rename(db.connect(), old, new)
    if r is None:
        return jsonify({"ok": False, "error": "no such tag"}), 404
    userdata.changed("tags")
    userdata.changed("post_tags")              # exported by tag name
    return jsonify({"ok": True, **r})


@app.post("/api/tags/delete")
def tags_delete():
    body = _body()
    name = organize.clean_name(body.get("name"))
    n = organize.delete(db.connect(), name) if name else None
    if n is None:
        return jsonify({"ok": False, "error": "no such tag"}), 404
    userdata.changed("tags")
    userdata.changed("post_tags")
    return jsonify({"ok": True, "posts": n})


@app.post("/api/tags/color")
def tags_color():
    body = _body()
    name = organize.clean_name(body.get("name"))
    try:
        color = organize.clean_color(body.get("color"))
    except ValueError:
        return jsonify({"ok": False, "error": "color must be #rrggbb or null"}), 400
    if name is None or "color" not in body:
        return jsonify({"ok": False, "error": "name must be a tag name, and color #rrggbb or null"}), 400
    if not organize.set_color(db.connect(), name, color):
        return jsonify({"ok": False, "error": "no such tag"}), 404
    userdata.changed("tags")
    return jsonify({"ok": True, "color": color})


@app.post("/api/tags/delete-unused")
def tags_delete_unused():
    names = _body().get("names")
    if not _str_list(names) or len(names) > 5000:
        return jsonify({"ok": False, "error": "names must be a list of 1 to 5000 tag names"}), 400
    gone = organize.delete_unused(db.connect(), names)
    if gone:
        userdata.changed("tags")
    return jsonify({"ok": True, "deleted": gone})


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

_BAD_NAME = f"name must be 1 to {organize.MAX_NAME} characters, no quotes"


@app.get("/api/collections")
def list_collections():
    return jsonify(organize.collections(db.connect()))


@app.post("/api/collections")
def create_collection():
    name = organize.clean_name(_body().get("name"))
    if name is None:
        return jsonify({"ok": False, "error": _BAD_NAME}), 400
    c = organize.create_collection(db.connect(), name, int(time.time()))
    if c is None:
        return jsonify({"ok": False, "error": "a collection with that name exists"}), 400
    userdata.changed("collections")
    return jsonify({"ok": True, "collection": c})


@app.post("/api/collections/reorder")
def reorder_collections():
    ids = _body().get("ids")
    if not _ids(ids) or not ids or len(ids) > 5000:
        return jsonify({"ok": False, "error": "ids must be a list of 1 to 5000 collection ids"}), 400
    conn = db.connect()
    organize.reorder_collections(conn, ids)
    userdata.changed("collections")
    return jsonify({"ok": True, "collections": organize.collections(conn)})


@app.get("/api/collections/<int:cid>")
def get_collection(cid):
    conn = db.connect()
    c = organize.collection(conn, cid)
    if c is None:
        return jsonify({"ok": False, "error": "no such collection"}), 404
    total, posts = organize.collection_posts(conn, cid, offset=_int_arg("offset", 0, 0, 10**9),
                                             limit=_int_arg("limit", 60, 1, 200))
    return jsonify({"collection": c, "total": total, "posts": posts})


@app.post("/api/collections/<int:cid>/<action>")
def change_collection(cid, action):
    body = _body()
    conn = db.connect()
    if action not in ("rename", "delete", "add", "remove", "order", "cover"):
        abort(404)
    if conn.execute("SELECT 1 FROM collections WHERE id = ?", (cid,)).fetchone() is None:
        return jsonify({"ok": False, "error": "no such collection"}), 404
    if action == "rename":
        name = organize.clean_name(body.get("name"))
        if name is None:
            return jsonify({"ok": False, "error": _BAD_NAME}), 400
        if not organize.rename_collection(conn, cid, name):
            return jsonify({"ok": False, "error": "a collection with that name exists"}), 400
        userdata.changed("collections")
        userdata.changed("collection_posts")       # exported by collection name
        return jsonify({"ok": True, "collection": organize.collection(conn, cid)})
    if action == "delete":
        n = organize.delete_collection(conn, cid)
        userdata.changed("collections")
        userdata.changed("collection_posts")
        return jsonify({"ok": True, "posts": n})
    if action == "cover":
        post = body.get("post")
        if post is not None and not isinstance(post, str) or not organize.set_cover(conn, cid, post):
            return jsonify({"ok": False, "error": "post must be the id of a post in this collection, or null"}), 400
        userdata.changed("collections")
        return jsonify({"ok": True, "collection": organize.collection(conn, cid)})
    posts = _post_ids(body)
    if posts is None:
        return jsonify({"ok": False, "error": f"posts must be a list of 1 to {organize.MAX_POSTS} ids"}), 400
    if action == "add":
        added = organize.add_posts(conn, cid, posts, int(time.time()))
        if added:
            userdata.changed("collection_posts")
        return jsonify({"ok": True, "added": added})
    if action == "remove":
        n = organize.remove_posts(conn, cid, posts)
        if n:
            userdata.changed("collection_posts")
            userdata.changed("collections")         # the cover may have gone with it
        return jsonify({"ok": True, "removed": n})
    organize.reorder(conn, cid, posts)
    userdata.changed("collection_posts")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

_BAD_PERSON_NAME = f"name must be 1 to {organize.MAX_NAME} characters, no quotes"
_BAD_ACCOUNTS = f"accounts must be a list of at most {people.MAX_ACCOUNTS} {{ platform, id }}"


def _people_changed(names=False):
    """Links changed (and, with ``names``, the people themselves: links,
    sources, mutes and saved web links are exported by person name)."""
    if names:
        userdata.changed("people")
        userdata.changed("person_labels")
        userdata.changed("sources")
        userdata.changed("muted_people")
        userdata.changed("no_sync_people")
        userdata.changed("links")
    userdata.changed("person_accounts")


def _untie_own_links(conn, pid, cfg=None):
    """A profile that became an account or a source of the person is no
    longer also in their Links (#217): each such link is untied from them
    only (links.remove_person: their other people keep it). Nothing comes
    back when the account or source goes."""
    if pid is None:
        return
    ids = biofetch.own_links(conn, pid, sources.routes(cfg or config.load()))
    if ids and links.remove_person(conn, ids, pid)[0]:
        userdata.changed("links")


@app.get("/api/people")
def list_people():
    return jsonify(people.people(db.connect()))


@app.get("/api/people/attributes")
def people_attributes():
    """Labels and genders in use, the gender presets and the country codes:
    what the person page's fields suggest and the Creators filters offer."""
    return jsonify(people.attributes(db.connect()))


MAX_PROFILES = 20                              # profile links per new person


def _profiles(value, conn, cfg):
    """([resolved source], routing table) for a new person's profile links
    (sources.resolve, or an Instagram name; two links to one profile count
    once), or raises sources.Refused naming the one that is not right."""
    if value is None:
        return [], None
    if not isinstance(value, list) or len(value) > MAX_PROFILES or not all(isinstance(v, str) for v in value):
        raise sources.Refused(f"profiles must be a list of at most {MAX_PROFILES} profile links")
    out, seen, table = [], set(), sources.routes(cfg)
    for text in dict.fromkeys(v.strip() for v in value if v.strip()):
        try:
            r = sources.resolve(text, table, cfg["media_roots"])
        except sources.Refused as e:
            try:
                r = sources.resolve_name(text, cfg["media_roots"])
            except sources.Refused:
                raise sources.Refused(f"{text[:200]}: {e}")
        if (r["tool"], r["target"]) in seen:
            continue                           # two links to one profile
        seen.add((r["tool"], r["target"]))
        folder = sources.inside_root(r["folder"], cfg["media_roots"]) or r["folder"]
        if sources.existing(conn, r["tool"], r["target"], folder, r["platform"]) is not None:
            raise sources.Refused(f"{text[:200]}: there is already a source for it; link its account instead")
        out.append(r)
    return out, table


@app.post("/api/people")
def create_person():
    """A person, with accounts already indexed and/or profile links: each
    link becomes a source of theirs (nothing is downloaded until a sync)."""
    body = _body()
    name, accounts = people.clean_name(body.get("name")), people.clean_accounts(body.get("accounts"))
    if name is None:
        return jsonify({"ok": False, "error": _BAD_PERSON_NAME}), 400
    if accounts is None:
        return jsonify({"ok": False, "error": _BAD_ACCOUNTS}), 400
    conn, cfg, now = db.connect(), config.load(), int(time.time())
    carried = nosync.count_accounts(conn)
    try:
        profiles, table = _profiles(body.get("profiles"), conn, cfg)
        if profiles and not cfg["media_roots"]:
            raise sources.Refused("add a media root in Settings first")
        p = people.create(conn, name, [] if profiles else accounts, now)
    except (people.Refused, sources.Refused) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    made = []
    if profiles:
        # The person is made empty, its accounts linked last: undoing it
        # (all or nothing) takes nobody's account away from them.
        try:
            for r in profiles:
                # Nothing set: they follow their platform's download defaults.
                made.append(sources.create(conn, cfg["media_roots"], r["tool"], r["target"], None, p["id"], None,
                                           None, now, table))
            # A profile whose account is indexed already: that account is
            # theirs too, unless it is someone else's (the source would
            # show under them).
            owners, given = db.accounts(conn), {people.canonical(conn, *a) for a in accounts}
            free = []
            for r in (sources.row(conn, sid) for sid in made):
                if r["author_id"]:
                    key = people.canonical(conn, r["platform"], r["author_id"])
                    other = (owners.get(key) or {}).get("person")
                    if other and key not in given:
                        raise sources.Refused(f"{r['target']}: that account is {other['name']}'s already; "
                                              "add the profile there, or merge the two people")
                    free.append(key)
            p = people.link(conn, p["id"], accounts + free, [], now)["person"]
        except (people.Refused, sources.Refused) as e:
            with conn:                         # all or nothing
                conn.executemany("DELETE FROM sources WHERE id = ?", [(sid,) for sid in made])
            people.delete(conn, p["id"])
            return jsonify({"ok": False, "error": str(e)}), 400
    _note_carried(conn, carried)
    _people_changed(names=True)
    if made:
        userdata.changed("sources")
    return jsonify({"ok": True, "person": p, "sources": [_source_or_404(sid) for sid in made]})


def _note_carried(conn, before):
    """userdata's no_sync_accounts, when accounts taken from a person set to
    Don't sync got the switch of their own (people._attach) since
    ``before`` (nosync.count_accounts)."""
    if nosync.count_accounts(conn) != before:
        userdata.changed("no_sync_accounts")


@app.post("/api/people/merge")
def merge_people():
    body = _body()
    ids, accounts = body.get("ids"), people.clean_accounts(body.get("accounts"))
    name = None if body.get("name") is None else people.clean_name(body.get("name"))
    if not _ids(ids) or not ids or len(ids) > people.MAX_ACCOUNTS:
        return jsonify({"ok": False, "error": "ids must be a list of person ids"}), 400
    ids = list(dict.fromkeys(ids))
    if body.get("name") is not None and name is None:
        return jsonify({"ok": False, "error": _BAD_PERSON_NAME}), 400
    if accounts is None:
        return jsonify({"ok": False, "error": _BAD_ACCOUNTS}), 400
    if len(ids) + len(accounts) < 2:
        return jsonify({"ok": False, "error": "nothing to merge: send two people, or a person and accounts"}), 400
    conn = db.connect()
    found = {r[0] for r in conn.execute(f"SELECT id FROM people WHERE id IN ({', '.join('?' for _ in ids)})", ids)}
    if found != set(ids):
        return jsonify({"ok": False, "error": "no such person"}), 404
    if body.get("confirm") not in (None, True):
        return jsonify({"ok": False, "error": "confirm must be true"}), 400
    muted = any(news.is_muted(conn, person=i) for i in ids)
    unsynced = any(nosync.is_on(conn, person=i) for i in ids)
    if unsynced:
        # The one kept is set to Don't sync: the others' sources, and those
        # of the accounts added, stop syncing. Asked first, as /api/no-sync does.
        stopping = {sid for i in ids for sid in sources.of_person_all(conn, i)}
        for a in accounts:
            stopping.update(sources.of_account(conn, *a))
        asked = _no_sync_ask(conn, sorted(stopping), body)
        if asked:
            return asked
    pictures = _avatars_of(conn, ids)
    carried = nosync.count_accounts(conn)
    try:
        p = people.merge(conn, ids, name, accounts, int(time.time()))
    except people.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    _note_carried(conn, carried)
    avatars.drop_unused(conn, config.load()["data_directory"], pictures)
    if muted:                                  # a muted one's accounts stay muted in the one they join
        news.mute(conn, True, person=p["id"])
    if unsynced:                               # and one's set to Don't sync stay so: nothing starts syncing
        nosync.set_on(conn, True, person=p["id"])
        p = people.person(conn, p["id"])
    _untie_own_links(conn, p["id"])            # the others' accounts and sources are its own now
    _people_changed(names=True)
    userdata.changed("sources")                # the others' sources moved to the first
    return jsonify({"ok": True, "person": p})


@app.get("/api/people/suggestions")
def people_suggestions():
    return jsonify(people.suggestions(db.connect()))


@app.post("/api/people/suggestions/dismiss")
def dismiss_suggestion():
    body = _body()
    if not isinstance(body.get("id"), str):
        return jsonify({"ok": False, "error": "id must be a suggestion id"}), 400
    if not people.dismiss(db.connect(), body["id"], int(time.time())):
        return jsonify({"ok": False, "error": "no such suggestion; reload"}), 404
    userdata.changed("dismissed_suggestions")
    return jsonify({"ok": True})


@app.post("/api/people/<int:pid>/sync")
def sync_person(pid):
    """Sync each of a person's sources, through the normal queue."""
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    ids = sources.of_person(conn, pid)
    queued, skipped, errors, not_synced, only = sync.sync_all(only=set(ids), scripts_ok=not _foreign_origin())
    if queued:
        print(f"[jobs] sync person {pid}: {len(queued)} queued")
    return jsonify({"ok": True, "sources": len(ids), "jobs": queued, "skipped": skipped, "errors": errors,
                    "not_synced": not_synced, "profile_only": only})


@app.get("/api/people/<int:pid>")
def get_person(pid):
    conn = db.connect()
    p = people.person(conn, pid) if people.exists(conn, pid) else None
    if p is None:
        return jsonify({"ok": False, "error": "no such person"}), 404
    return jsonify({**p, "links": _labelled(links.of_person(conn, pid))})


@app.post("/api/people/<int:pid>")
def update_person(pid):
    body = _body()
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    name = None if body.get("name") is None else people.clean_name(body["name"])
    notes = body.get("notes")
    gender = None if body.get("gender") is None else people.clean_gender(body["gender"])
    country = None if body.get("country") is None else people.clean_country(body["country"])
    labels = None if body.get("labels") is None else people.clean_labels(body["labels"])
    # Sent as null, the rating is cleared; not sent, it stays.
    rating = people.KEEP
    if "rating" in body:
        rating = None if body["rating"] is None else people.clean_rating(body["rating"])
    if body.get("name") is not None and name is None:
        return jsonify({"ok": False, "error": _BAD_PERSON_NAME}), 400
    if notes is not None and (not isinstance(notes, str) or len(notes) > people.MAX_NOTES):
        return jsonify({"ok": False, "error": f"notes must be text of at most {people.MAX_NOTES} characters"}), 400
    if body.get("gender") is not None and gender is None:
        return jsonify({"ok": False, "error": f"gender must be text of at most {people.MAX_GENDER} characters "
                                              "(\"\" for none)"}), 400
    if body.get("country") is not None and country is None:
        return jsonify({"ok": False, "error": "country must be an ISO 3166-1 alpha-2 code, such as \"FR\" "
                                              "(\"\" for none)"}), 400
    if body.get("labels") is not None and labels is None:
        return jsonify({"ok": False, "error": f"labels must be a list of at most {people.MAX_LABELS} names of "
                                              f"1 to {organize.MAX_NAME} characters, no quotes"}), 400
    if body.get("rating") is not None and rating is None:
        return jsonify({"ok": False, "error": f"rating must be a whole number from 1 to {people.MAX_RATING} "
                                              "(half stars: 7 is three and a half), or null for none"}), 400
    if name is None and notes is None and gender is None and country is None and labels is None \
            and rating is people.KEEP:
        return jsonify({"ok": False, "error": "send name, notes, gender, country, labels or rating"}), 400
    try:
        p = people.update(conn, pid, name, notes, gender, country, labels, int(time.time()), rating=rating)
    except people.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    _people_changed(names=name is not None)
    if notes is not None or gender is not None or country is not None or rating is not people.KEEP:
        userdata.changed("people")
    if labels is not None:
        userdata.changed("person_labels")
    return jsonify({"ok": True, "person": p})


@app.delete("/api/people/<int:pid>")
def delete_person(pid):
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    pictures = _avatars_of(conn, [pid])
    # Set to Don't sync: their accounts keep it, so none starts syncing.
    if nosync.is_on(conn, person=pid) and nosync.carry(conn, sorted(people.account_set(conn, pid))):
        userdata.changed("no_sync_accounts")
    n = people.delete(conn, pid)
    avatars.drop_unused(conn, config.load()["data_directory"], pictures)
    _people_changed(names=True)
    return jsonify({"ok": True, "unlinked": n})


def _avatars_of(conn, ids):
    return [r[0] for r in conn.execute(
        f"SELECT avatar FROM people WHERE id IN ({', '.join('?' for _ in ids)}) AND avatar != ''", ids)]


@app.get("/api/people/<int:pid>/avatar/choices")
def avatar_choices(pid):
    """What a person's picture can be made from, on this machine: the
    profile pictures instaloader saved for their accounts, and the newest
    pictures of their posts."""
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    pics = avatars.profile_pics(conn, pid, _roots())
    return jsonify({
        "profile_pics": [{"key": f["key"], "name": f["name"], "at": f["at"], "account": f["account"],
                          "url": f"/media/people/{pid}/profile-pic/{f['key']}"} for f in pics],
        "media": [{**m, "thumb": f"/media/{m['id']}/thumb"} for m in avatars.media(conn, pid)],
    })


def _set_avatar(conn, pid, name):
    """The person's picture becomes ``name`` ('' for none); the files of the
    one it replaces go when no one else has it."""
    old = _avatars_of(conn, [pid])
    p = people.update(conn, pid, avatar=name)
    avatars.drop_unused(conn, config.load()["data_directory"], old)
    userdata.changed("people")
    return p


def _upload_crop():
    """An upload's crop, from the query (x, y, size), or None."""
    if not any(k in request.args for k in ("x", "y", "size")):
        return None
    try:
        crop = {k: float(request.args.get(k, "")) for k in ("x", "y", "size")}
    except ValueError:
        raise avatars.Refused("crop must be x, y and size, fractions of the picture from 0 to 1")
    return avatars.clean_crop(crop)


def _avatar_source(conn, pid, body, roots):
    """The file of avatar_choices a JSON body names ({media} or
    {profile_pic}), opened, or raises avatars.Refused."""
    media_id, key = body.get("media"), body.get("profile_pic")
    if media_id is not None:
        if not _ids([media_id]):
            raise avatars.Refused("media must be a media id")
        src = avatars.media_source(conn, pid, media_id)
    elif isinstance(key, str):
        src = next((f["path"] for f in avatars.profile_pics(conn, pid, roots) if f["key"] == key), None)
        if src is None:
            raise avatars.Refused("no such profile picture; reload")
    else:
        raise avatars.Refused("send the picture as the body, or JSON with media or profile_pic")
    f = avatars.open_in_roots(src, roots)
    if f is None:
        raise avatars.Refused("that file is not in a media folder any more")
    return f


@app.post("/api/people/<int:pid>/avatar")
def set_avatar(pid):
    """A person's picture: the request's body is the picture itself (an
    image/* or application/octet-stream type; the crop in the query: x, y,
    size), or JSON naming one of avatar_choices ({media} or {profile_pic},
    and an optional crop)."""
    kind = (request.mimetype or "").lower()
    upload = kind.startswith("image/") or kind == "application/octet-stream"
    body = None if upload else _body()
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    cfg = config.load()
    try:
        if upload:
            crop = _upload_crop()
            try:
                data = avatars.read_upload(request.stream, request.content_length)
            except avatars.TooLarge as e:
                return jsonify({"ok": False, "error": str(e)}), 413
            name = avatars.make(cfg["data_directory"], io.BytesIO(data), crop)
        else:
            crop = avatars.clean_crop(body.get("crop"))
            with _avatar_source(conn, pid, body, cfg["media_roots"]) as f:
                name = avatars.make(cfg["data_directory"], f, crop)
    except avatars.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "person": _set_avatar(conn, pid, name)})


@app.delete("/api/people/<int:pid>/avatar")
def remove_avatar(pid):
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    return jsonify({"ok": True, "person": _set_avatar(conn, pid, "")})


@app.post("/api/people/<int:pid>/accounts")
def person_accounts(pid):
    body = _body()
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    add, remove = people.clean_accounts(body.get("add")), people.clean_accounts(body.get("remove"))
    if add is None or remove is None or not (add or remove):
        return jsonify({"ok": False, "error": "add or remove must be a list of { platform, id }"}), 400
    # Unlinked from someone set to Don't sync, an account keeps it.
    leaving = []
    if remove and nosync.is_on(conn, person=pid):
        mine = people.account_set(conn, pid)
        leaving = [people.canonical(conn, *k) for k in remove if tuple(k) in mine]
    carried = nosync.count_accounts(conn)
    try:
        r = people.link(conn, pid, add, remove, int(time.time()))
    except people.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if r["removed"] and leaving:
        nosync.carry(conn, leaving)
    _note_carried(conn, carried)
    if r["added"]:
        _untie_own_links(conn, pid)
    if r["added"] or r["removed"]:
        _people_changed()
    return jsonify({"ok": True, **r})


BIO_OFF = "link-in-bio import is off: turn it on in Settings → Downloads"


@app.post("/api/people/<int:pid>/bio-import")
def bio_import(pid):
    """A person's link-in-bio page (biofetch.py): the one page fetched, the
    accounts it lists suggested, and its address and the links it shows a
    visitor saved to the person's Links (links.keep: only the ones they do
    not have yet; one saved before for someone else becomes theirs too,
    nothing saved before is changed or removed, so importing again picks up
    what is new). A link to one of their accounts or sources is not saved
    (counted as already theirs, #217). No account or source is added: the
    dashboard adds each one the user picks through /api/people/<id>/accounts
    or /api/sources, which takes its link out of their Links. No link is fetched (a mydeck.bio-style redirect link
    on the page's own host is only asked where it goes: biofetch's
    REDIRECT_LINKS). Never from another site
    (_origin_guard)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"url"} or not isinstance(body["url"], str):
        return jsonify({"ok": False, "error": "send { url: a link-in-bio page's link }"}), 400
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    cfg = config.load()
    if not biofetch.enabled(cfg):
        return jsonify({"ok": False, "error": BIO_OFF}), 403
    try:
        page = biofetch.fetch(body["url"])
    except biofetch.Refused as e:
        try:
            shown = biofetch.check_url(body["url"])[2]
        except biofetch.Refused:
            shown = "a refused link"           # not logged as sent: it may hold anything
        print(f"[people] link-in-bio import of {shown}: {e}")
        return jsonify({"ok": False, "error": str(e)}), e.status
    read, table = biofetch.read(page.text, page.url, page.resolved), sources.routes(cfg)
    accounts = biofetch.suggest(conn, pid, biofetch.page_links(read), table, cfg["media_roots"],
                                sources.profile_only_platforms(cfg))
    found, dropped = biofetch.shown_links(read, page.url, table)
    found, own = biofetch.not_own(conn, pid, found, table)
    kept = links.keep(conn, pid, found, _link_now(), biofetch.MAX_SAVED)
    if kept is None:                           # deleted while the page was fetched
        return jsonify({"ok": False, "error": "no such person"}), 404
    kept["already"] += own                     # theirs as an account or a source (#217)
    if kept["saved"] or kept["shared"]:
        userdata.changed("links")
    kept["dropped"] = dropped
    # Counts only: nothing the page says is logged.
    print(f"[people] link-in-bio import of {page.url}: {len(accounts)} accounts, {kept['saved']} links saved, "
          f"{kept['shared']} saved already and now theirs too, {kept['already']} theirs already, {dropped} dropped, "
          f"{kept['over']} over the limit")
    return jsonify({"ok": True, "url": page.url, "accounts": accounts, "links": kept})


_BAD_SENT = (f"send {{ page_url: a link-in-bio page's link, links: [{{ url, title }}] (at most {biofetch.MAX_LINKS}; "
             f"url an http or https address of at most {links.MAX_URL} characters, title text of at most "
             f"{biofetch.LABEL_READ}), person: a person's id or null }}")


def _sent_links(body):
    """[(url, title)] of the import's ``links``, each as sent, or None when
    one is not { url: an http(s) address as text, title: text or none }."""
    sent = body.get("links")
    if not isinstance(sent, list) or len(sent) > biofetch.MAX_LINKS:
        return None
    out = []
    for x in sent:
        if not isinstance(x, dict) or "url" not in x or not set(x) <= {"url", "title"}:
            return None
        url, title = x["url"], x.get("title")
        if not isinstance(url, str) or len(url) > links.MAX_URL \
                or not url.lower().startswith(("http://", "https://")):
            return None
        if title is not None and (not isinstance(title, str) or len(title) > biofetch.LABEL_READ):
            return None
        out.append((url, title or ""))
    return out


@app.post("/api/links/import")
def import_links():
    """A link-in-bio page's links as the browser shows them (the
    userscript, on a page of biofetch.SITES): its address and those links
    saved to a person's Links, or to no one's, as the link-in-bio import
    does (links.keep: only the new ones, and the person given to those saved
    already; nothing saved before is changed or removed; none that is one of
    the person's accounts or sources, #217). Nothing is fetched: what is kept is what was sent, through
    biofetch's filters. Under the same switch as the import (bio_import).
    The userscript's, so another site reaches it (FOREIGN_ALLOWED)."""
    body = request.get_json(silent=True)
    sent = _sent_links(body) if isinstance(body, dict) else None
    if sent is None or not {"page_url", "links"} <= set(body) <= {"page_url", "links", "person"} \
            or not isinstance(body["page_url"], str):
        return jsonify({"ok": False, "error": _BAD_SENT}), 400
    pid, error = _link_person(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    cfg = config.load()
    if not biofetch.enabled(cfg):
        return jsonify({"ok": False, "error": BIO_OFF}), 403
    try:
        found, dropped = biofetch.sent_links(body["page_url"], sent)
    except biofetch.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    conn, page_url = db.connect(), found[0][0]
    found, own = biofetch.not_own(conn, pid, found, sources.routes(cfg))
    kept = links.keep(conn, pid, found, _link_now(), biofetch.MAX_SAVED)
    if kept is None:                           # deleted in between
        return jsonify({"ok": False, "error": "person must be the id of a person, or null"}), 400
    kept["already"] += own                     # theirs as an account or a source (#217)
    if kept["saved"] or kept["shared"]:
        userdata.changed("links")
    kept["dropped"] = dropped
    print(f"[links] import from the browser of {page_url}: {kept['saved']} links saved, "
          f"{kept['shared']} saved already and now theirs too, {kept['already']} there already, {dropped} dropped, "
          f"{kept['over']} over the limit")
    return jsonify({"ok": True, "url": page_url, "person": pid, "links": kept})


# ---------------------------------------------------------------------------
# Links (links.py: any web address, tied to people or to no one; never fetched)
# ---------------------------------------------------------------------------

_BAD_URL = f"url must be an http or https address of at most {links.MAX_URL} characters, with no user name in it"


def _link_person(body):
    """(person id or None, error): the body's ``person``, which must be a
    person's id or null."""
    pid = body.get("person")
    if pid is None:
        return None, None
    if not isinstance(pid, int) or isinstance(pid, bool) or not people.exists(db.connect(), pid):
        return None, "person must be the id of a person, or null"
    return pid, None


_BAD_PEOPLE = f"people must be a list of at most {links.MAX_PEOPLE} people's ids"


def _link_people(body):
    """(people's ids, error): the body's ``people`` (a list of people's
    ids, each once; [] for no one), else its ``person`` (one id, or null
    for no one) as a list; links.UNSET when it has neither. Both is an
    error."""
    if "people" in body and "person" in body:
        return links.UNSET, "send person or people, not both"
    if "person" in body:
        pid, error = _link_person(body)
        return ([] if pid is None else [pid]), error
    if "people" not in body:
        return links.UNSET, None
    pids = body["people"]
    if not _ids(pids) or len(pids) > links.MAX_PEOPLE:
        return links.UNSET, _BAD_PEOPLE
    pids = list(dict.fromkeys(pids))
    conn = db.connect()
    if not all(people.exists(conn, pid) for pid in pids):
        return links.UNSET, "people must be people's ids: one is not"
    return pids, None


# A person in the body deleted between its check and the write (links.NoSuchPerson).
_GONE_PERSON = {"ok": False, "error": "no such person: deleted meanwhile"}


def _link_text(body):
    """(title, notes, error), each None when absent from the body."""
    title = None if "title" not in body else links.clean_title(body["title"])
    notes = None if "notes" not in body else links.clean_notes(body["notes"])
    if "title" in body and title is None:
        return None, None, f"title must be text of at most {links.MAX_TITLE} characters"
    if "notes" in body and notes is None:
        return None, None, f"notes must be text of at most {links.MAX_NOTES} characters"
    return title, notes, None


def _link_now():
    """When a link is saved, or given to a person: the clock the links
    routes read (a test sets its own here, not time.time's)."""
    return int(time.time())


def _labelled(items, cfg=None):
    """Links (dicts, or one, or None) with the ``label`` of their site
    (linklabels.py: Settings → Link labels), read afresh for each answer."""
    if items is None:
        return None
    linklabels.apply(config.load() if cfg is None else cfg, items if isinstance(items, list) else [items])
    return items


def _taken(lid):
    """The 409 for a URL saved already: the link that has it, with its
    people, so the page can say whose it is."""
    return jsonify({"ok": False, "error": "that link is saved already", "id": lid,
                    "link": None if lid is None else _labelled(links.get(db.connect(), lid))}), 409


@app.get("/api/links")
def list_links():
    v = request.args.get("person")
    kind, q = request.args.get("kind") or None, (request.args.get("q") or "").strip()
    if kind not in (None, "social", "other"):
        return jsonify({"ok": False, "error": "kind must be social or other"}), 400
    if len(q) > links.MAX_QUERY:
        return jsonify({"ok": False, "error": f"q must be at most {links.MAX_QUERY} characters"}), 400
    cfg = config.load()
    out = links.listing(db.connect(), person=None if v == "none" else _person_arg(), no_person=v == "none",
                        kind_=kind, site_=request.args.get("site") or None, q=q or None,
                        q_sites=linklabels.matching(cfg, q) if q else ())
    _labelled(out["links"], cfg)
    return jsonify(out)


@app.post("/api/links")
def create_link():
    body = _body()
    # From another site (the userscript's "Add this page"): a URL and a title, saved to no one.
    if _foreign_origin() and not set(body) <= {"url", "title"}:
        return jsonify({"ok": False, "error": FOREIGN_LINK}), 403
    url = links.clean_url(body.get("url"))
    if url is None:
        return jsonify({"ok": False, "error": _BAD_URL}), 400
    title, notes, error = _link_text(body)
    pids, error = (None, error) if error else _link_people(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    taken = links.find(conn, url)
    if taken is not None:
        return _taken(taken)
    try:
        link = links.create(conn, url, title or "", notes or "", [] if pids is links.UNSET else pids, _link_now())
    except links.NoSuchPerson:
        return jsonify(_GONE_PERSON), 400
    except sqlite3.IntegrityError:             # saved by another request in between
        return _taken(links.find(conn, url))
    userdata.changed("links")
    return jsonify({"ok": True, "link": _labelled(link)})


@app.post("/api/links/<int:lid>")
def update_link(lid):
    body = _body()
    conn = db.connect()
    if links.get(conn, lid) is None:
        return jsonify({"ok": False, "error": "no such link"}), 404
    url = None if "url" not in body else links.clean_url(body["url"])
    if "url" in body and url is None:
        return jsonify({"ok": False, "error": _BAD_URL}), 400
    title, notes, error = _link_text(body)
    pids, error = (None, error) if error else _link_people(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    if not {"url", "title", "notes", "person", "people"} & set(body):
        return jsonify({"ok": False, "error": "send url, title, notes, person or people"}), 400
    other = links.find(conn, url) if url is not None else None
    if other is not None and other != lid:
        return _taken(other)
    kw = {"pids": pids, "now": _link_now()} if pids is not links.UNSET else {}
    try:
        link = links.update(conn, lid, url=url, title=title, notes=notes, **kw)
    except links.NoSuchPerson:
        return jsonify(_GONE_PERSON), 400
    except sqlite3.IntegrityError:
        return _taken(links.find(conn, url))
    userdata.changed("links")
    return jsonify({"ok": True, "link": _labelled(link)})


@app.delete("/api/links/<int:lid>")
def delete_link(lid):
    conn = db.connect()
    if links.get(conn, lid) is None or not links.delete(conn, lid):
        return jsonify({"ok": False, "error": "no such link"}), 404
    userdata.changed("links")
    return jsonify({"ok": True})


def _link_ids(body):
    """(the body's ``ids``, error): 1 to links.MAX_IDS link ids."""
    ids = body.get("ids")
    if not _ids(ids) or not ids or len(ids) > links.MAX_IDS:
        return None, f"ids must be a list of 1 to {links.MAX_IDS} link ids"
    return ids, None


@app.post("/api/links/assign")
def assign_links():
    """Many links to one person (``person`` null: to no one), or to several
    (``people``), in one transaction (links.assign): their people become
    those, replacing any other. All or none: an id no link has is a 404
    and nothing moves. With ``if_person`` (an id or null: an Undo), only
    the links that are that person's alone now move, the others are passed
    over."""
    body = _body()
    ids, error = _link_ids(body)
    if not error and "person" not in body and "people" not in body:
        error = "send person: the id of a person, or null"
    pids, error = (None, error) if error else _link_people(body)
    only = body.get("if_person", links.UNSET)
    if not error and only is not links.UNSET and only is not None and not (
            isinstance(only, int) and not isinstance(only, bool) and 0 <= only < 2**53):
        error = "if_person must be the id of a person, or null"
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    try:
        moved, gone, skipped = links.assign(conn, ids, pids, _link_now(), only_from=only)
    except links.NoSuchPerson:
        return jsonify(_GONE_PERSON), 400
    if gone and only is links.UNSET:
        return jsonify({"ok": False, "error": "no such link", "missing": gone}), 404
    if moved:
        userdata.changed("links")
    # A link deleted right after is left out, not given as null.
    out = [x for x in (links.get(conn, i) for i in dict.fromkeys(ids)) if x is not None]
    return jsonify({"ok": True, "moved": moved, "skipped": skipped, "links": _labelled(out)})


@app.post("/api/links/delete")
def delete_links():
    body = _body()
    ids, error = _link_ids(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    deleted = links.delete_many(db.connect(), ids)
    if deleted:
        userdata.changed("links")
    return jsonify({"ok": True, "deleted": deleted})


def _one_person_links(body):
    """(ids, person id, error) of an add-person or remove-person body:
    ``ids`` as _link_ids, ``person`` a person's id."""
    ids, error = _link_ids(body)
    pid = body.get("person")
    if not error and (pid is None or not isinstance(pid, int) or isinstance(pid, bool)
                      or not people.exists(db.connect(), pid)):
        error = "person must be the id of a person"
    return ids, pid, error


@app.post("/api/links/add-person")
def add_link_person():
    """Also give links to a person (links.add_person): each goes last in
    their order, and stays its other people's. All or none: an id no link
    has is a 404 and nothing changes. ``added``: the ids that were not
    theirs yet (an Undo takes those back, /api/links/remove-person)."""
    body = _body()
    ids, pid, error = _one_person_links(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    try:
        added, gone = links.add_person(conn, ids, pid, _link_now())
    except links.NoSuchPerson:
        return jsonify(_GONE_PERSON), 400
    if added is None:
        return jsonify({"ok": False, "error": "no such link", "missing": gone}), 404
    if added:
        userdata.changed("links")
    out = [x for x in (links.get(conn, i) for i in dict.fromkeys(ids)) if x is not None]
    return jsonify({"ok": True, "added": added, "links": _labelled(out)})


@app.post("/api/links/remove-person")
def remove_link_person():
    """Take links from a person (links.remove_person): their other people
    keep them; one left with no one goes to Unsorted. Ids no link has are
    passed over. ``removed``: the ids that were theirs."""
    body = _body()
    ids, pid, error = _one_person_links(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    removed, _ = links.remove_person(conn, ids, pid)
    if removed:
        userdata.changed("links")
    out = [x for x in (links.get(conn, i) for i in dict.fromkeys(ids)) if x is not None]
    return jsonify({"ok": True, "removed": removed, "links": _labelled(out)})


@app.get("/api/people/recent-links")
def recent_link_people():
    """The people most recently given a link (links.recent_people), for the
    quick-add's picker. ``limit`` is kept between 1 and links.MAX_RECENT."""
    return jsonify({"people": links.recent_people(db.connect(), _int_arg("limit", 10, 1, links.MAX_RECENT))})


@app.get("/api/links/people")
def link_people():
    """Whom the userscript's link import can save to: every person's id and
    name (links.names), nothing else of them, and the ids of the ten most
    recently given a link, latest first. The userscript's, so another site
    reaches it (FOREIGN_ALLOWED): that is why it is not /api/people."""
    conn = db.connect()
    return jsonify({"people": links.names(conn), "recent": [p["id"] for p in links.recent_people(conn, 10)]})


@app.post("/api/people/<int:pid>/links/order")
def order_person_links(pid):
    body = _body()
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    ids, error = _link_ids(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    links.reorder(conn, pid, ids)
    userdata.changed("links")
    return jsonify({"ok": True, "links": _labelled(links.of_person(conn, pid))})


# ---------------------------------------------------------------------------
# Sources (sources.py: where a person's posts are downloaded from)
# ---------------------------------------------------------------------------

def _sources_active():
    """{source id: its queued or running sync}."""
    return sync.active()


def _with_session(s, cfg=None):
    """A source with the session its sync would use (its own, else its
    tool's), for the form's login hint, and its schedule. A saved login
    says whether its session file exists (only looked for, never opened)."""
    session = sync.session_of(s["tool"], s["options"], cfg)
    if session["mode"] == "login":
        session = {**session, "session_file": downloaders.session_file_exists(session["user"])}
    out = {**s, "session": session, "metadata": _metadata_info(s["tool"], s["options"], s["defaults"], cfg),
           "schedule": scheduler.status(s, cfg), "script_warning": _script_warning(s)}
    return _foreign_source(out) if _foreign_origin() else out


# Another site (the userscript) reads a source to show its button: never
# what it inherits (the download defaults), nor which script or login its
# syncs use (a browser profile, an account name). "dashboard_only": its
# syncs only start from the dashboard (a script, or a login: sync_source).
FOREIGN_HIDDEN = ("defaults", "session", "metadata", "script_warning")


def _foreign_source(s):
    dashboard_only = s["options"]["script"] is not None or s["session"]["mode"] != "none"
    return {**{k: v for k, v in s.items() if k not in FOREIGN_HIDDEN},
            "options": {**s["options"], "script": None, "session": None}, "dashboard_only": dashboard_only}


def _script_warning(s):
    """Why a source's script would fail its next sync (scripts.warning), or None."""
    sid = s["options"]["script"]
    return scripts.warning(sid, s["tool"]) if sid is not None else None


@app.get("/api/sources")
def list_sources():
    r = sources.listing(db.connect(), _roots(), _sources_active())
    cfg = config.load()
    with scripts.read_once():                  # the scripts folder read once for every source's warning
        return jsonify({**r, "sources": [_with_session(s, cfg) for s in r["sources"]]})


def _source_or_404(sid):
    s = sources.get(db.connect(), sid, _sources_active())
    if s is None:
        abort(404)
    return _with_session(s)


@app.get("/api/sources/resolve")
def resolve_source():
    """What pasting a profile link would add: the tool, platform, target and
    default folder, shown before saving. ``tool=none``: as a profile-only
    source with no downloader, of any site. A link the routing table does
    not take says, in ``profile``, what it would be added as that way."""
    cfg = config.load()
    tool = request.args.get("tool")
    try:
        if tool == "instaloader":
            # A profile name or @name, as POST /api/sources takes it with that tool.
            r = sources.resolve_name(request.args.get("url"), cfg["media_roots"])
        else:
            r = sources.resolve(request.args.get("url"), sources.routes(cfg), cfg["media_roots"],
                                tool=sources.NONE if tool == sources.NONE else None)
    except sources.Refused as e:
        # An answer, not a failed request: the page asks as the user types.
        out, table = {"ok": False, "error": str(e)}, sources.routes(cfg)
        # Only a host the table lacks: a post's link is no profile either way.
        if tool is None and sources.parse_url(request.args.get("url"), table)[0] is None:
            try:
                out["profile"] = {k: v for k, v in sources.resolve(request.args.get("url"), table, [],
                                                                   tool=sources.NONE).items() if k != "folder"}
            except sources.Refused:
                pass
        return jsonify(out)
    conn = db.connect()
    folder = (sources.inside_root(r["folder"], cfg["media_roots"]) or r["folder"]) if r["folder"] else None
    # What a new source would start from: its platform's download defaults.
    defaults = sources.inherited(sources.new_row(r["tool"], r["platform"], r["target"]), cfg)
    sid = sources.existing(conn, r["tool"], r["target"], folder, r["platform"])
    # Its account (or the source there already) is set to Don't sync: no Add, no Sync.
    off = sources.not_synced(conn, sources.row(conn, sid)) if sid is not None \
        else sources.resolved_not_synced(conn, r["tool"], r["platform"], r["target"], folder, cfg["media_roots"])
    out = {"ok": True, **r, "source": sid, "no_sync": off,
           # Added as it is, it is profile only (#218): no downloader, or its
           # platform is in Settings → Profile only.
           "profile_only": r["tool"] == sources.NONE or not sources.wants_download(r["platform"], defaults, cfg),
           "choices": sources.choices(r["tool"], r["platform"], r["target"])}
    if _foreign_origin():
        return jsonify(out)       # not the defaults, nor the session they use (_foreign_source)
    return jsonify({**out, "defaults": defaults, "metadata": _metadata_info(r["tool"], defaults, defaults, cfg),
                    "session": sync.session_of(r["tool"], defaults, cfg)})


def _metadata_info(tool, options, defaults, cfg=None):
    """The metadata modes a source form shows: the one its syncs use
    (``options``), the one it inherits (``defaults``), the tool's setting."""
    out = {"mode": sync.metadata_of(tool, options, cfg), "inherited": sync.metadata_of(tool, defaults, cfg),
           "setting": sync.metadata_of(tool, {}, cfg)}
    # yt-dlp's none needs it, else its syncs write the files (sync.yt_dlp_mode).
    return {**out, "ffmpeg": thumbs.have_ffmpeg()} if tool == "yt-dlp" else out


def _script_refused(options, tool=None):
    """(error, status) when ``options`` (a source's, as sent) set a script
    that cannot be: from another origin (403), or one that does not exist,
    is refused now, or runs another tool than ``tool`` when it is known
    (400). Else None; a malformed id is parse_options'."""
    sid = options.get("script") if isinstance(options, dict) else None
    if not isinstance(sid, str) or not sources.SCRIPT_ID_RE.fullmatch(sid):
        return None
    if _foreign_origin():
        return FOREIGN, 403
    try:
        scripts.runnable(sid) if tool is None else scripts.runnable_for(sid, tool)
    except jobs.BadRequest as e:
        return str(e), 400
    return None


@app.post("/api/sources")
def create_source():
    body = _body()
    refused = _script_refused(body.get("options"))
    if refused:
        return jsonify({"ok": False, "error": refused[0]}), refused[1]
    # Which login a sync uses is the dashboard's to say: another site could
    # pick an account, and learn which browser profiles exist. From it a
    # source has none (below), and can be sent only that.
    session = body["options"].get("session") if isinstance(body.get("options"), dict) else None
    if isinstance(session, dict) and session.get("mode") != "none" and _foreign_origin():
        return jsonify({"ok": False, "error": FOREIGN_REFUSED}), 403
    tool = body.get("tool")
    if tool is not None and tool not in (*sources.TOOLS, sources.NONE):
        return jsonify({"ok": False, "error": f"tool must be one of: {', '.join((*sources.TOOLS, sources.NONE))}"}), 400
    cfg = config.load()
    table = sources.routes(cfg)
    if tool == sources.NONE:
        # A profile only (#218), of any site: no downloader, no folder.
        try:
            r = sources.resolve(body.get("target"), table, cfg["media_roots"], tool=sources.NONE)
        except sources.Refused as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if body.get("folder") is not None:
            return jsonify({"ok": False, "error": "a profile with no downloader has no folder"}), 400
        target = r["target"]
    elif tool == "instaloader":
        target = sources.parse_target(tool, body.get("target"))
        if target is None:
            return jsonify({"ok": False, "error": "target must be a profile name, @name or profile URL"}), 400
    else:
        # A link: the routing table picks the tool.
        try:
            r = sources.resolve(body.get("target"), table, cfg["media_roots"])
        except sources.Refused as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if tool is not None and r["tool"] != tool:
            return jsonify({"ok": False, "error": f"this link syncs with {r['tool']} (Settings → Link routing)"}), 400
        tool, target = r["tool"], r["target"]
    refused = _script_refused(body.get("options"), tool)   # now that its tool is known
    if refused:
        return jsonify({"ok": False, "error": refused[0]}), refused[1]
    folder, person, account = body.get("folder"), body.get("person"), body.get("account")
    if folder is not None and not isinstance(folder, str):
        return jsonify({"ok": False, "error": "folder must be an absolute path inside a media root"}), 400
    if person is not None and (not isinstance(person, int) or isinstance(person, bool) or not 0 <= person < 2**53):
        return jsonify({"ok": False, "error": "person must be a person id"}), 400
    if account is not None:
        account = people.clean_accounts([account])
        if not account:
            return jsonify({"ok": False, "error": "account must be { platform, id }"}), 400
        account = account[0]
    platform = sources.PLATFORM[tool] if tool == "instaloader" else r["platform"]
    if tool == sources.NONE and isinstance(body.get("options"), dict) and body["options"].get("download") == "on":
        return jsonify({"ok": False, "error": "a profile with no downloader cannot download: leave tool out "
                                              "so the routing table picks one", "needs_tool": True}), 400
    # Over what it inherits (its platform's download defaults): an option
    # not sent, or sent as it would be anyway, follows them.
    base = sources.inherited(sources.new_row(tool, platform, target), cfg)
    # A script, or a login, is the dashboard's to pick: from another site a
    # new source does not inherit them either. It runs the tool's own
    # command, with no login (set on it: not the tool's session setting).
    if _foreign_origin():
        base = {**base, "script": None, "session": {"mode": "none"}}
    options, error = sources.parse_options(body.get("options"), base=base, tool=tool, platform=platform,
                                           target=target)
    error = error or sources.login_refused(options, platform, sync.session_of(tool, options, cfg))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    try:
        sid = sources.create(conn, cfg["media_roots"], tool, target, folder, person, account, options,
                             int(time.time()), table, pin=("script", "session") if _foreign_origin() else ())
    except sources.NotSynced as e:
        return jsonify({"ok": False, "error": str(e), "no_sync": True}), 409
    except sources.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    owner = sources._owner(conn, sources.row(conn, sid), db.accounts(conn))[1]
    _untie_own_links(conn, owner and owner["id"], cfg)
    userdata.changed("sources")
    return jsonify({"ok": True, "source": _source_or_404(sid)})


@app.get("/api/sources/<int:sid>")
def get_source(sid):
    s = sources.get(db.connect(), sid, _sources_active())
    if s is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    return jsonify(_with_session(s))


@app.post("/api/sources/<int:sid>")
def update_source(sid):
    body = _body()
    conn = db.connect()
    s = sources.get(conn, sid)
    if s is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    # "reset": the options it stops setting itself (true: all of them), so
    # they follow its platform's download defaults again; before "options".
    reset = body.get("reset")
    if body.get("pin") not in (None, True, False):
        return jsonify({"ok": False, "error": "pin must be true or false"}), 400
    # Download (#218) is never reset: it has no default to follow (its
    # "platform" is a choice of its own), and a reset must not make a source
    # the user set to never download syncable again.
    resettable = [k for k in sources.OPTION_KEYS if k != "download"]
    if reset is True:
        reset = list(resettable)
    if reset is not None and (not isinstance(reset, list) or any(k not in resettable for k in reset)):
        return jsonify({"ok": False, "error": f"reset must be true or a list of: {', '.join(resettable)}"}), 400
    # "person": the person it is added to (an id), or null for none; alone,
    # or with options (written once they are).
    person = body.get("person")
    if "person" in body and person is not None:
        if not isinstance(person, int) or isinstance(person, bool) or not 0 <= person < 2**53:
            return jsonify({"ok": False, "error": "person must be a person id or null"}), 400
        if not people.exists(conn, person):
            return jsonify({"ok": False, "error": "no such person"}), 400
    if "person" in body and body.get("options") is None and not reset:
        _set_source_person(conn, sid, person)
        return jsonify({"ok": True, "source": _source_or_404(sid)})
    if body.get("options") is None and reset:
        body["options"] = {}
    if not isinstance(body.get("options"), dict):
        return jsonify({"ok": False, "error": f"send options: {{ {', '.join(sources.OPTION_KEYS)} }}"}), 400
    if sid in _sources_active() and (set(body["options"]) | set(reset or ())) - {"schedule"}:
        # The sync's end clears full history and last N: it would clear the new
        # ones. It reads the options again at the end, so a schedule can change.
        return jsonify({"ok": False, "error": "its sync is queued or running; wait for it to end"}), 409
    sent = body["options"]
    if "folder" in body:
        return _give_tool(conn, s, sent, body["folder"])
    if s["tool"] == sources.NONE and sent.get("download") == "on":
        return jsonify({"ok": False, "error": sources.profile_only_error(conn, sources.row(conn, sid)),
                        "needs_tool": True}), 400
    # What it has with those reset: the value it inherits (sources.update
    # then stores none for them, as for any option equal to it).
    base = {**s["options"], **{k: s["defaults"][k] for k in reset or ()}}
    refused = _script_refused(sent, s["tool"]) if sent.get("script") != s["options"]["script"] else None
    if refused:
        return jsonify({"ok": False, "error": refused[0]}), refused[1]
    options, error = sources.parse_options(sent, base=base, tool=s["tool"], platform=s["platform"],
                                           target=s["target"])
    # Last N stays open while a first sync with it has not worked yet.
    if not error and sent.get("first_posts") not in (None, s["options"]["first_posts"]) \
            and sources.synced_ok(sources.row(conn, sid)) and s["options"]["first_posts"] is None:
        error = "only the last posts is for a source's first sync, and this one has synced already"
    error = error or sources.login_refused(options, s["platform"], sync.session_of(s["tool"], options))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    # Only what changes is written: an option sent as it is now (the form
    # sends what it shows) keeps being set or inherited, as it was; one
    # changed is set, even to what it inherits now; one reset is inherited.
    # With "pin" (the dashboard's Options, which sends only the fields the
    # user changed), every option sent is set as sent: a pick equal to a
    # default that moved after the form opened is the user's all the same.
    pinned = sent if body.get("pin") is True else {}
    changed = {k: options[k] for k in sources.OPTION_KEYS if options[k] != base[k] or k in pinned}
    sources.write(conn, sid, changed, [k for k in reset or () if k not in changed])
    if options["schedule"] != s["options"]["schedule"] or options["download"] != s["options"]["download"]:
        scheduler.forget(sid)
    # A new schedule or session: a source the scheduler stopped is tried again.
    if options["schedule"] != s["options"]["schedule"] or options["session"] != s["options"]["session"]:
        sources.resume(conn, sid)
    if "person" in body:
        _set_source_person(conn, sid, person)
    userdata.changed("sources")
    return jsonify({"ok": True, "source": _source_or_404(sid)})


def _give_tool(conn, s, sent, folder):
    """POST /api/sources/<id> with "folder" (a path, or null for the
    default): a profile with no downloader (tool none, #218) becomes a
    source that downloads, with the tool the routing table picks for its
    link, into that folder, and ``sent`` options (download on, or platform
    when its platform is not profile only)."""
    if s["tool"] != sources.NONE:
        return jsonify({"ok": False, "error": "only a profile with no downloader is given a folder; "
                                              "this one has one"}), 400
    if folder is not None and not isinstance(folder, str):
        return jsonify({"ok": False, "error": "folder must be an absolute path inside a media root, or null"}), 400
    cfg = config.load()
    table = sources.routes(cfg)
    try:
        r = sources.resolve(s["target"], table, cfg["media_roots"])
    except sources.Refused as e:
        return jsonify({"ok": False, "error": f"no downloader takes this link: {e}"}), 400
    refused = _script_refused(sent, r["tool"])
    if refused:
        return jsonify({"ok": False, "error": refused[0]}), refused[1]
    base = sources.inherited(sources.new_row(r["tool"], r["platform"], r["target"]), cfg)
    # What it sets itself (a schedule…) stays set, each checked again for
    # the new tool; one it cannot have there goes back to what it inherits.
    for key in s["overrides"]:
        base = sources.clean_options({key: s["options"][key]}, base, tool=r["tool"], platform=r["platform"],
                                     target=r["target"], stories_daily=False) or base
    options, error = sources.parse_options({"download": s["options"]["download"], **sent}, base=base,
                                           tool=r["tool"], platform=r["platform"], target=r["target"])
    if not error and not sources.wants_download(r["platform"], options, cfg):
        error = (f"{r['platform']} is profile only in Settings: send download on"
                 if options["download"] == "platform" else "send download on, or platform")
    error = error or sources.login_refused(options, r["platform"], sync.session_of(r["tool"], options, cfg))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    try:
        sources.give_tool(conn, s["id"], cfg["media_roots"], folder, table, options)
    except sources.NotSynced as e:
        return jsonify({"ok": False, "error": str(e), "no_sync": True}), 409
    except sources.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    scheduler.forget(s["id"])
    userdata.changed("sources")
    return jsonify({"ok": True, "source": _source_or_404(s["id"])})


def _set_source_person(conn, sid, pid):
    """sources.set_person, and what it changes noted: its account, taken
    along, moves between people; one unlinked from a person set to Don't
    sync keeps the switch (as /api/people/<id>/accounts does)."""
    carried = nosync.count_accounts(conn)
    before = people.account_set(conn, pid) if pid is not None else None
    removed = sources.set_person(conn, sid, pid, int(time.time()))
    if removed and nosync.is_on(conn, person=removed[0]):
        nosync.carry(conn, [removed[1]])
    _note_carried(conn, carried)
    _untie_own_links(conn, pid)
    if removed or (pid is not None and people.account_set(conn, pid) != before):
        _people_changed()
    userdata.changed("sources")


@app.post("/api/source-defaults/apply")
def apply_source_default():
    """Settings → Download defaults, "Apply to all sources": every source of
    ``tool`` on ``platform`` that sets ``option`` itself stops doing so and
    follows the default. Not those syncing now (their sync's end writes
    their options): they are counted as skipped."""
    body = _body()
    tool, platform, key = body.get("tool"), body.get("platform"), body.get("option")
    if tool not in sources.TOOLS or not isinstance(platform, str) or key not in sources.DEFAULT_KEYS:
        return jsonify({"ok": False, "error": "send { tool, platform, option }, option one of: "
                                              f"{', '.join(sources.DEFAULT_KEYS)}"}), 400
    conn, cfg, active = db.connect(), config.load(), _sources_active()
    rows = [r for r in conn.execute("SELECT * FROM sources WHERE tool = ? AND platform = ? ORDER BY id",
                                    (tool, platform)).fetchall() if key in sources.overridden(r, cfg)]
    done, skipped = [], []
    for r in rows:
        if r["id"] in active and key != "schedule":
            skipped.append(r["id"])
            continue
        before = sources.stored_options(r, cfg)
        sources.reset(conn, r["id"], [key])
        after = sources.stored_options(sources.row(conn, r["id"]), cfg)
        if before["schedule"] != after["schedule"]:
            scheduler.forget(r["id"])
        if before["schedule"] != after["schedule"] or before["session"] != after["session"]:
            sources.resume(conn, r["id"])
        done.append(r["id"])
    if done:
        userdata.changed("sources")
    return jsonify({"ok": True, "applied": len(done), "skipped": len(skipped)})


@app.post("/api/sources/<int:sid>/rename")
def accept_rename(sid):
    """Accept the new handle the tool reported: the target changes, never
    the folder or its files."""
    body = _body()
    conn = db.connect()
    s = sources.get(conn, sid)
    if s is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    suggestion = s["health"]["rename"]
    if suggestion is None or suggestion["from"] != s["target"].lower():
        return jsonify({"ok": False, "error": "this source has no rename to accept"}), 400
    if body.get("to") != suggestion["to"]:
        return jsonify({"ok": False, "error": f"send the suggested name: {{ to: \"{suggestion['to']}\" }}"}), 400
    if sid in _sources_active():
        return jsonify({"ok": False, "error": "its sync is queued or running; wait for it to end"}), 409
    new = sources.parse_target(s["tool"], suggestion["to"])
    if new is None or new != suggestion["to"]:
        return jsonify({"ok": False, "error": "the suggested name is not a profile name"}), 400
    other = sources.existing(conn, s["tool"], new)
    if other is not None and other != sid:
        return jsonify({"ok": False, "error": f"there is already a {s['tool']} source for {new}"}), 409
    if not sources.rename(conn, sid, s["target"], new, int(time.time())):
        return jsonify({"ok": False, "error": "the source's target changed meanwhile"}), 409
    scheduler.forget(sid)
    userdata.changed("sources")
    userdata.changed("handle_renames")
    return jsonify({"ok": True, "source": _source_or_404(sid)})


@app.delete("/api/sources/<int:sid>/rename")
def dismiss_rename(sid):
    conn = db.connect()
    s = sources.get(conn, sid)
    if s is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    sources.dismiss_rename(conn, sid)
    userdata.changed("sources")
    return jsonify({"ok": True, "source": _source_or_404(sid)})


@app.post("/api/sources/<int:sid>/sync")
def sync_source(sid):
    row = sources.row(db.connect(), sid)
    if row is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    # A sync with a login (cookies, a saved session) only starts from the
    # dashboard: another site could make it fetch as the user's account.
    if _foreign_origin() and sync.session_of(row["tool"], sources.stored_options(row))["mode"] != "none":
        return jsonify({"ok": False, "error": FOREIGN_LOGIN}), 403
    try:
        job = sync.sync(sid, scripts_ok=not _foreign_origin())
    except sync.Busy as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    except sync.NotSynced as e:
        return jsonify({"ok": False, "error": str(e), "no_sync": True}), 409
    except sync.ProfileOnly as e:
        return jsonify({"ok": False, "error": str(e), "profile_only": True}), 409
    except sync.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 403
    except jobs.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    print(f"[jobs] #{job['id']} {job['kind']} queued")
    return jsonify({"ok": True, "job": job})


@app.post("/api/sources/sync-all")
def sync_all_sources():
    queued, skipped, errors, not_synced, only = sync.sync_all(scripts_ok=not _foreign_origin())
    if queued:
        print(f"[jobs] sync all: {len(queued)} queued")
    return jsonify({"ok": True, "jobs": queued, "skipped": skipped, "errors": errors, "not_synced": not_synced,
                    "profile_only": only})


@app.delete("/api/sources/<int:sid>")
def delete_source(sid):
    if sid in _sources_active():
        return jsonify({"ok": False, "error": "its sync is queued or running; cancel it first"}), 409
    if not sources.delete(db.connect(), sid):
        return jsonify({"ok": False, "error": "no such source"}), 404
    scheduler.forget(sid)
    userdata.changed("sources")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Scan and config
# ---------------------------------------------------------------------------

@app.get("/api/scan")
def scan_status():
    return jsonify(scanner.status())


@app.post("/api/scan")
def scan_start():
    if not scanner.start(_roots()):
        return jsonify({"ok": False, "error": "already running"})
    return jsonify({"ok": True})


YOUTUBE_MAX = 24 * 3600


def _public_config(cfg):
    return {"media_roots": cfg["media_roots"], "data_directory": cfg["data_directory"],
            "profile": {"id": cfg["active_profile"], "name": cfg["profile_name"]},
            "version": config.__version__, "tools": cfg.get("tools") or {},
            "instaloader": sync.settings(cfg), "routes": sources.routes(cfg),
            "link_labels": linklabels.rules(cfg),
            "gallery-dl": sync.tool_settings("gallery-dl", cfg), "yt-dlp": sync.tool_settings("yt-dlp", cfg),
            "youtube_max_seconds": yt_dlp.youtube_max_seconds(cfg),
            "check_updates": cfg.get("check_updates") is True,
            "schedules_paused": cfg.get("schedules_paused") is True,
            "desktop_notifications": notify.enabled(cfg),
            "bio_import": biofetch.enabled(cfg),
            # Settings → Profile only (#218): the platforms whose sources
            # are never downloaded unless one says otherwise, and those to pick from.
            "profile_only_platforms": sources.profile_only_platforms(cfg),
            "platforms": sources.known_platforms(db.connect(), cfg),
            # Creators' card borders by rating (config.rating_colors).
            "rating_colors": config.rating_colors(cfg),
            # yt-dlp's metadata mode none embeds with it (sync.yt_dlp_mode).
            "ffmpeg": thumbs.have_ffmpeg(),
            # Settings → Download defaults: one block per tool and platform.
            "source_defaults": sources.default_blocks(db.connect(), cfg)}


@app.get("/api/config")
def get_config():
    return jsonify(_public_config(config.load()))


@app.post("/api/config")
def set_config():
    # Tool paths, roots, the schedules' pause, the link-in-bio switch: the
    # dashboard's alone: _origin_guard refuses another site (instagram.com).
    body = _body()
    # One read-modify-write at a time: two saves at once each keep the other's change.
    with config.editing:
        return _set_config(body)


def _defaults_refused(sent, defaults, cfg):
    """Why download defaults sent (``sent``, cleaned: ``defaults``) cannot
    be, beyond their shape: a script that cannot run for the tool, a kind
    that needs a login the tool's session is not. Else None."""
    for tool, platforms in sent.items():
        for platform in (platforms or {}):
            block = (defaults.get(tool) or {}).get(platform) or {}
            if "script" in block:
                refused = _script_refused(block, tool)
                if refused:
                    return f"{tool} {platform}: {refused[0]}"
            options = sources.clean_options(block, tool=tool, platform=platform, stories_daily=False, defaults=True)
            error = sources.login_refused(options, platform, sync.session_of(tool, options, cfg))
            if error:
                return f"{tool} {platform}: {error}"
    return None


def _set_config(body):
    cfg = config.load()
    tools = roots = insta = None
    changes = {}                               # config key -> new value, saved as they are
    if "slim_metadata" in body:
        # The old switch, which would do nothing now: said, not ignored.
        return jsonify({"ok": False, "error": "slim_metadata is gone: send instaloader: { metadata: "
                                              "\"full\", \"slim\" or \"none\" }"}), 400
    if "routes" in body:
        changes["routes"], error = sources.clean_routes(body["routes"])
        if error:
            return jsonify({"ok": False, "error": error})
    if "link_labels" in body:
        changes["link_labels"], error = linklabels.clean_rules(body["link_labels"])
        if error:
            return jsonify({"ok": False, "error": error})
    for tool in ("gallery-dl", "yt-dlp"):
        if tool in body:
            changes[tool], error = sync.clean_tool_settings(tool, body[tool], sync.tool_settings(tool, cfg))
            if error:
                return jsonify({"ok": False, "error": error})
    if "youtube_max_seconds" in body:
        v = body["youtube_max_seconds"]
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= YOUTUBE_MAX:
            return jsonify({"ok": False, "error": f"youtube_max_seconds must be whole seconds from 1 to {YOUTUBE_MAX}"})
        changes["youtube_max_seconds"] = v
    if "check_updates" in body:
        if not isinstance(body["check_updates"], bool):
            return jsonify({"ok": False, "error": "check_updates must be true or false"})
        changes["check_updates"] = body["check_updates"]
    if "schedules_paused" in body:
        if not isinstance(body["schedules_paused"], bool):
            return jsonify({"ok": False, "error": "schedules_paused must be true or false"})
        changes["schedules_paused"] = body["schedules_paused"]
    if "desktop_notifications" in body:
        if not isinstance(body["desktop_notifications"], bool):
            return jsonify({"ok": False, "error": "desktop_notifications must be true or false"})
        changes["desktop_notifications"] = body["desktop_notifications"]
    if "bio_import" in body:
        if not isinstance(body["bio_import"], bool):
            return jsonify({"ok": False, "error": "bio_import must be true or false"})
        changes["bio_import"] = body["bio_import"]
    if "profile_only_platforms" in body:
        changes["profile_only_platforms"], error = sources.clean_platforms(body["profile_only_platforms"])
        if error:
            return jsonify({"ok": False, "error": error})
        # A source it would make profile only while its sync is queued or
        # running: refused, as update_source and /api/no-sync refuse, so no
        # job downloads from a profile set never to be downloaded.
        listed = {**cfg, "profile_only_platforms": changes["profile_only_platforms"]}
        conn = db.connect()
        for sid in _sources_active():
            r = sources.row(conn, sid)
            if r is not None and not sources.profile_only(r, cfg) and sources.profile_only(r, listed):
                return jsonify({"ok": False, "error": f"a {r['platform']} source's sync is queued or running; "
                                                      "wait for it to end, or stop it"}), 409
    if "rating_colors" in body:
        # null: back to the defaults (the key leaves config.json).
        if body["rating_colors"] is not None:
            changes["rating_colors"], error = config.clean_rating_colors(body["rating_colors"])
            if error:
                return jsonify({"ok": False, "error": error})
        else:
            changes["rating_colors"] = None
    if "source_defaults" in body:
        defaults, error = sources.clean_defaults(body["source_defaults"], cfg.get("source_defaults"))
        error = error or _defaults_refused(body["source_defaults"], defaults, cfg)
        if error:
            return jsonify({"ok": False, "error": error})
        changes["source_defaults"] = defaults
    sessions = {t: sync.session_of(t, {"session": None}, cfg) for t in sync.KINDS if t in body}
    if "tools" in body:                        # checked before anything is saved
        tools, error = config.clean_tools(body["tools"], jobs.TOOLS)
        if error:
            return jsonify({"ok": False, "error": error})
    if "instaloader" in body:
        insta, error = sync.clean_settings(body["instaloader"], sync.settings(cfg))
        if error:
            return jsonify({"ok": False, "error": error})
    if "media_roots" in body:
        roots, error = config.clean_roots(body["media_roots"])
        error = error or _roots_taken(config.read(), roots, cfg["active_profile"])
        if error:
            return jsonify({"ok": False, "error": error})
    if tools is not None:
        # Only the tools sent change; one sent empty is dropped: back to PATH.
        kept = {k: v for k, v in (cfg.get("tools") or {}).items() if k not in body["tools"]}
        cfg["tools"] = {**kept, **tools}
    changed = roots is not None and roots != cfg["media_roots"]
    if roots is not None:
        cfg["media_roots"] = roots
    if insta is not None:
        cfg["instaloader"] = insta
    before = dict(cfg)
    cfg.update(changes)
    if "rating_colors" in changes and changes["rating_colors"] is None:
        del cfg["rating_colors"]
    if tools is not None or roots is not None or insta is not None or changes:
        # The old switch (slim_metadata) is instaloader's metadata mode now.
        if "slim_metadata" in cfg:
            cfg["instaloader"] = {**(cfg.get("instaloader") or {}), "metadata": sync.settings(cfg)["metadata"]}
            del cfg["slim_metadata"]
        config.save(cfg)
    # A new tool-level session: the sources using it that the scheduler stopped are tried again.
    resumed = [sources.resume_tool(db.connect(), t) for t, was in sessions.items()
               if was != sync.session_of(t, {"session": None}, cfg)]
    if "profile_only_platforms" in changes:
        # A source that stops or starts downloading: its schedule's note goes.
        for (sid,) in db.connect().execute("SELECT id FROM sources").fetchall():
            scheduler.forget(sid)
    if "source_defaults" in changes:
        # As for a source's own: a schedule or session it inherits that
        # changed, and the scheduler tries a source it stopped again.
        conn = db.connect()
        for r in conn.execute("SELECT * FROM sources").fetchall():
            old, new = sources.stored_options(r, before), sources.stored_options(r, cfg)
            if old["schedule"] != new["schedule"]:
                scheduler.forget(r["id"])
            if old["schedule"] != new["schedule"] or old["session"] != new["session"]:
                sources.resume(conn, r["id"])
                resumed.append([r["id"]])
    if any(resumed):
        userdata.changed("sources")
    if changed:
        scanner.start(roots)
    return jsonify({"ok": True, "config": _public_config(cfg)})


# ---------------------------------------------------------------------------
# Profiles: separate libraries, one active at a time (config.py)
# ---------------------------------------------------------------------------

_BAD_PROFILE_NAME = f"name must be 1 to {config.MAX_PROFILE_NAME} characters"


def _profile_name(value):
    """A profile's name as kept (spaces collapsed), or None."""
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > config.MAX_PROFILE_NAME or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None
    return name


def _name_taken(raw, name, exclude=None):
    return any(p["id"] != exclude and config.profile_name(p).casefold() == name.casefold() for p in raw["profiles"])


def _roots_taken(raw, roots, exclude=None):
    """Why ``roots`` cannot be a profile's (another one, not ``exclude``,
    has one of them, or one around or inside it), else None."""
    owner = config.roots_owner(raw, roots, exclude)
    if owner is None:
        return None
    name, theirs, ours = owner
    if os.path.realpath(theirs) == os.path.realpath(ours):
        return f"{ours} is a media root of the profile \"{name}\""
    return f"{ours} overlaps {theirs}, a media root of the profile \"{name}\""


def _profile_counts(data_dir, live=None):
    """(posts, people) in a profile's database, read-only: 0 when it has
    none yet, None for what cannot be read (a database from before the
    table, an unmounted disk). ``live``: the open connection, for the
    active profile."""
    path = os.path.join(data_dir, "feedvault.db")
    if live is None and not os.path.isfile(path):
        return 0, 0
    conn, out = None, []
    try:
        conn = live or sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        for table in ("posts", "people"):
            try:
                out.append(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                out.append(None)
        return tuple(out)
    except (sqlite3.Error, OSError):
        return None, None
    finally:
        if conn is not None and live is None:
            conn.close()


def _public_profile(p, active_id):
    posts, persons = _profile_counts(config.profile_data(p), db.connect() if p["id"] == active_id else None)
    return {"id": p["id"], "name": config.profile_name(p), "data_directory": config.profile_data(p),
            "media_roots": config.profile_roots(p), "active": p["id"] == active_id,
            "posts": posts, "people": persons}


def _profile_list(raw):
    active = config.active(raw)["id"]
    return {"active": active, "profiles": [_public_profile(p, active) for p in raw["profiles"]]}


@app.get("/api/profiles")
def list_profiles():
    return jsonify(_profile_list(config.read()))


@app.post("/api/profiles")
def create_profile():
    body = _body()
    with config.editing:
        return _create_profile(body)


def _create_profile(body):
    name = _profile_name(body.get("name"))
    if name is None:
        return jsonify({"ok": False, "error": _BAD_PROFILE_NAME}), 400
    data = body.get("data_directory")
    if not isinstance(data, str) or not data.strip() or "\x00" in data:
        return jsonify({"ok": False, "error": "data_directory must be a folder's path"}), 400
    data = os.path.abspath(os.path.expanduser(data.strip()))
    if os.path.lexists(data) and not os.path.isdir(data):
        return jsonify({"ok": False, "error": f"not a folder: {data}"}), 400
    roots, error = config.clean_roots(body.get("media_roots", []))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    raw = config.read()
    if _name_taken(raw, name):
        return jsonify({"ok": False, "error": "a profile with that name exists"}), 400
    owner = config.data_dir_owner(raw, data)
    if owner:
        return jsonify({"ok": False, "error": f"the profile \"{owner}\" already uses that data directory"}), 400
    error = _roots_taken(raw, roots)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    try:
        config.make_private_dir(data)
    except OSError as e:
        return jsonify({"ok": False, "error": f"cannot create {data}: {e.strerror or e}"}), 400
    ids = {p["id"] for p in raw["profiles"]}
    pid = next(i for i in iter(lambda: os.urandom(4).hex(), None) if i not in ids)
    raw["profiles"].append({"id": pid, "name": name, "data_directory": data, "media_roots": roots})
    config.write(raw)
    print(f"[profiles] {name} created ({data})")
    out = _profile_list(raw)
    return jsonify({"ok": True, "profile": next(p for p in out["profiles"] if p["id"] == pid), **out})


@app.post("/api/profiles/<pid>")
def rename_profile(pid):
    name = _profile_name(_body().get("name"))
    if name is None:
        return jsonify({"ok": False, "error": _BAD_PROFILE_NAME}), 400
    with config.editing:
        raw = config.read()
        profile = next((p for p in raw["profiles"] if p["id"] == pid), None)
        if profile is None:
            return jsonify({"ok": False, "error": "no such profile"}), 404
        if _name_taken(raw, name, exclude=pid):
            return jsonify({"ok": False, "error": "a profile with that name exists"}), 400
        profile["name"] = name
        config.write(raw)
        return jsonify({"ok": True, **_profile_list(raw)})


@app.delete("/api/profiles/<pid>")
def delete_profile(pid):
    """Forget a profile. Its data directory and its media stay on disk,
    untouched: a profile made again on that data directory has it all back."""
    with config.editing:
        raw = config.read()
        profile = next((p for p in raw["profiles"] if p["id"] == pid), None)
        if profile is None:
            return jsonify({"ok": False, "error": "no such profile"}), 404
        if len(raw["profiles"]) == 1:
            return jsonify({"ok": False, "error": "the last profile cannot be removed"}), 400
        if config.active(raw)["id"] == pid:
            return jsonify({"ok": False, "error": "switch to another profile before removing this one"}), 400
        raw["profiles"].remove(profile)
        config.write(raw)
        print(f"[profiles] {config.profile_name(profile)} removed (its files are kept)")
        return jsonify({"ok": True, **_profile_list(raw)})


def _open_library(cfg):
    """Point the process at ``cfg``'s library (the active profile's), as a
    start does: its database (made or migrated), the user data read back
    into its empty tables, the jobs and syncs a stopped FeedVault left."""
    db.init(config.db_path(cfg))
    userdata.restore_all(db.connect(), cfg["data_directory"])
    news.ensure(db.connect())
    jobs.recover()
    sync.resume()


def _watch_library(cfg):
    """After _open_library: the scan, and the caches filled in the background."""
    scanner.start(cfg["media_roots"])
    archives.warm(db.connect())
    trash.warm(cfg["media_roots"])


def _switch_refused():
    """Why the library cannot be switched now, else None. What a job
    downloads, and what a scan reads, belongs to the library it started in."""
    n = len(jobs.active())
    if n:
        return (f"{n} job{' is' if n == 1 else 's are'} queued or running; "
                "wait for them to end, or cancel them on the Jobs page")
    if scanner.status()["running"]:
        return "a scan is running; try again when it is done"
    return None


@app.post("/api/profiles/<pid>/activate")
def activate_profile(pid):
    """Switch to another profile: 409 with ``busy`` while a job is queued or
    running or a scan runs (nothing started in one library may write into
    another). The hashing pass is stopped instead (it starts again in the
    new library, and reads what it had left in this one when it is back)."""
    try:
        with library.alone(), config.editing:
            return _activate_profile(pid)
    except library.Busy as e:
        return jsonify({"ok": False, "error": str(e), "busy": True}), 409


def _activate_profile(pid):
    raw = config.read()
    if not any(p["id"] == pid for p in raw["profiles"]):
        return jsonify({"ok": False, "error": "no such profile"}), 404
    previous = config.active(raw)["id"]
    if pid == previous:
        return jsonify({"ok": True, **_profile_list(raw)})
    refused = _switch_refused()
    if refused:
        return jsonify({"ok": False, "error": refused, "busy": True}), 409
    if not scanner.claim():                    # claimed: no scan starts until the new library is open
        return jsonify({"ok": False, "error": "a scan is running; try again when it is done", "busy": True}), 409
    try:
        with hashing.paused():
            userdata.flush()                   # the changes not written yet, into the library they are for
            raw["active_profile"] = pid
            config.write(raw)
            try:
                _open_library(config.load())
            except Exception as e:             # an unmounted disk, a newer database: stay on the one that works
                raw["active_profile"] = previous
                config.write(raw)
                try:
                    _open_library(config.load())
                except Exception as again:
                    print(f"[profiles] cannot open {previous} again: {type(again).__name__}: {again}")
                return jsonify({"ok": False, "error": f"cannot open that profile: {e}"}), 500
            scheduler.forget_all()
            sync.forget_batch()
    except TimeoutError as e:
        return jsonify({"ok": False, "error": f"{e}; try again in a moment", "busy": True}), 409
    finally:
        scanner.release(forget=config.load()["active_profile"] != previous)
    cfg = config.load()
    _watch_library(cfg)
    print(f"[profiles] switched to {cfg['profile_name']} ({config.db_path(cfg)})")
    return jsonify({"ok": True, **_profile_list(config.read())})


@app.get("/api/downloaders")
def get_downloaders():
    return jsonify(downloaders.status())


@app.get("/api/browsers/profiles")
def browser_profiles():
    """The profiles a cookies session can name, per browser: names only,
    read from Firefox's profiles.ini and the other browsers' folder names."""
    return jsonify({"profiles": browsers.listing()})


@app.post("/api/downloaders/check")
def check_downloaders():
    """Find every tool again (Check again)."""
    return jsonify({"ok": True, **downloaders.status(refresh=True)})


@app.post("/api/yt-dlp/info-json-cookies")
def clean_info_json_cookies():
    """Take the cookies out of every yt-dlp info JSON under the media roots
    (info_cookies.py); ``apply`` false (the default) only counts them."""
    body = _body()
    apply = body.get("apply", False)
    if not isinstance(apply, bool):
        return jsonify({"ok": False, "error": "apply must be true or false"}), 400
    # A yt-dlp sync, a yt-dlp source's script sync or a script running yt-dlp: all in its lock group.
    if any(j["group"] == "yt-dlp" and j["state"] == "running" for j in jobs.active()):
        return jsonify({"ok": False, "error": "yt-dlp is running (a sync or a script); try again once it ends"}), 409
    try:
        r = info_cookies.sweep(_roots(), apply)
    except info_cookies.Busy as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    if apply:
        print(f"[cookies] removed from {r['files']} info JSON(s), {r['failures']} failed")
    return jsonify({"ok": True, "applied": apply, **r})


# ---------------------------------------------------------------------------
# Jobs (jobs.py: started by kind, never from a command in the request)
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs():
    # The sidebar's "New" and Unsorted links counts ride along with the poll (news.py, links.py).
    new, new_until = news.count(db.connect())
    unread, latest = notify.unread(db.connect())
    # A tab that shows desktop notifications itself says so: notify-send waits.
    if request.args.get("desktop") == "1":
        notify.tab_shows()
    return jsonify({**jobs.listing(), "sync_all": sync.batch(), "new": new, "new_until": new_until,
                    "unsorted_links": links.unsorted(db.connect()),
                    "notifications": {"unread": unread, "latest": latest, "desktop": notify.enabled()}})


@app.get("/api/jobs/kinds")
def job_kinds():
    return jsonify(jobs.kinds())


@app.post("/api/jobs")
def start_job():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("kind"), str):
        return jsonify({"ok": False, "error": "send { kind, params }"}), 400
    if body.get("kind") in save.KINDS:         # their checks are POST /api/save's
        return jsonify({"ok": False, "error": "start it with POST /api/save"}), 400
    if body.get("kind") in scripts.JOB_KINDS:  # the origin check is theirs
        return jsonify({"ok": False, "error": "start it with POST /api/scripts/<id>/run, or a source's Sync"}), 400
    try:
        job = sync.submit(body.get("kind"), body.get("params"))
    except jobs.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    print(f"[jobs] #{job['id']} {job['kind']} queued")
    return jsonify({"ok": True, "job": job})


@app.get("/api/jobs/<int:job_id>")
def get_job(job_id):
    job = jobs.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "no such job"}), 404
    return jsonify(job)


@app.get("/api/jobs/<int:job_id>/log")
def job_log(job_id):
    log = jobs.log(job_id, _int_arg("after", 0, 0, 2**53))
    if log is None:
        return jsonify({"ok": False, "error": "no such job"}), 404
    return jsonify(log)


@app.post("/api/jobs/<int:job_id>/cancel")
def cancel_job(job_id):
    try:
        job = jobs.cancel(job_id)
    except jobs.TooLate as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    if job is None:
        if jobs.get(job_id) is None:
            return jsonify({"ok": False, "error": "no such job"}), 404
        return jsonify({"ok": False, "error": "the job has already ended"}), 409
    print(f"[jobs] #{job_id} cancelled")
    return jsonify({"ok": True, "job": job})


# ---------------------------------------------------------------------------
# Scripts (scripts.py: files on disk, listed and run; nothing here writes one)
# ---------------------------------------------------------------------------

FOREIGN = "scripts can only be run from FeedVault's own dashboard"


# Another site never reaches these (_origin_guard's FOREIGN_ALLOWED).
@app.get("/api/scripts")
def list_scripts():
    return jsonify(scripts.listing())


@app.get("/api/scripts/<sid>")
def get_script(sid):
    s, refused = scripts.lookup(sid, content=True)
    if s is None:
        # A refused folder lists nothing: its reason, not "no such script".
        return jsonify({"ok": False, "error": scripts.missing(sid, refused) if refused else "no such script"}), 404
    return jsonify(s)


@app.post("/api/scripts/<sid>/run")
def run_script(sid):
    """A script by id, with its inputs: never a command, a path or its text."""
    body = request.get_json(silent=True)
    body = {} if body is None else body
    if not isinstance(body, dict) or set(body) - set(scripts.INPUTS) \
            or not all(v is None or isinstance(v, str) for v in body.values()):
        return jsonify({"ok": False, "error": "send { target, url, folder }, each text or left out"}), 400
    try:
        job = scripts.run(sid, body)
    except LookupError:
        return jsonify({"ok": False, "error": "no such script"}), 404
    except jobs.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    print(f"[jobs] #{job['id']} script {sid} queued")
    return jsonify({"ok": True, "job": job})


@app.get("/api/browse")
def browse():
    if not shutil.which("zenity"):
        return jsonify({"ok": False, "error": "zenity is not installed", "path": None})
    try:
        out = subprocess.run(
            ["zenity", "--file-selection", "--directory", "--title=Select a media folder"],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"path": None})
    path = out.stdout.strip() if out.returncode == 0 else ""
    return jsonify({"path": path or None})


@app.post("/api/quit")
def quit_app():
    def stop():
        scheduler.stop()                       # os._exit skips atexit
        jobs.shutdown()
        userdata.flush()
        os._exit(0)

    threading.Timer(0.3, stop).start()
    print("[api] Shutdown requested from the dashboard")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Media
#
# Only paths recorded by the scanner are ever served; the URL carries a row id,
# never a path.
# ---------------------------------------------------------------------------

# Image types by their first bytes, for files whose name does not say
# (yt-dlp keeps TikTok's thumbnails as ".image").
_MAGIC = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"), (b"RIFF", "image/webp"), (b"GIF8", "image/gif"))


def _sniff(path):
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return None
    for magic, mimetype in _MAGIC:
        if head.startswith(magic) and (mimetype != "image/webp" or head[8:12] == b"WEBP"):
            return mimetype
    return None


# The only types a file is served as, by its own name: never what a JSON
# beside it says (a gallery-dl item is typed by its "extension"), so a page
# saved as x.html is never run as one on this origin. Anything else is a
# download.
_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif",
    "heic": "image/heic", "avif": "image/avif",
    "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "mkv": "video/x-matroska",
    "m4a": "audio/mp4", "mp3": "audio/mpeg", "opus": "audio/ogg", "ogg": "audio/ogg", "oga": "audio/ogg",
    "wav": "audio/wav", "flac": "audio/flac", "aac": "audio/aac",
}


def _type_of(path):
    return _TYPES.get(os.path.splitext(path)[1][1:].lower())


def _send(path, sniff=False, media=True, roots=None):
    """Serve a file, from what it is opened as once. ``media``: a path the
    scanner recorded, which must be inside a media root and outside its
    trash once opened (/proc/self/fd/N: a symlink there that leads
    elsewhere, or a file swapped for one, is never served; scanner.in_roots
    of ``roots``, else config's); False for FeedVault's own files (a cached
    thumbnail, a trash thumbnail checked by trash.thumb)."""
    if not path:
        abort(404)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        abort(404)
    try:
        st, opened = os.fstat(fd), f"/proc/self/fd/{fd}"
        if not stat.S_ISREG(st.st_mode) \
                or media and not scanner.in_roots(os.readlink(opened), _roots() if roots is None else roots):
            abort(404)
        mimetype = _sniff(opened) if sniff and path.endswith(".image") else _type_of(path)
        # Read through the fd, named and tagged as the file (Werkzeug's etag for its path).
        resp = send_file(opened, mimetype=mimetype or "application/octet-stream", as_attachment=mimetype is None,
                         download_name=os.path.basename(path), conditional=True, max_age=3600,
                         etag=f"{st.st_mtime}-{st.st_size}-{adler32(path.encode()) & 0xFFFFFFFF}")
    finally:
        os.close(fd)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return resp


@app.get("/media/<int:media_id>")
def serve_media(media_id):
    row = db.media_row(db.connect(), media_id)
    return _send(row["path"] if row else None)


def _sources_in_roots(row, roots):
    """Whether what a thumbnail is made from (the file, its poster) resolves
    inside a media root and outside its trash: never one of a file elsewhere."""
    return all(scanner.in_roots(p, roots) for p in (row["path"], row["poster_path"]) if p)


@app.get("/media/<int:media_id>/thumb")
def serve_thumb(media_id):
    """Small JPEG for grids. Falls back to the original image if one cannot be made."""
    row = db.media_row(db.connect(), media_id)
    cfg = config.load()
    if row is None or not _sources_in_roots(row, cfg["media_roots"]):
        abort(404)
    path = thumbs.thumb_for(cfg["data_directory"], row)
    if path:
        return _send(path, media=False)
    if row["kind"] == "image":
        return _send(row["path"], roots=cfg["media_roots"])
    abort(404)


@app.get("/media/<int:media_id>/poster")
def serve_poster(media_id):
    row = db.media_row(db.connect(), media_id)
    return _send(row["poster_path"] if row else None, sniff=True)


@app.get("/media/copy/<int:copy_id>/thumb")
def serve_copy_thumb(copy_id):
    """Thumbnail of an extra copy's first item: only paths the scanner
    recorded for that copy are read, as for /media."""
    copy = db.copy_row(db.connect(), copy_id)
    if copy is None or not copy["media"]:
        abort(404)
    first = min(copy["media"], key=lambda m: m["idx"])
    cfg = config.load()
    if not _sources_in_roots(first, cfg["media_roots"]):
        abort(404)
    path = thumbs.thumb_for(cfg["data_directory"], first)
    if path:
        return _send(path, media=False)
    if first["kind"] == "image":
        return _send(first["path"], roots=cfg["media_roots"])
    abort(404)


@app.get("/media/avatars/<file>")
def serve_avatar(file):
    """A person's picture (avatars.py), from FeedVault's own files. Its name
    is a hash of it: a new picture is a new URL, so it is cached for good."""
    found = avatars.file_name(file)
    if found is None:
        abort(404)
    base, (name, kind) = config.load()["data_directory"], found
    target = avatars.path(base, name, kind)
    # A picture saved before portraits has no card file: its large one, a square, stands in.
    if kind == "card" and not os.path.isfile(target):
        target = avatars.path(base, name)
    resp = _send(target, media=False)
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return resp


@app.get("/media/people/<int:pid>/profile-pic/<key>")
def serve_profile_pic(pid, key):
    """One of the profile pictures avatar_choices offers, to show it: only a
    file found for that person, inside a media root."""
    conn, roots = db.connect(), _roots()
    if not people.exists(conn, pid):
        abort(404)
    found = next((f["path"] for f in avatars.profile_pics(conn, pid, roots) if f["key"] == key), None)
    return _send(found, roots=roots)


@app.get("/trash/<key>/thumb")
def serve_trash_thumb(key):
    """Thumbnail of a trashed entry. The key is opaque; trash.thumb only reads
    files listed in a manifest that resolve inside their trash folder."""
    if len(key) != 20 or not all(c in "0123456789abcdef" for c in key):
        abort(404)
    cfg = config.load()
    return _send(trash.thumb(cfg["media_roots"], key, cfg["data_directory"]), media=False)


# The script as written talks to the default port. Served, it names this
# instance's port instead, in exactly these three places, so a copy
# installed from another port (the demo, a changed FEEDVAULT_PORT) talks to
# the server it came from. The port is the one FeedVault listens on, never
# anything from the request: a Host header cannot choose where an installed
# script sends its requests. The host stays localhost, and @connect and
# @match are left as written.
USERSCRIPT_ORIGIN = "http://localhost:3380"
USERSCRIPT_LINES = ("// @updateURL    ", "// @downloadURL  ", 'const API_BASE = "')


def _userscript_text(port):
    folder = config.bundled("userscript")
    try:
        with open(os.path.join(folder, "feedvault.user.js"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        abort(404)
    origin = f"http://localhost:{int(port)}"
    for line in USERSCRIPT_LINES:
        text = text.replace(line + USERSCRIPT_ORIGIN, line + origin, 1)
    return text


@app.get("/userscript/feedvault.user.js")
def serve_userscript():
    resp = Response(_userscript_text(config.PORT), mimetype="text/javascript",
                    headers={"Cache-Control": "no-cache"})
    # An update check with the ETag it has gets a 304, as from a file.
    resp.add_etag()
    return resp.make_conditional(request)


# ---------------------------------------------------------------------------
# Dashboard (single-page app)
# ---------------------------------------------------------------------------

@app.get("/", defaults={"path": ""})
@app.get("/<path:path>")
def spa(path):
    if path.startswith(("api/", "media/", "userscript/")):
        abort(404)
    static = config.static_dir()
    if static is None:
        return ("FeedVault backend is running, but the dashboard is not built. "
                "Run ./run.sh --build.", 200, {"Content-Type": "text/plain"})
    # Joined safely before anything is looked up: a path out of the folder
    # (%2f is not resolved by the browser) is the dashboard, whatever is there.
    file = safe_join(static, path) if path else None
    if file and os.path.isfile(file):
        return send_from_directory(static, path)
    return send_from_directory(static, "index.html")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main():
    if "--version" in sys.argv:
        print(f"FeedVault {config.__version__}")
        return
    cfg = config.load()
    url = f"http://localhost:{config.PORT}"
    # Before touching the database: a running instance must not see it migrated.
    if _port_busy(config.PORT):
        print(f"[api] Port {config.PORT} already in use — FeedVault may already be running.")
        sys.exit(1)
    # A umask of 002 (or an older FeedVault) left them group-writable: scripts would be refused.
    scripts.tighten()
    try:
        _open_library(cfg)
    except db.SchemaTooNew as e:
        print(f"[db] {e}")
        sys.exit(1)
    scheduler.start()
    # Ctrl+C and SIGTERM still write the last few seconds of user data, after
    # stopping the scheduler, then running jobs (atexit runs the last
    # registered first).
    atexit.register(userdata.flush)
    atexit.register(jobs.shutdown)
    atexit.register(scheduler.stop)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    _watch_library(cfg)
    if "--no-browser" not in sys.argv and os.environ.get("FEEDVAULT_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"[api] FeedVault {config.__version__}")
    print(f"[api] Dashboard → {url}")
    print(f"[api] Config      {config.config_path()}")
    if len(config.read()["profiles"]) > 1:
        print(f"[api] Profile     {cfg['profile_name']}")
    print(f"[api] Database    {config.db_path(cfg)}")
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
