#!/usr/bin/env python3
"""Stand-ins for gallery-dl and yt-dlp, for tests and the demo: never touch the network.

Install on a PATH as ``gallery-dl`` / ``yt-dlp`` (a small script calling
``gallery_dl_main`` or ``yt_dlp_main``). Both read their accounts from the
JSON file named by FAKE_DOWNLOADS (default ``fake_downloads.json`` beside
this script), keyed by the profile link FeedVault passes:

    {"accounts": {
        "https://x.com/someone/media": {
            "category": "twitter", "user": {"id": 900, "name": "someone", "nick": "Some One"},
            "posts": [{"id": "1800000000000000001", "ts": 1717243200, "text": "…", "files": 2}]},
        "https://tiktok.com/@someone": {
            "extractor_key": "TikTok", "uploader_id": "6800000000000000009", "uploader": "someone",
            "channel": "Some One", "videos": [{"id": "7300000000000000001", "ts": 1717243200,
                                               "title": "…", "description": "…", "duration": 12}],
            "pinned": ["7300000000000000001"]}},
     "fail": null | "429" | "login" | "private" | "notfound",
     "config_cookies": false}

and behave like gallery-dl 1.32 and yt-dlp 2026.08 for one profile link with
the flags FeedVault passes, newest post first:

- both: the flag that skips the user's config (``--config-ignore``,
  ``--ignore-config``), with no effect: there is no config to skip.
- both: ``--simulate`` (and yt-dlp's ``--no-playlist``), as Settings →
  Downloaders → Test runs them on one public item: one line and exit 0,
  or the ``fail`` setting's output and exit code; writes nothing.
- gallery-dl: ``--write-metadata`` (``<file>.<ext>.json`` beside each file,
  shaped like the fixtures), ``--download-archive`` (its SQLite table, one
  ``<category><archive_fmt>`` entry per file), ``-o skip=abort:N`` (stops
  after N files in a row already in the archive or on disk), ``-D``.
- yt-dlp: ``--write-info-json --write-thumbnail``, ``--download-archive``
  (``<extractor> <id>`` lines), ``--break-on-existing`` (exit 101 at the first
  archived video), ``-o`` (``%(uploader_id)s``, ``%(upload_date)s``,
  ``%(id)s``, ``%(ext)s``, ``%%``), ``--match-filters "duration <= N"``. A
  YouTube channel also gets its playlist info JSON. ``pinned`` videos are
  listed first, as TikTok lists a profile's pinned videos. With
  ``--cookies-from-browser`` (or ``config_cookies``: cookies from the
  user's own yt-dlp config) the info JSON holds the cookies, as yt-dlp's
  does (``cookies`` in each format and at the top, a ``Cookie`` in their
  ``http_headers``).

Every run appends {"tool", "argv", "at"} as one JSON line to
FAKE_DOWNLOADS_LOG, when set.
"""
import argparse
import json
import os
import re
import sqlite3
import struct
import sys
import time
import zlib
from datetime import datetime, timezone


def png(path, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * 32 for _ in range(32))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def mp4(path):
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)


def colour(text):
    return tuple((sum(map(ord, text)) * k) % 256 for k in (3, 7, 11))


def _data():
    path = os.environ.get("FAKE_DOWNLOADS") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                            "fake_downloads.json")
    with open(path) as f:
        return json.load(f)


def _log(tool, argv):
    if os.environ.get("FAKE_DOWNLOADS_LOG"):
        with open(os.environ["FAKE_DOWNLOADS_LOG"], "a") as f:
            f.write(json.dumps({"tool": tool, "argv": argv, "at": time.time()}) + "\n")


# --- gallery-dl ---------------------------------------------------------------

GALLERY_DL_FAIL = {
    "429": ("HttpError", "'429 Too Many Requests' for 'https://api.x.com/graphql'", 4),
    "login": ("AuthRequired", "'cookies' needed to access this timeline", 16),
    "private": ("AuthorizationError", "{name}'s Tweets are protected", 16),
    "notfound": ("NotFoundError", "Requested user could not be found", 4),
}


