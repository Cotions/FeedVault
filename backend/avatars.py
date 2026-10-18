"""People's profile pictures (people.py): the user's own, one per person.

Set on the person's page from what is on this machine, never fetched: a
picture uploaded, an image of one of their posts (a video's poster), or a
profile picture instaloader saved beside their posts
(``<date>_UTC_profile_pic.jpg``).

Whatever it came as, it is opened as one of the image formats thumbs.py
reads (never by its name), cropped to a portrait (2:3, as Stash shows a
performer), and saved again as WebP three times: WIDTH x HEIGHT for the
page, CARD wide for the Creators cards, and a SMALL square of the
portrait's upper part (the face, mostly) for the round chips. They live in
``<data_dir>/avatars/``, named by a hash of the picture saved, never next
to the media (the media folders are only ever read). Pictures saved before
portraits (a 512 px square and its 96 px one, no card file) are still
served: the card size falls back to the large one, and every place shows
them covering its box. The person keeps that
name (people.avatar, exported in people.json), so a rebuilt index points
at the same files; a file no person names any more is deleted as the
change that let it go is made.
"""
import hashlib
import io
import math
import os
import re
import stat
import tempfile

from PIL import Image, ImageOps

import config
import db
import thumbs

WIDTH, HEIGHT = 600, 900                # the portrait, 2:3
CARD = 300                              # the cards' portrait, 300 x 450
SMALL = 96                              # the chips' square
TOP_BIAS = 0.25                         # the default crop of a tall picture: a quarter of what is cut off is above
SMALL_TOP = 0.2                         # the chips' square: a fifth of the portrait it leaves out is above it
QUALITY = 86
MAX_UPLOAD = 15 * 1024 * 1024           # bytes
MAX_FILE = 100 * 1024 * 1024            # a picture read from the media folders
MAX_PIXELS = 50_000_000                 # width x height, read from the header before anything is decoded
FOLDER = "avatars"
NAME_RE = re.compile(r"[0-9a-f]{20}")
# What instaloader names a profile's picture (parsers.instaloader._SIDE_RE, covers left out).
PROFILE_PIC_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC_profile_pic\.(?:jpg|jpeg|png|webp)",
                            re.IGNORECASE)
MAX_FOLDERS = 200                       # folders looked in for profile pictures, per person
MAX_PROFILE_PICS = 6                    # offered, newest first
MAX_MEDIA = 60                          # post pictures offered, newest first


class Refused(ValueError):
    """A picture that cannot be used; the message is for the user."""


class TooLarge(Refused):
    """An upload over MAX_UPLOAD."""


def clean_name(value):
    """An avatar's name as stored ('' for none), or None when it cannot be one."""
    if value == "" or isinstance(value, str) and NAME_RE.fullmatch(value):
        return value
    return None


# The files of an avatar: kind -> what follows the name.
KINDS = {"large": "", "card": f"-{CARD}", "small": f"-{SMALL}"}


def path(data_dir, name, kind="large"):
    return os.path.join(data_dir, FOLDER, f"{name}{KINDS[kind]}.webp")


def file_name(value):
    """The avatar name and which of its files (a KINDS key), from a served
    file's name (``<name>.webp``, ``<name>-300.webp`` or ``<name>-96.webp``),
    or None."""
    m = re.fullmatch(rf"([0-9a-f]{{20}})(-{CARD}|-{SMALL})?\.webp", value or "")
    if not m:
        return None
    return m[1], next(k for k, v in KINDS.items() if v == (m[2] or ""))


