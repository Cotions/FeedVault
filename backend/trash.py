"""Deleting posts and media, through a trash folder.

Nothing is destroyed on delete. Files move to ``<root>/.feedvault-trash/`` under
the same relative path, which is a rename on the same disk and so instant even
for large videos. The scanner skips dot-folders, so trashed files never come
back as posts or show up as unmatched. Emptying the trash is the only step that
removes files for good.

Each move is logged in the trash folder's ``.manifest.jsonl`` (original path,
trash path, post id, time, and what the Trash page shows about the post, which
is gone from the index by then). Restore and purge work from those lines.

A post trashed again (an instaloader sync brought back a copy of a trashed
post, which goes straight back) keeps one entry: the copy's is purged when
the entry it was deleted with is still whole (merge_again).

Trashing a gallery-dl or yt-dlp post also adds it to those tools' download
archives (archives.py), so no sync brings it back; the entries it added are
kept on its manifest lines, and restoring it takes them out again.
Emptying the trash leaves them: a post deleted for good stays gone.
"""
import hashlib
import json
import os
import shutil
import threading
import time
import uuid

import archives
import db
import organize
import scanner
import thumbs
from parsers import IMAGE_EXT, VIDEO_EXT, ext_of

TRASH_NAME = ".feedvault-trash"
MANIFEST = ".manifest.jsonl"
# instaloader side files that live next to a post's metadata JSON.
_SIDE_SUFFIXES = (".txt", "_location.txt", "_comments.json")


class TrashError(Exception):
    pass


def trash_dir(root):
    return os.path.join(root, TRASH_NAME)


def _root_for(path, roots):
    """The media root holding ``path``. Refuses anything outside, or already trashed."""
    real = os.path.realpath(path)
    for root in roots:
        r = os.path.realpath(root)
        if real.startswith(r.rstrip(os.sep) + os.sep):
            if real.startswith(os.path.join(r, TRASH_NAME) + os.sep):
                raise TrashError("already in the trash")
            return r, os.path.relpath(real, r)
    raise TrashError("outside the media roots")


def _free_name(dest):
    if not os.path.lexists(dest):
        return dest
    stem, ext = os.path.splitext(dest)
    n = 1
    while os.path.lexists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def _move(path, roots, line):
    """Move one file to its root's trash and log it. Returns (size, trash path)."""
    root, rel = _root_for(path, roots)
    size = os.lstat(path).st_size
    dest = _free_name(os.path.join(trash_dir(root), rel))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        os.rename(path, dest)
    except OSError as e:
        raise TrashError(f"could not move to the trash: {e.strerror or e}") from e
    now = time.time()
    with open(os.path.join(trash_dir(root), MANIFEST), "a", encoding="utf-8") as f:
        f.write(json.dumps({"from": path, "to": dest, **line, "at": int(now), "at_ms": _next_stamp(roots, now),
                            "size": size}) + "\n")
    return size, dest


def _post_info(post, items, partial):
    """What a manifest line keeps about the post, for the Trash page."""
    return {"platform": post["platform"],
            "author": {"id": post["author_id"], "handle": post["author_handle"]}
            if post["author_id"] or post["author_handle"] else None,
            "kind": post["kind"], "posted_at": post["posted_at"], "items": items, "partial": partial}


def _media_files(m):
    """A media row's files, each with its manifest fields."""
    out = [(m["path"], {"role": "media", "idx": m["idx"], "media_kind": m["kind"]})]
    if m["poster_path"]:
        out.append((m["poster_path"], {"role": "poster", "idx": m["idx"], "media_kind": "image"}))
    return out


def _post_files(conn, post):
    """Every file that belongs to a post: media, posters, metadata, side files,
    as (path, manifest fields)."""
    return _files(conn.execute("SELECT * FROM media WHERE post_id = ? ORDER BY idx", (post["id"],)),
                  post["meta_path"], json.loads(post["side_files"]))


