#!/usr/bin/env python3
"""Build a demo vault of invented instaloader output, for trying the dashboard.

    python3 scripts/make_demo.py /tmp/feedvault-demo

Writes <dir>/media (fake posts), <dir>/data (database goes here) and
<dir>/config.json. Start the backend against it with:

    FEEDVAULT_CONFIG=<dir>/config.json backend/venv/bin/python backend/app.py

Every handle, name and caption is made up. If ffmpeg is installed, videos are
real 3-second clips; otherwise they are placeholders that will not play.
"""
import json
import os
import random
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "tests"))
import fakes  # noqa: E402

CREATORS = [
    ("mossy.trails", 9001, "Mossy Trails"),
    ("pixel_bakery", 9002, "Pixel Bakery"),
    ("night.tram", 9003, "Night Tram"),
    ("quiet_kiln", 9004, "Quiet Kiln Ceramics"),
    ("lo.fi.garden", 9005, "lo-fi garden"),
]

CAPTIONS = [
    "Morning fog over the ridge. Took the long way round. #hiking #fog",
    "New sourdough recipe is finally stable 🍞\n\n72% hydration, 20h cold proof. #baking #sourdough",
    "Last tram of the night, empty carriage, city lights. #nightphotography",
    "Glaze test batch 14. The blue one surprised me. #ceramics #pottery",
    "Tomatoes are in. Nothing else matters right now. #garden",
    "Carousel dump from the weekend trip, swipe →",
    "Short clip of the kiln opening. Always nervous. #ceramics",
    "Rain again. Cozy corner, tea, a book. @pixel_bakery sent cookies!",
    "",
    "Process video: shaping a boule in 30 seconds #baking",
    "Station at 2am. Nobody around.",
    "Moss macro series part 3. #macro #moss",
    "Café au lait and croissants in the old market. #travel",
    "Throwback to the first bowl I ever threw. It's lopsided and I love it.",
]

LOCATIONS = [None, None, "Lisbon, Portugal", "Kyoto, Japan", "Somewhere in the Alps", None]


def real_video(path, colour):
    hexcol = "0x%02x%02x%02x" % colour
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"color=c={hexcol}:s=360x640:d=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-vf", "drawtext=text='FeedVault demo':fontcolor=white:fontsize=28:x=(w-tw)/2:y=(h-th)/2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
        check=True,
    )


def nicer_png(rng):
    """Gradient images in varied aspect ratios, if Pillow is around."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    def png(path, rgb=(47, 158, 79), size=None):
        w, h = rng.choice([(1080, 1080), (1080, 1350), (1080, 566), (1080, 1920)])
        w, h = w // 3, h // 3
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)
        dark = tuple(c // 4 for c in rgb)
        for y in range(h):
            t = y / h
            draw.line([(0, y), (w, y)], fill=tuple(int(a * (1 - t) + b * t) for a, b in zip(rgb, dark)))
        for _ in range(6):
            r = rng.randint(8, w // 4)
            x, y = rng.randint(0, w), rng.randint(0, h)
            draw.ellipse([x - r, y - r, x + r, y + r], outline=(232, 236, 246), width=2)
        img.save(path, "PNG")
    return png


def add_duplicates(media, ts):
    """Something for the Duplicates page: a typo'd second folder holding
    copies of a few posts (one of them missing a carousel item), and a repost
    of one picture under another creator and post id."""
    src = os.path.join(media, "pixel_bakery")
    dst = os.path.join(media, "pixel_bakerry")
    os.makedirs(dst, exist_ok=True)
    bases = sorted({n[: -len(".json")] if n.endswith(".json") else n[: -len(".json.xz")]
                    for n in os.listdir(src) if n.endswith((".json", ".json.xz"))})[:4]
    dropped = False
    for base in bases:
        names = sorted(n for n in os.listdir(src) if n.startswith(base))
        for n in names:
            if not dropped and n.endswith("_2.jpg") and any(m.endswith("_3.jpg") for m in names):
                dropped = True                  # the copy that differs
                continue
            shutil.copy2(os.path.join(src, n), os.path.join(dst, n))

    mossy = os.path.join(media, "mossy.trails")
    image = next(n for n in sorted(os.listdir(mossy))          # a single-image post: no .mp4 beside it
                 if n.endswith("_UTC.jpg") and not os.path.exists(os.path.join(mossy, n[:-4] + ".mp4")))
    handle, uid, name = CREATORS[2]
    base = fakes.write_post(os.path.join(media, handle), "DEMOrepost01", ts + 86_400,
                            fakes.owner(handle, uid, name), kind="image",
                            caption="Reposting this, too good not to share 🌿 (via @mossy.trails)")
    shutil.copyfile(os.path.join(mossy, image), base + ".jpg")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = os.path.abspath(sys.argv[1])
    media = os.path.join(root, "media")
    if os.path.exists(media):
        shutil.rmtree(media)
    rng = random.Random(42)
    fakes.png = nicer_png(rng) or fakes.png
    have_ffmpeg = shutil.which("ffmpeg") is not None
    if have_ffmpeg:
        # fakes.fake_video writes a placeholder; swap in a real clip.
        fakes.fake_video = lambda path: real_video(path, tuple(rng.randrange(40, 200) for _ in range(3)))

    ts = 1_700_000_000
    for i in range(48):
        handle, uid, name = rng.choice(CREATORS)
        kind = rng.choices(["image", "carousel", "video"], weights=[5, 3, 2])[0]
        slides = [rng.random() < 0.25 for _ in range(rng.randint(2, 6))] if kind == "carousel" else None
        ts += rng.randint(3_600, 400_000)
        shortcode = "DEMO" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz0123456789_-")
                                     for _ in range(7))
        fakes.write_post(
            os.path.join(media, handle), shortcode, ts, fakes.owner(handle, uid, name),
            kind=kind, caption=rng.choice(CAPTIONS), slides=slides, compress=(i % 3 == 0),
            likes=rng.randint(0, 5000), comments=rng.randint(0, 200),
            views=rng.randint(100, 90000) if kind == "video" else None,
            product_type="clips" if kind == "video" else None,
            location=rng.choice(LOCATIONS),
        )
    # A stray file so the Unmatched page has something to show.
    fakes.png(os.path.join(media, "pixel_bakery", "screenshot_from_phone.png"), (200, 60, 60))
    add_duplicates(media, ts)

    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    with open(os.path.join(root, "config.json"), "w") as f:
        json.dump({"data_directory": os.path.join(root, "data"), "media_roots": [media]}, f, indent=2)
    print(f"Demo vault at {root} ({'real' if have_ffmpeg else 'placeholder'} videos)")
    print(f"Start: FEEDVAULT_CONFIG={root}/config.json backend/venv/bin/python backend/app.py")


if __name__ == "__main__":
    main()
