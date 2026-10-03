"""Profile sync: a source's tool run as a job, only for what is new.

The job takes a source id and nothing else; its argument list is built here
from the stored source (sources.py) and the settings, never from a request,
and the stored target is checked again first. One job kind per tool, each
its own lock group (one run of a tool at a time).

instaloader:

- ``--latest-stamps <data_dir>/instaloader/stamps.ini``: instaloader keeps
  the time of each profile's newest downloaded post there, away from the
  media, so it stops at it whatever files exist (trashed posts stay gone).
  It keeps no list of deleted posts: a trashed post newer than the stamp
  (deleted before the sync that passed it) comes back, and goes straight
  back to the trash after the sync (retrash), with a line in its log. The
  list it checks is kept in ``<data_dir>/instaloader/retrash/`` while the
  sync runs, so a sync FeedVault stopped is re-trashed at the next start.
- ``--fast-update`` (stop at the first post whose files exist) only for a
  first sync with no stamp, where it is the only stopping point. With a
  stamp it would stop at a post saved on its own (the userscript's Save, a
  download by hand) newer than the stamp, and never fetch the posts between
  them: the stamp alone limits the walk, and instaloader skips files that
  exist one by one. Decided from the stamps file when the job is queued,
  and again right before it starts (after seeding).
- the first sync of a source seeds that file with the newest post FeedVault
  already has for the account, so it never walks the whole profile again
  (unless the source asks for its full history). Only dates that can be
  trusted count (see trusted_newest): a seed after a post never downloaded
  would skip it for good.
- metadata on (``--no-compress-json``), so new posts get captions and the
  account's numeric id; people.refresh_aliases links them to the folder's
  older filename-only posts.
- ``--filename-pattern`` as the folder's files are already named, so new
  files sit beside the old ones and a post already there is recognised.
- ``--sanitize-paths`` when the folder is on exFAT, FAT or NTFS (statfs,
  see windows_names): tagged posts and highlights have a ``:`` in their
  names otherwise, which those refuse.

gallery-dl and yt-dlp (archives.py):

- ``--download-archive`` in the data directory: the tool skips what it lists,
  whatever files exist; trashing a post adds it there (never again).
- ``-o skip=abort:5`` (gallery-dl) and ``--break-on-existing`` (yt-dlp) stop
  the run at what is already there, so a sync with nothing new is quick;
  yt-dlp only on platforms that list a profile newest first with nothing
  older in front (STOPS_AT_ARCHIVED).
- the first sync of a source seeds the archive with the posts already
  indexed for its account.
- the user's own config files are skipped (``--config-ignore``,
  ``--ignore-config``) when the tool's ``ignore_config`` setting is on.
  Without it, a sync whose new files no parser can read says the config
  is the likely cause (_unread).
- metadata on (``--write-metadata``; ``--write-info-json --write-thumbnail``),
  into the source's folder; YouTube videos longer than ``youtube_max_seconds``
  are not downloaded (ChannelVault's).
- after a yt-dlp sync, the info JSONs it wrote are rewritten without the
  cookies yt-dlp copies into them (info_cookies.py), before the folder is
  indexed.

What a source downloads (its options, sources.parse_options):

- content: instaloader ``--reels --stories --highlights --tagged``, and
  ``--no-posts`` without posts; gallery-dl ``-o include=…`` (a profile's
  own page only: its user extractor dispatches to one per kind). yt-dlp
  has none: the link picks (a YouTube tab).
- media: instaloader images: ``--post-filter`` (and ``--storyitem-filter``)
  ``not is_video``, plus ``--no-videos --no-video-thumbnails`` (a
  carousel's videos). Videos: ``--no-pictures``, file by file, so a
  carousel keeps its videos; it leaves an image post its metadata only,
  which the parser does not index (no media). Story items ignore
  --no-pictures: ``--storyitem-filter is_video`` for them. --no-pictures
  cannot go with --fast-update (instaloader refuses): fast_update() is
  False with it. gallery-dl, file by file: ``--filter "extension in
  exts_video"`` / ``exts_image``.
- since (a floor): instaloader ``date_utc >= datetime(Y, M, D)`` in the
  same filters; gallery-dl ``--date-after`` (it stops at the first older
  post) where a profile lists newest first with nothing pinned in front
  (DATE_AFTER_STOPS), else ``date >= datetime(Y, M, D)`` in ``--filter``;
  yt-dlp ``--dateafter``, plus ``--break-match-filters`` to stop at the
  first older video where it may stop at all (as for --break-on-existing).
- first_posts (the first sync's newest N): gallery-dl ``--post-range
  1-N`` (each kind's extractor has its own: N per kind), yt-dlp
  ``--playlist-items 1:N`` (each level: a YouTube channel's page gets N
  per tab). instaloader has no way to (``--count`` is not for
  profiles): refused when the source is saved.

The filters instaloader and gallery-dl evaluate as Python are fixed text
and the three numbers of a date checked by sources.py (a ``%d`` each),
nothing else: no text a user typed ever reaches them.

How they meet the stopping points:

- instaloader keeps a stamp per kind: ``post-timestamp``,
  ``reels-timestamp``, ``tagged-timestamp``, ``story-timestamp``;
  highlights have none (walked, files there skipped). A kind turned on
  later has no stamp yet, so its first sync walks it all: nothing is
  missed. --fast-update stays off then (it is only for a first sync of
  posts, see below), and off with reels at all: reels are walked before
  posts, and a reel on the grid is the same file, so the posts would stop
  at it.
- a floor raises each of those stamps, when missing or older, to just
  before it, after the first-sync seed (_floor_stamps), so the walk stops
  there; the filter still drops anything older that comes through (a
  pinned post). Raising the floor later just filters; lowering it, or
  widening media, does not bring back what the stamps passed: that is
  what full history is for (it drops the post, reels and tagged stamps,
  then walks back to the floor).
- "last N" is for the first sync only: set back to null once a sync
  succeeds (like full history), and the API refuses setting it on a
  source that has synced without it. Options cannot change while a sync
  is queued or running, as its end sets these two back. gallery-dl and
  yt-dlp still seed their archive
  first, so posts already indexed are skipped within those N. The day of
  the oldest post it added or its archive skipped (today at the latest)
  becomes the source's floor (unless it has a later one): the archive
  alone would not keep the next sync from going on to the older posts
  (gallery-dl stops at 5 files in a row it has, fewer than N may be;
  TikTok and a YouTube channel's page never stop early). A sync that
  neither added nor listed a post FeedVault knows keeps "last N" for the
  next run instead.
- stories, highlights and tagged posts need a logged-in session (Instagram
  shows them to logged-in viewers only): refused when the source is saved
  without one, and again at sync time (the setting can change since).

How it went is read from the output (login required, private, not found,
rate limited: health.py's tables) and stored on the source. Two syncs of one tool pause between
them (config ``<tool>.pause``).
"""
import configparser
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import archives
import config
import db
import health
import info_cookies
import jobs
import news
import notify
import people
import scanner
import sources
import trash
import userdata
from parsers import is_media, yt_dlp
from parsers.instaloader import _HANDLE_RE as _TARGET_RE, _NAME_RE, _SPACED_RE, _day_start

KIND = "instaloader-sync"
GROUP = "instaloader"
KINDS = {"instaloader": KIND, "gallery-dl": "gallery-dl-sync", "yt-dlp": "yt-dlp-sync"}
SCRIPT_KIND = "script-sync"                    # a source's own script instead (scripts.py)
YT_DLP_NAME = "%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s"
BREAK_ON_EXISTING = 101                        # yt-dlp's exit code when --break-on-existing stopped it
PAUSE_DEFAULT = 60
TOOL_PAUSE_DEFAULT = 30                        # gallery-dl and yt-dlp
PAUSE_MAX = 3600

DATED = "{target}-{date_utc:%Y-%m-%d}-{shortcode}"
SPACED = "{target} - {shortcode}"
STAMPED = "{date_utc}_UTC"
TITLE = "{date_utc}_UTC_{typename}"            # profile pictures: named as parsers.instaloader._SIDE_RE expects
CLEAN_SHARE = 0.9                              # of a folder's media files, for a pattern to count as detected cleanly

_STAMPED_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC(?:_\d+)?\.[A-Za-z0-9]+")
_SIDE_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC_(?:profile_pic|cover)\.[A-Za-z0-9]+")
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"         # instaloader's LatestStamps.ISO_FORMAT


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _pause(value, default):
    ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= PAUSE_MAX
    return value if ok else default


def settings(cfg=None):
    """The global instaloader settings, cleaned: {"session": {...}, "pause": seconds}."""
    raw = (cfg or config.load()).get("instaloader") or {}
    session = sources.clean_session(raw.get("session")) or {"mode": "none"}
    return {"session": session, "pause": _pause(raw.get("pause"), PAUSE_DEFAULT)}


