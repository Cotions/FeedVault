#!/usr/bin/env python3
"""A stand-in for instaloader, for tests and the demo: never touches the network.

Install it on a PATH as ``instaloader`` (a symlink or a copy). It reads its
profiles from the JSON file named by FAKE_INSTALOADER (default
``fake_instaloader.json`` beside this script):

    {"profiles": {"somebody": {"id": 555, "name": "Some Body", "bio": "…",
                               "posts": [{"shortcode": "C0FAKE00001", "ts": 1717243200,
                                          "caption": "…", "kind": "image" | "video" | "carousel",
                                          "slides": 3, "video_slides": [2]}]}},
     "fail": null | "429" | "login" | "private" | "notfound" | "crash" | "leak",
     "delay": 0}

A profile may have its own "fail" (one of the same), used when the top
one is null. A profile may also have "reels" and "tagged" (posts as above; a tagged
post has its own "owner": {"username", "id", "full_name"?}), "stories" ([{"id", "ts",
"video": bool}]) and "highlights" ([{"title", "items": [story items]}]).

It behaves like instaloader 4.15 for one profile target (or one post,
``-<shortcode>``) with the flags
FeedVault passes: it honours --latest-stamps (post-timestamp,
reels-timestamp, tagged-timestamp and story-timestamp of the target's
section, written back as instaloader does; highlights have none; a
target no profile has whose stamped profile-id one has is a renamed
profile: "Profile <old> has changed its name to <new>.", the section moves
to the new name and the profile is downloaded, exit 1 as instaloader's),
--fast-update (stops at the first file that exists), --dirname-pattern and
--filename-pattern (str.format with target, profile, shortcode, date_utc;
a tagged post's target is ``<profile>/:tagged``, a highlight's
``<profile>/<title>``, as instaloader's; with --sanitize-paths the ``:``
and Windows' other reserved characters in those become their full-width
look-alikes, as instaloader's sanitize_path does), --no-compress-json (metadata
JSON beside the media, plus the profile's JSON and a caption .txt),
--no-posts, --reels, --tagged, --stories and --highlights (the last two
fail with "Login required." without --login or --load-cookies; with
either it prints instaloader's lines of a session loaded and accepted), in
instaloader's order (tagged, reels, highlights, posts, stories),
--no-videos, --no-video-thumbnails and --no-pictures (no picture of a post
or carousel slide, nor a video's thumbnail: an image post gets its JSON and
caption only; story items ignore it, as instaloader's do; refused with
--fast-update), and --post-filter and
--storyitem-filter, evaluated as instaloader does (each name must be an
attribute of the item: ``is_video``, ``date_utc`` (naive UTC), ``typename``;
``datetime`` is the class).
Every run appends {"argv", "at"} as one JSON line to FAKE_INSTALOADER_LOG,
when set. With FAKE_INSTALOADER_WINDOWS_NAMES set it writes as on exFAT or
NTFS: a name with a reserved character (``:``) fails with errno 22.
"delay" sleeps that many seconds before each post (for cancelling).
"""
import argparse
import ast
import configparser
import json
import os
import struct
import sys
import time
import zlib
from datetime import datetime, timezone

STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


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


class Stamp(datetime):
    """Formats like instaloader's datetimes: 2024-06-01_12-00-00 without a spec."""

    def __format__(self, spec):
        return super().__format__(spec or "%Y-%m-%d_%H-%M-%S")


def node(p, profile, target):
    kind = p.get("kind", "image")
    typename = {"image": "GraphImage", "video": "GraphVideo", "carousel": "GraphSidecar"}[kind]
    n = {"__typename": typename, "id": str(abs(hash(p["shortcode"])) % 10**18), "shortcode": p["shortcode"],
         "taken_at_timestamp": p["ts"], "is_video": kind == "video",
         "owner": {"id": str(profile["id"]), "username": target, "full_name": profile.get("name", target)},
         "edge_media_to_caption": {"edges": [{"node": {"text": p["caption"]}}] if p.get("caption") else []},
         "edge_media_preview_like": {"count": p.get("likes", 12)}, "edge_media_to_comment": {"count": 3}}
    if kind == "carousel":
        n["edge_sidecar_to_children"] = {"edges": [{"node": {"is_video": s in p.get("video_slides", ())}}
                                                   for s in range(1, p.get("slides", 2) + 1)]}
    return n


