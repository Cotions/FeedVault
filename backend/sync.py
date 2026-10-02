"""Profile sync: a source's tool run as a job, only for what is new.

The job takes a source id and nothing else; its argument list is built here
from the stored source (sources.py) and the settings, never from a request,
and the stored target is checked again first. One job kind per tool, each
its own lock group (one run of a tool at a time).

instaloader:

- ``--latest-stamps <data_dir>/instaloader/stamps.ini``: instaloader keeps
  the time of each profile's newest downloaded post there, away from the
  media, so it stops at it whatever files exist (trashed posts stay gone).
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

gallery-dl and yt-dlp (archives.py):

- ``--download-archive`` in the data directory: the tool skips what it lists,
  whatever files exist; trashing a post adds it there (never again).
- ``-o skip=abort:5`` (gallery-dl) and ``--break-on-existing`` (yt-dlp) stop
  the run at what is already there, so a sync with nothing new is quick;
  yt-dlp only on platforms that list a profile newest first with nothing
  older in front (STOPS_AT_ARCHIVED).
- the first sync of a source seeds the archive with the posts already
  indexed for its account.
- metadata on (``--write-metadata``; ``--write-info-json --write-thumbnail``),
  into the source's folder; YouTube videos longer than ``youtube_max_seconds``
  are not downloaded (ChannelVault's).
- after a yt-dlp sync, the info JSONs it wrote are rewritten without the
  cookies yt-dlp copies into them (info_cookies.py), before the folder is
  indexed.

How it went is read from the output (login required, private, not found,
rate limited) and stored on the source. Two syncs of one tool pause between
them (config ``<tool>.pause``).
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
import info_cookies
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


def tool_settings(tool, cfg=None):
    """gallery-dl's or yt-dlp's settings, cleaned: {"session": {...}, "pause":
    seconds}, the session "none" or a browser's cookies (they have no login
    of their own FeedVault could name)."""
    raw = (cfg or config.load()).get(tool) or {}
    session = sources.clean_session(raw.get("session"), sources.COOKIE_MODES) or {"mode": "none"}
    return {"session": session, "pause": _pause(raw.get("pause"), TOOL_PAUSE_DEFAULT)}


def clean_tool_settings(tool, value, current):
    """Settings from POST /api/config merged over ``current``. Returns (settings, error)."""
    if not isinstance(value, dict) or set(value) - {"session", "pause"}:
        return None, f"{tool} must be {{ session, pause }}"
    out = dict(current)
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
    stamps = configparser.ConfigParser(interpolation=None)
    stamps.read(stamps_path(cfg), encoding="utf-8")
    return {"tool": "instaloader", "rescan": folder, "args": [
        "--latest-stamps", stamps_path(cfg),
        *(["--fast-update"] if fast_update(stamps, target, options) else []),
        "--no-compress-json",
        "--dirname-pattern", _escape(folder),
        "--filename-pattern", pattern,
        "--title-pattern", TITLE,
        *session_flags(session),
        "--", target,
    ]}


def fast_update(stamps, target, options):
    """Whether a sync passes --fast-update: only when the profile has no
    stamp yet (a first sync) and walks only what is new (no full history,
    which goes past the posts already there too)."""
    return not options["full_history"] and not stamps.has_option(target, "post-timestamp")


def _with_stamps(args, stamps, target, options):
    """The argument list with --fast-update as fast_update() says now."""
    out = [a for a in args if a != "--fast-update"]
    if fast_update(stamps, target, options):
        out.insert(out.index("--latest-stamps") + 2, "--fast-update")
    return out


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


def trusted_newest(conn, platform, author_id, skip=()):
    """The newest post time of an account (aliases included) that can seed
    instaloader's stamp, or None. A post with metadata has its real time; a
    post rebuilt from a dated file name counts until the end of that day
    (its mtime only within it); one from a name without a date (``{target} -
    {shortcode}``) has only its mtime, which a copy that did not keep it
    makes the copy's date, later than posts never downloaded: not counted.
    Posts in ``skip`` (ids) do not count either."""
    clause, args = db.post_filter(platform=platform, author=author_id)
    newest = None
    for pid, tool, path, posted in conn.execute(
            f"SELECT p.id, p.tool, p.meta_path, p.posted_at {db._FROM} {clause} AND p.posted_at IS NOT NULL", args):
        if pid in skip:
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


def _queued_source(conn, params, argv):
    """The source a sync about to start is for. Cancelled when it is gone,
    or when its id names another source now (the database was replaced):
    argv[-1] is the target the job was queued with."""
    src = sources.row(conn, _source_id(params))
    if src is None or (argv and src["target"] != argv[-1]):
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
    moved = save.gather(conn, src, config.load()["media_roots"], note)
    _seed_stamp(conn, src, options, note, moved)
    if not argv:
        return None
    stamps = configparser.ConfigParser(interpolation=None)
    stamps.read(stamps_path(), encoding="utf-8")
    return _with_stamps(argv[1:], stamps, src["target"], options)


# A stamp before every post: the sync walks the whole profile, without
# --fast-update, and skips the files already there one by one.
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _seed_stamp(conn, src, options, note, moved=()):
    """Seed the stamps file on a first sync (no stamp yet). Posts just moved
    out of _saved (``moved``) were saved one by one, not synced: they never
    seed it. When they are all there is, the stamp goes before every post
    (EPOCH), so the walk passes them and goes on to the older posts; it is
    in the file before instaloader runs, so a retry does the same."""
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
            newest = trusted_newest(conn, *key, skip=set(moved))
            if newest is None and not moved:
                note("first sync: no reliable date, fetching full history")
                return
    if newest is None and moved:
        if not stamps.has_section(target):
            stamps.add_section(target)
        stamps.set(target, "post-timestamp", EPOCH.strftime(STAMP_FORMAT))
        _write_stamps(stamps, path)
        note(f"first sync of {target}: downloading everything but the "
             f"{len(moved)} post{'' if len(moved) == 1 else 's'} saved already")
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


def _outcome(params, code, lines, index, tool="instaloader"):
    added = index["added"] if index else 0
    result = {"added": added, "updated": index["updated"] if index else 0, "error": None, "line": None,
              **_owner(params)}
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
    old = _outdated(tool)
    if old:
        result["outdated"] = True
        message += f". {tool} {old[0]} is out of date ({old[1]} is out): update it in Settings → Downloaders"
    return "failed", result, message


def _outdated(tool):
    """(installed, latest) when the latest-version check is on and the tool
    is older than PyPI's latest, else None (and when that cannot be told)."""
    import downloaders                         # it imports this module
    try:
        return downloaders.outdated(tool)
    except Exception as e:                     # a failed sync still ends as it went
        print(f"[sync] could not tell whether {tool} is out of date: {e}")
        return None


