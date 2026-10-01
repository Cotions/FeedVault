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
  again (gallery-dl from the per-file metadata JSONs of its own posts; yt-dlp
  from every post's platform and id, which are yt-dlp's extractor and id).
- "never again": trashing a gallery-dl or yt-dlp post adds its entries, so a
  sync never brings it back; restoring it removes the ones trashing added.

gallery-dl opens the database with SQLite's locking, yt-dlp appends to its
file under flock(); FeedVault does the same, so a sync running meanwhile
loses nothing.
"""
import fcntl
import json
import os
import sqlite3

import db

TOOLS = ("gallery-dl", "yt-dlp")
FILES = {"gallery-dl": "archive.sqlite3", "yt-dlp": "archive.txt"}

# gallery-dl's archive_fmt per category, for the extractors of a profile's
# posts (gallery-dl 1.32). An entry is the category followed by it, filled
# from the file's metadata JSON. Categories not here are not seeded.
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
# Entries
# ---------------------------------------------------------------------------

def gallery_dl_entry(d):
    """The archive entry of the file a gallery-dl metadata JSON describes,
    or None (a post-level JSON, an unknown category, a key missing)."""
    if not isinstance(d, dict) or "filename" not in d or "extension" not in d:
        return None
    fmt = GALLERY_DL_FORMATS.get(d.get("category"))
    if fmt is None:
        return None
    try:
        return d["category"] + fmt.format_map(d)
    except (KeyError, IndexError, ValueError, AttributeError):
        return None


def _json_entries(paths):
    out = []
    for p in paths:
        if not p.endswith(".json"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                entry = gallery_dl_entry(json.load(f))
        except (OSError, ValueError):
            continue
        if entry:
            out.append(entry)
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
        out["gallery-dl"] = _json_entries([post["meta_path"], *sides])
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
    entries = _json_entries(mine)
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


def seed(tool, conn, platform, author_id, data_dir):
    """Add an entry for every post indexed for an account (aliases
    included), before its first sync. Returns (posts, entries added)."""
    clause, args = db.post_filter(platform=platform, author=author_id)
    rows = conn.execute(f"SELECT p.id, p.tool, p.meta_path, p.side_files {db._FROM} {clause}", args).fetchall()
    entries = []
    for r in rows:
        if tool == "gallery-dl":
            if r["tool"] == "gallery-dl":
                entries.extend(_json_entries([r["meta_path"], *json.loads(r["side_files"] or "[]")]))
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