def write(base, p, profile, owner, videos=True, thumbnails=True, pictures=True):
    """A post's files, named after ``base``, as instaloader writes them."""
    kind = p.get("kind", "image")
    slides = p.get("slides", 2) if kind == "carousel" else 1
    colour = tuple((sum(map(ord, p["shortcode"])) * k) % 256 for k in (3, 7, 11))
    for s in range(1, slides + 1):
        name = f"{base}_{s}" if slides > 1 else base
        video = kind == "video" or (kind == "carousel" and s in p.get("video_slides", ()))
        if pictures and (not video or thumbnails or not videos):
            png(name + ".jpg", tuple((c + 40 * s) % 256 for c in colour))
        if video and videos:
            mp4(name + ".mp4")
    with open(base + ".json", "w") as f:
        json.dump({"node": node(p, profile, owner),
                   "instaloader": {"version": "4.15.1", "node_type": "Post"}}, f, indent=4)
    if p.get("caption"):
        with open(base + ".txt", "w") as f:
            f.write(p["caption"])
    folder = os.path.dirname(base) or "."
    for n in os.listdir(folder):
        if n.startswith(os.path.basename(base)):
            os.utime(os.path.join(folder, n), (p["ts"], p["ts"]))


def one_post(data, args, target):
    """``-<shortcode>``: that post alone, its owner as {profile}, the target
    as given as {target} (as instaloader's download_post)."""
    code = target[1:]
    found = next(((name, profile, p) for name, profile in data["profiles"].items()
                  for p in profile["posts"] if p["shortcode"] == code), None)
    if found is None:
        print(f"{target}: Fetching Post metadata failed.", file=sys.stderr)
        print("\nErrors or warnings occurred:", file=sys.stderr)
        return 1
    owner, profile, p = found
    time.sleep(data.get("delay", 0))
    date = Stamp.fromtimestamp(p["ts"], timezone.utc).replace(tzinfo=None)
    folder = (args.dirname_pattern or "{target}").format(target=target, profile=owner)
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, (args.filename_pattern or "{date_utc}_UTC").format(
        target=target, profile=owner, shortcode=code, date_utc=date, date=date))
    write(base, p, profile, owner)
    print(f"{base}.jpg json ")
    return 0


def fail(kind, target):
    """A failed profile, as instaloader 4.15 prints it: each error on stderr
    as it happens, repeated under "Errors or warnings occurred:" at the end
    (context.error with repeat_at_end; the 429 retries are not repeated)."""
    errors = []
    if kind == "429":
        print("JSON Query to graphql/query: 429 Too Many Requests [retrying; skip with ^C]", file=sys.stderr)
        print('Instagram responded with HTTP error "429 - Too Many Requests". Please do not run multiple\n'
              "instances of Instaloader in parallel or within short sequence.", file=sys.stderr)
        errors.append(f"{target}: Please wait a few minutes before you try again.")
    elif kind == "login":
        errors.append(f"{target}: Login required.")
    elif kind == "private":
        errors.append(f"{target}: Private but not followed.")
    elif kind == "notfound":
        print(f"Profile {target} does not exist.\nThe most similar profile is: {target}_.", file=sys.stderr)
        print("\nErrors or warnings occurred:", file=sys.stderr)
        return 1
    elif kind == "crash":
        raise RuntimeError("fake crash")
    elif kind == "leak":
        # Made-up secrets in an error line: FeedVault must never store or show them.
        print(f"Loaded session from /home/someone/.config/instaloader/session-{target}.")
        errors.append(f"{target}: JSON Query to api/v1/users: 400 Bad Request - cookie sessionid=FAKE-SECRET-1; "
                      "csrftoken=FAKE-SECRET-2 [Cookie: ds_user_id=FAKE-SECRET-3] "
                      "Authorization: Bearer FAKESECRETFAKESECRET4")
    for e in errors:
        print(e, file=sys.stderr)
    print("\nErrors or warnings occurred:", file=sys.stderr)
    for e in errors:
        print(e, file=sys.stderr)
    return 1


