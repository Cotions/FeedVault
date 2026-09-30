"""Fake instaloader output for tests and the demo vault.

Shapes follow what instaloader 4.15 writes (see backend/parsers/instaloader.py).
Handles, names and captions are invented; no real account or media is used.
"""
import json
import lzma
import os
import struct
import zlib
from datetime import datetime, timezone


def png(path, rgb=(47, 158, 79), size=(64, 64)):
    """Write a solid-colour PNG with the standard library only."""
    w, h = size
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    body = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(body)


def base_name(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d_%H-%M-%S") + "_UTC"


def post_node(shortcode, ts, owner, caption="", typename="GraphImage", children=None,
              likes=10, comments=2, views=None, product_type=None, location=None):
    node = {
        "__typename": typename,
        "id": str(abs(hash(shortcode)) % 10**18),
        "shortcode": shortcode,
        "taken_at_timestamp": ts,
        "owner": owner,
        "edge_media_to_caption": {"edges": [{"node": {"text": caption}}] if caption else []},
        "edge_media_preview_like": {"count": likes},
        "edge_media_to_comment": {"count": comments},
        "is_video": typename == "GraphVideo",
    }
    if views is not None:
        node["video_view_count"] = views
    if product_type:
        node["product_type"] = product_type
    if location:
        node["location"] = {"name": location}
    if children:
        node["edge_sidecar_to_children"] = {"edges": [{"node": {"is_video": c}} for c in children]}
    return node


def write_meta(path, node, node_type="Post", compress=False):
    data = {"node": node, "instaloader": {"version": "4.15.1", "node_type": node_type}}
    if compress:
        with lzma.open(path + ".json.xz", "wt") as f:
            json.dump(data, f)
        return path + ".json.xz"
    with open(path + ".json", "w") as f:
        json.dump(data, f, indent=4)
    return path + ".json"


def fake_video(path):
    # Not a playable video; enough for indexing tests. The demo vault reuses it.
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)


def write_post(folder, shortcode, ts, owner, kind="image", caption="", slides=None,
               compress=False, **kw):
    """Write one post the way instaloader lays it out. Returns the base path.

    kind: image | video | carousel. For carousel, ``slides`` is a list of
    booleans, True for a video slide.
    """
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, base_name(ts))
    colour = tuple((sum(map(ord, shortcode)) * k) % 256 for k in (3, 7, 11))
    if kind == "image":
        png(base + ".jpg", colour)
        node = post_node(shortcode, ts, owner, caption, "GraphImage", **kw)
    elif kind == "video":
        png(base + ".jpg", colour)
        fake_video(base + ".mp4")
        node = post_node(shortcode, ts, owner, caption, "GraphVideo", **kw)
    else:
        slides = slides or [False, False]
        for i, is_video in enumerate(slides, start=1):
            png(f"{base}_{i}.jpg", tuple((c + 40 * i) % 256 for c in colour))
            if is_video:
                fake_video(f"{base}_{i}.mp4")
        node = post_node(shortcode, ts, owner, caption, "GraphSidecar", children=slides, **kw)
    if caption:
        with open(base + ".txt", "w") as f:
            f.write(caption)
    write_meta(base, node, compress=compress)
    return base


def owner(handle, uid, name=None):
    return {"id": str(uid), "username": handle, "full_name": name or handle.title()}
