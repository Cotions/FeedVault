"""People: one person's accounts across platforms, linked into one.

An account is a platform and an author id, as the index has it (see
db.accounts). Links are user data, like tags: kept in ``people`` and
``person_accounts``, never touched by a rescan, and mirrored to JSON by
userdata.py, accounts by platform and author id and people by name, so a
rebuilt index gets them back. They are keyed by account, not by post, so a
post trashed and restored, or replaced by a duplicate copy, stays with its
person. An account with no person is shown on its own, as before.

The person is only in the database; files stay where each tool wrote them.
"""
import os

import db
import organize

MAX_NOTES = 5000
MAX_ACCOUNTS = 500              # accounts per call


def clean_name(name):
    """A person's name with its spaces collapsed, or None (same rules as a tag name)."""
    return organize.clean_name(name)


def clean_accounts(value):
    """[(platform, author id)] from [{"platform", "id"}], or None when any is malformed."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ACCOUNTS:
        return None
    out = []
    for a in value:
        if not isinstance(a, dict) or not isinstance(a.get("platform"), str) or not a["platform"] \
                or not isinstance(a.get("id"), str) or not a["id"]:
            return None
        out.append((a["platform"], a["id"]))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Aliases (derived, on every scan)
# ---------------------------------------------------------------------------

FILENAMES = "% (filenames)"     # tool of posts rebuilt from file names


def _folder(path, folder_id):
    """The folder of ``path`` named ``folder_id`` (any case), nearest first, or None."""
    d = os.path.dirname(path)
    while True:
        if os.path.basename(d).lower() == folder_id:
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def refresh_aliases(conn):
    """Rebuild account_aliases from the index.

    Posts rebuilt from file names carry the folder's name as author id; posts
    with metadata carry the platform's own id. When a folder holds both for
    the same handle, they are one account: the folder name becomes an alias
    of the id, so it is shown and linked with it. Only when exactly one
    account with metadata in that folder has that handle, so a folder of
    mixed downloads never merges two people."""
    found = []
    for platform, folder_id, meta_path in conn.execute(
            "SELECT platform, author_id, MIN(meta_path) FROM posts "
            "WHERE tool LIKE ? AND author_id IS NOT NULL GROUP BY 1, 2", (FILENAMES,)).fetchall():
        folder = _folder(meta_path, folder_id)
        if folder is None:
            continue
        handles = {folder_id} | {(r[0] or "").lower() for r in conn.execute(
            "SELECT DISTINCT author_handle FROM posts WHERE platform = ? AND author_id = ?", (platform, folder_id))}
        # Paths under the folder: [folder/, folder0) in byte order ('0' follows '/').
        ids = {r[0] for r in conn.execute(
            "SELECT DISTINCT author_id FROM posts WHERE platform = ? AND meta_path >= ? AND meta_path < ? "
            "AND author_id IS NOT NULL AND author_id != ? AND tool NOT LIKE ? AND lower(author_handle) IN "
            f"({', '.join('?' for _ in handles)})",
            (platform, folder + os.sep, folder + chr(ord(os.sep) + 1), folder_id, FILENAMES, *handles))}
        if len(ids) == 1:
            found.append((platform, folder_id, ids.pop()))
    have = set(conn.execute("SELECT platform, alias_id, author_id FROM account_aliases").fetchall())
    if have != set(found):                     # an unchanged index stays unchanged (and cached)
        conn.execute("DELETE FROM account_aliases")
        conn.executemany("INSERT OR REPLACE INTO account_aliases(platform, alias_id, author_id) VALUES (?, ?, ?)",
                         found)
    return len(found)


def canonical(conn, platform, author_id):
    """The account an id stands for: itself, or the id it is an alias of."""
    row = conn.execute("SELECT author_id FROM account_aliases WHERE platform = ? AND alias_id = ?",
                       (platform, author_id)).fetchone()
    return (platform, row[0]) if row else (platform, author_id)


def account_set(conn, person_id):
    """{(platform, author id)} of a person, aliases included both ways."""
    return {(r[0], r[1]) for r in conn.execute(db.PERSON_ACCOUNTS, (person_id,) * 3)}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _person(row, accounts):
    """A person as the API shows it. ``accounts`` are account rows (db.accounts)."""
    newest = [a["newest"] for a in accounts if a["newest"] is not None]
    return {
        "id": row["id"], "name": row["name"], "notes": row["notes"], "created_at": row["created_at"],
        "accounts": accounts,
        "platforms": sorted({a["platform"] for a in accounts}),
        "count": sum(a["count"] for a in accounts),
        "bytes": sum(a["bytes"] for a in accounts),
        "newest": max(newest) if newest else None,
    }


def _linked(conn, rows):
    """{person id: [account row]}: indexed accounts as db.accounts has them,
    linked ones not in the index (not scanned yet, or every post trashed)
    as empty rows."""
    known = db.accounts(conn)
    out = {r["id"]: [] for r in rows}
    seen = set()
    ids = list(out)
    if not ids:
        return out
    for pid, platform, aid in conn.execute(
            "SELECT person_id, platform, author_id FROM person_accounts "
            f"WHERE person_id IN ({', '.join('?' for _ in ids)}) ORDER BY platform, author_id", ids):
        key = canonical(conn, platform, aid)
        if key in seen:
            continue
        seen.add(key)
        a = known.get(key)
        if a is None:
            a = {"platform": platform, "id": aid, "handle": None, "name": None, "count": 0, "bytes": 0,
                 "newest": None, "aliases": [], "person": None, "handles": [], "names": [], "url": None}
        out[pid].append(a)
    for accounts in out.values():
        accounts.sort(key=lambda a: (-a["count"], a["platform"], a["handle"] or a["id"]))
    return out


def people(conn):
    """Every person, by name."""
    def compute(conn):
        rows = conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE, id").fetchall()
        linked = _linked(conn, rows)
        return [_person(r, linked[r["id"]]) for r in rows]
    return db._memo(conn, ("people",), compute)


def person(conn, pid):
    row = conn.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
    return _person(row, _linked(conn, [row])[pid]) if row else None


# ---------------------------------------------------------------------------
# Changes (each returns what the API answers; the caller notes userdata)
# ---------------------------------------------------------------------------

class Refused(ValueError):
    """A change that cannot be made; the message is for the user."""


def _name_free(conn, name, but=()):
    row = conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
    return row is None or row[0] in but


def _check_accounts(conn, accounts):
    """The accounts as stored (aliases replaced by the id they stand for);
    Refused when one is not in the index."""
    known = db.accounts(conn)
    out = []
    for platform, aid in accounts:
        key = canonical(conn, platform, aid)
        if key not in known:
            raise Refused(f"unknown account {platform}:{aid}")
        out.append(key)
    return list(dict.fromkeys(out))


def _attach(conn, pid, accounts, now):
    """Link accounts to a person, taking them from any other person (an
    account belongs to one person). Returns how many links changed."""
    n = 0
    for platform, aid in accounts:
        # Drop the alias either way, so an account is never linked twice.
        conn.execute("DELETE FROM person_accounts WHERE platform = ? AND author_id IN "
                     "(SELECT alias_id FROM account_aliases WHERE platform = ? AND author_id = ?)",
                     (platform, platform, aid))
        row = conn.execute("SELECT person_id FROM person_accounts WHERE platform = ? AND author_id = ?",
                           (platform, aid)).fetchone()
        if row and row[0] == pid:
            continue
        conn.execute("INSERT OR REPLACE INTO person_accounts(person_id, platform, author_id, at) VALUES (?, ?, ?, ?)",
                     (pid, platform, aid, now))
        n += 1
    return n


def create(conn, name, accounts, now):
    """A new person with these accounts (moved from any other person)."""
    if not _name_free(conn, name):
        raise Refused("a person with that name exists")
    accounts = _check_accounts(conn, accounts)
    with conn:
        pid = conn.execute("INSERT INTO people(name, created_at) VALUES (?, ?)", (name, now)).lastrowid
        _attach(conn, pid, accounts, now)
    return person(conn, pid)


def update(conn, pid, name=None, notes=None):
    if name is not None and not _name_free(conn, name, but=(pid,)):
        raise Refused("a person with that name exists")
    with conn:
        if name is not None:
            conn.execute("UPDATE people SET name = ? WHERE id = ?", (name, pid))
        if notes is not None:
            conn.execute("UPDATE people SET notes = ? WHERE id = ?", (notes, pid))
    return person(conn, pid)


def delete(conn, pid):
    """Drop a person and its links. Posts are never touched. Returns the accounts unlinked."""
    with conn:
        n = conn.execute("DELETE FROM person_accounts WHERE person_id = ?", (pid,)).rowcount
        conn.execute("DELETE FROM people WHERE id = ?", (pid,))
    return n


def link(conn, pid, add, remove, now):
    """Add accounts to a person (moved from any other) and remove some of
    its own. Removing an account removes its aliases' links too."""
    add = _check_accounts(conn, add)
    removed = 0
    with conn:
        added = _attach(conn, pid, add, now)
        for platform, aid in remove:
            platform, aid = canonical(conn, platform, aid)
            removed += conn.execute(
                "DELETE FROM person_accounts WHERE person_id = ? AND platform = ? AND (author_id = ? OR author_id IN "
                "(SELECT alias_id FROM account_aliases WHERE platform = ? AND author_id = ?))",
                (pid, platform, aid, platform, aid)).rowcount
    return {"added": added, "removed": removed, "person": person(conn, pid)}


def merge(conn, ids, name, accounts, now):
    """Fold several people (and accounts) into the first id: its links,
    notes and name stay, the others' accounts move to it and they are gone."""
    keep = ids[0]
    if name is not None and not _name_free(conn, name, but=ids):
        raise Refused("a person with that name exists")
    accounts = _check_accounts(conn, accounts)
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM people WHERE id IN ({', '.join('?' for _ in ids)})", ids)}
    with conn:
        notes = [rows[i]["notes"].strip() for i in ids if rows[i]["notes"].strip()]
        for other in ids[1:]:
            conn.execute("UPDATE person_accounts SET person_id = ? WHERE person_id = ?", (keep, other))
            conn.execute("DELETE FROM people WHERE id = ?", (other,))
        _attach(conn, keep, accounts, now)
        conn.execute("UPDATE people SET notes = ? WHERE id = ?", ("\n\n".join(notes)[:MAX_NOTES], keep))
        if name is not None:
            conn.execute("UPDATE people SET name = ? WHERE id = ?", (name, keep))
    return person(conn, keep)
