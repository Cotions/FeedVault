"""FeedVault backend: a catalog of downloaded social posts.

Run from source with ./run.sh, or directly: python backend/app.py
"""
import atexit
import os
import stat
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory
from werkzeug.security import safe_join
from zlib import adler32

import archives
import biofetch
import config
import db
import downloaders
import duplicates
import hashing
import info_cookies
import jobs
import news
import notify
import organize
import people
import save
import save_tools
import scanner
import scheduler
import scripts
import sources
import sync
import thumbs
import trash
import userdata
from parsers import yt_dlp

app = Flask(__name__, static_folder=None)

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
# ---------------------------------------------------------------------------

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
CSRF_HEADER = "X-FeedVault"


def _host_only(host_header):
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


@app.before_request
def _origin_guard():
    if _host_only(request.headers.get("Host")) not in ALLOWED_HOSTS:
        return jsonify({"ok": False, "error": "forbidden host"}), 403
    if request.path.startswith("/api/") and request.method != "OPTIONS" \
            and not request.headers.get(CSRF_HEADER):
        return jsonify({"ok": False, "error": f"missing {CSRF_HEADER} header"}), 403
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


@app.after_request
def _no_frames(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    csp = resp.headers.get("Content-Security-Policy")
    resp.headers["Content-Security-Policy"] = f"{csp}; {NO_FRAMES}" if csp else NO_FRAMES
    return resp


def _roots():
    return config.load()["media_roots"]


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
    return jsonify(db.unmatched(db.connect()))


@app.post("/api/saved")
def saved():
    body = request.get_json(silent=True) or {}
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
    body = request.get_json(silent=True) or {}
    posts = body.get("posts") or []
    media = body.get("media") or []
    if not isinstance(posts, list) or not all(isinstance(p, str) for p in posts) \
            or not isinstance(media, list) or not all(isinstance(m, int) and not isinstance(m, bool) for m in media):
        return jsonify({"ok": False, "error": "posts must be a list of ids, media a list of numbers"}), 400
    if not posts and not media:
        return jsonify({"ok": False, "error": "nothing to delete"}), 400
    cfg = config.load()
    report = trash.delete(posts[:5000], media[:5000], cfg["media_roots"], cfg["data_directory"])
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
    body = request.get_json(silent=True) or {}
    posts, keys = body.get("posts"), body.get("keys")
    if posts is not None and not _str_list(posts) or keys is not None and not _str_list(keys) \
            or posts is None and keys is None:
        return jsonify({"ok": False, "error": "posts or keys must be a non-empty list of ids"}), 400
    cfg = config.load()
    return jsonify(trash.restore((posts or [])[:500], cfg["media_roots"], cfg["data_directory"],
                                 keys=(keys or [])[:5000]))


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
    body = request.get_json(silent=True) or {}
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


def _choice(c):
    return isinstance(c, dict) and isinstance(c.get("group"), str) and isinstance(c.get("keep"), str)


@app.post("/api/duplicates/resolve")
def duplicates_resolve():
    body = request.get_json(silent=True) or {}
    choices = body.get("groups") if "groups" in body else [body]
    if not isinstance(choices, list) or not choices or len(choices) > 500 or not all(map(_choice, choices)):
        return jsonify({"ok": False, "error": "send { group, keep } or groups: [{ group, keep }] (at most 500)"}), 400
    threshold = _threshold(body.get("threshold"))
    if threshold is None:
        return jsonify({"ok": False, "error": THRESHOLD_ERROR}), 400
    cfg = config.load()
    report = duplicates.resolve([(c["group"], c["keep"]) for c in choices],
                                cfg["media_roots"], cfg["data_directory"], threshold=threshold)
    if report.get("posts"):
        userdata.changed("decisions")          # a trashed post takes its decision along, or hands it on
    for name in report.pop("carried", []):
        userdata.changed(name)
    if report.get("posts") or report.get("copies"):
        print(f"[duplicates] resolved {len(report['resolved'])} groups: {len(report['posts'])} posts, "
              f"{len(report['copies'])} copies, {report['files']} files → trash")
    return jsonify(report)


@app.post("/api/duplicates/dismiss")
def duplicates_dismiss():
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("group"), str):
        return jsonify({"ok": False, "error": "group must be a group id"}), 400
    threshold = _threshold(body.get("threshold"))
    if threshold is None:
        return jsonify({"ok": False, "error": THRESHOLD_ERROR}), 400
    if not duplicates.dismiss(db.connect(), body["group"], threshold):
        return jsonify({"ok": False, "error": "no such group; reload"}), 404
    userdata.changed("dismissed_duplicates")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Review decisions
