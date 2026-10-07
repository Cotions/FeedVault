"""Duplicates: posts, or copies of one post, holding the same or similar files.

Three kinds of group:

- ``copies``: an indexed post and the extra copies of it the scanner found in
  other folders (db.save_copies). Items are compared by position: size, then
  the partial hash (first and last MiB), then a full sha1, which the worker
  computes only for items that got that far.
- ``content``: different posts (a repost saved under another id) that share
  at least one file, by size and full sha1. Posts linked through any shared
  file form one group.
- ``similar``: different posts with a picture that looks the same (a resized
  or recompressed repost): perceptual hashes (dHash) at most ``threshold``
  bits apart, between posts not already in one content group. Never called
  identical: each one is resolved by hand.

Hashes come from hashing.py; until a file is hashed its group is pending.
Nothing here reads a media file: resolve() only stats them, to refuse when a
file changed since it was hashed.
"""
import hashlib
import heapq
import json
import os
import threading
import time
from functools import lru_cache

import db
import hashing
import organize
import scanner
import thumbs
import trash

KINDS = ("copies", "content", "similar")
# A file shared by more posts than this (a placeholder image, a watermark
# card) does not link them into one group: they are not reposts of each other.
# The same goes for a picture near that many others.
MAX_SHARED = 20
# Near matches: how many of the 64 dHash bits may differ, by default (config
# "similar_threshold") and at most. Past 10 the lookup slows down (see
# near_pairs) and unrelated pictures start to match.
SIMILAR_DEFAULT = 6
SIMILAR_MAX = 10
# A dHash with this few bits set, or unset, is a flat picture (a solid
# colour, a smooth gradient, a black frame): those match each other whatever
# they show, so they link nothing.
FLAT_BITS = 6


def group_id(kind, key):
    return hashlib.sha1(f"{kind}\0{key}".encode("utf-8", "surrogateescape")).hexdigest()[:20]


def _key(members):
    """What a dismissal is stored under: the sorted post ids, and the
    metadata paths of copies (copy ids do not survive rebuilding the index)."""
    return json.dumps(sorted(m["post_id"] if m["type"] == "post" else m["meta_path"] for m in members))


_HASH_COLUMNS = "path, size, mtime_ns, partial, full, width, height, dhash"


def _hash_row(r):
    return (r[1], r[2], r[3], r[4], r[5] * r[6] if r[5] and r[6] else None, r[5] or None, r[6] or None,
            hashing.from_db(r[7]))


class _Hashes:
    """path -> (size, mtime_ns, partial, full, pixels or None, width, height,
    dhash), read from media_hash as asked for, each path once: groups need
    the few files of their members, and reading every row up front took
    most of a listing's time on a large archive (#perf). A path, once read,
    keeps its value for the life of the object, so a group and its checks
    (resolve) see the same hashes."""

    def __init__(self, conn):
        self._conn = conn
        self._rows = {}

    def get(self, path, default=None):
        if path not in self._rows:
            r = self._conn.execute(f"SELECT {_HASH_COLUMNS} FROM media_hash WHERE path = ?", (path,)).fetchone()
            self._rows[path] = _hash_row(r) if r is not None else None
        h = self._rows[path]
        return default if h is None else h


def _hashes(conn):
    return _Hashes(conn)


def _dismissed(conn, kind):
    """Dismissals of one kind: "these only look alike" says nothing about
    the same posts sharing a file."""
    out = []
    for (key,) in conn.execute("SELECT key FROM dismissed_duplicates WHERE kind = ?", (kind,)):
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
        h = h if h is not None and h[0] == m["size"] else None
        media_id = m["id"] if "id" in m.keys() else None          # copies' files are not media rows
        out.append({"idx": m["idx"], "kind": m["kind"], "size": m["size"], "path": m["path"],
                    "poster_path": m["poster_path"],
                    "hash": h and h[3], "width": h and h[5], "height": h and h[6],
                    "url": media_id and f"/media/{media_id}", "thumb_url": media_id and f"/media/{media_id}/thumb"})
    return out


