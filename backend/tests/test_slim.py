"""instaloader's metadata slimmed and compressed: the slim form
(parsers.instaloader.slim), folders holding ``.json`` and ``.json.xz``, the
clean-up job (slim.py) and the ``slim_metadata`` setting."""
import collections
import copy
import dataclasses
import json
import lzma
import os
import stat
import threading
import time

import pytest

import fake_instaloader
import instaloader_samples as samples
from conftest import H
from fakes import (GALLERY_DL, YT_DLP, base_name, gallery_dl_case, owner, png, post_node, write_meta, write_post,
                   yt_dlp_case)
from test_sync import (DAY, TS, add_source, carol_profile, ended, fake, get, post, set_config,  # noqa: F401
                       sync_now, wait_for)

import config
import db
import jobs
import save
import scanner
import scripts
import slim
import sync
from parsers import instaloader as parser

CAROL = owner("carol.cooks", 777, "Carol Cooks")
DAVE = owner("dave_draws", 888, "Dave Draws")

# Every shape the parser reads, full size (instaloader_samples) and as the
# other tests' fixtures write them (fakes.post_node, fake_instaloader.node).
SAMPLES = {
    "GraphImage": samples.post("CIMAGE00001", TS, CAROL, location="Lyon"),
    "GraphVideo": samples.post("CVIDEO00001", TS + 1, CAROL, "GraphVideo", views=5300),
    "reel": samples.post("CREEL000001", TS + 2, CAROL, "GraphVideo", views=91000, product_type="clips"),
    "GraphSidecar": samples.post("CSIDECAR001", TS + 3, CAROL, "GraphSidecar", slides=[False, True, False]),
    "tagged": samples.post("CTAGGED0001", TS + 4, DAVE, tagged=[CAROL]),
    "no caption": samples.post("CNOCAPTION1", TS + 5, CAROL, caption=""),
    "no iphone_struct": samples.post("CNOIPHONE01", TS + 6, CAROL, "GraphSidecar", slides=[False, False], iphone=False),
    "story image": samples.story_item(3391112223334445556, TS + 7, CAROL),
    "story video": samples.story_item(3391112223334445557, TS + 8, CAROL, video=True),
    "Profile": samples.profile(CAROL),
    "Profile, no links": samples.profile(DAVE, bio="", url=None, links=()),
    "old format": samples.old_post("BOLDPOST001", TS - 3000 * DAY, CAROL),
    "iphone_struct only": samples.iphone_only("CIPHONE0001", TS + 9, CAROL),
    "iphone_struct only, image": samples.iphone_only("CIPHONE0002", TS + 10, CAROL, video=False, caption=""),
    "fakes: image": {"node": post_node("FIMAGE00001", TS, CAROL, "hello #World", location="Lyon"),
                     "instaloader": {"version": "4.15.1", "node_type": "Post"}},
    "fakes: reel": {"node": post_node("FREEL000001", TS, CAROL, "", "GraphVideo", views=12, product_type="clips"),
                    "instaloader": {"version": "4.15.1", "node_type": "Post"}},
    "fakes: carousel": {"node": post_node("FCAROUSEL01", TS, CAROL, "x", "GraphSidecar", children=[False, True]),
                        "instaloader": {"version": "4.15.1", "node_type": "Post"}},
    "fake instaloader: carousel": {
        "node": fake_instaloader.node({"shortcode": "FAKECAR0001", "ts": TS, "caption": "c", "kind": "carousel"},
                                      {"id": 777, "name": "Carol Cooks"}, "carol.cooks"),
        "instaloader": {"version": "4.15.1", "node_type": "Post"}},
    "fake instaloader: story": {
        "node": {"__typename": "GraphStoryImage", "id": "4455", "taken_at_timestamp": TS, "is_video": False,
                 "owner": {"id": "777", "username": "carol.cooks", "full_name": "Carol Cooks"}},
        "instaloader": {"version": "4.15.1", "node_type": "StoryItem"}},
    "fake instaloader: profile": {
        "node": {"id": "777", "username": "carol.cooks", "biography": "bio", "external_url": "https://c.example/"},
        "instaloader": {"version": "4.15.1", "node_type": "Profile"}},
}
POSTS = [k for k, v in SAMPLES.items() if v["instaloader"]["node_type"] != "Profile"]
FULL = [k for k in SAMPLES if not k.startswith("fake")]


def plain(data):
    """As instaloader --no-compress-json wrote it."""
    return json.dumps(data, indent=4, sort_keys=True).encode()


def load(path):
    return parser._load(str(path))


# ---------------------------------------------------------------------------
# The slim form
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", SAMPLES)
def test_slim_reads_as_the_original(name, tmp_path):
    data = SAMPLES[name]
    before = copy.deepcopy(data)
    slimmed = parser.slim(data)
    assert data == before                                    # the original is not touched
    assert parser.reads(data) is not None
    assert parser.reads(slimmed) == parser.reads(data)
    assert slimmed["instaloader"] == data["instaloader"]
    assert parser.slim(slimmed) == slimmed                   # slim already: the same again
    assert parser.slim(json.loads(json.dumps(slimmed))) == slimmed

    # And through parse_dir, files and all: the same post from either file.
    def parsed(folder, structure, compress):
        folder.mkdir()
        base = "carol.cooks_777" if name.startswith("Profile") or name.endswith("profile") else base_name(TS)
        for n in (base + "_1.jpg", base + "_2.jpg", base + ".txt"):
            png(str(folder / n)) if n.endswith(".jpg") else (folder / n).write_text("caption")
        if compress:
            (folder / (base + ".json.xz")).write_bytes(slim.pack(structure))
        else:
            (folder / (base + ".json")).write_bytes(plain(structure))
        os.utime(folder / (base + (".json.xz" if compress else ".json")), (TS, TS))
        r = parser.parse_dir(str(folder), str(folder), sorted(os.listdir(folder)))
        text = json.dumps([[dataclasses.asdict(p) for p in r.posts], [dataclasses.asdict(p) for p in r.profiles],
                           sorted(n.replace(".json.xz", ".json") for n in r.claimed), r.errors])
        return json.loads(text.replace(str(folder), "").replace(".json.xz", ".json"))

    full = parsed(tmp_path / "full", data, False)
    assert full[0] or full[1]
    assert parsed(tmp_path / "slim", slimmed, True) == full


class Spy(dict):
    """A dict that notes every key read from it, and from what is in it."""

    def __init__(self, value, seen, path=()):
        super().__init__(value)
        self.seen, self.path = seen, path

    def _wrap(self, key, v):
        if isinstance(v, dict):
            return Spy(v, self.seen, self.path + (key,))
        if isinstance(v, list):
            return [self._wrap(key, x) for x in v[:1]] + v[1:] if key != "bio_links" else \
                [self._wrap(key, x) for x in v]
        return v

    def __getitem__(self, key):
        self.seen.add(self.path + (key,))
        return self._wrap(key, super().__getitem__(key))

    def get(self, key, default=None):
        self.seen.add(self.path + (key,))
        return self._wrap(key, super().get(key, default)) if key in self.keys() else default


def has(value, path):
    for k in path:
        if isinstance(value, list):
            value = value[0] if value else None
        if not isinstance(value, dict) or k not in value:
            return False
        value = value[k]
    return True