# ---------------------------------------------------------------------------

@app.post("/api/review")
def review():
    body = request.get_json(silent=True) or {}
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
    body = request.get_json(silent=True) or {}
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
    body = request.get_json(silent=True) or {}
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
    body = request.get_json(silent=True) or {}
    name = organize.clean_name(body.get("name"))
    n = organize.delete(db.connect(), name) if name else None
    if n is None:
        return jsonify({"ok": False, "error": "no such tag"}), 404
    userdata.changed("tags")
    userdata.changed("post_tags")
    return jsonify({"ok": True, "posts": n})


@app.post("/api/tags/color")
def tags_color():
    body = request.get_json(silent=True) or {}
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
    names = (request.get_json(silent=True) or {}).get("names")
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
    name = organize.clean_name((request.get_json(silent=True) or {}).get("name"))
    if name is None:
        return jsonify({"ok": False, "error": _BAD_NAME}), 400
    c = organize.create_collection(db.connect(), name, int(time.time()))
    if c is None:
        return jsonify({"ok": False, "error": "a collection with that name exists"}), 400
    userdata.changed("collections")
    return jsonify({"ok": True, "collection": c})


@app.post("/api/collections/reorder")
def reorder_collections():
    ids = (request.get_json(silent=True) or {}).get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 5000 \
            or any(not isinstance(i, int) or isinstance(i, bool) for i in ids):
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
    conn = db.connect()
    if action not in ("rename", "delete", "add", "remove", "order", "cover"):
        abort(404)
    if conn.execute("SELECT 1 FROM collections WHERE id = ?", (cid,)).fetchone() is None:
        return jsonify({"ok": False, "error": "no such collection"}), 404
    body = request.get_json(silent=True) or {}
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
    sources and mutes are exported by person name)."""
    if names:
        userdata.changed("people")
        userdata.changed("sources")
        userdata.changed("muted_people")
    userdata.changed("person_accounts")


@app.get("/api/people")
def list_people():
    return jsonify(people.people(db.connect()))


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
        if sources.existing(conn, r["tool"], r["target"], folder) is not None:
            raise sources.Refused(f"{text[:200]}: there is already a source for it; link its account instead")
        out.append(r)
    return out, table


@app.post("/api/people")
def create_person():
    """A person, with accounts already indexed and/or profile links: each
    link becomes a source of theirs (nothing is downloaded until a sync)."""
    body = request.get_json(silent=True) or {}
    name, accounts = people.clean_name(body.get("name")), people.clean_accounts(body.get("accounts"))
    if name is None:
        return jsonify({"ok": False, "error": _BAD_PERSON_NAME}), 400
    if accounts is None:
        return jsonify({"ok": False, "error": _BAD_ACCOUNTS}), 400
    conn, cfg, now = db.connect(), config.load(), int(time.time())
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
                options = sources.clean_options(None, tool=r["tool"], platform=r["platform"], target=r["target"])
                made.append(sources.create(conn, cfg["media_roots"], r["tool"], r["target"], None, p["id"], None,
                                           options, now, table))
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
    _people_changed(names=True)
    if made:
        userdata.changed("sources")
    return jsonify({"ok": True, "person": p, "sources": [_source_or_404(sid) for sid in made]})


@app.post("/api/people/merge")
def merge_people():
    body = request.get_json(silent=True) or {}
    ids, accounts = body.get("ids"), people.clean_accounts(body.get("accounts"))
    name = None if body.get("name") is None else people.clean_name(body.get("name"))
    if not isinstance(ids, list) or not ids or len(ids) > people.MAX_ACCOUNTS \
            or not all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i < 2**53 for i in ids):
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
    muted = any(news.is_muted(conn, person=i) for i in ids)
    try:
        p = people.merge(conn, ids, name, accounts, int(time.time()))
    except people.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if muted:                                  # a muted one's accounts stay muted in the one they join
        news.mute(conn, True, person=p["id"])
    _people_changed(names=True)
    userdata.changed("sources")                # the others' sources moved to the first
    return jsonify({"ok": True, "person": p})


@app.get("/api/people/suggestions")
def people_suggestions():
    return jsonify(people.suggestions(db.connect()))


@app.post("/api/people/suggestions/dismiss")
def dismiss_suggestion():
    body = request.get_json(silent=True) or {}
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
    queued, skipped, errors = sync.sync_all(only=set(ids), scripts_ok=not _foreign_origin())
    if queued:
        print(f"[jobs] sync person {pid}: {len(queued)} queued")
    return jsonify({"ok": True, "sources": len(ids), "jobs": queued, "skipped": skipped, "errors": errors})


@app.get("/api/people/<int:pid>")
def get_person(pid):
    conn = db.connect()
    p = people.person(conn, pid) if people.exists(conn, pid) else None
    if p is None:
        return jsonify({"ok": False, "error": "no such person"}), 404
    return jsonify(p)


@app.post("/api/people/<int:pid>")
def update_person(pid):
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    body = request.get_json(silent=True) or {}
    name = None if body.get("name") is None else people.clean_name(body["name"])
    notes = body.get("notes")
    if body.get("name") is not None and name is None:
        return jsonify({"ok": False, "error": _BAD_PERSON_NAME}), 400
    if notes is not None and (not isinstance(notes, str) or len(notes) > people.MAX_NOTES):
        return jsonify({"ok": False, "error": f"notes must be text of at most {people.MAX_NOTES} characters"}), 400
    if name is None and notes is None:
        return jsonify({"ok": False, "error": "send name or notes"}), 400
    try:
        p = people.update(conn, pid, name, notes)
    except people.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    _people_changed(names=name is not None)
    if notes is not None:
        userdata.changed("people")
    return jsonify({"ok": True, "person": p})


@app.delete("/api/people/<int:pid>")
def delete_person(pid):
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    n = people.delete(conn, pid)
    _people_changed(names=True)
    return jsonify({"ok": True, "unlinked": n})


@app.post("/api/people/<int:pid>/accounts")
def person_accounts(pid):
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    body = request.get_json(silent=True) or {}
    add, remove = people.clean_accounts(body.get("add")), people.clean_accounts(body.get("remove"))
    if add is None or remove is None or not (add or remove):
        return jsonify({"ok": False, "error": "add or remove must be a list of { platform, id }"}), 400
    try:
        r = people.link(conn, pid, add, remove, int(time.time()))
    except people.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if r["added"] or r["removed"]:
        _people_changed()
    return jsonify({"ok": True, **r})


BIO_OFF = "link-in-bio import is off: turn it on in Settings → Downloads"


@app.post("/api/people/<int:pid>/bio-import")
def bio_import(pid):
    """The accounts a person's link-in-bio page lists (biofetch.py): the one
    page fetched, nothing added. The dashboard adds each one the user picks
    through /api/people/<id>/accounts or /api/sources."""
    if _foreign_origin():
        return jsonify({"ok": False, "error": "a link-in-bio page can only be imported from FeedVault's own "
                                              "dashboard"}), 403
    conn = db.connect()
    if not people.exists(conn, pid):
        return jsonify({"ok": False, "error": "no such person"}), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"url"} or not isinstance(body["url"], str):
        return jsonify({"ok": False, "error": "send { url: a link-in-bio page's link }"}), 400
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
    found = biofetch.suggest(conn, pid, biofetch.page_links(page.text), sources.routes(cfg), cfg["media_roots"])
    print(f"[people] link-in-bio import of {page.url}: {len(found['accounts'])} accounts, {found['other']} other links")
    return jsonify({"ok": True, "url": page.url, **found})


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
    return {**s, "session": session, "schedule": scheduler.status(s, cfg)}


@app.get("/api/sources")
def list_sources():
    r = sources.listing(db.connect(), _roots(), _sources_active())
    cfg = config.load()
    return jsonify({**r, "sources": [_with_session(s, cfg) for s in r["sources"]]})


def _source_or_404(sid):
    s = sources.get(db.connect(), sid, _sources_active())
    if s is None:
        abort(404)
    return _with_session(s)


@app.get("/api/sources/resolve")
def resolve_source():
    """What pasting a profile link would add: the tool, platform, target and
    default folder, shown before saving."""
    cfg = config.load()
    try:
        if request.args.get("tool") == "instaloader":
            # A profile name or @name, as POST /api/sources takes it with that tool.
            r = sources.resolve_name(request.args.get("url"), cfg["media_roots"])
        else:
            r = sources.resolve(request.args.get("url"), sources.routes(cfg), cfg["media_roots"])
    except sources.Refused as e:
        # An answer, not a failed request: the page asks as the user types.
        return jsonify({"ok": False, "error": str(e)})
    conn = db.connect()
    folder = sources.inside_root(r["folder"], cfg["media_roots"]) or r["folder"]
    return jsonify({"ok": True, **r, "source": sources.existing(conn, r["tool"], r["target"], folder),
                    "choices": sources.choices(r["tool"], r["platform"], r["target"]),
                    "session": sync.session_of(r["tool"], sources.clean_options(None), cfg)})


def _script_refused(options):
    """(error, status) when ``options`` (a source's, as sent) set a script
    that cannot be: from another origin (403), or one that does not exist
    or is refused now (400). Else None; a malformed id is parse_options'."""
    sid = options.get("script") if isinstance(options, dict) else None
    if not isinstance(sid, str) or not sources.SCRIPT_ID_RE.fullmatch(sid):
        return None
    if _foreign_origin():
        return FOREIGN, 403
    try:
        scripts.runnable(sid)
    except jobs.BadRequest as e:
        return str(e), 400
    return None


@app.post("/api/sources")
def create_source():
    body = request.get_json(silent=True) or {}
    refused = _script_refused(body.get("options"))
    if refused:
        return jsonify({"ok": False, "error": refused[0]}), refused[1]
    tool = body.get("tool")
    if tool is not None and tool not in sources.TOOLS:
        return jsonify({"ok": False, "error": f"tool must be one of: {', '.join(sources.TOOLS)}"}), 400
    cfg = config.load()
    table = sources.routes(cfg)
    if tool == "instaloader":
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
    options, error = sources.parse_options(body.get("options"), tool=tool, platform=platform, target=target)
    error = error or sources.login_refused(options, platform, sync.session_of(tool, options, cfg))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    conn = db.connect()
    try:
        sid = sources.create(conn, cfg["media_roots"], tool, target, folder, person, account, options,
                             int(time.time()), table)
    except sources.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    userdata.changed("sources")
    return jsonify({"ok": True, "source": _source_or_404(sid)})


@app.get("/api/sources/<int:sid>")
def get_source(sid):
    s = sources.get(db.connect(), sid, _sources_active())
    if s is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    return jsonify(_with_session(s))


def _script_source_foreign(s):
    """The 403 for another site's change to a source that runs a script
    (a schedule, a rename, a delete), else None: such a source is changed
    from the dashboard only, as its script is set there."""
    if s["options"]["script"] and _foreign_origin():
        return jsonify({"ok": False, "error": "a source that runs a script can only be changed from FeedVault's "
                                              "own dashboard"}), 403
    return None


@app.post("/api/sources/<int:sid>")
def update_source(sid):
    conn = db.connect()
    s = sources.get(conn, sid)
    if s is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("options"), dict):
        return jsonify({"ok": False, "error": f"send options: {{ {', '.join(sources.OPTION_KEYS)} }}"}), 400
    refused = _script_source_foreign(s)
    if refused:
        return refused
    if sid in _sources_active() and set(body["options"]) - {"schedule"}:
        # The sync's end clears full history and last N: it would clear the new
        # ones. It reads the options again at the end, so a schedule can change.
        return jsonify({"ok": False, "error": "its sync is queued or running; wait for it to end"}), 409
    sent = body["options"]
    refused = _script_refused(sent) if sent.get("script") != s["options"]["script"] else None
    if refused:
        return jsonify({"ok": False, "error": refused[0]}), refused[1]
    options, error = sources.parse_options(sent, base=s["options"], tool=s["tool"], platform=s["platform"],
                                           target=s["target"])
    # Last N stays open while a first sync with it has not worked yet.
    if not error and sent.get("first_posts") not in (None, s["options"]["first_posts"]) \
            and s["last_sync_at"] is not None and s["options"]["first_posts"] is None:
        error = "only the last posts is for a source's first sync, and this one has synced already"
    error = error or sources.login_refused(options, s["platform"], sync.session_of(s["tool"], options))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    # While it syncs only the schedule is sent: that key alone is written.
    sources.update(conn, sid, options, keys=("schedule",) if set(sent) <= {"schedule"} else None)
    if options["schedule"] != s["options"]["schedule"]:
        scheduler.forget(sid)
    # A new schedule or session: a source the scheduler stopped is tried again.
    if options["schedule"] != s["options"]["schedule"] or options["session"] != s["options"]["session"]:
        sources.resume(conn, sid)
    userdata.changed("sources")
    return jsonify({"ok": True, "source": _source_or_404(sid)})


