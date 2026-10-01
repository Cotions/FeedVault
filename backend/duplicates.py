"""Exact duplicates: posts, or copies of one post, holding the same files.

Two kinds of group:

- ``copies``: an indexed post and the extra copies of it the scanner found in
  other folders (db.save_copies). Items are compared by position: size, then
  the partial hash (first and last MiB), then a full sha1, which the worker
  computes only for items that got that far.
- ``content``: different posts (a repost saved under another id) that share
  at least one file, by size and full sha1. Posts linked through any shared
  file form one group.

Hashes come from hashing.py; until a file is hashed its group is pending.
Nothing here reads a media file: resolve() only stats them, to refuse when a
file changed since it was hashed.
"""
import hashlib
import json
import os
import time

import db
import hashing
import scanner
import thumbs
import trash

KINDS = ("copies", "content")
# A file shared by more posts than this (a placeholder image, a watermark
# card) does not link them into one group: they are not reposts of each other.
MAX_SHARED = 20


def group_id(kind, key):
    return hashlib.sha1(f"{kind}\0{key}".encode("utf-8", "surrogateescape")).hexdigest()[:20]


def _key(members):
    """What a dismissal is stored under: the sorted post ids, and the
    metadata paths of copies (copy ids do not survive rebuilding the index)."""
    return json.dumps(sorted(m["post_id"] if m["type"] == "post" else m["meta_path"] for m in members))


def _hashes(conn):
    """path -> (size, mtime_ns, partial, full, pixels or None)."""
    return {r[0]: (r[1], r[2], r[3], r[4], r[5] * r[6] if r[5] and r[6] else None) for r in conn.execute(
        "SELECT path, size, mtime_ns, partial, full, width, height FROM media_hash")}


def _dismissed(conn):
    out = []
    for (key,) in conn.execute("SELECT key FROM dismissed_duplicates"):
        try:
            out.append(frozenset(json.loads(key)))
        except (ValueError, TypeError):
            pass
    return out


def is_dismissed(key, dismissed):
    """A group stays dismissed while its members are among a dismissed
    group's: losing a member does not bring it back, gaining one does."""
    members = set(json.loads(key))
    return any(members <= d for d in dismissed)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def _items(media, hashes):
    out = []
    for m in media:
        h = hashes.get(m["path"])
        out.append({"idx": m["idx"], "kind": m["kind"], "size": m["size"], "path": m["path"],
                    "poster_path": m["poster_path"],
                    "hash": h[3] if h is not None and h[0] == m["size"] else None})
    return out


def _member(kind, post_id, meta_path, items, saved_at, kept, **extra):
    return {"type": kind, "post_id": post_id, "folder": os.path.dirname(meta_path), "meta_path": meta_path,
            "items": items, "paths": [i["path"] for i in items], "files": len(items),
            "bytes": sum(i["size"] or 0 for i in items), "saved_at": saved_at, "kept": kept, **extra}


def _post_member(conn, row, hashes):
    media = conn.execute("SELECT * FROM media WHERE post_id = ? AND missing = 0 ORDER BY idx",
                         (row["id"],)).fetchall()
    summary = db.summary(conn, row)
    return _member("post", row["id"], row["meta_path"], _items(media, hashes), row["saved_at"],
                   row["decision"] == "keep", id=row["id"], post=summary,
                   thumb_url=summary["cover"]["url"] if summary["cover"] and summary["cover"].get("poster", True)
                   else None)


def _copy_member(copy, post_row, hashes):
    media = sorted(json.loads(copy["media"]), key=lambda m: m["idx"])
    first = media[0] if media else None
    thumb = first and (first["kind"] == "image" or first.get("poster_path") or thumbs.have_ffmpeg())
    return _member("copy", copy["post_id"], copy["meta_path"], _items(media, hashes),
                   int(copy["meta_mtime"] or copy["first_seen"]),
                   post_row["decision"] == "keep", id=f"copy:{copy['id']}", copy_id=copy["id"], post=None,
                   thumb_url=f"/media/copy/{copy['id']}/thumb" if thumb else None)


def _pixels(member, hashes):
    """Total pixels of a member's images, or None unless every image item's
    size is known (videos are not measured, so they do not count)."""
    total = 0
    for i in member["items"]:
        if i["kind"] != "image":
            continue
        h = hashes.get(i["path"])
        if h is None or h[0] != i["size"] or h[4] is None:
            return None
        total += h[4]
    return total


def suggest(members, hashes=None):
    """The member to keep: kept decision, then more media, then the highest
    resolution (when known for every member), then the oldest saved, then
    the shortest path."""
    pixels = {m["id"]: _pixels(m, hashes) if hashes is not None else None for m in members}
    known = None not in pixels.values()
    return min(members, key=lambda m: (not m["kept"], -m["files"], -pixels[m["id"]] if known else 0,
                                       m["saved_at"] or 0, len(m["meta_path"]), m["meta_path"]))


