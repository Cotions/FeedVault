"""Profile sync: a source's tool run as a job, only for what is new.

The job takes a source id and nothing else; its argument list is built here
from the stored source (sources.py) and the settings, never from a request,
and the stored target is checked again first. One job kind per tool, each
its own lock group (one run of a tool at a time).

instaloader:

- ``--latest-stamps <data_dir>/instaloader/stamps.ini``: instaloader keeps
  the time of each profile's newest downloaded post there, away from the
  media, so it stops at it whatever files exist (trashed posts stay gone);
  ``--fast-update`` also stops at the first file that exists.
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

gallery-dl and yt-dlp (archives.py):

- ``--download-archive`` in the data directory: the tool skips what it lists,
  whatever files exist; trashing a post adds it there (never again).
- ``-o skip=abort:5`` (gallery-dl) and ``--break-on-existing`` (yt-dlp) stop
  the run at what is already there, so a sync with nothing new is quick.
- the first sync of a source seeds the archive with the posts already
  indexed for its account.
- metadata on (``--write-metadata``; ``--write-info-json --write-thumbnail``),
  into the source's folder; YouTube videos longer than ``youtube_max_seconds``
  are not downloaded (ChannelVault's).

How it went is read from the output (login required, private, not found,
rate limited) and stored on the source. instaloader syncs pause between two
(config ``instaloader.pause``).
"""
import configparser
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import archives
import config
import db
import jobs
import people
import sources
import userdata
from parsers import is_media, yt_dlp
from parsers.instaloader import _HANDLE_RE as _TARGET_RE, _NAME_RE, _SPACED_RE, _day_start

KIND = "instaloader-sync"
GROUP = "instaloader"
KINDS = {"instaloader": KIND, "gallery-dl": "gallery-dl-sync", "yt-dlp": "yt-dlp-sync"}
YT_DLP_NAME = "%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s"
BREAK_ON_EXISTING = 101                        # yt-dlp's exit code when --break-on-existing stopped it
PAUSE_DEFAULT = 60
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

def settings(cfg=None):
    """The global instaloader settings, cleaned: {"session": {...}, "pause": seconds}."""
    raw = (cfg or config.load()).get("instaloader") or {}
    session = sources.clean_session(raw.get("session")) or {"mode": "none"}
    pause = raw.get("pause")
    if not isinstance(pause, int) or isinstance(pause, bool) or not 0 <= pause <= PAUSE_MAX:
        pause = PAUSE_DEFAULT
    return {"session": session, "pause": pause}


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
        p = value["pause"]
        if not isinstance(p, int) or isinstance(p, bool) or not 0 <= p <= PAUSE_MAX:
            return None, f"pause must be whole seconds from 0 to {PAUSE_MAX}"
        out["pause"] = p
    return out, None


def session_flags(session):
    if session["mode"] == "cookies":
        return ["--load-cookies", session["browser"]]
    if session["mode"] == "login":
        return ["--login", session["user"]]
    return []


def tool_settings(tool, cfg=None):
    """gallery-dl's or yt-dlp's settings, cleaned: {"session": {...}}, the
    session "none" or a browser's cookies (they have no login of their own
    FeedVault could name)."""
    raw = (cfg or config.load()).get(tool) or {}
    session = sources.clean_session(raw.get("session"), sources.COOKIE_MODES) or {"mode": "none"}
    return {"session": session}


def clean_tool_settings(tool, value, current):
    """Settings from POST /api/config merged over ``current``. Returns (settings, error)."""
    if not isinstance(value, dict) or set(value) - {"session"}:
        return None, f"{tool} must be {{ session }}"
    out = dict(current)
    if "session" in value:
        out["session"] = sources.clean_session(value["session"], sources.COOKIE_MODES)
        if out["session"] is None:
            return None, ('session must be { "mode": "none" } or { "mode": "cookies", "browser": '
                          f'{" | ".join(sources.BROWSERS)} }}')
    return out, None


