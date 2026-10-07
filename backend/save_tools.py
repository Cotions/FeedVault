"""Save one X or TikTok post: the userscript's Save button, as a job.

The request names a post link and nothing else. It is parsed strictly
(parse_link): a host from a fixed list per platform (never a subdomain
match, never a short link, which only the site could resolve), a path of
the post's shape, the post id digits only. What reaches the tool is not that
link but one built here from the id (and, for TikTok, the profile name,
checked), after ``--``:

    X       gallery-dl  https://x.com/i/web/status/<id>
    TikTok  yt-dlp      https://www.tiktok.com/@<name>/video/<id>

Each runs as its tool's sync does (sync._build_gallery_dl, _build_yt_dlp:
the same metadata flags, the tool's own settings for the user's config and
cookies), into a folder of its own in the data directory
(``<tool>/saving/<platform>-<id>``), the files named as a sync names them
(gallery-dl's default names, yt-dlp's sync.YT_DLP_NAME). Whose post it is is
only known once it is there: the metadata names the owner, and the files
move to the owner's folder (owner_folder), never over a file already there.
That folder is then indexed, and the post's entries go into the download
archives (archives.py) as trashing would add them: a later sync of the
profile does not download it again (with gallery-dl or yt-dlp for X; with
yt-dlp for TikTok, as no gallery-dl entry can be made from yt-dlp's files).
Only a run that exited 0 moves anything: what a failed one left (an info
JSON and thumbnail without their video) is dropped with the saving folder.

Same lock group and pause as the tool's syncs; the queue limit is
save.py's, Instagram saves included.
"""
import os
import re
import shutil
from urllib.parse import urlsplit

import archives
import config
import db
import health
import info_cookies
import jobs
import people
import save
import scanner
import sources
import sync
from parsers import gallery_dl as gallery_dl_parser, yt_dlp as yt_dlp_parser

# platform -> what saves one of its posts. ``hosts``: the link's host, whole
# (lowercase), nothing else; ``short``: short-link hosts, refused with a hint.
PLATFORMS = {
    "twitter": {"tool": "gallery-dl", "kind": "gallery-dl-post", "name": "X",
                "hosts": {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com",
                          "mobile.twitter.com"},
                "short": {"t.co"}},
    "tiktok": {"tool": "yt-dlp", "kind": "yt-dlp-post", "name": "TikTok",
               "hosts": {"tiktok.com", "www.tiktok.com", "m.tiktok.com"},
               "short": {"vm.tiktok.com", "vt.tiktok.com"}},
}
LINK_MAX = 500
# A profile's link, as the userscript's Sync profile adds it: the folder a
# save falls back to is the one that source would get (sources.default_folder).
PROFILE_LINKS = {"twitter": "https://x.com/{handle}", "tiktok": "https://www.tiktok.com/@{handle}"}
# A post id: digits only, ASCII, no leading zero (both sites' ids are 64-bit numbers).
ID_RE = re.compile(r"[1-9][0-9]{0,19}")
# X: /<name>/status/<id>, /i/web/status/<id>, /i/status/<id>, optionally a
# photo or video of it (/photo/1). The name is not used: the id says it all.
_X_PATH_RE = re.compile(r"/(?:i/web|i|[A-Za-z0-9_]{1,15})/status/([0-9]+)(?:/(?:photo|video)/[1-4])?/?")
# TikTok: /@<name>/video/<id>. Its names: letters, digits, _ and ., at most 24.
TIKTOK_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,24}")
_TIKTOK_PATH_RE = re.compile(r"/@([^/]+)/(video|photo)/([0-9]+)/?")
LINK_HELP = ("send { \"url\": \"<a post's link>\" }: https://x.com/<name>/status/<id> or "
             "https://www.tiktok.com/@<name>/video/<id>, and nothing else")


class BadLink(ValueError):
    """Not a post link this can save; the message is for the user."""


