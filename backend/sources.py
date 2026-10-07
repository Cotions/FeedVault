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
from datetime import date
from urllib.parse import urlsplit

import db
import health
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

# Under the first media root: posts the Save button got for accounts with no
# folder (save.py). Never a source's folder: a sync would take them for its
# own, and gather moves them out of it.
SAVED = "_saved"

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
    target = url
    if tool == "instaloader":
        target = parse_target("instaloader", url)
        if target is None:
            raise Refused("paste a link to an Instagram profile, not to a post")
    return _resolved(tool, platform, target, roots)


def resolve_name(text, roots):
    """resolve() for an Instagram profile name or @name, with instaloader."""
    target = parse_target("instaloader", text)
    if target is None:
        raise Refused("not an Instagram profile name")
    return _resolved("instaloader", PLATFORM["instaloader"], target, roots)


def default_folder(tool, platform, target, roots):
    """Where a new source's posts go unless another folder is picked."""
    if tool == "instaloader":
        return os.path.join(roots[0], target)
    return os.path.join(roots[0], platform, folder_name(target))


def _resolved(tool, platform, target, roots):
    folder = default_folder(tool, platform, target, roots) if roots else None
    if folder and in_saved(folder, roots):
        raise Refused(SAVED_REFUSED.format(folder=folder))
    return {"tool": tool, "platform": platform, "target": target, "folder": folder}


SAVED_REFUSED = ("{folder} is where the Save button keeps posts of accounts that have no folder "
                 f"({SAVED}): a source cannot download into it or a folder inside it; pick another folder")


def in_saved(folder, roots):
    """Whether ``folder`` (symlinks followed) is a media root's ``_saved``
    or inside one. Checked wherever a source's folder is: here when one is
    added, and in sync.py before each run."""
    real = os.path.realpath(folder)
    for r in roots:
        d = os.path.realpath(os.path.join(r, SAVED))
        if real == d or real.startswith(d.rstrip(os.sep) + os.sep):
            return True
    return False


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


# ---------------------------------------------------------------------------
# What a source downloads (sync.py turns it into flags)
# ---------------------------------------------------------------------------

# What a source can fetch besides the defaults, by tool and platform, in the
# order shown; the first entry of DEFAULT_CONTENT is what it gets with none
# chosen. gallery-dl only on a profile's own page (its "include" option),
# yt-dlp never: there the link picks it (x.com/name/media, a YouTube tab).
CONTENT = {
    ("instaloader", "instagram"): ("posts", "reels", "stories", "highlights", "tagged"),
    ("gallery-dl", "instagram"): ("posts", "reels", "stories", "highlights", "tagged"),
    ("gallery-dl", "twitter"): ("timeline", "media", "tweets", "with_replies"),
    ("gallery-dl", "bluesky"): ("media", "posts", "replies", "video"),
    ("gallery-dl", "tiktok"): ("posts", "reposts", "stories"),
}
DEFAULT_CONTENT = {key: (kinds[0],) for key, kinds in CONTENT.items()}
# A profile's own page on each platform gallery-dl has content choices for
# (the extractors that read "include"), as normalized links have it.
_PROFILE_PAGES = {
    "instagram": re.compile(r"/[A-Za-z0-9._]+"),
    "twitter": re.compile(r"/[A-Za-z0-9_]+"),
    "bluesky": re.compile(r"/profile/[^/]+"),
    "tiktok": re.compile(r"/@[^/]+"),
}
# Instagram shows these to logged-in viewers only.
LOGIN_CONTENT = {"stories", "highlights", "tagged"}
MEDIA = ("all", "images", "videos")
MEDIA_TOOLS = ("instaloader", "gallery-dl")
# instaloader cannot stop after N posts of a profile (its --count only
# applies to hashtags, locations, the feed and saved posts).
FIRST_POSTS_TOOLS = ("gallery-dl", "yt-dlp")
FIRST_POSTS_MAX = 10000
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# How often the scheduler syncs a source (scheduler.py); off by default.
SCHEDULES = ("off", "hourly", "daily", "weekly")
# Last: stored_options applies the keys in this order, and a stored
# schedule wins over the one content's stories would pick.
OPTION_KEYS = ("full_history", "session", "content", "media", "since", "first_posts", "script", "schedule")
# A script's id (scripts.py): a file's name without its suffix, or a built-in's.
SCRIPT_ID_RE = re.compile(r"(?:builtin:)?[a-z0-9_-]{1,64}")
LOGIN_REFUSED = ("{kinds} need a logged-in session: choose one for this source, or set one in "
                 "Settings → Sync")