def _member(kind, post_id, meta_path, items, saved_at, kept, **extra):
    return {"type": kind, "post_id": post_id, "folder": os.path.dirname(meta_path), "meta_path": meta_path,
            "items": items, "paths": [i["path"] for i in items], "files": len(items),
            "bytes": sum(i["size"] or 0 for i in items), "saved_at": saved_at, "kept": kept,
            "posted_at": None, "match": None, **extra}


def _post_member(conn, row, hashes):
    media = conn.execute("SELECT * FROM media WHERE post_id = ? AND missing = 0 ORDER BY idx",
                         (row["id"],)).fetchall()
    summary = db.summary(conn, row)
    return _member("post", row["id"], row["meta_path"], _items(media, hashes), row["saved_at"],
                   row["decision"] == "keep", id=row["id"], post=summary, posted_at=row["posted_at"],
                   author=row["author_id"] or row["author_handle"] or os.path.dirname(row["meta_path"]),
                   thumb_url=summary["cover"]["url"] if summary["cover"] and summary["cover"].get("poster", True)
                   else None)


def _set_match(member, idxs):
    """Point a content or similar member at the first of its items that
    matched another member: its resolution and size are what the keeper rule
    compares, and its thumbnail is the one shown."""
    item = next((i for i in member["items"] if i["idx"] in idxs), None)
    if item is not None:
        member["match"] = item["idx"]
        member["thumb_url"] = item["thumb_url"]


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
    """The copy to keep: kept decision, then more media, then the highest
    resolution (when known for every member), then the oldest saved, then
    the shortest path."""
    pixels = {m["id"]: _pixels(m, hashes) if hashes is not None else None for m in members}
    known = None not in pixels.values()
    return min(members, key=lambda m: (not m["kept"], -m["files"], -pixels[m["id"]] if known else 0,
                                       m["saved_at"] or 0, len(m["meta_path"]), m["meta_path"]))


def _matched(member):
    return next((i for i in member["items"] if i["idx"] == member["match"]), None)


def suggest_original(members):
    """The post to keep among different posts (a repost, a re-upload): kept
    decision, then the earliest posted (the original, whoever saved it
    first), then the highest resolution of the matching picture (when known
    for every member), then the largest file, then the shortest path."""
    def pixels(m):
        i = _matched(m)
        return i["width"] * i["height"] if i and i["width"] and i["height"] else None
    px = {m["id"]: pixels(m) for m in members}
    known = None not in px.values()
    return min(members, key=lambda m: (not m["kept"], m["posted_at"] is None, m["posted_at"] or 0,
                                       -px[m["id"]] if known else 0, -((_matched(m) or {}).get("size") or 0),
                                       len(m["meta_path"]), m["meta_path"]))