def clean_settings(value, current):
    """Settings from POST /api/config merged over ``current``. Returns (settings, error)."""
    if not isinstance(value, dict) or set(value) - {"session", "pause"}:
        return None, "instaloader must be { session, pause }"
    out = dict(current)
    if "session" in value:
        out["session"] = sources.clean_session(value["session"])
        if out["session"] is None:
            return None, ('session must be { "mode": "none" }, { "mode": "cookies", "browser": '
                          f'{" | ".join(sources.BROWSERS)} }} or {{ "mode": "login", "user": "<name>" }}')
    if "pause" in value:
        if _pause(value["pause"], None) is None:
            return None, f"pause must be whole seconds from 0 to {PAUSE_MAX}"
        out["pause"] = value["pause"]
    return out, None


def session_flags(session):
    if session["mode"] == "cookies":
        return ["--load-cookies", session["browser"]]
    if session["mode"] == "login":
        return ["--login", session["user"]]
    return []


# The flag that makes each tool skip the user's own config files: a config
# can change the output layout, turn off metadata or add postprocessors that
# run commands. Off by default, as people keep their credentials there.
IGNORE_CONFIG = {"gallery-dl": "--config-ignore", "yt-dlp": "--ignore-config"}


def tool_settings(tool, cfg=None):
    """gallery-dl's or yt-dlp's settings, cleaned: {"session": {...}, "pause":
    seconds, "ignore_config": bool}, the session "none" or a browser's
    cookies (they have no login of their own FeedVault could name)."""
    raw = (cfg or config.load()).get(tool) or {}
    session = sources.clean_session(raw.get("session"), sources.COOKIE_MODES) or {"mode": "none"}
    return {"session": session, "pause": _pause(raw.get("pause"), TOOL_PAUSE_DEFAULT),
            "ignore_config": raw.get("ignore_config") is True}


def clean_tool_settings(tool, value, current):
    """Settings from POST /api/config merged over ``current``. Returns (settings, error)."""
    if not isinstance(value, dict) or set(value) - {"session", "pause", "ignore_config"}:
        return None, f"{tool} must be {{ session, pause, ignore_config }}"
    if "ignore_config" in value and not isinstance(value["ignore_config"], bool):
        return None, "ignore_config must be true or false"
    out = dict(current)
    if "ignore_config" in value:
        out["ignore_config"] = value["ignore_config"]
    if "session" in value:
        out["session"] = sources.clean_session(value["session"], sources.COOKIE_MODES)
        if out["session"] is None:
            return None, ('session must be { "mode": "none" } or { "mode": "cookies", "browser": '
                          f'{" | ".join(sources.BROWSERS)} }}')
    if "pause" in value:
        if _pause(value["pause"], None) is None:
            return None, f"pause must be whole seconds from 0 to {PAUSE_MAX}"
        out["pause"] = value["pause"]
    return out, None


def cookie_flags(session):
    """--cookies-from-browser <browser>: the tool reads the browser's cookies, FeedVault never."""
    return ["--cookies-from-browser", session["browser"]] if session["mode"] == "cookies" else []


def config_flags(tool, cfg=None):
    """The tool's flag to skip the user's config files, when its setting says so."""
    return [IGNORE_CONFIG[tool]] if tool_settings(tool, cfg)["ignore_config"] else []


def session_of(tool, options, cfg=None):
    """The session a sync of a source uses: its own, else the tool's setting."""
    return options["session"] or (settings(cfg) if tool == "instaloader" else tool_settings(tool, cfg))["session"]


def stamps_path(cfg=None):
    return os.path.join((cfg or config.load())["data_directory"], "instaloader", "stamps.ini")


# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------

def detect_pattern(folder):
    """(filename pattern, clean) for the media files right in ``folder``:
    the layout most of them follow, and whether at least CLEAN_SHARE do.
    An empty or unreadable folder, or one where none fits, gets DATED."""
    try:
        names = [n for n in os.listdir(folder) if not n.startswith(".") and is_media(n)
                 and not _SIDE_RE.fullmatch(n)]
    except OSError:
        names = []
    counts = {DATED: 0, SPACED: 0, STAMPED: 0}
    for n in names:
        m = _NAME_RE.fullmatch(n)
        if m and _TARGET_RE.fullmatch(m["target"]):   # a highlight's title is not a profile name
            counts[DATED] += 1
        elif _SPACED_RE.fullmatch(n):
            counts[SPACED] += 1
        elif _STAMPED_RE.fullmatch(n):
            counts[STAMPED] += 1
    best = max(counts, key=lambda k: (counts[k], k == DATED))
    if not counts[best]:
        return DATED, not names
    return best, counts[best] >= CLEAN_SHARE * len(names)


# Filesystems that refuse ":" and the other characters Windows does in a
# name, by statfs f_type: instaloader names tagged posts ``<profile>/:tagged``
# and highlights after their title, so a sync there fails without
# --sanitize-paths. FUSE (ntfs-3g, exfat-fuse) only says "fuse": its mount
# type in /proc/self/mounts tells (fuseblk: a block device, NTFS or exFAT).
WINDOWS_NAMES = {0x2011BAB0: "exfat", 0x4D44: "vfat", 0x5346544E: "ntfs", 0x7366746E: "ntfs3"}
FUSE_MAGIC = 0x65735546
FUSE_WINDOWS = {"fuseblk", "fuse.exfat", "fuse.exfat-fuse", "fuse.ntfs-3g"}
MOUNTS = "/proc/self/mounts"


def fs_magic(path):
    """statfs(2) f_type of the filesystem ``path`` is on, or None (not
    Linux, or the call failed)."""
    import ctypes
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        buf = ctypes.create_string_buffer(512)             # struct statfs, f_type first
        if libc.statfs(os.fsencode(path), buf) != 0:
            return None
    except (OSError, AttributeError):
        return None
    return ctypes.c_long.from_buffer(buf).value & 0xFFFFFFFF


def mount_type(path):
    """The type /proc/self/mounts gives the mount ``path`` is on, or None."""
    real, best = os.path.realpath(path), (None, None)
    try:
        with open(MOUNTS, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                # Spaces and the like are octal escapes there (\040).
                point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), parts[1])
                inside = real == point or real.startswith(point.rstrip("/") + "/")
                if inside and (best[0] is None or len(point) >= len(best[0])):
                    best = (point, parts[2])
    except OSError:
        return None
    return best[1]


def windows_names(folder):
    """Whether ``folder`` is on a filesystem that refuses Windows' reserved
    characters (exFAT, FAT, NTFS): instaloader then gets --sanitize-paths,
    which makes its names valid there (the ``:`` of ``:tagged`` and of a
    highlight's title becomes a full-width colon, U+FF1A). It changes nothing FeedVault reads: the parser
    reads a synced post from its metadata, whatever its file's name, and
    detect_pattern only looks at the files right in the folder (tagged
    posts and highlights go in a subfolder named after the profile)."""
    magic = fs_magic(folder)
    if magic in WINDOWS_NAMES:
        return True
    return magic == FUSE_MAGIC and mount_type(folder) in FUSE_WINDOWS


def _escape(path):
    """A folder for --dirname-pattern, which instaloader runs through str.format."""
    return path.replace("{", "{{").replace("}", "}}")


# ---------------------------------------------------------------------------
# The job kind
# ---------------------------------------------------------------------------

def _source_id(params):
    v = params["source"]
    if not (v.isascii() and v.isdigit() and len(v) < 16):
        raise jobs.BadRequest("source must be a source id")
    return int(v)


def _build(params):
    """The argument list, from the stored source and the settings only."""
    sid = _source_id(params)
    src = sources.row(db.connect(), sid)
    if src is None:
        raise jobs.BadRequest("no such source")
    if src["tool"] != "instaloader":
        raise jobs.BadRequest(f"a {src['tool']} source cannot be synced yet")
    cfg = config.load()
    # Stored data is checked again: sources.json can be edited by hand.
    target = sources.parse_target("instaloader", src["target"])
    folder = sources.inside_root(src["folder"], cfg["media_roots"])
    if target is None or target != src["target"]:
        raise jobs.BadRequest("the source's target is not a profile name")
    if folder is None:
        raise jobs.BadRequest("the source's folder is not inside a media root")
    if sources.in_saved(folder, cfg["media_roots"]):
        raise jobs.BadRequest(sources.SAVED_REFUSED.format(folder=folder))
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise jobs.BadRequest(f"cannot create the source's folder: {e.strerror or e}")
    options = _options(src)
    session = session_of("instaloader", options, cfg)
    refused = sources.login_refused(options, src["platform"], session)
    if refused:
        raise jobs.BadRequest(refused)
    pattern, _ = detect_pattern(folder)
    stamps = configparser.ConfigParser(interpolation=None)
    stamps.read(stamps_path(cfg), encoding="utf-8")
    return {"tool": "instaloader", "rescan": folder, "args": [
        "--latest-stamps", stamps_path(cfg),
        *(["--fast-update"] if fast_update(stamps, target, options) else []),
        "--no-compress-json",
        "--dirname-pattern", _escape(folder),
        "--filename-pattern", pattern,
        "--title-pattern", TITLE,
        *(["--sanitize-paths"] if windows_names(folder) else []),
        *content_flags(options),
        *session_flags(session),
        "--", target,
    ]}


