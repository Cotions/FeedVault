"""A large synthetic vault for performance work (make_demo.py --large N).

Adds, beside the regular demo, N invented posts across some 300 accounts
on Instagram (instaloader layout), X and TikTok (gallery-dl) and YouTube
(yt-dlp), about 2.2 media files a post: tiny PNGs of random pixels (each
file different, each with a real dHash) and placeholder videos. Then the
user data an archive this size has: some 50 people linked to accounts,
tags, collections, review decisions, saved links; extra copies of posts
in a typo'd folder, reposts sharing a file and resized reposts (the
Duplicates page); stray files (Unmatched); and, once indexed, posts in
the trash.

Every handle, name, caption and address is made up; nothing is fetched.
Deterministic: the same N gives the same vault.
"""
import json
import os
import random
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "tests"))
import fakes  # noqa: E402

ACCOUNTS = 300
PEOPLE = 50
WORDS = ("moss fog ridge bakery sourdough tram night kiln glaze garden tomato carousel rain tea book station "
         "macro market croissant bowl clay river harbour bridge lantern meadow orchard pebble quilt saffron "
         "thistle umbra velvet willow yarrow zinnia amber birch cedar dune ember fern grove heron iris").split()
TAGS = ["favourites", "to-print", "inspiration", "recipes", "travel", "ceramics", "night", "street", "nature",
        "portraits", "archive", "to-check", "best-of-2023", "best-of-2024", "funny", "tutorial", "music",
        "architecture", "food", "pets", "sky", "sea", "mountains", "city", "black-and-white", "colour",
        "film", "macro", "people", "events", "reference", "work", "home", "garden", "shop", "wishlist",
        "later", "shared", "saved-for-mum", "misc"]


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def _video(path, seed):
    _write(path, b"\x00\x00\x00\x18ftypmp42" + seed.to_bytes(8, "big") + b"\x00" * 56)


def _caption(rng, handle):
    words = rng.sample(WORDS, rng.randint(3, 12))
    tags = " ".join("#" + w for w in rng.sample(WORDS, rng.randint(0, 3)))
    mention = f" @{handle}" if rng.random() < 0.05 else ""
    return (" ".join(words).capitalize() + ". " + tags + mention).strip()


def _accounts(rng):
    """[(platform, handle, id, name)], ACCOUNTS of them, mostly Instagram."""
    out = []
    for i in range(ACCOUNTS):
        r = i % 20
        platform = "instagram" if r < 13 else "twitter" if r < 16 else "tiktok" if r < 19 else "youtube"
        a, b = rng.choice(WORDS), rng.choice(WORDS)
        handle = f"{a}.{b}{i}" if platform in ("instagram", "tiktok") else f"{a}_{b}{i}"
        uid = {"instagram": 20_000_000 + i, "twitter": 4_200_000_000 + i, "tiktok": 6_950_000_000_000_000_000 + i,
               "youtube": f"UClarge{i:017d}"}[platform]
        out.append((platform, handle, uid, f"{a.title()} {b.title()} {i}"))
    return out