def _files(media, meta, sides=()):
    files = []
    for m in media:
        files.extend(_media_files(m))
    files.append((meta, {"role": "meta"}))
    # The parser's side files (gallery-dl's other per-file JSONs, music):
    # left behind, they would bring the post back on the next scan.
    files.extend((p, {"role": "side"}) for p in sides)
    for ext in (".json.xz", ".json"):
        if meta.endswith(ext):
            base = meta[: -len(ext)]
            files.extend((base + s, {"role": "side"}) for s in _SIDE_SUFFIXES)
            break
    # Filename-only posts use their first media file as the metadata path:
    # the first mention wins.
    out = {}
    for path, fields in files:
        out.setdefault(path, fields)
    return list(out.items())


def _move_all(files, roots, post_id, info, report, data_dir):
    """Move every existing file; all-or-nothing is not possible across renames,
    so report per file and tell the caller whether everything went."""
    ok = True
    for path, fields in files:
        if not os.path.lexists(path):
            continue                            # already gone: nothing to move
        try:
            size, dest = _move(path, roots, {"post": post_id, "batch": report["batch"], **info, **fields})
            report["bytes"] += size
            report["files"] += 1
            if fields.get("role") == "media":   # thumbnails are keyed by the media file
                thumbs.move(data_dir, path, dest)   # the Trash page shows it from there
        except (TrashError, OSError) as e:
            ok = False
            report["errors"].append({"path": path, "error": str(e)})
    return ok


def delete(post_ids, media_ids, roots, data_dir, copy_ids=(), pick=None):
    """Move posts, media items and extra copies (db.save_copies) to the trash.

    ``pick(conn)``, when given, runs under the write lock before anything
    moves and returns the (post ids, copy ids) to delete instead, so a caller
    can check the index and the files in the same lock as the move."""
    report = {"ok": True, "posts": [], "media": [], "copies": [], "files": 0, "bytes": 0, "errors": [],
              "batch": uuid.uuid4().hex}
    if not db.write_lock.acquire(timeout=30):
        report.pop("batch")
        return {**report, "ok": False, "error": "a scan is running; try again in a moment"}
    try:
        conn = db.connect()
        if pick is not None:
            post_ids, copy_ids = pick(conn)
        for cid in dict.fromkeys(copy_ids):
            copy = db.copy_row(conn, cid)
            if copy is not None:
                _delete_copy(conn, copy, roots, data_dir, report)

        for pid in dict.fromkeys(post_ids):
            post = conn.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
            if post is None:
                continue
            _delete_post(conn, post, roots, data_dir, report)

        for mid in dict.fromkeys(media_ids):
            m = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
            if m is None:
                continue
            post = conn.execute("SELECT * FROM posts WHERE id = ?", (m["post_id"],)).fetchone()
            others = conn.execute("SELECT path FROM media WHERE post_id = ? AND id != ? ORDER BY idx",
                                  (m["post_id"], mid)).fetchall()
            if not others:                      # the last item: the post goes with it
                _delete_post(conn, post, roots, data_dir, report)
                if post["id"] in report["posts"]:
                    report["media"].append(mid)
                continue
            files = _media_files(m)
            info = _never_again(_post_info(post, len(others) + 1, True), archives.media_entries(post, m), data_dir)
            moved = report["files"]
            if not _move_all(files, roots, post["id"], info, report, data_dir):
                _not_moved(info, report["files"] == moved, data_dir)
                continue
            for f, _ in files:
                thumbs.forget(data_dir, f)
            conn.execute("DELETE FROM media WHERE id = ?", (mid,))
            # Filename-only posts use their first media file as the metadata path.
            if post["meta_path"] == m["path"]:
                conn.execute("UPDATE posts SET meta_path = ? WHERE id = ?", (others[0]["path"], post["id"]))
            report["media"].append(mid)
        conn.commit()
    finally:
        db.write_lock.release()
    report.pop("batch")
    return report


def _never_again(info, entries, data_dir):
    """Add a post's (or an item's) archive entries before its files move;
    the manifest lines keep the ones added, for restore. Copies add none:
    their post stays."""
    added = archives.never_again(entries, data_dir) if entries and data_dir else {}
    return {**info, "archive": added} if added else info


def _not_moved(info, nothing, data_dir):
    """A deletion that failed: when no file moved, no manifest line keeps
    the archive entries it added, so they are taken out again (the post
    stays, and can still be synced). When some did, their lines keep them."""
    if nothing and info.get("archive"):
        archives.take_back(info["archive"], data_dir)