def _gallery_dl_files(account, post):
    """(file name, metadata) per file of a post, as gallery-dl would name them."""
    date = datetime.fromtimestamp(post["ts"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    user = account["user"]
    out = []
    if account["category"] == "twitter":
        for num in range(1, post.get("files", 1) + 1):
            ext = "mp4" if post.get("video") else "jpg"
            out.append((f"{post['id']}_{num}.{ext}", {
                "category": "twitter", "subcategory": "media", "tweet_id": int(post["id"]), "retweet_id": 0,
                "quote_id": 0, "num": num, "count": post.get("files", 1), "date": date,
                "content": post.get("text", ""), "author": user, "user": user, "favorite_count": 3,
                "reply_count": 1, "view_count": 40, "hashtags": [], "type": "video" if post.get("video") else "photo",
                "filename": f"{post['id']}{num:x}", "extension": ext}))
    else:                                       # tiktok
        n = post.get("files", 0)
        items = [(0, "", "mp4")] if not n else [(i, f"{post['id'][-6:]}{i:02d}", "jpg") for i in range(1, n + 1)]
        for num, file_id, ext in items:
            name = f"{post['id']} {post.get('text', '')[:20]}".strip()
            name = f"{post['id']}_{num:02d} {file_id}" if num else name
            out.append((f"{name}.{ext}", {
                "category": "tiktok", "subcategory": "post", "id": post["id"], "num": num, "file_id": file_id,
                "date": date, "createTime": post["ts"], "desc": post.get("text", ""),
                "author": {"id": str(user["id"]), "uniqueId": user["name"], "nickname": user.get("nick")},
                "post_type": "image" if n else "video", "filename": file_id, "extension": ext}))
    return out


def _gallery_dl_key(d):
    if d["category"] == "twitter":
        return f"twitter{d['tweet_id']}_{d['retweet_id']}_{d['num']}"
    return f"tiktok{d['id']}_{d['num']}_{d['file_id']}"


def gallery_dl_main(argv):
    if argv == ["--version"]:
        print("1.32.14")
        return 0
    _log("gallery-dl", argv)
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--write-metadata", action="store_true")
    ap.add_argument("--config-ignore", action="store_true")
    ap.add_argument("-s", "--simulate", action="store_true")
    ap.add_argument("--download-archive")
    ap.add_argument("-o", action="append", default=[])
    ap.add_argument("-D")
    ap.add_argument("--cookies-from-browser")
    ap.add_argument("urls", nargs="*")
    args = ap.parse_args(argv)
    options = dict(o.split("=", 1) for o in args.o)
    abort = int(options["skip"].split(":")[1]) if options.get("skip", "").startswith("abort:") else None
    data = _data()
    status = 0
    for url in args.urls:
        account = data["accounts"].get(url)
        if args.simulate and not data.get("fail"):
            print(f"# {url.rsplit('/', 1)[-1]}_1.jpg")      # the test item: nothing written
            continue
        if data.get("fail") or account is None:
            exc, msg, code = GALLERY_DL_FAIL[data.get("fail") or "notfound"]
            name = (account or {}).get("user", {}).get("name", "someone")
            print(f"[twitter][error] {exc}: {msg.format(name=name)}", file=sys.stderr)
            status |= code
            continue
        folder = args.D or os.path.join("gallery-dl", account["category"], account["user"]["name"])
        os.makedirs(folder, exist_ok=True)
        archive = None
        if args.download_archive:
            os.makedirs(os.path.dirname(args.download_archive), exist_ok=True)
            archive = sqlite3.connect(args.download_archive)
            archive.execute("CREATE TABLE IF NOT EXISTS archive (entry TEXT PRIMARY KEY) WITHOUT ROWID")
        skipped = 0
        try:
            for post in sorted(account["posts"], key=lambda p: -p["ts"]):
                for name, d in _gallery_dl_files(account, post):
                    path = os.path.join(folder, name)
                    key = _gallery_dl_key(d)
                    if (archive and archive.execute("SELECT 1 FROM archive WHERE entry = ?", (key,)).fetchone()) \
                            or os.path.exists(path):
                        print(f"# {path}")
                        skipped += 1
                        if abort is not None and skipped >= abort:
                            raise StopIteration
                        continue
                    skipped = 0
                    (mp4 if d["extension"] == "mp4" else lambda p: png(p, colour(name)))(path)
                    if args.write_metadata:
                        with open(path + ".json", "w", encoding="utf-8") as f:
                            json.dump(d, f, indent=4)
                    if archive:
                        archive.execute("INSERT OR IGNORE INTO archive(entry) VALUES (?)", (key,))
                        archive.commit()
                    print(path)
        except StopIteration:
            pass
        finally:
            if archive:
                archive.close()
    return status


# --- yt-dlp ---------------------------------------------------------------------

YT_DLP_FAIL = {
    "429": "ERROR: [{ie}] {id}: HTTP Error 429: Too Many Requests",
    "login": "ERROR: [{ie}] {id}: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies "
             "for the authentication",
    "private": "ERROR: [{ie}] {id}: This account is private",
    "notfound": "ERROR: [{ie}] {id}: Unable to find user",
}
THUMB = {"TikTok": "image", "Youtube": "webp"}
FAKE_COOKIE = "sessionid=FAKE-SECRET"


def _formats(video_id, cookies):
    """Two formats as yt-dlp lists them; with the cookies it used when it had some."""
    out = []
    for fid in ("h264_540p", "h264_720p"):
        f = {"format_id": fid, "url": f"https://v16.example.invalid/{video_id}/{fid}.mp4", "ext": "mp4",
             "http_headers": {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}}
        if cookies:
            f["cookies"] = f"{FAKE_COOKIE}; Domain=.tiktok.com; Path=/; Secure"
            f["http_headers"]["Cookie"] = FAKE_COOKIE
        out.append(f)
    return out


def _chosen(video_id, cookies):
    """The formats, and the chosen one's fields copied to the top, as yt-dlp does."""
    formats = _formats(video_id, cookies)
    return {"formats": formats, **{k: v for k, v in formats[-1].items() if k in ("format_id", "url", "http_headers",
                                                                               "cookies")}}


def _fill(template, fields):
    return re.sub(r"%%|%\((\w+)\)s", lambda m: "%" if m.group(0) == "%%" else str(fields.get(m.group(1), "NA")),
                  template)


def yt_dlp_main(argv):
    if argv == ["--version"]:
        print("2026.08.19")
        return 0
    _log("yt-dlp", argv)
    ap = argparse.ArgumentParser(add_help=False)
    for flag in ("--write-info-json", "--write-thumbnail", "--break-on-existing", "--ignore-config",
                 "--simulate", "--no-playlist"):
        ap.add_argument(flag, action="store_true")
    ap.add_argument("--download-archive")
    ap.add_argument("-o", default="%(title)s [%(id)s].%(ext)s")
    ap.add_argument("--match-filters")
    ap.add_argument("--cookies-from-browser")
    ap.add_argument("urls", nargs="*")
    args = ap.parse_args(argv)
    longest = None
    if args.match_filters:
        m = re.fullmatch(r"duration <= (\d+)", args.match_filters)
        longest = int(m.group(1))
    data = _data()
    status = 0
    for url in args.urls:
        account = data["accounts"].get(url)
        if args.simulate and not data.get("fail"):
            vid = url.rsplit("=", 1)[-1]
            print(f"[youtube] Extracting URL: {url}\n[youtube] {vid}: Downloading webpage\n"
                  f"[info] {vid}: Downloading 1 format(s): 18")
            continue
        if data.get("fail") or account is None:
            # A profile's errors come from its list extractor, as yt-dlp's do.
            ie = {"TikTok": "tiktok:user", "Youtube": "youtube:tab"}.get(
                (account or {}).get("extractor_key"), "generic")
            print(YT_DLP_FAIL[data.get("fail") or "notfound"].format(ie=ie, id=url.rsplit("/", 1)[-1]),
                  file=sys.stderr)
            status = 1
            continue
        ie = account["extractor_key"]
        have = set()
        if args.download_archive and os.path.exists(args.download_archive):
            with open(args.download_archive, encoding="utf-8") as f:
                have = set(f.read().splitlines())
        base_fields = {k: account.get(k) for k in ("uploader_id", "uploader", "channel", "channel_id")}
        if ie == "Youtube" and args.write_info_json:
            fields = {**base_fields, "id": account["channel_id"], "upload_date": "NA", "ext": "info.json"}
            path = _fill(args.o, fields)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path[:-len(".info.json")] + ".info.json", "w", encoding="utf-8") as f:
                json.dump({"_type": "playlist", "id": account["channel_id"], "extractor": "youtube:tab",
                           "extractor_key": "YoutubeTab", "webpage_url": url, "title": f"{account['uploader']} - Videos",
                           **base_fields}, f, indent=1)
        pinned = account.get("pinned") or []
        for v in sorted(account["videos"], key=lambda v: (v["id"] not in pinned, -v["ts"])):
            line = f"{ie.lower()} {v['id']}"
            if line in have:
                print(f"[download] {v['id']}: has already been recorded in the archive")
                if args.break_on_existing:
                    print("[info] Encountered a video that is already in the archive, stopping due to "
                          "--break-on-existing")
                    return 101
                continue
            if longest is not None and v.get("duration", 0) > longest:
                print(f"[download] {v.get('title')} does not pass filter (duration <= {longest}), skipping ..")
                continue
            day = datetime.fromtimestamp(v["ts"], timezone.utc).strftime("%Y%m%d")
            fields = {**base_fields, "id": v["id"], "upload_date": day, "ext": "mp4"}
            path = _fill(args.o, fields)
            base = path[:-len(".mp4")]
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            mp4(path)
            if args.write_thumbnail:
                png(f"{base}.{THUMB.get(ie, 'jpg')}", colour(v["id"]))
            if args.write_info_json:
                web = (f"https://www.tiktok.com/@{account['uploader']}/video/{v['id']}" if ie == "TikTok"
                       else f"https://www.youtube.com/watch?v={v['id']}")
                with open(base + ".info.json", "w", encoding="utf-8") as f:
                    json.dump({"_type": "video", "id": v["id"], "extractor": ie.lower(), "extractor_key": ie,
                               "webpage_url": web, "title": v.get("title", ""), "description": v.get("description", ""),
                               "timestamp": v["ts"], "upload_date": day, "duration": v.get("duration"),
                               "uploader_url": account.get("uploader_url"), "like_count": 5, "view_count": 50,
                               "comment_count": 2, "ext": "mp4", "_version": {"version": "2026.08.19"},
                               **_chosen(v["id"], args.cookies_from_browser or data.get("config_cookies")),
                               **base_fields}, f, indent=1)
            if args.download_archive:
                with open(args.download_archive, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            print(f"[download] Destination: {path}")
    return status


if __name__ == "__main__":
    tool = os.path.basename(sys.argv[0])
    sys.exit((yt_dlp_main if tool.startswith("yt-dlp") else gallery_dl_main)(sys.argv[1:]))
