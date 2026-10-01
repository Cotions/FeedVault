"""Tags: the user's own ways to sort what they keep.

User data, like review decisions: never touched by a rescan and mirrored to
JSON by userdata.py. Rows are keyed by post id and outlive the post's index
row, so a post moved to the trash and restored (or replaced by a duplicate
copy of itself) comes back with them. Rows of a post that is gone for good
are dropped when the trash is emptied or purged (forget_gone).
"""
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
    src = conn.execute("SELECT id FROM tags WHERE name = ?", (old,)).fetchone()
    if src is None:
        return None
    dst = conn.execute("SELECT id FROM tags WHERE name = ?", (new,)).fetchone()
    with conn:
        if dst is not None and dst[0] != src[0]:
            conn.execute("INSERT OR IGNORE INTO post_tags(post_id, tag_id, at) "
                         "SELECT post_id, ?, at FROM post_tags WHERE tag_id = ?", (dst[0], src[0]))
            conn.execute("DELETE FROM post_tags WHERE tag_id = ?", (src[0],))
            conn.execute("DELETE FROM tags WHERE id = ?", (src[0],))
            name = conn.execute("SELECT name FROM tags WHERE id = ?", (dst[0],)).fetchone()[0]
            return {"name": name, "merged": True}
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new, src[0]))
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
# Posts gone for good
# ---------------------------------------------------------------------------

def forget_gone(conn, trashed):
    """Drop the rows of posts that are neither in the index nor among
    ``trashed`` (post ids still in some trash manifest). Called after the
    trash is emptied or purged, under db.write_lock. Returns the user data
    tables that changed."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS trashed_posts (id TEXT PRIMARY KEY)")
    with conn:
        conn.execute("DELETE FROM temp.trashed_posts")
        conn.executemany("INSERT OR IGNORE INTO temp.trashed_posts(id) VALUES (?)", [(p,) for p in trashed])
        n = conn.execute("DELETE FROM post_tags WHERE post_id NOT IN (SELECT id FROM posts) "
                         "AND post_id NOT IN (SELECT id FROM temp.trashed_posts)").rowcount
        conn.execute("DELETE FROM temp.trashed_posts")
    return ["post_tags"] if n else []
