"""Save one Instagram post: the userscript's Save button, as a job.

The request names a shortcode and nothing else (checked against SHORTCODE_RE);
the argument list is built here, the shortcode last, after ``--``, as
instaloader's one-post target ``-<shortcode>``.

Whose post it is is only known once instaloader has fetched it, so it
downloads into a folder of its own in the data directory (``saving/<shortcode>``,
files named ``{shortcode}``). When it exits, the post's JSON names its owner,
and the files move to the owner's folder (owner_folder), named the way that
folder's files are (sync.detect_pattern), never over a file already there.
That folder is then indexed.

Same lock group and pause as instaloader syncs: one instaloader at a time.
"""
import os
import re
import shutil
import threading
from datetime import datetime, timezone

import config
import db
import jobs
import people
import scanner
import sources
import sync
from parsers import instaloader as parser

KIND = "instaloader-post"
SHORTCODE_RE = re.compile(r"[A-Za-z0-9_-]{5,40}", re.ASCII)
SAVED = "_saved"                               # under the first media root: posts of owners with no folder
QUEUE_MAX_DEFAULT = 20                         # Save jobs queued or running at once (config save_queue_max)
QUEUE_MAX_LIMIT = 500


def valid_shortcode(value):
    return isinstance(value, str) and SHORTCODE_RE.fullmatch(value) is not None


def _shortcode(params):
    code = params["shortcode"]
    if not valid_shortcode(code):
        raise jobs.BadRequest("shortcode must be 5 to 40 letters, digits, _ or -")
    return code


def staging(code, cfg=None):
    return os.path.join((cfg or config.load())["data_directory"], "instaloader", "saving", code)


def _build(params):
    code = _shortcode(params)
    cfg = config.load()
    if not cfg["media_roots"]:
        raise jobs.BadRequest("add a media root in Settings first")
    stage = staging(code, cfg)
    return {"tool": "instaloader", "args": [
        "--no-compress-json",
        "--dirname-pattern", sync._escape(stage),
        "--filename-pattern", "{shortcode}",
        *sync.session_flags(sync.settings(cfg)["session"]),
        "--", "-" + code,
    ]}


def _clear(stage):
    """Remove the saving folder: FeedVault's own, in the data directory."""
    shutil.rmtree(stage, ignore_errors=True)


def _start(params, note, argv=None):
    """Right before instaloader starts: an empty saving folder (a cancelled
    run may have left files)."""
    stage = staging(_shortcode(params))
    _clear(stage)
    os.makedirs(stage)


# ---------------------------------------------------------------------------
# Where the files go
# ---------------------------------------------------------------------------

def owner_folder(conn, roots, author_id, handle):
    """The folder a post of this owner goes to: the folder of the owner's
    instaloader source, else the folder right under a media root holding most
    of the owner's posts, else ``<first root>/<handle>`` when it exists, else
    ``<first root>/_saved``. Always inside a media root."""
    handle = handle.lower() if isinstance(handle, str) and sources._HANDLE_RE.fullmatch(handle) else None
    keys = [people.canonical(conn, "instagram", a) for a in (author_id, handle) if a]
    for key in keys:
        row = conn.execute("SELECT folder FROM sources WHERE tool = 'instaloader' AND platform = ? AND author_id = ? "
                           "ORDER BY id LIMIT 1", key).fetchone()
        if row and sources.inside_root(row[0], roots):
            return row[0]
    if handle:
        row = conn.execute("SELECT folder FROM sources WHERE tool = 'instaloader' AND target = ?", (handle,)).fetchone()
        if row and sources.inside_root(row[0], roots):
            return row[0]
    accounts = db.accounts(conn)
    for key in keys:
        if key in accounts:
            found = sources.account_folder(conn, *key, roots)
            if found:
                return found
    if handle and handle.strip(".") and os.path.isdir(os.path.join(roots[0], handle)):
        return os.path.join(roots[0], handle)
    return os.path.join(roots[0], SAVED)


class _Stamp(datetime):
    """Formats like instaloader's dates: 2024-06-01_12-00-00 without a spec."""

    def __format__(self, spec):
        return super().__format__(spec or "%Y-%m-%d_%H-%M-%S")


def base_name(pattern, handle, code, posted):
    """A post's file name without suffix, as a sync with ``pattern`` names it
    (the owner's handle as its target)."""
    date = _Stamp.fromtimestamp(posted or 0, timezone.utc).replace(tzinfo=None)
    if pattern != sync.STAMPED and not handle:
        pattern = sync.STAMPED                 # the other layouts need a handle
    name = pattern.format(target=handle, profile=handle, shortcode=code, date_utc=date)
    return name.replace("/", "∕")         # as instaloader sanitizes