def choices(tool, platform, target):
    """What the options of a source can be: {content (the kinds it can
    fetch, empty when the link picks), content_default, login (kinds that
    need a logged-in session), media, since, first_posts (whether each can
    be set)}."""
    kinds = CONTENT.get((tool, platform), ())
    if tool == "gallery-dl" and kinds:
        page = _PROFILE_PAGES[platform]
        if not isinstance(target, str) or not page.fullmatch(urlsplit(target).path):
            kinds = ()
    return {"content": list(kinds), "content_default": list(DEFAULT_CONTENT[(tool, platform)]) if kinds else [],
            "login": [k for k in kinds if platform == "instagram" and k in LOGIN_CONTENT],
            "media": tool in MEDIA_TOOLS, "since": True, "first_posts": tool in FIRST_POSTS_TOOLS}


def _since(value):
    """A YYYY-MM-DD date from 1970-01-01 to today, else None."""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return value if date(1970, 1, 1) <= day <= date.today() else None


def parse_options(value, base=None, tool="instaloader", platform="instagram", target=None, stories_daily=True):
    """A source's options merged over ``base``: (options, None), or (None,
    error) when ``value`` is malformed or asks for what the source cannot
    do (see choices). The options:

    - full_history: bool, the next sync walks everything instead of
      starting after what is already there;
    - session: null (the tool's setting), else see clean_session (no login
      for gallery-dl and yt-dlp);
    - content: null (the default), else the kinds to fetch, of choices();
      the default alone is stored as null;
    - media: all, images or videos (instaloader and gallery-dl);
    - since: null, or a YYYY-MM-DD date: nothing older is downloaded;
    - first_posts: null, or 1 to 10000: the first sync gets only that many
      of the newest posts (gallery-dl and yt-dlp; not with full_history);
    - script: null (the tool's own command), else the id of a script its
      sync runs instead (scripts.py; whether it exists is checked when it
      runs, and by the API when it is set);
    - schedule: off, hourly, daily or weekly. Stories last 24 h: content
      that turns them on makes an off schedule daily, unless ``value``
      sends a schedule too (``stories_daily`` False: never)."""
    out = {"full_history": False, "session": None, "content": None, "media": "all", "since": None,
           "first_posts": None, "script": None, "schedule": "off", **(base or {})}
    if value is None:
        return out, None
    if not isinstance(value, dict):
        return None, "options must be an object"
    unknown = set(value) - set(OPTION_KEYS)
    if unknown:
        return None, f"unknown option {sorted(unknown)[0]!r}: options are {', '.join(OPTION_KEYS)}"
    can = choices(tool, platform, target)
    if "full_history" in value:
        if not isinstance(value["full_history"], bool):
            return None, "full_history must be true or false"
        out["full_history"] = value["full_history"]
    if "session" in value:
        if value["session"] is None:
            out["session"] = None
        else:
            out["session"] = clean_session(value["session"], SESSION_MODES if tool == "instaloader" else COOKIE_MODES)
            if out["session"] is None:
                return None, ('session must be null, { "mode": "none" }, { "mode": "cookies", "browser": '
                              f'{" | ".join(BROWSERS)} }}'
                              + (' or { "mode": "login", "user": "<name>" }' if tool == "instaloader" else ""))
    if "content" in value:
        content = value["content"]
        if content is not None:
            if not can["content"]:
                return None, ("this link picks what it downloads (a profile's own page offers a choice)"
                              if tool == "gallery-dl" else f"{tool} downloads what the link lists: pick it "
                                                            "with the link (a YouTube tab, for instance)")
            if not isinstance(content, list) or not content or \
                    any(not isinstance(k, str) or k not in can["content"] for k in content):
                return None, f"content must be a non-empty list of: {', '.join(can['content'])}"
            content = [k for k in can["content"] if k in content]
            if stories_daily and "stories" in content and "stories" not in (out["content"] or ()) \
                    and "schedule" not in value and out["schedule"] == "off":
                out["schedule"] = "daily"
            if content == can["content_default"]:
                content = None
        out["content"] = content
    if "media" in value:
        if value["media"] not in MEDIA:
            return None, f"media must be one of: {', '.join(MEDIA)}"
        if value["media"] != "all" and not can["media"]:
            return None, f"{tool} downloads every video: media must be all"
        out["media"] = value["media"]
    if "since" in value:
        if value["since"] is not None and _since(value["since"]) is None:
            return None, "since must be a date as YYYY-MM-DD, from 1970-01-01 to today"
        out["since"] = value["since"]
    if "first_posts" in value:
        n = value["first_posts"]
        if n is not None:
            if not can["first_posts"]:
                return None, f"{tool} cannot stop after a number of posts: first_posts must be null"
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= FIRST_POSTS_MAX:
                return None, f"first_posts must be null or a whole number from 1 to {FIRST_POSTS_MAX}"
        out["first_posts"] = n
    if "script" in value:
        if value["script"] is not None and not (isinstance(value["script"], str)
                                                and SCRIPT_ID_RE.fullmatch(value["script"])):
            return None, "script must be null or a script's id"
        out["script"] = value["script"]
    if "schedule" in value:
        if value["schedule"] not in SCHEDULES:
            return None, f"schedule must be one of: {', '.join(SCHEDULES)}"
        out["schedule"] = value["schedule"]
    if out["full_history"] and out["first_posts"] is not None:
        return None, "the first sync is either the full history or only the last posts, not both"
    return out, None