@pytest.mark.parametrize("name", SAMPLES)
def test_slim_keeps_every_field_the_parser_reads(name):
    """The rule comes from the code: whatever parse_dir's readers look up
    in a node and find there is in the slim form too."""
    data = SAMPLES[name]
    seen = set()
    assert parser.reads({"node": Spy(data["node"], seen), "instaloader": data["instaloader"]}) == parser.reads(data)
    assert seen
    slimmed = parser.slim(data)["node"]
    lost = sorted(p for p in seen if has(data["node"], p) and not has(slimmed, p))
    assert lost == []


def strings(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)
    elif isinstance(value, str):
        yield value


def test_what_slim_keeps_of_a_post():
    node = parser.slim(SAMPLES["GraphSidecar"])["node"]
    full = SAMPLES["GraphSidecar"]["node"]
    assert node["owner"] == {"id": "777", "username": "carol.cooks", "full_name": "Carol Cooks"}
    assert node["edge_media_to_caption"] == {"edges": [{"node": {"text": samples.CAPTION}}]}
    assert node["edge_media_to_comment"] == {"count": 56} and node["edge_media_preview_like"] == {"count": 1234}
    assert node["edge_liked_by"] == {"count": 1234}
    assert node["iphone_struct"] == {
        "user": {"pk": 777, "id": "777", "username": "carol.cooks", "full_name": "Carol Cooks"},
        "caption": {"text": samples.CAPTION}, "taken_at": TS + 3, "like_count": 1234, "comment_count": 56,
        "play_count": None}
    # The node's own short values stay though nothing reads them; the rest goes.
    for k in ("__typename", "id", "shortcode", "taken_at_timestamp", "is_video", "comments_disabled",
              "accessibility_caption", "viewer_has_liked", "title", "location"):
        assert node[k] == full[k], k
    for k in ("edge_sidecar_to_children", "display_url", "display_resources", "thumbnail_src", "thumbnail_resources",
              "media_preview", "dimensions", "edge_media_to_tagged_user", "sharing_friction_info",
              "edge_media_preview_comment", "edge_web_media_to_related_media", "pinned_for_users"):
        assert k in full and k not in node, k
    assert not [s for s in strings(node) if s.startswith("http")]
    assert parser.slim(SAMPLES["GraphImage"])["node"]["location"] == {"name": "Lyon"}


def test_what_slim_keeps_of_a_profile():
    node = parser.slim(SAMPLES["Profile"])["node"]
    full = SAMPLES["Profile"]["node"]
    assert node["bio_links"] == [{"url": "https://carol.example/book"}, {"url": "https://shop.example/carol"}]
    assert (node["biography"], node["external_url"]) == (full["biography"], "https://carol.example/")
    assert (node["id"], node["username"], node["full_name"]) == ("777", "carol.cooks", "Carol Cooks")
    for k in ("iphone_struct", "edge_owner_to_timeline_media", "edge_related_profiles", "profile_pic_url_hd",
              "biography_with_entities", "external_url_linkshimmed", "edge_followed_by"):
        assert k in full and k not in node, k


def test_a_long_biography_and_caption_are_kept_whole():
    bio = "b" * 5000
    assert parser.slim(samples.profile(CAROL, bio=bio))["node"]["biography"] == bio
    old = samples.old_post("BOLDPOST002", TS, CAROL, caption="c" * 5000)
    assert parser.slim(old)["node"]["caption"] == "c" * 5000


@pytest.mark.parametrize("data", [
    None, [], "x", 3, {}, {"node": {}}, {"instaloader": {"node_type": "Post"}},
    {"node": [], "instaloader": {"node_type": "Post"}},
    {"node": {"shortcode": "X"}, "instaloader": "4.13"},
    {"node": {"shortcode": "X"}, "instaloader": {"version": "4.13"}},
    {"node": {"name": "tomatoes"}, "instaloader": {"version": "4.13", "node_type": "Hashtag"}},
    {"node": {"__typename": "GraphImage"}, "instaloader": {"version": "4.13", "node_type": "Post"}},      # no post id
    {"node": {"username": "x"}, "instaloader": {"version": "4.13", "node_type": "Profile"}},              # no id
    {"node": {"shortcode": "X", "owner": "carol"}, "instaloader": {"version": "4.13", "node_type": "Post"}},
    {"node": {"id": 1, "bio_links": 7}, "instaloader": {"version": "4.13", "node_type": "Profile"}},
], ids=repr)
def test_slim_leaves_what_it_does_not_recognise(data):
    """None: the caller rewrites nothing."""
    before = copy.deepcopy(data)
    assert parser.slim(data) is None
    assert data == before


def test_slim_leaves_other_tools_metadata():
    found = 0
    for top, pattern in ((GALLERY_DL, ".json"), (YT_DLP, ".info.json")):
        for dirpath, _, names in os.walk(top):
            for n in names:
                if n.endswith(pattern):
                    with open(os.path.join(dirpath, n), encoding="utf-8") as f:
                        assert parser.slim(json.load(f)) is None, n
                    found += 1
    assert found > 5


def test_slim_keeps_a_field_of_an_unexpected_shape_as_it_is():
    """A field the parser reads, in a shape this code does not know: kept
    whole rather than guessed at."""
    data = samples.post("CODDSHAPE01", TS, CAROL)
    data["node"]["location"] = ["Lyon", "France"]
    data["node"]["edge_media_to_comment"] = 56
    data["node"]["taken_at_timestamp"] = {"seconds": TS}
    node = parser.slim(data)["node"]
    assert node["location"] == ["Lyon", "France"] and node["edge_media_to_comment"] == 56
    assert node["taken_at_timestamp"] == {"seconds": TS}
    assert parser.reads(parser.slim(data)) == parser.reads(data)


def test_sizes():
    """What the PR says of the sizes: the full-size samples, as instaloader
    wrote them with --no-compress-json, compressed, and slim."""
    total = collections.Counter()
    for name in FULL:
        data = SAMPLES[name]
        sizes = {"plain": len(plain(data)),
                 "xz": len(lzma.compress(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())),
                 "slim": len(slim.pack(parser.slim(data)))}
        print(f"{name:28} {sizes['plain']:>8} {sizes['xz']:>7} {sizes['slim']:>6}")
        total.update(sizes)
        assert sizes["slim"] < sizes["xz"] < sizes["plain"]
    print(f"{'total':28} {total['plain']:>8} {total['xz']:>7} {total['slim']:>6}")
    assert total["slim"] * 20 < total["plain"]


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------

def test_no_command_asks_for_plain_json(env, client, fake):
    fake.set(carol_profile())
    s = add_source(client)
    job = sync_now(client, s["id"])
    assert job["state"] == "done" and "--no-compress-json" not in job["argv"]
    assert "--no-compress-json" not in save._build({"shortcode": "CSAVEME0001"})["args"]
    assert all("--no-compress-json" not in r["argv"] for r in fake.runs())
    assert "--no-compress-json" not in json.dumps(scripts.BUILTINS)
    names = os.listdir(env["media"] / "instagram" / "carol.cooks")
    assert [n for n in names if n.endswith(".json.xz")] and not [n for n in names if n.endswith(".json")]


# ---------------------------------------------------------------------------
# .json and .json.xz in one folder
# ---------------------------------------------------------------------------

def scan(env):
    return scanner.scan(env["roots"])


