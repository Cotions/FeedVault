"""New posts: indexed after the last time the user looked.

One high-water mark, ``seen_at`` (unix time, table seen_at, user data
mirrored by userdata.py), against each post's ``first_seen``: when the index
first had it (db.upsert_post; never posted_at, which says nothing about
when the post arrived). "Mark all seen" moves the mark, never backwards.

"Mark seen" on a person or on an account not linked to one sets a mark of
its own for each of its accounts (seen_marks, user data too, by platform
and account id so a rebuilt index keeps them). A post is new when it came
after both the global mark and its account's mark. An account linked to a
person later keeps the marks it had; the person's next "Mark seen" covers it.
"""
import time

import db
import people
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


def account_keys(conn, platform, author_id):
    """{(platform, id)} of one account: the id it stands for (an alias gives
    its account's) and every folder-name alias of it."""
    platform, aid = people.canonical(conn, platform, author_id)
    return {(platform, aid), *((platform, r[0]) for r in conn.execute(
        "SELECT alias_id FROM account_aliases WHERE platform = ? AND author_id = ?", (platform, aid)))}


def mark_seen(conn, at=None, person=None, account=None):
    """Move the global mark to ``at`` (default now, never later than now),
    unless it is already past it. Returns the mark.

    With ``person`` (an id) or ``account`` ((platform, id)), only the posts
    of that person's accounts, or of that account, stop being new: their
    own mark moves instead (never backwards either), and that is returned."""
    now = int(time.time())
    at = now if at is None else min(int(at), now)
    if person is None and account is None:
        with conn:
            conn.execute("INSERT INTO seen_at(id, at) VALUES (1, ?) "
                         "ON CONFLICT(id) DO UPDATE SET at = MAX(at, excluded.at)", (at,))
            # Marks the global one has passed say nothing any more.
            gone = conn.execute("DELETE FROM seen_marks WHERE at <= (SELECT at FROM seen_at WHERE id = 1)").rowcount
        userdata.changed("seen_at")
        if gone:
            userdata.changed("seen_marks")
        return seen_at(conn)
    keys = people.account_set(conn, person) if person is not None else account_keys(conn, *account)
    if keys and at > (seen_at(conn) or 0):
        with conn:
            conn.executemany("INSERT INTO seen_marks(platform, author_id, at) VALUES (?, ?, ?) "
                             "ON CONFLICT(platform, author_id) DO UPDATE SET at = MAX(at, excluded.at)",
                             [(p, a, at) for p, a in sorted(keys)])
        userdata.changed("seen_marks")
    return at


def count(conn):
    """(how many posts are new, the newest one's first_seen or None): cheap
    (posts_first_seen), for every jobs poll. "Mark all seen" sends that
    first_seen back as ``at``, so what was indexed since the count stays new."""
    n, newest = conn.execute(f"SELECT COUNT(*), MAX(p.first_seen) FROM posts p WHERE {db.NEW}").fetchone()
    return n, newest


def summary(conn):
    """{count, since, by_person: [{id, name, count, until}], by_account:
    [{platform, id, handle, person, count, until}]}, most new posts first.
    An account is as on the Creators page: folder-name aliases count for the
    id they stand for. Posts without an author count in ``count`` only.
    ``until``: the newest one's first_seen, for a "Mark seen" of that row."""
    since = seen_at(conn)
    rows = conn.execute(f"SELECT p.platform, p.author_id, COUNT(*), MAX(p.first_seen) FROM posts p "
                        f"WHERE {db.NEW} GROUP BY 1, 2").fetchall()
    alias, accounts = db.aliases(conn), db.accounts(conn)
    count, by_account, by_person = 0, {}, {}
    for platform, aid, n, until in rows:
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
                               "person": a["person"]["id"] if a["person"] else None, "count": 0, "until": 0}
        targets = [by_account[key]]
        if a["person"]:
            targets.append(by_person.setdefault(a["person"]["id"], {**a["person"], "count": 0, "until": 0}))
        for r in targets:
            r["count"] += n
            r["until"] = max(r["until"], until)
    order = lambda r: (-r["count"], str(r.get("handle") or r.get("name") or ""))     # noqa: E731
    return {"count": count, "since": since, "by_person": sorted(by_person.values(), key=order),
            "by_account": sorted(by_account.values(), key=order)}
