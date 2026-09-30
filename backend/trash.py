"""Deleting posts and media, through a trash folder.

Nothing is destroyed on delete. Files move to ``<root>/.feedvault-trash/`` under
the same relative path, which is a rename on the same disk and so instant even
for large videos. The scanner skips dot-folders, so trashed files never come
back as posts or show up as unmatched. Emptying the trash is the only step that
removes files for good.

Each move is logged in the trash folder's ``.manifest.jsonl`` (original path,
trash path, post id, time), so a restore can be added without guesswork.
"""
import json
import os
import shutil
import time
import uuid

import db
import scanner
import thumbs

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


def _move(path, roots, post_id, batch):
    """Move one file to its root's trash. Returns its size."""
    root, rel = _root_for(path, roots)
    size = os.path.getsize(path)
    dest = _free_name(os.path.join(trash_dir(root), rel))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        os.rename(path, dest)
    except OSError as e:
        raise TrashError(f"could not move to the trash: {e.strerror or e}") from e
    with open(os.path.join(trash_dir(root), MANIFEST), "a", encoding="utf-8") as f:
        f.write(json.dumps({"from": path, "to": dest, "post": post_id, "batch": batch,
                            "at": int(time.time())}) + "\n")
    return size


def _post_files(conn, post):
    """Every file that belongs to a post: media, posters, metadata, side files."""
    files = []
    for m in conn.execute("SELECT path, poster_path FROM media WHERE post_id = ? ORDER BY idx", (post["id"],)):
        files.append(m["path"])
        if m["poster_path"]:
            files.append(m["poster_path"])
    meta = post["meta_path"]
    files.append(meta)
    for ext in (".json.xz", ".json"):
        if meta.endswith(ext):
            base = meta[: -len(ext)]
            files.extend(base + s for s in _SIDE_SUFFIXES)
            break
    return list(dict.fromkeys(files))


def _move_all(files, roots, post_id, report):
    batch = report["batch"]
    """Move every existing file; all-or-nothing is not possible across renames,
    so report per file and tell the caller whether everything went."""
    ok = True
    for path in files:
        if not os.path.lexists(path):
            continue                            # already gone: nothing to move
        try:
            report["bytes"] += _move(path, roots, post_id, batch)
            report["files"] += 1
        except (TrashError, OSError) as e:
            ok = False
            report["errors"].append({"path": path, "error": str(e)})
    return ok


def delete(post_ids, media_ids, roots, data_dir):
    report = {"ok": True, "posts": [], "media": [], "files": 0, "bytes": 0, "errors": [],
              "batch": uuid.uuid4().hex}
    if not db.write_lock.acquire(timeout=30):
        report.pop("batch")
        return {**report, "ok": False, "error": "a scan is running; try again in a moment"}
    try:
        conn = db.connect()
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
            files = [m["path"]] + ([m["poster_path"]] if m["poster_path"] else [])
            if not _move_all(files, roots, post["id"], report):
                continue
            for f in files:
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


def _delete_post(conn, post, roots, data_dir, report):
    files = _post_files(conn, post)
    if not _move_all(files, roots, post["id"], report):
        conn.commit()                           # keep the index in step with what did move
        return
    for f in files:
        thumbs.forget(data_dir, f)
    db.remove_post(conn, post["id"])
    report["posts"].append(post["id"])


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


def empty(roots):
    """Permanently delete every trash folder under the media roots."""
    with db.write_lock:
        before = usage(roots)
        for r in before["roots"]:
            if os.path.isdir(r["path"]) and os.path.basename(r["path"]) == TRASH_NAME:
                shutil.rmtree(r["path"])
    return {"ok": True, "files": before["files"], "bytes": before["bytes"]}


def _read_manifest(root):
    path = os.path.join(trash_dir(root), MANIFEST)
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return entries


def _write_manifest(root, entries):
    path = os.path.join(trash_dir(root), MANIFEST)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    os.replace(tmp, path)


def restore(post_ids, roots):
    """Put back the files of each post's most recent deletion, then re-index them."""
    report = {"ok": True, "posts": [], "files": 0, "errors": []}
    wanted = set(post_ids)
    touched_dirs = set()
    with db.write_lock:
        for root in roots:
            entries = _read_manifest(root)
            if not entries:
                continue
            latest = {}                          # post -> batch of its latest deletion
            for e in entries:
                if e.get("post") in wanted:
                    latest[e["post"]] = e.get("batch")
            keep = []
            for e in entries:
                pid = e.get("post")
                if pid not in latest or e.get("batch") != latest[pid]:
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
                    if pid not in report["posts"]:
                        report["posts"].append(pid)
                except (TrashError, OSError) as err:
                    report["errors"].append({"path": dest, "error": str(err)})
                    keep.append(e)
            _write_manifest(root, keep)
    if touched_dirs:
        scanner.index_dirs(roots, touched_dirs)
    return report
