"""Download archives: how gallery-dl and yt-dlp know what they already have.

Both tools skip what their archive lists, and FeedVault keeps the archives in
its data directory, away from the media, so what decides "already
downloaded" is not which files exist:

    gallery-dl  <data_dir>/gallery-dl/archive.sqlite3   table archive(entry), one entry per
                                                        file: <category><the extractor's archive_fmt>
    yt-dlp      <data_dir>/yt-dlp/archive.txt           one line per video: "<extractor> <id>"

FeedVault writes to them in two cases:

- seeding, before a source's first sync: an entry for every post already
  indexed for the source's account, so the first sync does not download them
  again (gallery-dl from the per-file metadata JSONs of its own posts, with
  the archive formats of the installed gallery-dl when they can be read,
  else a fixed table; yt-dlp from every post's platform and id, which are
  yt-dlp's extractor and id).
- "never again": trashing a gallery-dl or yt-dlp post adds its entries, so a
  sync never brings it back; restoring it removes the ones trashing added.

gallery-dl opens the database with SQLite's locking, yt-dlp appends to its
file under flock(); FeedVault does the same, so a sync running meanwhile
loses nothing.
"""
import fcntl
import json
import os
import re
import signal
import sqlite3
import string
import subprocess
import tempfile
import threading

import db

TOOLS = ("gallery-dl", "yt-dlp")
FILES = {"gallery-dl": "archive.sqlite3", "yt-dlp": "archive.txt"}

# gallery-dl's archive_fmt per category, for the extractors of a profile's
# posts (gallery-dl 1.32). An entry is the category followed by it, filled
# from the file's metadata JSON. Used when the installed gallery-dl's own
# formats cannot be read (installed_formats); categories in neither are not
# seeded.
GALLERY_DL_FORMATS = {
    "twitter": "{tweet_id}_{retweet_id}_{num}",
    "tiktok": "{id}_{num}_{file_id}",
    "instagram": "{media_id}",
    "reddit": "{filename}",
    "bluesky": "{filename}",
    "pixiv": "{id}{suffix}.{extension}",
}


def path(tool, data_dir):
    return os.path.join(data_dir, tool, FILES[tool])


# ---------------------------------------------------------------------------
# The installed gallery-dl's archive formats
# ---------------------------------------------------------------------------

FORMATS_TIMEOUT = 20                           # seconds; importing every extractor takes one or two
FORMATS_MAX = 4 * 1024 * 1024                  # bytes of output
FORMATS_COUNT = 5000                           # extractor classes
# Run by the installed gallery-dl's own Python, nothing from outside in it:
# [category, subcategory, archive_fmt] of every extractor class, as JSON.
_FORMATS_CODE = """\
import json
from gallery_dl import extractor
out = []
for cls in extractor.extractors():
    fmt = getattr(cls, "archive_fmt", None)
    if all(isinstance(v, str) for v in (cls.category, cls.subcategory, fmt)):
        out.append([cls.category, cls.subcategory, fmt])
print(json.dumps(out))
"""
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(?:\[[A-Za-z0-9_]{1,64}\]){0,3}")    # key, key[sub]
_SPEC_RE = re.compile(r"[<>^]?0?\d?")           # at most "{num:>02}": never a width that fills memory
_PYTHON_RE = re.compile(r"python(?:\d+(?:\.\d+)?)?")

_formats_lock = threading.Lock()               # the cache below, never held while gallery-dl runs
_formats_run = threading.Lock()                # one run at a time
_formats = {"key": None, "formats": None, "error": None, "last": None}


def _safe_format(fmt):
    """Whether an archive_fmt only names keys (``{id}``, ``{asset[id]}``),
    with at most a short width: no attribute lookups, no conversions, none
    of gallery-dl's own formatter syntax (``|``, ``?``), which str.format
    would not fill the way gallery-dl does."""
    if not isinstance(fmt, str) or not 0 < len(fmt) <= 200:
        return False
    try:
        parts = list(string.Formatter().parse(fmt))
    except ValueError:
        return False
    return all(name is None or (_FIELD_RE.fullmatch(name) and conv is None and _SPEC_RE.fullmatch(spec or ""))
               for _, name, spec, conv in parts)


