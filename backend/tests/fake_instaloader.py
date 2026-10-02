#!/usr/bin/env python3
"""A stand-in for instaloader, for tests and the demo: never touches the network.

Install it on a PATH as ``instaloader`` (a symlink or a copy). It reads its
profiles from the JSON file named by FAKE_INSTALOADER (default
``fake_instaloader.json`` beside this script):

    {"profiles": {"somebody": {"id": 555, "name": "Some Body", "bio": "…",
                               "posts": [{"shortcode": "C0FAKE00001", "ts": 1717243200,
                                          "caption": "…", "kind": "image" | "video" | "carousel",
                                          "slides": 3}]}},
     "fail": null | "429" | "login" | "private" | "notfound" | "crash",
     "delay": 0}

and behaves like instaloader 4.15 for one profile target (or one post,
``-<shortcode>``) with the flags
FeedVault passes: it honours --latest-stamps (the post-timestamp of the
target's section, written back as instaloader does), --fast-update (stops at
the first file that exists), --dirname-pattern and --filename-pattern
(str.format with target, profile, shortcode, date_utc), --no-compress-json
(metadata JSON beside the media, plus the profile's JSON and a caption .txt).
Every run appends {"argv", "at"} as one JSON line to FAKE_INSTALOADER_LOG,
when set.
"delay" sleeps that many seconds before each post (for cancelling).
"""
import argparse
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
        n["edge_sidecar_to_children"] = {"edges": [{"node": {"is_video": False}}] * p.get("slides", 2)}
    return n


def write(base, p, profile, owner):
    """A post's files, named after ``base``, as instaloader writes them."""
    kind = p.get("kind", "image")
    slides = p.get("slides", 2) if kind == "carousel" else 1
    colour = tuple((sum(map(ord, p["shortcode"])) * k) % 256 for k in (3, 7, 11))
    for s in range(1, slides + 1):
        name = f"{base}_{s}" if slides > 1 else base
        png(name + ".jpg", tuple((c + 40 * s) % 256 for c in colour))
        if kind == "video":
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
    if kind == "429":
        print("JSON Query to graphql/query: 429 Too Many Requests [retrying; skip with ^C]", file=sys.stderr)
        print('Instagram responded with HTTP error "429 - Too Many Requests". Please do not run multiple\n'
              "instances of Instaloader in parallel or within short sequence.", file=sys.stderr)
        print(f"{target}: Please wait a few minutes before you try again.", file=sys.stderr)
    elif kind == "login":
        print(f"{target}: Login required.", file=sys.stderr)
    elif kind == "private":
        print(f"{target}: Private but not followed.", file=sys.stderr)
    elif kind == "notfound":
        print(f"Profile {target} does not exist.\nThe most similar profile is: {target}_.", file=sys.stderr)
    elif kind == "crash":
        raise RuntimeError("fake crash")
    print("\nErrors or warnings occurred:", file=sys.stderr)
    return 1


def main(argv):
    if argv == ["--version"]:
        print("4.15.1")
        return 0
    if os.environ.get("FAKE_INSTALOADER_LOG"):
        with open(os.environ["FAKE_INSTALOADER_LOG"], "a") as f:
            f.write(json.dumps({"argv": argv, "at": time.time()}) + "\n")
    ap = argparse.ArgumentParser(add_help=False)
    for flag in ("--latest-stamps", "--dirname-pattern", "--filename-pattern", "--title-pattern",
                 "--load-cookies", "--login"):
        ap.add_argument(flag)
    for flag in ("--fast-update", "--no-compress-json"):
        ap.add_argument(flag, action="store_true")
    ap.add_argument("targets", nargs="*")
    args = ap.parse_args(argv)
    data_file = os.environ.get("FAKE_INSTALOADER") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                   "fake_instaloader.json")
    with open(data_file) as f:
        data = json.load(f)
    if args.login and not os.environ.get("FAKE_INSTALOADER_SESSION"):
        print("Session file does not exist yet - Logging in.", file=sys.stderr)
        print("Login error: no password to log in with.", file=sys.stderr)
        return 3
    if args.latest_stamps:
        print(f"Using latest stamps from {args.latest_stamps}.")
    status = 0
    for target in args.targets:
        if data.get("fail"):
            status = fail(data["fail"], target)
            continue
        if target.startswith("-"):
            if not args.no_compress_json:
                print("fake instaloader: only --no-compress-json is supported", file=sys.stderr)
                return 2
            status = one_post(data, args, target) or status
            continue
        profile = data["profiles"].get(target)
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
        last = None
        if stamps.has_option(target, "post-timestamp"):
            last = datetime.strptime(stamps.get(target, "post-timestamp"), STAMP_FORMAT)
        posts = sorted(profile["posts"], key=lambda p: -p["ts"])
        print(f"Retrieving posts from profile {target}.")
        for i, p in enumerate(posts, start=1):
            date = Stamp.fromtimestamp(p["ts"], timezone.utc)
            if last is not None and not date > last:
                break
            time.sleep(data.get("delay", 0))
            base = os.path.join(folder, (args.filename_pattern or "{date_utc}_UTC").format(
                target=target, profile=target, shortcode=p["shortcode"], date_utc=date.replace(tzinfo=None),
                date=date.replace(tzinfo=None)))
            kind = p.get("kind", "image")
            slides = p.get("slides", 2) if kind == "carousel" else 1
            first = base + ("_1" if slides > 1 else "") + ".jpg"
            if os.path.exists(first):
                print(f"[{i:2d}/{len(posts):2d}] {base}.jpg exists")
                if args.fast_update:
                    break
                continue
            write(base, p, profile, target)
            print(f"[{i:2d}/{len(posts):2d}] {base}.jpg json ")
        if args.latest_stamps and posts:
            if not stamps.has_section(target):
                stamps.add_section(target)
            stamps.set(target, "profile-id", str(profile["id"]))
            stamps.set(target, "post-timestamp",
                       datetime.fromtimestamp(posts[0]["ts"]).astimezone().strftime(STAMP_FORMAT))
            os.makedirs(os.path.dirname(args.latest_stamps), exist_ok=True)
            with open(args.latest_stamps, "w") as f:
                stamps.write(f)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