def _ended(job):
    """Store how it went on the source, and let a new source adopt its account."""
    _tally(job)
    if job["started_at"] is None:
        return                                 # cancelled while queued: it never ran
    sid = _source_id(job["params"])
    r = job["result"] or {}
    conn = db.connect()
    src = sources.row(conn, sid)
    if src is not None and job["argv"] and src["target"] != job["argv"][-1]:
        return                                 # the id names another source now (database replaced)
    if not sources.record(conn, sid, job["id"], job["ended_at"] or int(time.time()), {
            "state": job["state"], "error": r.get("error"), "message": job["message"],
            "line": r.get("line"), "added": r.get("added", 0), "job": job["id"],
            "outdated": r.get("outdated", False)}):
        return
    changed = {"sources"}
    src = sources.row(conn, sid)
    options = _options(src)
    if job["state"] == "done" and options["full_history"]:
        sources.update(conn, sid, {**options, "full_history": False})     # once is enough
    if job["state"] in ("done", "failed"):
        roots = config.load()["media_roots"]
        adopted = sources.adopt(conn, sid, roots, job["ended_at"])
        changed.update(adopted)
        if adopted and src["tool"] == "instaloader":
            import save                        # it imports this module
            # Its account is known only now: its saved posts join the folder.
            save.gather(conn, sources.row(conn, sid), roots, lambda text: print(f"[sync] source {sid}: {text}"))
    for name in sorted(changed):
        userdata.changed(name)