def _content(options, tool="instaloader", platform="instagram"):
    return options["content"] or sources.DEFAULT_CONTENT.get((tool, platform), ())


def fast_update(stamps, target, options):
    """Whether a sync passes --fast-update: only when the profile has no
    stamp yet (a first sync) and walks only what is new (no full history,
    which goes past the posts already there too), for posts, and not with
    reels: those walked first would stop the posts at the first reel of
    the grid, its file just written. Never with videos only: instaloader
    refuses --fast-update with --no-pictures."""
    content = _content(options)
    return not options["full_history"] and "posts" in content and "reels" not in content \
        and options["media"] != "videos" and not stamps.has_option(target, "post-timestamp")


# instaloader flags per content kind (posts are on unless --no-posts).
CONTENT_FLAGS = {"reels": "--reels", "stories": "--stories", "highlights": "--highlights", "tagged": "--tagged"}


def _floor(options):
    """(year, month, day) of options' since, ints, or None."""
    if options["since"] is None:
        return None
    day = datetime.strptime(options["since"], "%Y-%m-%d")
    return day.year, day.month, day.day


def item_filter(options, story=False):
    """The expression for --post-filter, or with ``story`` for
    --storyitem-filter (both instaloader.Post and StoryItem have is_video
    and date_utc), or None. Videos only is --no-pictures for posts, a
    filter for story items only (they ignore it). instaloader evaluates it
    as Python: it is made of fixed text and the three numbers of a checked
    date only, never of anything a user typed."""
    terms = {"images": ["not is_video"], "videos": ["is_video"] if story else []}.get(options["media"], [])
    floor = _floor(options)
    if floor is not None:
        terms.append("date_utc >= datetime(%d, %d, %d)" % floor)
    return " and ".join(terms) or None


def content_flags(options):
    """What an instaloader sync fetches (sources.CONTENT), which media and since when."""
    content = _content(options)
    expr, story = item_filter(options), item_filter(options, story=True)
    return [
        *(["--no-posts"] if "posts" not in content else []),
        *[CONTENT_FLAGS[k] for k in sources.CONTENT[("instaloader", "instagram")] if k in content and k != "posts"],
        # Images only: video posts are filtered out, a carousel's videos and their thumbnails are not fetched.
        *(["--no-videos", "--no-video-thumbnails"] if options["media"] == "images" else []),
        # Videos only: no picture is fetched, a carousel's videos are.
        *(["--no-pictures"] if options["media"] == "videos" else []),
        *(["--post-filter", expr] if expr else []),
        *(["--storyitem-filter", story] if story and {"stories", "highlights"} & set(content) else []),
    ]


def _with_stamps(args, stamps, target, options):
    """The argument list with --fast-update as fast_update() says now."""
    out = [a for a in args if a != "--fast-update"]
    if fast_update(stamps, target, options):
        out.insert(out.index("--latest-stamps") + 2, "--fast-update")
    return out


def _options(src):
    """A stored source's options, checked again (defaults when malformed)."""
    return sources.stored_options(src)


def _write_stamps(stamps, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        stamps.write(f)
    os.replace(tmp, path)


FILENAMES = "instaloader (filenames)"           # parsers.instaloader's tool for posts rebuilt from names
DAY = 86400


def trusted_newest(conn, platform, author_id, skip=(), saved=(), held=None):
    """The newest post time of an account (aliases included) that can seed
    instaloader's stamp, or None. A post with metadata has its real time; a
    post rebuilt from a dated file name counts until the end of that day
    (its mtime only within it); one from a name without a date (``{target} -
    {shortcode}``) has only its mtime, which a copy that did not keep it
    makes the copy's date, later than posts never downloaded: not counted.
    Posts in ``skip`` (ids) do not count either, nor those in ``saved``
    folders (the _saved folders: saved one by one, not synced); ``held``, a
    list, gets the ids of those."""
    clause, args = db.post_filter(platform=platform, author=author_id)
    newest = None
    for pid, tool, path, posted in conn.execute(
            f"SELECT p.id, p.tool, p.meta_path, p.posted_at {db._FROM} {clause} AND p.posted_at IS NOT NULL", args):
        if pid in skip or os.path.dirname(os.path.normpath(path)) in saved:
            if held is not None:
                held.append(pid)
            continue
        if tool == FILENAMES:
            m = _NAME_RE.fullmatch(os.path.basename(path))
            day = _day_start(m["date"]) if m else None
            if day is None:
                continue
            posted = min(posted, day + DAY - 1)
        newest = posted if newest is None else max(newest, posted)
    return newest


# A queued sync whose source is gone by the time it starts: removed, or the
# database was replaced under the queue.
GONE = "source {sid} no longer exists (removed, or the database was replaced): nothing to sync"


def queued_target(params, argv):
    """The target a sync was queued with: its ``target`` param (a script's
    sync, whose argv is the script's), else argv[-1]; None when unknown."""
    if "target" in params:
        return params["target"]
    return argv[-1] if argv else None


def _queued_source(conn, params, argv):
    """The source a sync about to start is for. Cancelled when it is gone,
    or when its id names another source now (the database was replaced):
    queued_target is the target the job was queued with."""
    src = sources.row(conn, _source_id(params))
    target = queued_target(params, argv)
    if src is None or (target is not None and src["target"] != target):
        raise jobs.Cancelled(GONE.format(sid=params["source"]))
    return src


def _start(params, note, argv=None):
    """Right before instaloader starts (no other instaloader runs): seed the
    stamps file on a source's first sync. Returns the argument list with
    --fast-update as the stamps file now says (see fast_update)."""
    import save                                # it imports this module
    conn = db.connect()
    src = _queued_source(conn, params, argv)
    options = _options(src)
    roots = config.load()["media_roots"]
    moved = save.gather(conn, src, roots, note)
    _seed_stamp(conn, src, options, note, moved, roots)
    _keep_trashed(src["id"], _trashed(conn, roots))
    if not argv:
        return None
    stamps = configparser.ConfigParser(interpolation=None)
    stamps.read(stamps_path(), encoding="utf-8")
    return _with_stamps(argv[1:], stamps, src["target"], options)


_trashed_before = {}                           # source id -> trashed posts not in the index when its sync starts


def _retrash_dir():
    return os.path.join(config.load()["data_directory"], "instaloader", "retrash")


def _retrash_path(sid):
    """Where a sync's _trashed_before is kept while it runs, for the next
    start when FeedVault stops before the sync ends (see resume)."""
    return os.path.join(_retrash_dir(), f"{int(sid)}.json")


def _keep_trashed(sid, ids):
    _trashed_before[sid] = ids
    path = _retrash_path(sid)
    if not ids:
        try:
            os.remove(path)                    # one a crash left, already done with
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "posts": sorted(ids)}, f)
    os.replace(tmp, path)


def _kept_trashed(sid):
    """Whether a list is kept for the sync of source ``sid``."""
    return bool(_trashed_before.get(sid)) or os.path.exists(_retrash_path(sid))


def _trashed_list(sid):
    """The list kept for the sync of source ``sid``: from memory, else from
    its file (after a restart); None when there is none."""
    ids = _trashed_before.get(sid)
    if ids is not None:
        return ids
    path = _retrash_path(sid)
    try:
        with open(path, encoding="utf-8") as f:
            posts = json.load(f)["posts"]
        return {i for i in posts if isinstance(i, str)} if isinstance(posts, list) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as e:
        print(f"[sync] source {sid}: unreadable {path}, ignored: {e}")
        return None


def _take_trashed(sid, keep_file=False):
    """Forget the list kept for the sync of source ``sid``. ``keep_file``:
    its file stays, for the next start (resume)."""
    _trashed_before.pop(sid, None)
    if keep_file:
        return
    try:
        os.remove(_retrash_path(sid))
    except FileNotFoundError:
        pass