@app.post("/api/sources/<int:sid>/rename")
def accept_rename(sid):
    """Accept the new handle the tool reported: the target changes, never
    the folder or its files."""
    conn = db.connect()
    s = sources.get(conn, sid)
    if s is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    refused = _script_source_foreign(s)
    if refused:
        return refused
    suggestion = s["health"]["rename"]
    body = request.get_json(silent=True) or {}
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
    refused = _script_source_foreign(s)
    if refused:
        return refused
    sources.dismiss_rename(conn, sid)
    userdata.changed("sources")
    return jsonify({"ok": True, "source": _source_or_404(sid)})


@app.post("/api/sources/<int:sid>/sync")
def sync_source(sid):
    if sources.row(db.connect(), sid) is None:
        return jsonify({"ok": False, "error": "no such source"}), 404
    try:
        job = sync.sync(sid, scripts_ok=not _foreign_origin())
    except sync.Busy as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    except sync.Refused as e:
        return jsonify({"ok": False, "error": str(e)}), 403
    except jobs.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    print(f"[jobs] #{job['id']} {job['kind']} queued")
    return jsonify({"ok": True, "job": job})


@app.post("/api/sources/sync-all")
def sync_all_sources():
    queued, skipped, errors = sync.sync_all(scripts_ok=not _foreign_origin())
    if queued:
        print(f"[jobs] sync all: {len(queued)} queued")
    return jsonify({"ok": True, "jobs": queued, "skipped": skipped, "errors": errors})