def clean_crop(value):
    """The portrait to keep, {x, y, size}: its left and top as fractions of
    the picture's width and height, its size as a fraction of the largest
    2:3 box the picture holds (1: that box; 0.5: half as wide and high).
    None (the default) keeps the middle across and the upper part of a tall
    picture (TOP_BIAS). Raises Refused when malformed."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise Refused("crop must be { x, y, size }, fractions of the picture")
    out = {}
    for k in ("x", "y", "size"):
        v = value.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
            raise Refused("crop must be { x, y, size }, fractions of the picture from 0 to 1")
        out[k] = float(v)
    if out["size"] < 0.01:
        raise Refused("crop size must be at least 0.01")
    return out


# ---------------------------------------------------------------------------
# Making one
# ---------------------------------------------------------------------------

def _fit(w, h):
    """The largest 2:3 box a w x h picture holds: (width, height)."""
    return (w, w * 1.5) if w * 1.5 <= h else (h / 1.5, h)


def _portrait(w, h, crop):
    """(left, top, width, height) in pixels of a w x h picture."""
    bw, bh = _fit(w, h)
    k = crop["size"] if crop else 1
    bw, bh = max(1, min(bw * k, w)), max(1, min(bh * k, h))
    if crop is None:
        left, top = (w - bw) / 2, (h - bh) * TOP_BIAS
    else:
        left, top = crop["x"] * w, crop["y"] * h
    left, top = max(0, min(left, w - bw)), max(0, min(top, h - bh))
    return round(left), round(top), max(1, round(bw)), max(1, round(bh))


def _webp(img, width, height):
    out = io.BytesIO()
    img.resize((width, height), Image.LANCZOS).save(out, "WEBP", quality=QUALITY, method=4)
    return out.getvalue()


def _sized(img, width):
    """A portrait as WebP, ``width`` wide at most, 2:3; one narrower as it is
    (never enlarged, its rounded pixels kept)."""
    if img.width <= width:
        return _webp(img, img.width, img.height)
    return _webp(img, width, round(width * 1.5))


def _write(target, data):
    """``data`` at ``target``, atomically, readable by its owner only."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".avatar.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def make(data_dir, src, crop=None):
    """Save the picture in ``src`` (a path or a binary file) as an avatar,
    cropped (clean_crop). Returns its name. Raises Refused when it is not a
    picture Pillow reads as one of thumbs.FORMATS, or one too large to open."""
    try:
        with thumbs.open_image(src) as img:
            w, h = img.size
            if w * h > MAX_PIXELS:
                raise Refused(f"the picture is too large ({w} x {h} pixels; at most {MAX_PIXELS // 1_000_000} "
                              "million)")
            # A JPEG decoded at a fraction of its size when that is still
            # enough: the portrait WIDTH wide, whichever way EXIF turns it.
            box = min(_fit(w, h)[0], _fit(h, w)[0]) * (crop["size"] if crop else 1)
            scale = min(1, WIDTH / max(1, box))
            img.draft("RGB", (math.ceil(w * scale), math.ceil(h * scale)))
            img = ImageOps.exif_transpose(img)  # the first frame of an animation
            alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            img = img.convert("RGBA" if alpha else "RGB")
    except Refused:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Refused(f"the picture is too large (at most {MAX_PIXELS // 1_000_000} million pixels)")
    except (OSError, ValueError, SyntaxError):
        raise Refused("that is not a picture FeedVault can read (JPEG, PNG, WebP, GIF or AVIF)")
    left, top, bw, bh = _portrait(img.width, img.height, crop)
    img = img.crop((left, top, left + bw, top + bh))
    big, card = _sized(img, WIDTH), _sized(img, CARD)
    # The chips' square: as wide as the portrait, from its upper part.
    side = min(img.width, img.height)
    up = round((img.height - side) * SMALL_TOP)
    small = _webp(img.crop((0, up, side, up + side)), min(SMALL, side), min(SMALL, side))
    name = hashlib.sha256(big).hexdigest()[:20]
    config.make_private_dir(os.path.join(data_dir, FOLDER))
    for kind, data in (("large", big), ("card", card), ("small", small)):
        target = path(data_dir, name, kind)
        if not os.path.isfile(target):
            _write(target, data)
    return name


def read_upload(stream, length):
    """The bytes of an upload (at most MAX_UPLOAD), or raises Refused
    (TooLarge, before anything is read when its length says so)."""
    too_large = f"the picture is too large: at most {MAX_UPLOAD // (1024 * 1024)} MB"
    if length is not None and length > MAX_UPLOAD:
        raise TooLarge(too_large)
    data = stream.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise TooLarge(too_large)
    if not data:
        raise Refused("send the picture as the request's body")
    return data


