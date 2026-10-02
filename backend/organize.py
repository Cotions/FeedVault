"""Tags and collections: the user's own ways to sort what they keep.

User data, like review decisions: never touched by a rescan and mirrored to
JSON by userdata.py. Rows are keyed by post id and outlive the post's index
row, so a post moved to the trash and restored (or replaced by a duplicate
copy of itself) comes back with them. Rows of a post are dropped when its
trash entries are deleted for good and it is not indexed (forget_gone).
"""
import db

MAX_POSTS = 5000                # posts per bulk call
MAX_NAME = 64


def clean_name(name):
    """A tag name with its spaces collapsed, or None when it cannot be one."""
    if not isinstance(name, str):
        return None
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME or '"' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None
    return name


def _existing(conn, post_ids):
    """The ids among ``post_ids`` that are in the index, in the order given."""
    ids = list(dict.fromkeys(post_ids))
    if not ids:
        return []
    found = {r[0] for r in conn.execute(
        f"SELECT id FROM posts WHERE id IN ({', '.join('?' for _ in ids)})", ids)}
    return [i for i in ids if i in found]


def _tag_id(conn, name):
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def tags(conn):
    """Every tag with the number of indexed posts that have it."""
    return [{"name": r[0], "color": r[1], "count": r[2]} for r in conn.execute("""
        SELECT t.name, t.color, COUNT(p.id) AS n
        FROM tags t
        LEFT JOIN post_tags pt ON pt.tag_id = t.id
        LEFT JOIN posts p ON p.id = pt.post_id
        GROUP BY t.id
        ORDER BY n DESC, t.name COLLATE NOCASE""")]


def apply(conn, post_ids, add, remove, now):
    """Add and remove tags (clean names) on the indexed posts among ``post_ids``.
    Missing tags in ``add`` are created."""
    ids = _existing(conn, post_ids)
    out = {"posts": ids, "added": 0, "removed": 0, "created": []}
    if not ids:
        return out                      # no tag made for no post
    with conn:
        for name in dict.fromkeys(add):
            tid = _tag_id(conn, name)
            if tid is None:
                tid = conn.execute("INSERT INTO tags(name, created_at) VALUES (?, ?)", (name, now)).lastrowid
                out["created"].append(name)
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO post_tags(post_id, tag_id, at) VALUES (?, ?, ?)",
                             [(i, tid, now) for i in ids])
            out["added"] += conn.total_changes - before
        for name in dict.fromkeys(remove):
            tid = _tag_id(conn, name)
            if tid is None:
                continue
            before = conn.total_changes
            conn.executemany("DELETE FROM post_tags WHERE post_id = ? AND tag_id = ?", [(i, tid) for i in ids])
            out["removed"] += conn.total_changes - before
    return out


def rename(conn, old, new):
    """Rename tag ``old`` to ``new``, merging into ``new`` when that is
    another tag already. None when ``old`` does not exist."""
    src = _tag_id(conn, old)
    if src is None:
        return None
    dst = _tag_id(conn, new)
    with conn:
        if dst is not None and dst != src:
            conn.execute("INSERT OR IGNORE INTO post_tags(post_id, tag_id, at) "
                         "SELECT post_id, ?, at FROM post_tags WHERE tag_id = ?", (dst, src))
            conn.execute("DELETE FROM post_tags WHERE tag_id = ?", (src,))
            conn.execute("DELETE FROM tags WHERE id = ?", (src,))
            name = conn.execute("SELECT name FROM tags WHERE id = ?", (dst,)).fetchone()[0]
            return {"name": name, "merged": True}
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new, src))
    return {"name": new, "merged": False}


def delete(conn, name):
    """Delete a tag from every post. The number of posts it was on, or None
    when there is no such tag."""
    tid = _tag_id(conn, name)
    if tid is None:
        return None
    with conn:
        n = conn.execute("DELETE FROM post_tags WHERE tag_id = ?", (tid,)).rowcount
        conn.execute("DELETE FROM tags WHERE id = ?", (tid,))
    return n


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