def mixed(env):
    """A folder with a plain post, a compressed one, and one that has both
    files (TWIN: its .json.xz slim). Returns (folder, the twin's base)."""
    folder = env["media"] / "carol.cooks"
    write_post(folder, "PLAIN000001", TS, CAROL, "image", caption="plain one")
    write_post(folder, "PACKED00001", TS + DAY, CAROL, "video", caption="packed one", compress=True)
    twin = write_post(folder, "TWIN0000001", TS + 2 * DAY, CAROL, "carousel", caption="both", slides=[False, True])
    with open(twin + ".json") as f:
        data = json.load(f)
    with open(twin + ".json.xz", "wb") as f:
        f.write(slim.pack(parser.slim(data)))
    return folder, twin


def ids(client, query=""):
    return sorted(p["id"] for p in get(client, "/api/posts" + query)["posts"])


def test_mixed_folder_is_indexed_once_each(env, client):
    folder, twin = mixed(env)
    r = scan(env)
    assert (r["added"], r["unmatched"], r["errors"]) == (3, 0, [])
    assert ids(client) == ["instagram:PACKED00001", "instagram:PLAIN000001", "instagram:TWIN0000001"]
    conn = db.connect()
    row = conn.execute("SELECT meta_path, side_files FROM posts WHERE id = 'instagram:TWIN0000001'").fetchone()
    assert row["meta_path"] == twin + ".json.xz"                 # read from the compressed one
    assert twin + ".json" in json.loads(row["side_files"])       # the plain one goes where the post goes
    assert conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM unmatched").fetchone()[0] == 0
    assert get(client, "/api/posts/instagram/TWIN0000001")["media_count"] == 2
    again = scan(env)
    assert (again["added"], again["updated"], again["unmatched"]) == (0, 0, 0)


def test_a_json_xz_appearing_beside_an_indexed_json_is_the_same_post(env, client):
    """A post indexed from its .json, downloaded again by an instaloader
    that compresses: still one post, with its first_seen and user data."""
    folder = env["media"] / "carol.cooks"
    base = write_post(folder, "AGAIN000001", TS, CAROL, "image", caption="again")
    scan(env)
    post(client, "/api/tags/apply", {"posts": ["instagram:AGAIN000001"], "add": ["keeper"], "remove": []})
    conn = db.connect()
    first = conn.execute("SELECT first_seen FROM posts WHERE id = 'instagram:AGAIN000001'").fetchone()[0]
    with open(base + ".json") as f:
        data = json.load(f)
    with lzma.open(base + ".json.xz", "wt") as f:
        json.dump(data, f)
    r = scan(env)
    assert (r["added"], r["unmatched"], r["errors"]) == (0, 0, [])
    row = conn.execute("SELECT meta_path, first_seen FROM posts WHERE id = 'instagram:AGAIN000001'").fetchone()
    assert (row["meta_path"], row["first_seen"]) == (base + ".json.xz", first)
    assert conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0] == 0
    assert get(client, "/api/posts/instagram/AGAIN000001")["tags"] == ["keeper"]
    assert get(client, "/api/duplicates")["groups"] == []


def test_mixed_folder_trash_and_restore_take_both_files(env, client):
    folder, twin = mixed(env)
    scan(env)
    before = sorted(os.listdir(folder))
    names = sorted(n for n in before if n.startswith(os.path.basename(twin)))
    assert len(names) == 6                                       # 2 jpg, mp4, txt, json, json.xz
    all_ids = ["instagram:PACKED00001", "instagram:PLAIN000001", "instagram:TWIN0000001"]
    r = post(client, "/api/delete", {"posts": all_ids})
    assert sorted(r["posts"]) == all_ids and r["errors"] == [] and r["files"] == len(before)
    assert os.listdir(folder) == []
    assert sorted(os.listdir(env["media"] / ".feedvault-trash" / "carol.cooks")) == before
    assert ids(client) == [] and scan(env)["added"] == 0
    r = post(client, "/api/trash/restore", {"posts": all_ids})
    assert sorted(r["posts"]) == all_ids and r["errors"] == []
    assert sorted(os.listdir(folder)) == before
    assert ids(client) == all_ids
    again = scan(env)
    assert (again["added"], again["unmatched"]) == (0, 0)
    assert db.connect().execute("SELECT COUNT(*) FROM copies").fetchone()[0] == 0


def test_a_saved_post_with_both_files_moves_whole(env, client, fake):
    """save.gather: the plain file follows the post out of _saved, renamed
    as the rest; the sync that follows does not download it again."""
    code = "CSAVEME0001"
    fake.set({"stranger": {"id": 4242, "name": "Stranger", "posts": [
        {"shortcode": code, "ts": TS + 10 * DAY, "caption": "saved", "kind": "carousel", "slides": 2}]}})
    job = ended(post(client, "/api/save", {"platform": "instagram", "shortcode": code})["job"]["id"])
    assert job["state"] == "done", job
    saved = env["media"] / "_saved"
    [packed] = [n for n in os.listdir(saved) if n.endswith(".json.xz")]
    (saved / packed[:-3]).write_bytes(plain(load(saved / packed)))
    scan(env)
    assert ids(client) == [f"instagram:{code}"]
    s = add_source(client, target="stranger")
    job = sync_now(client, s["id"])
    assert job["state"] == "done", job
    folder = env["media"] / "instagram" / "stranger"
    for suffix in ("_1.jpg", "_2.jpg", ".json.xz", ".json", ".txt"):
        assert (folder / f"stranger-2024-06-11-{code}{suffix}").is_file(), suffix
    assert not [n for n in os.listdir(saved) if code in n]
    assert ids(client) == [f"instagram:{code}"] and job["result"]["added"] == 0
    assert not any(code in line for r in fake.runs()[1:] for line in r.get("downloaded", []))
    conn = db.connect()
    assert conn.execute("SELECT meta_path FROM posts").fetchone()[0] == str(folder / f"stranger-2024-06-11-{code}.json.xz")
    assert conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# The clean-up
# ---------------------------------------------------------------------------

@pytest.fixture
def runner(env, monkeypatch):
    monkeypatch.setattr(jobs, "_active", collections.OrderedDict())
    monkeypatch.setattr(jobs, "_closing", False)
    yield
    jobs.shutdown()
    for t in threading.enumerate():
        if t.name.startswith("job-") and not t.name.endswith("-log"):
            t.join(10)


def tree(top):
    """Every file under ``top`` as it is on the disk: content (a link's
    target), mode, times."""
    out = {}
    for dirpath, dirnames, names in os.walk(top):
        for n in names + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            path = os.path.join(dirpath, n)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                out[os.path.relpath(path, top)] = ("link", os.readlink(path))
            else:
                with open(path, "rb") as f:
                    out[os.path.relpath(path, top)] = (f.read(), stat.S_IMODE(st.st_mode), st.st_mtime_ns, st.st_ino)
    return out


def index():
    conn = db.connect()
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
            for t in ("posts", "media", "copies", "profiles", "unmatched", "dismissed_duplicates")}


