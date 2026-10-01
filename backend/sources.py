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

A source is added by pasting a profile link: the link's host picks the tool
from the routing table in the config (``routes``, editable in Settings), and
the target is the link, normalized (instaloader's is the profile name).

Nothing here runs a tool: sync.py builds the job from a stored source.
"""
import json
import os
import re
from urllib.parse import urlsplit

import db
import people

TOOLS = ("instaloader", "gallery-dl", "yt-dlp")
PLATFORM = {"instaloader": "instagram"}
# The routing table's defaults: host -> tool. A host also covers its
# subdomains (www.x.com, m.youtube.com), never a longer name (x.com.evil.example).
ROUTES = {
    "instagram.com": "instaloader",
    "x.com": "gallery-dl", "twitter.com": "gallery-dl", "reddit.com": "gallery-dl",
    "bsky.app": "gallery-dl", "pixiv.net": "gallery-dl",
    "youtube.com": "yt-dlp",
    "tiktok.com": "yt-dlp",                    # or gallery-dl, in Settings
}
ROUTES_MAX = 100
# The platform a host's posts are indexed under (the tools' own names for it);
# any other host: the first part of its name ("patreon.com": "patreon").
HOST_PLATFORMS = {"instagram.com": "instagram", "x.com": "twitter", "twitter.com": "twitter",
                  "reddit.com": "reddit", "bsky.app": "bluesky", "pixiv.net": "pixiv",
                  "youtube.com": "youtube", "tiktok.com": "tiktok"}
BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge")
SESSION_MODES = ("none", "cookies", "login")
COOKIE_MODES = ("none", "cookies")             # gallery-dl and yt-dlp: a browser's cookies or nothing
ERRORS = ("login_required", "private", "not_found", "rate_limited", "missing", "generic")

# An Instagram username: letters, digits, dots and underscores, at most 30.
_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
_URL_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?instagram\.com/([^/?#]+)/?(?:[?#].*)?", re.IGNORECASE)


class Refused(ValueError):
    """A change that cannot be made; the message is for the user."""


# ---------------------------------------------------------------------------
# Profile links and the routing table
# ---------------------------------------------------------------------------

_HOST_RE = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
# What a profile path may hold: no spaces, quotes, backslashes or controls.
_PATH_RE = re.compile(r"(?:/[A-Za-z0-9._~@%+-]+)+")
URL_MAX = 500
# Path parts that say what kind of page it is, not whose: skipped when
# naming a source's folder.
# Pages that are what their query string says (?v=…, ?list=…, ?q=…): the
# query is dropped, so they are refused rather than saved as a broken link.
_QUERY_PAGES = {"watch", "playlist", "results", "search", "hashtag", "explore"}
_PAGE_WORDS = {"user", "users", "u", "profile", "channel", "c", "en", "ja", "media", "videos", "shorts",
               "streams", "tweets", "with_replies", "likes", "submitted", "posts", "artworks", "illustrations",
               "featured", "playlists", "member", "creator"}


def clean_routes(value):
    """A routing table from Settings, or (None, error): {host: tool}, each
    host a lowercase domain name, each tool known; instaloader only for
    instagram.com (it downloads nothing else)."""
    if not isinstance(value, dict) or not value or len(value) > ROUTES_MAX:
        return None, f"routes must map 1 to {ROUTES_MAX} hosts to a tool"
    out = {}
    for host, tool in value.items():
        h = host.strip().lower() if isinstance(host, str) else ""
        if h.startswith("www."):
            h = h[4:]
        if not _HOST_RE.fullmatch(h):
            return None, f"not a host name: {host}"
        if tool not in TOOLS:
            return None, f"{h}: tool must be one of: {', '.join(TOOLS)}"
        if tool == "instaloader" and h != "instagram.com":
            return None, f"{h}: instaloader only downloads from instagram.com"
        out[h] = tool
    return out, None


def routes(cfg):
    """The routing table in use: the config's when it is valid, else the defaults."""
    table, _ = clean_routes(cfg.get("routes")) if cfg.get("routes") is not None else (None, None)
    return table or dict(ROUTES)


def route(host, table):
    """(table host, tool) for a link's host: the longest table entry that
    is the host or a domain it is under; None when there is none."""
    best = None
    for h, tool in table.items():
        if (host == h or host.endswith("." + h)) and (best is None or len(h) > len(best[0])):
            best = (h, tool)
    return best


def parse_url(text, table):
    """(normalized link, table host, tool) for a pasted profile link, else
    (None, error). The link becomes ``https://<host><path>``: lowercase
    host without ``www.``, ``m.`` or ``mobile.``, no login part, port, query or fragment, no
    trailing slash. Its host must be in the routing table."""
    if not isinstance(text, str):
        return None, "paste a profile link"
    text = text.strip()
    if not text or len(text) > URL_MAX or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text):
        return None, "paste a profile link"
    if "://" not in text:
        text = "https://" + text
    try:
        u = urlsplit(text)
        port = u.port
    except ValueError:
        return None, "not a link"
    if u.scheme.lower() not in ("http", "https"):
        return None, "only http and https links"
    if u.username is not None or u.password is not None or "@" in u.netloc:
        return None, "a link with a login part is refused"
    if port not in (None, 80, 443):
        return None, "a link with a port is refused"
    host = (u.hostname or "").lower()
    # The same profile under the site's www or mobile host: one target.
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    if not _HOST_RE.fullmatch(host):
        return None, "not a link to a website"
    found = route(host, table)
    if found is None:
        return None, f"{host} is not in the routing table (Settings → Link routing)"
    path = re.sub(r"/{2,}", "/", u.path).rstrip("/")
    if not path:
        return None, "paste a link to a profile, not to the site's home page"
    if path.rsplit("/", 1)[-1].lower() in _QUERY_PAGES:
        return None, "that is a link to a video, playlist or search, not to a profile"
    if not _PATH_RE.fullmatch(path) or any(p in (".", "..") for p in path.split("/")):
        return None, "the link has characters a profile link does not"
    return (f"https://{host}{path}", found[0], found[1]), None