class Item:
    """What a filter sees of a post or story item (Post's and StoryItem's attributes it reads)."""

    def __init__(self, p, story=False):
        self.is_video = p.get("video", False) if story else p.get("kind", "image") == "video"
        self.date_utc = datetime.fromtimestamp(p["ts"], timezone.utc).replace(tzinfo=None)
        self.typename = ("GraphStoryVideo" if self.is_video else "GraphStoryImage") if story else \
            {"image": "GraphImage", "video": "GraphVideo", "carousel": "GraphSidecar"}[p.get("kind", "image")]


def item_filter(text):
    """--post-filter / --storyitem-filter as instaloader's filterstr_to_filterfunc builds it."""
    if text is None:
        return None

    class Names(ast.NodeTransformer):
        def visit_Name(self, n):
            if not isinstance(n.ctx, ast.Load):
                raise ValueError(f"Invalid filter: Modifying variables ({n.id}) not allowed.")
            if n.id == "datetime":
                return n
            if n.id not in ("is_video", "date_utc", "typename"):
                raise ValueError(f"Invalid filter: {n.id} not a Post attribute.")
            return ast.copy_location(ast.Attribute(ast.copy_location(ast.Name("item", ast.Load()), n), n.id,
                                                   ast.Load()), n)
    code = compile(ast.fix_missing_locations(Names().visit(ast.parse(text, mode="eval"))), "<filter>", "eval")
    return lambda item: bool(eval(code, {"item": item, "datetime": datetime}))


def read_stamp(stamps, target, key):
    if stamps.has_option(target, key):
        return datetime.strptime(stamps.get(target, key), STAMP_FORMAT)
    return None


def set_stamp(stamps, target, key, when):
    if not stamps.has_section(target):
        stamps.add_section(target)
    stamps.set(target, key, when.astimezone().strftime(STAMP_FORMAT))


RESERVED = {":": "\uff1a", "<": "\ufe64", ">": "\ufe65", '"': "\uff02", "\\": "\ufe68", "|": "\uff5c",
            "?": "\ufe16", "*": "\uff0a"}


def sanitize(name, windows):
    """instaloader's _PostPathFormatter.sanitize_path, with --sanitize-paths as ``windows``."""
    name = name.replace("/", "\u2215")
    if name.startswith("."):
        name = name.replace(".", "\u2024", 1)
    return "".join(RESERVED.get(c, c) for c in name) if windows else name


class Refused(OSError):
    """A name the filesystem refuses (FAKE_INSTALOADER_WINDOWS_NAMES)."""


def check_name(base, folder):
    if os.environ.get("FAKE_INSTALOADER_WINDOWS_NAMES") and any(c in RESERVED for c in os.path.relpath(base, folder)):
        raise Refused(22, "Invalid argument", base)


def walk(posts, target, owner_target, profile, args, folder, stamps, key, keep):
    """Posts newest first (``pinned`` ones first) down to the stamp
    ``key``, filtered; their stamp written back. ``owner_target``: {target}
    in the file names."""
    last = read_stamp(stamps, target, key) if args.latest_stamps else None
    ordered = sorted(posts, key=lambda p: (not p.get("pinned"), -p["ts"]))
    for i, p in enumerate(ordered, start=1):
        date = Stamp.fromtimestamp(p["ts"], timezone.utc)
        if last is not None and not date > last:
            if p.get("pinned"):
                continue
            break
        time.sleep(keep["delay"])
        if keep["post"] and not keep["post"](Item(p)):
            print(f"[{i:2d}/{len(ordered):2d}] {p['shortcode']} skipped")
            continue
        base = os.path.join(folder, (args.filename_pattern or "{date_utc}_UTC").format(
            target=owner_target, profile=owner_target, shortcode=p["shortcode"], date_utc=date.replace(tzinfo=None),
            date=date.replace(tzinfo=None)))
        check_name(base, folder)
        os.makedirs(os.path.dirname(base), exist_ok=True)
        kind = p.get("kind", "image")
        slides = p.get("slides", 2) if kind == "carousel" else 1
        first = base + ("_1" if slides > 1 else "")
        if any(os.path.exists(first + ext) for ext in (".jpg", ".mp4")):
            print(f"[{i:2d}/{len(ordered):2d}] {base}.jpg exists")
            if args.fast_update and not p.get("pinned"):
                break
            continue
        owner = p.get("owner") or {}
        # A tagged post is its owner's: their id and name (full_name, else the user name).
        mine = {"id": owner["id"], "name": owner.get("full_name", owner["username"])} if owner else {}
        write(base, p, {**profile, **mine}, owner.get("username", target),
              videos=not args.no_videos, thumbnails=not args.no_video_thumbnails, pictures=not args.no_pictures)
        print(f"[{i:2d}/{len(ordered):2d}] {base}.jpg json ")
    if args.latest_stamps and ordered:
        newest = max(ordered, key=lambda p: p["ts"])
        set_stamp(stamps, target, key, datetime.fromtimestamp(newest["ts"], timezone.utc))