def parse_link(text):
    """{platform, id, handle} of a post link, else raises BadLink. Query and
    fragment are dropped; nothing is fetched (a short link is refused, not
    followed)."""
    if not isinstance(text, str) or not text or len(text) > LINK_MAX \
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text) or not text.isascii():
        raise BadLink("not a post link")
    try:
        u = urlsplit(text)
        port = u.port
    except ValueError:
        raise BadLink("not a post link")
    if u.scheme not in ("http", "https"):
        raise BadLink("only http and https links")
    if u.username is not None or u.password is not None or "@" in u.netloc or port not in (None, 80, 443):
        raise BadLink("a link with a login part or a port is refused")
    host = (u.hostname or "").lower()
    platform = next((p for p, spec in PLATFORMS.items() if host in spec["hosts"]), None)
    if platform is None:
        short = next((spec["name"] for spec in PLATFORMS.values() if host in spec["short"]), None)
        if short:
            raise BadLink(f"{host} is a short link, which only {short} can resolve: open it and save "
                          "from the post's own page")
        raise BadLink(f"{host or 'that'} is not X or TikTok")
    if platform == "twitter":
        m = _X_PATH_RE.fullmatch(u.path)
        if m is None:
            raise BadLink("not a link to a post on X (https://x.com/<name>/status/<id>)")
        pid, handle = m.group(1), None
    else:
        m = _TIKTOK_PATH_RE.fullmatch(u.path)
        if m is None:
            raise BadLink("not a link to a TikTok video (https://www.tiktok.com/@<name>/video/<id>)")
        handle, what, pid = m.groups()
        if what == "photo":
            raise BadLink("TikTok photo posts cannot be saved: yt-dlp downloads videos")
        if not TIKTOK_HANDLE_RE.fullmatch(handle):
            raise BadLink("not a TikTok profile name")
    if not ID_RE.fullmatch(pid):
        raise BadLink("not a post id")
    return {"platform": platform, "id": pid, **({"handle": handle} if handle else {})}


def link_of(params):
    """The link the tool gets, built from checked params only. Raises
    jobs.BadRequest."""
    platform, pid, handle = params.get("platform"), params.get("id"), params.get("handle")
    if platform not in PLATFORMS or not isinstance(pid, str) or not ID_RE.fullmatch(pid):
        raise jobs.BadRequest("not a post FeedVault can save")
    if platform == "twitter":
        if handle is not None:
            raise jobs.BadRequest("an X save takes no name")
        return f"https://x.com/i/web/status/{pid}"
    if not isinstance(handle, str) or not TIKTOK_HANDLE_RE.fullmatch(handle):
        raise jobs.BadRequest("not a TikTok profile name")
    return f"https://www.tiktok.com/@{handle}/video/{pid}"


def post_id(params):
    return f"{params['platform']}:{params['id']}"


def staging(params, cfg=None):
    tool = PLATFORMS[params["platform"]]["tool"]
    return os.path.join((cfg or config.load())["data_directory"], tool, "saving", f"{params['platform']}-{params['id']}")


def _build(params, platform):
    if params.get("platform") != platform:
        raise jobs.BadRequest("not a post of this kind")
    link = link_of(params)
    cfg = config.load()
    if not cfg["media_roots"]:
        raise jobs.BadRequest("add a media root in Settings first")
    tool = PLATFORMS[platform]["tool"]
    stage = staging(params, cfg)
    # Both tools expand $NAME in the folder they are given (os.path.expandvars).
    if "$" in stage:
        raise jobs.BadRequest("the data directory holds a $, which the tool would expand")
    session = sync.session_of(tool, sources.clean_options(None), cfg)
    if tool == "gallery-dl":
        args = [*sync.config_flags(tool, cfg), "--write-metadata", "-D", stage]
    else:
        # The output template is %-formatted: a % in the folder is doubled.
        args = [*sync.config_flags(tool, cfg), "--write-info-json", "--write-thumbnail", "--no-playlist",
                "-o", os.path.join(stage.replace("%", "%%"), sync.YT_DLP_NAME)]
    return {"tool": tool, "args": [*args, *sync.cookie_flags(session), "--", link]}


def _start(params, note, argv=None):
    """Right before the tool starts: an empty saving folder (a cancelled
    run may have left files)."""
    link_of(params)
    stage = staging(params)
    save._clear(stage)
    config.make_private_dir(stage)


def _strip_cookies(job, note):
    """After yt-dlp: the info JSON it wrote without the cookies it copies
    into it, before anything moves (as after a sync)."""
    params = job["params"]
    if job["started_at"] is None:
        return
    cleaned, failed = info_cookies.after_sync(staging(params), {})
    if cleaned:
        note(f"cookies removed from {cleaned} info JSON{'' if cleaned == 1 else 's'}")
    for path, error in failed:
        note(f"could not remove the cookies from {path}: {error}")