def _finish(kind, members, differs, pending, hashes):
    keep = suggest(members, hashes)
    key = _key(members)
    return {
        "id": group_id(kind, key), "kind": kind, "key": key,
        "members": members,
        "identical": False if differs else None if pending else True,
        "pending": pending,
        "differs": differs,
        "suggested": keep["id"],
        "frees": sum(m["bytes"] for m in members if m is not keep),
        "bytes": sum(m["bytes"] for m in members),
    }


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------

def _hash_of(item, hashes):
    """(partial, full) of an item, or None until it is hashed at its recorded
    size (a row may hold only a perceptual hash)."""
    h = hashes.get(item["path"])
    if h is None or item["size"] is None or h[0] != item["size"] or h[2] is None:
        return None
    return h[2], h[3]


def same_file(a, b, hashes):
    """True, False, or None when not hashed far enough yet."""
    if a["size"] != b["size"]:
        return False
    ha, hb = _hash_of(a, hashes), _hash_of(b, hashes)
    if ha is None or hb is None:
        return None
    if ha[0] != hb[0]:
        return False
    if ha[1] is None or hb[1] is None:          # the whole files are next in the worker's queue
        return None
    return ha[1] == hb[1]


def _compare_copies(ref, copy, hashes):
    """(differences, pending) of a copy against the indexed post, item by item."""
    differs, pending = [], False
    mine = {i["idx"]: i for i in ref["items"]}
    theirs = {i["idx"]: i for i in copy["items"]}
    for idx in sorted(set(mine) | set(theirs)):
        a, b = mine.get(idx), theirs.get(idx)
        if b is None:
            differs.append({"member": copy["id"], "idx": idx, "reason": "missing"})
        elif a is None:
            differs.append({"member": copy["id"], "idx": idx, "reason": "extra"})
        elif a["size"] != b["size"]:
            differs.append({"member": copy["id"], "idx": idx, "reason": "size"})
        else:
            same = same_file(a, b, hashes)
            if same is None:
                pending = True
            elif not same:
                differs.append({"member": copy["id"], "idx": idx, "reason": "content"})
    return differs, pending


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

def _copies_groups(conn, hashes):
    by_post = {}
    for c in conn.execute("SELECT * FROM copies ORDER BY meta_path"):
        by_post.setdefault(c["post_id"], []).append(c)
    out = []
    for post_id, cps in by_post.items():
        row = conn.execute(f"{db._SELECT} WHERE p.id = ?", (post_id,)).fetchone()
        if row is None:                         # trashed since the scan: the copy becomes the post next scan
            continue
        ref = _post_member(conn, row, hashes)
        members = [ref] + [_copy_member(c, row, hashes) for c in cps]
        differs, pending = [], False
        for m in members[1:]:
            d, p = _compare_copies(ref, m, hashes)
            differs += d
            pending = pending or p
        out.append(_finish("copies", members, differs, pending, hashes))
    return out


def _content_groups(conn, hashes):
    # Posts sharing a file (same size and full sha1), joined into components.
    by_digest = {}
    for post_id, path, size in conn.execute("SELECT post_id, path, size FROM media WHERE missing = 0"):
        h = hashes.get(path)
        if h is not None and h[3] and h[0] == size:
            by_digest.setdefault((size, h[3]), set()).add(post_id)
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for posts in by_digest.values():
        if 1 < len(posts) <= MAX_SHARED:
            first, *rest = sorted(posts)
            for p in rest:
                parent[find(p)] = find(first)
    components = {}
    for p in parent:
        components.setdefault(find(p), []).append(p)

    out = []
    for ids in components.values():
        rows = [conn.execute(f"{db._SELECT} WHERE p.id = ?", (i,)).fetchone() for i in sorted(ids)]
        members = [_post_member(conn, r, hashes) for r in rows if r is not None]
        if len(members) < 2:
            continue
        digests = [{(i["size"], i["hash"]) for i in m["items"] if i["hash"]} for m in members]
        differs = []
        for n, m in enumerate(members):
            others = digests[:n] + digests[n + 1:]
            for i in m["items"]:
                if not i["hash"] or not all((i["size"], i["hash"]) in o for o in others):
                    differs.append({"member": m["id"], "idx": i["idx"], "reason": "only here"})
        out.append(_finish("content", members, differs, False, hashes))
    return out


def all_groups(conn, kind, include_dismissed=False, hashes=None):
    """Every group of a kind, biggest saving first. Uncached: resolve() calls
    it under the write lock to see the index as it is now."""
    hashes = _hashes(conn) if hashes is None else hashes
    groups = _copies_groups(conn, hashes) if kind == "copies" else _content_groups(conn, hashes)
    if not include_dismissed:
        dismissed = _dismissed(conn)
        groups = [g for g in groups if not is_dismissed(g["key"], dismissed)]
    groups.sort(key=lambda g: (-g["frees"], g["id"]))
    return groups