def story_items(items, target, owner_target, profile, args, folder, keep, last=None):
    """Story items newest first down to ``last``, filtered (--storyitem-filter)."""
    for it in sorted(items, key=lambda it: -it["ts"]):
        date = Stamp.fromtimestamp(it["ts"], timezone.utc)
        if last is not None and not date > last:
            break
        if keep["story"] and not keep["story"](Item(it, story=True)):
            print(f"<story {it['id']} skipped>")
            continue
        base = os.path.join(folder, (args.filename_pattern or "{date_utc}_UTC").format(
            target=owner_target, profile=target, shortcode=str(it["id"]), date_utc=date.replace(tzinfo=None),
            date=date.replace(tzinfo=None)))
        check_name(base, folder)
        os.makedirs(os.path.dirname(base), exist_ok=True)
        video = it.get("video", False)
        if os.path.exists(base + (".mp4" if video and not args.no_videos else ".jpg")):
            print(f"{base}.jpg exists")
            if args.fast_update:
                break
            continue
        if video and not args.no_videos:
            mp4(base + ".mp4")
        if not video or not args.no_video_thumbnails or args.no_videos:
            png(base + ".jpg", (90, 120, 200))
        with open(base + ".json", "w") as f:
            json.dump({"node": {"__typename": "GraphStoryVideo" if video else "GraphStoryImage", "id": str(it["id"]),
                                "taken_at_timestamp": it["ts"], "is_video": video,
                                "owner": {"id": str(profile["id"]), "username": target,
                                          "full_name": profile.get("name", target)}},
                       "instaloader": {"version": "4.15.1", "node_type": "StoryItem"}}, f, indent=4)
        print(f"{base}.jpg json ")


def main(argv):
    try:
        return run(argv)
    except Refused as e:                       # as instaloader dies on an OSError it does not catch
        print(f"{e}", file=sys.stderr)
        print("\nErrors or warnings occurred:", file=sys.stderr)
        return 1