def platform_of(table_host):
    return HOST_PLATFORMS.get(table_host) or table_host.split(".")[0]


def profile_handle(url):
    """The profile's name in a link: its first part that is not a page kind
    (``/user/``, ``/media``), without ``@``; else None."""
    for part in urlsplit(url).path.split("/"):
        part = part.lstrip("@")
        if part and part.lower() not in _PAGE_WORDS:
            return part
    return None


def folder_name(url):
    """A folder name for a profile link: its profile name, lowercase; else "profile"."""
    name = re.sub(r"[^a-z0-9._-]+", "_", (profile_handle(url) or "").lower()).strip("._")
    return name[:80] or "profile"


def resolve(text, table, roots):
    """What adding a source for a pasted link would make: {tool, platform,
    target, folder}, or raises Refused. An Instagram link with instaloader
    gives the profile name as target; any other tool, the normalized link.
    The folder is ``<first media root>/<target>`` for instaloader,
    ``<first media root>/<platform>/<name>`` for the others."""
    parsed, error = parse_url(text, table)
    if parsed is None:
        raise Refused(error)
    url, host, tool = parsed
    platform = platform_of(host)
    if tool == "instaloader":
        target = parse_target("instaloader", url)
        if target is None:
            raise Refused("paste a link to an Instagram profile, not to a post")
        folder = os.path.join(roots[0], target) if roots else None
    else:
        target = url
        folder = os.path.join(roots[0], platform, folder_name(url)) if roots else None
    return {"tool": tool, "platform": platform, "target": target, "folder": folder}


def check_target(tool, target, table):
    """``target`` when it is still a valid stored target for ``tool`` (a
    profile name for instaloader; else a normalized link whose host is in
    the routing table), else None. sync.py checks again before each run."""
    if tool == "instaloader":
        return target if parse_target("instaloader", target) == target else None
    parsed, _ = parse_url(target, table)
    return target if parsed is not None and parsed[0] == target else None


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


