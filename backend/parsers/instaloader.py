"""instaloader output.

instaloader names every file of a post after one base name (default
``{date_utc}_UTC``) in a folder per target (default the profile name):

    2024-06-01_12-00-00_UTC.json[.xz]   metadata, {"node": {...}, "instaloader": {...}}
    2024-06-01_12-00-00_UTC.jpg         single image, or a video's thumbnail
    2024-06-01_12-00-00_UTC.mp4         single video
    2024-06-01_12-00-00_UTC_1.jpg       carousel item 1 (a video item also gets _1.mp4)
    2024-06-01_12-00-00_UTC.txt         caption
    2024-06-01_12-00-00_UTC_comments.json, _location.txt

The node is Instagram's own JSON as instaloader received it, so field names
vary with the endpoint it came from. Every lookup below tolerates both the
GraphQL shape and the iPhone API shape, and falls back to None.
"""
import json
import lzma
import os
import re

from . import AccountFile, DirResult, Media, ParsedPost, Profile, ext_of, is_media, IMAGE_EXT, VIDEO_EXT

TOOL = "instaloader"

_POST_TYPES = {"Post", "StoryItem"}
_SIDE_SUFFIXES = (".txt", "_location.txt", "_comments.json")
_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)


def _meta_base(name):
    if name.endswith(".json.xz"):
        return name[:-8]
    if name.endswith(".json") and not name.endswith("_comments.json"):
        return name[:-5]
    return None