def _stamp(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _template(case, n=0):
    folder = os.path.join(fakes.GALLERY_DL, case)
    with open(os.path.join(folder, sorted(x for x in os.listdir(folder) if x.endswith(".json"))[n])) as f:
        return json.load(f)


def _yt_template():
    folder = os.path.join(fakes.YT_DLP, "youtube", "video")
    with open(os.path.join(folder, next(n for n in os.listdir(folder) if n.endswith(".info.json")))) as f:
        return json.load(f)


class Builder:
    def __init__(self, media, n):
        self.media, self.n = media, n
        self.rng = random.Random(1234)
        self.seed = 0
        self.files = 0
        self.posts = []                         # (post id, platform, account id, folder, instaloader base)
        self.first = {}                         # instaloader base: the seed of its first picture
        self.tw, self.tt, self.yt = _template("twitter/photo"), _template("tiktok/video", 1), _yt_template()

    def image(self, path):
        self.seed += 1
        fakes.noise_png(path, self.seed)
        self.files += 1
        return self.seed

    def video(self, path):
        self.seed += 1
        _video(path, self.seed)
        self.files += 1

    # -- one post per platform ---------------------------------------------

    def instagram(self, acc, ts):
        _, handle, uid, name = acc
        folder = os.path.join(self.media, "instagram", handle)
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, fakes.base_name(ts))
        kind = self.rng.choices(["image", "carousel", "video"], weights=[5, 3, 2])[0]
        code = "L" + "".join(self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz0123456789")
                             for _ in range(10))
        caption = _caption(self.rng, handle)
        slides = None
        if kind == "image":
            first = self.image(base + ".jpg")
            typename = "GraphImage"
        elif kind == "video":
            first = self.image(base + ".jpg")
            self.video(base + ".mp4")
            typename = "GraphVideo"
        else:
            slides = [self.rng.random() < 0.2 for _ in range(self.rng.randint(2, 7))]
            for i, is_video in enumerate(slides, 1):
                seed = self.image(f"{base}_{i}.jpg")
                first = seed if i == 1 else first
                if is_video:
                    self.video(f"{base}_{i}.mp4")
            typename = "GraphSidecar"
        node = fakes.post_node(code, ts, fakes.owner(handle, uid, name), caption, typename, children=slides,
                               likes=self.rng.randint(0, 50_000), comments=self.rng.randint(0, 900),
                               views=self.rng.randint(100, 900_000) if kind == "video" else None,
                               product_type="clips" if kind == "video" else None)
        if caption:
            with open(base + ".txt", "w") as f:
                f.write(caption)
        fakes.write_meta(base, node, compress=self.rng.random() < 0.05)
        self.posts.append((f"instagram:{code}", "instagram", str(uid), folder, base))
        self.first[base] = first

    def twitter(self, acc, ts):
        _, handle, uid, name = acc
        tid = 1_800_000_000_000_000_000 + self.seed
        text = _caption(self.rng, handle)
        common = {k: v for k, v in self.tw.items()
                  if k not in ("filename", "extension", "type", "width", "height", "description", "num")}
        user = {**self.tw["user"], "id": uid, "name": handle, "nick": name}
        common.update(tweet_id=tid, conversation_id=tid, date=_stamp(ts), content=text,
                      author={**self.tw["author"], "id": uid, "name": handle, "nick": name}, user=user,
                      favorite_count=self.rng.randint(0, 9000), reply_count=self.rng.randint(0, 90),
                      hashtags=[t[1:] for t in text.split() if t.startswith("#")])
        folder = os.path.join(self.media, "gallery-dl", "twitter", handle)
        os.makedirs(folder, exist_ok=True)
        count = self.rng.choice([0, 1, 1, 1, 2, 4])
        common["count"] = count
        if not count:
            with open(os.path.join(folder, f"{tid}.json"), "w") as f:
                json.dump(common, f)
        for num in range(1, count + 1):
            name_ = f"{tid}_{num}.jpg"
            self.image(os.path.join(folder, name_))
            with open(os.path.join(folder, name_ + ".json"), "w") as f:
                json.dump({"filename": f"large{tid}{num}", "extension": "jpg", "type": "photo", "width": 16,
                           "height": 16, "description": None, **common, "num": num}, f)
        self.posts.append((f"twitter:{tid}", "twitter", str(uid), folder, None))

    def tiktok(self, acc, ts):
        _, handle, uid, name = acc
        pid = str(7_500_000_000_000_000_000 + self.seed)
        text = _caption(self.rng, handle)
        tpl = self.tt
        author = {**tpl["author"], "id": str(uid), "uniqueId": handle, "nickname": name, "signature": ""}
        common = {**tpl, "id": pid, "desc": text, "createTime": str(ts), "date": _stamp(ts), "author": author,
                  "user": handle, "post_type": "video", "textExtra": [], "challenges": [],
                  "stats": {"diggCount": self.rng.randint(0, 90_000), "shareCount": 3, "commentCount": 4,
                            "playCount": self.rng.randint(1000, 900_000), "collectCount": "4"},
                  "video": {**tpl["video"], "id": pid}}
        folder = os.path.join(self.media, "gallery-dl", "tiktok", handle)
        os.makedirs(folder, exist_ok=True)
        title = text[:40]
        for ext, ftype, file_id in (("mp4", "video", ""), ("jpg", "cover", "cover")):
            stem = f"{pid} {title}{f' [{file_id}]' if file_id else ''}"
            path = os.path.join(folder, f"{stem}.{ext}")
            self.video(path) if ext == "mp4" else self.image(path)
            with open(path + ".json", "w", encoding="utf-8") as f:
                json.dump({**common, "filename": f"large{pid}", "extension": ext, "type": ftype, "num": 0,
                           "file_id": file_id, "title": text}, f, ensure_ascii=False)
        self.posts.append((f"tiktok:{pid}", "tiktok", str(uid), folder, None))

    def youtube(self, acc, ts):
        _, handle, uid, name = acc
        vid = f"L{self.seed:010d}"
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d")
        folder = os.path.join(self.media, "youtube", handle)
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, f"@{handle}-{day}-{vid}")
        title = _caption(self.rng, handle)[:60]
        data = {**self.yt, "id": vid, "display_id": vid, "title": title, "fulltitle": title,
                "description": _caption(self.rng, handle), "channel_id": uid, "channel": name,
                "channel_url": f"https://www.youtube.com/channel/{uid}", "uploader": handle,
                "uploader_id": f"@{handle}", "uploader_url": f"https://www.youtube.com/@{handle}",
                "webpage_url": f"https://www.youtube.com/watch?v={vid}", "upload_date": day, "timestamp": ts,
                "view_count": self.rng.randint(10, 2_000_000), "like_count": self.rng.randint(0, 9000),
                "duration": self.rng.randint(20, 3000)}
        with open(base + ".info.json", "w") as f:
            json.dump(data, f)
        self.video(f"{base}.{data['ext']}")
        self.image(base + ".jpg")
        self.posts.append((f"youtube:{vid}", "youtube", uid, folder, None))

    # -- the whole vault -----------------------------------------------------

    def build(self):
        accounts = _accounts(self.rng)
        # Most posts from a few accounts, as in a real archive.
        weights = [1 / (i + 3) for i in range(len(accounts))]
        ts = 1_500_000_000
        make = {"instagram": self.instagram, "twitter": self.twitter, "tiktok": self.tiktok,
                "youtube": self.youtube}
        for i in range(self.n):
            ts += self.rng.randint(60, 9_000)
            # A post for each account first, then most for the busy ones.
            acc = accounts[i] if i < len(accounts) else self.rng.choices(accounts, weights)[0]
            make[acc[0]](acc, ts)
        self.profiles(accounts)
        self.duplicates(ts)
        self.strays()
        return accounts

    def profiles(self, accounts):
        """Instagram profile files linking to an X or TikTok account: what the
        People page's link suggestions read."""
        others = [a for a in accounts if a[0] in ("twitter", "tiktok")]
        for i, (platform, handle, uid, name) in enumerate(a for a in accounts if a[0] == "instagram"):
            folder = os.path.join(self.media, "instagram", handle)
            if i % 3 or not os.path.isdir(folder):
                continue
            other = others[i % len(others)]
            url = (f"https://x.com/{other[1]}" if other[0] == "twitter" else f"https://www.tiktok.com/@{other[1]}")
            fakes.write_meta(os.path.join(folder, f"{handle}_{uid}"),
                             {"id": str(uid), "username": handle, "full_name": name, "biography": "invented",
                              "external_url": url, "bio_links": []}, node_type="Profile", compress=True)

    def duplicates(self, ts):
        """Copies of Instagram posts in typo'd folders (copies), reposts
        sharing a file under another account (content), and resized
        reposts (similar)."""
        insta = [p for p in self.posts if p[1] == "instagram"]
        rng = self.rng
        for pid, _, _, folder, base in rng.sample(insta, min(150, len(insta))):
            dst = folder + "_old"
            os.makedirs(dst, exist_ok=True)
            stem = os.path.basename(base)
            for n in os.listdir(folder):
                if n.startswith(stem):
                    shutil.copy2(os.path.join(folder, n), os.path.join(dst, n))
                    self.files += n.endswith((".jpg", ".mp4"))
        accounts = sorted({(p[2], p[3]) for p in insta})
        for i, (pid, _, _, folder, base) in enumerate(rng.sample(insta, min(400, len(insta)))):
            src = next((base + s for s in (".jpg", "_1.jpg") if os.path.exists(base + s)), None)
            if src is None:
                continue
            uid, other = accounts[(i * 7) % len(accounts)]
            if other == folder:
                continue
            ts += 7
            handle = os.path.basename(other)
            new = fakes.write_post(other, f"LREPOST{i:05d}", ts, fakes.owner(handle, uid), kind="image",
                                   caption=f"repost {i}")
            if i % 4 == 0:                      # resized: similar, not identical
                fakes.noise_png(new + ".jpg", self.first[base], 2)
            else:
                shutil.copyfile(src, new + ".jpg")
            self.files += 1

    def strays(self):
        folder = os.path.join(self.media, "loose files")
        os.makedirs(folder, exist_ok=True)
        for i in range(250):
            self.image(os.path.join(folder, f"IMG_{i:05d}.png"))


