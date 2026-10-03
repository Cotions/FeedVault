"""Notifications: one entry for each sync that brought new posts or failed.

A sync's end (sync._ended) adds it, manual or scheduled alike: "12 new
posts from X", "X: account not found". A scheduled sync that fails the way
the one before it did (the same health state, health.py) adds none, so a
source retried every few hours by the scheduler stays one entry until it
changes. Kept in the database (table notifications), the newest KEPT only;
not user data: like the jobs list, it is what happened, not a choice.

The text is built from handles and tool output: it is scrubbed
(health.scrub) and the dashboard shows it as text, never HTML. A "new"
entry opens the posts its sync indexed (db.NOTIFIED), whatever was marked
seen since.
"""
import time

import health

KEPT = 200
TEXT_MAX = 200
FAILED = {"rate_limited": "rate limited", "private": "private profile", **health.BLOCKING}
_COLUMNS = ("id", "at", "kind", "text", "job_id", "source_id", "person_id", "platform", "author_id", "state",
            "count", "scheduled", "read")


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def new_text(count, who):
    return health.scrub(f"{plural(count, 'new post')} from {who}", TEXT_MAX)


def failed_text(who, state, message):
    """"<who>: account not found", or the job's message for a failure the
    health states do not name."""
    return health.scrub(f"{who}: {FAILED.get(state) or message or 'sync failed'}", TEXT_MAX)


def add(conn, kind, text, *, job=None, source=None, person=None, account=None, state=None, count=0,
        folder=None, seen=None, scheduled=False, at=None):
    """Add an entry, prune past KEPT. ``account``: (platform, id) or None;
    ``seen``: (from, to), the first_seen range of the posts it brought.
    Returns its id."""
    platform, author_id = account or (None, None)
    seen_from, seen_to = seen or (None, None)
    with conn:
        cur = conn.execute(
            "INSERT INTO notifications(at, kind, text, job_id, source_id, person_id, platform, author_id, state, "
            "count, folder, seen_from, seen_to, scheduled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()) if at is None else at, kind, health.scrub(text, TEXT_MAX) or "", job, source,
             person, platform, author_id, state, count, folder and folder.rstrip("/"), seen_from, seen_to,
             int(bool(scheduled))))
        conn.execute("DELETE FROM notifications WHERE id <= ?", (cur.lastrowid - KEPT,))
    return cur.lastrowid


def unread(conn):
    """(how many entries are unread, the newest entry's id or None): for
    every jobs poll."""
    n = conn.execute("SELECT COUNT(*) FROM notifications WHERE read = 0").fetchone()[0]
    return n, conn.execute("SELECT MAX(id) FROM notifications").fetchone()[0]


def listing(conn, limit=KEPT):
    """{unread, latest, entries: newest first}."""
    rows = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM notifications ORDER BY id DESC LIMIT ?",
                        (limit,)).fetchall()
    entries = [{**{k: r[k] for k in _COLUMNS if k not in ("platform", "author_id")},
                "account": {"platform": r["platform"], "id": r["author_id"]} if r["author_id"] else None,
                "scheduled": bool(r["scheduled"]), "read": bool(r["read"])} for r in rows]
    n, latest = unread(conn)
    return {"unread": n, "latest": latest, "entries": entries}


def read(conn, upto=None):
    """Mark the entries up to ``upto`` (an id; default all) read. Returns
    how many were unread."""
    with conn:
        return conn.execute("UPDATE notifications SET read = 1 WHERE read = 0 AND id <= ?",
                            (2**63 - 1 if upto is None else upto,)).rowcount
