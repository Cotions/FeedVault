"""Grid thumbnails, made on first request and cached in the data directory.

Never written next to the media: the media folders are only ever read.
Images go through Pillow; videos without a poster get a frame from ffmpeg when
it is installed.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps

WIDTH = 480
MAX_HEIGHT = 1200      # 9:16 at WIDTH is 853; taller panoramas get cropped by the grid anyway
QUALITY = 82
# The formats a downloaded picture is opened as. Pillow picks one by content,
# not by name: without this an EPS saved as .jpg is rendered by Ghostscript.
FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "AVIF")


def open_image(path):
    return Image.open(path, formats=FORMATS)


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None


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
    os.makedirs(os.path.dirname(out), exist_ok=True)
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


def _video_frame(src, out):
    """One frame, a second in (or the first frame of a very short clip)."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fd, frame = tempfile.mkstemp(dir=os.path.dirname(out), suffix=".png")
    os.close(fd)
    try:
        for seek in ("1", "0"):
            r = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-ss", seek, "-i", src,
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
    if _fresh(failed, src) or not have_ffmpeg() or not os.path.isfile(src):
        return None
    if _video_frame(src, out):
        return out
    os.makedirs(os.path.dirname(failed), exist_ok=True)
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
            os.makedirs(os.path.dirname(b), exist_ok=True)
            os.replace(a, b)
        except OSError:
            pass