def library(env):
    """Full-size posts and a profile as an instaloader with
    --no-compress-json left them, one post compressed and complete, one
    slim already; beside them what is not instaloader's. Returns the
    instaloader files to rewrite, by path relative to the media root."""
    carol, dave = env["media"] / "instagram" / "carol.cooks", env["media"] / "dave_draws"
    carol.mkdir(parents=True)
    dave.mkdir()
    todo = {}
    for i, name in enumerate(("GraphImage", "GraphVideo", "GraphSidecar", "old format", "iphone_struct only")):
        base = carol / base_name(TS + i * DAY)
        for n in ("_1.jpg", "_2.jpg") if name == "GraphSidecar" else (".jpg",):
            png(f"{base}{n}", (i * 40, 90, 200 - i * 30))
        (carol / (base.name + ".txt")).write_text("caption")
        (carol / (base.name + ".json")).write_bytes(plain(SAMPLES[name]))
        os.utime(f"{base}.json", (TS + i, TS + i))
        todo[os.path.relpath(f"{base}.json", env["media"])] = SAMPLES[name]
    (carol / "carol.cooks_777.json").write_bytes(plain(SAMPLES["Profile"]))
    todo["instagram/carol.cooks/carol.cooks_777.json"] = SAMPLES["Profile"]
    (carol / "id").write_text("777\n")
    comments = carol / (base_name(TS) + "_comments.json")
    comments.write_text(json.dumps([{"id": 1, "text": "nice", "owner": {"username": "x"}, "instaloader": "no"}]))
    # compressed and complete (instaloader's own), compressed and slim
    base = dave / base_name(TS + 20 * DAY)
    png(f"{base}.jpg")
    with lzma.open(f"{base}.json.xz", "wt") as f:
        json.dump(SAMPLES["tagged"], f)
    todo[os.path.relpath(f"{base}.json.xz", env["media"])] = SAMPLES["tagged"]
    base = dave / base_name(TS + 21 * DAY)
    png(f"{base}.jpg")
    with open(f"{base}.json.xz", "wb") as f:
        f.write(slim.pack(parser.slim(samples.post("CSLIMDONE01", TS + 21 * DAY, DAVE))))
    # other tools', and files that only look like metadata
    gallery_dl_case("twitter/photo", env["media"] / "twitter")
    yt_dlp_case("tiktok/video", env["media"] / "tiktok")
    (dave / "notes.json").write_text('{"instaloader": "is mentioned", "node": 1}')
    (dave / "broken.json").write_text('{"instaloader": {"node_type": "Post"}, "node": {')
    (dave / "junk.json.xz").write_bytes(b"\xfd7zXZ\x00 not xz at all")
    (dave / "hashtag.json").write_text(json.dumps({"node": {"name": "tomatoes"},
                                                   "instaloader": {"version": "4.13", "node_type": "Hashtag"}}))
    return todo


