"""gallery-dl output.

gallery-dl writes one metadata JSON per downloaded file, not per post. With
``--write-metadata`` it sits next to the file under the file's full name, and
the ``metadata`` postprocessor with ``"extension-format": "json"`` replaces the
file's extension instead:

    twitter/someone/1790000000000000000_1.jpg        a tweet's first photo
    twitter/someone/1790000000000000000_1.jpg.json   its metadata (default)
    twitter/someone/1790000000000000000_2.json       or this (extension-format)
    twitter/someone/1790000000000000001.json         a post-level JSON ("event": "post"),
                                                     the only trace of a text-only tweet
    tiktok/someone/7400000000000000000 title.mp4[.json]
    tiktok/someone/7400000000000000000 title [cover].jpg[.json]
    tiktok/someone/7400000000000000000_01 title [file id].jpg[.json]   slideshow photo
    tiktok/someone/7400000000000000000 title [music id].mp3[.json]     slideshow music

Every JSON carries the extractor's ``category`` and ``subcategory``, a file JSON
also ``filename`` and ``extension``; that is how they are recognized, never by
folder name. The JSONs of one post share the extractor's post id key
(``tweet_id``, ``id``, ...) and are ordered by ``num``.

Field names differ per extractor, so each site is one entry in ``SITES``.
Categories without an entry still become posts from the generic keys when an id
and a date are there; otherwise their files stay unclaimed (Unmatched).
"""
import json
import os
import re
from datetime import datetime, timezone

from . import DirResult, Media, ParsedPost, ext_of, IMAGE_EXT, VIDEO_EXT

TOOL = "gallery-dl"

_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)


def _dig(d, path):
    """``d[a][b]...`` for a key or a tuple of keys, None when anything is missing."""
    for k in (path if isinstance(path, tuple) else (path,)):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _first(d, paths):
    """The first plain value (not a dict or list) found along ``paths``."""
    for p in paths:
        v = _dig(d, p)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            return v
    return None


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _str(v):
    return None if v in (None, "") else str(v)