jobs.register(KIND, label="Sync from Instagram", params={"source": {"type": "text", "max": 15}},
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
    session = options["session"] or tool_settings("yt-dlp", cfg)["session"]
    longest = yt_dlp.youtube_max_seconds(cfg)
    # A YouTube channel's own page lists its tabs (Videos, then Shorts, …)
    # one after the other: stopping at the first video already there would
    # never reach the next tab. The archive still skips what it lists.
    tabs = src["platform"] == "youtube" and _youtube_root(target)
    stop = STOPS_AT_ARCHIVED.get(src["platform"], True) and not (options["full_history"] or tabs)
    return {"tool": "yt-dlp", "rescan": folder, "args": [
        "--write-info-json", "--write-thumbnail",
        "--download-archive", archives.path("yt-dlp", cfg["data_directory"]),
        *(["--break-on-existing"] if stop else []),
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


_seed_yt_dlp = _start_archive("yt-dlp")
_info_before = {}                              # source id -> its folder's info JSONs right before yt-dlp starts


def _start_yt_dlp(params, note, argv=None):
    _seed_yt_dlp(params, note, argv)
    src = sources.row(db.connect(), _source_id(params))
    _info_before[src["id"]] = info_cookies.listing(src["folder"])


def _strip_cookies(job, note):
    """After a yt-dlp run: take the cookies out of the info JSONs it wrote,
    whether they came from FeedVault's setting (--cookies-from-browser) or
    the user's own yt-dlp config. A failure is logged; the sync goes on."""
    before = _info_before.pop(int(job["params"]["source"]), None)
    if before is None or not job["rescan"]:
        return                                 # it never got to start
    cleaned, failed = info_cookies.after_sync(job["rescan"], before)
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


jobs.register(KINDS["gallery-dl"], label="Sync with gallery-dl", params={"source": {"type": "text", "max": 15}},
              build=_build_gallery_dl, group="gallery-dl", start=_start_archive("gallery-dl"),
              outcome=lambda p, code, lines, index: _outcome(p, code, lines, index, "gallery-dl"),
              ended=_ended, pause=lambda params: tool_settings("gallery-dl")["pause"],
              describe=_describe("Sync with gallery-dl"))
jobs.register(KINDS["yt-dlp"], label="Sync with yt-dlp", params={"source": {"type": "text", "max": 15}},
              build=_build_yt_dlp, group="yt-dlp", start=_start_yt_dlp, after=_strip_cookies,
              outcome=lambda p, code, lines, index: _outcome(p, code, lines, index, "yt-dlp"),
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
    (jobs, skipped, errors: [{source, error}] for those refused). The jobs
    queued become the batch (see batch), or join it while it still runs."""
    global _batch
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
            "label": job["label"]}


def _tally(job):
    with _batch_lock:
        if _batch is not None and job["id"] in _batch["jobs"]:
            _batch["ended"][job["id"]] = _ending(job)


def batch():
    """The last "Sync all" while FeedVault has run, or None: {id, started_at,
    total, ended, failed, added, profiles (sources that added posts), first
    (the label and source of the one that added the most), current (the
    job running, else the next queued, else None), jobs (its job ids),
    active (those still queued or running), done}. Progress counts live jobs only."""
    with _batch_lock:
        if _batch is None:
            return None
        b = {**_batch, "jobs": list(_batch["jobs"]), "ended": dict(_batch["ended"])}
    ids = set(b["jobs"])
    live = [j for j in jobs.active() if j["id"] in ids and j["id"] not in b["ended"]]
    ended = list(b["ended"].values())
    adders = sorted((e for e in ended if e["added"]), key=lambda e: -e["added"])
    current = next((j for j in live if j["state"] == "running"), None) or (live[0] if live else None)
    return {"id": b["id"], "started_at": b["started_at"], "total": len(b["jobs"]),
            "ended": len(b["jobs"]) - len(live), "failed": sum(e["state"] == "failed" for e in ended),
            "added": sum(e["added"] for e in ended), "profiles": len(adders),
            "first": {k: adders[0][k] for k in ("label", "source")} if adders else None,
            "current": current, "jobs": b["jobs"], "active": [j["id"] for j in live], "done": not live}