def clean_options(value, base=None, tool="instaloader", platform="instagram", target=None, stories_daily=True):
    """parse_options' options, or None when malformed."""
    return parse_options(value, base, tool, platform, target, stories_daily)[0]


def login_refused(options, platform, session):
    """The message refusing what ``options`` fetch when it needs a login
    that ``session`` (the one a sync would use) is not, else None."""
    need = [k for k in (options["content"] or ()) if platform == "instagram" and k in LOGIN_CONTENT]
    if not need or session["mode"] != "none":
        return None
    return LOGIN_REFUSED.format(kinds=", ".join(need).capitalize())


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
    result = last_result(row)
    job = active.get(row["id"])
    return {
        "id": row["id"], "tool": row["tool"], "platform": row["platform"], "target": row["target"],
        "url": row["target"] if row["tool"] != "instaloader" else db.profile_url(row["platform"], row["target"]),
        "folder": row["folder"],
        "account": {"platform": key[0], "id": key[1]} if key else None,
        "person": person,
        "options": stored_options(row),
        "choices": choices(row["tool"], row["platform"], row["target"]),
        "created_at": row["created_at"], "last_sync_at": row["last_sync_at"],
        "last_job_id": row["last_job_id"],
        "last_result": result,
        "health": health.public(result, row["last_sync_at"], failures(result), row["target"]),
        "job": job,
    }


def stored_options(src):
    """A stored source's options, checked again one by one: a malformed
    value gets its default and the others stay (sources.json can be edited
    by hand). A source stored without a schedule has none: its stories do
    not pick one."""
    try:
        stored = json.loads(src["options"] or "{}")
    except ValueError:
        stored = None
    where = {"tool": src["tool"], "platform": src["platform"], "target": src["target"]}
    out = clean_options(None, **where)
    for key in OPTION_KEYS:
        if isinstance(stored, dict) and key in stored:
            out = clean_options({key: stored[key]}, out, **where, stories_daily=False) or out
    return out


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


def of_person(conn, pid):
    """The ids of a person's sources, as listing() shows them (sources._owner)."""
    accounts = db.accounts(conn)
    return [r["id"] for r in conn.execute("SELECT * FROM sources ORDER BY target, tool").fetchall()
            if (_owner(conn, r, accounts)[1] or {}).get("id") == pid]


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


