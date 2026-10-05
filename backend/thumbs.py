"""Grid thumbnails, made on first request and cached in the data directory.

Never written next to the media: the media folders are only ever read.
Images go through Pillow; videos without a poster get a frame from ffmpeg when
it is installed.
"""
import hashlib
import os
import subprocess
import tempfile

from PIL import Image, ImageOps

import config

WIDTH = 480
MAX_HEIGHT = 1200      # 9:16 at WIDTH is 853; taller panoramas get cropped by the grid anyway
QUALITY = 82
# The formats a downloaded picture is opened as. Pillow picks one by content,
# not by name: without this an EPS saved as .jpg is rendered by Ghostscript.
FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "AVIF")


def open_image(path):
    return Image.open(path, formats=FORMATS)


def ffmpeg_path():
    """The ffmpeg to run, or None: the path set in Settings, else the first
    in PATH's absolute folders, as for the downloaders (jobs.tool_path:
    never a bare name, nor a relative result)."""
    import jobs                                # it imports db, which imports this module
    return jobs.tool_path("ffmpeg")


def ffprobe_path():
    """The ffprobe to run, or None: the one beside the ffmpeg set in
    Settings when there is one (checked as it is: config.tool_refused),
    else the first in PATH's absolute folders (jobs._which; ffprobe is not
    one of Settings' tools)."""
    import jobs
    configured = (config.load().get("tools") or {}).get("ffmpeg")
    if configured:
        beside = os.path.join(os.path.dirname(configured), "ffprobe")
        if config.tool_refused(beside) is None:
            return beside
    return jobs._which("ffprobe")


def have_ffmpeg():
    return ffmpeg_path() is not None


def _cache_path(data_dir, media_path):
    # Keyed by the media file's path, not its row id: ids are reassigned when
    # the index is rebuilt, and a stale thumbnail must never show another post.
    key = hashlib.sha1(media_path.encode("utf-8", "surrogateescape")).hexdigest()
    return os.path.join(data_dir, "thumbs", key[:2], key + ".jpg")


def _fresh(out, src):
    try:
        return os.path.getmtime(out) >= os.path.getmtime(src)
    except OSError:
        return False


def _save_image(src, out):
    with open_image(src) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((WIDTH, MAX_HEIGHT))
        if img.mode != "RGB":
            img = img.convert("RGB")
        _atomic_save(out, lambda f: img.save(f, "JPEG", quality=QUALITY, optimize=True))


def _atomic_save(out, write):
    config.make_private_dir(os.path.dirname(out))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            write(f)
        os.replace(tmp, out)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _video_frame(src, out, ffmpeg):
    """One frame, a second in (or the first frame of a very short clip),
    made by the program at ``ffmpeg`` (ffmpeg_path)."""
    config.make_private_dir(os.path.dirname(out))
    fd, frame = tempfile.mkstemp(dir=os.path.dirname(out), suffix=".png")
    os.close(fd)
    try:
        for seek in ("1", "0"):
            r = subprocess.run(
                [ffmpeg, "-loglevel", "error", "-y", "-ss", seek, "-i", src,
                 "-frames:v", "1", frame],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0 and os.path.getsize(frame) > 0:
                _save_image(frame, out)
                return True
        return False
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        try:
            os.remove(frame)
        except OSError:
            pass


def thumb_for(data_dir, row):
    """Path to a cached thumbnail for a media row, or None if none can be made."""
    out = _cache_path(data_dir, row["path"])
    if row["kind"] == "image" or row["poster_path"]:
        src = row["poster_path"] or row["path"]
        if _fresh(out, src):
            return out
        try:
            _save_image(src, out)
            return out
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
    src = row["path"]
    if _fresh(out, src):
        return out
    # Remember clips ffmpeg could not read, so a grid refresh does not retry
    # them on every request. Replacing the file (newer mtime) clears it.
    failed = out + ".failed"
    if _fresh(failed, src) or not os.path.isfile(src):
        return None
    ffmpeg = ffmpeg_path()                     # looked up once: none now is not a clip ffmpeg failed on
    if ffmpeg is None:
        return None
    if _video_frame(src, out, ffmpeg):
        return out
    config.make_private_dir(os.path.dirname(failed))
    open(failed, "w").close()
    return None


def cached(data_dir, row):
    """The cached thumbnail of a media row if one is there and up to date,
    without making one."""
    out = _cache_path(data_dir, row["path"])
    return out if _fresh(out, row["poster_path"] or row["path"]) else None


def forget(data_dir, media_path):
    """Drop the cached thumbnail of a media file that is going away."""
    out = _cache_path(data_dir, media_path)
    for path in (out, out + ".failed"):
        try:
            os.remove(path)
        except OSError:
            pass


def move(data_dir, old_path, new_path):
    """Keep a cached thumbnail when its file moves (to the trash and back)."""
    old, new = _cache_path(data_dir, old_path), _cache_path(data_dir, new_path)
    for a, b in ((old, new), (old + ".failed", new + ".failed")):
        try:
            config.make_private_dir(os.path.dirname(b))
            os.replace(a, b)
        except OSError:
            pass
