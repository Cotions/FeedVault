"""Don't sync (#189): a person, or an account, that FeedVault never
downloads from.

Set on a person it covers each of their accounts; set on an account it
covers that account (and its folder-name aliases), whoever it is linked to.
Off by default. While it is on:

- its sources stay stored but never sync: Sync is refused (sync.sync,
  NotSynced), Sync all and a person's Sync skip them (counted as
  ``not_synced``), and the scheduler passes them by;
- no source can be added for it (sources.create), and it gets no source
  suggestion (sources.suggestions);
- its posts stay in the library exactly as before.

Every sync kind (each tool's, and a source's own script) builds through
sync.synced_source, which refuses a source set to Don't sync: no route,
POST /api/jobs included, queues one. What stays allowed are the user's
deliberate one-off actions that are not a source's sync: saving one post
(POST /api/save) and running a script by hand with an explicit link or
target (POST /api/scripts/<id>/run).

Nothing starts syncing behind the user's back: an account that leaves a
person set to Don't sync (unlinked, linked to someone else, the person
deleted) gets the switch of its own, and merging people into one set to
it asks first (409 ``confirm``), as turning it on does.

User data (no_sync_people by person name, no_sync_accounts by platform and
account id), mirrored by userdata.py; a source's own state is
sources.not_synced. Another site never reaches the switch (app.py's
FOREIGN_ALLOWED does not list it).
"""
import time

import db
import people


def flags(conn):
    """({(platform, author id)} of every account not synced, on its own or
    through its person, aliases both ways; {person id} set to Don't sync)."""
    return ({(r[0], r[1]) for r in conn.execute(db.NO_SYNC)},
            {r[0] for r in conn.execute("SELECT person_id FROM no_sync_people")})


def listing(conn):
    """{people: [ids], accounts: [{platform, id}]}: what the switch is on
    for, as set (an account of a person set to it is not listed)."""
    return {"people": [r[0] for r in conn.execute("SELECT person_id FROM no_sync_people ORDER BY person_id")],
            "accounts": [{"platform": r[0], "id": r[1]} for r in conn.execute(
                "SELECT platform, author_id FROM no_sync_accounts ORDER BY platform, author_id")]}


def count_accounts(conn):
    """How many accounts have the switch of their own: compared before and
    after a change that may carry it (people._attach), for userdata."""
    return conn.execute("SELECT COUNT(*) FROM no_sync_accounts").fetchone()[0]


def is_on(conn, person=None, account=None):
    """Whether the switch is on for a person (an id) itself, or for an
    account ((platform, id)) itself, as stored (not through its person)."""
    if person is not None:
        return conn.execute("SELECT 1 FROM no_sync_people WHERE person_id = ?", (person,)).fetchone() is not None
    key = people.canonical(conn, *account)
    return conn.execute("SELECT 1 FROM no_sync_accounts WHERE platform = ? AND author_id = ?",
                        key).fetchone() is not None


def set_on(conn, on, person=None, account=None, now=None):
    """Turn the switch on (``on``) or off for a person (an id) or an account
    ((platform, id), stored as the id it stands for). Returns the userdata
    table that changed, or None. The caller notes userdata."""
    now = int(time.time()) if now is None else now
    with conn:
        if person is not None:
            name = "no_sync_people"
            if on:
                cur = conn.execute("INSERT OR IGNORE INTO no_sync_people(person_id, at) VALUES (?, ?)", (person, now))
            else:
                cur = conn.execute("DELETE FROM no_sync_people WHERE person_id = ?", (person,))
        else:
            name, key = "no_sync_accounts", people.canonical(conn, *account)
            if on:
                cur = conn.execute("INSERT OR IGNORE INTO no_sync_accounts(platform, author_id, at) VALUES (?, ?, ?)",
                                   (*key, now))
            else:
                # The id as stored, and any alias it was stored under.
                cur = conn.execute(
                    "DELETE FROM no_sync_accounts WHERE platform = ? AND (author_id = ? OR author_id IN "
                    "(SELECT alias_id FROM account_aliases WHERE platform = ? AND author_id = ?))",
                    (*key, *key))
    return name if cur.rowcount else None


def carry(conn, accounts, now=None):
    """The switch on for each of ``accounts`` ((platform, id)): those of a
    person set to Don't sync who leave them (unlinked, or the person
    deleted), so none of their sources starts syncing on its own. Returns
    whether any changed."""
    now = int(time.time()) if now is None else now
    with conn:
        n = sum(conn.execute("INSERT OR IGNORE INTO no_sync_accounts(platform, author_id, at) VALUES (?, ?, ?)",
                             (*people.canonical(conn, *key), now)).rowcount for key in accounts)
    return n > 0