# ---------------------------------------------------------------------------
# Where the files go
# ---------------------------------------------------------------------------

def _usable(folder, roots):
    real = sources.inside_root(folder, roots) if folder else None
    return real if real and not sources.in_saved(real, roots) else None


def owner_folder(conn, roots, platform, tool, author_id, handle):
    """The folder a post of this owner goes to: the folder of the owner's
    gallery-dl or yt-dlp source (this tool's first), else of one whose link
    names the handle, else the folder holding most of the owner's posts
    those tools got, else the folder a new source for the handle would
    download into (``<first root>/<platform>/<name>``, made), else
    ``<first root>/_saved``. Always inside a media root."""
    handle = handle if isinstance(handle, str) and handle else None
    keys = [people.canonical(conn, platform, a) for a in (author_id, handle and handle.lower()) if a]
    for key in keys:
        for (folder,) in conn.execute("SELECT folder FROM sources WHERE tool != 'instaloader' AND platform = ? "
                                      "AND author_id = ? ORDER BY tool = ? DESC, id", (*key, tool)):
            if _usable(folder, roots):
                return folder
    if handle:
        for folder, target in conn.execute("SELECT folder, target FROM sources WHERE tool != 'instaloader' "
                                           "AND platform = ? ORDER BY tool = ? DESC, id", (platform, tool)):
            if (sources.profile_handle(target) or "").lower() == handle.lower() and _usable(folder, roots):
                return folder
    for key in keys:
        clause, args = db.post_filter(platform=key[0], author=key[1])
        counts = {}
        for (path,) in conn.execute(f"SELECT p.meta_path {db._FROM} {clause} "
                                    f"AND p.tool IN ('gallery-dl', 'yt-dlp')", args):
            folder = os.path.dirname(path)
            if _usable(folder, roots):
                counts[folder] = counts.get(folder, 0) + 1
        if counts:
            return max(counts, key=lambda f: (counts[f], f))
    if handle and re.sub(r"[^a-z0-9._-]+", "_", handle.lower()).strip("._"):
        return sources.default_folder(tool, platform, PROFILE_LINKS[platform].format(handle=handle), roots)
    return os.path.join(roots[0], save.SAVED)


def _read(params, stage):
    """The post the tool saved, as its parser reads the saving folder, or None."""
    platform = params["platform"]
    try:
        names = [n for n in os.listdir(stage) if os.path.isfile(os.path.join(stage, n))]
    except OSError:
        return None
    parser = gallery_dl_parser if PLATFORMS[platform]["tool"] == "gallery-dl" else yt_dlp_parser
    # Only the post asked for: a retweet's link saves the original under its
    # own id, which is not the one FeedVault would answer for.
    posts = parser.parse_dir(stage, stage, names).posts
    return next((p for p in posts if p.platform == platform and p.post_id == params["id"]), None)


def _place(params, stage, roots, note):
    """Move the saved files to the owner's folder. Returns the folder, or
    None when the tool saved no post."""
    post = _read(params, stage)
    if post is None:
        return None
    conn = db.connect()
    tool = PLATFORMS[params["platform"]]["tool"]
    folder = owner_folder(conn, roots, params["platform"], tool, post.author_id, post.author_handle)
    os.makedirs(folder, exist_ok=True)
    moved = kept = 0
    for name in sorted(os.listdir(stage)):
        src, dst = os.path.join(stage, name), os.path.join(folder, name)
        if not os.path.isfile(src) or os.path.islink(src):
            continue
        if os.path.lexists(dst):
            kept += 1                          # never over a file already there
            continue
        shutil.move(src, dst)
        moved += 1
    note(f"{moved} file{'' if moved == 1 else 's'} moved to {folder}"
         + (f"; {kept} already there, kept as they were" if kept else ""))
    return folder


def record(conn, pid):
    """Add the indexed post's entries to the download archives, as trashing
    it would (archives.post_entries, with the installed gallery-dl's
    formats): a sync of its profile skips it. Returns how many were new."""
    row = conn.execute("SELECT id, tool, meta_path, side_files FROM posts WHERE id = ?", (pid,)).fetchone()
    if row is None:
        return 0
    if row["tool"] == "gallery-dl":
        archives.installed_formats()           # read now, so post_entries has them (it never starts gallery-dl)
    data_dir = config.load()["data_directory"]
    return sum(len(archives.add(tool, e, data_dir)) for tool, e in archives.post_entries(row).items())