def groups(conn, kind):
    return db._memo(conn, ("duplicates", kind), lambda c: all_groups(c, kind))


def _public(g):
    members = [{k: v for k, v in m.items() if k != "items"} | {
        "items": [{k: v for k, v in i.items() if k != "poster_path"} for i in m["items"]]}
        for m in g["members"]]
    return {k: v for k, v in g.items() if k != "key"} | {"members": members}


def listing(conn, kind, offset=0, limit=50):
    gs = groups(conn, kind)
    return {
        "kind": kind,
        "total": len(gs),
        "identical": sum(1 for g in gs if g["identical"]),
        "pending": sum(1 for g in gs if g["pending"]),
        "frees": sum(g["frees"] for g in gs),
        "identical_frees": sum(g["frees"] for g in gs if g["identical"]),
        "dismissed": conn.execute("SELECT COUNT(*) FROM dismissed_duplicates WHERE kind = ?",
                                  (kind,)).fetchone()[0],
        "groups": [_public(g) for g in gs[offset:offset + limit]],
    }


# ---------------------------------------------------------------------------
# Resolving and dismissing
# ---------------------------------------------------------------------------

def find(conn, gid):
    for kind in KINDS:
        for g in groups(conn, kind):
            if g["id"] == gid:
                return g
    return None


def dismiss(conn, gid):
    """Mark a group "not a duplicate". False if there is no such group."""
    g = find(conn, gid)
    if g is None:
        return False
    conn.execute("INSERT OR REPLACE INTO dismissed_duplicates(key, kind, at) VALUES (?, ?, ?)",
                 (g["key"], g["kind"], int(time.time())))
    conn.commit()
    return True


def _check(g, keep, keepers, hashes):
    """Why this group cannot be resolved keeping ``keep`` right now, or None."""
    if g is None:
        return "this group changed since it was loaded; reload"
    member = next((m for m in g["members"] if m["id"] == keep), None)
    if member is None:
        return "the member to keep is not in this group"
    if g["pending"]:
        return "still being hashed; try again when hashing is done"
    if any(m["id"] in keepers for m in g["members"] if m is not member):
        return "a member to trash is the one kept in another group"
    for m in g["members"]:
        for i in m["items"]:
            now = hashing.stat(i["path"])
            if now is None:
                if m is member:
                    return f"a file of the member to keep is gone: {i['path']}"
                continue                        # already gone: nothing to move
            h = hashes.get(i["path"])
            if h is not None and (h[0], h[1]) != now:
                return "a file changed since it was hashed; wait for the next pass"
    return None


def resolve(choices, roots, data_dir):
    """Keep one member of each group and trash the others. ``choices`` is
    [(group id, member id to keep)]. Every group is rebuilt and checked under
    the same lock as the move (see _check); a group that fails is skipped
    whole."""
    skipped, planned, promote = [], [], []

    def pick(conn):
        hashes = _hashes(conn)
        current = {g["id"]: g for g in all_groups(conn, "copies", hashes=hashes)}
        if any(gid not in current for gid, _ in choices):     # content groups only when asked for
            current.update((g["id"], g) for g in all_groups(conn, "content", hashes=hashes))
        keepers = {keep for _, keep in choices}
        posts, copies = [], []
        for gid, keep in choices:
            g = current.get(gid)
            error = _check(g, keep, keepers, hashes)
            if error:
                skipped.append({"group": gid, "error": error})
                continue
            others = [m for m in g["members"] if m["id"] != keep]
            posts += [m["post_id"] for m in others if m["type"] == "post"]
            copies += [m["copy_id"] for m in others if m["type"] == "copy"]
            planned.append((gid, others))
            kept = next(m for m in g["members"] if m["id"] == keep)
            if kept["type"] == "copy":
                promote.append((kept["post_id"], kept["folder"], kept["kept"]))
        return posts, copies

    report = trash.delete([], [], roots, data_dir, pick=pick)
    report.pop("media", None)
    if "error" in report:
        return {**report, "resolved": [], "skipped": skipped}
    gone = set(report["posts"]) | {f"copy:{c}" for c in report["copies"]}
    resolved = [gid for gid, others in planned if all(m["id"] in gone for m in others)]

    # A kept copy whose post went to the trash becomes the post now, not at
    # the next scan, and takes the post's decision with it.
    moved = [(pid, folder, kept) for pid, folder, kept in promote if pid in report["posts"]]
    if moved:
        scanner.index_dirs(roots, [folder for _, folder, _ in moved])
        conn = db.connect()
        db.set_decision(conn, [pid for pid, _, kept in moved if kept], "keep", int(time.time()))
    return {**report, "ok": bool(resolved) or not (skipped or report["errors"]),
            "resolved": resolved, "skipped": skipped}