def _delete_post(conn, post, roots, data_dir, report):
    files = _post_files(conn, post)
    items = conn.execute("SELECT COUNT(*) FROM media WHERE post_id = ?", (post["id"],)).fetchone()[0]
    info = _never_again(_post_info(post, items, False), archives.post_entries(post), data_dir)
    moved = report["files"]
    if not _move_all(files, roots, post["id"], info, report, data_dir):
        _not_moved(info, report["files"] == moved, data_dir)
        conn.commit()                           # keep the index in step with what did move
        return
    for f, _ in files:
        thumbs.forget(data_dir, f)
    db.remove_post(conn, post["id"])
    report["posts"].append(post["id"])


def _delete_copy(conn, copy, roots, data_dir, report):
    """An extra copy of a post: its files go like a post's, with the copy's
    metadata path on each manifest line so the Trash page keeps it apart."""
    post = conn.execute("SELECT * FROM posts WHERE id = ?", (copy["post_id"],)).fetchone()
    platform, _, _ = copy["post_id"].partition(":")
    info = _post_info(post, len(copy["media"]), False) if post else \
        {"platform": platform, "author": None, "kind": None, "posted_at": None,
         "items": len(copy["media"]), "partial": False}
    files = _files(copy["media"], copy["meta_path"], copy["side_files"])
    if not _move_all(files, roots, copy["post_id"], {**info, "copy": copy["meta_path"]}, report, data_dir):
        conn.commit()
        return
    for f, _ in files:
        thumbs.forget(data_dir, f)
    conn.execute("DELETE FROM copies WHERE id = ?", (copy["id"],))
    conn.execute("DELETE FROM unmatched WHERE path = ?", (copy["meta_path"],))
    report["copies"].append(copy["id"])


def usage(roots):
    out = {"files": 0, "bytes": 0, "roots": []}
    for root in roots:
        path = trash_dir(root)
        files = size = 0
        for dirpath, _, names in os.walk(path):
            for n in names:
                if dirpath == path and n == MANIFEST:
                    continue
                try:
                    size += os.lstat(os.path.join(dirpath, n)).st_size
                    files += 1
                except OSError:
                    pass
        out["roots"].append({"root": root, "path": path, "files": files, "bytes": size})
        out["files"] += files
        out["bytes"] += size
    return out


def empty(roots, data_dir=None):
    """Permanently delete every trash folder under the media roots."""
    with db.write_lock:
        before = usage(roots)
        gone = set()
        for r in before["roots"]:
            for line in _read_manifest(r["root"]):
                gone.add(line.get("post"))
                if data_dir and isinstance(line.get("to"), str):
                    thumbs.forget(data_dir, line["to"])
            if os.path.isdir(r["path"]) and os.path.basename(r["path"]) == TRASH_NAME:
                shutil.rmtree(r["path"])
        forgotten = _forget_gone(roots, gone)
    return {"ok": True, "files": before["files"], "bytes": before["bytes"], "forgotten": forgotten}


def _forget_gone(roots, gone):
    """Forget the tags and the like of the posts in ``gone`` (whose trash
    entries were just deleted for good) that are now neither in the index nor
    in any manifest. Only these: a post whose trash sits on a root that is
    offline right now is never a candidate. Returns the user data tables that
    changed."""
    trashed = {line.get("post") for root in roots for line in _read_manifest(root)}
    return organize.forget_gone(db.connect(), [p for p in gone if isinstance(p, str) and p not in trashed])


# ---------------------------------------------------------------------------
# The manifest
#
# One JSON line per trashed file. Delete appends, restore and purge rewrite it;
# all three hold db.write_lock. Reads do not: an append in progress at worst
# leaves a half line, which is skipped and read whole next time (the file's
# size changed, so the cache misses).
# ---------------------------------------------------------------------------

_cache = {}                     # manifest path -> ((mtime_ns, size, inode), lines, entries, entries by key,
#                                  bytes read up to, their sha1)
_cache_lock = threading.Lock()


_last_stamp = None              # the highest at_ms written or read; None until a manifest is read


def _stamp(line):
    """A line's at_ms, or its whole second for lines written before it."""
    ms = line.get("at_ms")
    if isinstance(ms, int) and not isinstance(ms, bool):
        return ms
    at = line.get("at")
    return at * 1000 if isinstance(at, int) and not isinstance(at, bool) else 0