# A folder whose posts are this much one account's is that account's own
# folder; a folder holding one is never another account's.
OWN_SHARE = 0.9


def _under_root(folder, roots):
    """The media root ``folder`` is strictly inside, or None."""
    for r in roots:
        r = r.rstrip(os.sep)
        if folder.startswith(r + os.sep):
            return r
    return None


def _folder_counts(conn, rows, roots, accounts):
    """From ``rows`` of (platform, author id, folder, posts, instaloader
    posts), each folder's posts by account (canonical): right in it, under
    it (itself included), and instaloader's under it. Folders inside a media
    root only: posts right in a root are in no folder."""
    direct, under, insta = {}, {}, {}

    def add(table, folder, key, n):
        if n:
            f = table.setdefault(folder, {})
            f[key] = f.get(key, 0) + n

    for platform, aid, folder, n, ni in rows:
        folder = folder.rstrip(os.sep)
        root = _under_root(folder, roots)
        if root is None:
            continue
        key = people.canonical(conn, platform, aid)
        if key not in accounts:
            continue
        add(direct, folder, key, n)
        parts = folder[len(root) + 1:].split(os.sep)
        for i in range(1, len(parts) + 1):
            f = os.path.join(root, *parts[:i])
            add(under, f, key, n)
            add(insta, f, key, ni)
    return direct, under, insta


def _main(counts):
    return max(counts, key=lambda k: (counts[k], k))


def _own_folders(direct, under):
    """{folder: account key} of the folders that are one account's own, as
    the tools lay them out (instaloader ``<root>/<name>``, gallery-dl and
    yt-dlp ``<root>/<platform>/<name>``, or wherever a source was pointed):
    posts right in it, most of them that account's, most of the posts under
    it that account's, and no folder under it that is nearly all
    (OWN_SHARE) another account's. A platform folder (``<root>/instagram``
    with a folder per account) is never one: it has no posts of its own, or
    holds other accounts' folders. Of an account's nested folders (a
    profile's highlight subfolders) only the topmost is kept."""
    holds = {}                                 # folder -> accounts with a folder under it
    for f, c in under.items():
        k = _main(c)
        if c[k] >= OWN_SHARE * sum(c.values()):
            p = os.path.dirname(f)
            while p in under:
                holds.setdefault(p, set()).add(k)
                p = os.path.dirname(p)
    own = {}
    for f, c in direct.items():
        k = _main(c)
        if under[f][k] * 2 > sum(under[f].values()) and not holds.get(f, set()) - {k}:
            own[f] = k

    def nested(f, k):
        p = os.path.dirname(f)
        while p in under:
            if own.get(p) == k:
                return True
            p = os.path.dirname(p)
        return False
    return {f: k for f, k in own.items() if not nested(f, k)}


# Posts by account and folder (meta_path up to its last "/"), and how many
# of them instaloader's.
_BY_FOLDER = ("SELECT platform, author_id, rtrim(meta_path, replace(meta_path, '/', '')), COUNT(*), "
              "SUM(tool LIKE 'instaloader%') FROM posts WHERE author_id IS NOT NULL")


def _as_indexed(folder, roots):
    """``folder`` with symlinks followed, as the scanner sees it under its
    media root (posts are indexed by those paths), else as given."""
    real = os.path.realpath(folder)
    for r in roots:
        rr = os.path.realpath(r)
        if real == rr or real.startswith(rr.rstrip(os.sep) + os.sep):
            return os.path.normpath(os.path.join(r, os.path.relpath(real, rr)))
    return os.path.normpath(folder)


def folder_owners(conn, folder, roots):
    """The accounts (canonical keys) with their own folder (_own_folders) at
    ``folder`` or under it, symlinks followed."""
    prefix = _as_indexed(folder, roots).rstrip(os.sep) + os.sep
    rows = conn.execute(_BY_FOLDER + " AND substr(meta_path, 1, ?) = ? GROUP BY 1, 2, 3",
                        (len(prefix), prefix)).fetchall()
    direct, under, _ = _folder_counts(conn, rows, roots, db.accounts(conn))
    return set(_own_folders(direct, under).values())


