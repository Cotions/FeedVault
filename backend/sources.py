"""Sources: where a person's posts are downloaded from.

A source is a tool and its target (instaloader and a profile name), and the
folder inside a media root the tool writes to. It is user data, like people:
kept in ``sources``, mirrored to JSON by userdata.py (by tool and target, its
person by name), never touched by a rescan.

A source belongs to an account (platform and author id, as indexed) once one
is known, and then shows with whoever that account is linked to: link the
account to a person later and the source follows. A source added to a person
for a profile not downloaded yet has no account; it keeps that person until
its first sync finds one (see adopt).

Nothing here runs a tool: sync.py builds the job from a stored source.
"""
import json
import os
import re

import db
import people

TOOLS = ("instaloader",)                       # this slice; gallery-dl and yt-dlp come with #4 slice 2
PLATFORM = {"instaloader": "instagram"}
BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge")
SESSION_MODES = ("none", "cookies", "login")
ERRORS = ("login_required", "private", "not_found", "rate_limited", "generic")

# An Instagram username: letters, digits, dots and underscores, at most 30.
_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
_URL_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?instagram\.com/([^/?#]+)/?(?:[?#].*)?", re.IGNORECASE)


class Refused(ValueError):
    """A change that cannot be made; the message is for the user."""


def parse_target(tool, text):
    """The target a pasted profile URL, @handle or handle names (lowercase),
    or None when it is not one."""
    if tool != "instaloader" or not isinstance(text, str):
        return None
    text = text.strip()
    m = _URL_RE.fullmatch(text)
    handle = m.group(1) if m else text[1:] if text.startswith("@") else text
    # Pages that are not profiles (/p/<code>, /reel/…, /stories/…).
    if not _HANDLE_RE.fullmatch(handle) or handle.lower() in people._NOT_HANDLES or handle.strip(".") == "":
        return None
    return handle.lower()


def clean_session(value):
    """An instaloader session setting, or None when malformed:
    {"mode": "none"} | {"mode": "cookies", "browser": "firefox"} |
    {"mode": "login", "user": "name"}. FeedVault only passes the browser's
    name or the user name on; instaloader reads the cookies or its own saved
    session file, never FeedVault."""
    if not isinstance(value, dict) or value.get("mode") not in SESSION_MODES:
        return None
    mode = value["mode"]
    if set(value) - {"mode", {"cookies": "browser", "login": "user"}.get(mode)}:
        return None
    if mode == "cookies":
        return {"mode": mode, "browser": value["browser"]} if value.get("browser") in BROWSERS else None
    if mode == "login":
        user = value.get("user")
        return {"mode": mode, "user": user} if isinstance(user, str) and _HANDLE_RE.fullmatch(user) else None
    return {"mode": "none"}


def clean_options(value, base=None):
    """A source's options merged over ``base``, or None when malformed:
    full_history (bool: the first sync downloads everything instead of
    starting after the newest post indexed) and session (null: the global
    setting, else see clean_session)."""
    out = {"full_history": False, "session": None, **(base or {})}
    if value is None:
        return out
    if not isinstance(value, dict) or set(value) - {"full_history", "session"}:
        return None
    if "full_history" in value:
        if not isinstance(value["full_history"], bool):
            return None
        out["full_history"] = value["full_history"]
    if "session" in value:
        if value["session"] is None:
            out["session"] = None
        else:
            out["session"] = clean_session(value["session"])
            if out["session"] is None:
                return None
    return out


def inside_root(folder, roots):
    """``folder`` normalized, when it resolves (symlinks followed) inside a
    media root or to one, else None. It need not exist yet. Kept unresolved,
    as the scanner sees it under the root."""
    if not isinstance(folder, str) or not os.path.isabs(folder) or "\0" in folder:
        return None
    real = os.path.realpath(folder)
    for r in roots:
        rr = os.path.realpath(r)
        if real == rr or real.startswith(rr.rstrip(os.sep) + os.sep):
            return os.path.normpath(folder)
    return None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _owner(conn, row, accounts):
    """(account key or None, person {id, name} or None) of a source row."""
    key = None
    if row["author_id"] is not None:
        key = people.canonical(conn, row["platform"], row["author_id"])
        a = accounts.get(key)
        if a and a["person"]:
            return key, a["person"]
    if row["person_id"] is not None:
        p = conn.execute("SELECT id, name FROM people WHERE id = ?", (row["person_id"],)).fetchone()
        if p:
            return key, {"id": p["id"], "name": p["name"]}
    return key, None


def _public(conn, row, accounts, active):
    key, person = _owner(conn, row, accounts)
    job = active.get(row["id"])
    return {
        "id": row["id"], "tool": row["tool"], "platform": row["platform"], "target": row["target"],
        "url": db.profile_url(row["platform"], row["target"]),
        "folder": row["folder"],
        "account": {"platform": key[0], "id": key[1]} if key else None,
        "person": person,
        "options": json.loads(row["options"] or "{}"),
        "created_at": row["created_at"], "last_sync_at": row["last_sync_at"],
        "last_job_id": row["last_job_id"],
        "last_result": json.loads(row["last_result"]) if row["last_result"] else None,
        "job": job,
    }


def get(conn, sid, active=None):
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone() if 0 <= sid < 2**53 else None
    return _public(conn, row, db.accounts(conn), active or {}) if row else None


def row(conn, sid):
    return conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone() if 0 <= sid < 2**53 else None


def listing(conn, roots, active=None):
    """Every source, by target, and the folders that could be one."""
    accounts = db.accounts(conn)
    rows = conn.execute("SELECT * FROM sources ORDER BY target, tool").fetchall()
    return {"sources": [_public(conn, r, accounts, active or {}) for r in rows],
            "suggestions": suggestions(conn, roots)}


def account_folder(conn, platform, author_id, roots):
    """The top folder under a media root holding the account's instaloader
    posts (aliases included), the one with the most, or None."""
    clause, args = db.post_filter(platform=platform, author=author_id)
    counts = {}
    for (path,) in conn.execute(f"SELECT p.meta_path {db._FROM} {clause} AND p.tool LIKE 'instaloader%'", args):
        top = _top(path, roots)
        if top:
            counts[top] = counts.get(top, 0) + 1
    return max(counts, key=lambda f: (counts[f], f)) if counts else None


def _top(path, roots):
    """The folder right under a media root that holds ``path``, or None."""
    for r in roots:
        r = r.rstrip(os.sep)
        if path.startswith(r + os.sep):
            first = path[len(r) + 1:].split(os.sep, 1)
            return os.path.join(r, first[0]) if len(first) == 2 else None
    return None


def suggestions(conn, roots):
    """Profile folders with instaloader posts and no source yet, offered as
    sources for the user to confirm (never created on their own): one per
    folder right under a media root, its main account, and as target the
    account's current handle (else the folder's name)."""
    def compute(conn):
        accounts = db.accounts(conn)
        taken = {r[0] for r in conn.execute("SELECT folder FROM sources")}
        taken_targets = {r[0] for r in conn.execute("SELECT target FROM sources WHERE tool = 'instaloader'")}
        folders = {}                           # folder -> {account key: posts}
        for platform, aid, n, path in conn.execute(
                "SELECT platform, author_id, COUNT(*), MIN(meta_path) FROM posts "
                "WHERE tool LIKE 'instaloader%' AND platform = 'instagram' AND author_id IS NOT NULL GROUP BY 1, 2"):
            top = _top(path, roots)
            if top is None or top in taken:
                continue
            key = people.canonical(conn, platform, aid)
            if key in accounts:
                f = folders.setdefault(top, {})
                f[key] = f.get(key, 0) + n
        out = []
        for folder, keys in sorted(folders.items()):
            key = max(keys, key=lambda k: (keys[k], k))
            a = accounts[key]
            name = os.path.basename(folder).lower()
            target = next((t for t in ((a["handle"] or "").lower(), name)
                           if _HANDLE_RE.fullmatch(t) and t not in people._NOT_HANDLES), None)
            if target is None or target in taken_targets:
                continue
            out.append({"tool": "instaloader", "platform": "instagram", "target": target, "folder": folder,
                        "account": {"platform": key[0], "id": key[1]}, "handle": a["handle"],
                        "count": sum(keys.values()), "person": a["person"]})
        return out
    return db._memo(conn, ("source-suggestions", tuple(roots)), compute)


# ---------------------------------------------------------------------------
# Changes (the caller notes userdata)
# ---------------------------------------------------------------------------

def create(conn, roots, tool, target, folder, person_id, account, options, now):
    """A new source. ``folder``: an existing folder inside a media root, or
    None for ``<first media root>/<target>``. ``account``: (platform, id) of
    an indexed account, else found from the folder's posts when it has some."""
    if tool not in TOOLS:
        raise Refused(f"tool must be one of: {', '.join(TOOLS)}")
    if not roots:
        raise Refused("add a media root in Settings first")
    if folder is None:
        folder = os.path.join(roots[0], target)
    real = inside_root(folder, roots)
    if real is None:
        raise Refused("the folder must be inside a media root")
    if os.path.exists(real) and not os.path.isdir(real):
        raise Refused("the folder path is a file")
    if conn.execute("SELECT 1 FROM sources WHERE tool = ? AND target = ?", (tool, target)).fetchone():
        raise Refused(f"there is already a {tool} source for {target}")
    platform = PLATFORM[tool]
    if account is not None:
        key = people.canonical(conn, *account)
        if key not in db.accounts(conn) or key[0] != platform:
            raise Refused(f"unknown account {account[0]}:{account[1]}")
    else:
        key = _folder_account(conn, platform, real, roots)
    if person_id is not None and not people.exists(conn, person_id):
        raise Refused("no such person")
    with conn:
        sid = conn.execute(
            "INSERT INTO sources(person_id, platform, author_id, tool, target, folder, options, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (person_id, platform, key[1] if key else None, tool, target, real, json.dumps(options), now)).lastrowid
    return sid


def _folder_account(conn, platform, folder, roots):
    """The account of a profile folder's posts: its name as filename-only
    posts carry it, canonical (the numeric id once metadata names it), else
    the account with the most posts in it."""
    accounts = db.accounts(conn)
    if _top(os.path.join(folder, "x"), roots) == folder:
        key = people.canonical(conn, platform, os.path.basename(folder).lower())
        if key in accounts:
            return key
    prefix = folder.rstrip(os.sep) + os.sep
    found = conn.execute(
        "SELECT author_id, COUNT(*) FROM posts WHERE platform = ? AND author_id IS NOT NULL "
        "AND substr(meta_path, 1, ?) = ? GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1",
        (platform, len(prefix), prefix)).fetchone()
    key = people.canonical(conn, platform, found[0]) if found else None
    return key if key in accounts else None


def update(conn, sid, options):
    with conn:
        conn.execute("UPDATE sources SET options = ? WHERE id = ?", (json.dumps(options), sid))


def delete(conn, sid):
    """Forget a source. Its folder and posts stay."""
    with conn:
        return conn.execute("DELETE FROM sources WHERE id = ?", (sid,)).rowcount


def record(conn, sid, job_id, ended_at, result):
    """Store how a sync ended. False when the source is gone."""
    with conn:
        return conn.execute(
            "UPDATE sources SET last_sync_at = ?, last_job_id = ?, last_result = ? WHERE id = ?",
            (ended_at, job_id, json.dumps(result), sid)).rowcount > 0


def adopt(conn, sid, roots, now):
    """After a sync: a source with no account takes the one its folder's
    posts now have, and a source added to a person links that account to
    them when it has no person yet. Returns which user tables changed."""
    src = row(conn, sid)
    if src is None or src["author_id"] is not None:
        return []
    key = _folder_account(conn, src["platform"], src["folder"], roots)
    if key is None:
        return []
    link = src["person_id"] is not None and people.exists(conn, src["person_id"]) \
        and not db.accounts(conn)[key]["person"]
    with conn:
        conn.execute("UPDATE sources SET author_id = ? WHERE id = ?", (key[1], sid))
        if link:
            people._attach(conn, src["person_id"], [key], now)
    return ["sources", "person_accounts"] if link else ["sources"]