def _ts(v):
    """gallery-dl writes dates as "YYYY-MM-DD HH:MM:SS" in UTC."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if not isinstance(v, str) or not v:
        return None
    if v.isdigit():
        return int(v)
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


# --- per-site table ---------------------------------------------------------

def _twitter_url(post, d):
    return f"https://x.com/{post.author_handle or 'i'}/status/{post.post_id}"


def _twitter_fixup(post, d):
    """Retweets and quotes, as gallery-dl's twitter extractor marks them.

    A retweet is saved under the retweet's own id in the retweeter's folder,
    with ``retweet_id`` the original and ``author`` its author. It becomes the
    original post (so it meets a direct download of it as a copy), with a note
    of who retweeted it. A quoted tweet, saved with ``quoted=true``, carries
    the id and handle of the tweet that quoted it.
    """
    rt = _int(d.get("retweet_id"))
    if rt:
        post.post_id = str(rt)
        post.posted_at = _ts(d.get("date_original")) or post.posted_at
        prefix = f"RT @{post.author_handle}: "
        if post.text.startswith(prefix):
            post.text = post.text[len(prefix):]
        by = _dig(d, ("user", "name"))
        if by and by != post.author_handle:
            post.album = f"Retweeted by @{by}"
    elif _int(d.get("quote_id")) and d.get("quote_by"):
        post.album = f"Quoted by @{d['quote_by']}"
    if not post.views:                         # 0 when the API did not say
        post.views = None


def _tiktok_url(post, d):
    kind = "photo" if d.get("post_type") == "image" else "video"
    return f"https://www.tiktok.com/@{post.author_handle}/{kind}/{post.post_id}"


def _tiktok_hashtags(d):
    tags = [t.get("hashtagName") for t in d.get("textExtra") or () if isinstance(t, dict)]
    tags += [c.get("title") for c in d.get("challenges") or () if isinstance(c, dict)]
    return [t for t in tags if isinstance(t, str) and t]


# One entry per gallery-dl category. Values are a key, a tuple of keys into
# nested dicts, or a list of those to try in order.
#   post_id      the key all files of one post share
#   author_*     where the author's id, handle and display name are
#   text, date   caption and posting time
#   likes/comments/views   counters, when the site reports them
#   hashtags     a key holding a list of tags, or a function of the JSON;
#                tags found in the text are always added
#   url          function (post, json) -> public URL of the post
#   posters      file "type" values that are a video's thumbnail
#   sides        file "type" values the post owns but are not media (music,
#                subtitles): claimed, never shown, trashed with the post
#   kinds        subcategory -> post kind, e.g. stories
#   fixup        function (post, json) for what a table cannot say
SITES = {
    "twitter": {
        "post_id": "tweet_id",
        "author_id": ("author", "id"), "author_handle": ("author", "name"), "author_name": ("author", "nick"),
        "text": "content", "date": "date",
        "likes": "favorite_count", "comments": "reply_count", "views": "view_count",
        "hashtags": "hashtags",
        "url": _twitter_url,
        "posters": {"preview"},
        "fixup": _twitter_fixup,
    },
    "tiktok": {
        "post_id": "id",
        "author_id": ("author", "id"), "author_handle": [("author", "uniqueId"), "user"],
        "author_name": ("author", "nickname"),
        "text": "desc", "date": ["date", "createTime"],
        "likes": [("stats", "diggCount"), ("statsV2", "diggCount")],
        "comments": [("stats", "commentCount"), ("statsV2", "commentCount")],
        "views": [("stats", "playCount"), ("statsV2", "playCount")],
        "hashtags": _tiktok_hashtags,
        "url": _tiktok_url,
        "posters": {"cover"},
        "sides": {"audio", "subtitle"},
    },
}

# Any other category: the keys most extractors use.
GENERIC = {
    "post_id": ["post_id", "id"],
    "author_id": [("author", "id"), ("user", "id"), ("owner", "id"), "author_id", "user_id"],
    "author_handle": [("author", "name"), ("author", "username"), ("user", "name"), ("user", "username"),
                      ("owner", "username"), "username", "author", "user", "uploader"],
    "author_name": [("author", "nick"), ("author", "display_name"), ("user", "nick"),
                    ("user", "display_name"), ("owner", "full_name"), "nick", "display_name"],
    "text": ["content", "description", "desc", "caption", "text", "title"],
    "date": ["date", "created_at", "timestamp"],
    "hashtags": "hashtags",
    "kinds": {"stories": "story", "story": "story", "highlights": "story"},
}


def _paths(spec):
    if spec is None:
        return []
    return spec if isinstance(spec, list) else [spec]


def _site(category):
    return SITES.get(category, GENERIC)


def _group_key(d, site):
    return _str(_first(d, _paths(site["post_id"])))


# --- reading a directory ----------------------------------------------------

def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _is_ours(d):
    return (isinstance(d, dict) and isinstance(d.get("category"), str)
            and isinstance(d.get("subcategory"), str))


def _file_for(name, d, names_set):
    """The downloaded file a JSON describes, or None for a post-level JSON
    (or a file that is not there)."""
    if "filename" not in d or "extension" not in d:
        return None
    stem = name[:-5]
    if stem in names_set:                      # <file>.<ext>.json
        return stem
    ext = d.get("extension")
    if isinstance(ext, str) and ext and f"{stem}.{ext}" in names_set:
        return f"{stem}.{ext}"                 # <file>.json
    return None


def _num(d):
    n = _int(d.get("num"))
    return n if n is not None else -1          # post-level JSONs sort first


def _post_from(category, entries, dirpath):
    """One post from the (json name, data, file name) entries of one group."""
    site = _site(category)
    entries.sort(key=lambda e: (_num(e[1]), e[0]))
    files = [e for e in entries if "filename" in e[1]]
    posters = site.get("posters", set())
    sides = site.get("sides", set())
    # The first media file's JSON names the post (it stays put when later
    # items are deleted); a text-only post only has its post-level JSON.
    main = [e for e in files if e[1].get("type") not in posters | sides]
    head = (main or files or entries)[0]
    d = head[1]

    media, side_files, pending_posters = [], [], []
    expected = []                              # kinds the JSONs list, present or not
    for name, fd, fname in files:
        ftype = fd.get("type")
        ext = (fd.get("extension") or ext_of(fname or "")).lower()
        if ftype in sides or (ext not in IMAGE_EXT and ext not in VIDEO_EXT):
            if fname:
                side_files.append(os.path.join(dirpath, fname))
            continue
        if ftype in posters and ext in IMAGE_EXT:
            if fname:
                pending_posters.append(os.path.join(dirpath, fname))
            continue
        kind = "video" if ext in VIDEO_EXT else "image"
        expected.append(kind)
        if fname:                              # idx counts missing files too, so it stays put
            media.append(Media(len(expected), kind, os.path.join(dirpath, fname)))
    # A thumbnail goes to a video without one, in order (twitter writes each
    # preview right after its video, tiktok the cover beside the video).
    # Thumbnails with no video to belong to are pictures in their own right.
    for p in pending_posters:
        video = next((m for m in media if m.kind == "video" and m.poster_path is None), None)
        if video:
            video.poster_path = p
        else:
            expected.append("image")
            media.append(Media(len(expected), "image", p))

    pid = _group_key(d, site)
    text = _first(d, _paths(site.get("text")))
    text = text if isinstance(text, str) else ""
    tags = site.get("hashtags")
    tags = tags(d) if callable(tags) else _dig(d, tags) if tags else None
    tags = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    tags += _HASHTAG_RE.findall(text)

    kind = (site.get("kinds") or {}).get(d.get("subcategory"))
    if kind is None:
        kind = "text" if not expected else "carousel" if len(expected) > 1 else expected[0]

    post = ParsedPost(
        platform=category,
        post_id=pid,
        url=None,
        kind=kind,
        author_id=_str(_first(d, _paths(site.get("author_id")))),
        author_handle=_str(_first(d, _paths(site.get("author_handle")))),
        author_name=_str(_first(d, _paths(site.get("author_name")))),
        posted_at=_ts(_first(d, _paths(site.get("date")))),
        text=text,
        meta_path=os.path.join(dirpath, head[0]),
        tool=TOOL,
        likes=_int(_first(d, _paths(site.get("likes")))),
        comments=_int(_first(d, _paths(site.get("comments")))),
        views=_int(_first(d, _paths(site.get("views")))),
        hashtags=list(dict.fromkeys(t.lower() for t in tags)),
        media=media,
    )
    if site.get("fixup"):
        site["fixup"](post, d)
    if site.get("url"):
        post.url = site["url"](post, d)
    post.side_files = [os.path.join(dirpath, e[0]) for e in entries if e is not head] + side_files
    return post


def parse_dir(root, dirpath, names):
    result = DirResult()
    names_set = set(names)
    groups = {}                                # (category, post id) -> [(json, data, file)]

    for n in sorted(names):
        if not n.endswith(".json"):
            continue
        path = os.path.join(dirpath, n)
        try:
            d = _load(path)
        except (OSError, ValueError) as e:
            # Only complain about a JSON named after a file beside it.
            stem = n[:-5]
            if stem in names_set or any(m.rsplit(".", 1)[0] == stem for m in names_set if m != n):
                result.errors.append((path, f"unreadable metadata: {e}"))
                result.claimed.add(n)
            continue
        if not _is_ours(d):
            continue
        category = d["category"]
        site = _site(category)
        pid = _group_key(d, site)
        fname = _file_for(n, d, names_set)
        if pid is None or (category not in SITES and _ts(_first(d, _paths(site["date"]))) is None):
            # gallery-dl's own, but nothing to make a post of: an info.json, or
            # an unknown site without an id and a date. Its file stays unclaimed.
            result.claimed.add(n)
            continue
        groups.setdefault((category, pid), []).append((n, d, fname))

    for (category, _), entries in sorted(groups.items()):
        post = _post_from(category, entries, dirpath)
        result.posts.append(post)
        result.claimed |= {e[0] for e in entries} | {e[2] for e in entries if e[2]}
    return result