def build(media, n):
    """Write the large vault's posts under ``media``. Returns the builder."""
    b = Builder(media, n)
    b.accounts = b.build()
    return b


def seed_userdata(data, b):
    """People, links between them and accounts, tags, collections, decisions
    and links, as user data files the app imports on its first start (added
    to the regular demo's)."""
    rng = random.Random(99)
    folder = os.path.join(data, "userdata")
    os.makedirs(folder, exist_ok=True)

    def rows(name):
        p = os.path.join(folder, f"{name}.json")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)["rows"]
        return []

    def write(name, out):
        with open(os.path.join(folder, f"{name}.json"), "w") as f:
            json.dump({"version": 1, "rows": out}, f)

    at = 1_700_000_000
    people, person_accounts = rows("people"), rows("person_accounts")
    pool = b.accounts[:]
    rng.shuffle(pool)
    for i in range(PEOPLE):
        name = f"Large Person {i:02d}"
        people.append({"name": name, "notes": "", "created_at": at + i})
        for platform, _, uid, _ in pool[i * 3: i * 3 + rng.randint(1, 3)]:
            person_accounts.append({"platform": platform, "author_id": str(uid), "person": name, "at": at})
    write("people", people)
    write("person_accounts", person_accounts)

    ids = [p[0] for p in b.posts]
    tags, post_tags = rows("tags"), rows("post_tags")
    tags += [{"name": t, "color": None, "created_at": at} for t in TAGS]
    seen = {(r["post_id"], r["tag"]) for r in post_tags}
    for pid in rng.sample(ids, len(ids) // 4):
        for t in rng.sample(TAGS, rng.choice([1, 1, 1, 2, 3])):
            if (pid, t) not in seen:
                seen.add((pid, t))
                post_tags.append({"post_id": pid, "tag": t, "at": at})
    write("tags", list({t["name"]: t for t in tags}.values()))
    write("post_tags", post_tags)

    collections, collection_posts = rows("collections"), rows("collection_posts")
    for c in range(15):
        name = f"Collection {c:02d}"
        collections.append({"name": name, "cover_post": None, "created_at": at, "position": c + 1})
        for pos, pid in enumerate(rng.sample(ids, min(len(ids), rng.randint(20, 400)))):
            collection_posts.append({"collection": name, "post_id": pid, "position": pos, "at": at})
    write("collections", collections)
    write("collection_posts", collection_posts)

    write("decisions", [{"post_id": pid, "decision": "keep", "at": at} for pid in rng.sample(ids, len(ids) // 3)])

    links = rows("links")
    for i in range(300):
        person = f"Large Person {i % PEOPLE:02d}" if i % 3 == 0 else None
        links.append({"url": f"https://links-{i}.example/{rng.choice(WORDS)}", "title": f"Link {i}",
                      "notes": "", "person": person, "position": i // PEOPLE if person else None,
                      "created_at": at + i})
    write("links", links)


def trash_some(cfg, b, count=300):
    """Once indexed: move ``count`` posts to the trash, as the Delete button
    does (needs the backend's modules)."""
    import trash
    rng = random.Random(7)
    ids = [p[0] for p in b.posts if not p[0].startswith("youtube:")]
    picked = rng.sample(ids, min(count, len(ids)))
    for i in range(0, len(picked), 50):
        trash.delete(picked[i:i + 50], [], cfg["media_roots"], cfg["data_directory"])
    return len(picked)