def _next_stamp(roots, now):
    """at_ms for a new line: the time in milliseconds, but always above every
    stamp written or read before (under db.write_lock, as every move is), so
    a list's ``upto`` never covers a deletion made after it was taken."""
    global _last_stamp
    if _last_stamp is None:
        for root in roots:
            _load(root)                        # sets _last_stamp from what is there
    _last_stamp = max(int(now * 1000), (_last_stamp or 0) + 1)
    return _last_stamp


def _manifest_path(root):
    return os.path.join(trash_dir(root), MANIFEST)


def _parse(data):
    """The manifest lines in ``data`` (bytes of whole lines)."""
    lines = []
    for raw in data.splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict) and isinstance(line.get("to"), str) \
                and isinstance(line.get("from"), str):
            lines.append(line)
    return lines


def _load(root):
    """(lines, entries, entries by key) of one root's manifest, parsed once per
    version of the file. Shared between callers: never mutate them.

    A manifest that only grew (a delete appends to it: same file, the bytes
    read before unchanged) is read from where the last read stopped, and
    only the entries its new lines belong to are grouped again: a big trash
    is not parsed whole after every delete."""
    global _last_stamp
    path = _manifest_path(root)
    try:
        with open(path, "rb") as f:
            data = f.read()
            st = os.fstat(f.fileno())
    except OSError:
        return [], [], {}
    end = data.rfind(b"\n") + 1                # a line being appended is read next time
    sig = (st.st_mtime_ns, len(data), st.st_ino)
    with _cache_lock:
        hit = _cache.get(path)
    if hit and hit[0] == sig:
        return hit[1:4]
    view = memoryview(data)
    h = hashlib.sha1(view[:hit[4]] if hit and hit[4] <= end else b"")
    if hit and hit[0][2] == st.st_ino and hit[4] <= end and h.digest() == hit[5]:
        lines = hit[1] + _parse(data[hit[4]:end])
        entries = _group(root, lines, prior=hit[3], start=len(hit[1]))
        h.update(view[hit[4]:end])
    else:
        lines = _parse(data[:end])
        entries = _group(root, lines)
        h = hashlib.sha1(view[:end])
    digest = h.digest()
    by_key = {g["key"]: g for g in entries}
    _last_stamp = max(_last_stamp or 0, max((g["stamp"] for g in entries), default=0))
    with _cache_lock:
        _cache[path] = (sig, lines, entries, by_key, end, digest)
    return lines, entries, by_key


def _read_manifest(root):
    return _load(root)[0]