@app.delete("/api/sources/<int:sid>")
def delete_source(sid):
    s = sources.get(db.connect(), sid)
    refused = _script_source_foreign(s) if s is not None else None
    if refused:
        return refused
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
            "version": config.__version__, "tools": cfg.get("tools") or {},
            "instaloader": sync.settings(cfg), "routes": sources.routes(cfg),
            "gallery-dl": sync.tool_settings("gallery-dl", cfg), "yt-dlp": sync.tool_settings("yt-dlp", cfg),
            "youtube_max_seconds": yt_dlp.youtube_max_seconds(cfg),
            "check_updates": cfg.get("check_updates") is True,
            "schedules_paused": cfg.get("schedules_paused") is True,
            "desktop_notifications": notify.enabled(cfg),
            "bio_import": biofetch.enabled(cfg)}


@app.get("/api/config")
def get_config():
    return jsonify(_public_config(config.load()))


@app.post("/api/config")
def set_config():
    # Tool paths, roots, the schedules' pause, the link-in-bio switch: the
    # dashboard's alone. The userscript (instagram.com) never writes them.
    if _foreign_origin():
        return jsonify({"ok": False, "error": "settings can only be changed from FeedVault's own dashboard"}), 403
    body = request.get_json(silent=True) or {}
    # One read-modify-write at a time: two saves at once each keep the other's change.
    with config.editing:
        return _set_config(body)