def platform_folder(folder, roots, table):
    """Whether ``folder`` is right under a media root and named after a
    platform or a tool (``<root>/instagram``, ``<root>/gallery-dl``): where
    each account gets its own folder, not one account's. Symlinks followed."""
    folder = _as_indexed(folder, roots)
    names = {*HOST_PLATFORMS.values(), *(platform_of(h) for h in table), *TOOLS}
    return (os.path.dirname(folder) in {os.path.normpath(r) for r in roots}
            and os.path.basename(folder).lower() in names)


def suggestions(conn, roots):
    """Profile folders with instaloader posts and no source yet, offered as
    sources for the user to confirm (never created on their own): one per
    account, its own folder (_own_folders: never one holding other accounts'
    folders, never _saved) with the most of its instaloader posts, counted;
    as target the account's current handle (else the folder's name). Only
    metadata names a handle reliably: for an account known from file names
    alone (its id is the folder's name, not a number), the folder's name is
    the target, as a stray file named after someone else would set its handle."""
    def compute(conn):
        accounts = db.accounts(conn)
        taken = [r[0].rstrip(os.sep) for r in conn.execute("SELECT folder FROM sources")]
        taken_targets = {r[0] for r in conn.execute("SELECT target FROM sources WHERE tool = 'instaloader'")}
        # Posts in _saved left out, or they could stand for an account's folder.
        saved = [os.path.join(r.rstrip(os.sep), SAVED) + os.sep for r in roots]
        rows = conn.execute(
            _BY_FOLDER + "".join(" AND substr(meta_path, 1, ?) != ?" for _ in saved) + " GROUP BY 1, 2, 3",
            [x for d in saved for x in (len(d), d)]).fetchall()
        direct, under, insta = _folder_counts(conn, rows, roots, accounts)
        best = {}                              # account -> (its instaloader posts there, folder)
        for folder, key in _own_folders(direct, under).items():
            n = insta.get(folder, {}).get(key, 0)
            if key[0] != "instagram" or not n:
                continue
            if any(folder == t or folder.startswith(t + os.sep) for t in taken):
                continue                       # a source has it already
            if key not in best or (n, folder) > best[key]:
                best[key] = (n, folder)
        out = []
        for key, (n, folder) in sorted(best.items(), key=lambda x: x[1][1]):
            a = accounts[key]
            name = os.path.basename(folder).lower()
            handle = (a["handle"] or "").lower() if key[1].isdigit() else ""
            target = next((t for t in (handle, name)
                           if _HANDLE_RE.fullmatch(t) and t not in people._NOT_HANDLES), None)
            if target is None or target in taken_targets:
                continue
            out.append({"tool": "instaloader", "platform": "instagram", "target": target, "folder": folder,
                        "account": {"platform": key[0], "id": key[1]}, "handle": a["handle"],
                        "count": n, "person": a["person"]})
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
    else:
        platform = platform_of(route(urlsplit(target).hostname, table)[0])
    if folder is None:
        folder = default_folder(tool, platform, target, roots)
    real = inside_root(folder, roots)
    if real is None:
        raise Refused("the folder must be inside a media root")
    if in_saved(real, roots):
        raise Refused(SAVED_REFUSED.format(folder=real))
    if os.path.exists(real) and not os.path.isdir(real):
        raise Refused("the folder path is a file")
    # A source's syncs write into its folder: one account's own, never a
    # media root or a platform's folder holding everyone's.
    default = os.path.normpath(default_folder(tool, platform, target, roots))
    if any(os.path.realpath(real) == os.path.realpath(r) for r in roots):
        raise Refused(f"{real} is a media root: pick the account's own folder inside it, such as {default}")
    if platform_folder(real, roots, table) and os.path.realpath(real) != os.path.realpath(default):
        raise Refused(f"{real} is a platform's folder, where each account has a folder of its own: "
                      f"pick the account's, such as {default}")
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
    others = folder_owners(conn, real, roots) - {key}
    if others:
        accounts = db.accounts(conn)
        names = sorted(accounts[k]["handle"] or k[1] for k in others)
        shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        raise Refused(f"{real} holds other accounts' posts ({shown}): pick the account's own folder, "
                      f"such as {default}")
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