def cookie_flags(session):
    """--cookies-from-browser <browser>: the tool reads the browser's cookies, FeedVault never."""
    return ["--cookies-from-browser", session["browser"]] if session["mode"] == "cookies" else []


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
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise jobs.BadRequest(f"cannot create the source's folder: {e.strerror or e}")
    options = _options(src)
    session = options["session"] or settings(cfg)["session"]
    pattern, _ = detect_pattern(folder)
    return {"tool": "instaloader", "rescan": folder, "args": [
        "--latest-stamps", stamps_path(cfg),
        # Full history walks the whole profile: past posts already there too.
        *([] if options["full_history"] else ["--fast-update"]),
        "--no-compress-json",
        "--dirname-pattern", _escape(folder),
        "--filename-pattern", pattern,
        "--title-pattern", TITLE,
        *session_flags(session),
        "--", target,
    ]}


def _options(src):
    """A stored source's options, checked again (defaults when malformed)."""
    try:
        stored = json.loads(src["options"] or "{}")
    except ValueError:
        stored = None
    tool = src["tool"]
    return sources.clean_options(stored if isinstance(stored, dict) else None, tool=tool) \
        or sources.clean_options(None, tool=tool)


def _write_stamps(stamps, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        stamps.write(f)
    os.replace(tmp, path)


FILENAMES = "instaloader (filenames)"           # parsers.instaloader's tool for posts rebuilt from names
DAY = 86400


def trusted_newest(conn, platform, author_id):
    """The newest post time of an account (aliases included) that can seed
    instaloader's stamp, or None. A post with metadata has its real time; a
    post rebuilt from a dated file name counts until the end of that day
    (its mtime only within it); one from a name without a date (``{target} -
    {shortcode}``) has only its mtime, which a copy that did not keep it
    makes the copy's date, later than posts never downloaded: not counted."""
    clause, args = db.post_filter(platform=platform, author=author_id)
    newest = None
    for tool, path, posted in conn.execute(
            f"SELECT p.tool, p.meta_path, p.posted_at {db._FROM} {clause} AND p.posted_at IS NOT NULL", args):
        if tool == FILENAMES:
            m = _NAME_RE.fullmatch(os.path.basename(path))
            day = _day_start(m["date"]) if m else None
            if day is None:
                continue
            posted = min(posted, day + DAY - 1)
        newest = posted if newest is None else max(newest, posted)
    return newest


def _start(params, note):
    """Right before instaloader starts (no other instaloader runs): seed the
    stamps file on a source's first sync."""
    conn = db.connect()
    src = sources.row(conn, _source_id(params))
    if src is None:
        raise RuntimeError("the source was removed")
    options = _options(src)
    path = stamps_path()
    stamps = configparser.ConfigParser(interpolation=None)
    stamps.read(path, encoding="utf-8")
    target = src["target"]
    if options["full_history"]:
        note(f"{target}: full history, every post not in the folder yet")
        if stamps.has_option(target, "post-timestamp"):
            stamps.remove_option(target, "post-timestamp")
            _write_stamps(stamps, path)
        return
    if stamps.has_option(target, "post-timestamp"):
        return
    newest = None
    if src["author_id"] is not None:
        key = people.canonical(conn, src["platform"], src["author_id"])
        a = db.accounts(conn).get(key)
        if a and a["newest"] is not None:
            newest = trusted_newest(conn, *key)
            if newest is None:
                note("first sync: no reliable date, fetching full history")
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


# What went wrong, from the output: the first kind whose words appear.
# Rate limiting comes first: Instagram also answers a throttled client with
# login pages and missing profiles. A 403 is Instagram refusing an anonymous
# client, after which instaloader says the profile does not exist.
FAILURES = [(error, re.compile(words, re.I)) for error, words in [
    ("rate_limited", r"\b429 too many|too many requests|please wait a few minutes|rate limit"),
    ("private", r"private but not followed|privateprofilenotfollowedexception|profile is private"),
    ("login_required", r"login required|loginrequiredexception|redirected to login|use --login|"
                       r"session file does not exist|checkpoint_required|challenge_required|login_required|"
                       r"login error|not logged in|\b403 forbidden\b"),
    ("not_found", r"profile \S+ does not exist|profilenotexistsexception|\bnot found\b"),
]]
MESSAGES = {
    "rate_limited": "Instagram is limiting requests: wait a while before syncing again",
    "not_found": "Profile not found: renamed, deleted, or blocked",
    "private": "Private profile: the session in use does not follow it",
    "login_required": "Instagram wants a logged-in session for this; see Settings",
    "generic": "instaloader failed",
}


# gallery-dl logs "[<category>][error] <Exception>: <message>".
GALLERY_DL_FAILURES = [(error, re.compile(words, re.I)) for error, words in [
    ("rate_limited", r"\b429 too many|too many requests|rate limit"),
    ("private", r"tweets are protected|\bprotected\b|private (?:account|profile)|account is private"),
    ("login_required", r"authrequired|authorizationerror|authenticationerror|login required|"
                       r"credentials required|insufficient privileges|requires? (?:a )?login|\b401 unauthorized\b"),
    ("not_found", r"notfounderror|could not be found|\b404 not found\b|does not exist|account (?:is )?suspended"),
]]
YT_DLP_FAILURES = [(error, re.compile(words, re.I)) for error, words in [
    ("rate_limited", r"http error 429|too many requests|rate[- ]limit"),
    ("private", r"private video|account is private|is a private|private account"),
    ("login_required", r"sign in to confirm|login required|log in for access|requires authentication|"
                       r"use --cookies|age-restricted|members-only|\b401 unauthorized\b"),
    ("not_found", r"http error 404|\bnot found\b|video unavailable|does not exist|unable to find|"
                  r"account (?:has been )?(?:banned|terminated|suspended)"),
]]
TOOL_MESSAGES = {
    "rate_limited": "The site is limiting requests: wait a while before syncing again",
    "not_found": "Profile not found: renamed, deleted, or blocked",
    "private": "Private profile: the cookies in use do not have access to it",
    "login_required": "The site wants a logged-in session for this; see Settings (browser cookies)",
}


def classify(lines, failures=None):
    """(error, line): what the output of a failed run says went wrong, and
    the line that says it (else the last line of output)."""
    texts = [t for _, t in lines if t.strip() and not t.startswith("[feedvault]")]
    for error, words in failures or FAILURES:
        for t in reversed(texts):
            if words.search(t):
                return error, t.strip()[:500]
    return "generic", texts[-1].strip()[:500] if texts else None


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


def _outcome(params, code, lines, index, tool="instaloader"):
    added = index["added"] if index else 0
    result = {"added": added, "updated": index["updated"] if index else 0, "error": None, "line": None}
    new = f"{added} new post{'' if added == 1 else 's'}"
    if code == 0 or (tool == "yt-dlp" and code == BREAK_ON_EXISTING):
        return "done", result, new
    if tool != "instaloader":
        # One video or file that could not be had is not the profile failing.
        items = _item_errors(tool, lines)
        # Not a rate limit or a login wall: those stop every item, not one.
        if items and classify([(0, t) for t in items], GALLERY_DL_FAILURES if tool == "gallery-dl"
                              else YT_DLP_FAILURES)[0] in ("private", "not_found", "generic"):
            result["line"] = items[-1][:500]
            skipped = f"{len(items)} item{'' if len(items) == 1 else 's'}"
            return "done", result, f"{new}; {skipped} could not be downloaded: {items[-1][:200]}"
    if tool == "instaloader":
        result["error"], result["line"] = classify(lines)
        message = MESSAGES[result["error"]]
    else:
        result["error"], result["line"] = classify(
            lines, GALLERY_DL_FAILURES if tool == "gallery-dl" else YT_DLP_FAILURES)
        message = TOOL_MESSAGES.get(result["error"], f"{tool} failed")
    if result["error"] == "generic" and result["line"]:
        message = f"{message}: {result['line'][:200]}"
    if added:
        message += f" ({added} new post{'' if added == 1 else 's'} before it stopped)"
    return "failed", result, message


def _ended(job):
    """Store how it went on the source, and let a new source adopt its account."""
    if job["started_at"] is None:
        return                                 # cancelled while queued: it never ran
    sid = _source_id(job["params"])
    r = job["result"] or {}
    conn = db.connect()
    if not sources.record(conn, sid, job["id"], job["ended_at"] or int(time.time()), {
            "state": job["state"], "error": r.get("error"), "message": job["message"],
            "line": r.get("line"), "added": r.get("added", 0), "job": job["id"]}):
        return
    changed = {"sources"}
    src = sources.row(conn, sid)
    options = _options(src)
    if job["state"] == "done" and options["full_history"]:
        sources.update(conn, sid, {**options, "full_history": False})     # once is enough
    if job["state"] in ("done", "failed"):
        changed.update(sources.adopt(conn, sid, config.load()["media_roots"], job["ended_at"]))
    for name in sorted(changed):
        userdata.changed(name)


jobs.register(KIND, label="Sync from Instagram", params={"source": {"type": "text", "max": 15}},
              build=_build, group=GROUP, start=_start, outcome=_outcome, ended=_ended,
              pause=lambda: settings()["pause"],
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
    # Both tools expand $NAME in the folder they are given (os.path.expandvars).
    if "$" in folder:
        raise jobs.BadRequest("the source's folder holds a $, which the tool would expand")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise jobs.BadRequest(f"cannot create the source's folder: {e.strerror or e}")
    return src, target, folder, cfg


def _build_gallery_dl(params):
    src, target, folder, cfg = _archive_source(params, "gallery-dl")
    options = _options(src)
    session = options["session"] or tool_settings("gallery-dl", cfg)["session"]
    return {"tool": "gallery-dl", "rescan": folder, "args": [
        "--write-metadata",
        "--download-archive", archives.path("gallery-dl", cfg["data_directory"]),
        # Stop after 5 files in a row that are already there; full history goes on to the end.
        "-o", "skip=true" if options["full_history"] else "skip=abort:5",
        "-D", folder,
        *cookie_flags(session),
        "--", target,
    ]}


YOUTUBE_TABS = {"videos", "shorts", "streams", "live", "podcasts", "releases", "playlists", "featured"}


def _youtube_root(target):
    """Whether a YouTube link is a channel's own page (``/@name``,
    ``/channel/<id>``), not one of its tabs or a playlist."""
    parts = urlsplit(target).path.strip("/").split("/")
    return parts[-1].lower() not in YOUTUBE_TABS and parts[0].lower() != "playlist"


def _build_yt_dlp(params):
    src, target, folder, cfg = _archive_source(params, "yt-dlp")
    options = _options(src)
    session = options["session"] or tool_settings("yt-dlp", cfg)["session"]
    longest = yt_dlp.youtube_max_seconds(cfg)
    # A YouTube channel's own page lists its tabs (Videos, then Shorts, …)
    # one after the other: stopping at the first video already there would
    # never reach the next tab. The archive still skips what it lists.
    tabs = src["platform"] == "youtube" and _youtube_root(target)
    return {"tool": "yt-dlp", "rescan": folder, "args": [
        "--write-info-json", "--write-thumbnail",
        "--download-archive", archives.path("yt-dlp", cfg["data_directory"]),
        *([] if options["full_history"] or tabs else ["--break-on-existing"]),
        # The output template is %-formatted: a % in the folder is doubled.
        "-o", os.path.join(folder.replace("%", "%%"), YT_DLP_NAME),
        # Long YouTube videos are ChannelVault's; one without a duration (live) is skipped too.
        *(["--match-filters", f"duration <= {longest}"] if src["platform"] == "youtube" else []),
        *cookie_flags(session),
        "--", target,
    ]}


def _start_archive(tool):
    def start(params, note):
        """Right before the tool starts: seed its archive on a source's first sync."""
        conn = db.connect()
        src = sources.row(conn, _source_id(params))
        if src is None:
            raise RuntimeError("the source was removed")
        data_dir = config.load()["data_directory"]
        os.makedirs(os.path.dirname(archives.path(tool, data_dir)), exist_ok=True)
        if _options(src)["full_history"]:
            note("full history: every post not in the archive yet")
        if src["last_sync_at"] is not None:
            return
        if src["author_id"] is None:
            note("first sync: no post indexed yet, downloading everything")
            return
        key = people.canonical(conn, src["platform"], src["author_id"])
        posts, added = archives.seed(tool, conn, key[0], key[1], data_dir)
        note(f"first sync: {added} archive entr{'y' if added == 1 else 'ies'} added for "
             f"{posts} post{'' if posts == 1 else 's'} already indexed")
    return start


def _describe(label):
    def describe(params, argv):
        if not argv:
            return label
        return "Sync " + argv[-1].removeprefix("https://")
    return describe


jobs.register(KINDS["gallery-dl"], label="Sync with gallery-dl", params={"source": {"type": "text", "max": 15}},
              build=_build_gallery_dl, group="gallery-dl", start=_start_archive("gallery-dl"),
              outcome=lambda p, code, lines, index: _outcome(p, code, lines, index, "gallery-dl"),
              ended=_ended, describe=_describe("Sync with gallery-dl"))
jobs.register(KINDS["yt-dlp"], label="Sync with yt-dlp", params={"source": {"type": "text", "max": 15}},
              build=_build_yt_dlp, group="yt-dlp", start=_start_archive("yt-dlp"),
              outcome=lambda p, code, lines, index: _outcome(p, code, lines, index, "yt-dlp"),
              ended=_ended, describe=_describe("Sync with yt-dlp"))


# ---------------------------------------------------------------------------
# Starting syncs
# ---------------------------------------------------------------------------

class Busy(Exception):
    """The source's sync is already queued or running."""


def active():
    """{source id: {id, state, waits_until}} of the syncs queued or running."""
    out = {}
    kinds = set(KINDS.values())
    for j in jobs.active():
        if j["kind"] in kinds:
            out.setdefault(int(j["params"]["source"]), {k: j[k] for k in ("id", "state", "waits_until")})
    return out


_submitting = threading.Lock()                 # "already queued?" and queueing, as one step


def _kind(sid):
    src = sources.row(db.connect(), sid)
    if src is None:
        raise jobs.BadRequest("no such source")
    if src["tool"] not in KINDS:
        raise jobs.BadRequest(f"no sync for {src['tool']} sources")
    return KINDS[src["tool"]]


def sync(sid):
    """Queue one source's sync: the job's public dict. Raises Busy, or
    jobs.BadRequest when the source cannot be synced."""
    with _submitting:
        if sid in active():
            raise Busy("its sync is already queued or running")
        return jobs.submit(_kind(sid), {"source": str(sid)})


def sync_all():
    """Queue a sync for every source not already queued or running, by
    target. They run one after another, the pause between each. Returns
    (jobs, skipped, errors: [{source, error}] for those refused)."""
    queued, skipped, errors = [], 0, []
    with _submitting:
        busy = active()
        for sid, tool in db.connect().execute("SELECT id, tool FROM sources ORDER BY target, id").fetchall():
            if sid in busy:
                skipped += 1
                continue
            try:
                if tool not in KINDS:
                    raise jobs.BadRequest(f"no sync for {tool} sources")
                queued.append(jobs.submit(KINDS[tool], {"source": str(sid)}))
            except jobs.BadRequest as e:
                errors.append({"source": sid, "error": str(e)})
    return queued, skipped, errors