def _set_config(body):
    cfg = config.load()
    tools = roots = insta = None
    changes = {}                               # config key -> new value, saved as they are
    if "routes" in body:
        changes["routes"], error = sources.clean_routes(body["routes"])
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
    cfg.update(changes)
    if tools is not None or roots is not None or insta is not None or changes:
        config.save(cfg)
    # A new tool-level session: the sources using it that the scheduler stopped are tried again.
    resumed = [sources.resume_tool(db.connect(), t) for t, before in sessions.items()
               if before != sync.session_of(t, {"session": None}, cfg)]
    if any(resumed):
        userdata.changed("sources")
    if changed:
        scanner.start(roots)
    return jsonify({"ok": True, "config": _public_config(cfg)})


@app.get("/api/downloaders")
def get_downloaders():
    return jsonify(downloaders.status())


@app.post("/api/downloaders/check")
def check_downloaders():
    """Find every tool again (Check again)."""
    return jsonify({"ok": True, **downloaders.status(refresh=True)})


@app.post("/api/yt-dlp/info-json-cookies")
def clean_info_json_cookies():
    """Take the cookies out of every yt-dlp info JSON under the media roots
    (info_cookies.py); ``apply`` false (the default) only counts them."""
    body = request.get_json(silent=True) or {}
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
    # The sidebar's "New" count rides along with the poll (news.py).
    new, new_until = news.count(db.connect())
    unread, latest = notify.unread(db.connect())
    # A tab that shows desktop notifications itself says so: notify-send waits.
    if request.args.get("desktop") == "1":
        notify.tab_shows()
    return jsonify({**jobs.listing(), "sync_all": sync.batch(), "new": new, "new_until": new_until,
                    "notifications": {"unread": unread, "latest": latest, "desktop": notify.enabled()}})


