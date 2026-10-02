"""New posts: indexed after the last time the user looked.

One high-water mark, ``seen_at`` (unix time, table seen_at, user data
mirrored by userdata.py), against each post's ``first_seen``: when the index
first had it (db.upsert_post; never posted_at, which says nothing about
when the post arrived). "Mark all seen" moves the mark, never backwards.
"""
import time

import db
import userdata


def seen_at(conn):
    row = conn.execute("SELECT at FROM seen_at WHERE id = 1").fetchone()
    return row[0] if row else None


def ensure(conn):
    """Startup, after userdata.restore_all: a database without a mark (new,
    and no seen_at.json to restore it from) starts at now, so nothing it
    already has is new."""
    if seen_at(conn) is None:
        with conn:
            conn.execute("INSERT OR IGNORE INTO seen_at(id, at) VALUES (1, ?)", (int(time.time()),))
        userdata.changed("seen_at")


def mark_seen(conn, at=None):
    """Move the mark to ``at`` (default now, never later than now), unless it
    is already past it. Returns the mark."""
    now = int(time.time())
    at = now if at is None else min(int(at), now)
    with conn:
        conn.execute("INSERT INTO seen_at(id, at) VALUES (1, ?) "
                     "ON CONFLICT(id) DO UPDATE SET at = MAX(at, excluded.at)", (at,))
    userdata.changed("seen_at")
    return seen_at(conn)


def summary(conn):
    """{count, since, by_person: [{id, name, count}], by_account: [{platform,
    id, handle, person, count}]}, most new posts first. An account is as on
    the Creators page: folder-name aliases count for the id they stand for.
    Posts without an author count in ``count`` only."""
    since = seen_at(conn)
    rows = conn.execute(f"SELECT p.platform, p.author_id, COUNT(*) FROM posts p WHERE {db.NEW} "
                        "GROUP BY 1, 2").fetchall()
    alias, accounts = db.aliases(conn), db.accounts(conn)
    count, by_account, by_person = 0, {}, {}
    for platform, aid, n in rows:
        count += n
        if aid is None:
            continue
        key = (platform, alias.get((platform, aid), aid))
        a = accounts.get(key) or accounts.get((platform, aid))
        if a is None:
            continue
        key = (a["platform"], a["id"])
        if key not in by_account:
            by_account[key] = {"platform": a["platform"], "id": a["id"], "handle": a["handle"],
                               "person": a["person"]["id"] if a["person"] else None, "count": 0}
        by_account[key]["count"] += n
        if a["person"]:
            p = by_person.setdefault(a["person"]["id"], {**a["person"], "count": 0})
            p["count"] += n
    order = lambda r: (-r["count"], str(r.get("handle") or r.get("name") or ""))     # noqa: E731
    return {"count": count, "since": since, "by_person": sorted(by_person.values(), key=order),
            "by_account": sorted(by_account.values(), key=order)}
