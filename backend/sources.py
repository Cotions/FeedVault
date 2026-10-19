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

import browsers
import config
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
# In a session error, after the browsers: the optional browser profile.
PROFILE_HINT = ', "profile": "<a browser profile\'s name, optional>"'
SESSION_MODES = ("none", "cookies", "login")
COOKIE_MODES = ("none", "cookies")             # gallery-dl and yt-dlp: a browser's cookies or nothing
ERRORS = ("login_required", "private", "not_found", "rate_limited", "missing", "setup", "generic")

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
    """A folder name for a profile link: its profile name, lowercase (``_name``
    and ``name_`` are other profiles than ``name``); else "profile"."""
    name = re.sub(r"[^a-z0-9._-]+", "_", (profile_handle(url) or "").lower()).strip(".")
    return name[:80] or "profile"


def resolve(text, table, roots):
    """What adding a source for a pasted link would make: {tool, platform,
    target, folder}, or raises Refused. An Instagram link with instaloader
    gives the profile name as target; any other tool, the normalized link.
    The folder is ``<first media root>/<platform>/<name>``: the target for
    instaloader, the link's profile name for the others."""
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
    """Where a new source's posts go unless another folder is picked:
    ``<first media root>/<platform>/<name>``, the name being instaloader's
    target as it is (a profile name), else the link's (folder_name)."""
    if tool == "instaloader":
        return os.path.join(roots[0], platform, target)
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
    {"mode": "cookies", "browser": "firefox"}, with an optional "profile"
    (a browser profile's name, browsers.clean_name; null as absent) |
    {"mode": "login", "user": "name"} (instaloader only), among ``modes``.
    FeedVault only passes on the browser's name, the folder of the browser
    profile named (browsers.py) or the user name; the tool reads the
    cookies or its own saved session file, never FeedVault."""
    if not isinstance(value, dict) or value.get("mode") not in modes:
        return None
    mode = value["mode"]
    if set(value) - {"mode", *{"cookies": ("browser", "profile"), "login": ("user",)}.get(mode, ())}:
        return None
    if mode == "cookies":
        if value.get("browser") not in BROWSERS:
            return None
        if value.get("profile") is None:
            return {"mode": mode, "browser": value["browser"]}
        profile = browsers.clean_name(value["browser"], value["profile"])
        return {"mode": mode, "browser": value["browser"], "profile": profile} if profile else None
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
# Which metadata files a sync writes beside the media (sync.metadata_flags):
# full (the tool's own), slim (instaloader's, slimmed after the run:
# slim.py), none (nothing beside the media; yt-dlp embeds it in the file).
METADATA = {"instaloader": ("full", "slim", "none"), "gallery-dl": ("full", "none"), "yt-dlp": ("full", "none")}
# Last: stored_options applies the keys in this order, and a stored
# schedule wins over the one content's stories would pick.
OPTION_KEYS = ("full_history", "session", "content", "media", "since", "first_posts", "script", "metadata",
               "schedule")
# What a platform's download defaults (Settings → Download defaults) can
# set: every option but full history, which is a one-off.
DEFAULT_KEYS = OPTION_KEYS[1:]
# A script's id (scripts.py): a file's name without its suffix, or a built-in's.
SCRIPT_ID_RE = re.compile(r"(?:builtin:)?[a-z0-9_-]{1,64}")
LOGIN_REFUSED = ("{kinds} need a logged-in session: choose one for this source, or set one in "
                 "Settings → Sync")
# Every option as a source has it when neither it nor its platform's
# defaults say otherwise.
BUILTIN = {"full_history": False, "session": None, "content": None, "media": "all", "since": None,
           "first_posts": None, "script": None, "metadata": None, "schedule": "off"}


def choices(tool, platform, target, defaults=False):
    """What the options of a source can be: {content (the kinds it can
    fetch, empty when the link picks), content_default, login (kinds that
    need a logged-in session), media, since, first_posts (whether each can
    be set), metadata (the modes its tool has)}. ``defaults``: a platform's
    download defaults, which offer the kinds of a profile's own page
    whatever the target (a source whose link picks ignores them)."""
    kinds = CONTENT.get((tool, platform), ())
    if tool == "gallery-dl" and kinds and not defaults:
        page = _PROFILE_PAGES[platform]
        if not isinstance(target, str) or not page.fullmatch(urlsplit(target).path):
            kinds = ()
    return {"content": list(kinds), "content_default": list(DEFAULT_CONTENT[(tool, platform)]) if kinds else [],
            "login": [k for k in kinds if platform == "instagram" and k in LOGIN_CONTENT],
            "media": tool in MEDIA_TOOLS, "since": True, "first_posts": tool in FIRST_POSTS_TOOLS,
            "metadata": list(METADATA.get(tool, ("full",)))}


def _since(value):
    """A YYYY-MM-DD date from 1970-01-01 to today, else None."""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return value if date(1970, 1, 1) <= day <= date.today() else None


def parse_options(value, base=None, tool="instaloader", platform="instagram", target=None, stories_daily=True,
                  defaults=False):
    """A source's options merged over ``base``: (options, None), or (None,
    error) when ``value`` is malformed or asks for what the source cannot
    do (see choices; ``defaults``: what a platform's defaults can). The
    options:

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
    - metadata: null (the tool's setting), else full, slim (instaloader
      only) or none: which metadata files its syncs write (METADATA);
    - schedule: off, hourly, daily or weekly. Stories last 24 h: content
      that turns them on makes an off schedule daily, unless ``value``
      sends a schedule too (``stories_daily`` False: never)."""
    out = {**BUILTIN, **(base or {})}
    if value is None:
        return out, None
    if not isinstance(value, dict):
        return None, "options must be an object"
    unknown = set(value) - set(OPTION_KEYS)
    if unknown:
        return None, f"unknown option {sorted(unknown)[0]!r}: options are {', '.join(OPTION_KEYS)}"
    can = choices(tool, platform, target, defaults)
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
                              f'{" | ".join(BROWSERS)}{PROFILE_HINT} }}'
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
    if "metadata" in value:
        if value["metadata"] is not None and value["metadata"] not in can["metadata"]:
            return None, f"metadata must be null or one of: {', '.join(can['metadata'])}"
        out["metadata"] = value["metadata"]
    if "schedule" in value:
        if value["schedule"] not in SCHEDULES:
            return None, f"schedule must be one of: {', '.join(SCHEDULES)}"
        out["schedule"] = value["schedule"]
    if out["full_history"] and out["first_posts"] is not None:
        return None, "the first sync is either the full history or only the last posts, not both"
    return out, None


def clean_options(value, base=None, tool="instaloader", platform="instagram", target=None, stories_daily=True,
                  defaults=False):
    """parse_options' options, or None when malformed."""
    return parse_options(value, base, tool, platform, target, stories_daily, defaults)[0]


# ---------------------------------------------------------------------------
# Download defaults and what a source inherits
# ---------------------------------------------------------------------------
#
# config ``source_defaults``: {tool: {platform: {option: value}}}, set in
# Settings → Download defaults. A source stores only its overrides: the
# options it sets itself, as {"v": 2, option: value, ...} (OVERRIDES). Any
# other option is inherited: its platform's default when there is one,
# else BUILTIN. So a default changed in Settings reaches every source that
# does not set that option, at its next sync. Options as stored before
# (every key, no "v") are read as overrides of what differs from BUILTIN.

OVERRIDES = 2                                  # "v" of a source's options as stored now
_PLATFORM_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


def defaults_of(cfg, tool, platform):
    """The download defaults set for ``tool`` on ``platform`` ({option:
    value}, only those set), each checked again (config.json can be edited
    by hand): one that is malformed is left out."""
    block = ((cfg or {}).get("source_defaults") or {})
    block = block.get(tool) if isinstance(block, dict) else None
    block = block.get(platform) if isinstance(block, dict) else None
    if not isinstance(block, dict):
        return {}
    out, where = {}, {"tool": tool, "platform": platform, "target": None, "defaults": True}
    for key in DEFAULT_KEYS:
        got = clean_options({key: block[key]}, **where, stories_daily=False) if key in block else None
        if got is not None:
            out[key] = got[key]
    return out


def clean_defaults(value, current=None):
    """The download defaults from POST /api/config ({tool: {platform:
    {option: value}}}), merged over ``current`` per tool and platform (a
    platform sent replaces its block; null or {} removes it), or (None,
    error). An option equal to BUILTIN is not kept: no default."""
    if not isinstance(value, dict) or set(value) - set(TOOLS):
        return None, f"source_defaults must be {{ tool: {{ platform: {{ option: value }} }} }}, tools: {', '.join(TOOLS)}"
    out = {t: dict(p) for t, p in (current or {}).items() if t in TOOLS and isinstance(p, dict)}
    for tool, platforms in value.items():
        if not isinstance(platforms, dict) or len(platforms) > ROUTES_MAX:
            return None, f"{tool}: send {{ platform: {{ option: value }} }}"
        for platform, block in platforms.items():
            if not isinstance(platform, str) or not _PLATFORM_RE.fullmatch(platform) \
                    or (tool == "instaloader" and platform != PLATFORM[tool]):
                return None, f"{tool}: not a platform it downloads from: {str(platform)[:64]}"
            if block is None or block == {}:
                out.get(tool, {}).pop(platform, None)
                continue
            if not isinstance(block, dict):
                return None, f"{tool} {platform}: send {{ option: value }}"
            if set(block) - set(DEFAULT_KEYS):
                return None, f"{tool} {platform}: defaults are {', '.join(DEFAULT_KEYS)}"
            options, error = parse_options(block, tool=tool, platform=platform, stories_daily=False, defaults=True)
            if error:
                return None, f"{tool} {platform}: {error}"
            kept = {k: options[k] for k in DEFAULT_KEYS if k in block and options[k] != BUILTIN[k]}
            if kept:
                out.setdefault(tool, {})[platform] = kept
            else:
                out.get(tool, {}).pop(platform, None)
    return {t: p for t, p in out.items() if p}, None


def _stored(src):
    """The overrides a source row stores, as stored (unchecked): {option:
    value}. Options stored before overrides (no "v") keep what differs from
    BUILTIN; anything else is inherited."""
    try:
        stored = json.loads(src["options"] or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    if stored.get("v") == OVERRIDES:
        return {k: v for k, v in stored.items() if k in OPTION_KEYS}
    return {k: v for k, v in stored.items() if k in OPTION_KEYS and v != BUILTIN[k]}


def synced_ok(src):
    """Whether a sync of the source has worked (ended done) once: a failed
    or cancelled first sync leaves it a first sync, for "last N". A row
    synced with no result state stored (from before results were kept, or
    one edited by hand) counts as synced: an established source."""
    if src["last_sync_at"] is None:
        return False
    result = last_result(src)
    if not isinstance((result or {}).get("state"), str):
        return True
    return health.public(result, src["last_sync_at"], 0)["ok_at"] is not None


def _resolve(src, stored, cfg):
    """(options, overridden keys): ``stored`` (a row's overrides) over its
    platform's defaults over BUILTIN, option by option, each checked again:
    a malformed value is skipped. Of the defaults, only what the source can
    have: its kinds (none when its link picks), and "last N" only before
    its first sync (it is for that one) and never with full history."""
    where = {"tool": src["tool"], "platform": src["platform"], "target": src["target"]}
    out = clean_options(None, **where)
    defaults = defaults_of(cfg, src["tool"], src["platform"])
    can = choices(**where)
    overridden = []
    for key in OPTION_KEYS:
        if key in stored:
            got = clean_options({key: stored[key]}, out, **where, stories_daily=False)
            if got is not None:
                out = got
                overridden.append(key)
                continue
        if key not in defaults:
            continue
        if key == "content" and not can["content"]:
            continue
        if key == "first_posts" and (synced_ok(src) or out["full_history"]):
            continue
        out = clean_options({key: defaults[key]}, out, **where, stories_daily=False) or out
    return out, overridden


def stored_options(src, cfg=None):
    """A stored source's options as its syncs use them: its overrides, else
    its platform's defaults (``cfg``: the config, read when not given),
    else BUILTIN; each checked again one by one, a malformed value skipped
    (sources.json can be edited by hand). A source stored without a
    schedule has the default one: its stories do not pick one."""
    return _resolve(src, _stored(src), cfg if cfg is not None else config.load())[0]


def inherited(src, cfg=None):
    """What a source's options would be with no overrides: what each
    option it does not set follows."""
    return _resolve(src, {}, cfg if cfg is not None else config.load())[0]


def overridden(src, cfg=None):
    """The options a source sets itself (valid ones), in OPTION_KEYS order."""
    return _resolve(src, _stored(src), cfg if cfg is not None else config.load())[1]


def overrides_of(options, base):
    """The options to store for a source: those of ``options`` that differ
    from ``base`` (what it would inherit), as stored ({"v": 2, ...})."""
    return {"v": OVERRIDES, **{k: options[k] for k in OPTION_KEYS if k in options and options[k] != base[k]}}


DEFAULT_PAIRS = (("instaloader", "instagram"), ("gallery-dl", "twitter"), ("gallery-dl", "bluesky"),
                 ("gallery-dl", "tiktok"), ("gallery-dl", "instagram"), ("yt-dlp", "youtube"), ("yt-dlp", "tiktok"))


def default_blocks(conn, cfg):
    """The (tool, platform) pairs Settings shows download defaults for: those
    the routing table routes to, those of a source, those with defaults
    set; each with its choices and its defaults (BUILTIN with the
    defaults over it), and how many sources set each option themselves."""
    table = routes(cfg)
    # Those a link can be routed to either way (Settings → Link routing)
    # are always there, so a platform's defaults can be set before it is.
    pairs = set(DEFAULT_PAIRS)
    pairs |= {(tool, PLATFORM.get(tool) or platform_of(host)) for host, tool in table.items()}
    rows = conn.execute("SELECT * FROM sources").fetchall()
    pairs |= {(r["tool"], r["platform"]) for r in rows if r["tool"] in TOOLS}
    stored = (cfg.get("source_defaults") or {}) if isinstance(cfg.get("source_defaults"), dict) else {}
    pairs |= {(t, p) for t, ps in stored.items() if t in TOOLS and isinstance(ps, dict) for p in ps
              if isinstance(p, str) and _PLATFORM_RE.fullmatch(p)}
    counts = {}
    for r in rows:
        for key in overridden(r, cfg):
            k = (r["tool"], r["platform"], key)
            counts[k] = counts.get(k, 0) + 1
    out = []
    for tool, platform in sorted(pairs, key=lambda p: (TOOLS.index(p[0]), p[1])):
        if tool == "instaloader" and platform != PLATFORM[tool]:
            continue
        set_ = defaults_of(cfg, tool, platform)
        out.append({"tool": tool, "platform": platform,
                    "choices": choices(tool, platform, None, defaults=True),
                    "defaults": {**{k: BUILTIN[k] for k in DEFAULT_KEYS}, **set_}, "set": sorted(set_),
                    "sources": sum(1 for r in rows if (r["tool"], r["platform"]) == (tool, platform)),
                    "overrides": {k: counts.get((tool, platform, k), 0) for k in DEFAULT_KEYS}})
    return out


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


def _public(conn, row, accounts, active, cfg=None):
    key, person = _owner(conn, row, accounts)
    result = last_result(row)
    job = active.get(row["id"])
    cfg = cfg if cfg is not None else config.load()
    options, overrides = _resolve(row, _stored(row), cfg)
    return {
        "id": row["id"], "tool": row["tool"], "platform": row["platform"], "target": row["target"],
        "url": row["target"] if row["tool"] != "instaloader" else db.profile_url(row["platform"], row["target"]),
        "folder": row["folder"],
        "account": {"platform": key[0], "id": key[1]} if key else None,
        "person": person,
        "options": options,
        # The options it sets itself; any other follows "defaults": what
        # it inherits (its platform's download defaults, else BUILTIN).
        "overrides": overrides,
        "defaults": inherited(row, cfg),
        "choices": choices(row["tool"], row["platform"], row["target"]),
        "created_at": row["created_at"], "last_sync_at": row["last_sync_at"],
        "last_job_id": row["last_job_id"],
        "synced": synced_ok(row),
        "last_result": result,
        "health": health.public(result, row["last_sync_at"], failures(result), row["target"]),
        "job": job,
    }


def get(conn, sid, active=None):
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone() if 0 <= sid < 2**53 else None
    return _public(conn, row, db.accounts(conn), active or {}) if row else None


def row(conn, sid):
    return conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone() if 0 <= sid < 2**53 else None


def listing(conn, roots, active=None):
    """Every source, by target, and the folders that could be one."""
    accounts, cfg = db.accounts(conn), config.load()
    rows = conn.execute("SELECT * FROM sources ORDER BY target, tool").fetchall()
    return {"sources": [_public(conn, r, accounts, active or {}, cfg) for r in rows],
            "suggestions": suggestions(conn, roots)}


def of_person(conn, pid):
    """The ids of a person's sources, as listing() shows them (sources._owner)."""
    accounts = db.accounts(conn)
    return [r["id"] for r in conn.execute("SELECT * FROM sources ORDER BY target, tool").fetchall()
            if (_owner(conn, r, accounts)[1] or {}).get("id") == pid]


def account_folder(conn, platform, author_id, roots):
    """The folder holding the account's instaloader posts (aliases included),
    the one with the most, or None: a folder right under a media root, or
    right under the platform's there (``<root>/instagram/<name>``)."""
    clause, args = db.post_filter(platform=platform, author=author_id)
    counts = {}
    for (path,) in conn.execute(f"SELECT p.meta_path {db._FROM} {clause} AND p.tool LIKE 'instaloader%'", args):
        top = _top(path, roots)
        if top and os.path.basename(top).lower() == platform:
            top = _top(path, [top]) or top
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
    the tools lay them out (``<root>/<platform>/<name>``, instaloader's
    ``<root>/<name>`` of older libraries, or wherever a source was pointed):
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

def create(conn, roots, tool, target, folder, person_id, account, options, now, table=None, pin=()):
    """A new source. ``target``: checked (check_target). ``folder``: an
    existing folder inside a media root, or None for resolve()'s default.
    ``account``: (platform, id) of an indexed account, else found from the
    folder's posts when it has some. ``options``: what it downloads (see
    update: what equals its platform's defaults is inherited), or None:
    everything inherited. ``pin``: options set on it even when equal to
    what it inherits (one another site created has no script or login,
    whatever the defaults say later)."""
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
    if platform_folder(real, roots, table):
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
        if key is None:
            key = _handle_account(conn, platform, target if tool == "instaloader" else profile_handle(target))
            # An account known from file names alone (its id is its folder's
            # name) is that folder's, not the profile's: the first sync finds it.
            if key and tool == "instaloader" and not key[1].isdigit():
                key = None
    others = folder_owners(conn, real, roots) - {key}
    if others:
        accounts = db.accounts(conn)
        names = sorted(accounts[k]["handle"] or k[1] for k in others)
        shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        raise Refused(f"{real} holds other accounts' posts ({shown}): pick the account's own folder, "
                      f"such as {default}")
    if person_id is not None and not people.exists(conn, person_id):
        raise Refused("no such person")
    base = inherited(new_row(tool, platform, target))
    stored = overrides_of(options, base) if options is not None else {"v": OVERRIDES}
    stored.update({k: options[k] for k in pin if options is not None})
    with conn:
        sid = conn.execute(
            "INSERT INTO sources(person_id, platform, author_id, tool, target, folder, options, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (person_id, platform, key[1] if key else None, tool, target, real, json.dumps(stored), now)).lastrowid
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


def new_row(tool, platform, target):
    """What _resolve reads of a source not stored yet."""
    return {"tool": tool, "platform": platform, "target": target, "last_sync_at": None, "options": None}


def update(conn, sid, options, keys=None, cfg=None):
    """Store a source's options (as its syncs would use them): an option
    equal to what it inherits is stored as inherited, so it follows its
    platform's defaults from then on; any other is its override.
    ``keys``: only these change, in one statement over what is stored, so
    a sync's end (full history, last N) and a schedule changed meanwhile
    never undo each other."""
    src = row(conn, sid)
    if src is None:
        return
    cfg = cfg if cfg is not None else config.load()
    base = inherited(src, cfg)
    with conn:
        if keys is None:
            conn.execute("UPDATE sources SET options = ? WHERE id = ?",
                         (json.dumps(overrides_of(options, base)), sid))
            return
        # Options stored before overrides are rewritten whole first.
        whole = overrides_of({**_resolve(src, _stored(src), cfg)[0], **{k: options[k] for k in keys}}, base)
        kept = [k for k in keys if options[k] != base[k]]
        gone = [k for k in keys if options[k] == base[k]]
        paths = [a for k in kept for a in (f"$.{k}", json.dumps(options[k]))]
        conn.execute(
            "UPDATE sources SET options = CASE WHEN json_valid(options) AND json_type(options) = 'object' "
            "AND json_extract(options, '$.v') = ? "
            f"THEN json_set(json_remove(options{', ?' * len(gone)}){', ?, json(?)' * len(kept)}) "
            "ELSE ? END WHERE id = ?",
            (OVERRIDES, *(f"$.{k}" for k in gone), *paths, json.dumps(whole), sid))


def write(conn, sid, pin, drop=()):
    """What a source's form saves: ``pin`` ({option: value}, those the
    user changed) become its overrides, even one equal to what it inherits
    now, so a later default does not move it; ``drop`` (those reset) are
    inherited again. Any other stays as it was, set or inherited: a form
    opened before a default changed does not pin the old one. In one
    statement over what is stored, as update() with ``keys``."""
    src = row(conn, sid)
    if src is None:
        return
    drop = [k for k in drop if k not in pin]
    # Options stored before overrides are rewritten whole first.
    whole = {"v": OVERRIDES, **{k: v for k, v in _stored(src).items() if k not in drop}, **pin}
    paths = [a for k, v in pin.items() for a in (f"$.{k}", json.dumps(v))]
    with conn:
        conn.execute(
            "UPDATE sources SET options = CASE WHEN json_valid(options) AND json_type(options) = 'object' "
            "AND json_extract(options, '$.v') = ? "
            f"THEN json_set(json_remove(options{', ?' * len(drop)}){', ?, json(?)' * len(pin)}) "
            "ELSE ? END WHERE id = ?",
            (OVERRIDES, *(f"$.{k}" for k in drop), *paths, json.dumps(whole), sid))


def reset(conn, sid, keys=None):
    """Drop a source's overrides of ``keys`` (None: all of them): it
    inherits those options again."""
    src = row(conn, sid)
    if src is None:
        return
    left = {k: v for k, v in _stored(src).items() if keys is not None and k not in keys}
    with conn:
        conn.execute("UPDATE sources SET options = ? WHERE id = ?", (json.dumps({"v": OVERRIDES, **left}), sid))


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
    ids, cfg = [], config.load()
    for r in conn.execute("SELECT * FROM sources WHERE tool = ? ORDER BY id", (tool,)).fetchall():
        if health.blocking(last_result(r)) and stored_options(r, cfg)["session"] is None:
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