def _python_of(exe):
    """The argv that runs the Python the gallery-dl at ``exe`` runs with:
    its virtualenv's (pipx, a venv), else its ``#!`` line's when that names
    a Python by absolute path (``/usr/bin/env python3`` too; with flags such
    as a distribution's ``-sP``); else None (a standalone build)."""
    import downloaders                         # it imports sync, which imports this module
    kind, venv = downloaders.install_of(exe)
    if venv:
        python = os.path.join(venv, "bin", "python")
        return [python] if os.access(python, os.X_OK) else None
    try:
        with open(exe, "rb") as f:
            first = f.readline(256)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    words = first[2:].decode("utf-8", "replace").split()
    if not words or not os.path.isabs(words[0]):
        return None
    if os.path.basename(words[0]) == "env":
        return words if len(words) == 2 and _PYTHON_RE.fullmatch(words[1]) else None
    flags = words[1:]
    if not _PYTHON_RE.fullmatch(os.path.basename(words[0])) or len(flags) > 2 \
            or not all(re.fullmatch(r"-[A-Za-z]{1,4}", f) for f in flags):
        return None
    return words


def _read_formats(exe):
    """({(category, subcategory): fmt, (category, None): fmt when all of a
    category's extractors share one}, error) from the gallery-dl at ``exe``."""
    import jobs
    python = _python_of(exe)
    if python is None:
        return None, "not a Python install FeedVault can ask"
    with tempfile.TemporaryDirectory() as cwd:   # nothing to import by accident from where it runs
        try:
            proc = subprocess.Popen([*python, "-c", _FORMATS_CODE], cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            return None, f"could not start {python[0]}: {e.strerror or e}"
        try:
            out, _ = proc.communicate(timeout=FORMATS_TIMEOUT)
        except subprocess.TimeoutExpired:
            jobs._killpg(proc, signal.SIGKILL)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:  # a process that left the group still holds the pipe
                proc.stdout.close()
            return None, f"no answer within {FORMATS_TIMEOUT} s"
        finally:
            jobs._killpg(proc, signal.SIGKILL)   # anything it left behind in its group
    if proc.returncode != 0:
        return None, f"it could not list its extractors (exit code {proc.returncode})"
    if len(out) > FORMATS_MAX:
        return None, "its answer is too large"
    try:
        rows = json.loads(out)
    except ValueError:
        return None, "its answer is not JSON"
    if not isinstance(rows, list) or len(rows) > FORMATS_COUNT:
        return None, "its answer is not a list of extractors"
    formats, by_category, refused = {}, {}, set()
    for row in rows:
        if not (isinstance(row, list) and len(row) == 3 and all(isinstance(v, str) for v in row)):
            continue
        category, sub, fmt = row
        if not (_NAME_RE.fullmatch(category) and (sub == "" or _NAME_RE.fullmatch(sub)) and _safe_format(fmt)):
            refused.add(category)
            continue
        formats[(category, sub)] = fmt
        by_category.setdefault(category, set()).add(fmt)
    for category, fmts in by_category.items():
        # One format for the whole category only when no extractor of it was
        # refused: a refused one's files would get the others' format.
        if len(fmts) == 1 and category not in refused:
            formats[(category, None)] = next(iter(fmts))
    return (formats, None) if formats else (None, "it listed no archive format")


def installed_formats(run=True):
    """(formats, error) of the installed gallery-dl (see _read_formats),
    read once per executable and version of its file and kept in memory; a
    failure too. ``run`` False: only what is already known, nothing started:
    the last formats read, even from an older version (gallery-dl rarely
    changes one), else (None, None)."""
    import jobs
    exe = jobs.tool_path("gallery-dl")
    if exe is None:
        return None, "gallery-dl is not installed"
    try:
        st = os.stat(exe)
        key = (exe, os.path.realpath(exe), st.st_mtime_ns, st.st_size)
    except OSError as e:
        return None, f"cannot read {exe}: {e.strerror or e}"
    with _formats_lock:
        if _formats["key"] == key:
            return _formats["formats"], _formats["error"]
        if not run:
            return _formats["last"], None
    with _formats_run:
        with _formats_lock:
            if _formats["key"] == key:         # read by the run this one waited for
                return _formats["formats"], _formats["error"]
        formats, error = _read_formats(exe)
        with _formats_lock:
            _formats.update(key=key, formats=formats, error=error, last=formats or _formats["last"])
        return formats, error


def warm(conn):
    """At startup: read the installed gallery-dl's formats in the
    background, for the trash's "never again" entries (which never start
    it, post_entries), but only when there is a gallery-dl source: without
    one, gallery-dl may not even be installed, and the first seed reads
    them. Returns the thread started, or None."""
    if not conn.execute("SELECT 1 FROM sources WHERE tool = 'gallery-dl' LIMIT 1").fetchone():
        return None
    t = threading.Thread(target=installed_formats, daemon=True, name="gallery-dl-formats")
    t.start()
    return t


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def _format_of(d, formats):
    category = d.get("category")
    if formats and isinstance(category, str):
        sub = d.get("subcategory")
        fmt = formats.get((category, sub)) if isinstance(sub, str) else None
        if fmt is None:
            fmt = formats.get((category, None))
        if fmt is not None:
            return fmt
    return GALLERY_DL_FORMATS.get(category)


def gallery_dl_entry(d, formats=None):
    """The archive entry of the file a gallery-dl metadata JSON describes,
    or None (a post-level JSON, an unknown category, a key missing).
    ``formats``: installed_formats()'s, else the fixed table only."""
    if not isinstance(d, dict) or "filename" not in d or "extension" not in d:
        return None
    fmt = _format_of(d, formats)
    if fmt is None:
        return None
    try:
        return d["category"] + fmt.format_map(d)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return None


def _json_entries(paths, formats=None, unknown=None):
    """The entries of these files' metadata JSONs; ``unknown``, when given,
    counts per category the files with no entry: no format known for their
    category, or their metadata lacks the format's keys."""
    out = []
    for p in paths:
        if not p.endswith(".json"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        entry = gallery_dl_entry(d, formats)
        if entry:
            out.append(entry)
        elif unknown is not None and isinstance(d, dict) and "filename" in d and "extension" in d:
            category = str(d.get("category"))[:64]
            unknown[category] = unknown.get(category, 0) + 1
    return out


def yt_dlp_entry(post_id):
    """yt-dlp's line for a post id ("tiktok:123": "tiktok 123"): the
    platform is the extractor's key, lowercase, and the id its own."""
    platform, _, pid = post_id.partition(":")
    return f"{platform} {pid}" if platform and pid and pid.splitlines() == [pid] else None


def post_entries(post):
    """{tool: [entries]} a trashed post adds: a gallery-dl post its files'
    entries, and a gallery-dl or yt-dlp post its yt-dlp line (both tools
    download TikTok and X: whichever syncs it next, it stays gone). Other
    posts nothing (instaloader has no archive)."""
    tool = post["tool"] or ""
    out = {}
    if tool == "gallery-dl":
        sides = json.loads(post["side_files"] or "[]")
        out["gallery-dl"] = _json_entries([post["meta_path"], *sides], installed_formats(run=False)[0])
    if tool in ("gallery-dl", "yt-dlp"):
        line = yt_dlp_entry(post["id"])
        if line:
            out["yt-dlp"] = [line]
    return {t: e for t, e in out.items() if e}


def media_entries(post, media):
    """{tool: [entries]} for one media item trashed on its own (the post
    stays): gallery-dl's entry for that file, from the JSON beside it."""
    if (post["tool"] or "") != "gallery-dl":
        return {}
    jsons = [post["meta_path"], *json.loads(post["side_files"] or "[]")]
    files = [p for p in (media["path"], media["poster_path"]) if p]
    mine = [j for j in jsons if any(j in (f + ".json", os.path.splitext(f)[0] + ".json") for f in files)]
    entries = _json_entries(mine, installed_formats(run=False)[0])
    return {"gallery-dl": entries} if entries else {}


# ---------------------------------------------------------------------------
# Reading and writing the archives
# ---------------------------------------------------------------------------

def _sqlite(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p, timeout=60)
    conn.execute("CREATE TABLE IF NOT EXISTS archive (entry TEXT PRIMARY KEY) WITHOUT ROWID")
    return conn


def _locked(p):
    """The yt-dlp archive, opened for reading and writing under an exclusive flock()."""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    f = open(p, "a+", encoding="utf-8")
    fcntl.flock(f, fcntl.LOCK_EX)
    f.seek(0)
    return f


def add(tool, entries, data_dir):
    """Add entries to a tool's archive. Returns those that were not there."""
    # One line each: splitlines() also breaks at \r, \x1c, \u2028 ….
    entries = list(dict.fromkeys(e for e in entries if isinstance(e, str) and e and e.splitlines() == [e]))
    if not entries:
        return []
    p = path(tool, data_dir)
    if tool == "gallery-dl":
        conn = _sqlite(p)
        try:
            added = []
            with conn:
                for e in entries:
                    if conn.execute("INSERT OR IGNORE INTO archive(entry) VALUES (?)", (e,)).rowcount:
                        added.append(e)
            return added
        finally:
            conn.close()
    with _locked(p) as f:
        have = set(f.read().splitlines())
        added = [e for e in entries if e not in have]
        if added:
            f.seek(0, os.SEEK_END)
            f.write("".join(e + "\n" for e in added))
        return added


def remove(tool, entries, data_dir):
    """Take entries out of a tool's archive. Returns how many were there."""
    entries = set(e for e in entries if isinstance(e, str) and e)
    p = path(tool, data_dir)
    if not entries or not os.path.exists(p):
        return 0
    if tool == "gallery-dl":
        conn = _sqlite(p)
        try:
            with conn:
                return sum(conn.execute("DELETE FROM archive WHERE entry = ?", (e,)).rowcount for e in entries)
        finally:
            conn.close()
    with _locked(p) as f:
        lines = f.read().splitlines()
        keep = [ln for ln in lines if ln not in entries]
        if len(keep) == len(lines):
            return 0
        # Rewritten in place, under the lock: a rename would leave a yt-dlp
        # appending meanwhile writing to the old file.
        f.seek(0)
        f.truncate()
        f.write("".join(ln + "\n" for ln in keep))
        return len(lines) - len(keep)


def has(tool, entry, data_dir):
    p = path(tool, data_dir)
    if not os.path.exists(p):
        return False
    if tool == "gallery-dl":
        conn = _sqlite(p)
        try:
            return conn.execute("SELECT 1 FROM archive WHERE entry = ?", (entry,)).fetchone() is not None
        finally:
            conn.close()
    with open(p, encoding="utf-8") as f:
        return entry in f.read().splitlines()


def seed(tool, conn, platform, author_id, data_dir, formats=None, unknown=None):
    """Add an entry for every post indexed for an account (aliases
    included), before its first sync. Returns (posts, entries added).
    gallery-dl: ``formats`` as for gallery_dl_entry; ``unknown`` (a dict)
    gets the files per category that could not be seeded."""
    clause, args = db.post_filter(platform=platform, author=author_id)
    rows = conn.execute(f"SELECT p.id, p.tool, p.meta_path, p.side_files {db._FROM} {clause}", args).fetchall()
    entries = []
    for r in rows:
        if tool == "gallery-dl":
            if r["tool"] == "gallery-dl":
                entries.extend(_json_entries([r["meta_path"], *json.loads(r["side_files"] or "[]")],
                                             formats, unknown))
        else:
            line = yt_dlp_entry(r["id"])
            if line:
                entries.append(line)
    return len(rows), len(add(tool, entries, data_dir))


# ---------------------------------------------------------------------------
# Trash and restore (trash.py)
# ---------------------------------------------------------------------------

def never_again(entries, data_dir):
    """Add a trashed post's entries ({tool: [entries]}). Returns {tool:
    [entries added]}, for the manifest: restore takes out only those, not
    what the archive had before. A failure is logged, never stops a trash."""
    out = {}
    for tool, es in entries.items():
        try:
            added = add(tool, es, data_dir)
        except (OSError, sqlite3.Error) as e:
            print(f"[archives] could not add to the {tool} archive: {e}")
            continue
        if added:
            out[tool] = added
    return out


def take_back(entries, data_dir):
    """Remove what never_again added ({tool: [entries]}, from the manifest)."""
    for tool, es in (entries or {}).items():
        if tool not in TOOLS or not isinstance(es, list):
            continue
        try:
            remove(tool, es, data_dir)
        except (OSError, sqlite3.Error) as e:
            print(f"[archives] could not remove from the {tool} archive: {e}")