_COUNT = "(SELECT COUNT(*) FROM collection_posts cp JOIN posts p ON p.id = cp.post_id WHERE cp.collection_id = c.id)"
# The chosen cover if it is indexed and still in the collection, else the first indexed post.
_COVER = """COALESCE(
    (SELECT p.id FROM collection_posts cp JOIN posts p ON p.id = cp.post_id
      WHERE cp.collection_id = c.id AND cp.post_id = c.cover_post),
    (SELECT p.id FROM collection_posts cp JOIN posts p ON p.id = cp.post_id
      WHERE cp.collection_id = c.id ORDER BY cp.position LIMIT 1))"""


def _collection(conn, row):
    return {"id": row["id"], "name": row["name"], "count": row["count"], "created_at": row["created_at"],
            "cover_post": row["cover_post"],
            "cover": db._cover(conn, row["cover_id"]) if row["cover_id"] else None}


def _collection_rows(conn, where="", args=()):
    return conn.execute(f"SELECT c.*, {_COUNT} AS count, {_COVER} AS cover_id FROM collections c "
                        f"{where} ORDER BY c.position, c.id", args).fetchall()


def collections(conn):
    return [_collection(conn, r) for r in _collection_rows(conn)]


def collection(conn, cid):
    rows = _collection_rows(conn, "WHERE c.id = ?", (cid,))
    return _collection(conn, rows[0]) if rows else None


def _name_taken(conn, name, cid=None):
    return conn.execute("SELECT 1 FROM collections WHERE name = ? AND id IS NOT ?", (name, cid)).fetchone() is not None


def create_collection(conn, name, now):
    """The new collection, or None when the name is taken."""
    if _name_taken(conn, name):
        return None
    with conn:
        cid = conn.execute(
            "INSERT INTO collections(name, created_at, position) "
            "VALUES (?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM collections))", (name, now)).lastrowid
    return collection(conn, cid)


def rename_collection(conn, cid, name):
    """False when another collection has that name."""
    if _name_taken(conn, name, cid):
        return False
    with conn:
        conn.execute("UPDATE collections SET name = ? WHERE id = ?", (name, cid))
    return True


def delete_collection(conn, cid):
    """The number of posts it held (the posts stay)."""
    with conn:
        n = conn.execute("DELETE FROM collection_posts WHERE collection_id = ?", (cid,)).rowcount
        conn.execute("DELETE FROM collections WHERE id = ?", (cid,))
    return n


def add_posts(conn, cid, post_ids, now):
    """Append the indexed posts that are not in the collection yet, in the
    order given. Returns their ids."""
    have = {r[0] for r in conn.execute("SELECT post_id FROM collection_posts WHERE collection_id = ?", (cid,))}
    ids = [i for i in _existing(conn, post_ids) if i not in have]
    with conn:
        last = conn.execute("SELECT COALESCE(MAX(position), 0) FROM collection_posts WHERE collection_id = ?",
                            (cid,)).fetchone()[0]
        conn.executemany("INSERT INTO collection_posts(collection_id, post_id, position, at) VALUES (?, ?, ?, ?)",
                         [(cid, pid, last + n, now) for n, pid in enumerate(ids, start=1)])
    return ids


def remove_posts(conn, cid, post_ids):
    with conn:
        before = conn.total_changes
        conn.executemany("DELETE FROM collection_posts WHERE collection_id = ? AND post_id = ?",
                         [(cid, p) for p in dict.fromkeys(post_ids)])
        n = conn.total_changes - before
        conn.execute("UPDATE collections SET cover_post = NULL WHERE id = ? AND cover_post NOT IN "
                     "(SELECT post_id FROM collection_posts WHERE collection_id = ?)", (cid, cid))
    return n


