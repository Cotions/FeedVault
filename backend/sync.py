"""Profile sync: a source's tool run as a job, only for what is new.

The job takes a source id and nothing else; its argument list is built here
from the stored source (sources.py) and the settings, never from a request.
This slice runs instaloader:

- ``--latest-stamps <data_dir>/instaloader/stamps.ini``: instaloader keeps
  the time of each profile's newest downloaded post there, away from the
  media, so it stops at it whatever files exist (trashed posts stay gone);
  ``--fast-update`` also stops at the first file that exists.
- the first sync of a source seeds that file with the newest post FeedVault
  already has for the account, so it never walks the whole profile again
  (unless the source asks for its full history).
- metadata on (``--no-compress-json``), so new posts get captions and the
  account's numeric id; people.refresh_aliases links them to the folder's
  older filename-only posts.
- ``--filename-pattern`` as the folder's files are already named, so new
  files sit beside the old ones and a post already there is recognised.

How it went is read from the output (login required, private, not found,
rate limited) and stored on the source. Syncs run one at a time (group
"instaloader"), with a pause between two (config ``instaloader.pause``).
"""
import configparser
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

import config
import db
import jobs
import people
import sources
import userdata
from parsers import is_media
from parsers.instaloader import _HANDLE_RE as _TARGET_RE, _NAME_RE, _SPACED_RE

KIND = "instaloader-sync"
GROUP = "instaloader"
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
    os.makedirs(folder, exist_ok=True)
    options = _options(src)
    session = options["session"] or settings(cfg)["session"]
    pattern, _ = detect_pattern(folder)
    return {"tool": "instaloader", "rescan": folder, "args": [
        "--latest-stamps", stamps_path(cfg),
        "--fast-update",
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
    return sources.clean_options(stored if isinstance(stored, dict) else None) or sources.clean_options(None)


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
    if stamps.has_option(target, "post-timestamp"):
        return
    if options["full_history"]:
        note(f"first sync of {target}: full history")
        return
    newest = None
    if src["author_id"] is not None:
        key = people.canonical(conn, src["platform"], src["author_id"])
        a = db.accounts(conn).get(key)
        newest = a and a["newest"]
    if newest is None:
        note(f"first sync of {target}: no post indexed yet, downloading everything")
        return
    if not stamps.has_section(target):
        stamps.add_section(target)
    stamps.set(target, "post-timestamp", datetime.fromtimestamp(newest, timezone.utc).strftime(STAMP_FORMAT))
    if key[1].isdigit() and not stamps.has_option(target, "profile-id"):
        stamps.set(target, "profile-id", key[1])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        stamps.write(f)
    os.replace(tmp, path)
    note(f"first sync of {target}: starting after its newest indexed post, "
         f"{datetime.fromtimestamp(newest, timezone.utc):%Y-%m-%d %H:%M} UTC")


# What went wrong, from the output: the first kind whose words appear.
# Rate limiting comes first: Instagram also answers a throttled client with
# login pages and missing profiles. A 403 is Instagram refusing an anonymous
# client, after which instaloader says the profile does not exist.
FAILURES = [(error, re.compile(words, re.I)) for error, words in [
    ("rate_limited", r"\b429\b|too many requests|please wait a few minutes|rate limit"),
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


def classify(lines):
    """(error, line): what the output of a failed run says went wrong, and
    the line that says it (else the last line of output)."""
    texts = [t for _, t in lines if t.strip() and not t.startswith("[feedvault]")]
    for error, words in FAILURES:
        for t in reversed(texts):
            if words.search(t):
                return error, t.strip()[:500]
    return "generic", texts[-1].strip()[:500] if texts else None


def _outcome(params, code, lines, index):
    added = index["added"] if index else 0
    result = {"added": added, "updated": index["updated"] if index else 0, "error": None, "line": None}
    if code == 0:
        return "done", result, f"{added} new post{'' if added == 1 else 's'}"
    result["error"], result["line"] = classify(lines)
    message = MESSAGES[result["error"]]
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
    if job["state"] in ("done", "failed"):
        changed.update(sources.adopt(conn, sid, config.load()["media_roots"], job["ended_at"]))
    for name in sorted(changed):
        userdata.changed(name)


jobs.register(KIND, label="Sync from Instagram", params={"source": {"type": "text", "max": 15}},
              build=_build, group=GROUP, start=_start, outcome=_outcome, ended=_ended,
              pause=lambda: settings()["pause"],
              describe=lambda params, argv: f"Sync @{argv[-1]}" if argv else "Sync from Instagram")


# ---------------------------------------------------------------------------
# Starting syncs
# ---------------------------------------------------------------------------

class Busy(Exception):
    """The source's sync is already queued or running."""


def active():
    """{source id: {id, state, waits_until}} of the syncs queued or running."""
    out = {}
    for j in jobs.active():
        if j["kind"] == KIND:
            out.setdefault(int(j["params"]["source"]), {k: j[k] for k in ("id", "state", "waits_until")})
    return out


_submitting = threading.Lock()                 # "already queued?" and queueing, as one step


def sync(sid):
    """Queue one source's sync: the job's public dict. Raises Busy, or
    jobs.BadRequest when the source cannot be synced."""
    with _submitting:
        if sid in active():
            raise Busy("its sync is already queued or running")
        return jobs.submit(KIND, {"source": str(sid)})


def sync_all():
    """Queue a sync for every source not already queued or running, by
    target. They run one after another, the pause between each. Returns
    (jobs, skipped, errors: [{source, error}] for those refused)."""
    queued, skipped, errors = [], 0, []
    with _submitting:
        busy = active()
        for (sid,) in db.connect().execute("SELECT id FROM sources ORDER BY target, id").fetchall():
            if sid in busy:
                skipped += 1
                continue
            try:
                queued.append(jobs.submit(KIND, {"source": str(sid)}))
            except jobs.BadRequest as e:
                errors.append({"source": sid, "error": str(e)})
    return queued, skipped, errors