def clean_session(value, modes=SESSION_MODES):
    """A session setting, or None when malformed: {"mode": "none"} |
    {"mode": "cookies", "browser": "firefox"} | {"mode": "login", "user":
    "name"} (instaloader only), among ``modes``. FeedVault only passes the
    browser's name or the user name on; the tool reads the cookies or its
    own saved session file, never FeedVault."""
    if not isinstance(value, dict) or value.get("mode") not in modes:
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


def clean_options(value, base=None, tool="instaloader"):
    """A source's options merged over ``base``, or None when malformed:
    full_history (bool: the next sync walks everything instead of starting
    after what is already there) and session (null: the tool's setting,
    else see clean_session; no login for gallery-dl and yt-dlp)."""
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
            out["session"] = clean_session(value["session"], SESSION_MODES if tool == "instaloader" else COOKIE_MODES)
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
        "url": row["target"] if row["tool"] != "instaloader" else db.profile_url(row["platform"], row["target"]),
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
    account's current handle (else the folder's name). Only metadata names a
    handle reliably: for an account known from file names alone (its id is
    the folder's name, not a number), the folder's name is the target, as a
    stray file named after someone else would set its handle."""
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
            handle = (a["handle"] or "").lower() if key[1].isdigit() else ""
            target = next((t for t in (handle, name)
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

def create(conn, roots, tool, target, folder, person_id, account, options, now, table=None):
    """A new source. ``target``: checked (check_target). ``folder``: an
    existing folder inside a media root, or None for resolve()'s default.
    ``account``: (platform, id) of an indexed account, else found from the
    folder's posts when it has some."""
    if tool not in TOOLS:
        raise Refused(f"tool must be one of: {', '.join(TOOLS)}")
    if not roots:
        raise Refused("add a media root in Settings first")
    table = table or dict(ROUTES)
    if check_target(tool, target, table) is None:
        raise Refused("the target is not a profile name or a profile link in the routing table")
    if tool == "instaloader":
        platform = PLATFORM[tool]
        default = os.path.join(roots[0], target)
    else:
        platform = platform_of(route(urlsplit(target).hostname, table)[0])
        default = os.path.join(roots[0], platform, folder_name(target))
    if folder is None:
        folder = default
    real = inside_root(folder, roots)
    if real is None:
        raise Refused("the folder must be inside a media root")
    if os.path.exists(real) and not os.path.isdir(real):
        raise Refused("the folder path is a file")
    if existing(conn, tool, target, real) is not None:
        raise Refused(f"there is already a {tool} source for {target} or its folder")
    if account is not None:
        key = people.canonical(conn, *account)
        if key not in db.accounts(conn) or key[0] != platform:
            raise Refused(f"unknown account {account[0]}:{account[1]}")
    else:
        key = _folder_account(conn, platform, real, roots)
        if key is None and tool != "instaloader":
            key = _handle_account(conn, platform, profile_handle(target))
    if person_id is not None and not people.exists(conn, person_id):
        raise Refused("no such person")
    with conn:
        sid = conn.execute(
            "INSERT INTO sources(person_id, platform, author_id, tool, target, folder, options, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (person_id, platform, key[1] if key else None, tool, target, real, json.dumps(options), now)).lastrowid
    return sid


def existing(conn, tool, target, folder=None):
    """The id of the source of ``tool`` that is already there for a target:
    the same one in any case (X, TikTok and YouTube names are not
    case-sensitive), or one that downloads into the same folder (x.com and
    twitter.com links to one profile). Else None."""
    row = conn.execute("SELECT id FROM sources WHERE tool = ? AND (lower(target) = lower(?) OR folder = ?) "
                       "ORDER BY lower(target) = lower(?) DESC, id LIMIT 1",
                       (tool, target, folder, target)).fetchone()
    return row[0] if row else None


def _handle_account(conn, platform, handle):
    """The indexed account whose handle (any it had) is a link's profile
    name, when exactly one has it: posts downloaded before, into another
    folder, so the first sync does not fetch them again."""
    if not handle:
        return None
    handle = handle.lower()
    found = [key for key, a in db.accounts(conn).items() if key[0] == platform and handle in
             {h.lower() for h in [a["handle"], *a["handles"]] if isinstance(h, str)}]
    return found[0] if len(found) == 1 else None


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
