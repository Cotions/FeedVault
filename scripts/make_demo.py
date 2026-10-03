#!/usr/bin/env python3
"""Build a demo vault of invented instaloader and gallery-dl output, for trying the dashboard.

    backend/venv/bin/python scripts/make_demo.py /tmp/feedvault-demo

Writes <dir>/media (fake posts), <dir>/data (database goes here) and
<dir>/config.json. It also installs stand-ins for gallery-dl and yt-dlp in
<dir>/bin (set as the tools in the config; they never touch the network), so
adding https://x.com/demo_skies, https://tiktok.com/@demo.clips or
https://youtube.com/@demoshorts as a source and syncing it downloads invented
posts (<dir>/fake_downloads.json). Likewise an instaloader stand-in: the
profile demo.reels has posts, reels, stories, highlights and tagged posts
(<dir>/fake_instaloader.json). The demo_skies source is there already, with
options (media tab, images, since 2023-11-01). One TikTok profile, old.sync, is as a yt-dlp
sync with cookies left it before FeedVault removed them. Start the backend against it with:

    FEEDVAULT_CONFIG=<dir>/config.json backend/venv/bin/python backend/app.py

With the backend's virtualenv (as above) it also builds the index, so the
demo starts with a few new posts and one notification entry (seed_news);
with a bare python3 the first start builds it and nothing is new.

Every handle, name and caption is made up. If ffmpeg is installed, videos are
real 3-second clips; otherwise they are placeholders that will not play.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

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


def photo(path, seed, size=(1080, 1350)):
    """A picture with some structure (blurred shapes): the gradients above
    are too flat to fingerprint."""
    from PIL import Image, ImageDraw, ImageFilter
    rng = random.Random(seed)
    img = Image.new("RGB", size, tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    w, h = size
    for _ in range(40):
        x, y, r = rng.randrange(w), rng.randrange(h), rng.randrange(w // 20, w // 4)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=tuple(rng.randrange(256) for _ in range(3)))
    img = img.filter(ImageFilter.GaussianBlur(3))
    img.save(path, "JPEG", quality=92)
    return img


def add_duplicates(media, ts):
    """Something for the Duplicates page: a typo'd second folder holding
    copies of a few posts (one of them missing a carousel item), a repost
    of one picture under another creator and post id, and a smaller,
    recompressed repost of another (similar, not identical)."""
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

    try:
        import PIL  # noqa: F401
    except ImportError:
        return
    handle, uid, name = CREATORS[3]
    base = fakes.write_post(os.path.join(media, handle), "DEMOglaze014", ts - 30 * 86_400,
                            fakes.owner(handle, uid, name), kind="image",
                            caption="Glaze test batch 14, the full set. #ceramics")
    img = photo(base + ".jpg", 14)
    handle, uid, name = CREATORS[4]
    base = fakes.write_post(os.path.join(media, handle), "DEMOrepost02", ts + 2 * 86_400,
                            fakes.owner(handle, uid, name), kind="image",
                            caption="saw this and had to share (from @quiet_kiln)")
    img.resize((540, 675)).save(base + ".jpg", "JPEG", quality=60)


def _gdl_template(case, n=0):
    folder = os.path.join(fakes.GALLERY_DL, case)
    with open(os.path.join(folder, sorted(x for x in os.listdir(folder) if x.endswith(".json"))[n])) as f:
        return json.load(f)


def _gdl_write(folder, name, data, ext=None):
    """One gallery-dl file: the media (made up) and its .json beside it."""
    os.makedirs(folder, exist_ok=True)
    if ext in ("jpg", "png"):
        fakes.png(os.path.join(folder, name), tuple(random.Random(name).randrange(40, 220) for _ in range(3)))
    elif ext == "mp4":
        fakes.fake_video(os.path.join(folder, name))
    elif ext == "mp3":
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"ID3" + b"\x00" * 32)
    with open(os.path.join(folder, name + ".json" if ext else name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _stamp(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


X_USERS = {
    "tram_spotter": {"id": 4100001, "name": "tram_spotter", "nick": "Tram Spotter"},
    "kiln_notes": {"id": 4100002, "name": "kiln_notes", "nick": "Kiln Notes"},
    "junipervale": {"id": 4100003, "name": "junipervale", "nick": "Juniper Vale"},
}


def add_gallery_dl(media, ts):
    """A few X and TikTok posts laid out as gallery-dl writes them, from the
    shapes in backend/tests/fixtures/gallery_dl (text and people invented)."""
    base = os.path.join(media, "gallery-dl")

    def tweet(folder_user, tid, author, text, when, files, **extra):
        tpl = _gdl_template("twitter/photo")
        common = {k: v for k, v in tpl.items()
                  if k not in ("filename", "extension", "type", "width", "height", "description", "num")}
        user = X_USERS[folder_user]
        common.update(tweet_id=tid, conversation_id=tid, date=_stamp(when), content=text,
                      author={**tpl["author"], **X_USERS[author]}, user={**tpl["user"], **user},
                      favorite_count=random.Random(tid).randint(3, 900), reply_count=random.Random(tid).randint(0, 40),
                      count=len(files), hashtags=[t[1:] for t in text.split() if t.startswith("#")], **extra)
        folder = os.path.join(base, "twitter", user["name"])
        if not files:                                       # text-only: the "event": "post" JSON
            _gdl_write(folder, f"{tid}.json", common)
        for num, (ext, ftype) in enumerate(files, 1):
            d = {"filename": f"demo{tid}{num}", "extension": ext, "type": ftype, "width": 1080, "height": 1350,
                 "description": None, **common, "num": num}
            _gdl_write(folder, f"{tid}_{num}.{ext}", d, ext)

    t = ts + 3 * 86_400
    tweet("tram_spotter", 1790000000000000101, "tram_spotter",
          "Night line 12, last run. Rain on the windows. #trams #nightphotography", t, [("jpg", "photo")])
    tweet("tram_spotter", 1790000000000000102, "tram_spotter",
          "Depot open day: four of the old ones lined up 🚋 #trams", t + 7_200,
          [("jpg", "photo")] * 4)
    tweet("tram_spotter", 1790000000000000103, "tram_spotter",
          "Short clip: the 1950s car pulling out of the depot", t + 20_000,
          [("mp4", "video"), ("jpg", "preview")], view_count=12_400)
    tweet("tram_spotter", 1790000000000000104, "tram_spotter",
          "Does anyone know why line 4 skips the bridge stop on Sundays?", t + 40_000, [])
    tweet("tram_spotter", 1790000000000000105, "kiln_notes",
          "RT @kiln_notes: Shino glaze, second firing. Happy with this one. #ceramics", t + 60_000,
          [("jpg", "photo"), ("jpg", "photo")], retweet_id=1790000000000000099, date_original=_stamp(t - 86_400))

    def tiktok(pid, text, when, files, post_type, who=("6900000000000000001", "lo.fi.garden", "lo-fi garden")):
        tpl = _gdl_template("tiktok/video", 1)
        author = {**tpl["author"], "id": who[0], "uniqueId": who[1], "nickname": who[2], "signature": ""}
        common = {**tpl, "id": pid, "desc": text, "createTime": str(when), "date": _stamp(when), "author": author,
                  "user": who[1], "post_type": post_type, "textExtra": [], "challenges": [],
                  "stats": {"diggCount": random.Random(pid).randint(100, 90_000), "shareCount": 12,
                            "commentCount": random.Random(pid).randint(0, 300),
                            "playCount": random.Random(pid).randint(1_000, 900_000), "collectCount": "40"}}
        common["video"] = {**tpl["video"], "id": pid}
        folder = os.path.join(base, "tiktok", who[1])
        for num, (ext, ftype, file_id) in enumerate(files, 1):
            n = num if ftype == "image" else 0
            d = {**common, "filename": f"demo{pid}{num}", "extension": ext, "type": ftype, "num": n,
                 "file_id": file_id, "title": text or f"TikTok {'photo' if ftype == 'image' else ftype} #{pid}"}
            title = d["title"][:40]
            stem = f"{pid}{'_%02d' % n if n else ''} {title}{f' [{file_id}]' if file_id else ''}"
            _gdl_write(folder, f"{stem}.{ext}", d, ext)

    tiktok("7300000000000000201", "Tomato harvest timelapse 🍅 #garden #timelapse", t + 80_000,
           [("mp4", "video", ""), ("jpg", "cover", "cover")], "video")
    tiktok("7300000000000000202", "", t + 90_000,
           [("jpg", "image", "a1b2c3"), ("jpg", "image", "d4e5f6"), ("jpg", "image", "a7b8c9"),
            ("mp3", "audio", "7300000000000000999")], "image")

    # Juniper Vale (see add_person) on X and TikTok, under other handles.
    tweet("junipervale", 1790000000000000201, "junipervale",
          "Kiln day. Twelve mugs in, fingers crossed. #ceramics", t + 100_000, [("jpg", "photo")])
    tweet("junipervale", 1790000000000000202, "junipervale",
          "Repotted the monstera. It did not thank me.", t + 130_000, [])
    juni = ("6900000000000000002", "juni.vale", "Juniper Vale")
    tiktok("7300000000000000301", "Throwing a mug in 40 seconds #pottery", t + 110_000,
           [("mp4", "video", ""), ("jpg", "cover", "cover")], "video", juni)
    tiktok("7300000000000000302", "Watering day 🌿 #plants", t + 150_000,
           [("mp4", "video", ""), ("jpg", "cover", "cover")], "video", juni)


def add_person(media, ts):
    """One person across platforms for the People views: Juniper Vale is
    @juniper.makes on Instagram (called @juni.studio before a rename, so the
    older posts sit in that folder), @junipervale on X and @juni.vale on
    TikTok (add_gallery_dl). The Instagram profile file links to both, which
    is what the link suggestions read; nothing is linked yet."""
    uid, name = 9006, "Juniper Vale"
    for i, (handle, caption) in enumerate([
            ("juni.studio", "First market stall! Small batch of planters. #ceramics"),
            ("juni.studio", "Glaze tests, round two. #pottery"),
            ("juniper.makes", "New name, same mud. I'm @juniper.makes now 🌱"),
            ("juniper.makes", "Planters for the spring market. #ceramics #plants"),
            ("juniper.makes", "Studio cat inspecting the drying rack.")]):
        fakes.write_post(os.path.join(media, handle), f"DEMOjuni{i:04d}", ts - (60 - 10 * i) * 86_400,
                         fakes.owner(handle, uid, name), kind="image", caption=caption)
    fakes.write_meta(os.path.join(media, "juniper.makes", f"juniper.makes_{uid}"),
                     {"id": str(uid), "username": "juniper.makes", "full_name": name,
                      "biography": "Pots and plants. Slow videos on TikTok.",
                      "external_url": "https://x.com/junipervale",
                      "bio_links": [{"title": "TikTok", "url": "https://www.tiktok.com/@juni.vale"}]},
                     node_type="Profile", compress=True)


def seed_tags(data, tagged):
    """User data files the app imports on its first start (an existing demo's
    own tags are left alone)."""
    folder = os.path.join(data, "userdata")
    if os.path.exists(os.path.join(folder, "post_tags.json")):
        return
    os.makedirs(folder, exist_ok=True)
    names = sorted({t for _, t in tagged})
    with open(os.path.join(folder, "tags.json"), "w") as f:
        json.dump({"version": 1, "rows": [{"name": n, "color": None, "created_at": 1_700_000_000} for n in names]}, f)
    with open(os.path.join(folder, "post_tags.json"), "w") as f:
        json.dump({"version": 1, "rows": [{"post_id": p, "tag": t, "at": 1_700_000_000} for p, t in tagged]}, f)


TESTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "tests")


def add_fake_tools(root, ts):
    """gallery-dl and yt-dlp stand-ins (backend/tests/fake_downloaders.py),
    with a few invented profiles to sync. Returns the config's tools."""
    day = 86_400
    accounts = {
        # Every third post a retweet (not on the media tab), one a reply, one a video.
        "https://x.com/demo_skies": {
            "category": "twitter", "user": {"id": 7001, "name": "demo_skies", "nick": "Demo Skies"},
            "posts": [{"id": str(1900000000000000000 + i), "ts": ts - i * day, "text": f"evening sky, take {i}",
                       "files": 1 + i % 3, "video": i == 2,
                       "in": ["with-replies"] if i == 5 else ["timeline", "tweets"] if i % 3 == 0
                       else ["timeline", "tweets", "media"]} for i in range(1, 9)]},
        "https://tiktok.com/@demo.clips": {
            "extractor_key": "TikTok", "uploader_id": "6900000000000000777", "uploader": "demo.clips",
            "channel": "Demo Clips", "uploader_url": "https://www.tiktok.com/@demo.clips",
            "videos": [{"id": str(7400000000000000000 + i), "ts": ts - i * day, "title": f"clip {i}",
                        "description": f"clip {i} #demo", "duration": 15} for i in range(1, 5)]},
        # Account health: one that went private, one whose output is nothing known.
        "https://tiktok.com/@demo.hidden": {
            "extractor_key": "TikTok", "uploader_id": "6900000000000000999", "uploader": "demo.hidden",
            "fail": "private", "videos": []},
        "https://x.com/demo_oops": {
            "category": "twitter", "user": {"id": 7002, "name": "demo_oops", "nick": "Demo Oops"}, "fail": "odd",
            "posts": []},
        "https://youtube.com/@demoshorts": {
            "extractor_key": "Youtube", "uploader_id": "@demoshorts", "uploader": "demoshorts",
            "channel": "Demo Shorts", "channel_id": "UCdemoShortsChannel00001",
            "uploader_url": "https://www.youtube.com/@demoshorts",
            "videos": [{"id": f"DEMOshort{i:02d}", "ts": ts - i * day, "title": f"short {i}",
                        "duration": 40 if i % 4 else 1200} for i in range(1, 6)]},
    }
    instaloader = add_fake_instaloader(root, ts)
    data = os.path.join(root, "fake_downloads.json")
    with open(data, "w") as f:
        json.dump({"accounts": accounts, "fail": None}, f, indent=1)
    bin_dir = os.path.join(root, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    tools = {}
    for tool, main in (("gallery-dl", "gallery_dl_main"), ("yt-dlp", "yt_dlp_main")):
        exe = os.path.join(bin_dir, tool)
        with open(exe, "w") as f:
            f.write(f"#!{sys.executable}\nimport os, sys\nos.environ.setdefault('FAKE_DOWNLOADS', {data!r})\n"
                    f"sys.path.insert(0, {os.path.abspath(TESTS)!r})\nimport fake_downloaders\n"
                    f"sys.exit(fake_downloaders.{main}(sys.argv[1:]))\n")
        os.chmod(exe, 0o755)
        tools[tool] = exe
    return {**tools, "instaloader": instaloader}


def add_fake_instaloader(root, ts):
    """An instaloader stand-in (backend/tests/fake_instaloader.py) with one
    profile, demo.reels, that has every kind instaloader downloads. Any
    session "works" with it: logged in or not is all it checks."""
    day = 86_400

    def post(code, days_ago, kind="image", **extra):
        return {"shortcode": code, "ts": ts - days_ago * day, "caption": f"{code[-3:]} from the demo", "kind": kind,
                **extra}
    profile = {
        "id": 7100, "name": "Demo Reels", "bio": "invented: posts, reels, stories, highlights and tags",
        # A carousel with a video slide, for "videos only" (its pictures stay behind).
        "posts": [post("DEMOcarou01", 2, "carousel", slides=3, video_slides=[2])]
        + [post(f"DEMOpost{i:03d}", i * 20, "video" if i % 3 == 0 else "image") for i in range(1, 9)],
        "reels": [post(f"DEMOreel{i:03d}", i * 25 + 3, "video") for i in range(1, 5)],
        "tagged": [post("DEMOtagd001", 12,
                        owner={"username": "mossy.trails", "id": "9001", "full_name": "Mossy Trails"})],
        "stories": [{"id": 3100000000000000000 + i, "ts": ts - i * 3600, "video": i == 2} for i in range(1, 4)],
        "highlights": [{"title": "Kilns", "items": [{"id": 3200000000000000001, "ts": ts - 90 * day,
                                                     "video": False}]}],
    }
    # Account health (seed_sources): Instagram's side of the demo creators
    # with a source. pixel_bakery wants a login, mossy.trails is rate
    # limited, night.tram is back (its last sync did not find it; a Sync
    # works), quiet_kiln is now quiet.kiln.studio (found by the id the
    # stamps file keeps, as instaloader does).
    profiles = {
        "demo.reels": profile,
        "pixel_bakery": {"id": 9002, "fail": "login", "posts": []},
        "mossy.trails": {"id": 9001, "fail": "429", "posts": []},
        "night.tram": {"id": 9003, "name": "Night Tram", "posts": [post("DEMOtram001", 1)]},
        "quiet.kiln.studio": {"id": 9004, "name": "Quiet Kiln Ceramics", "posts": [post("DEMOkiln001", 1)]},
    }
    data = os.path.join(root, "fake_instaloader.json")
    with open(data, "w") as f:
        json.dump({"profiles": profiles, "fail": None, "delay": 0}, f, indent=1)
    exe = os.path.join(root, "bin", "instaloader")
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    with open(exe, "w") as f:
        f.write(f"#!{sys.executable}\nimport os, sys\nos.environ.setdefault('FAKE_INSTALOADER', {data!r})\n"
                f"os.environ.setdefault('FAKE_INSTALOADER_SESSION', '1')\n"
                f"sys.path.insert(0, {os.path.abspath(TESTS)!r})\nimport fake_instaloader\n"
                f"sys.exit(fake_instaloader.main(sys.argv[1:]))\n")
    os.chmod(exe, 0o755)
    return exe


def seed_sources(data, media):
    """A source with options, so the Sources page shows one: demo_skies'
    media tab, images only, nothing before 2023-11-01, synced daily (by the
    fake gallery-dl add_fake_tools sets: the scheduler syncs it once the
    demo starts). Then one source per account health state, each as its
    last sync left it, and the fake tools answer a Sync the same way (see
    add_fake_tools and add_fake_instaloader)."""
    out = os.path.join(data, "userdata", "sources.json")
    if os.path.exists(out):
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    options = {"full_history": False, "session": None, "content": ["media"], "media": "images",
               "since": "2023-11-01", "first_posts": None, "schedule": "daily"}
    rows = [{
        "tool": "gallery-dl", "target": "https://x.com/demo_skies", "platform": "twitter", "author_id": None,
        "person": None, "folder": os.path.join(media, "twitter", "demo_skies"), "options": json.dumps(options),
        "created_at": 1_700_000_000, "last_sync_at": None, "last_result": None}]
    now = int(time.time())
    day = 86_400
    good = now - 9 * day                           # their last sync that worked

    def failed(health, line, failures, at, message):
        return {"state": "failed", "error": health if health != "error" else "generic", "message": message,
                "line": line, "added": 0, "job": None, "outdated": False, "failures": failures, "health": health,
                "ok_at": good, "login": {"mode": "none", "found": None, "accepted": None}}
    states = [
        # (tool, target, platform, author, folder, schedule, last sync, last_result)
        ("instaloader", "night.tram", "instagram", "9003", "night.tram", "daily", now - 2 * day,
         failed("not_found", "Profile night.tram does not exist.", 2, now - 2 * day,
                "Profile not found: renamed, deleted, or blocked")),
        ("instaloader", "pixel_bakery", "instagram", "9002", "pixel_bakery", "daily", now - day,
         {**failed("login_required", "pixel_bakery: Login required.", 1, now - day,
                   "Instagram wants a logged-in session for this; see Settings"),
          "login": {"mode": "login", "found": True, "accepted": False}}),
        ("instaloader", "mossy.trails", "instagram", "9001", "mossy.trails", "hourly", now - 3600,
         failed("rate_limited", "mossy.trails: Please wait a few minutes before you try again.", 4, now - 3600,
                "Instagram is limiting requests: wait a while before syncing again")),
        ("instaloader", "quiet_kiln", "instagram", "9004", "quiet_kiln", "off", now - 3 * day,
         {"state": "done", "error": None, "message": "1 new post; the profile is now called quiet.kiln.studio "
          "(accept the new name on the source)", "line": None, "added": 1, "job": None, "outdated": False,
          "failures": 0, "health": "renamed", "ok_at": now - 3 * day,
          "login": {"mode": "none", "found": None, "accepted": None},
          "rename": {"from": "quiet_kiln", "to": "quiet.kiln.studio", "at": now - 3 * day}}),
        ("yt-dlp", "https://tiktok.com/@demo.hidden", "tiktok", None, "tiktok/demo.hidden", "off", now - 5 * day,
         failed("private", "ERROR: [tiktok:user] demo.hidden: This user's account is private. Log into an account "
                "that has access", 1, now - 5 * day, "Private profile: the session in use does not follow it")),
        ("gallery-dl", "https://x.com/demo_oops", "twitter", None, "twitter/demo_oops", "off", now - 6 * day,
         failed("error", "[twitter][error] HttpError: '500 Internal Server Error' for 'https://api.x.com/graphql'",
                3, now - 6 * day, "gallery-dl failed")),
    ]
    for tool, target, platform, author, folder, schedule, at, result in states:
        rows.append({"tool": tool, "target": target, "platform": platform, "author_id": author, "person": None,
                     "folder": os.path.join(media, folder),
                     "options": json.dumps({**options, "content": None, "media": "all", "since": None,
                                            "schedule": schedule,
                                            # A saved login the site turns away (the fake's "login").
                                            "session": {"mode": "login", "user": "demo.me"}
                                            if target == "pixel_bakery" else None}),
                     "created_at": 1_700_000_000, "last_sync_at": at, "last_result": json.dumps(result)})
    with open(out, "w") as f:
        json.dump({"version": 1, "rows": rows}, f)
    # The id instaloader keeps for quiet_kiln, which finds it under its new name.
    stamps = os.path.join(data, "instaloader", "stamps.ini")
    os.makedirs(os.path.dirname(stamps), exist_ok=True)
    with open(stamps, "w") as f:
        f.write(f"[quiet_kiln]\nprofile-id = 9004\npost-timestamp = "
                f"{datetime.fromtimestamp(now - 30 * day, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f%z')}\n")


def seed_news(config_path):
    """Index the demo now, so it starts with new posts: the newest of
    lo.fi.garden's and quiet_kiln's posts came after the "Mark all seen"
    mark, and lo.fi.garden's arrived with a sync that left a notification
    entry. Needs the backend's dependencies (its virtualenv); without them
    the demo starts with nothing new."""
    os.environ["FEEDVAULT_CONFIG"] = config_path
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    try:
        import config
        import db
        import news
        import notify
        import scanner
        import userdata
    except ImportError as e:
        print(f"Not indexed now ({e}): run it with backend/venv/bin/python for new posts in the demo")
        return
    cfg = config.load()
    db.init(config.db_path(cfg))
    conn = db.connect()
    userdata.restore_all(conn, cfg["data_directory"])
    scanner.scan(cfg["media_roots"])           # built from nothing: nothing new
    now = int(time.time())
    synced, looked = now - 3600, now - 2 * 3600
    with conn:
        conn.execute("INSERT INTO seen_at(id, at) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET at = excluded.at",
                     (looked,))
        for handle, n, at in (("lo.fi.garden", 4, synced), ("quiet_kiln", 2, now - 1800)):
            conn.execute("UPDATE posts SET first_seen = ? WHERE id IN (SELECT id FROM posts WHERE meta_path LIKE ? "
                         "ORDER BY posted_at DESC LIMIT ?)",
                         (at, os.path.join(cfg["media_roots"][0], handle) + os.sep + "%", n))
    notify.add(conn, "new", "4 new posts from @lo.fi.garden", account=("instagram", "9005"), state="ok", count=4,
               folder=os.path.join(cfg["media_roots"][0], "lo.fi.garden"), seen=(synced, synced), scheduled=True,
               at=synced)
    userdata.export(conn, "seen_at", cfg["data_directory"])
    print(f"Indexed: {news.count(conn)[0]} new posts, 1 notification")


def add_old_yt_dlp_sync(media, root, ts):
    """A TikTok profile yt-dlp downloaded with a browser's cookies before
    FeedVault removed them: its info JSONs still hold the (made-up) cookies,
    for Settings → "Remove cookies from existing yt-dlp info JSONs"."""
    import fake_downloaders
    data = os.path.join(root, "old_sync.json")
    with open(data, "w") as f:
        json.dump({"accounts": {"https://tiktok.com/@old.sync": {
            "extractor_key": "TikTok", "uploader_id": "6900000000000000888", "uploader": "old.sync",
            "channel": "Old Sync", "uploader_url": "https://www.tiktok.com/@old.sync",
            "videos": [{"id": str(7400000000000000100 + i), "ts": ts - (30 + i) * 86_400, "title": f"older clip {i}",
                        "description": f"older clip {i} #demo", "duration": 15} for i in range(1, 4)]}}}, f)
    os.environ["FAKE_DOWNLOADS"] = data
    try:
        fake_downloaders.yt_dlp_main([
            "--write-info-json", "--write-thumbnail", "--cookies-from-browser", "firefox",
            "-o", os.path.join(media, "tiktok", "old.sync", "%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s"),
            "https://tiktok.com/@old.sync"])
    finally:
        del os.environ["FAKE_DOWNLOADS"]
        os.remove(data)


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
    tagged = []                                   # (post id, tag): a few, so the tag views have something
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
        if handle == "quiet_kiln":
            tagged.append((f"instagram:{shortcode}", "ceramics"))
        if rng.random() < 0.2:
            tagged.append((f"instagram:{shortcode}", "favourites"))
    # A stray file so the Unmatched page has something to show.
    fakes.png(os.path.join(media, "pixel_bakery", "screenshot_from_phone.png"), (200, 60, 60))
    add_duplicates(media, ts)
    add_gallery_dl(media, ts)
    add_person(media, ts)
    add_old_yt_dlp_sync(media, root, ts)

    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    seed_tags(os.path.join(root, "data"), tagged)
    seed_sources(os.path.join(root, "data"), media)
    tools = add_fake_tools(root, ts)
    with open(os.path.join(root, "config.json"), "w") as f:
        json.dump({"data_directory": os.path.join(root, "data"), "media_roots": [media], "tools": tools}, f, indent=2)
    seed_news(os.path.join(root, "config.json"))
    print(f"Demo vault at {root} ({'real' if have_ffmpeg else 'placeholder'} videos)")
    print(f"Start: FEEDVAULT_CONFIG={root}/config.json backend/venv/bin/python backend/app.py")


if __name__ == "__main__":
    main()