def reorder(conn, cid, post_ids):
    """Put the given posts of the collection in this order, in the places
    they held between them; the others do not move."""
    held = dict(conn.execute("SELECT post_id, position FROM collection_posts WHERE collection_id = ?", (cid,)))
    ids = [p for p in dict.fromkeys(post_ids) if p in held]
    with conn:
        conn.executemany("UPDATE collection_posts SET position = ? WHERE collection_id = ? AND post_id = ?",
                         [(pos, cid, pid) for pos, pid in zip(sorted(held[p] for p in ids), ids)])


def set_cover(conn, cid, post_id):
    """False when the post is not in the collection."""
    if post_id is not None and conn.execute(
            "SELECT 1 FROM collection_posts WHERE collection_id = ? AND post_id = ?", (cid, post_id)).fetchone() is None:
        return False
    with conn:
        conn.execute("UPDATE collections SET cover_post = ? WHERE id = ?", (post_id, cid))
    return True


def collection_posts(conn, cid, offset=0, limit=60):
    """(total, [post summary]) of the indexed posts, in the collection's order."""
    total = conn.execute("SELECT COUNT(*) FROM collection_posts cp JOIN posts p ON p.id = cp.post_id "
                         "WHERE cp.collection_id = ?", (cid,)).fetchone()[0]
    rows = conn.execute(f"{db._SELECT} JOIN collection_posts cp ON cp.post_id = p.id "
                        "WHERE cp.collection_id = ? ORDER BY cp.position, p.id LIMIT ? OFFSET ?",
                        (cid, limit, offset)).fetchall()
    return total, db.summaries(conn, rows)


def carry_over(conn, pairs):
    """Give each (source, target) post pair's target the source's tags and
    collection places. Returns the user data tables that changed."""
    changed = set()
    with conn:
        for src, dst in pairs:
            before = conn.total_changes
            conn.execute("INSERT OR IGNORE INTO post_tags(post_id, tag_id, at) "
                         "SELECT ?, tag_id, at FROM post_tags WHERE post_id = ?", (dst, src))
            if conn.total_changes > before:
                changed.add("post_tags")
            before = conn.total_changes
            conn.execute("INSERT OR IGNORE INTO collection_posts(collection_id, post_id, position, at) "
                         "SELECT collection_id, ?, position, at FROM collection_posts WHERE post_id = ?", (dst, src))
            if conn.total_changes > before:
                changed.add("collection_posts")
    return sorted(changed)


# ---------------------------------------------------------------------------
# Posts gone for good
# ---------------------------------------------------------------------------

def forget_gone(conn, post_ids):
    """Drop the rows of the posts among ``post_ids`` (none of them left in any
    trash manifest) that are not in the index either. Called after the trash
    is emptied or purged, under db.write_lock. Returns the user data tables
    that changed."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS gone_posts (id TEXT PRIMARY KEY)")
    with conn:
        conn.execute("DELETE FROM temp.gone_posts")
        conn.executemany("INSERT OR IGNORE INTO temp.gone_posts(id) VALUES (?)", [(p,) for p in post_ids])
        conn.execute("DELETE FROM temp.gone_posts WHERE id IN (SELECT id FROM posts)")
        gone = "IN (SELECT id FROM temp.gone_posts)"
        changed = []
        if conn.execute(f"DELETE FROM post_tags WHERE post_id {gone}").rowcount:
            changed.append("post_tags")
        if conn.execute(f"DELETE FROM collection_posts WHERE post_id {gone}").rowcount:
            changed.append("collection_posts")
        if conn.execute(f"UPDATE collections SET cover_post = NULL WHERE cover_post {gone}").rowcount:
            changed.append("collections")
        if conn.execute(f"DELETE FROM saved_posts WHERE post_id {gone}").rowcount:
            changed.append("saved_posts")
        conn.execute("DELETE FROM temp.gone_posts")
    return changed