def _in_trash(roots):
    """Ids of the Instagram posts in the trash as a whole (not one item, not
    an extra copy)."""
    return {g["public"]["post"] for g in trash._all_entries(roots)
            if g["public"]["platform"] == "instagram" and isinstance(g["public"]["post"], str)
            and not g["public"]["partial"] and not g["public"]["copy"]}


def _indexed(conn, ids):
    """{id: meta path} of the posts in ``ids`` that are in the index."""
    ids, out = sorted(ids), {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        out.update(conn.execute(f"SELECT id, meta_path FROM posts WHERE id IN ({','.join('?' * len(part))})",
                                part).fetchall())
    return out


def _trashed(conn, roots):
    """The posts in the trash (_in_trash) that are not in the index."""
    ids = _in_trash(roots)
    return ids - set(_indexed(conn, ids))


def _retrash(params, note):
    sid = None
    try:
        sid = _source_id(params)
        src = sources.row(db.connect(), sid)
        return retrash(sid, src["folder"] if src else None, note or print)
    except Exception as e:                     # the sync still ends as it went
        if sid is not None:
            _take_trashed(sid)
        (note or print)(f"could not put the trashed posts it brought back in the trash: {e}")
        return []


def _retrash_cancelled(sid):
    """A sync cancelled while instaloader ran, or that FeedVault stopped (at
    the next start, resume): its folder is not indexed, so a trashed post it
    brought back would be new at the next scan. Index the folder (what the
    next scan would do) and put those back in the trash. True when its
    folder is missing: the list is kept for a later start."""
    def note(text):
        print(f"[sync] source {sid}: {text}")
    try:
        src = sources.row(db.connect(), sid)
        if src is None or src["tool"] != "instaloader":
            _take_trashed(sid)
            return False
        if not os.path.isdir(src["folder"]):
            note("its folder is missing (media root offline?): checked at a later start")
            return True
        scanner.index_dirs(config.load()["media_roots"], [src["folder"]], new=True)
        retrash(sid, src["folder"], note)
    except Exception as e:                     # the next scan indexes them, as before
        _take_trashed(sid)
        note(f"could not put the trashed posts it brought back in the trash: {e}")
    return False


def resume():
    """At startup, after jobs.recover: the syncs FeedVault stopped (quit or
    killed) while instaloader ran left their lists (_keep_trashed). Each is
    done as for a cancelled sync, then forgotten."""
    try:
        names = sorted(os.listdir(_retrash_dir()))
    except FileNotFoundError:
        return
    except OSError as e:                       # never stops the start
        print(f"[sync] cannot read {_retrash_dir()}: {e}")
        return
    for name in names:
        sid = name[:-len(".json")]
        if name.endswith(".json") and sid.isascii() and sid.isdigit() and len(sid) < 16:
            print(f"[sync] source {sid}: its sync was interrupted, checking for trashed posts it brought back")
            _retrash_cancelled(int(sid))


def retrash(sid, folder, note):
    """Put back in the trash the posts the sync of source ``sid`` brought
    back: in the trash and not in the index when it started, in its folder
    now, and still in the trash (not restored meanwhile). Returns their ids."""
    before = _trashed_list(sid)
    if not before or not folder:
        _take_trashed(sid)
        return []
    gone = _put_back(sid, before, folder, note)
    _take_trashed(sid)                         # only once done: stopped before, the next start does it
    return gone


def _put_back(sid, before, folder, note):
    cfg = config.load()
    base = os.path.join(os.path.realpath(folder), "")
    now = _indexed(db.connect(), before)
    back = sorted({pid for pid, meta in now.items() if os.path.realpath(meta).startswith(base)}
                  & _in_trash(cfg["media_roots"]))
    if not back:
        return []
    report = trash.delete(back, [], cfg["media_roots"], cfg["data_directory"])
    gone = report["posts"]
    if gone:
        note(f"{len(gone)} trashed post{'' if len(gone) == 1 else 's'} came back with this sync "
             f"(instaloader keeps no list of deleted posts): back in the trash")
        try:
            merged = trash.merge_again(cfg["media_roots"], gone, cfg["data_directory"])
        except Exception as e:                 # two entries, as before
            merged = []
            note(f"could not merge their trash entries: {e}")
        if merged:
            n = len(merged)
            note(f"{n} of them kept {'its' if n == 1 else 'their'} first trash entry, with the files "
                 f"{'it was' if n == 1 else 'they were'} deleted with; the cop{'y' if n == 1 else 'ies'} "
                 f"this sync downloaded {'was' if n == 1 else 'were'} deleted")
    if len(gone) < len(back):
        left = len(back) - len(gone)
        why = report.get("error") or "; ".join(e["error"] for e in report["errors"][:1]) or "unknown error"
        note(f"{left} trashed post{'' if left == 1 else 's'} came back and could not go back to the trash: {why}")
    return gone


# A stamp before every post: the sync walks the whole profile, without
# --fast-update, and skips the files already there one by one.
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _seed_stamp(conn, src, options, note, moved=(), roots=()):
    """Seed the stamps file on a first sync (see _seed_posts), then raise
    the stamps of what the source fetches to its floor (see _floor_stamps)."""
    _seed_posts(conn, src, options, note, moved, roots)
    _floor_stamps(src, options, note)


# The stamps instaloader keeps per profile for what it fetches (LatestStamps'
# keys); highlights have none (each is walked, files there skipped), and
# stories are kept a day only.
STAMP_KEYS = {"posts": "post-timestamp", "reels": "reels-timestamp", "tagged": "tagged-timestamp"}


def _floor_stamps(src, options, note):
    """With a floor (options' since): the stamp of each of posts, reels and
    tagged posts the source fetches goes up to just before it, when it is
    missing or older, so the walk stops there instead of going on to the
    oldest post for the filter to reject. After the seed, so a seed older
    than the floor (or none) never takes it back. A stamp newer than the
    floor stays: what is older than it was walked already."""
    floor = _floor(options)
    if floor is None:
        return
    at = datetime(*floor, tzinfo=timezone.utc) - timedelta(microseconds=1)
    path = stamps_path()
    stamps = configparser.ConfigParser(interpolation=None)
    stamps.read(path, encoding="utf-8")
    target, raised = src["target"], []
    for kind in _content(options):
        key = STAMP_KEYS.get(kind)
        if key is None:
            continue
        try:
            if datetime.strptime(stamps.get(target, key), STAMP_FORMAT) >= at:
                continue
        except (configparser.Error, ValueError):
            pass
        if not stamps.has_section(target):
            stamps.add_section(target)
        stamps.set(target, key, at.strftime(STAMP_FORMAT))
        raised.append(kind)
    if raised:
        _write_stamps(stamps, path)
        note(f"{target}: nothing before {options['since']}; {', '.join(raised)} start there")


def _seed_posts(conn, src, options, note, moved=(), roots=()):
    """Seed the stamps file on a first sync (no stamp yet). Posts saved one
    by one, not synced, never seed it: those Save added (saved_posts,
    whatever folder they went to), those just moved out of _saved
    (``moved``) and those still in it (left there by gather). When they
    are all there is, the stamp goes before every post (EPOCH), so the walk
    passes them and goes on to the older posts; it is in the file before
    instaloader runs, so a retry does the same."""
    import save                                # it imports this module
    path = stamps_path()
    stamps = configparser.ConfigParser(interpolation=None)
    stamps.read(path, encoding="utf-8")
    target = src["target"]
    if options["full_history"]:
        # Every kind with a stamp walks back again (to the floor, if any).
        note(f"{target}: full history, every post not in the folder yet")
        gone = [key for key in STAMP_KEYS.values() if stamps.has_option(target, key)]
        for key in gone:
            stamps.remove_option(target, key)
        if gone:
            _write_stamps(stamps, path)
        return
    if stamps.has_option(target, "post-timestamp"):
        return
    newest, held = None, []
    saved = {os.path.normpath(os.path.join(r, save.SAVED)) for r in roots}
    skip = set(moved) | save.saved_posts(conn)
    if src["author_id"] is not None:
        key = people.canonical(conn, src["platform"], src["author_id"])
        a = db.accounts(conn).get(key)
        if a and a["newest"] is not None:
            newest = trusted_newest(conn, *key, skip=skip, saved=saved, held=held)
            if newest is None and not held and not moved:
                note("first sync: no reliable date, fetching full history")
                return
    else:
        # No account yet (found as gather finds it): posts it left in
        # _saved have a file of theirs in the folder already, where
        # --fast-update would stop. The stamp goes before every post instead.
        key = sources._handle_account(conn, src["platform"], target)
        if key is not None:
            trusted_newest(conn, *key, skip=skip, saved=saved, held=held)
    if newest is None and (held or moved):
        if not stamps.has_section(target):
            stamps.add_section(target)
        stamps.set(target, "post-timestamp", EPOCH.strftime(STAMP_FORMAT))
        _write_stamps(stamps, path)
        n = len(held)
        note(f"first sync of {target}: downloading everything but the "
             f"{len(moved)} post{'' if len(moved) == 1 else 's'} saved already" if moved else
             f"first sync of {target}: downloading everything (the {n} post{'' if n == 1 else 's'} "
             f"saved one by one give{'s' if n == 1 else ''} no starting point)")
        return
    if newest is None:
        note(f"first sync of {target}: no post indexed yet, downloading everything")
        return
    if not stamps.has_section(target):
        stamps.add_section(target)
    stamps.set(target, "post-timestamp", datetime.fromtimestamp(newest, timezone.utc).strftime(STAMP_FORMAT))
    if key[1].isdigit() and not stamps.has_option(target, "profile-id"):
        stamps.set(target, "profile-id", key[1])
    _write_stamps(stamps, path)
    note(f"first sync of {target}: starting after its newest indexed post, "
         f"{datetime.fromtimestamp(newest, timezone.utc):%Y-%m-%d %H:%M} UTC")


# What went wrong, from the output: one table of fixed patterns per tool,
# with the exact strings they match, in health.py.
FAILURES = health.INSTALOADER
MESSAGES = {
    "rate_limited": "Instagram is limiting requests: wait a while before syncing again",
    "not_found": "Profile not found: renamed, deleted, or blocked",
    "private": "Private profile: the session in use does not follow it",
    "login_required": "Instagram wants a logged-in session for this; see Settings",
    "generic": "instaloader failed",
}


GALLERY_DL_FAILURES = health.GALLERY_DL
YT_DLP_FAILURES = health.YT_DLP
TOOL_MESSAGES = {
    "rate_limited": "The site is limiting requests: wait a while before syncing again",
    "not_found": "Profile not found: renamed, deleted, or blocked",
    "private": "Private profile: the cookies in use do not have access to it",
    "login_required": "The site wants a logged-in session for this; see Settings (browser cookies)",
}


def classify(lines, failures=None):
    """(error, line): what the output of a failed run says went wrong
    (health.classify with the tool's table, FAILURES by default), and the
    line that says it (else the last line of output); "generic" for
    output no pattern knows. The line is raw: health.scrub it."""
    state, line = health.classify(lines, failures or FAILURES)
    return ("generic" if state == "error" else state), line


# An error line about one item, not the profile: yt-dlp names the video's
# extractor ("[youtube]", "[TikTok]"; a channel or user is "[youtube:tab]",
# "[tiktok:user]"), gallery-dl logs a file it could not get under "[download]".
_YT_DLP_ERROR = re.compile(r"ERROR: \[([^\]]+)\]")
_GALLERY_DL_ERROR = re.compile(r"^\[([^\]]+)\]\[error\]")


def _item_errors(tool, lines):
    """The error lines of a run when every one is about a single item (a
    private or removed video in a channel), else None."""
    pattern = _YT_DLP_ERROR if tool == "yt-dlp" else _GALLERY_DL_ERROR
    found = []
    for _, t in lines:
        m = pattern.match(t.strip()) if tool == "gallery-dl" else pattern.search(t)
        if m is None:
            continue
        if (":" in m.group(1)) if tool == "yt-dlp" else (m.group(1) != "download"):
            return None
        found.append(t.strip())
    return found or None


def _owner(params):
    """{"account", "person"} of the source, for the dashboard to link to: the
    account (that of the folder's posts for a source without one yet) and
    the person's id, or None."""
    try:
        conn = db.connect()
        src = sources.get(conn, _source_id(params))
        account = src and src["account"]
        if src and account is None:
            key = sources._folder_account(conn, src["platform"], src["folder"], config.load()["media_roots"])
            account = {"platform": key[0], "id": key[1]} if key else None
    except Exception as e:                     # only a link: the sync still ends as it went
        print(f"[sync] source {params.get('source')}: no account to link to: {e}")
        src = None
    if src is None:
        return {"account": None, "person": None}
    return {"account": account, "person": src["person"]["id"] if src["person"] else None}


def _outcome(params, code, lines, index, note=None, tool="instaloader"):
    state, result, message = _ended_as(params, code, lines, index, note, tool)
    # The session it used, as the output tells (health.login); a source's
    # options do not change while it syncs (app.update_source).
    src = sources.row(db.connect(), _source_id(params)) if "source" in params else None
    if src is not None:
        result["login"] = health.login(tool, lines, session_of(tool, _options(src)), _said(state, result))
    return state, result, message


def _ended_as(params, code, lines, index, note, tool):
    if tool != "instaloader":
        _note_listed(params, tool, lines)
    added = index["added"] if index else 0
    if tool == "instaloader" and index:
        added = max(0, added - len(_retrash(params, note)))
    result = {"added": added, "updated": index["updated"] if index else 0, "error": None, "line": None,
              **_owner(params)}
    if index and index.get("seen"):
        result["seen"] = list(index["seen"])   # the first_seen range of what it added (notify.py)
    new = notify.plural(added, "new post")
    rename = health.renamed(tool, lines)
    if rename:
        result["rename"] = rename
    unread = _unread(tool, index)
    if code == 0 or (tool == "yt-dlp" and code == BREAK_ON_EXISTING) or health.only_renamed(tool, lines):
        if rename:
            new += f"; the profile is now called {rename[1]} (accept the new name on the source)"
        return "done", result, new + unread
    if tool != "instaloader":
        # One video or file that could not be had is not the profile failing.
        items = _item_errors(tool, lines)
        # Not a rate limit or a login wall: those stop every item, not one.
        if items and classify([(0, t) for t in items], GALLERY_DL_FAILURES if tool == "gallery-dl"
                              else YT_DLP_FAILURES)[0] in ("private", "not_found", "generic"):
            result["line"] = health.scrub(items[-1])
            skipped = f"{len(items)} item{'' if len(items) == 1 else 's'}"
            return "done", result, f"{new}; {skipped} could not be downloaded: {health.scrub(items[-1], 200)}{unread}"
    if tool == "instaloader":
        result["error"], result["line"] = classify(lines)
        message = MESSAGES[result["error"]]
    else:
        result["error"], result["line"] = classify(
            lines, GALLERY_DL_FAILURES if tool == "gallery-dl" else YT_DLP_FAILURES)
        message = TOOL_MESSAGES.get(result["error"], f"{tool} failed")
    # Tool output is untrusted text: no cookie, token or session path is kept (health.scrub).
    result["line"] = health.scrub(result["line"])
    if result["error"] == "generic" and result["line"]:
        message = f"{message}: {result['line'][:200]}"
    if added:
        message += f" ({notify.plural(added, 'new post')} before it stopped)"
    message += unread
    old = _outdated(tool)
    if old:
        result["outdated"] = True
        message += f". {tool} {old[0]} is out of date ({old[1]} is out): update it in Settings → Downloaders"
    return "failed", result, message


def _unread(tool, index):
    """What to add to a gallery-dl or yt-dlp sync's message when files it
    wrote could not be read (no metadata beside them, another layout): the
    user's own config is the likely cause while FeedVault does not skip it."""
    n = (index or {}).get("unread", 0) if tool != "instaloader" else 0
    if not n:
        return ""
    files = f"{n} file{'' if n == 1 else 's'} it wrote could not be read"
    if tool_settings(tool)["ignore_config"]:
        return f"; {files} (no metadata FeedVault knows beside {'it' if n == 1 else 'them'})"
    return (f"; {files}: your own {tool} config is the likely cause (it can turn metadata off or change "
            f"the layout); try \"Ignore my {tool} config\" in Settings → Downloaders")


def _outdated(tool):
    """(installed, latest) when the latest-version check is on and the tool
    is older than PyPI's latest, else None (and when that cannot be told)."""
    import downloaders                         # it imports this module
    try:
        return downloaders.outdated(tool)
    except Exception as e:                     # a failed sync still ends as it went
        print(f"[sync] could not tell whether {tool} is out of date: {e}")
        return None


def _health_state(job):
    """What a sync's output said, as health.STATES: ok when it worked (renamed
    when the tool found the profile under a new name), the
    error it was classified as when it failed ("generic" and a missing tool
    are "error"), None when it never got that far (cancelled, interrupted)."""
    return _said(job["state"], job["result"] or {})


def _said(state, result):
    if state == "done":
        return "renamed" if result.get("rename") else "ok"
    if state != "failed":
        return None
    return result.get("error") if result.get("error") in health.STATES else "error"


def _failures(src, state):
    """Failed syncs in a row, for the scheduler's back-off: a failure adds
    one, a sync that worked starts again from 0, a cancelled or interrupted
    one leaves the count."""
    before = sources.failures(sources.last_result(src))
    return before + 1 if state == "failed" else 0 if state == "done" else before


def _ended(job):
    """Store how it went on the source, and let a new source adopt its account."""
    # First: the folder listing yt-dlp's start took goes, whatever fails
    # below (its after hook never ran when the tool could not start).
    _info_before.pop(int(job["params"]["source"]), None)
    if job["state"] in ("done", "failed"):
        _mark_muted(job)
    _tally(job)
    if job["started_at"] is None:
        return                                 # cancelled while queued: it never ran
    sid = _source_id(job["params"])
    waits = job["state"] == "cancelled" and _kept_trashed(sid) and _retrash_cancelled(sid)
    # Left when the run ended before its outcome or after hook (the tool
    # could not start). FeedVault stopped it: the file stays, for resume().
    _take_trashed(sid, keep_file=job["state"] == "interrupted" or waits)
    listed = _listed.pop(sid, None)
    r = job["result"] or {}
    conn = db.connect()
    src = sources.row(conn, sid)
    target = queued_target(job["params"], job["argv"])
    if src is not None and target is not None and src["target"] != target:
        return                                 # the id names another source now (database replaced)
    ended_at = job["ended_at"] or int(time.time())
    before = sources.last_result(src)
    if not sources.record(conn, sid, job["id"], ended_at, {
            "state": job["state"], "error": r.get("error"), "message": job["message"],
            "line": health.scrub(r.get("line")), "added": r.get("added", 0), "job": job["id"],
            "outdated": r.get("outdated", False), "failures": _failures(src, job["state"]),
            **health.record(before, _health_state(job), job["state"], ended_at, src["target"] if src else None,
                            r.get("rename"), r.get("login") if job["state"] in ("done", "failed") else None)}):
        return
    changed = {"sources"}
    _notify(conn, job, sid, before)
    src = sources.row(conn, sid)
    options = _options(src)
    if job["state"] == "done" and (options["full_history"] or options["first_posts"]):
        floor = _first_posts_floor(conn, src, job, listed) if options["first_posts"] else None
        since = max(filter(None, (floor, options["since"])), default=None)
        # Once is enough; "last N" stays for the next run when there is no floor to stop it.
        sources.update(conn, sid, {**options, "full_history": False, "since": since,
                                   "first_posts": options["first_posts"] if floor is None else None},
                       keys=("full_history", "since", "first_posts"))
    if job["state"] in ("done", "failed"):
        roots = config.load()["media_roots"]
        adopted = sources.adopt(conn, sid, roots, job["ended_at"])
        changed.update(adopted)
        if adopted and src["tool"] == "instaloader":
            import save                        # it imports this module
            # Its account is known only now: its saved posts join the folder.
            try:
                save.gather(conn, sources.row(conn, sid), roots, lambda text: print(f"[sync] source {sid}: {text}"))
            except Exception as e:             # they join it before its next sync instead
                print(f"[sync] source {sid}: could not move its saved posts: {e}")
        if src["tool"] == "instaloader":
            try:
                _forget_saved(conn, sources.row(conn, sid))
            except Exception as e:             # they only seed nothing a while longer
                print(f"[sync] source {sid}: could not update its saved posts: {e}")
    for name in sorted(changed):
        userdata.changed(name)


def _who(src):
    """How an entry names a source: its person, else @handle (instaloader)
    or the link without https://."""
    if src["person"]:
        return src["person"]["name"]
    return f"@{src['target']}" if src["tool"] == "instaloader" else src["target"].removeprefix("https://")


def _mark_muted(job):
    """A sync of a muted person's or account's source (news.py) says so in
    its result (``muted``): no toast, no notification, not in "Sync all"'s
    count."""
    try:
        conn = db.connect()
        src = sources.get(conn, _source_id(job["params"]))
        account = src and src["account"]
        if src and news.is_muted(conn, src["person"] and src["person"]["id"],
                                 account and (account["platform"], account["id"])):
            job["result"] = {**(job["result"] or {}), "muted": True}
            jobs.amend(job["id"], {"muted": True})
    except Exception as e:                     # it only says so as if it were not muted
        print(f"[sync] source {job['params'].get('source')}: could not tell whether it is muted: {e}")


def _notify(conn, job, sid, before):
    """The notifications entry of a sync that brought posts or failed
    (notify.py). A scheduled one that failed as the sync before it did adds
    none, nor does a muted one. Its id goes in the job's result (``notification``)."""
    state, r = job["state"], job["result"] or {}
    said = _said(state, r)
    if r.get("muted") or not ((state == "done" and r.get("added")) or state == "failed"):
        return
    scheduled = job["params"].get("scheduled") == "1"
    if state == "failed" and scheduled and health.state_of(before) == said:
        return
    try:
        src = sources.get(conn, sid)
        account = src["account"] or r.get("account")
        who = _who(src)
        common = {"job": job["id"], "source": sid, "person": src["person"] and src["person"]["id"],
                  "account": account and (account["platform"], account["id"]), "state": said,
                  "scheduled": scheduled}
        if state == "done":
            seen = r.get("seen")
            text = notify.new_text(r["added"], who)
            nid = notify.add(conn, "new", text, count=r["added"], folder=job["rescan"],
                             seen=tuple(seen) if seen else None, **common)
        else:
            text = notify.failed_text(who, said, job["message"])
            nid = notify.add(conn, "failed", text, **common)
        jobs.amend(job["id"], {"notification": nid})
        notify.desktop(text)
    except Exception as e:                     # only the list misses it: the sync ended as it went
        print(f"[sync] source {sid}: no notification: {e}")


def _first_posts_floor(conn, src, job, listed=None):
    """After a "last N" first sync that worked: the day (UTC) of the oldest
    post it added or listed (``listed``: the oldest posted_at of the posts
    its archive skipped, see _note_listed), else None (it listed nothing
    FeedVault knows). It becomes the source's floor: the archive only stops
    a sync at 5 files in a row it has (gallery-dl), or not at all (TikTok,
    a YouTube channel's page), so the next sync would go on past those N to
    the older posts, also when the N were all there already (a seeded
    archive: nothing added)."""
    prefix = os.path.join(src["folder"], "")
    added = conn.execute("SELECT MIN(posted_at) FROM posts WHERE first_seen >= ? AND posted_at IS NOT NULL "
                         "AND substr(meta_path, 1, ?) = ?", (job["started_at"], len(prefix), prefix)).fetchone()[0]
    oldest = min(filter(None, (added, listed)), default=None)
    if oldest is None:
        return None
    # Not after today: a floor is a day up to today's, in local time.
    return min(datetime.fromtimestamp(oldest, timezone.utc).date(), date.today()).isoformat()


# What the archive skipped, as each tool says it: gallery-dl prints a file it
# has as "# <path>", yt-dlp "[download] <id>: has already been recorded in the archive".
_GALLERY_DL_SKIPPED = re.compile(r"# (/.+)")
_YT_DLP_SKIPPED = re.compile(r"\[download\] (\S+): has already been recorded in the archive")
_listed = {}                                   # source id -> oldest posted_at its "last N" sync's archive skipped


def _note_listed(params, tool, lines):
    """For a "last N" sync: the oldest post among those its archive skipped
    (indexed already, so the sync added nothing for them), kept for _ended
    (see _first_posts_floor). A failure here only loses that fallback."""
    try:
        conn = db.connect()
        src = sources.row(conn, _source_id(params))
        if src is None or not _options(src)["first_posts"]:
            return
        pattern = _GALLERY_DL_SKIPPED if tool == "gallery-dl" else _YT_DLP_SKIPPED
        found = sorted({m.group(1) for _, t in lines for m in [pattern.fullmatch(t.strip())] if m})
        oldest = None
        for i in range(0, len(found), 500):
            part = found[i:i + 500]
            marks = ",".join("?" * len(part))
            if tool == "gallery-dl":
                q = (f"SELECT MIN(p.posted_at) FROM media m JOIN posts p ON p.id = m.post_id "
                     f"WHERE m.path IN ({marks})", part)
            else:
                q = (f"SELECT MIN(posted_at) FROM posts WHERE platform = ? AND post_id IN ({marks})",
                     [src["platform"], *part])
            v = conn.execute(*q).fetchone()[0]
            oldest = v if oldest is None or (v is not None and v < oldest) else oldest
        if oldest is not None:
            _listed[src["id"]] = oldest
    except Exception as e:                     # the sync still ends as it went
        print(f"[sync] source {params.get('source')}: could not read the posts it listed: {e}")


def _forget_saved(conn, src):
    """The saved posts (saved_posts) of the source's account that its stamp
    is now later than: the walk has passed them, so they can no longer
    seed one, and their entries go."""
    import save                                # it imports this module
    if src is None or src["author_id"] is None:
        return
    stamps = configparser.ConfigParser(interpolation=None)
    stamps.read(stamps_path(), encoding="utf-8")
    try:
        stamp = datetime.strptime(stamps.get(src["target"], "post-timestamp"), STAMP_FORMAT).timestamp()
    except (configparser.Error, ValueError):
        return
    save.forget_synced(conn, *people.canonical(conn, src["platform"], src["author_id"]), stamp)


# ``scheduled``: queued by the scheduler (notify.py: a failure it repeats adds no entry).
PARAMS = {"source": {"type": "text", "max": 15}, "scheduled": {"type": "choice", "choices": ["1"], "required": False}}

jobs.register(KIND, label="Sync from Instagram", params=PARAMS,
              build=_build, group=GROUP, start=_start, outcome=_outcome, ended=_ended,
              pause=lambda params: settings()["pause"],
              describe=lambda params, argv: f"Sync @{argv[-1]}" if argv else "Sync from Instagram")


# ---------------------------------------------------------------------------
# gallery-dl and yt-dlp
# ---------------------------------------------------------------------------

def _archive_source(params, tool):
    """(source row, link, folder, cfg) of a gallery-dl or yt-dlp source,
    each checked again. Raises jobs.BadRequest."""
    sid = _source_id(params)
    src = sources.row(db.connect(), sid)
    if src is None:
        raise jobs.BadRequest("no such source")
    if src["tool"] != tool:
        raise jobs.BadRequest(f"not a {tool} source")
    cfg = config.load()
    # Stored data is checked again: sources.json can be edited by hand.
    target = sources.check_target(tool, src["target"], sources.routes(cfg))
    if target is None:
        raise jobs.BadRequest("the source's target is not a profile link in the routing table")
    folder = sources.inside_root(src["folder"], cfg["media_roots"])
    if folder is None:
        raise jobs.BadRequest("the source's folder is not inside a media root")
    if sources.in_saved(folder, cfg["media_roots"]):
        raise jobs.BadRequest(sources.SAVED_REFUSED.format(folder=folder))
    # Both tools expand $NAME in the folder they are given (os.path.expandvars).
    if "$" in folder:
        raise jobs.BadRequest("the source's folder holds a $, which the tool would expand")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise jobs.BadRequest(f"cannot create the source's folder: {e.strerror or e}")
    return src, target, folder, cfg


# gallery-dl's "include" names for sources.CONTENT's kinds (its user
# extractors' subcategories), for a profile's own page.
GALLERY_DL_INCLUDE = {"with_replies": "with-replies"}
# Platforms where gallery-dl may stop at the first post older than a
# source's floor (--date-after): those that list a profile newest first with
# nothing older in front. Instagram and TikTok list pinned posts first
# (gallery-dl keeps them, on Instagram by default), Bluesky can too: there
# the floor is a --filter, which skips older files without stopping.
DATE_AFTER_STOPS = {"twitter"}


def gallery_dl_flags(options, platform):
    """What a gallery-dl sync fetches, which media, since when and how many
    on a first sync. --filter is evaluated as Python: it is made of fixed
    text and the numbers of a checked date only."""
    terms = {"images": ["extension in exts_image"], "videos": ["extension in exts_video"]}.get(options["media"], [])
    floor = _floor(options)
    after = []
    if floor is not None:
        if platform in DATE_AFTER_STOPS:
            # It drops a post at the very time given: one second before the day starts.
            after = ["--date-after", (datetime(*floor) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")]
        else:
            terms.append("(not date or date >= datetime(%d, %d, %d))" % floor)
    content = options["content"]
    return [
        *(["-o", "include=" + ",".join(GALLERY_DL_INCLUDE.get(k, k) for k in content)] if content else []),
        *(["--filter", " and ".join(terms)] if terms else []),
        *after,
        *(["--post-range", f"1-{int(options['first_posts'])}"] if options["first_posts"] else []),
    ]


def _build_gallery_dl(params):
    src, target, folder, cfg = _archive_source(params, "gallery-dl")
    options = _options(src)
    session = session_of("gallery-dl", options, cfg)
    refused = sources.login_refused(options, src["platform"], session)
    if refused:
        raise jobs.BadRequest(refused)
    return {"tool": "gallery-dl", "rescan": folder, "args": [
        *config_flags("gallery-dl", cfg),
        "--write-metadata",
        "--download-archive", archives.path("gallery-dl", cfg["data_directory"]),
        # Stop after 5 files in a row that are already there; full history goes on to the end.
        "-o", "skip=true" if options["full_history"] else "skip=abort:5",
        *gallery_dl_flags(options, src["platform"]),
        "-D", folder,
        *cookie_flags(session),
        "--", target,
    ]}


# Whether a yt-dlp sync may stop at the first video its archive has, per
# platform (any other: yes). A TikTok profile lists its pinned videos (up to
# 3, usually old, so archived) first, and yt-dlp neither skips nor reorders
# them: it would stop there every time and never reach a new video. Without
# it the whole listing is paged through (15 videos a request); the archive
# still keeps every listed video from being fetched again.
STOPS_AT_ARCHIVED = {"tiktok": False}
YOUTUBE_TABS = {"videos", "shorts", "streams", "live", "podcasts", "releases", "playlists", "featured"}


def _youtube_root(target):
    """Whether a YouTube link is a channel's own page (``/@name``,
    ``/channel/<id>``), not one of its tabs or a playlist."""
    parts = urlsplit(target).path.strip("/").split("/")
    return parts[-1].lower() not in YOUTUBE_TABS and parts[0].lower() != "playlist"


def _build_yt_dlp(params):
    src, target, folder, cfg = _archive_source(params, "yt-dlp")
    options = _options(src)
    session = session_of("yt-dlp", options, cfg)
    longest = yt_dlp.youtube_max_seconds(cfg)
    # A YouTube channel's own page lists its tabs (Videos, then Shorts, …)
    # one after the other: stopping at the first video already there would
    # never reach the next tab. The archive still skips what it lists.
    tabs = src["platform"] == "youtube" and _youtube_root(target)
    ordered = STOPS_AT_ARCHIVED.get(src["platform"], True) and not tabs
    stop = ordered and not options["full_history"]
    floor = _floor(options)
    day = "%04d%02d%02d" % floor if floor else None
    return {"tool": "yt-dlp", "rescan": folder, "args": [
        *config_flags("yt-dlp", cfg),
        "--write-info-json", "--write-thumbnail",
        "--download-archive", archives.path("yt-dlp", cfg["data_directory"]),
        *(["--break-on-existing"] if stop else []),
        # Nothing older than the floor; where the listing is newest first, stop at the first older video.
        *(["--dateafter", day] if day else []),
        *(["--break-match-filters", f"upload_date >=? {day}"] if day and ordered else []),
        *(["--playlist-items", f"1:{int(options['first_posts'])}"] if options["first_posts"] else []),
        # The output template is %-formatted: a % in the folder is doubled.
        "-o", os.path.join(folder.replace("%", "%%"), YT_DLP_NAME),
        # Long YouTube videos are ChannelVault's; one without a duration (live) is skipped too.
        *(["--match-filters", f"duration <= {longest}"] if src["platform"] == "youtube" else []),
        *cookie_flags(session),
        "--", target,
    ]}


def _start_archive(tool):
    def start(params, note, argv=None):
        """Right before the tool starts: seed its archive on a source's first sync."""
        conn = db.connect()
        src = _queued_source(conn, params, argv)
        data_dir = config.load()["data_directory"]
        os.makedirs(os.path.dirname(archives.path(tool, data_dir)), exist_ok=True)
        options = _options(src)
        if options["full_history"]:
            note("full history: every post not in the archive yet")
        if options["first_posts"]:
            # gallery-dl's --post-range applies to each kind's extractor, yt-dlp's to each tab of a channel.
            each = (" of each kind" if tool == "gallery-dl" and len(options["content"] or ()) > 1
                    else " of each tab" if src["platform"] == "youtube" and _youtube_root(src["target"]) else "")
            note(f"first sync: only the newest {options['first_posts']} posts{each}")
        if options["since"]:
            note(f"nothing before {options['since']}")
        if src["last_sync_at"] is not None:
            return
        if src["author_id"] is None:
            note("first sync: no post indexed yet, downloading everything")
            return
        key = people.canonical(conn, src["platform"], src["author_id"])
        formats, unknown = None, {}
        if tool == "gallery-dl":
            formats, error = archives.installed_formats()
            if formats is None:
                note(f"gallery-dl's own archive formats could not be read ({error}): using FeedVault's "
                     f"table ({', '.join(sorted(archives.GALLERY_DL_FORMATS))})")
        posts, added = archives.seed(tool, conn, key[0], key[1], data_dir, formats, unknown)
        note(f"first sync: {added} archive entr{'y' if added == 1 else 'ies'} added for "
             f"{posts} post{'' if posts == 1 else 's'} already indexed")
        for category, files in sorted(unknown.items()):
            note(f"{files} {category} file{'' if files == 1 else 's'} not seeded (no archive format known for "
                 f"{category}, or its metadata lacks a key the format needs), so this sync may download "
                 f"{'it' if files == 1 else 'them'} again")
    return start


_seed_yt_dlp = _start_archive("yt-dlp")
_info_before = {}                              # source id -> (its folder, the info JSONs in it) right before yt-dlp starts


def _start_yt_dlp(params, note, argv=None):
    _seed_yt_dlp(params, note, argv)
    src = sources.row(db.connect(), _source_id(params))
    _info_before[src["id"]] = (src["folder"], info_cookies.listing(src["folder"]))


def _strip_cookies(job, note):
    """After a yt-dlp run: take the cookies out of the info JSONs it wrote,
    whether they came from FeedVault's setting (--cookies-from-browser) or
    the user's own yt-dlp config. A failure is logged; the sync goes on."""
    listed = _info_before.pop(int(job["params"]["source"]), None)
    if listed is None or not job["rescan"]:
        return                                 # it never got to start
    # The folder listed at start: a script's sync may rescan another one.
    cleaned, failed = info_cookies.after_sync(*listed)
    if cleaned:
        note(f"cookies removed from {cleaned} info JSON{'' if cleaned == 1 else 's'}")
    for path, error in failed:
        note(f"could not remove the cookies from {path}: {error}")


def _describe(label):
    def describe(params, argv):
        if not argv:
            return label
        return "Sync " + argv[-1].removeprefix("https://")
    return describe


jobs.register(KINDS["gallery-dl"], label="Sync with gallery-dl", params=PARAMS,
              build=_build_gallery_dl, group="gallery-dl", start=_start_archive("gallery-dl"),
              outcome=lambda p, code, lines, index, note: _outcome(p, code, lines, index, note, "gallery-dl"),
              ended=_ended, pause=lambda params: tool_settings("gallery-dl")["pause"],
              describe=_describe("Sync with gallery-dl"))
jobs.register(KINDS["yt-dlp"], label="Sync with yt-dlp", params=PARAMS,
              build=_build_yt_dlp, group="yt-dlp", start=_start_yt_dlp, after=_strip_cookies,
              outcome=lambda p, code, lines, index, note: _outcome(p, code, lines, index, note, "yt-dlp"),
              ended=_ended, pause=lambda params: tool_settings("yt-dlp")["pause"],
              describe=_describe("Sync with yt-dlp"))


# ---------------------------------------------------------------------------
# Starting syncs
# ---------------------------------------------------------------------------

class Busy(Exception):
    """The source's sync is already queued or running."""


def active():
    """{source id: {id, state, waits_until}} of the syncs queued or running.
    Live jobs only: nothing here outlives the process or the database."""
    out = {}
    kinds = {*KINDS.values(), SCRIPT_KIND}
    for j in jobs.active():
        if j["kind"] in kinds:
            out.setdefault(int(j["params"]["source"]), {k: j[k] for k in ("id", "state", "waits_until")})
    return out


_submitting = threading.Lock()                 # "already queued?" and queueing, as one step


class Refused(Exception):
    """The source runs a script, and the request may not run one."""


SCRIPT_REFUSED = "this source runs a script, which only FeedVault's own dashboard can start"


def _job(src, scheduled=False, scripts_ok=True):
    """(kind, params) of a source's sync: its script's (scripts.py) when it
    has one, else its tool's. Raises Refused for a script when not
    ``scripts_ok``, jobs.BadRequest when it cannot be synced."""
    params = {"source": str(src["id"]), **({"scheduled": "1"} if scheduled else {})}
    script = _options(src)["script"]
    if script is not None:
        if not scripts_ok:
            raise Refused(SCRIPT_REFUSED)
        import scripts                         # it imports this module
        return SCRIPT_KIND, {**params, **scripts.sync_params(script, src)}
    if src["tool"] not in KINDS:
        raise jobs.BadRequest(f"no sync for {src['tool']} sources")
    return KINDS[src["tool"]], params


def sync(sid, scheduled=False, scripts_ok=True):
    """Queue one source's sync: the job's public dict. Raises Busy, Refused
    (see _job), or jobs.BadRequest when the source cannot be synced.
    ``scheduled``: the scheduler's (notify.py)."""
    import scripts                             # it imports this module
    with _submitting, scripts.read_once():     # its script read once, for its params and its build
        if sid in active():
            raise Busy("its sync is already queued or running")
        src = sources.row(db.connect(), sid)
        if src is None:
            raise jobs.BadRequest("no such source")
        return jobs.submit(*_job(src, scheduled, scripts_ok))


def sync_all(only=None, scripts_ok=True):
    """Queue a sync for every source not already queued or running, by
    target, or for those of ``only`` (source ids: a person's). They run one
    after another, the pause between each. Returns (jobs, skipped, errors:
    [{source, error}] for those refused; a source with a script, when not
    ``scripts_ok``). The jobs queued become the batch (see batch), or join
    it while it still runs."""
    global _batch
    import scripts                             # it imports this module
    queued, skipped, errors = [], 0, []
    # The scripts folder read once for every source that has a script.
    with _submitting, scripts.read_once():
        busy = active()
        for src in db.connect().execute("SELECT * FROM sources ORDER BY target, id").fetchall():
            sid = src["id"]
            if only is not None and sid not in only:
                continue
            if sid in busy:
                skipped += 1
                continue
            try:
                queued.append(jobs.submit(*_job(src, scripts_ok=scripts_ok)))
            except (jobs.BadRequest, Refused) as e:
                errors.append({"source": sid, "error": str(e)})
        if queued:
            with _batch_lock:
                running = _batch is not None and len(_batch["ended"]) < len(_batch["jobs"])
                if running:
                    _batch["jobs"].extend(j["id"] for j in queued)
                else:
                    _batch = {"id": queued[0]["id"], "started_at": queued[0]["created_at"],
                              "jobs": [j["id"] for j in queued], "ended": {}}
                # A job may have ended before it was in the batch (a source gone at start).
                for j in queued:
                    done = jobs.get(j["id"])
                    if done and done["state"] in jobs.STATES[2:]:
                        _batch["ended"][j["id"]] = _ending(done)
    return queued, skipped, errors


# The last "Sync all", in memory only: a restart, which also ends every job
# it queued, forgets it, so it can never show syncs a replaced database no
# longer has. ended: {job id: _ending(job)}, filled by the ended hook.
_batch = None
_batch_lock = threading.Lock()


def _ending(job):
    r = job["result"] or {}
    return {"state": job["state"], "added": r.get("added", 0), "source": int(job["params"]["source"]),
            "label": job["label"], "muted": bool(r.get("muted"))}


def _tally(job):
    with _batch_lock:
        if _batch is not None and job["id"] in _batch["jobs"]:
            _batch["ended"][job["id"]] = _ending(job)


def batch():
    """The last "Sync all" while FeedVault has run, or None: {id, started_at,
    total, ended, failed, added (muted sources left out of both), profiles (sources that added posts), first
    (the label and source of the one that added the most), current (the
    job running, else the next queued, else None), jobs (its job ids),
    active (those still queued or running), done}. Progress counts live jobs only."""
    with _batch_lock:
        if _batch is None:
            return None
        b = {**_batch, "jobs": list(_batch["jobs"]), "ended": dict(_batch["ended"])}
    ids = set(b["jobs"])
    live = [j for j in jobs.active() if j["id"] in ids and j["id"] not in b["ended"]]
    ended = [e for e in b["ended"].values() if not e.get("muted")]     # muted: never in its count
    adders = sorted((e for e in ended if e["added"]), key=lambda e: -e["added"])
    current = next((j for j in live if j["state"] == "running"), None) or (live[0] if live else None)
    return {"id": b["id"], "started_at": b["started_at"], "total": len(b["jobs"]),
            "ended": len(b["jobs"]) - len(live), "failed": sum(e["state"] == "failed" for e in ended),
            "added": sum(e["added"] for e in ended), "profiles": len(adders),
            "first": {k: adders[0][k] for k in ("label", "source")} if adders else None,
            "current": current, "jobs": b["jobs"], "active": [j["id"] for j in live], "done": not live}