@app.get("/api/jobs/kinds")
def job_kinds():
    return jsonify(jobs.kinds())


@app.post("/api/jobs")
def start_job():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "send { kind, params }"}), 400
    if body.get("kind") in save.KINDS:         # their checks are POST /api/save's
        return jsonify({"ok": False, "error": "start it with POST /api/save"}), 400
    if body.get("kind") in scripts.JOB_KINDS:  # the origin check is theirs
        return jsonify({"ok": False, "error": "start it with POST /api/scripts/<id>/run, or a source's Sync"}), 400
    try:
        job = jobs.submit(body.get("kind"), body.get("params"))
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

def _foreign_origin():
    """Whether the request comes from a page that is not FeedVault's: an
    Origin not on this machine, or a browser saying it is cross-site. The
    userscript's requests from instagram.com are; the dashboard's are not
    (nor the Vite dev server's, on another port of this machine). Running
    scripts is refused to them, on top of the X-FeedVault header."""
    origin = request.headers.get("Origin")
    if origin is not None:
        scheme, _, host = origin.partition("://")
        if scheme != "http" or _host_only(host) not in ALLOWED_HOSTS:
            return True
    return request.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")


FOREIGN = "scripts can only be run from FeedVault's own dashboard"


@app.get("/api/scripts")
def list_scripts():
    if _foreign_origin():
        return jsonify({"ok": False, "error": FOREIGN}), 403
    return jsonify(scripts.listing())


@app.get("/api/scripts/<sid>")
def get_script(sid):
    if _foreign_origin():
        return jsonify({"ok": False, "error": FOREIGN}), 403
    s, refused = scripts.lookup(sid, content=True)
    if s is None:
        # A refused folder lists nothing: its reason, not "no such script".
        return jsonify({"ok": False, "error": scripts.missing(sid, refused) if refused else "no such script"}), 404
    return jsonify(s)


@app.post("/api/scripts/<sid>/run")
def run_script(sid):
    """A script by id, with its inputs: never a command, a path or its text."""
    if _foreign_origin():
        return jsonify({"ok": False, "error": FOREIGN}), 403
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
    folder = os.path.join(config.BUNDLE_DIR, "userscript") if config.FROZEN \
        else os.path.join(config.REPO_DIR, "userscript")
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
        db.init(config.db_path(cfg))
    except db.SchemaTooNew as e:
        print(f"[db] {e}")
        sys.exit(1)
    userdata.restore_all(db.connect(), cfg["data_directory"])
    news.ensure(db.connect())
    jobs.recover()
    sync.resume()
    scheduler.start()
    # Ctrl+C and SIGTERM still write the last few seconds of user data, after
    # stopping the scheduler, then running jobs (atexit runs the last
    # registered first).
    atexit.register(userdata.flush)
    atexit.register(jobs.shutdown)
    atexit.register(scheduler.stop)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    scanner.start(cfg["media_roots"])
    archives.warm(db.connect())
    trash.warm(cfg["media_roots"])
    if "--no-browser" not in sys.argv and os.environ.get("FEEDVAULT_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"[api] FeedVault {config.__version__}")
    print(f"[api] Dashboard → {url}")
    print(f"[api] Config      {config.config_path()}")
    print(f"[api] Database    {config.db_path(cfg)}")
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