def open_in_roots(file, roots):
    """A file of the media folders, opened for reading: refused (None) unless,
    as opened (/proc/self/fd/N, as app._send checks), it is a regular file
    inside a media root, out of its trash, and not over MAX_FILE."""
    import scanner                             # it imports people, which imports this module
    try:
        fd = os.open(file, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except (OSError, TypeError, ValueError):
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE \
                or not scanner.in_roots(os.readlink(f"/proc/self/fd/{fd}"), roots):
            os.close(fd)
            return None
        return os.fdopen(fd, "rb")
    except OSError:
        os.close(fd)
        return None


# ---------------------------------------------------------------------------
# Where one can come from
# ---------------------------------------------------------------------------

def _folders(conn, pid):
    """{folder: account (platform, author id) or None}: where a person's posts
    are, and their sources' folders."""
    out = {}
    for platform, aid, folder in conn.execute(
            # dirname in SQL: what is left of the path once its last part is trimmed off.
            "SELECT DISTINCT platform, author_id, rtrim(meta_path, replace(meta_path, '/', '')) FROM posts "
            f"WHERE (platform, author_id) IN ({db.PERSON_ACCOUNTS}) LIMIT ?", (pid, pid, pid, MAX_FOLDERS)):
        out.setdefault(folder.rstrip("/") or "/", (platform, aid))
    for platform, aid, folder in conn.execute(
            "SELECT platform, author_id, folder FROM sources WHERE person_id = ? "
            f"OR (platform, author_id) IN ({db.PERSON_ACCOUNTS}) ORDER BY id LIMIT ?",
            (pid, pid, pid, pid, MAX_FOLDERS)):
        if folder:
            out.setdefault(os.path.normpath(folder), (platform, aid) if aid else None)
    return dict(list(out.items())[:MAX_FOLDERS])


def profile_pics(conn, pid, roots):
    """The profile pictures instaloader saved for a person's accounts, newest
    first (by the date in their name): [{key, name, path, account, at}],
    ``key`` an opaque id of the file. Only files inside a media root."""
    import scanner
    known = db.accounts(conn)
    found = {}
    for folder, account in _folders(conn, pid).items():
        try:
            with os.scandir(folder) as it:
                names = [e.name for e in it if PROFILE_PIC_RE.fullmatch(e.name) and e.is_file()]
        except OSError:
            continue
        for n in names:
            p = os.path.join(folder, n)
            if p in found or not scanner.in_roots(p, roots):
                continue
            a = known.get(account) if account else None
            found[p] = {"key": hashlib.sha1(p.encode("utf-8", "surrogateescape")).hexdigest()[:20], "name": n,
                        "path": p, "at": n[:19],
                        "account": {"platform": account[0], "id": account[1],
                                    "handle": (a or {}).get("handle")} if account else None}
    return sorted(found.values(), key=lambda f: (f["at"], f["path"]), reverse=True)[:MAX_PROFILE_PICS]


def media(conn, pid, limit=MAX_MEDIA):
    """A person's newest pictures, from their posts: images, and videos that
    have a poster. [{id, post, kind, posted_at}]."""
    rows = conn.execute(
        "SELECT m.id, m.kind, p.id AS post, p.posted_at FROM media m JOIN posts p ON p.id = m.post_id "
        f"WHERE (p.platform, p.author_id) IN ({db.PERSON_ACCOUNTS}) AND m.missing = 0 AND p.missing = 0 "
        "AND (m.kind = 'image' OR m.poster_path IS NOT NULL) "
        "ORDER BY p.posted_at DESC, p.id, m.idx LIMIT ?", (pid, pid, pid, limit)).fetchall()
    return [{"id": r["id"], "post": r["post"], "kind": r["kind"], "posted_at": r["posted_at"]} for r in rows]


def media_source(conn, pid, media_id):
    """The file to make an avatar from for one of a person's media items: the
    image itself, or a video's poster. Raises Refused when it is not theirs
    or has no picture."""
    row = conn.execute(
        "SELECT m.kind, m.path, m.poster_path FROM media m JOIN posts p ON p.id = m.post_id "
        f"WHERE m.id = ? AND (p.platform, p.author_id) IN ({db.PERSON_ACCOUNTS})",
        (media_id, pid, pid, pid)).fetchone()
    if row is None:
        raise Refused("no such picture among their posts")
    if row["kind"] == "image":
        return row["path"]
    if row["poster_path"]:
        return row["poster_path"]
    raise Refused("that video has no poster picture")


# ---------------------------------------------------------------------------
# Clean up
# ---------------------------------------------------------------------------

def drop_unused(conn, data_dir, names):
    """Delete the files of the avatars among ``names`` that no person has."""
    for name in {n for n in names if n and NAME_RE.fullmatch(n)}:
        if conn.execute("SELECT 1 FROM people WHERE avatar = ? LIMIT 1", (name,)).fetchone():
            continue
        for kind in KINDS:
            try:
                os.remove(path(data_dir, name, kind))
            except OSError:
                pass