def _write_manifest(root, lines):
    path = _manifest_path(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    os.replace(tmp, path)


def entry_key(root, post, batch):
    # Opaque on purpose: the URL of a trash thumbnail must never carry a path.
    return hashlib.sha1(f"{root}\0{post}\0{batch}".encode("utf-8", "surrogateescape")).hexdigest()[:20]


def _line_key(root, line):
    batch = line.get("batch")
    if isinstance(line.get("copy"), str):     # an extra copy: its own entry, even in the post's batch
        batch = f"{batch}\0copy\0{line['copy']}"
    return entry_key(root, line.get("post"), batch)


# ---------------------------------------------------------------------------
# Entries: one per (trash folder, post, batch)
# ---------------------------------------------------------------------------

def _media_kind(line):
    if line.get("media_kind") in ("image", "video"):
        return line["media_kind"]
    ext = ext_of(line["to"])
    return "video" if ext in VIDEO_EXT else "image" if ext in IMAGE_EXT else None


def _roles(lines):
    """Each line's role. Lines written before roles were recorded: a video's
    image with the same stem is its poster (instaloader's layout)."""
    if all(line.get("role") for line in lines):
        return [line["role"] for line in lines]
    stems = {os.path.splitext(line["to"])[0] for line in lines if _media_kind(line) == "video"}
    out = []
    for line in lines:
        if line.get("role"):
            out.append(line["role"])
            continue
        kind = _media_kind(line)
        if kind == "image" and os.path.splitext(line["to"])[0] in stems:
            out.append("poster")
        elif kind:
            out.append("media")
        elif line["to"].endswith((".json", ".json.xz")):
            out.append("meta")
        else:
            out.append("side")
    return out


def _group(root, lines, prior=None, start=0):
    """Entries of ``lines``. With ``prior`` (entries by key of
    ``lines[:start]``), only the lines from ``start`` on are grouped: the
    entries they belong to are made anew, the others kept as they were."""
    groups = {}
    for seq in range(start, len(lines)):
        line = lines[seq]
        key = _line_key(root, line)
        g = groups.get(key)
        if g is None:
            old = prior.get(key) if prior else None
            g = groups[key] = {"key": key, "root": root, "lines": list(old["lines"]) if old else []}
        g["lines"].append(line)
        g["seq"] = seq                           # later in the file: deleted later, within one second
    for g in groups.values():
        ls = g["lines"]
        roles = _roles(ls)
        g["roles"] = roles
        g["at"] = max((line.get("at") or 0) for line in ls)
        g["stamp"] = max(_stamp(line) for line in ls)
        g["paths"] = tuple(line["to"] for line in ls)
        # What the lines recorded, for an entry not measured yet (_measured_now).
        g["recorded"] = (sum(line["size"] for line in ls), len(ls), False) \
            if all(isinstance(line.get("size"), int) for line in ls) else None
        media = sorted((i for i, r in enumerate(roles) if r == "media"),
                       key=lambda i: (ls[i].get("idx") or 0, ls[i]["to"]))
        g["media"] = [ls[i] for i in media]
        first = ls[0]
        post = first.get("post") or ""
        platform, _, post_id = post.partition(":") if ":" in post else (None, "", post)
        author = first.get("author") if isinstance(first.get("author"), dict) else None
        kinds = [_media_kind(m) for m in g["media"]]
        g["public"] = {
            "key": g["key"], "post": first.get("post"),
            "platform": first.get("platform") or platform or None, "post_id": post_id or None,
            "batch": first.get("batch"), "at": g["at"],
            "author": {"id": author.get("id"), "handle": author.get("handle")} if author else None,
            "kind": first.get("kind") or ("carousel" if len(kinds) > 1 else kinds[0] if kinds else None),
            "posted_at": first.get("posted_at"),
            "items": len(g["media"]),
            "of": max((line["items"] for line in ls if isinstance(line.get("items"), int)), default=None),
            # Items deleted one by one, then the last one taking the post
            # with it, in the same call: the whole post is gone, not partial.
            "partial": any(line.get("partial") is True for line in ls)
            and not any(line.get("partial") is False for line in ls),
            "copy": isinstance(first.get("copy"), str),
        }
    if prior:
        return [g for key, g in prior.items() if key not in groups] + list(groups.values())
    return list(groups.values())


_sorted = (None, [])            # (the roots' entry lists it was made from, all of them sorted)


def _all_entries(roots):
    global _sorted
    lists = [_load(root)[1] for root in roots]
    made_from, out = _sorted
    if made_from is not None and len(made_from) == len(lists) and all(a is b for a, b in zip(made_from, lists)):
        return out                             # no manifest changed: sorted already
    out = [g for entries in lists for g in entries]
    out.sort(key=lambda g: (-g["at"], -g["seq"], g["key"]))
    _sorted = (lists, out)
    return out


def _author_key(a):
    return (a or {}).get("id") or (a or {}).get("handle")


# Measuring every entry on every list would be one lstat per trashed file
# per request (tens of thousands after a big sort, on a spinning disk). An
# entry is measured when it is on the page asked for (again once its last
# measure is MEASURE_TTL old), or by check(); every other entry counts as
# its last measure, or as its lines recorded it (every file there) if it was
# never measured, so the totals stay close without touching the disk.
MEASURE_TTL = 30
_measured = {}                  # entry key -> (its files, time measured, (bytes, files, missing))


def _measure(g):
    """(bytes, files, missing) of an entry, from the files still in the trash.
    One lstat per file: a file moved out by hand is only noticed this way."""
    size = files = 0
    missing = False
    for line in g["lines"]:
        try:
            st = os.lstat(line["to"])
        except OSError:
            missing = True
            continue
        files += 1
        size += line["size"] if isinstance(line.get("size"), int) else st.st_size
    return size, files, missing


def _measured_now(g, fresh, now, force=False):
    """_measure, from _measured when that is still good: for ``fresh``, one
    younger than MEASURE_TTL, else any as long as the entry lists the same
    files (``force``: none). Never measured and not ``fresh``: as its lines
    say, when each recorded its size (old lines did not: measured once then)."""
    paths = g["paths"]
    hit = None if force else _measured.get(g["key"])
    if hit and hit[0] == paths and (not fresh or now - hit[1] < MEASURE_TTL):
        return hit[2]
    if not fresh and hit is None and g["recorded"]:
        return g["recorded"]
    m = _measure(g)
    _measured[g["key"]] = (paths, now, m)
    return m


def _forget_measures(entries):
    """Drop the measures of entries no longer in the trash."""
    if len(_measured) > len(entries) + 1000:
        alive = {g["key"] for g in entries}
        for key in [k for k in _measured if k not in alive]:
            _measured.pop(key, None)


def check(roots):
    """Measure every entry now ("Check for missing files"): {entries, files,
    bytes, missing: entries with a file gone}."""
    entries = _all_entries(roots)
    now = time.monotonic()
    out = {"entries": len(entries), "files": 0, "bytes": 0, "missing": 0}
    for g in entries:
        size, files, missing = _measured_now(g, True, now, force=True)
        out["files"] += files
        out["bytes"] += size
        out["missing"] += missing
    _forget_measures(entries)
    return out


def _matches(g, author=None, since=None, before=None, platform=None, accounts=None, upto=None):
    """``accounts``: a person's {(platform, author id)} (people.account_set).
    ``upto``: a list's ``upto``, nothing deleted after it was taken."""
    if upto is not None and g["stamp"] > upto:
        return False
    p = g["public"]
    # Author ids and handles are only unique within a platform.
    if platform is not None and p["platform"] != platform:
        return False
    if author is not None and _author_key(p["author"]) != author:
        return False
    if accounts is not None and (p["platform"], _author_key(p["author"])) not in accounts:
        return False
    if since is not None and p["at"] < since:
        return False
    if before is not None and p["at"] >= before:
        return False
    return True


def items(roots, author=None, since=None, before=None, platform=None, offset=0, limit=60, accounts=None,
          upto=None):
    entries = _all_entries(roots)
    ffmpeg = thumbs.have_ffmpeg()
    newest = max((g["stamp"] for g in entries), default=0)
    out = {"total": 0, "files": 0, "bytes": 0, "upto": newest if upto is None else min(upto, newest),
           "trash": {"entries": len(entries), "files": 0, "bytes": 0}, "authors": [], "entries": []}
    authors = {}
    now = time.monotonic()
    for g in entries:
        match = _matches(g, author, since, before, platform, accounts, upto)
        # Only the page shown is measured again (see MEASURE_TTL).
        shown = match and offset <= out["total"] < offset + limit
        size, files, missing = _measured_now(g, shown, now)
        p = g["public"]
        out["trash"]["files"] += files
        out["trash"]["bytes"] += size
        ak = _author_key(p["author"])
        if ak is not None:
            a = authors.setdefault((p["platform"], ak), {
                "platform": p["platform"], "id": p["author"].get("id"), "handle": p["author"].get("handle"),
                "entries": 0, "bytes": 0})
            a["entries"] += 1
            a["bytes"] += size
        if not match:
            continue
        out["total"] += 1
        out["files"] += files
        out["bytes"] += size
        if offset < out["total"] <= offset + limit:
            out["entries"].append({**p, "files": files, "bytes": size, "missing": missing,
                                   "thumb_url": f"/trash/{g['key']}/thumb" if _thumb_source(g, ffmpeg) else None})
    out["authors"] = sorted(authors.values(), key=lambda a: (-a["bytes"], a["handle"] or ""))
    _forget_measures(entries)
    return out


def _find(roots, key):
    for root in roots:
        g = _load(root)[2].get(key)
        if g is not None:
            return g
    return None


# ---------------------------------------------------------------------------
# Serving thumbnails of trashed files
# ---------------------------------------------------------------------------

def _inside_trash(path, root):
    """Whether ``path`` (symlinks followed) is a file inside ``root``'s trash folder."""
    base = os.path.realpath(trash_dir(root))
    real = os.path.realpath(path)
    return real.startswith(base + os.sep) and real != os.path.join(base, MANIFEST)


def _removable(path, root):
    """Whether purge may unlink ``path``: its folder (symlinks followed) is the
    trash folder or inside it, and it is not the manifest. The last component
    is not followed, since os.remove on a symlink removes only the link; a
    trashed symlink to a file outside can then still be purged on its own."""
    base = os.path.realpath(trash_dir(root))
    parent = os.path.realpath(os.path.dirname(path))
    if parent != base and not parent.startswith(base + os.sep):
        return False
    return not (parent == base and os.path.basename(path) == MANIFEST)


def _thumb_source(g, ffmpeg=None):
    """(media line, poster path or None) to make the entry's thumbnail from."""
    if not g["media"]:
        return None
    m = g["media"][0]
    if _media_kind(m) == "image":
        return m, None
    for line, role in zip(g["lines"], g["roles"]):
        if role == "poster" and (line.get("idx") == m.get("idx") if "idx" in m
                                 else os.path.splitext(line["to"])[0] == os.path.splitext(m["to"])[0]):
            return m, line["to"]
    return (m, None) if (thumbs.have_ffmpeg() if ffmpeg is None else ffmpeg) else None


def thumb(roots, key, data_dir):
    """Path of a thumbnail to send for an entry (or of the trashed image itself
    when none can be made), or None.

    Only files listed in a manifest, and only those that resolve inside that
    root's trash folder, are ever read."""
    g = _find(roots, key)
    src = g and _thumb_source(g)
    if not src:
        return None
    m, poster = src
    if not _inside_trash(m["to"], g["root"]) or (poster and not _inside_trash(poster, g["root"])):
        return None
    row = {"path": m["to"], "kind": _media_kind(m), "poster_path": poster}
    out = thumbs.thumb_for(data_dir, row)
    if out:
        return out
    return m["to"] if row["kind"] == "image" else None


# ---------------------------------------------------------------------------
# Restore and purge
# ---------------------------------------------------------------------------

def restore(post_ids, roots, data_dir=None, keys=None):
    """Put files back, then re-index them: each post's most recent deletion
    (``post_ids``), or exactly the entries named by ``keys``."""
    report = {"ok": True, "posts": [], "files": 0, "errors": []}
    wanted = set(post_ids or ())
    wanted_keys = set(keys or ())
    touched_dirs = set()
    archived = {}                                # tool -> archive entries the restored lines added
    with db.write_lock:
        for root in roots:
            lines = _read_manifest(root)
            if not lines:
                continue
            latest = {}                          # post -> batch of its latest deletion
            for e in lines:
                if e.get("post") in wanted:
                    latest[e["post"]] = e.get("batch")
            keep = []
            for e in lines:
                pid = e.get("post")
                if not (pid in latest and e.get("batch") == latest[pid]) \
                        and _line_key(root, e) not in wanted_keys:
                    keep.append(e)
                    continue
                src, dest = e["to"], e["from"]
                try:
                    if os.path.lexists(dest):
                        raise TrashError("a file is already back at the original place")
                    if not os.path.lexists(src):
                        raise TrashError("no longer in the trash")
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    os.rename(src, dest)
                    report["files"] += 1
                    touched_dirs.add(os.path.dirname(dest))
                    if data_dir:
                        thumbs.move(data_dir, src, dest)
                    if isinstance(e.get("archive"), dict):
                        for tool, entries in e["archive"].items():
                            if isinstance(entries, list):
                                archived.setdefault(tool, set()).update(x for x in entries if isinstance(x, str))
                    if pid not in report["posts"]:
                        report["posts"].append(pid)
                except (TrashError, OSError) as err:
                    report["errors"].append({"path": dest, "error": str(err)})
                    keep.append(e)
            if len(keep) != len(lines):
                _write_manifest(root, keep)
        if archived and data_dir:
            archives.take_back({t: sorted(es) for t, es in archived.items()}, data_dir)
    if touched_dirs:
        scanner.index_dirs(roots, touched_dirs)
    return report


def _prune_dirs(path, root):
    """Remove folders left empty by a purge, up to (not including) the trash folder."""
    top = os.path.realpath(trash_dir(root))
    d = os.path.dirname(path)
    while os.path.realpath(d).startswith(top + os.sep):
        try:
            os.rmdir(d)
        except OSError:
            return
        d = os.path.dirname(d)


def purge(roots, keys, data_dir, match=None):
    """Permanently delete the files of the given entries and drop their lines.
    ``match`` (_matches arguments) picks the entries instead of ``keys``."""
    with db.write_lock:
        if match is not None:
            keys = [g["key"] for g in _all_entries(roots) if _matches(g, **match)]
        return _purge(roots, keys, data_dir)


def _purge(roots, keys, data_dir):
    """purge, under db.write_lock."""
    report = {"ok": True, "entries": 0, "keys": [], "files": 0, "bytes": 0, "dropped": 0, "errors": []}
    wanted = set(keys)
    gone = set()
    for root in roots:
        lines = _read_manifest(root)
        if not any(_line_key(root, e) in wanted for e in lines):
            continue
        keep, failed, done, posts = [], set(), set(), {}
        for e in lines:
            key = _line_key(root, e)
            if key not in wanted:
                keep.append(e)
                continue
            posts[key] = e.get("post")
            path = e["to"]
            try:
                if not os.path.lexists(path):
                    report["dropped"] += 1
                    done.add(key)
                    continue
                if not _removable(path, root):
                    raise TrashError("outside the trash folder")
                if os.path.isdir(path) and not os.path.islink(path):
                    raise TrashError("not a file")
                size = os.lstat(path).st_size
                os.remove(path)
                report["files"] += 1
                report["bytes"] += size
                done.add(key)
                thumbs.forget(data_dir, path)
                _prune_dirs(path, root)
            except (TrashError, OSError) as err:
                report["errors"].append({"path": path, "error": str(err)})
                failed.add(key)
                keep.append(e)
        gone.update(posts[k] for k in done - failed)
        report["keys"].extend(sorted(done - failed))
        report["entries"] += len(done - failed)
        _write_manifest(root, keep)
    report["forgotten"] = _forget_gone(roots, gone) if gone else []
    return report


# ---------------------------------------------------------------------------
# A post trashed again
# ---------------------------------------------------------------------------

def _whole(g):
    """Whether an entry is a whole post (not one item, not an extra copy)
    with every file it lists still in the trash, inside the trash folder."""
    p = g["public"]
    return not p["partial"] and not p["copy"] and all(
        os.path.lexists(line["to"]) and _inside_trash(line["to"], g["root"]) for line in g["lines"])


def merge_again(roots, post_ids, data_dir):
    """One trash entry for a post trashed again: a sync (sync.retrash)
    brought back a copy of a post that was in the trash, and that copy was
    just trashed too. Its entry (the post's newest) is purged when an older
    entry of the same post, in the same trash folder, is whole (every file
    still there) and has as many media items: the post keeps the entry it
    was deleted with, and the files it had then. Otherwise both stay. Only
    a copy that added no archive entries (instaloader's) is dropped.
    Returns the post ids merged."""
    wanted, merged = set(post_ids), []
    with db.write_lock:
        drop = []
        for root in roots:
            by_post = {}
            for g in _load(root)[1]:
                if g["public"]["post"] in wanted and not g["public"]["copy"]:
                    by_post.setdefault(g["public"]["post"], []).append(g)
            for pid, gs in sorted(by_post.items()):
                if len(gs) < 2:
                    continue
                gs.sort(key=lambda g: (g["at"], g["seq"]))
                new = gs[-1]
                if not _whole(new) or any(isinstance(line.get("archive"), dict) for line in new["lines"]):
                    continue
                # Not sharing a file: one taken out of the trash by hand frees
                # its trash path for the copy's file of the same name.
                files = {line["to"] for line in new["lines"]}
                if any(_whole(g) and g["public"]["items"] == new["public"]["items"]
                       and not files & {line["to"] for line in g["lines"]} for g in gs[:-1]):
                    drop.append(new["key"])
                    merged.append(pid)
        report = _purge(roots, drop, data_dir) if drop else None
    if report and report["errors"]:
        left = {g["public"]["post"] for g in _all_entries(roots) if g["key"] in set(drop) - set(report["keys"])}
        merged = [pid for pid in merged if pid not in left]
    return merged