def _place(code, stage, roots, note):
    """Move the saved files to the owner's folder. Returns (folder, account
    key or None), or (None, None) when instaloader saved no post."""
    meta = os.path.join(stage, code + ".json")
    try:
        data = parser._load(meta)
        post = parser._post_from(data["node"], "Post", None, meta, [])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, None
    if post is None:
        return None, None
    conn = db.connect()
    folder = owner_folder(conn, roots, post.author_id, post.author_handle)
    os.makedirs(folder, exist_ok=True)
    pattern, _ = sync.detect_pattern(folder)
    handle = post.author_handle if isinstance(post.author_handle, str) \
        and sources._HANDLE_RE.fullmatch(post.author_handle) else None
    base = base_name(pattern, handle, code, post.posted_at)
    moved = kept = 0
    for name in sorted(os.listdir(stage)):
        if not name.startswith(code):
            continue
        src, dst = os.path.join(stage, name), os.path.join(folder, base + name[len(code):])
        if os.path.lexists(dst):
            kept += 1                          # never over a file already there
            continue
        shutil.move(src, dst)
        moved += 1
    note(f"{moved} file{'' if moved == 1 else 's'} moved to {folder}"
         + (f"; {kept} already there, kept as they were" if kept else ""))
    key = people.canonical(conn, "instagram", post.author_id) if post.author_id else None
    return folder, key


# ---------------------------------------------------------------------------
# How it went
# ---------------------------------------------------------------------------

FAILURES = [(error, re.compile(words, re.I)) for error, words in [
    *[(e, w.pattern) for e, w in sync.FAILURES if e != "not_found"],
    ("not_found", r"fetching post metadata failed|post .* does not exist|\b404\b|\bnot found\b"),
]]
MESSAGES = {
    "rate_limited": "Instagram is limiting requests: wait a while before saving again",
    "not_found": "Post not found: removed, or only visible to a logged-in session",
    "private": "Private post: the session in use does not follow its account",
    "login_required": "Instagram wants a logged-in session for this post; see Settings → Downloaders",
    "generic": "instaloader failed",
}


def _outcome(params, code, lines, index):
    shortcode = _shortcode(params)
    stage = staging(shortcode)
    cfg = config.load()
    roots = cfg["media_roots"]
    result = {"post": None, "folder": None, "added": 0, "updated": 0, "error": None, "line": None,
              "account": None, "person": None}
    notes = []
    try:
        folder, key = _place(shortcode, stage, roots, notes.append) if roots else (None, None)
    except OSError as e:                       # a folder that cannot be made or written
        folder, key = None, None
        result["error"], result["line"] = "generic", None
        notes.append(f"could not move the files: {e.strerror or e}")
    for text in notes:
        print(f"[save] {shortcode}: {text}")
    _clear(stage)
    if folder:
        report = scanner.index_dirs(roots, [folder], new=True)
        result.update(folder=folder, added=report["added"], updated=report["updated"])
        conn = db.connect()
        if db.saved_ids(conn, [f"instagram:{shortcode}"]):
            result["post"] = f"instagram:{shortcode}"
        if key:
            result["account"] = {"platform": key[0], "id": key[1]}
            a = db.accounts(conn).get(key)
            result["person"] = a["person"]["id"] if a and a["person"] else None
    if result["error"]:
        return "failed", result, f"Downloaded, but {notes[-1]}"
    if code == 0 and result["post"]:
        return "done", result, f"Saved in {os.path.basename(folder)}"
    if code == 0:
        result["error"] = "generic"
        return "failed", result, "instaloader saved no post"
    result["error"], result["line"] = sync.classify(lines, FAILURES)
    message = MESSAGES[result["error"]]
    if result["error"] == "generic" and result["line"]:
        message = f"{message}: {result['line'][:200]}"
    return "failed", result, message


def _ended(job):
    if job["state"] not in ("done", "failed") and valid_shortcode(job["params"].get("shortcode")):
        _clear(staging(job["params"]["shortcode"]))   # cancelled or interrupted: what it left


jobs.register(KIND, label="Save a post", params={"shortcode": {"type": "text", "max": 40}},
              build=_build, group=sync.GROUP, start=_start, outcome=_outcome, ended=_ended,
              pause=lambda params: sync.settings()["pause"],
              describe=lambda params, argv: f"Save post {params.get('shortcode')}")


# ---------------------------------------------------------------------------
# Starting one
# ---------------------------------------------------------------------------

def have(conn, shortcode):
    """{id, path} of the post when FeedVault has it, else None."""
    if not db.saved_ids(conn, [f"instagram:{shortcode}"]):
        return None
    return {"id": f"instagram:{shortcode}", "path": f"/p/instagram/{shortcode}"}


class Full(Exception):
    """Too many Save jobs are queued or running already."""


def queue_max(cfg=None):
    v = (cfg or config.load()).get("save_queue_max")
    ok = isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= QUEUE_MAX_LIMIT
    return v if ok else QUEUE_MAX_DEFAULT


_submitting = threading.Lock()                 # "already queued?", the count and queueing, as one step


def submit(shortcode):
    """Queue a save: (the job's public dict, whether it was already queued
    or running). A shortcode with a Save job active gets that job back; one
    more than queue_max() active Save jobs raises Full."""
    with _submitting:
        active = [j for j in jobs.active() if j["kind"] == KIND]
        same = next((j for j in active if j["params"].get("shortcode") == shortcode), None)
        if same is not None:
            return same, True
        limit = queue_max()
        if len(active) >= limit:
            raise Full(f"{limit} post{'' if limit == 1 else 's'} {'is' if limit == 1 else 'are'} already "
                       "waiting to be saved; try again once some are done")
        return jobs.submit(KIND, {"shortcode": shortcode}), False
