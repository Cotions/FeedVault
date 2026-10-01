"""yt-dlp output.

With ``--write-info-json --write-thumbnail`` yt-dlp writes, per video, files
that share one base name (the output template without its extension):

    someone-20240601-7400000000000000000.mp4         the video (or .webm, .mkv, .m4a…)
    someone-20240601-7400000000000000000.info.json   its metadata
    someone-20240601-7400000000000000000.webp        the thumbnail (.jpg, .webp; TikTok's is .image)
    someone-20240601-7400000000000000000.en.vtt      subtitles, .description, .live_chat.json

The info JSON is recognized by its keys (``extractor_key``, ``id``,
``webpage_url``), never by folder name. A playlist or channel download also
writes the playlist's own info JSON (``_type: playlist``) and thumbnail: they
are claimed and are not a post.

Long YouTube videos belong to ChannelVault: one longer than the config's
``youtube_max_seconds`` (default 180) is claimed and listed on the Unmatched
page with that reason, never indexed.
"""
import bisect
import json
import os
import re
from datetime import datetime, timezone

from . import DirResult, Media, ParsedPost, ext_of, IMAGE_EXT, VIDEO_EXT

TOOL = "yt-dlp"
INFO = ".info.json"
YOUTUBE_MAX_DEFAULT = 180

AUDIO_EXT = {"m4a", "mp3", "opus", "ogg", "oga", "wav", "flac", "aac"}
# yt-dlp keeps a thumbnail's extension from its URL: TikTok's end in ".image".
THUMB_EXT = IMAGE_EXT | {"image"}

_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)


def youtube_max_seconds(cfg=None):
    """The longest YouTube video FeedVault indexes, from the config."""
    if cfg is None:
        import config
        cfg = config.load()
    v = cfg.get("youtube_max_seconds")
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else YOUTUBE_MAX_DEFAULT


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _is_ours(d):
    return (isinstance(d, dict) and isinstance(d.get("extractor_key"), str) and d["extractor_key"]
            and isinstance(d.get("id"), str) and d["id"] and isinstance(d.get("webpage_url"), str))


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _str(v):
    return v if isinstance(v, str) and v else None


def _posted_at(d):
    ts = d.get("timestamp")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return int(ts)
    day = d.get("upload_date")
    if isinstance(day, str):
        try:
            return int(datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return None


def _handle(d):
    """The account's handle: the last part of its profile URL (``@name``),
    else an uploader id that is not a number (TikTok's are)."""
    url = _str(d.get("uploader_url"))
    if url:
        last = url.rstrip("/").rsplit("/", 1)[-1]
        if last.startswith("@") and len(last) > 1:
            return last[1:]
    uid = _str(d.get("uploader_id"))
    if uid and not uid.isdigit():
        return uid.lstrip("@") or None
    return _str(d.get("uploader")) if uid else None


def _author_id(platform, d):
    # A YouTube uploader id is the @handle, which can change; the channel id cannot.
    keys = ("channel_id", "uploader_id") if platform == "youtube" else ("uploader_id", "channel_id")
    return next((d[k] for k in keys if _str(d.get(k))), None)


def _text(d):
    """The description, with the title in front when it says something else
    (not when it is the description cut short, as TikTok's are)."""
    desc = _str(d.get("description")) or ""
    title = _str(d.get("title")) or ""
    cut = title.rstrip(".…").rstrip()
    if not title or (cut and desc.startswith(cut)) or title == d.get("id"):
        return desc
    return f"{title}\n\n{desc}" if desc else title


def _named(base, ordered):
    """The names in ``ordered`` (sorted) that start with ``<base>.``: found by
    bisection, so a folder of thousands of videos is not walked per video."""
    prefix = base + "."
    i = bisect.bisect_left(ordered, prefix)
    while i < len(ordered) and ordered[i].startswith(prefix):
        yield ordered[i]
        i += 1


def _files_of(base, ordered):
    """(media, thumbnails, side files) named ``<base>.<something>``."""
    media, thumbs, sides = [], [], []
    for n in _named(base, ordered):
        if n == base + INFO:
            continue
        rest = n[len(base) + 1:]
        ext = ext_of(n)
        if "." not in rest and ext in VIDEO_EXT | AUDIO_EXT:
            media.append(n)
        elif "." not in rest and ext in THUMB_EXT:
            thumbs.append(n)
        else:
            sides.append(n)                    # subtitles (<base>.en.vtt), .description, .live_chat.json
    return sorted(media), sorted(thumbs), sorted(sides)


def _post(d, dirpath, base, media_files, thumbs, sides):
    platform = d["extractor_key"].lower()
    poster = os.path.join(dirpath, thumbs[0]) if thumbs else None
    if media_files:
        media = [Media(1, "video", os.path.join(dirpath, media_files[0]), poster)]
        kind = "video"
    elif poster and ext_of(poster) in IMAGE_EXT:
        media, kind = [Media(1, "image", poster)], "image"    # the video is not there: its picture is
    else:
        media, kind = [], "text"
    others = media_files[1:] + thumbs[1:] + sides
    if poster and not media:
        others.append(thumbs[0])               # a picture no browser shows by its name (.image)
    text = _text(d)
    version = d.get("_version")
    return ParsedPost(
        platform=platform,
        post_id=d["id"],
        url=d["webpage_url"],
        kind=kind,
        author_id=_author_id(platform, d),
        author_handle=_handle(d),
        author_name=_str(d.get("channel")) or _str(d.get("uploader")),
        posted_at=_posted_at(d),
        text=text,
        meta_path=os.path.join(dirpath, base + INFO),
        tool=TOOL,
        tool_version=_str(version.get("version")) if isinstance(version, dict) else None,
        likes=_int(d.get("like_count")),
        comments=_int(d.get("comment_count")),
        views=_int(d.get("view_count")),
        hashtags=list(dict.fromkeys(t.lower() for t in _HASHTAG_RE.findall(text))),
        media=media,
        side_files=[os.path.join(dirpath, n) for n in others],
    )


def parse_dir(root, dirpath, names):
    result = DirResult()
    max_seconds = None                         # read from the config once a YouTube video turns up

    ordered = sorted(names)
    for n in ordered:
        if not n.endswith(INFO):
            continue
        path = os.path.join(dirpath, n)
        base = n[:-len(INFO)]
        try:
            d = _load(path)
        except (OSError, ValueError) as e:
            # Only ours when something beside it shares its name.
            if any(m != n for m in _named(base, ordered)):
                result.errors.append((path, f"unreadable metadata: {e}"))
                result.claimed.add(n)
            continue
        if not _is_ours(d):
            continue
        media_files, thumbs, sides = _files_of(base, ordered)
        mine = {n, *media_files, *thumbs, *sides}
        result.claimed |= mine
        if d.get("_type") == "playlist":
            continue                           # a playlist's or channel's own JSON and picture
        if d["extractor_key"].lower() == "youtube":
            if max_seconds is None:
                max_seconds = youtube_max_seconds()
            duration = d.get("duration")
            if isinstance(duration, (int, float)) and duration > max_seconds:
                shown = os.path.join(dirpath, media_files[0]) if media_files else path
                result.skipped.append((shown, f"YouTube video longer than {_minutes(max_seconds)}: "
                                              "left to ChannelVault"))
                continue
        result.posts.append(_post(d, dirpath, base, media_files, thumbs, sides))
    return result


def _minutes(seconds):
    if seconds % 60:
        return f"{seconds} s"
    return f"{seconds // 60} min"