def run(argv):
    if argv == ["--version"]:
        print("4.15.1")
        return 0
    if os.environ.get("FAKE_INSTALOADER_LOG"):
        with open(os.environ["FAKE_INSTALOADER_LOG"], "a") as f:
            f.write(json.dumps({"argv": argv, "at": time.time()}) + "\n")
    ap = argparse.ArgumentParser(add_help=False)
    for flag in ("--latest-stamps", "--dirname-pattern", "--filename-pattern", "--title-pattern",
                 "--load-cookies", "--login", "--post-filter", "--storyitem-filter"):
        ap.add_argument(flag)
    for flag in ("--fast-update", "--no-compress-json", "--no-posts", "--reels", "--tagged", "--stories",
                 "--highlights", "--no-videos", "--no-video-thumbnails", "--no-pictures", "--sanitize-paths"):
        ap.add_argument(flag, action="store_true")
    ap.add_argument("targets", nargs="*")
    args = ap.parse_args(argv)
    data_file = os.environ.get("FAKE_INSTALOADER") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                   "fake_instaloader.json")
    with open(data_file) as f:
        data = json.load(f)
    if args.no_pictures and args.fast_update:
        print("Fatal error: --no-pictures and --fast-update cannot be used together.", file=sys.stderr)
        return 2
    if args.login and not os.environ.get("FAKE_INSTALOADER_SESSION"):
        print("Session file does not exist yet - Logging in.", file=sys.stderr)
        print("Login error: no password to log in with.", file=sys.stderr)
        return 3
    try:
        keep = {"post": item_filter(args.post_filter), "story": item_filter(args.storyitem_filter),
                "delay": data.get("delay", 0)}
    except (SyntaxError, ValueError) as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        return 2
    logged_in = bool(args.login or args.load_cookies)
    if args.login:
        print(f"Loaded session from /home/someone/.config/instaloader/session-{args.login}.")
        print(f"Logged in as {args.login}.")
    elif args.load_cookies:
        print(f"Cookies loaded successfully from {args.load_cookies}")
        print("someone has been successfully logged in.")
    if args.latest_stamps:
        print(f"Using latest stamps from {args.latest_stamps}.")
    status = 0
    for target in args.targets:
        if data.get("fail") or (data["profiles"].get(target) or {}).get("fail"):
            status = fail(data.get("fail") or data["profiles"][target]["fail"], target)
            continue
        if target.startswith("-"):
            if not args.no_compress_json:
                print("fake instaloader: only --no-compress-json is supported", file=sys.stderr)
                return 2
            status = one_post(data, args, target) or status
            continue
        profile = data["profiles"].get(target)
        repeat = []
        if profile is None and args.latest_stamps:
            # check_profile_id: the profile id --latest-stamps keeps finds a renamed profile.
            stamps = configparser.ConfigParser()
            stamps.read(args.latest_stamps)
            pid = stamps.get(target, "profile-id", fallback=None)
            new = next((name for name, p in data["profiles"].items() if str(p["id"]) == pid), None)
            if new is not None:
                print(f"Trying to find profile {target} using its unique ID {pid}.")
                repeat.append(f"Profile {target} has changed its name to {new}.")
                print(repeat[-1], file=sys.stderr)
                stamps[new] = dict(stamps[target])
                stamps.remove_section(target)
                with open(args.latest_stamps, "w") as f:
                    stamps.write(f)
                target, profile, status = new, data["profiles"][new], 1
        if profile is None:
            status = fail("notfound", target)
            continue
        folder = (args.dirname_pattern or "{target}").format(target=target, profile=target)
        os.makedirs(folder, exist_ok=True)
        print(f"[1/1] Downloading profile {target}")
        if not args.no_compress_json:
            print("fake instaloader: only --no-compress-json is supported", file=sys.stderr)
            return 2
        with open(os.path.join(folder, f"{target}_{profile['id']}.json"), "w") as f:
            json.dump({"node": {"id": str(profile["id"]), "username": target, "biography": profile.get("bio", ""),
                                "external_url": profile.get("url")},
                       "instaloader": {"version": "4.15.1", "node_type": "Profile"}}, f, indent=4)
        stamps = configparser.ConfigParser()
        if args.latest_stamps:
            stamps.read(args.latest_stamps)
        if args.tagged:
            print(f"Retrieving tagged posts for profile {target}.")
            walk(profile.get("tagged", []), target, f"{target}/{sanitize(':tagged', args.sanitize_paths)}", profile,
                 args, folder, stamps,
                 "tagged-timestamp", keep)
        if args.reels:
            print(f"Retrieving reels videos for profile {target}.")
            walk(profile.get("reels", []), target, target, profile, args, folder, stamps, "reels-timestamp", keep)
        if args.highlights:
            if not logged_in:
                print(f"Download highlights of {target}: Login required.", file=sys.stderr)
                status = 1
            else:
                for h in profile.get("highlights", []):
                    print(f'Retrieving highlights "{h["title"]}" from profile {target}')
                    story_items(h["items"], target, f"{target}/{sanitize(h['title'], args.sanitize_paths)}", profile,
                                args, folder, keep)
        if not args.no_posts:
            print(f"Retrieving posts from profile {target}.")
            walk(profile["posts"], target, target, profile, args, folder, stamps, "post-timestamp", keep)
        if args.stories:
            if not logged_in:
                print("Download stories: Login required.", file=sys.stderr)
                status = 1
            else:
                last = read_stamp(stamps, target, "story-timestamp") if args.latest_stamps else None
                print(f"[1/1] Retrieving stories from profile {target}.")
                story_items(profile.get("stories", []), target, target, profile, args, folder, keep, last)
                if args.latest_stamps:
                    set_stamp(stamps, target, "story-timestamp", datetime.now(timezone.utc))
        if args.latest_stamps:
            if stamps.has_section(target):
                stamps.set(target, "profile-id", str(profile["id"]))
            os.makedirs(os.path.dirname(args.latest_stamps), exist_ok=True)
            with open(args.latest_stamps, "w") as f:
                stamps.write(f)
        if status:
            print("\nErrors or warnings occurred:", file=sys.stderr)
            for e in repeat:
                print(e, file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