def start(client, mode, status=200):
    r = client.post("/api/jobs", json={"kind": slim.KIND, "params": {"mode": mode}}, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def run(client, mode="apply"):
    job = ended(start(client, mode)["job"]["id"], 30)
    assert job["state"] == "done", job
    return job


def no_temporary_files(env):
    left = [os.path.join(d, n) for d, _, names in os.walk(env["tmp"]) for n in names if n.endswith(slim.TMP)]
    assert left == []


def test_preview_changes_nothing_and_says_what_apply_would_do(env, client, runner):
    todo = library(env)
    scan(env)
    files, rows = tree(env["media"]), index()
    job = run(client, "preview")
    r = job["result"]
    assert tree(env["media"]) == files and index() == rows       # not a byte, not a row
    no_temporary_files(env)
    assert (r["mode"], r["files"], r["clean"], r["checked"], r["failures"], r["stopped"]) == \
        ("preview", len(todo), 1, len(todo) + 1, 0, False)
    assert r["bytes_before"] == sum(len(files[p][0]) for p in todo)
    assert r["bytes_after"] == sum(len(slim.pack(parser.slim(d))) for d in todo.values())      # exact, not a guess
    assert job["message"] == f"7 files to slim: {slim.fmt_bytes(r['bytes_before'])} now, " \
                             f"{slim.fmt_bytes(r['bytes_after'])} after"
    # What apply then does is what the preview said.
    done = run(client)["result"]
    assert (done["files"], done["bytes_before"], done["bytes_after"]) == (r["files"], r["bytes_before"], r["bytes_after"])
    after = tree(env["media"])
    assert sum(len(after[p if p.endswith(".xz") else p + ".xz"][0]) for p in todo) == r["bytes_after"]


def test_apply_replaces_the_files_and_the_index_follows(env, client, runner):
    todo = library(env)
    scan(env)
    before = tree(env["media"])
    posts = {p["id"]: p for p in get(client, "/api/posts?limit=50")["posts"]}
    assert len(posts) == 7 + 2                                   # instaloader's, gallery-dl's, yt-dlp's
    details = {i: get(client, "/api/posts/" + i.replace(":", "/", 1)) for i in posts}
    suggestions = get(client, "/api/people/suggestions")
    rows = index()
    job = run(client)
    r = job["result"]
    assert (r["mode"], r["files"], r["clean"], r["failures"], r["failed"], r["stopped"]) == ("apply", 7, 1, 0, [], False)
    assert job["message"] == f"7 files slimmed, {slim.fmt_bytes(r['bytes_before'] - r['bytes_after'])} freed"
    after = tree(env["media"])
    no_temporary_files(env)
    for rel, data in todo.items():
        new = rel if rel.endswith(".xz") else rel + ".xz"
        assert rel == new or rel not in after                    # the plain file is gone
        assert load(env["media"] / new) == parser.slim(data)
        assert after[new][1:3] == before[rel][1:3]               # its mode and its time
    # Everything else is the very file it was.
    assert {k: v for k, v in after.items() if k not in todo and k[:-3] not in todo} == \
        {k: v for k, v in before.items() if k not in todo}
    # The index names the new files, with no scan asked for, and reads the same posts.
    conn = db.connect()
    metas = [r[0] for r in conn.execute("SELECT meta_path FROM posts WHERE tool = 'instaloader'")]
    assert len(metas) == 7 and all(m.endswith(".json.xz") and os.path.isfile(m) for m in metas)
    assert conn.execute("SELECT source FROM profiles").fetchone()[0].endswith("carol.cooks_777.json.xz")
    assert {p["id"]: p for p in get(client, "/api/posts?limit=50")["posts"]} == posts
    for i, d in details.items():
        now = get(client, "/api/posts/" + i.replace(":", "/", 1))
        if d["source"]["tool"] == "instaloader":
            assert now["source"].pop("meta_path") in (d["source"]["meta_path"] + ".xz", d["source"].pop("meta_path"))
        assert now == d
    assert get(client, "/api/people/suggestions") == suggestions
    now = index()
    assert now["media"] == rows["media"] and len(now["posts"]) == len(rows["posts"])
    assert (now["copies"], now["unmatched"]) == (rows["copies"], rows["unmatched"])
    again = scan(env)
    assert (again["added"], again["updated"], again["missing"]) == (0, 0, 0)
    assert again["unmatched"] == len(rows["unmatched"])
    # Run again: nothing left to do, and nothing rewritten.
    files = tree(env["media"])
    r = run(client)["result"]
    assert (r["files"], r["clean"], r["checked"], r["bytes_before"], r["failures"]) == (0, 8, 8, 0, 0)
    assert tree(env["media"]) == files
    assert run(client, "preview")["message"] == "0 files to slim: 0 B now, 0 B after"


def test_apply_keeps_copies_and_dismissed_duplicates_right(env, client, runner):
    """The same post in two folders: one is indexed, the other is its copy,
    named by its metadata file in the copies and in a dismissed group."""
    a, b = env["media"] / "a", env["media"] / "b"
    for folder in (a, b):
        folder.mkdir()
        png(str(folder / (base_name(TS) + ".jpg")))
        (folder / (base_name(TS) + ".json")).write_bytes(plain(SAMPLES["GraphImage"]))
    scan(env)
    conn = db.connect()
    [copy_meta] = [r[0] for r in conn.execute("SELECT meta_path FROM copies")]
    with conn:
        conn.execute("INSERT INTO dismissed_duplicates(key, kind, at) VALUES (?, 'copies', 5)",
                     (json.dumps(sorted(["instagram:CIMAGE00001", copy_meta])),))
        conn.execute("INSERT INTO dismissed_duplicates(key, kind, at) VALUES (?, 'content', 6)",
                     (json.dumps(["instagram:OTHER", "instagram:OTHER2"]),))
    assert get(client, "/api/duplicates")["groups"] == []
    assert run(client)["result"]["files"] == 2
    assert [r[0] for r in conn.execute("SELECT meta_path FROM copies")] == [copy_meta + ".xz"]
    assert conn.execute("SELECT meta_path FROM posts").fetchone()[0].endswith(".json.xz")
    keys = sorted(r[0] for r in conn.execute("SELECT key FROM dismissed_duplicates"))
    assert keys == sorted([json.dumps(sorted(["instagram:CIMAGE00001", copy_meta + ".xz"])),
                           json.dumps(["instagram:OTHER", "instagram:OTHER2"])])
    assert get(client, "/api/duplicates")["groups"] == []        # still dismissed
    scan(env)
    assert [r[0] for r in conn.execute("SELECT meta_path FROM copies")] == [copy_meta + ".xz"]
    assert get(client, "/api/duplicates")["groups"] == []


def test_apply_finishes_a_post_that_has_both_files(env, client, runner):
    folder, twin = mixed(env)
    scan(env)
    packed = tree(folder)[os.path.basename(twin) + ".json.xz"]
    r = run(client, "preview")["result"]                         # the plain post, and the .json of the one with both
    assert (r["files"], r["clean"]) == (2, 2) and os.path.isfile(twin + ".json")
    r = run(client)["result"]
    assert (r["files"], r["failures"]) == (2, 0)
    assert not os.path.exists(twin + ".json") and tree(folder)[os.path.basename(twin) + ".json.xz"] == packed
    assert not [n for n in os.listdir(folder) if n.endswith(".json")]
    row = db.connect().execute("SELECT meta_path, side_files FROM posts WHERE id = 'instagram:TWIN0000001'").fetchone()
    assert row["meta_path"] == twin + ".json.xz" and twin + ".json" not in json.loads(row["side_files"])
    assert ids(client) == ["instagram:PACKED00001", "instagram:PLAIN000001", "instagram:TWIN0000001"]
    assert scan(env)["added"] == 0


def test_a_json_whose_json_xz_is_another_post_is_left(env, client, runner):
    """Never removed on the name alone: the .json.xz must read as the same post."""
    folder = env["media"] / "carol.cooks"
    base = write_post(folder, "MINE0000001", TS, CAROL, "image")
    for other in (slim.pack(parser.slim(samples.post("OTHER000001", TS, CAROL))), b"not xz", b""):
        with open(base + ".json.xz", "wb") as f:
            f.write(other)
        before = tree(folder)
        r = run(client)["result"]
        assert (r["files"], r["failures"]) == (0, 1)
        assert "does not read as the same post" in r["failed"][0]["error"] and r["failed"][0]["path"] == base + ".json"
        assert tree(folder) == before


STEPS = ["_write", "_verify", "_unchanged", "_place", "_remove"]


@pytest.mark.parametrize("step", STEPS)
def test_a_failure_at_any_step_leaves_the_original(env, client, runner, monkeypatch, step):
    todo = library(env)
    scan(env)
    before = tree(env["media"])
    victim = str(env["media"] / "instagram" / "carol.cooks" / (base_name(TS + 2 * DAY) + ".json"))
    packed = str(env["media"] / "dave_draws" / (base_name(TS + 20 * DAY) + ".json.xz"))
    real = getattr(slim, step)

    def failing(*args):
        subject = args[-1].path if isinstance(args[-1], slim._Todo) else args[-1]
        if subject in (victim, packed):
            raise OSError(28, "No space left on device")
        return real(*args)

    monkeypatch.setattr(slim, step, failing)
    job = run(client)
    r = job["result"]
    after = tree(env["media"])
    no_temporary_files(env)
    # _remove is only ever for a .json: the compressed file has no such step.
    failed = [victim] if step == "_remove" else sorted([victim, packed])
    assert sorted(f["path"] for f in r["failed"]) == failed and r["failures"] == len(failed)
    assert all("No space left on device" in f["error"] for f in r["failed"])
    assert r["files"] == len(todo) - len(failed) and f"{len(failed)} file" in job["message"]
    for path in failed:                                          # the originals: the very files, untouched
        rel = os.path.relpath(path, env["media"])
        assert after[rel] == before[rel]
    assert (os.path.relpath(victim, env["media"]) + ".xz" in after) == (step == "_remove")
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE tool = 'instaloader'").fetchone()[0] == 7
    meta = conn.execute("SELECT meta_path FROM posts WHERE id = 'instagram:CSIDECAR001'").fetchone()[0]
    assert meta == victim + (".xz" if step == "_remove" else "") and os.path.isfile(meta)
    assert get(client, "/api/posts/instagram/CSIDECAR001")["media_count"] == 2
    assert conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0] == 0
    # The next run does what this one could not.
    monkeypatch.setattr(slim, step, real)
    r = run(client)["result"]
    assert (r["files"], r["failures"]) == (len(failed), 0)
    assert not os.path.exists(victim) and load(victim + ".xz") == parser.slim(SAMPLES["GraphSidecar"])
    assert load(packed) == parser.slim(SAMPLES["tagged"])
    assert scan(env)["added"] == 0 and len(ids(client)) == 9