def _finish(kind, members, differs, pending, hashes, **extra):
    keep = suggest(members, hashes) if kind == "copies" else suggest_original(members)
    key = _key(members)
    return {
        "id": group_id(kind, key), "kind": kind, "key": key,
        "members": members,
        "identical": False if differs or kind == "similar" else None if pending else True,
        "pending": pending,
        "differs": differs,
        "suggested": keep["id"],
        "frees": sum(m["bytes"] for m in members if m is not keep),
        "bytes": sum(m["bytes"] for m in members),
        # Posts by different accounts: one is likely a repost of the other.
        "repost": kind != "copies" and len({m["author"] for m in members}) > 1,
        **extra,
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


def _components(links):
    """Union-find: the connected components of an iterable of (a, b) links."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in links:
        parent[find(b)] = find(a)
    out = {}
    for x in parent:
        out.setdefault(find(x), []).append(x)
    return list(out.values())


# The files of the digests more than one file has (by media_hash_full), and
# their posts: (post id, size, full sha1).
CONTENT_SQL = """
    SELECT m.post_id, m.size, h.full
    FROM (SELECT full, size FROM media_hash WHERE full IS NOT NULL AND full != ''
          GROUP BY full, size HAVING COUNT(*) > 1) d
    JOIN media_hash h ON h.full = d.full AND h.size = d.size
    CROSS JOIN media m ON m.path = h.path
    WHERE m.missing = 0 AND m.size = h.size"""


def _content_components(conn, hashes=None):
    """Lists of posts sharing a file (same size and full sha1), joined: read
    from the digests more than one file has (CONTENT_SQL), not from every
    media row."""
    by_digest = {}
    for post_id, size, full in conn.execute(CONTENT_SQL):
        by_digest.setdefault((size, full), set()).add(post_id)
    links = []
    for posts in by_digest.values():
        if 1 < len(posts) <= MAX_SHARED:
            first, *rest = sorted(posts)
            links += [(first, p) for p in rest]
    return _components(links)


def _content_groups(conn, hashes):
    out = []
    for ids in _content_components(conn, hashes):
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
            _set_match(m, {i["idx"] for i in m["items"] if i["hash"] and any((i["size"], i["hash"]) in o for o in others)})
        out.append(_finish("content", members, differs, False, hashes))
    return out


@lru_cache(maxsize=None)
def _masks(r):
    return [m for m in range(1 << 16) if m.bit_count() <= r]


# near_pairs' last answers, by threshold: (values, pairs). Every listing of
# similar groups asks again, after any change to the index (a decision, a
# tag: db._memo), with the same pictures; the pairs are a function of them.
_near = {}
_near_lock = threading.Lock()


def near_pairs(values, threshold, pause=None):
    """near_pairs_uncached(values, threshold, pause), the last answer for
    that threshold again when the values are the same. Do not modify it."""
    values = tuple(values)
    with _near_lock:
        hit = _near.get(threshold)
    if hit is not None and hit[0] == values:
        return hit[1]
    pairs = frozenset(near_pairs_uncached(values, threshold, pause))
    with _near_lock:
        _near[threshold] = (values, pairs)
    return pairs


# Buckets compared between two calls of near_pairs_uncached's ``pause``.
PAUSE_EVERY = 256


def near_pairs_uncached(values, threshold, pause=None):
    """Index pairs (i, j), i < j, of 64-bit values at most ``threshold`` bits
    apart. Multi-index hashing: split into four 16-bit bands, two values
    within t bits agree within t // 4 bits on at least one band, so only
    buckets whose band value is that close are compared. On ~86k hashes,
    about a second up to 7 bits, five at 10. ``pause()`` is called every
    PAUSE_EVERY buckets: the hashing worker waits there while a request is
    answered, rather than hold Python's lock for seconds."""
    r = threshold // 4
    masks = _masks(r)
    out = set()
    for shift in (0, 16, 32, 48):
        buckets = {}
        for n, v in enumerate(values):
            buckets.setdefault((v >> shift) & 0xFFFF, []).append(n)
        get = buckets.get
        for n, (band, mine) in enumerate(buckets.items()):
            if pause is not None and n % PAUSE_EVERY == 0:
                pause()
            for m in masks:
                other_band = band ^ m
                if other_band < band:
                    continue                          # each pair of buckets once
                theirs = get(other_band)
                if theirs is None:
                    continue
                for x, i in enumerate(mine):
                    vi = values[i]
                    for j in (mine[x + 1:] if theirs is mine else theirs):
                        if (vi ^ values[j]).bit_count() <= threshold:
                            out.add((i, j) if i < j else (j, i))
    return out


def _pictures(conn, hashes=None):
    """(post id, idx, dhash) of every image and video with a usable dHash,
    in media row order."""
    out = []
    for post_id, idx, value in conn.execute(
            "SELECT m.post_id, m.idx, h.dhash FROM media m CROSS JOIN media_hash h ON h.path = m.path "
            "WHERE m.missing = 0 AND m.kind IN ('image', 'video') AND h.size = m.size AND h.dhash IS NOT NULL "
            "ORDER BY m.id"):
        value = hashing.from_db(value)
        if FLAT_BITS < value.bit_count() < 64 - FLAT_BITS:
            out.append((post_id, idx, value))
    return out


def _near_links(conn, hashes, threshold, pause=None):
    """[(picture a, picture b, bits apart)] between different posts that are
    not already one content group, leaving out pictures near more than
    MAX_SHARED other posts. ``pause``: see near_pairs_uncached."""
    pics = _pictures(conn, hashes)
    content = {p: n for n, ids in enumerate(_content_components(conn, hashes)) for p in ids}
    links, near = [], {}
    for i, j in near_pairs([p[2] for p in pics], threshold, pause):
        a, b = pics[i], pics[j]
        if a[0] == b[0] or content.get(a[0], -1) == content.get(b[0], -2):
            continue
        links.append((a, b, (a[2] ^ b[2]).bit_count()))
        near.setdefault(a, set()).add(b[0])
        near.setdefault(b, set()).add(a[0])
    return [link for link in links if len(near[link[0]]) <= MAX_SHARED and len(near[link[1]]) <= MAX_SHARED]


def _stars(links):
    """Split posts linked by near pictures into groups around a centre, so
    every member looks like the centre itself. Connected components chain
    (A near B near C...) into groups of hundreds of unrelated pictures.
    Greedy: the post with the most unassigned neighbours takes them all,
    ties to the smaller id; repeat. -> [(centre, {member: bits from centre})]."""
    near = {}
    for a, b, d in links:
        for x, y in ((a[0], b[0]), (b[0], a[0])):
            mine = near.setdefault(x, {})
            mine[y] = min(mine.get(y, 64), d)
    out, taken = [], set()
    heap = [(-len(others), p) for p, others in near.items()]
    heapq.heapify(heap)
    while heap:
        n, c = heapq.heappop(heap)
        if c in taken:
            continue
        group = {q: d for q, d in near[c].items() if q not in taken}
        if len(group) != -n:                       # stale count: queue it again
            if group:
                heapq.heappush(heap, (-len(group), c))
            continue
        taken |= {c, *group}
        out.append((c, group))
    return out


def _similar_groups(conn, hashes, threshold):
    links = _near_links(conn, hashes, threshold)
    out = []
    for centre, near in _stars(links):
        ids = {centre, *near}
        matched = {}
        for a, b, _ in links:                      # the pictures that matched, within the group
            if a[0] in ids and b[0] in ids and centre in (a[0], b[0]):
                matched.setdefault(a[0], set()).add(a[1])
                matched.setdefault(b[0], set()).add(b[1])
        rows = [conn.execute(f"{db._SELECT} WHERE p.id = ?", (i,)).fetchone() for i in sorted(ids)]
        members = [_post_member(conn, r, hashes) for r in rows if r is not None]
        if len(members) < 2:
            continue
        differs = []
        for m in members:
            _set_match(m, matched[m["id"]])
            differs += [{"member": m["id"], "idx": i["idx"], "reason": "only here"}
                        for i in m["items"] if i["idx"] not in matched[m["id"]]]
        out.append(_finish("similar", members, differs, False, hashes, distance=max(near.values())))
    return out


def videos_to_measure(conn, pause=None):
    """Videos of posts in a content group or near another post at the
    loosest threshold, whose size is not known yet: hashing.py measures them
    with ffprobe, for the keeper rule. Videos in no group stay unmeasured,
    so after a scan that brought new pictures this compares them all again
    (seconds on a large archive): ``pause``, see near_pairs_uncached."""
    if not conn.execute("SELECT 1 FROM media m JOIN media_hash h ON h.path = m.path AND h.size = m.size "
                        "WHERE m.missing = 0 AND m.kind = 'video' AND h.width IS NULL LIMIT 1").fetchone():
        return []                                 # the usual case after the first pass: skip the grouping
    hashes = _hashes(conn)
    posts = {p for ids in _content_components(conn, hashes) for p in ids}
    posts |= {p[0] for link in _near_links(conn, hashes, SIMILAR_MAX, pause) for p in link[:2]}
    return sorted(path for post_id, path in conn.execute(
        "SELECT m.post_id, m.path FROM media m JOIN media_hash h ON h.path = m.path AND h.size = m.size "
        "WHERE m.missing = 0 AND m.kind = 'video' AND h.width IS NULL") if post_id in posts)


def all_groups(conn, kind, include_dismissed=False, hashes=None, threshold=SIMILAR_DEFAULT):
    """Every group of a kind, biggest saving first. Uncached: resolve() calls
    it under the write lock to see the index as it is now."""
    hashes = _hashes(conn) if hashes is None else hashes
    if kind == "copies":
        groups = _copies_groups(conn, hashes)
    elif kind == "content":
        groups = _content_groups(conn, hashes)
    else:
        groups = _similar_groups(conn, hashes, threshold)
    if not include_dismissed:
        dismissed = _dismissed(conn, kind)
        groups = [g for g in groups if not is_dismissed(g["key"], dismissed)]
    groups.sort(key=lambda g: (-g["frees"], g["id"]))
    return groups


def groups(conn, kind, threshold=SIMILAR_DEFAULT):
    threshold = threshold if kind == "similar" else None
    return db._memo(conn, ("duplicates", kind, threshold), lambda c: all_groups(c, kind, threshold=threshold))


def _public(g):
    members = [{k: v for k, v in m.items() if k != "items"} | {
        "items": [{k: v for k, v in i.items() if k != "poster_path"} for i in m["items"]]}
        for m in g["members"]]
    return {k: v for k, v in g.items() if k != "key"} | {"members": [
        {k: v for k, v in m.items() if k != "author"} for m in members]}


def listing(conn, kind, offset=0, limit=50, threshold=SIMILAR_DEFAULT):
    gs = groups(conn, kind, threshold)
    return {
        "kind": kind,
        "threshold": threshold if kind == "similar" else None,
        "total": len(gs),
        "reposts": sum(1 for g in gs if g["repost"]),
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

def find(conn, gid, threshold=SIMILAR_DEFAULT):
    for kind in KINDS:
        for g in groups(conn, kind, threshold):
            if g["id"] == gid:
                return g
    return None


def dismiss(conn, gid, threshold=SIMILAR_DEFAULT):
    """Mark a group "not a duplicate". False if there is no such group."""
    g = find(conn, gid, threshold)
    if g is None:
        return False
    conn.execute("INSERT OR REPLACE INTO dismissed_duplicates(key, kind, at) VALUES (?, ?, ?)",
                 (g["key"], g["kind"], int(time.time())))
    conn.commit()
    return True


def _dismissed_member(conn, entry):
    """One entry of a dismissal's key, as the Dismissed list shows it: a post
    (by id) or an extra copy (by metadata path), null fields once gone."""
    row = conn.execute(f"{db._SELECT} WHERE p.id = ?", (entry,)).fetchone()
    if row is not None:
        summary = db.summary(conn, row)
        cover = summary["cover"]
        return {"type": "post", "id": entry, "post": summary, "path": row["meta_path"],
                "thumb_url": cover["url"] if cover and cover.get("poster", True) else None}
    copy = conn.execute("SELECT * FROM copies WHERE meta_path = ?", (entry,)).fetchone()
    if copy is not None:
        media = sorted(json.loads(copy["media"]), key=lambda m: m["idx"])
        first = media[0] if media else None
        thumb = first and (first["kind"] == "image" or first.get("poster_path") or thumbs.have_ffmpeg())
        return {"type": "copy", "id": f"copy:{copy['id']}", "post": None, "path": entry,
                "thumb_url": f"/media/copy/{copy['id']}/thumb" if thumb else None}
    is_copy = os.path.isabs(entry)                # gone since: a copy's key is its absolute path
    return {"type": "copy" if is_copy else "post", "id": None if is_copy else entry, "post": None,
            "path": entry if is_copy else None, "thumb_url": None}


def dismissed(conn, kind=None):
    """Every dismissal (of one kind, or all), newest first. ``id`` is the
    group's id when it was dismissed, what undismiss() takes."""
    sql = "SELECT key, kind, at FROM dismissed_duplicates"
    rows = conn.execute(sql + " WHERE kind = ?" if kind else sql, (kind,) if kind else ()).fetchall()
    out = []
    for key, k, at in rows:
        try:
            entries = json.loads(key)
        except (ValueError, TypeError):
            entries = None
        if not isinstance(entries, list):
            continue
        out.append({"id": group_id(k, key), "kind": k, "at": at,
                    "members": [_dismissed_member(conn, e) for e in entries if isinstance(e, str)]})
    out.sort(key=lambda d: (-(d["at"] or 0), d["id"]))
    return out


def undismiss(conn, gid):
    """Forget a dismissal by its id: the group shows again. False if none."""
    for key, kind in conn.execute("SELECT key, kind FROM dismissed_duplicates").fetchall():
        if group_id(kind, key) == gid:
            conn.execute("DELETE FROM dismissed_duplicates WHERE key = ?", (key,))
            conn.commit()
            return True
    return False


def dismissed_copies(conn):
    """Metadata paths of extra copies whose copies group is dismissed (the
    Unmatched page has nothing to compare them with in Duplicates)."""
    dis = _dismissed(conn, "copies")
    if not dis:
        return set()
    by_post = {}
    for post_id, meta_path in conn.execute("SELECT post_id, meta_path FROM copies"):
        by_post.setdefault(post_id, []).append(meta_path)
    out = set()
    for post_id, paths in by_post.items():
        if is_dismissed(json.dumps(sorted([post_id, *paths])), dis):
            out.update(paths)
    return out


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


def resolve(choices, roots, data_dir, threshold=SIMILAR_DEFAULT):
    """Keep one member of each group and trash the others. ``choices`` is
    [(group id, member id to keep)]; similar groups (found at ``threshold``)
    only one at a time, never in bulk. Every group is rebuilt and checked
    under the same lock as the move (see _check); a group that fails is
    skipped whole."""
    skipped, planned, promote, carry = [], [], [], []

    def pick(conn):
        hashes = _hashes(conn)
        current = {g["id"]: g for g in all_groups(conn, "copies", hashes=hashes)}
        for kind in ("content", "similar"):                   # built only when asked for
            if any(gid not in current for gid, _ in choices):
                current.update((g["id"], g) for g in all_groups(conn, kind, hashes=hashes, threshold=threshold))
        keepers = {keep for _, keep in choices}
        posts, copies = [], []
        for gid, keep in choices:
            g = current.get(gid)
            error = _check(g, keep, keepers, hashes)
            if not error and g["kind"] == "similar" and len(choices) > 1:
                error = "similar groups are resolved one at a time"
            if error:
                skipped.append({"group": gid, "error": error})
                continue
            others = [m for m in g["members"] if m["id"] != keep]
            posts += [m["post_id"] for m in others if m["type"] == "post"]
            copies += [m["copy_id"] for m in others if m["type"] == "copy"]
            planned.append((gid, others))
            kept = next(m for m in g["members"] if m["id"] == keep)
            carry.extend((m["post_id"], kept["post_id"]) for m in others
                         if m["type"] == "post" and m["post_id"] != kept["post_id"])
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
    # The post kept from a content or similar group takes on the tags and collections of
    # the posts trashed for it (theirs stay, in case they are restored).
    carried = [(src, dst) for src, dst in carry if src in report["posts"]]
    report["carried"] = organize.carry_over(db.connect(), carried) if carried else []
    return {**report, "ok": bool(resolved) or not (skipped or report["errors"]),
            "resolved": resolved, "skipped": skipped}