def _load(path):
    if path.endswith(".xz"):
        with lzma.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dig(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d:
            d = d[k]
        elif isinstance(d, list) and isinstance(k, int) and -len(d) <= k < len(d):
            d = d[k]
        else:
            return None
    return d


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _caption(node):
    text = _dig(node, "edge_media_to_caption", "edges", 0, "node", "text")
    if text is None:
        cap = node.get("caption")
        text = cap.get("text") if isinstance(cap, dict) else cap
    if text is None:
        text = _dig(node, "iphone_struct", "caption", "text")
    return text if isinstance(text, str) else ""


def _typename(node):
    t = node.get("__typename") or ""
    return t[3:] if t.startswith("XDTGraph") else t


def _media_index(names):
    """{base: [(slot, name)]} of a folder's media files, under every base
    whose ``base.ext`` or ``base_N.ext`` they are (ext ASCII letters and
    digits, N decimal digits; a name can be both). Built once per folder:
    matching each post's pattern against every name was quadratic in the
    folder's size, most of a rescan's time on a large archive."""
    out = {}
    for n in names:
        stem, dot, ext = n.rpartition(".")
        if not dot or not ext or not (ext.isascii() and ext.isalnum()) or not is_media(n):
            continue
        out.setdefault(stem, []).append((1, n))
        head, under, num = stem.rpartition("_")
        if under and num.isdecimal():          # what \d matches
            out.setdefault(head, []).append((int(num), n))
    return out


def _media_for(dirpath, base, names, index=None):
    """Media files named after ``base``, grouped per carousel slot.

    A slot with an .mp4 is a video, and a .jpg beside it is its thumbnail.
    ``index``: _media_index(names), when the caller has it.
    """
    index = _media_index(names) if index is None else index
    slots = {}
    claimed = set()
    for idx, n in index.get(base, ()):
        slots.setdefault(idx, []).append(n)
        claimed.add(n)
    media = []
    for idx in sorted(slots):
        files = slots[idx]
        videos = sorted(f for f in files if ext_of(f) in VIDEO_EXT)
        images = sorted(f for f in files if ext_of(f) in IMAGE_EXT)
        if videos:
            poster = os.path.join(dirpath, images[0]) if images else None
            media.append(Media(idx, "video", os.path.join(dirpath, videos[0]), poster))
        elif images:
            media.append(Media(idx, "image", os.path.join(dirpath, images[0])))
    return media, claimed


def _post_from(node, node_type, version, meta_path, media):
    owner = node.get("owner") or {}
    iphone = node.get("iphone_struct") or {}
    author_id = _first(owner.get("id"), owner.get("pk"), _dig(iphone, "user", "pk"))
    handle = _first(owner.get("username"), _dig(iphone, "user", "username"))
    name = _first(owner.get("full_name"), _dig(iphone, "user", "full_name")) or None
    text = _caption(node)
    posted = _int(_first(node.get("date"), node.get("taken_at_timestamp"), iphone.get("taken_at")))
    typename = _typename(node)

    if node_type == "StoryItem":
        post_id = str(node.get("id") or "")
        url = f"https://www.instagram.com/stories/{handle}/{post_id}/" if handle else None
        kind = "story"
    else:
        post_id = _first(node.get("shortcode"), node.get("code"))
        reel = node.get("product_type") == "clips"
        url = f"https://www.instagram.com/{'reel' if reel else 'p'}/{post_id}/" if post_id else None
        kind = {"GraphSidecar": "carousel", "GraphVideo": "video", "GraphImage": "image"}.get(typename)
        if kind is None:
            kind = "carousel" if len(media) > 1 else (media[0].kind if media else "text")
    if not post_id:
        return None

    return ParsedPost(
        platform="instagram",
        post_id=str(post_id),
        url=url,
        kind=kind,
        author_id=str(author_id) if author_id is not None else None,
        author_handle=handle,
        author_name=name,
        posted_at=posted,
        text=text,
        meta_path=meta_path,
        tool=TOOL,
        tool_version=version,
        likes=_int(_first(_dig(node, "edge_media_preview_like", "count"),
                          _dig(node, "edge_liked_by", "count"),
                          iphone.get("like_count"))),
        comments=_int(_first(_dig(node, "edge_media_to_comment", "count"),
                             _dig(node, "edge_media_to_parent_comment", "count"),
                             iphone.get("comment_count"))),
        views=_int(_first(node.get("video_view_count"), node.get("video_play_count"),
                          iphone.get("play_count"), iphone.get("view_count"))),
        location=_dig(node, "location", "name"),
        hashtags=list(dict.fromkeys(h.lower() for h in _HASHTAG_RE.findall(text))),
        media=media,
    )


def _profile(node, path):
    """What a Profile file (``<handle>_<id>.json.xz``, written with the
    profile's posts) says for link suggestions: its bio and links."""
    if not isinstance(node, dict) or node.get("id") is None:
        return None
    urls = [node.get("external_url")]
    urls += [b.get("url") for b in node.get("bio_links") or () if isinstance(b, dict)]
    try:
        at = int(os.path.getmtime(path))
    except OSError:
        at = None
    bio = node.get("biography")
    return Profile("instagram", str(node["id"]), node.get("username"), bio if isinstance(bio, str) else "",
                   list(dict.fromkeys(u for u in urls if isinstance(u, str) and u)), at, path)


# --- the id file ---------------------------------------------------------------
#
# instaloader keeps the profile's numeric id in ``<profile>/id`` (or
# ``<profile>_id`` when the folder pattern has no profile in it), written
# with the profile's first download, and renames the folder when the profile
# changes its name. With --latest-stamps it keeps them in that file instead.

_ID_MAX = 32                    # bytes: an id and a newline


def _account_files(dirpath, names):
    """AccountFile for each id file among ``names`` that holds a numeric id."""
    out = []
    for n in names:
        if n == "id":
            handle = os.path.basename(dirpath)
        elif n.endswith("_id") and _HANDLE_RE.fullmatch(n[:-3]):
            handle = n[:-3]
        else:
            continue
        path = os.path.join(dirpath, n)
        try:
            if os.path.getsize(path) > _ID_MAX:
                continue
            with open(path, "rb") as f:
                text = f.read(_ID_MAX).decode("ascii").strip()
            at = int(os.path.getmtime(path))
        except (OSError, UnicodeDecodeError):
            continue
        if text.isdigit() and len(text) <= 20 and _HANDLE_RE.fullmatch(handle):
            out.append(AccountFile("instagram", str(int(text)), handle.lower(), path, at))
    return out


# --- filename-only posts ----------------------------------------------------
#
# With save_metadata=False instaloader writes media only, so everything has to
# come from the name. The common custom pattern is
#     {target}-{date:%Y-%m-%d}-{shortcode}[_N].ext
# where {target} is the profile name, or the highlight title for highlights.
# instaloader sets each file's mtime to the post time, which restores the
# exact timestamp the name rounds to a day.

_NAME_RE = re.compile(r"(?P<target>.+?)-(?P<date>\d{4}-\d{2}-\d{2})-(?P<stem>[A-Za-z0-9_-]+)\.(?P<ext>[A-Za-z0-9]+)")
# An older layout without the date: "{profile} - {shortcode}[ - N].ext".
_SPACED_RE = re.compile(r"(?P<target>.+?) - (?P<code>[A-Za-z0-9_-]{8,39})(?: - (?P<idx>\d+))?\.(?P<ext>[A-Za-z0-9]+)")
_SLOT_RE = re.compile(r"(?P<code>.+)_(?P<idx>\d+)")
_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
# Instaloader's own side files: profile pictures and highlight covers.
_SIDE_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC_(?:profile_pic|cover)\.(?:jpg|jpeg|png|webp)")
_SHORTCODE_LENGTHS = {11, 39}   # posts; long ones are private-account shortcodes


def _split_stem(stem, stem_counts):
    """(shortcode, slot) for a name stem like ``B_QcFdCp9iM_2``.

    Shortcodes may themselves end in ``_<digits>`` (``Bm3Lr49F_10``), so the
    suffix only counts as a carousel slot when what is left looks like a whole
    shortcode, or when several files share that prefix.
    """
    m = _SLOT_RE.fullmatch(stem)
    if m and (len(m["code"]) in _SHORTCODE_LENGTHS or stem_counts.get(m["code"], 0) > 1):
        return m["code"], int(m["idx"])
    return stem, 1


def _day_start(date):
    from datetime import datetime, timezone
    try:
        return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _posted_at(date, path):
    """The file mtime when it falls on the named day (±1 for time zones).

    With no date in the name, the mtime is all there is.
    """
    day = _day_start(date) if date else None
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        return day
    if day is None or abs(mtime - (day + 43200)) <= 36 * 3600:
        return mtime
    return day


def _filename_posts(root, dirpath, names):
    rel = os.path.relpath(dirpath, root)
    parts = [] if rel == "." else rel.split(os.sep)
    top = parts[0] if parts else None

    dated, spaced = [], []
    for n in names:
        if not is_media(n):
            continue
        m = _NAME_RE.fullmatch(n)
        if m:
            dated.append((n, m))
            continue
        m = _SPACED_RE.fullmatch(n)
        if m:
            spaced.append((n, m))
    prefix_counts = {}                         # target -> {code prefix: files}
    for _, m in dated:
        s = _SLOT_RE.fullmatch(m["stem"])
        if s:
            counts = prefix_counts.setdefault(m["target"], {})
            counts[s["code"]] = counts.get(s["code"], 0) + 1

    groups = {}
    for n, m in dated:
        code, idx = _split_stem(m["stem"], prefix_counts.get(m["target"], {}))
        g = groups.setdefault(code, {"target": m["target"], "date": m["date"], "slots": {}})
        g["slots"].setdefault(idx, []).append(n)
    for n, m in spaced:
        idx = int(m["idx"]) if m["idx"] else 1
        g = groups.setdefault(m["code"], {"target": m["target"], "date": None, "slots": {}})
        g["slots"].setdefault(idx, []).append(n)

    posts, claimed = [], set()
    for code, g in sorted(groups.items()):
        target = g["target"]
        # Highlights: a subfolder of a profile folder, or a target that cannot
        # be a handle ("More food", "#BARBIE"). The profile is the top folder.
        if len(parts) >= 2 or not _HANDLE_RE.fullmatch(target):
            album, handle = target, top
        else:
            album, handle = None, target
        media = []
        for idx in sorted(g["slots"]):
            files = sorted(g["slots"][idx])
            claimed.update(files)
            videos = [f for f in files if ext_of(f) in VIDEO_EXT]
            images = [f for f in files if ext_of(f) in IMAGE_EXT]
            if videos:
                poster = os.path.join(dirpath, images[0]) if images else None
                media.append(Media(idx, "video", os.path.join(dirpath, videos[0]), poster))
            elif images:
                media.append(Media(idx, "image", os.path.join(dirpath, images[0])))
        if album:
            kind = "story"
        elif len(media) > 1:
            kind = "carousel"
        else:
            kind = media[0].kind
        first = media[0].path
        posts.append(ParsedPost(
            platform="instagram",
            post_id=code,
            # Highlight items have no public URL of their own.
            url=None if album else f"https://www.instagram.com/p/{code}/",
            kind=kind,
            # The folder is the stable identity: a handle rename keeps writing
            # into the same folder under the new name.
            author_id=(top or handle or "").lower() or None,
            author_handle=handle,
            author_name=None,
            posted_at=_posted_at(g["date"], first),
            text="",
            meta_path=first,
            tool=TOOL + " (filenames)",
            album=album,
            media=media,
        ))
    return posts, claimed


def parse_dir(root, dirpath, names):
    result = DirResult()
    names_set = set(names)
    seen_instaloader = False
    index = None                               # _media_index(names), once a post needs it

    for n in sorted(names):
        base = _meta_base(n)
        if base is None:
            continue
        path = os.path.join(dirpath, n)
        try:
            data = _load(path)
        except (OSError, ValueError, lzma.LZMAError, EOFError) as e:
            # Only complain about files that are plausibly ours: a date-named
            # JSON next to media with the same base name.
            if any(m.startswith(base) and is_media(m) for m in names_set):
                result.errors.append((path, f"unreadable metadata: {e}"))
                result.claimed.add(n)
            continue
        info = data.get("instaloader") if isinstance(data, dict) else None
        if not isinstance(info, dict) or "node" not in data:
            continue
        seen_instaloader = True
        result.claimed.add(n)
        node_type = info.get("node_type")
        if node_type == "Profile":
            profile = _profile(data["node"], path)
            if profile:
                result.profiles.append(profile)
        if node_type not in _POST_TYPES:
            continue                           # Profile, Hashtag, iterator state
        node = data["node"] if isinstance(data["node"], dict) else {}
        if index is None:
            index = _media_index(names)
        media, claimed = _media_for(dirpath, base, names, index)
        post = _post_from(node, node_type, info.get("version"), path, media)
        if post is None:
            result.errors.append((path, "no post id in metadata"))
            continue
        if not media:
            # Metadata alone: instaloader fetched none of its files (a sync
            # of videos only, --no-pictures, leaves an image post so).
            result.claimed |= {base + s for s in _SIDE_SUFFIXES if base + s in names_set}
            continue
        result.posts.append(post)
        result.claimed |= claimed
        result.claimed |= {base + s for s in _SIDE_SUFFIXES if base + s in names_set}

    result.account_files = _account_files(dirpath, names)
    result.claimed |= {os.path.basename(a.path) for a in result.account_files}
    if seen_instaloader:
        # Profile folders also hold an "id" file.
        result.claimed |= {n for n in names if n == "id"}
    result.claimed |= {n for n in names if _SIDE_RE.fullmatch(n)}
    return result


def parse_filenames(root, dirpath, names):
    """Posts rebuilt from file names, for the files no parser with metadata
    claimed: it runs last (see parsers.PARSERS), so a gallery-dl file whose
    name happens to fit the pattern stays gallery-dl's."""
    result = DirResult()
    result.posts, result.claimed = _filename_posts(root, dirpath, names)
    return result