# ---------------------------------------------------------------------------
# How it went
# ---------------------------------------------------------------------------

MESSAGES = {
    "rate_limited": "{name} is limiting requests: wait a while before saving again",
    "not_found": "Post not found: removed, or only visible when logged in",
    "private": "Private post: the cookies in use do not have access to it",
    "login_required": "{name} wants a logged-in session for this post; see Settings → Sync (browser cookies)",
    "generic": "{tool} failed",
}
# A private post when the save used no cookies (mode "none", #124).
NO_SESSION = "Private post and no cookies in use: set a browser's cookies with access in Settings → Sync"


def _outcome(params, code, lines, index, note=None):
    platform = params["platform"]
    spec = PLATFORMS[platform]
    stage = staging(params)
    roots = config.load()["media_roots"]
    result = {"post": None, "folder": None, "added": 0, "updated": 0, "error": None, "line": None,
              "account": None, "person": None, "archived": 0}
    notes = []
    pid = post_id(params)
    try:
        folder = _place(params, stage, roots, notes.append) if roots and code == 0 else None
    except OSError as e:                       # a folder that cannot be made or written
        folder = None
        result["error"] = "generic"
        notes.append(f"could not move the files: {e.strerror or e}")
    for text in notes:
        print(f"[save] {pid}: {text}")
        if note:
            note(text)
    save._clear(stage)
    if folder:
        report = scanner.index_dirs(roots, [folder], new=True)
        result.update(folder=folder, added=report["added"], updated=report["updated"])
        conn = db.connect()
        if db.saved_ids(conn, [pid]):
            result["post"] = pid
            result["archived"] = record(conn, pid)
            row = conn.execute("SELECT platform, author_id FROM posts WHERE id = ?", (pid,)).fetchone()
            if row["author_id"]:
                key = people.canonical(conn, row["platform"], row["author_id"])
                result["account"] = {"platform": key[0], "id": key[1]}
                a = db.accounts(conn).get(key)
                result["person"] = a["person"]["id"] if a and a["person"] else None
    if result["error"]:
        return "failed", result, f"Downloaded, but {notes[-1]}"
    if code == 0 and result["post"]:
        return "done", result, f"Saved in {os.path.basename(folder)}"
    if code == 0:
        result["error"] = "generic"
        return "failed", result, f"{spec['tool']} saved no post" + (" (a post with no media?)" if
                                                                     platform == "twitter" else "")
    result["error"], line = sync.classify(lines, health.TABLES[spec["tool"]])
    result["line"] = health.scrub(line)
    message = MESSAGES[result["error"]].format(name=spec["name"], tool=spec["tool"])
    if result["error"] == "private" and sync.tool_settings(spec["tool"])["session"]["mode"] == "none":
        message = NO_SESSION                   # the setting _build read: a save has no session of its own
    if result["error"] == "generic" and result["line"]:
        message = f"{message}: {result['line'][:200]}"
    return "failed", result, message


def _ended(job):
    """What a run left in its saving folder (see save._ended)."""
    params = job["params"]
    if job["started_at"] is not None and params.get("platform") in PLATFORMS \
            and isinstance(params.get("id"), str) and ID_RE.fullmatch(params["id"]):
        save._clear(staging(params))


PARAMS = {"platform": {"type": "choice", "choices": sorted(PLATFORMS)}, "id": {"type": "text", "max": 20},
          "handle": {"type": "text", "max": 24, "required": False}}

for _platform, _spec in PLATFORMS.items():
    jobs.register(_spec["kind"], label=f"Save a post from {_spec['name']}", params=PARAMS,
                  build=lambda params, p=_platform: _build(params, p), group=_spec["tool"],
                  start=_start, outcome=_outcome, ended=_ended,
                  after=_strip_cookies if _spec["tool"] == "yt-dlp" else None,
                  pause=lambda params, t=_spec["tool"]: sync.tool_settings(t)["pause"],
                  describe=lambda params, argv, n=_spec["name"]: f"Save {n} post {params.get('id')}")
    save.KINDS.add(_spec["kind"])


# ---------------------------------------------------------------------------
# Starting one
# ---------------------------------------------------------------------------

def submit(params):
    """save.queue for a parsed link: (job, already queued or running)."""
    return save.queue(PLATFORMS[params["platform"]]["kind"], params, ("platform", "id"))