def update(conn, sid, options, keys=None):
    """Store a source's options. ``keys``: only these change, in one
    statement over what is stored, so a sync's end (full history, last N)
    and a schedule changed meanwhile never undo each other."""
    with conn:
        if keys is None:
            conn.execute("UPDATE sources SET options = ? WHERE id = ?", (json.dumps(options), sid))
            return
        paths = [a for k in keys for a in (f"$.{k}", json.dumps(options[k]))]
        conn.execute(
            "UPDATE sources SET options = CASE WHEN json_valid(options) AND json_type(options) = 'object' "
            f"THEN json_set(options{', ?, json(?)' * len(keys)}) ELSE ? END WHERE id = ?",
            (*paths, json.dumps(options), sid))


def last_result(row):
    """A source row's last_result as stored: the object, or None (none yet,
    not JSON or not an object: sources.json can be edited by hand)."""
    try:
        r = json.loads(row["last_result"]) if row is not None and row["last_result"] else None
    except (TypeError, ValueError):
        return None
    return r if isinstance(r, dict) else None


def failures(result):
    """Failed syncs in a row in a stored last_result (any value), else 0."""
    n = result.get("failures") if isinstance(result, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


# A run that reported a new name worked: once the suggestion is answered its state is ok.
_ANSWERED = (", '$.health', CASE WHEN json_extract(last_result, '$.health') = 'renamed' THEN 'ok' "
             "ELSE json_extract(last_result, '$.health') END")


def rename(conn, sid, old, new, now):
    """Accept a rename suggestion: the source's target becomes ``new`` (its
    folder and files stay as they are) and the suggestion goes. False when
    the target is no longer ``old`` (changed meanwhile). A source the
    scheduler had stopped (account not found) is scheduled again. Its
    account, when it has one, keeps both handles in its history
    (handle_renames, user data)."""
    src = row(conn, sid)
    key = people.canonical(conn, src["platform"], src["author_id"]) if src and src["author_id"] else None
    with conn:
        done = conn.execute(
            "UPDATE sources SET target = ?, last_result = CASE WHEN json_valid(last_result) "
            "AND json_type(last_result) = 'object' THEN json_set(json_remove(last_result, '$.rename'), "
            f"'$.resumed', json('true'){_ANSWERED}) ELSE last_result END WHERE id = ? AND target = ?",
            (new, sid, old)).rowcount > 0
        if done and key:                       # only a rename that happened goes in the history
            conn.execute("INSERT OR REPLACE INTO handle_renames(platform, author_id, old, new, at) "
                         "VALUES (?, ?, ?, ?, ?)", (*key, old.lower(), new.lower(), now))
        return done


def resume(conn, sid):
    """Schedule again a source the scheduler stopped (health.paused): its
    schedule changed. Its next sync's result replaces the mark, and counts
    blocking results from none (health.record)."""
    with conn:
        conn.execute(
            "UPDATE sources SET last_result = json_set(last_result, '$.resumed', json('true')) "
            "WHERE id = ? AND json_valid(last_result) AND json_type(last_result) = 'object'", (sid,))


def resume_tool(conn, tool):
    """Schedule again the sources of ``tool`` the scheduler stopped, or
    counting blocking results towards a stop, that use the tool's session
    (none of their own): that session changed in Settings, the count starts
    again. Returns their ids."""
    ids = []
    for r in conn.execute("SELECT * FROM sources WHERE tool = ? ORDER BY id", (tool,)).fetchall():
        if health.blocking(last_result(r)) and stored_options(r)["session"] is None:
            resume(conn, r["id"])
            ids.append(r["id"])
    return ids


def dismiss_rename(conn, sid):
    """Forget a source's rename suggestion; the next one the tool reports comes back."""
    with conn:
        conn.execute(
            f"UPDATE sources SET last_result = json_set(json_remove(last_result, '$.rename'){_ANSWERED}) "
            "WHERE id = ? AND json_valid(last_result) AND json_type(last_result) = 'object'", (sid,))


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
