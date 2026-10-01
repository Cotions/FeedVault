"""Parsers turn a downloader's output folder into posts.

One parser per tool, not per platform: instaloader writes one metadata file per
post, gallery-dl one per media file, yt-dlp one per video. So a parser is handed
a whole directory listing and decides which files it understands.

A parser never touches the network and never writes to the media folder.
"""
from dataclasses import dataclass, field
from typing import Optional

IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "heic", "avif"}
VIDEO_EXT = {"mp4", "mov", "webm", "m4v", "mkv"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_media(name):
    return ext_of(name) in MEDIA_EXT


@dataclass
class Media:
    idx: int
    kind: str                       # "image" | "video"
    path: str
    poster_path: Optional[str] = None


@dataclass
class ParsedPost:
    platform: str
    post_id: str
    url: str
    kind: str                       # image | video | carousel | story | text
    author_id: Optional[str]
    author_handle: Optional[str]
    author_name: Optional[str]
    posted_at: Optional[int]
    text: str
    meta_path: str
    tool: str
    tool_version: Optional[str] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    views: Optional[int] = None
    location: Optional[str] = None
    album: Optional[str] = None     # highlight title, collection name
    hashtags: list = field(default_factory=list)
    media: list = field(default_factory=list)
    # Other files that belong to the post and go to the trash with it: the
    # per-file metadata JSONs besides meta_path, music, subtitles.
    side_files: list = field(default_factory=list)

    @property
    def id(self):
        return f"{self.platform}:{self.post_id}"


@dataclass
class DirResult:
    posts: list = field(default_factory=list)
    # Files the parser owns, whether or not they became a post: media, captions,
    # profile metadata. Anything not claimed by any parser and that looks like
    # media ends up on the Unmatched page.
    claimed: set = field(default_factory=set)
    # (path, message) for files that look like ours but could not be read.
    errors: list = field(default_factory=list)


from . import instaloader, gallery_dl  # noqa: E402

PARSERS = [instaloader, gallery_dl]


def parse_dir(root, dirpath, names):
    """Run every parser over one directory. Earlier parsers win a file.

    ``root`` is the media root the directory sits under; parsers that infer
    things from folder layout (whose profile a folder is) need it.
    """
    result = DirResult()
    remaining = list(names)
    for parser in PARSERS:
        r = parser.parse_dir(root, dirpath, remaining)
        result.posts.extend(r.posts)
        result.claimed |= r.claimed
        result.errors.extend(r.errors)
        remaining = [n for n in remaining if n not in r.claimed]
    return result
