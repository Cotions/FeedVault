"""FeedVault backend: a catalog of downloaded social posts.

Run from source with ./run.sh, or directly: python backend/app.py
"""
import atexit
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import config
import db
import duplicates
import hashing
import jobs
import organize
import scanner
import thumbs
import trash
import userdata

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
# /media is exempt because <img> and <video> cannot send headers; a
# cross-origin page cannot read those bytes anyway.
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
    return None


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
    return jsonify(db.stats(db.connect()))


@app.get("/api/storage")
def get_storage():
    t = trash.usage(_roots())
    # The cached result is shared: extend a copy.
    return jsonify({**db.storage(db.connect()), "trash": {"files": t["files"], "bytes": t["bytes"]}})


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
    ))


def _purge_filter(f):
    """A purge filter, checked: {platform, author, since, before} with at least one set."""
    if not isinstance(f, dict) or set(f) - {"platform", "author", "since", "before"}:
        return None
    out = {k: f.get(k) for k in ("platform", "author", "since", "before")}
    if any(out[k] is not None and not (isinstance(out[k], str) and out[k]) for k in ("platform", "author")):
        return None
    for k in ("since", "before"):
        if out[k] is not None and (not isinstance(out[k], int) or isinstance(out[k], bool) or out[k] < 0):
            return None
    return out if any(v is not None for v in out.values()) else None


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
            return jsonify({"ok": False, "error": "filter needs platform, author, since or before (and no keys)"}), 400
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


def _public_config(cfg):
    return {"media_roots": cfg["media_roots"], "data_directory": cfg["data_directory"],
            "version": config.__version__, "tools": cfg.get("tools") or {}}


@app.get("/api/config")
def get_config():
    return jsonify(_public_config(config.load()))


@app.post("/api/config")
def set_config():
    body = request.get_json(silent=True) or {}
    cfg = config.load()
    tools = roots = None
    if "tools" in body:                        # checked before anything is saved
        tools, error = config.clean_tools(body["tools"], jobs.TOOLS)
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
    if tools is not None or roots is not None:
        config.save(cfg)
    if changed:
        scanner.start(roots)
    return jsonify({"ok": True, "config": _public_config(cfg)})


# ---------------------------------------------------------------------------
# Jobs (jobs.py: started by kind, never from a command in the request)
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs():
    return jsonify(jobs.listing())


@app.get("/api/jobs/kinds")
def job_kinds():
    return jsonify(jobs.kinds())


@app.post("/api/jobs")
def start_job():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "send { kind, params }"}), 400
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
        jobs.shutdown()                        # os._exit skips atexit
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

def _send(path):
    if not path or not os.path.isfile(path):
        abort(404)
    resp = send_file(path, conditional=True, max_age=3600)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.get("/media/<int:media_id>")
def serve_media(media_id):
    row = db.media_row(db.connect(), media_id)
    return _send(row["path"] if row else None)


@app.get("/media/<int:media_id>/thumb")
def serve_thumb(media_id):
    """Small JPEG for grids. Falls back to the original image if one cannot be made."""
    row = db.media_row(db.connect(), media_id)
    if row is None:
        abort(404)
    path = thumbs.thumb_for(config.load()["data_directory"], row)
    if path:
        return _send(path)
    if row["kind"] == "image":
        return _send(row["path"])
    abort(404)


@app.get("/media/<int:media_id>/poster")
def serve_poster(media_id):
    row = db.media_row(db.connect(), media_id)
    return _send(row["poster_path"] if row else None)


@app.get("/media/copy/<int:copy_id>/thumb")
def serve_copy_thumb(copy_id):
    """Thumbnail of an extra copy's first item: only paths the scanner
    recorded for that copy are read, as for /media."""
    copy = db.copy_row(db.connect(), copy_id)
    if copy is None or not copy["media"]:
        abort(404)
    first = min(copy["media"], key=lambda m: m["idx"])
    path = thumbs.thumb_for(config.load()["data_directory"], first)
    if path:
        return _send(path)
    if first["kind"] == "image":
        return _send(first["path"])
    abort(404)


@app.get("/trash/<key>/thumb")
def serve_trash_thumb(key):
    """Thumbnail of a trashed entry. The key is opaque; trash.thumb only reads
    files listed in a manifest that resolve inside their trash folder."""
    if len(key) != 20 or not all(c in "0123456789abcdef" for c in key):
        abort(404)
    cfg = config.load()
    return _send(trash.thumb(cfg["media_roots"], key, cfg["data_directory"]))


@app.get("/userscript/feedvault.user.js")
def serve_userscript():
    folder = os.path.join(config.BUNDLE_DIR, "userscript") if config.FROZEN \
        else os.path.join(config.REPO_DIR, "userscript")
    return send_from_directory(folder, "feedvault.user.js", mimetype="text/javascript", max_age=0)


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
    if path and os.path.isfile(os.path.join(static, path)):
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
    try:
        db.init(config.db_path(cfg))
    except db.SchemaTooNew as e:
        print(f"[db] {e}")
        sys.exit(1)
    userdata.restore_all(db.connect(), cfg["data_directory"])
    jobs.recover()
    # Ctrl+C and SIGTERM still write the last few seconds of user data, after
    # stopping running jobs (atexit runs the last registered first).
    atexit.register(userdata.flush)
    atexit.register(jobs.shutdown)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    scanner.start(cfg["media_roots"])
    if "--no-browser" not in sys.argv and os.environ.get("FEEDVAULT_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"[api] FeedVault {config.__version__}")
    print(f"[api] Dashboard → {url}")
    print(f"[api] Config      {config.config_path()}")
    print(f"[api] Database    {config.db_path(cfg)}")
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