@pytest.mark.parametrize("damage", ["cut", "other", "empty"])
def test_what_is_on_the_disk_is_read_back_before_anything_is_replaced(env, monkeypatch, damage):
    """Step 3 reads the temporary file itself: a write the disk did not
    keep whole is caught, and the original stays."""
    library(env)
    before = tree(env["media"])
    real = slim._write

    def write(todo):
        tmp = real(todo)
        with open(tmp, "r+b") as f:
            if damage == "cut":
                f.truncate(os.path.getsize(tmp) // 2)
            elif damage == "empty":
                f.truncate(0)
            else:
                f.write(slim.pack(parser.slim(samples.post("CSOMEONE001", TS, DAVE))))
                f.truncate()
        return tmp

    monkeypatch.setattr(slim, "_write", write)
    r = slim.sweep(env["roots"], True)
    assert (r["files"], r["failures"]) == (0, 7)
    assert tree(env["media"]) == before
    no_temporary_files(env)


def test_a_file_that_changes_while_it_is_rewritten_is_left(env, monkeypatch):
    """instaloader, or the user, wrote it again meanwhile: what is there
    now is not what was slimmed."""
    folder = env["media"] / "carol.cooks"
    base = write_post(folder, "CHANGING001", TS, CAROL, "image")
    real = slim._verify

    def verify(tmp, todo):
        real(tmp, todo)
        with open(todo.path, "w") as f:
            json.dump({"node": post_node("CHANGING001", TS, CAROL, "edited"),
                       "instaloader": {"version": "4.15.1", "node_type": "Post"}}, f)

    monkeypatch.setattr(slim, "_verify", verify)
    r = slim.sweep(env["roots"], True)
    assert (r["files"], r["failures"]) == (0, 1) and "changed" in r["failed"][0]["error"]
    assert sorted(n for n in os.listdir(folder) if "json" in n) == [os.path.basename(base) + ".json"]
    assert "edited" in open(base + ".json").read()


def test_a_json_xz_that_appears_meanwhile_is_never_replaced(env, monkeypatch):
    folder = env["media"] / "carol.cooks"
    base = write_post(folder, "RACING00001", TS, CAROL, "image")
    real = slim._unchanged
    theirs = b"instaloader is writing this"

    def unchanged(todo):
        real(todo)
        with open(todo.target, "wb") as f:
            f.write(theirs)

    monkeypatch.setattr(slim, "_unchanged", unchanged)
    before = open(base + ".json", "rb").read()
    r = slim.sweep(env["roots"], True)
    assert (r["files"], r["failures"]) == (0, 1) and "is there now" in r["failed"][0]["error"]
    assert open(base + ".json.xz", "rb").read() == theirs and open(base + ".json", "rb").read() == before
    no_temporary_files(env)
    # The same where the filesystem has no hard links (exFAT).
    os.unlink(base + ".json.xz")
    monkeypatch.setattr(os, "link", lambda *a, **kw: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted")))
    r = slim.sweep(env["roots"], True)
    assert (r["files"], r["failures"]) == (0, 1) and "is there now" in r["failed"][0]["error"]
    assert open(base + ".json.xz", "rb").read() == theirs and open(base + ".json", "rb").read() == before
    no_temporary_files(env)
    os.unlink(base + ".json.xz")
    monkeypatch.setattr(slim, "_unchanged", real)
    r = slim.sweep(env["roots"], True)                            # and without links, it works
    assert (r["files"], r["failures"]) == (1, 0)
    assert not os.path.exists(base + ".json") and parser.reads(load(base + ".json.xz"))[1]["post_id"] == "RACING00001"


def test_nothing_is_removed_before_its_replacement_is_in_place(env, monkeypatch):
    """The order of the steps, as the disk saw them."""
    library(env)
    events = []
    real_remove, real_place = slim._remove, slim._place

    def place(tmp, todo):
        assert os.path.isfile(todo.path)
        real_place(tmp, todo)
        events.append(("placed", todo.target))

    def remove(path):
        assert ("placed", path + ".xz") in events                # placed first
        assert parser.reads(load(path + ".xz")) == parser.reads(load(path))    # and it reads as the same post
        real_remove(path)
        events.append(("removed", path))

    monkeypatch.setattr(slim, "_place", place)
    monkeypatch.setattr(slim, "_remove", remove)
    r = slim.sweep(env["roots"], True)
    assert (r["files"], r["failures"]) == (7, 0)
    assert len([e for e in events if e[0] == "removed"]) == 6 and len([e for e in events if e[0] == "placed"]) == 7


def test_interrupted_and_run_again(env, client, runner, monkeypatch):
    """Cancelled while it works: it stops between two files, says what it
    did, and the next run does the rest without doing the first again."""
    todo = library(env)
    scan(env)
    reached, go = threading.Event(), threading.Event()
    real = slim.rewrite
    count = []

    def rewrite(t):
        count.append(t.path)
        if len(count) == 3:
            reached.set()
            assert go.wait(10)
        return real(t)

    monkeypatch.setattr(slim, "rewrite", rewrite)
    monkeypatch.setattr(slim, "CHUNK", 2)
    job = start(client, "apply")["job"]
    assert reached.wait(10)
    assert post(client, f"/api/jobs/{job['id']}/cancel")["ok"]
    go.set()
    job = ended(job["id"])
    assert job["state"] == "cancelled" and job["result"]["stopped"] is True
    done = job["result"]["files"]
    assert 3 <= done <= 4 and job["message"].startswith(f"stopped: {done} files slimmed")
    no_temporary_files(env)
    # Whole posts on either side of the stop: every one indexed once, where its file is.
    conn = db.connect()
    metas = [r[0] for r in conn.execute("SELECT meta_path FROM posts WHERE tool = 'instaloader'")]
    assert len(metas) == 7 and all(os.path.isfile(m) for m in metas)
    assert len([m for m in metas if m in count]) + len(count) - done >= 0
    monkeypatch.setattr(slim, "rewrite", real)
    files = tree(env["media"])
    r = run(client)["result"]
    assert (r["files"], r["clean"], r["failures"], r["stopped"]) == (len(todo) - done, 1 + done, 0, False)
    now = tree(env["media"])
    assert all(now[k] == v for k, v in files.items() if k.endswith(".json.xz") and k in now)     # not done twice
    assert not [k for k in now if k.endswith(".json") and k[:-5] + ".json.xz" in now]
    assert scan(env)["added"] == 0 and len(ids(client)) == 9


def test_a_temporary_file_left_by_a_kill_is_removed_by_the_next_run(env, client, runner):
    library(env)
    carol = env["media"] / "instagram" / "carol.cooks"
    left = carol / f".{base_name(TS)}.json.k3j2h1{slim.TMP}"
    left.write_bytes(b"half a file")
    other = carol / ".keep.tmp"                                   # not ours
    other.write_text("x")
    r = scan(env)
    assert r["unmatched"] == len(db.connect().execute("SELECT 1 FROM unmatched").fetchall())
    assert not db.connect().execute("SELECT 1 FROM unmatched WHERE path = ?", (str(left),)).fetchall()
    run(client, "preview")
    assert left.read_bytes() == b"half a file"                    # a preview removes nothing
    assert run(client)["result"]["failures"] == 0
    assert not left.exists() and other.read_text() == "x"


def test_stopped_before_the_plain_file_went_the_next_run_removes_it(env, monkeypatch):
    """Killed between "the .json.xz is in place" and "the .json is
    removed": one post still, and the next run finishes."""
    library(env)
    victim = str(env["media"] / "instagram" / "carol.cooks" / (base_name(TS) + ".json"))

    class Killed(BaseException):
        pass

    def remove(path):
        raise Killed()

    monkeypatch.setattr(slim, "_remove", remove)
    with pytest.raises(Killed):
        slim.sweep(env["roots"], True)
    monkeypatch.undo()
    assert os.path.isfile(victim) and load(victim) == SAMPLES["GraphImage"]
    assert load(victim + ".xz") == parser.slim(SAMPLES["GraphImage"])
    r = scanner.scan(env["roots"])
    assert r["errors"] == []
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE tool = 'instaloader'").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0] == 0
    r = slim.sweep(env["roots"], True)                            # dave_draws was done before the kill
    assert (r["files"], r["failures"]) == (6, 0) and not os.path.exists(victim)
    assert scanner.scan(env["roots"])["added"] == 0


def test_only_instaloader_files_are_touched(env, client, runner):
    todo = library(env)
    before = tree(env["media"])
    run(client)
    after = tree(env["media"])
    others = [k for k in before if k not in todo]
    assert len(others) > 20
    for k in others:
        assert after[k] == before[k], k                           # same bytes, same inode: not even rewritten
    for k in ("twitter", "tiktok"):
        assert not [n for n in os.listdir(env["media"] / k) if n.endswith(".xz")]
    assert any(k.endswith("_comments.json") for k in others) and any(k.endswith(".info.json") for k in others)


def test_symlinks_are_never_followed(env, client, runner, tmp_path):
    """Not out of the media root, and not to another file inside it."""
    outside = tmp_path / "outside"
    write_post(outside / "eve", "OUTSIDE0001", TS, CAROL, "image")
    lone = outside / (base_name(TS + DAY) + ".json")
    lone.write_bytes(plain(SAMPLES["GraphImage"]))
    inside = env["media"] / "carol.cooks"
    base = write_post(inside, "INSIDE00001", TS, CAROL, "image")
    os.symlink(outside / "eve", env["media"] / "eve")                          # a folder out of the root
    os.symlink(lone, inside / lone.name)                                       # a file out of the root
    os.symlink(base + ".json", inside / (base_name(TS + 2 * DAY) + ".json"))   # a file in the root
    os.symlink(outside / "eve", inside / "nested")
    before = tree(outside)
    for mode in ("preview", "apply"):
        r = run(client, mode)["result"]
        assert (r["files"], r["checked"], r["failures"]) == (1, 1, 0)
        assert tree(outside) == before
    assert not os.path.exists(base + ".json") and os.path.isfile(base + ".json.xz")
    assert os.path.islink(inside / lone.name) and os.path.islink(env["media"] / "eve")
    assert os.readlink(inside / (base_name(TS + 2 * DAY) + ".json")) == base + ".json"      # left, though it dangles
    assert not [n for n in os.listdir(outside / "eve") if n.endswith(".xz")]
    # Named outright, a link is refused too.
    set_config(slim_metadata=True)
    r = slim.files([str(inside / lone.name), str(lone), str(outside / "eve" / (base_name(TS) + ".json"))],
                   env["roots"], lambda text: None)
    assert (r["files"], r["checked"], r["failures"]) == (0, 0, 0)
    assert tree(outside) == before


def test_a_media_root_that_is_a_link_is_the_users_choice_and_the_trash_is_left(env, client, runner, tmp_path):
    real = tmp_path / "disk"
    base = write_post(real / "carol.cooks", "LINKED00001", TS, CAROL, "image")
    trashed = write_post(real / ".feedvault-trash" / "carol.cooks", "TRASHED0001", TS + DAY, CAROL, "image")
    hidden = write_post(real / ".hidden", "HIDDEN00001", TS + 2 * DAY, CAROL, "image")
    os.symlink(real, tmp_path / "link")
    set_config(media_roots=[str(tmp_path / "link")])
    r = run(client)["result"]
    assert (r["files"], r["failures"]) == (1, 0)
    assert os.path.isfile(base + ".json.xz") and not os.path.exists(base + ".json")
    assert os.path.isfile(trashed + ".json") and os.path.isfile(hidden + ".json")


def test_mode_owner_and_times_are_the_originals(env):
    folder = env["media"] / "carol.cooks"
    base = write_post(folder, "MODE0000001", TS, CAROL, "image")
    os.chmod(base + ".json", 0o640)
    os.utime(base + ".json", ns=(1_600_000_000_123_456_789, 1_500_000_000_987_654_321))
    scanner.scan(env["roots"])
    conn = db.connect()
    saved = conn.execute("SELECT * FROM posts").fetchone()
    r = slim.sweep(env["roots"], True)
    assert r["files"] == 1
    st = os.stat(base + ".json.xz")
    assert stat.S_IMODE(st.st_mode) == 0o640 and st.st_mtime_ns == 1_500_000_000_987_654_321
    assert st.st_uid == os.getuid() and st.st_nlink == 1
    now = conn.execute("SELECT * FROM posts").fetchone()
    differ = {k for k in saved.keys() if saved[k] != now[k]}
    assert differ <= {"meta_path", "meta_size", "last_seen", "tool_version"}, differ


def test_a_file_that_cannot_be_read_or_written_is_counted_and_the_rest_done(env, client, runner):
    if os.getuid() == 0:
        pytest.skip("root reads and writes anything")
    library(env)
    carol = env["media"] / "instagram" / "carol.cooks"
    secret = carol / (base_name(TS) + ".json")
    secret.chmod(0)
    locked = env["media"] / "locked"
    base = write_post(locked, "LOCKED00001", TS, CAROL, "image")
    locked.chmod(0o555)                                           # no file can be made in it
    try:
        r = run(client)["result"]
        assert r["failures"] == 2 and r["files"] == 6
        assert sorted(f["path"] for f in r["failed"]) == sorted([str(secret), base + ".json"])
        assert sorted(os.listdir(locked)) == sorted(os.path.basename(base) + s for s in (".jpg", ".json"))
    finally:
        locked.chmod(0o755)
        secret.chmod(0o644)
    assert load(secret) == SAMPLES["GraphImage"]
    assert run(client)["result"]["files"] == 2


def test_an_oversized_or_special_file_is_not_read(env, monkeypatch):
    folder = env["media"] / "carol.cooks"
    base = write_post(folder, "BIG00000001", TS, CAROL, "image")
    os.mkfifo(folder / (base_name(TS + DAY) + ".json"))            # reading it would wait for ever
    monkeypatch.setattr(slim, "MAX_BYTES", 100)
    r = slim.sweep(env["roots"], True)
    assert (r["files"], r["checked"], r["failures"]) == (0, 0, 0) and os.path.isfile(base + ".json")
    # A small file that unpacks to more than a metadata file's size: not unpacked whole.
    monkeypatch.setattr(slim, "MAX_BYTES", 100_000)
    with lzma.open(base + ".json.xz", "wt") as f:
        json.dump({"node": {"shortcode": "BIG00000001", "pad": "x" * 500_000},
                   "instaloader": {"version": "4", "node_type": "Post"}}, f)
    os.unlink(base + ".json")
    os.unlink(folder / (base_name(TS + DAY) + ".json"))
    before = tree(folder)
    r = slim.sweep(env["roots"], True)
    assert (r["files"], r["checked"]) == (0, 0) and tree(folder) == before


def test_the_job_needs_a_media_root_and_a_known_mode(env, client, runner):
    for params in ({}, {"mode": "delete"}, {"mode": "apply", "path": "/etc"}, {"mode": ["apply"]}):
        r = client.post("/api/jobs", json={"kind": slim.KIND, "params": params}, headers=H)
        assert r.status_code == 400, params
    set_config(media_roots=[])
    assert start(client, "apply", 400)["error"] == "add a media root in Settings first"
    assert jobs.active() == []
    assert client.post("/api/jobs", json={"kind": slim.KIND, "params": {"mode": "apply"}}).status_code in (400, 403)
    kinds = get(client, "/api/jobs/kinds")
    assert slim.KIND in json.dumps(kinds)


def test_the_job_waits_for_a_sync_and_a_sync_for_it(env, client, fake, tmp_path):
    """Same group as instaloader's jobs: never while it writes these files."""
    assert jobs._kinds[slim.KIND].group == sync.GROUP == jobs._kinds[save.KIND].group
    gate = tmp_path / "gate"
    fake.set(carol_profile(), gate=gate)
    s = add_source(client)
    first = post(client, f"/api/sources/{s['id']}/sync")["job"]
    wait_for(lambda: jobs.get(first["id"])["state"] == "running")
    second = start(client, "apply")["job"]
    assert jobs.get(second["id"])["state"] == "queued"
    gate.write_text("")
    assert ended(first["id"])["state"] == "done"
    job = ended(second["id"])
    assert job["state"] == "done" and job["result"]["files"] == 6      # the sync's 5 posts and the profile


# ---------------------------------------------------------------------------
# In-process job kinds (jobs.register(run=...))
# ---------------------------------------------------------------------------

def test_a_job_kind_that_runs_in_the_app(env, runner):
    gate, reached = threading.Event(), threading.Event()

    def work(params, note, stopped):
        note("working")
        if params.get("how") == "raise":
            raise RuntimeError("it broke")
        if params.get("how") == "wait":
            reached.set()
            while not stopped():
                gate.wait(0.02)
            return "done", {"got": "half"}, "half done"
        return "done", {"got": params.get("how")}, "all done"

    jobs.register("test-inside", label="inside", params={"how": {"type": "text", "required": False}}, group="test",
                  build=lambda p: {"tool": "feedvault", "args": ["test-inside"]}, run=work)
    try:
        job = ended(jobs.submit("test-inside", {"how": "fine"})["id"])
        assert (job["state"], job["result"], job["message"]) == ("done", {"got": "fine"}, "all done")
        assert job["argv"] == ["feedvault", "test-inside"]
        assert [ln["text"] for ln in jobs.log(job["id"])["lines"]] == ["[feedvault] working"]
        job = ended(jobs.submit("test-inside", {"how": "raise"})["id"])
        assert (job["state"], job["message"]) == ("failed", "it broke")
        job = jobs.submit("test-inside", {"how": "wait"})
        assert reached.wait(10)
        jobs.cancel(job["id"])
        job = ended(job["id"])
        assert (job["state"], job["result"], job["message"]) == ("cancelled", {"got": "half"}, "half done")
        reached.clear()
        job = jobs.submit("test-inside", {"how": "wait"})
        assert reached.wait(10)
        jobs.shutdown()                                           # FeedVault closing: told, and waited for
        job = jobs.get(job["id"])
        assert job["state"] == "interrupted"
    finally:
        jobs._kinds.pop("test-inside", None)


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------

def test_the_setting_is_off_by_default_and_a_boolean(env, client):
    assert get(client, "/api/config")["slim_metadata"] is False and slim.enabled() is False
    for bad in ("yes", 1, None, [True]):
        r = client.post("/api/config", json={"slim_metadata": bad}, headers=H).get_json()
        assert r["ok"] is False and r["error"] == "slim_metadata must be true or false"
    assert "slim_metadata" not in config.load() or config.load()["slim_metadata"] is False
    r = post(client, "/api/config", {"slim_metadata": True})
    assert r["ok"] and get(client, "/api/config")["slim_metadata"] is True and slim.enabled()
    post(client, "/api/config", {"slim_metadata": False})
    assert get(client, "/api/config")["slim_metadata"] is False
    set_config(slim_metadata="true")                               # a config.json edited by hand: only true is on
    assert slim.enabled() is False


def synced(env, client, fake):
    fake.set(carol_profile())
    s = add_source(client)
    job = sync_now(client, s["id"])
    assert job["state"] == "done", job
    folder = env["media"] / "instagram" / "carol.cooks"
    metas = sorted(n for n in os.listdir(folder) if n.endswith(".json.xz"))
    assert len(metas) == 6                                         # 5 posts, the profile
    return s, job, folder, {n: load(folder / n) for n in metas}


def test_setting_off_a_sync_leaves_the_metadata_complete(env, client, fake):
    s, job, folder, metas = synced(env, client, fake)
    [carousel] = [d for d in metas.values() if d["node"].get("__typename") == "GraphSidecar"]
    assert "edge_sidecar_to_children" in carousel["node"]          # compressed, and all of it
    assert not [n for n in os.listdir(folder) if n.endswith(".json")]
    assert all("display_url" in d["node"] or "profile_pic_url" in d["node"] for d in metas.values())
    assert not any("slimmed" in ln["text"] for ln in jobs.log(job["id"])["lines"])
    assert job["result"]["added"] == 5


def test_setting_on_a_sync_slims_what_it_wrote(env, client, fake):
    # A post that was there before the sync is not this sync's to slim.
    old = env["media"] / "instagram" / "carol.cooks"
    old.mkdir(parents=True)
    png(str(old / "2020-01-01_00-00-00_UTC.jpg"))
    (old / "2020-01-01_00-00-00_UTC.json").write_bytes(plain(samples.post("COLDER00001", 1577836800, CAROL)))
    os.utime(old / "2020-01-01_00-00-00_UTC.json", (1577836800, 1577836800))
    before = tree(old)
    time.sleep(1.1)                                               # a job's start is a whole second
    set_config(slim_metadata=True)
    s, job, folder, metas = synced(env, client, fake)
    for name, data in metas.items():
        assert parser.slim(data) == data, name
        assert "display_url" not in data["node"] and "profile_pic_url" not in data["node"]
    [carousel] = [d for d in metas.values() if d["node"].get("__typename") == "GraphSidecar"]
    assert "edge_sidecar_to_children" not in carousel["node"]
    assert any("6 files of metadata slimmed" in ln["text"] for ln in jobs.log(job["id"])["lines"])
    assert {k: v for k, v in tree(old).items() if k in before} == before
    # Indexed as any sync's posts: the same posts, the same text.
    assert job["result"]["added"] == 6
    p = get(client, "/api/posts/instagram/CNEWPOST001")
    assert (p["text"], p["media_count"], p["kind"], p["author"]["handle"]) == \
        ("New post 1 #fresh", 2, "carousel", "carol.cooks")
    assert db.connect().execute("SELECT source FROM profiles").fetchone()[0].endswith("carol.cooks_777.json.xz")
    no_temporary_files(env)
    assert sync_now(client, s["id"])["result"]["added"] == 0
    assert scanner.scan(env["roots"])["added"] == 0


def test_setting_on_a_failure_does_not_fail_the_sync(env, client, fake, monkeypatch):
    set_config(slim_metadata=True)
    monkeypatch.setattr(slim, "_place", lambda tmp, todo: (_ for _ in ()).throw(OSError(5, "Input/output error")))
    s, job, folder, metas = synced(env, client, fake)
    assert job["result"]["added"] == 5
    [carousel] = [d for d in metas.values() if d["node"].get("__typename") == "GraphSidecar"]
    assert "edge_sidecar_to_children" in carousel["node"]          # as instaloader wrote it
    assert any("could not slim" in ln["text"] and "Input/output error" in ln["text"]
               for ln in jobs.log(job["id"])["lines"])
    no_temporary_files(env)


def saved(env, client, fake):
    code = "CSAVEME0001"
    fake.set({"stranger": {"id": 4242, "name": "Stranger", "posts": [
        {"shortcode": code, "ts": TS + 10 * DAY, "caption": "saved one", "kind": "carousel", "slides": 2}]}})
    job = ended(post(client, "/api/save", {"platform": "instagram", "shortcode": code})["job"]["id"])
    assert job["state"] == "done", job
    [meta] = [n for n in os.listdir(env["media"] / "_saved") if n.endswith(".json.xz")]
    assert not [n for n in os.listdir(env["media"] / "_saved") if n.endswith(".json")]
    p = get(client, f"/api/posts/instagram/{code}")
    assert (p["text"], p["media_count"]) == ("saved one", 2)
    return job, load(env["media"] / "_saved" / meta)


def test_setting_off_a_save_leaves_the_metadata_complete(env, client, fake):
    job, data = saved(env, client, fake)
    assert "edge_sidecar_to_children" in data["node"]


def test_setting_on_a_save_slims_its_metadata(env, client, fake, capsys):
    set_config(slim_metadata=True)
    job, data = saved(env, client, fake)
    assert "edge_sidecar_to_children" not in data["node"] and parser.slim(data) == data
    assert "[save] CSAVEME0001: 1 file of metadata slimmed" in capsys.readouterr().out
    no_temporary_files(env)
    assert scanner.scan(env["roots"])["added"] == 0
