# FeedVault API

Backend listens on `127.0.0.1:3380`. Every `/api/*` request must carry the
header `X-FeedVault: 1`, GET included, or it gets a 403. `/media/*` URLs are
exempt so they work in `<img>` and `<video>` tags.

No CORS headers are ever sent. The built dashboard is served by the backend
itself (same origin). The Vite dev server proxies `/api` and `/media` to the
backend, so dev mode also runs same-origin.

Times are Unix seconds (UTC). Absent values are `null`, never missing keys.

## Post

A **post summary** (list endpoints):

```json
{
  "id": "instagram:C8xYzAbCdEf",
  "platform": "instagram",
  "post_id": "C8xYzAbCdEf",
  "url": "https://www.instagram.com/p/C8xYzAbCdEf/",
  "kind": "carousel",
  "author": { "id": "123456", "handle": "somebody", "name": "Some Body" },
  "posted_at": 1727481600,
  "saved_at": 1727500000,
  "text": "caption text, may be long, may be empty string",
  "stats": { "likes": 120, "comments": 4, "views": null },
  "media_count": 3,
  "bytes": 5447680,
  "cover": { "kind": "image", "url": "/media/17" },
  "missing": false,
  "decision": null,
  "tags": ["outfits", "summer"]
}
```

- `platform`: `instagram`, `twitter` (X; `url` points at x.com), `tiktok`; posts
  from other gallery-dl sites carry the gallery-dl category (`reddit`, …)
- `kind`: `image` | `video` | `carousel` | `story` | `text`
- `cover`: a thumbnail of the first media item (`/media/<id>/thumb`, a JPEG
  at most 480 px wide), or `null` for text-only posts. For a video,
  `cover.kind` is `"video"` and `cover.poster` says whether an image exists:
  `true` when there is a poster file or ffmpeg can grab a frame, `false` when
  neither, and then `cover.url` points at the video itself.
- `missing`: the metadata file is gone from disk. The post stays in the index.
- `bytes`: sum of `size` over the post's media items that are not missing
  (see [Sizes](#sizes)). `media_count` counts every item, missing ones too.
  The Feed adds these up to show the size of a selection in select mode, so
  no extra request is needed for it.

- `tags`: the post's tag names, sorted (see [Tags](#tags)); `[]` when none.

`url` is `null` when the post has no public address (highlight items).
`text` is an empty string when the downloader saved no caption.

A **full post** (`GET /api/posts/<platform>/<post_id>`) adds:

```json
{
  "media": [
    { "id": 17, "idx": 1, "kind": "image", "url": "/media/17", "poster_url": null, "thumb_url": "/media/17/thumb", "size": 204800, "missing": false },
    { "id": 18, "idx": 2, "kind": "video", "url": "/media/18", "poster_url": "/media/18/poster", "thumb_url": "/media/18/thumb", "size": 5242880, "missing": false }
  ],
  "location": "Paris, France",
  "album": "Summer trip",
  "hashtags": ["travel", "paris"],
  "collections": [{ "id": 3, "name": "Moodboard" }],
  "source": { "tool": "instaloader", "version": "4.15.1", "meta_path": "/abs/path/2024-06-01_12-00-00_UTC.json" }
}
```

- `album`: highlight title (or other collection name); for gallery-dl posts a
  note such as `"Retweeted by @someone"` or `"Quoted by @someone"`; `null` otherwise
- `collections`: the user's collections this post is in (see
  [Collections](#collections)), `[{ "id": 3, "name": "Moodboard" }]`, by name
- `source.tool`: `"instaloader"` or `"gallery-dl"` (`version` is `null` for
  gallery-dl, which does not record it); `meta_path` of a gallery-dl post is the
  JSON of its first media file, or the post-level JSON of a text-only tweet
- `source.tool` is `"instaloader (filenames)"` when the post was rebuilt from
  file names alone (downloads made with `save_metadata=False`); `meta_path`
  is then the first media file

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/posts?q=&platform=&author=&person=&kind=&tag=&untagged=&sort=&offset=&limit=` | `{ "total": 123, "posts": [summary, …] }` |
| GET | `/api/posts/<platform>/<post_id>` | full post, or 404 `{ "ok": false, "error": "not found" }` |
| GET | `/api/posts/summary?q=&platform=&author=&person=&kind=&review=&tag=&untagged=` | `{ "posts": 12, "media": 30, "bytes": 1048576 }`, see below |
| GET | `/api/authors` | `[account, …]`, most posts first, see [People](#people) |
| GET | `/api/storage?person=` | disk use by creator, kind and year, and the largest files, see [Storage](#storage) |
| GET | `/api/stats` | `{ "posts", "media", "authors", "bytes", "missing", "unmatched", "by_platform": { "instagram": 12 }, "by_kind": { "image": 5 } }`; `bytes` leaves out media marked missing |
| GET | `/api/unmatched` | `[{ "path", "size", "mtime", "reason" }]` |
| GET | `/api/scan` | scan status, see below |
| POST | `/api/scan` | starts a rescan in the background; `{ "ok": true }`, or `{ "ok": false, "error": "already running" }` |
| GET | `/api/config` | `{ "media_roots": ["/abs/path"], "data_directory": "/abs", "version": "0.0.0-dev", "tools": { "yt-dlp": "/abs/yt-dlp" }, "instaloader": { "session": { "mode": "none" }, "pause": 60 }, "gallery-dl": { "session": { "mode": "none" }, "pause": 30 }, "yt-dlp": { "session": { "mode": "none" }, "pause": 30 }, "youtube_max_seconds": 180, "routes": {…}, "check_updates": false }` |
| POST | `/api/config` | body `{ "media_roots": [...] }` and/or `{ "tools": { "yt-dlp": "/abs/path" } }` and/or `{ "instaloader": {…} }`, `{ "gallery-dl": {…} }`, `{ "yt-dlp": {…} }`, `{ "youtube_max_seconds": 180 }`, `{ "routes": {…} }`, `{ "check_updates": true }`; `{ "ok": true, "config": {…} }` or `{ "ok": false, "error": "…" }`. See [Tools](#tools), [Downloaders](#downloaders), [instaloader settings](#instaloader-settings), [gallery-dl and yt-dlp settings](#gallery-dl-and-yt-dlp-settings) and [Link routing](#link-routing) |
| POST | `/api/yt-dlp/info-json-cookies` | body `{ "apply": false }` (default: only counts) or `{ "apply": true }`; see [Cookies in info JSONs](#cookies-in-info-jsons) |
| GET | `/api/browse` | native folder picker (zenity): `{ "path": "/abs" }` or `{ "path": null }` if cancelled |
| POST | `/api/saved` | body `{ "ids": ["instagram:C8x…"] }` → `{ "saved": ["instagram:C8x…"] }` (used by the userscript) |
| POST | `/api/quit` | stops the backend |
| GET | `/media/<id>` | the media file bytes (Range supported, for video) |
| GET | `/media/<id>/poster` | poster image for a video, 404 if none |
| GET | `/media/<id>/thumb` | small JPEG, cached in the data directory; falls back to the original for images, 404 for a video with no frame |
| GET | `/media/copy/<copy_id>/thumb` | the same for the first item of an extra copy (see [Duplicates](#duplicates)) |

## Deleting

Deleting never destroys a file directly. Files move to a trash folder inside
the media root they came from (`<root>/.feedvault-trash/`, same relative path),
which is an instant rename on the same disk. The scanner skips that folder.
Only **Empty trash** and **purge** (below) remove files for good.

| Method | Path | Returns |
|---|---|---|
| POST | `/api/delete` | body `{ "posts": ["instagram:C8x…"], "media": [17, 18] }` (either list may be omitted) → see below |
| GET | `/api/trash` | `{ "files": 12, "bytes": 1048576, "roots": [{ "root": "/abs", "path": "/abs/.feedvault-trash", "files": 12, "bytes": 1048576 }] }` |
| POST | `/api/trash/empty` | permanently removes every trash folder → `{ "ok": true, "files": 12, "bytes": 1048576 }` |
| GET | `/api/trash/items?offset=&limit=&platform=&author=&person=&since=&before=` | what is in the trash, one entry per deletion, see [Trash contents](#trash-contents) |
| POST | `/api/trash/purge` | body `{ "keys": ["…"] }` or `{ "filter": { "platform": …, "author": …, "person": …, "since": …, "before": … } }` → permanently deletes those entries' files, see [Trash contents](#trash-contents) |
| GET | `/trash/<key>/thumb` | small JPEG of a trashed entry (no header needed, like `/media`) |

`/api/delete` removes each listed post with all its files (media, posters,
metadata JSON, caption and side files), and each listed media item on its own.
Removing the last media item of a post removes the post. A post whose files are
already gone is simply dropped from the index. Response:

```json
{
  "ok": true,
  "posts": ["instagram:C8x…"],
  "media": [17],
  "files": 5,
  "bytes": 5242880,
  "errors": [{ "path": "/abs/file.jpg", "error": "outside the media roots" }]
}
```

`posts` and `media` list what was actually removed from the index. A post or
item with any file that could not be moved stays in the index, and the reason
is in `errors`. `ok` is `false` only when nothing could be done at all (bad
body, or a scan held the index for more than 30 s).

**Never again.** Deleting a gallery-dl or yt-dlp post also adds it to the
download archives in the data directory (see
[gallery-dl and yt-dlp syncs](#gallery-dl-and-yt-dlp-syncs)), so no sync
downloads it again: gallery-dl's entry for each of its files, and the
yt-dlp line `<platform> <id>` (both tools download TikTok and X). A media
item deleted on its own adds its file's gallery-dl entry. The entries a
deletion added (not those the archive already had) are kept on its
manifest lines (`"archive": { "gallery-dl": [...], "yt-dlp": [...] }`), and
restoring it takes them out again. Emptying the trash or purging keeps them.
instaloader has no archive: its `--latest-stamps` keeps a trashed post
older than the profile's newest download from coming back, but a trashed
post newer than the stamp (trashed before any sync passed it) can be
downloaded again by the next sync.

### Trash contents

Every file moved to the trash gets one line in its trash folder's
`.manifest.jsonl`:

```json
{ "from": "/abs/alice/2024-06-01_12-00-00_UTC.jpg", "to": "/abs/.feedvault-trash/alice/2024-06-01_12-00-00_UTC.jpg",
  "post": "instagram:C8x…", "batch": "3f2a…", "at": 1727500000,
  "platform": "instagram", "author": { "id": "123456", "handle": "somebody" },
  "kind": "carousel", "posted_at": 1727481600, "items": 5, "partial": true,
  "role": "media", "idx": 2, "media_kind": "image", "size": 204800 }
```

A file of an extra copy (see [Duplicates](#duplicates)) gets the same line
plus `"copy": "<the copy's metadata path>"`; `post` is the id of the post it
is a copy of. Its entry is separate from the post's, even in the same batch.

`batch` is one `/api/delete` call. The trashed post is gone from the index, so
the line carries what the Trash page shows: `platform`, `author`, `kind` and
`posted_at` of the post, `items` (its media count when it was deleted),
`partial` (`true` when only some media items were deleted, not the post),
`role` (`media`, `poster`, `meta` or `side`), `idx` and `media_kind` for media
and posters, and `size` in bytes. Lines written before these fields existed
still work: the platform comes from the post id, the media kind from the file
extension, the size from the file on disk, and `author` is `null`.

`GET /api/trash/items` groups the lines by trash folder, post and batch, newest
deletion first:

```json
{
  "total": 3, "files": 9, "bytes": 15728640,
  "trash": { "entries": 7, "files": 21, "bytes": 52428800 },
  "authors": [{ "platform": "instagram", "id": "123456", "handle": "somebody", "entries": 4, "bytes": 31457280 }],
  "entries": [
    { "key": "9b1f0c7d2e4a6b8c0d1e", "post": "instagram:C8x…", "platform": "instagram", "post_id": "C8x…",
      "batch": "3f2a…", "at": 1727500000, "author": { "id": "123456", "handle": "somebody" },
      "kind": "carousel", "posted_at": 1727481600, "files": 1, "bytes": 204800,
      "items": 1, "of": 5, "partial": true, "copy": false, "missing": false, "thumb_url": "/trash/9b1f0c7d2e4a6b8c0d1e/thumb" }
  ]
}
```

- Query parameters, all optional: `platform`, `author` (an author id, or a
  handle for authors without one; ids are only unique within a platform, so
  send `platform` with it), `person` (a person id: entries of any of their
  accounts, see [People](#people); one that is not an id matches nothing),
  `since` (Unix seconds: deleted at or after),
  `before` (deleted strictly before), `offset` (default 0), `limit`
  (default 60, max 500).
- `total`, `files` and `bytes` add up every entry the filters match, not just
  one page. `trash` and `authors` cover the whole trash, whatever the filters.
- `key` is opaque. It names one entry and is what restore and purge take.
- `files` and `bytes` (here and in the totals) count the entry's files still
  in the trash. `items` counts its media items, `of` the post's media count when it
  was deleted (`null` for old lines).
- `partial`: only some media items of the post were deleted; the rest is still
  in the index. Not set when the same call went on to delete the whole post.
- `copy`: the entry is an extra copy of a post (trashed from Duplicates).
  Restoring it puts the folder back and the scanner records it as a copy
  again, or as the post if the post itself is gone.
- `missing`: at least one of the entry's files is no longer in the trash
  (moved or deleted by hand). Purging the entry drops its lines.
- `author` is `null` when the line predates author fields.
- `thumb_url` is `null` when the entry has no image or video, or its first
  item is a video with neither a poster nor ffmpeg to grab a frame.

`GET /trash/<key>/thumb` serves a thumbnail of the entry's first media item
(its poster for a video). It only ever reads a file listed in a manifest that
resolves (symlinks followed) inside that root's `.feedvault-trash` folder.
Thumbnails are cached under the trash path in the data directory, and dropped
when the entry is restored or purged.

`POST /api/trash/restore` also takes `{ "keys": ["…"] }`: puts back exactly
those entries (partial deletes included), then re-indexes. Same response as
with `posts`. Either `posts` or `keys` must be a non-empty list.

`POST /api/trash/purge` with `{ "keys": ["…"] }` (at most 5000) permanently
deletes the files of those entries and drops their lines from the manifest.
Unknown keys are ignored. A file is only deleted when its folder (symlinks
followed) is inside the trash folder of a configured media root; anything else
is refused and reported. A trashed symlink is removed itself, never its
target. Lines of files already gone are dropped. Response:

```json
{ "ok": true, "entries": 2, "keys": ["…", "…"], "files": 4, "bytes": 5242880, "dropped": 1,
  "errors": [{ "path": "/abs/.feedvault-trash/x.jpg", "error": "outside the trash folder" }] }
```

Instead of `keys`, `{ "filter": { … } }` purges every entry the same filters
as `/api/trash/items` match (`platform`, `author`, `person` (a number), `since`, `before`; each optional, but
the filter must name at least one, use `/api/trash/empty` for everything).
The match is made under the same lock as the purge itself. The page sends the
time it loaded the list as `before`, so nothing trashed after the user saw
the totals is purged with them.

`entries` counts the entries fully purged and `keys` names them, `dropped`
the lines removed for files that were already missing. An entry with an error
keeps its lines.
Delete, restore and purge never run at the same time (they share the
manifest).

## Review (keep or trash)

A post can be marked **kept**. Deciding to trash it is just `/api/delete`.
Decisions live in their own table, untouched by rescans, and are also written
to `<data_directory>/userdata/decisions.json` (2 s after the last change) so
they survive rebuilding the index. An older `<data_directory>/decisions.json`
is still read when the new file does not exist.

| Method | Path | Returns |
|---|---|---|
| POST | `/api/review` | body `{ "posts": ["instagram:C8x…"], "decision": "keep" }` or `"decision": null` to clear → `{ "ok": true, "posts": [...] }` |
| POST | `/api/trash/restore` | body `{ "posts": ["instagram:C8x…"] }` → moves those posts' files back out of the trash (their most recent deletion) and re-indexes them at once → `{ "ok": true, "posts": [...], "files": 4, "errors": [] }` |

Every post summary carries `"decision": "keep"` or `"decision": null`.
`/api/posts` takes `review=unreviewed` (no decision yet) or `review=kept`, and
`order=asc` for oldest first (default `desc`). `/api/stats` adds
`"kept"` and `"unreviewed"` counts.

Undo in the review screen: after a trash, call `/api/trash/restore` with that
post id; after a keep, call `/api/review` with `decision: null`.

`/api/posts` query parameters, all optional:

- `q`: full-text search over post text, author handle, author name and album (SQLite
  FTS5; plain words, prefix match on the last word). `tag:name` and
  `tag:"two words"` in it are tag filters, not words (see [Tags](#tags)), and
  mix freely with text: `tag:outfits red dress`
- `tag`: a tag name, matched without regard to (ASCII) case. Repeat it for several:
  a post must have all of them (`tag=a&tag=b`). Combined with any `tag:` in `q`;
  a value that cannot be a tag name matches nothing
- `untagged=1`: only posts with no tag
- `platform`: `instagram`, `twitter` (X, x.com included), `tiktok`, or another
  gallery-dl category name for sites without their own mapping (`reddit`, `bluesky`, …)
- `author`: author id (from `/api/authors`) or one of its folder-name aliases;
  either way the whole account's posts (see [People](#people))
- `person`: a person id: posts of every account linked to that person, across
  platforms (see [People](#people)); a value that is not an id matches nothing
- `kind`: one of the kinds above
- `sort`: `posted` (default) or `saved`
- `order`: `desc` (default, newest first) or `asc`
- `review`: `unreviewed` or `kept`
- `offset` (default 0), `limit` (default 60, max 200)

`/api/posts/summary` takes the same filter parameters as `/api/posts` (`q`,
`platform`, `author`, `person`, `kind`, `review`, `tag`, `untagged`; `sort`, `order`, `offset` and `limit`
are ignored) and totals everything they match, not just one page: `posts` is
always equal to the `total` that `/api/posts` returns for the same filters,
`media` and `bytes` count the media items of those posts that are not missing.
It is what a bulk delete of every match would move to the trash (give or take
the files that are not counted, see [Sizes](#sizes)).

Scan status:

```json
{
  "running": false,
  "last": {
    "started_at": 1727500000,
    "finished_at": 1727500004,
    "added": 10,
    "updated": 2,
    "missing": 0,
    "unmatched": 3,
    "errors": [{ "path": "/abs/file.json", "error": "invalid JSON" }]
  }
}
```

`last` is `null` before the first scan. A scan runs on startup.

## Duplicates

Duplicates come in three kinds, two exact and one visual:

- `copies`: the same post downloaded again into another folder (a typo'd
  profile folder, a second download). Only the first copy is indexed as the
  post; the scanner records every other one as an **extra copy** with its
  files, and still lists its metadata file on Unmatched as
  `duplicate of <post id> (<path of the indexed one>)`. A copy whose files
  are gone disappears on the next scan. Items are compared by position:
  size, then the sha1 of the first and last MiB, then the full sha1, so a
  copy damaged in the middle is never called identical. That last read
  costs one more pass over the files of copies that matched so far (on the
  reference archive, about 0.5 GiB).
- `content`: different posts (a repost saved under another id) sharing at
  least one media file with the same size and full sha1. Posts linked
  through any shared file form one group, except through a file more than
  20 posts share (a placeholder or a watermark card is not a repost).
- `similar`: different posts with a picture that looks the same, though
  its bytes differ (a resized or recompressed repost, a re-upload). Two
  pictures match when their perceptual hashes are at most `threshold` bits
  apart (of 64; the config file's `similar_threshold`, 6 by default, or the
  `threshold` parameter, 0 to 10). A group is a post and the posts with a
  picture matching one of its own, so every member looks like that centre
  (matches are not chained: A like B like C does not make A like C). The
  post with the most matches is the first centre, then the next among the
  rest. Left out: posts already in one `content` group, flat pictures (a
  solid colour, a smooth gradient, a black frame: 6 bits or fewer set, or
  unset) and a picture that matches more than 20 other posts. Similar groups
  are never `identical`, so they are resolved one by one, never in bulk.

Hashes are computed by a background worker after every scan, at the lowest
CPU and disk priority, and cached by path, size and mtime. Only files whose
size another file shares, and the files of posts that have extra copies,
are read. It pauses while a scan or a delete runs.

After the content hashes, the same worker takes a perceptual hash (a 64-bit
dHash) of every image and video in the index, for the `similar` kind: from
the cached grid thumbnail when there is one, else from the image or the
video's poster, else from a frame ffmpeg extracts (kept as the thumbnail).
A picture that cannot be decoded is tried again only when its file changes.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/duplicates?kind=&offset=&limit=&threshold=` | groups of one kind, see below |
| GET | `/api/duplicates/status` | the hashing worker's progress, see below |
| POST | `/api/duplicates/resolve` | body `{ "group": "…", "keep": "…" }`, or `{ "groups": [{ "group": "…", "keep": "…" }, …] }` (at most 500), plus `"threshold"` for a similar group: trashes every other member, see below |
| POST | `/api/duplicates/dismiss` | body `{ "group": "…" }`, plus `"threshold"` for a similar group → `{ "ok": true }`: "not a duplicate", for good |

`GET /api/duplicates`: `kind` is `copies` (default), `content` or
`similar`, `offset` (default 0), `limit` (default 50, max 500), `threshold`
(similar only: 0 to 10, default from the config; anything else is a 400).
Groups come biggest saving first. Resolve and dismiss rebuild a similar
group at the threshold they are given, so send the one it was listed at.

```json
{
  "kind": "copies", "threshold": null, "total": 120, "reposts": 0, "identical": 118, "pending": 0,
  "frees": 2147483648, "identical_frees": 2040109465, "dismissed": 1,
  "groups": [{
    "id": "4c1d9e0b7a2f3e5d6c8b", "kind": "copies",
    "identical": true, "pending": false, "differs": [],
    "suggested": "instagram:C8x…", "frees": 5242880, "bytes": 10485760, "repost": false,
    "members": [
      { "id": "instagram:C8x…", "type": "post", "post_id": "instagram:C8x…", "post": { "…": "post summary" },
        "folder": "/abs/cherrieskyl", "meta_path": "/abs/cherrieskyl/….json",
        "paths": ["/abs/cherrieskyl/…_1.jpg"],
        "items": [{ "idx": 1, "kind": "image", "size": 5242880, "path": "…", "hash": "9a0c…", "width": 1080, "height": 1350,
                    "url": "/media/17", "thumb_url": "/media/17/thumb" }],
        "files": 1, "bytes": 5242880, "saved_at": 1727500000, "posted_at": 1727400000, "match": null,
        "kept": false, "thumb_url": "/media/17/thumb" },
      { "id": "copy:3", "type": "copy", "copy_id": 3, "post_id": "instagram:C8x…", "post": null,
        "folder": "/abs/cherrrieskyl", "meta_path": "/abs/cherrrieskyl/…_1.jpg",
        "paths": ["/abs/cherrrieskyl/…_1.jpg"],
        "items": [{ "idx": 1, "kind": "image", "size": 5242880, "path": "…", "hash": "9a0c…", "width": 1080, "height": 1350,
                    "url": null, "thumb_url": null }],
        "files": 1, "bytes": 5242880, "saved_at": 1727400000, "posted_at": null, "match": null,
        "kept": false, "thumb_url": "/media/copy/3/thumb" }
    ]
  }]
}
```

- `id` names the group for resolve and dismiss. It is derived from the
  members, so it changes when a member comes or goes.
- Members: a post (`type: "post"`, `post` is its summary) or an extra copy
  (`type: "copy"`, `post: null`). `id` is the post id or `copy:<n>`.
  `folder` is the folder of the metadata file (for posts rebuilt from file
  names, of the first media file). `items`, `paths`, `files` and `bytes`
  cover media files that are on disk (posters and side files are moved with
  them but not counted). `saved_at` is the post's, or the copy's metadata
  file mtime; `posted_at` the post's (`null` for a copy). An item's `hash`
  is its full sha1, `null` until known (only files that may have a twin
  are fully hashed). `width` and `height` are its pixels, `null` when not
  known (images are read while hashing; videos are measured with ffprobe,
  when installed, once they are in a `content` or `similar` group). `url`
  and `thumb_url` serve the file and its thumbnail (`null` for a copy).
- `match` (content and similar groups): the `idx` of the member's first
  item that matches another member's. The member's `thumb_url` is that
  item's, and the keeper rule compares it.
- `kept`: the post has the "keep" decision. In a `copies` group every member
  shares the post's decision, and it stays with whichever member is kept.
- `repost`: the members are posts by different accounts, so one is most
  likely a repost of another. Only `content` and `similar` groups can be.
- `distance` (similar groups only): the most bits apart a member's
  picture is from the centre's it matches.
- `identical`: `true` when every member has the same files; `false` when
  something differs, and `differs` says what:
  `[{ "member": "copy:3", "idx": 2, "reason": "missing" }]`, with reasons
  `missing` (the post has the item, the copy does not), `extra` (the
  other way round), `size`, `content` (same size, other bytes), and for
  `content` and `similar` groups `only here` (no other member has this
  file, or nothing like it). `null` while some files are not hashed yet
  (`pending: true`). Always `false` for `similar` groups.
- `suggested`: the member to keep.
  - `copies` (one post, several downloads): the one marked kept, then the
    one with more media, then the highest resolution (total pixels of its
    images; only when known for every member, videos are not counted),
    then the oldest `saved_at`, then the shortest path.
  - `content` and `similar` (different posts): the one marked kept, then
    the earliest `posted_at` (the original, not whichever was saved
    first), then the highest resolution of the `match` item (when known
    for every member), then the largest `match` file, then the shortest
    path.
- `frees`: the bytes of every member but the suggested one.
- Top level: `threshold` the similar kind's (`null` for the others);
  `total`, `reposts`, `identical`, `pending` count groups; `frees` and
  `identical_frees` add up all groups (or the identical ones), all pages.
  `dismissed` counts groups of this kind marked "not a duplicate".

`GET /api/duplicates/status`:

```json
{ "running": true, "paused": false, "phase": "partial", "done": 1200, "total": 7496,
  "bytes": 2516582400, "hashed": 6900, "fingerprinted": 85120, "started_at": 1727500000,
  "finished_at": null, "errors": [{ "path": "/abs/x.mp4", "error": "Permission denied" }] }
```

`phase` is `partial` or `full` (content hashes), `dhash` (perceptual
hashes) or `probe` (video sizes from ffprobe), `null` when idle. `done` and
`total` count files in that phase, `bytes` what was read, `hashed` the
files with a content hash, `fingerprinted` those with a perceptual hash,
`finished_at` when the last complete pass ended, `errors` the last 20
files that could not be read.

`POST /api/duplicates/resolve` keeps one member of each group and moves
every other one to the trash, exactly like `/api/delete` (a copy's files go
the same way, with a `copy` field in the manifest, so the Trash page lists
and restores it). Under the same lock as the move, each group is rebuilt
from the index and checked first, and refused (listed in `skipped`, nothing
of it moved) when:

- it no longer exists with these members (a scan or another resolve
  changed it): reload;
- it is still being hashed;
- a file of any member changed (size or mtime) since it was hashed;
- a file of the member to keep is gone;
- a member to trash is the one kept in another group of the same call;
- it is a similar group sent along with other groups (similar groups are
  resolved one at a time).

When the kept member is a copy and the post is trashed, the copy becomes the
post at once, and the post's "keep" decision moves with it.

```json
{ "ok": true, "resolved": ["4c1d9e0b7a2f3e5d6c8b"],
  "skipped": [{ "group": "…", "error": "a file changed since it was hashed; wait for the next pass" }],
  "posts": ["instagram:R9…"], "copies": [3], "files": 7, "bytes": 10485760, "errors": [] }
```

`errors` lists files that could not be moved, as in `/api/delete`. `ok` is
`false` only when nothing could be done (every group skipped, a bad body,
or a scan held the index for more than 30 s).

`POST /api/duplicates/dismiss` stores the group as "not a duplicate". It is
user data, written to `<data_directory>/userdata/dismissed_duplicates.json`
like decisions, and keyed by the members' post ids and copy metadata paths,
so it survives rebuilding the index. It also covers the
same group after a member leaves; a group that gains a member shows again.
Unknown group: 404 `{ "ok": false, "error": "…" }`.

## Tags

Free-form labels, many per post. They are the user's own data: kept in the
`tags` and `post_tags` tables, never touched by a rescan, and written to
`<data_directory>/userdata/tags.json` and `post_tags.json` (2 s after the
last change, `post_tags.json` naming tags by name, not id) so a rebuilt
index gets them back.

- Names are compared without regard to case for ASCII letters only (`Outfits` and `outfits` are one
  tag, the first spelling kept; `Été` and `été` are two). Spaces inside a name are collapsed, and
  a name is 1 to 64 characters with no `"` and no control characters.
- Tags are keyed by post id. A post moved to the trash keeps its tags, so
  restoring it, or a duplicate copy taking its place (Duplicates, keep the
  copy), brings them back. When the trash is emptied or entries purged, the
  posts whose entries were deleted lose their tags if they are neither in the
  index nor in another trash entry (a post out of the index for another
  reason, such as its root being offline, keeps them).
- Duplicates, keeping one post of a "same content" group (another post id):
  the kept post also gets the tags and collection places of the posts
  trashed for it.
- Counts only cover posts in the index (not those in the trash).

| Method | Path | Returns |
|---|---|---|
| GET | `/api/tags` | `[{ "name": "outfits", "color": null, "count": 12 }]`, most used first |
| POST | `/api/tags/apply` | body `{ "posts": ["instagram:C8x…"], "add": ["outfits"], "remove": ["todo"] }` → see below |
| POST | `/api/tags/rename` | body `{ "from": "outfit", "to": "outfits" }` → `{ "ok": true, "name": "outfits", "merged": true }` |
| POST | `/api/tags/delete` | body `{ "name": "outfits" }` → `{ "ok": true, "posts": 12 }`: removes the tag from every post |

`/api/tags/apply` adds and removes tags on up to 5000 posts at once (more is
a 400). `add` and `remove` are lists of names, either may be omitted but not
both; a name in `add` that does not exist yet is created. Ids that are not
in the index are ignored. Response:

```json
{ "ok": true, "posts": ["instagram:C8x…"], "added": 3, "removed": 1, "created": ["outfits"] }
```

`posts` lists the ids that exist, `added` and `removed` count the links that
actually changed, `created` the new tags.

`/api/tags/rename` renames a tag. When `to` already names another tag, the
two are merged: every post of `from` gets `to`, and `from` is gone
(`merged: true`). Changing only the case of a name is a rename. An unknown
`from` is a 404, a bad `to` a 400. `/api/tags/delete` of an unknown name is a
404.

`color` is reserved for later and always `null` for now.

## Collections

Named, ordered sets of posts, with a cover. A post can be in several. User
data like tags: tables `collections` and `collection_posts`, untouched by
rescans, written to `<data_directory>/userdata/collections.json` and
`collection_posts.json` (posts keyed by post id, collections by name), and
the same trash rules: a post in the trash keeps its places, and loses them
when its trash entries are deleted for good (emptied or purged) and it is
neither indexed nor in another trash entry. Names are unique without regard to (ASCII) case, 1 to 64
characters, no `"` or control characters.

A **collection**:

```json
{ "id": 3, "name": "Moodboard", "count": 24, "created_at": 1727500000,
  "cover_post": "instagram:C8x…", "cover": { "kind": "image", "url": "/media/17/thumb" } }
```

- `count` covers posts in the index (not those in the trash).
- `cover_post` is the post chosen as cover, `null` when none is chosen; then
  `cover` is that of the first post in the collection. `cover` is a post
  summary's `cover` (see [Post](#post)), `null` for an empty collection.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/collections` | `[collection, …]`, in the order they were created |
| POST | `/api/collections` | body `{ "name": "Moodboard" }` → `{ "ok": true, "collection": {…} }`; 400 for a bad or taken name |
| GET | `/api/collections/<id>?offset=&limit=` | `{ "collection": {…}, "total": 24, "posts": [summary, …] }` in the collection's order; `limit` default 60, max 200; 404 if unknown |
| POST | `/api/collections/<id>/rename` | body `{ "name": "…" }` → `{ "ok": true, "collection": {…} }`; 400 for a bad or taken name |
| POST | `/api/collections/<id>/delete` | → `{ "ok": true, "posts": 24 }`; the posts stay |
| POST | `/api/collections/<id>/add` | body `{ "posts": ["instagram:C8x…"] }` (at most 5000) → `{ "ok": true, "added": ["instagram:C8x…"] }`, appended at the end in the order given; posts not in the index or already there are skipped |
| POST | `/api/collections/<id>/remove` | body `{ "posts": […] }` → `{ "ok": true, "removed": 2 }` |
| POST | `/api/collections/<id>/order` | body `{ "posts": […] }` → `{ "ok": true }`, see below |
| POST | `/api/collections/<id>/cover` | body `{ "post": "instagram:C8x…" }`, or `null` for the first post → `{ "ok": true, "collection": {…} }`; 400 if the post is not in it |

Every `/api/collections/<id>/…` call answers 404 `{ "ok": false, "error": … }`
for an unknown id.

`/order` takes posts of the collection in their new order and puts them in
the places those same posts held before, the rest staying where they are.
Sending one page in its new order reorders that page; sending every post
reorders the whole collection. Ids not in the collection are ignored.

## People

One person often posts on several platforms, under different handles. An
**account** is a platform and an author id, as indexed (instaloader's
`owner.id`, gallery-dl's `author.id`); a **person** links accounts into one.
An account with no person is shown on its own, as before.

People and their links are user data: tables `people` and `person_accounts`,
never touched by a rescan, and written to
`<data_directory>/userdata/people.json` and `person_accounts.json` (accounts
by platform and author id, people by name) 2 s after the last change, so a
rebuilt index gets them back. Links are by account, not by post: a post
trashed and restored, or replaced by a duplicate copy, keeps its person.
Nothing moves on disk.

Posts rebuilt from file names have no id: their author id is the profile
folder's name. When the same folder also holds posts with metadata whose
handle is that name (exactly one account), the folder name is an **alias** of
that account's id. Aliases are derived on every scan, not stored as user
data. An account and its aliases read as one: one row in `/api/authors` and
Storage, one link (linking or unlinking an alias acts on the account), and
the `author` and `person` filters take the aliases' posts in. No alias is
made between two ids linked to different people (merging them is the
user's call), and once every post of the id is gone the folder name is an
account of its own again.

An **account** (`/api/authors` rows, a person's `accounts`):

```json
{ "platform": "instagram", "id": "123456", "handle": "somebody", "name": "Some Body",
  "aliases": ["somebody"], "count": 812, "bytes": 2147483648, "newest": 1727481600,
  "url": "https://www.instagram.com/somebody/", "person": { "id": 3, "name": "Some Body" },
  "handles": [{ "handle": "somebody", "first": 1700000000, "last": 1727481600 },
              { "handle": "some.body.old", "first": 1600000000, "last": 1690000000 }],
  "names": [{ "name": "Some Body", "first": 1600000000, "last": 1727481600 }] }
```

- `handle` and `name` are those of the newest post (handles change).
- `count` and `bytes` cover the posts in the index, aliases included;
  `newest` is the newest `posted_at`.
- `url`: the profile's address for `instagram`, `twitter` and `tiktok`,
  `null` otherwise.
- `person`: the person the account is linked to, or `null`.
- `handles` and `names`: **handle history**, every handle and display name
  the account's posts (aliases included) carry, with the `posted_at` of the
  first and last post under it, the most recent first. Derived from the
  posts on every request (cached), not stored: a renamed account keeps its
  id, so its posts stay one account, and its old handles are listed here.
  `first` and `last` are `null` when no post under it has a date. The
  dashboard's Creators search and Feed author picker match any of them.

A **person**:

```json
{ "id": 3, "name": "Some Body", "notes": "", "created_at": 1727500000,
  "accounts": [account, …], "platforms": ["instagram", "twitter"],
  "count": 900, "bytes": 2347483648, "newest": 1727481600 }
```

- `name`: 1 to 64 characters, no `"` or control characters, unique without
  regard to (ASCII) case. `notes`: free text, at most 5000 characters.
- `accounts`: most posts first. An account linked but not in the index
  (before the first scan of a rebuilt index, or every post trashed) is listed
  with `count` 0 and `handle` `null`.
- `count`, `bytes`, `newest`: over every account.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/people` | `[person, …]`, by name |
| POST | `/api/people` | body `{ "name": "…", "accounts": [{ "platform": "instagram", "id": "123456" }] }` (`accounts` may be omitted) → `{ "ok": true, "person": {…} }` |
| GET | `/api/people/<id>` | person, or 404 |
| POST | `/api/people/<id>` | body `{ "name": "…" }` and/or `{ "notes": "…" }` → `{ "ok": true, "person": {…} }` |
| DELETE | `/api/people/<id>` | → `{ "ok": true, "unlinked": 2 }`: the person and its links are gone, never a post |
| POST | `/api/people/<id>/accounts` | body `{ "add": [{ "platform", "id" }], "remove": [{ "platform", "id" }] }` (either may be omitted) → `{ "ok": true, "added": 1, "removed": 0, "person": {…} }` |
| POST | `/api/people/merge` | body `{ "ids": [3, 7], "name": "…", "accounts": [{ "platform", "id" }] }` → `{ "ok": true, "person": {…} }`, see below |

- An account belongs to one person at most: adding it to a person (create,
  add, merge) takes it from any other. An account that is not in the index
  (no post, no alias) is a 400.
- `added` and `removed` count the links that changed.
- Merge keeps the first id: its name (or `name`, when given), and the
  others' notes appended to its own. Every account of the others, and
  `accounts`, move to it; the others are gone. `ids` must all exist (404
  otherwise); with one id, `accounts` must not be empty.
- A bad name, a name taken by another person, bad notes or a malformed
  account list is a 400 `{ "ok": false, "error": "…" }`; an unknown id a 404.

### Link suggestions

Accounts likely to be one person, found in the index and in metadata already
downloaded. Nothing is ever fetched.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/people/suggestions` | `{ "suggestions": [suggestion, …], "dismissed": 2 }`, most likely first |
| POST | `/api/people/suggestions/dismiss` | body `{ "id": "…" }` → `{ "ok": true }`: "not the same person", for good; 404 when the id is not listed (reload) |

```json
{ "id": "4c1d9e0b7a2f3e5d6c8b", "score": 0.92, "reason": "same_handle",
  "reasons": [{ "reason": "same_handle", "detail": "@somebody" },
              { "reason": "same_name", "detail": "Some Body" }],
  "accounts": [account, …], "person": null }
```

- `reason` (the strongest of `reasons`) and its `score`:
  - `bio_link` (0.95): an account's bio or website, as its metadata has it,
    links to another indexed account (`instagram.com/<handle>`,
    `x.com/<handle>` or `twitter.com/<handle>`, `tiktok.com/@<handle>`,
    any of its handles, old ones too). Read on every scan from instaloader's
    Profile file (`<handle>_<id>.json[.xz]`: `biography`, `external_url`,
    `bio_links`) and from gallery-dl's author dict of the newest post
    (`description`/`signature`, `url`).
  - `same_handle` (0.9): one handle (any in the account's history, or an
    alias) on several platforms.
  - `similar_handle` (0.7): handles equal once case, `.` `_` `-`, a leading
    `the`, `real`, `its` or `official`, a trailing `official` and trailing
    digits are set aside (`foo`, `foo_`, `thefoo`, `foo2`), but not two
    different numbers (`foo1`, `foo2`).
  - `same_name` (0.6): one display name, compared without case, accents,
    emoji and punctuation, at least 4 letters.
  A group found for several reasons scores 0.02 more per extra reason.
- `detail`: what matched, for people.
- `accounts`: the accounts of the group, most posts first. `person`: the
  person one of them is linked to, or `null`. Only groups where linking
  changes something are listed: at least one account has no person, and at
  most one person is involved (two people are a merge, left to the user).
  Linking is `POST /api/people` (`person` `null`) or
  `/api/people/<id>/accounts` with the rest.
- A dismissal is user data, written to
  `<data_directory>/userdata/dismissed_suggestions.json` and keyed by the
  group's accounts, so it survives rebuilding the index; a group that gains
  an account shows again.

## Sources

A **source** is where a person's posts come from: a tool and its target
(`instaloader` and a profile name, `gallery-dl` or `yt-dlp` and a profile
link), and the folder inside a media root the tool writes to. Clicking
**Sync** runs the tool for that source as a [job](#jobs) and indexes the
folder when it ends, so only new posts are downloaded and they show up in
the Feed without a terminal.

A source is added by pasting a profile link: the link's host picks the tool
from the [routing table](#link-routing).

Sources are user data: table `sources`, written to
`<data_directory>/userdata/sources.json` (by tool and target, the person by
name, the last sync's outcome included) 2 s after the last change, so a
rebuilt index gets them back. One source per tool and target.

A source belongs to an **account** once one is known, and is shown with the
person that account is linked to: linking the account later moves the source
with it. A source added to a person for a profile that has no posts yet has
`account` `null` and keeps that person; its first sync that indexes posts
sets `account`, and links that account to the person when the account has
no person yet.

```json
{ "id": 4, "tool": "instaloader", "platform": "instagram", "target": "somebody",
  "url": "https://www.instagram.com/somebody/",
  "folder": "/archive/instaloader/somebody",
  "account": { "platform": "instagram", "id": "somebody" },
  "person": { "id": 3, "name": "Some Body" },
  "options": { "full_history": false, "session": null },
  "created_at": 1727500000, "last_sync_at": 1727503600, "last_job_id": 41,
  "last_result": { "state": "failed", "error": "rate_limited",
                   "message": "Instagram is limiting requests: wait before syncing again",
                   "line": "…429 - Too Many Requests…", "added": 0, "job": 41, "outdated": false },
  "job": { "id": 42, "state": "queued", "waits_until": 1727503660 } }
```

- `target`: instaloader: the profile name, lowercase; gallery-dl and yt-dlp:
  the profile link, normalized (see [Link routing](#link-routing)). `url`:
  its address on the site (the link itself for a link).
- `platform`: what its posts are indexed under: `instagram`, or from the
  link's host (`twitter` for x.com and twitter.com, `tiktok`, `youtube`,
  `reddit`, `bluesky`, `pixiv`; another host, the first part of its name).
- `folder`: absolute, inside a media root (it need not exist before the
  first sync; the sync creates it).
- `account`: the indexed account the source belongs to (an alias is
  replaced by the id it stands for), or `null`. `person`: the account's
  person, else the person the source was added to, else `null`.
- `options`:
  - `full_history`: `false` (default): the first sync starts after the
    newest post FeedVault already has for the account, see below. `true`:
    the next sync walks the whole profile and downloads every post not in
    the folder yet (without `--fast-update`, the stamp dropped first); once
    it succeeds, it is set back to `false`.
  - `session`: `null` to use the global setting (see
    [instaloader settings](#instaloader-settings) and
    [gallery-dl and yt-dlp settings](#gallery-dl-and-yt-dlp-settings)), or
    one of the session values (gallery-dl and yt-dlp: none or cookies).
- `last_sync_at`: when the last sync ended (any outcome), or `null`.
- `last_result`: how it ended, or `null` before the first sync:
  - `state`: the job's (`done`, `failed`, `cancelled`, `interrupted`)
  - `error`: `null` when it worked, else what the output says went wrong:
    `login_required` (the site wants a logged-in session),
    `private` (a private profile the session does not follow),
    `not_found` (no such profile: renamed or deleted),
    `rate_limited` (HTTP 429, "Please wait a few minutes"), or `generic`.
    An HTTP 403 counts as `login_required`: it is how Instagram turns away
    an anonymous client, after which instaloader reports the profile as
    missing. gallery-dl and yt-dlp failures are classified from their
    error lines the same way (gallery-dl's `AuthRequired`, `NotFoundError`,
    "Tweets are protected"; yt-dlp's "Sign in to confirm", "Private video",
    "Video unavailable"). `missing`: the tool was not found (no output);
    the dashboard links `missing` and `login_required` to Settings →
    Downloaders
  - `message`: one line for people; `line`: the tool's last line of output
    behind it, or `null`. When the latest-version check is on (see
    [Downloaders](#downloaders)) and the tool is older than PyPI's latest
    release, a failed sync's message ends with
    `. yt-dlp 2026.01.01 is out of date (2026.08.06 is out): update it in
    Settings → Downloaders`, and `outdated` is `true` (else `false`)
  - `added`: new posts indexed (also after a failure: what was downloaded
    before it stopped is indexed)
- `job`: the source's sync while it is queued or running (`waits_until`,
  see [Jobs](#jobs)), else `null`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/sources` | `{ "sources": [source, …], "suggestions": [suggestion, …] }`, sources by target |
| GET | `/api/sources/resolve?url=…` | what adding that link would make, shown before saving: `{ "ok": true, "tool": "yt-dlp", "platform": "tiktok", "target": "https://tiktok.com/@someone", "folder": "/archive/tiktok/someone", "source": null }` (`source`: the id of the source already there for it). `{ "ok": false, "error" }` (still a 200: it answers the question) for a link that is not accepted |
| POST | `/api/sources` | body `{ "target": "…", "tool": "…", "folder": "/abs", "person": 3, "account": { "platform", "id" }, "options": {…} }` → `{ "ok": true, "source": {…} }` |
| GET | `/api/sources/<id>` | source, or 404 |
| POST | `/api/sources/<id>` | body `{ "options": {…} }` (the keys sent change) → `{ "ok": true, "source": {…} }` |
| DELETE | `/api/sources/<id>` | → `{ "ok": true }`: the source is forgotten; its folder, files and posts stay. 409 while its sync is queued or running |
| POST | `/api/sources/<id>/sync` | → `{ "ok": true, "job": {…} }`; 409 when its sync is already queued or running; 400 when it cannot be synced (its folder is no longer inside a media root) |
| POST | `/api/sources/sync-all` | → `{ "ok": true, "jobs": [job, …], "skipped": 1, "errors": [{ "source": 5, "error": "…" }] }`: a sync per source, by target, queued one after another; sources already queued or running are skipped, and those that cannot be synced (folder no longer inside a media root) listed in `errors` |

- `target`: a profile link. Its host picks the tool from the routing
  table; `tool` is optional, and when sent must be that tool (else a 400
  naming it). With `"tool": "instaloader"`, a profile name or `@name` works
  too. An Instagram link (`https://www.instagram.com/name/`, with or without
  `www.`, a query string or a trailing slash) gives instaloader the profile
  name. Anything else (a post or reel link, a name with other characters, a
  host not in the table) is a 400.
- `folder`: optional. Absent: `<first media root>/<target>` for
  instaloader, `<first media root>/<platform>/<name>` for a link, the name
  being the link's first path part that is not a page kind (`/user/`,
  `/media`, `/en/`…), lowercase, without `@`. Present: an absolute path
  that resolves inside a media root (symlinks followed); anything else is
  a 400.
- `person` (an id) and `account` are optional. With `account`, the source
  belongs to that indexed account (unknown: 400); without, to the account of
  the folder's posts when there are some, else (a link) to the one account
  of that platform whose handle, current or old, is the link's profile
  name, any case.
- A second source for the same tool and target is a 400, the target
  compared in any case, and so is one for the same tool and folder (an
  x.com and a twitter.com link to one profile).
- The request never carries flags, paths to run or a command: the sync job
  takes the source id only and builds everything from the stored source.

**Suggestions.** Profile folders that already hold instaloader posts but no
source, one per folder right under a media root, for the user to confirm
(`POST /api/sources` with the suggestion's `tool`, `target`, `folder` and
`account`). FeedVault never creates a source on its own.

```json
{ "tool": "instaloader", "platform": "instagram", "target": "somebody",
  "folder": "/archive/instaloader/somebody", "account": { "platform": "instagram", "id": "somebody" },
  "handle": "somebody", "count": 812, "person": null }
```

`target` is the account's current handle when its posts have metadata and
the handle is a valid profile name, else the folder's name (file names alone
do not say whose profile a folder is: a stray file can be named after
someone else).

### Link routing

`GET /api/config` has `"routes": { "<host>": "<tool>", … }`;
`POST /api/config` with `{ "routes": {…} }` replaces the table (1 to 100
entries). The defaults:

| Host | Tool |
|---|---|
| `instagram.com` | `instaloader` |
| `x.com`, `twitter.com`, `reddit.com`, `bsky.app`, `pixiv.net` | `gallery-dl` |
| `youtube.com` | `yt-dlp` |
| `tiktok.com` | `yt-dlp` (or `gallery-dl`) |

- A host is a lowercase domain name (`www.` is dropped); a tool one of
  `instaloader`, `gallery-dl`, `yt-dlp`, and `instaloader` only for
  `instagram.com`. Anything else is `{ "ok": false, "error" }` and nothing
  is saved. A broken table in `config.json` counts as the defaults.
- A link matches an entry when its host is the entry or a subdomain of it
  (`www.x.com`, `mobile.twitter.com`, `m.youtube.com`); the longest entry
  wins. A host that only starts or ends like one (`x.com.evil.example`,
  `evilx.com`) matches nothing.
- Accepted links: `http` or `https` (a missing scheme is `https`), no login
  part (`user@`), no port other than 80 or 443, a host name (not an IP
  address), and a path of letters, digits and `. _ ~ @ % + -` between
  slashes, not empty, no `.` or `..` part, at most 500 characters. Not a
  page that is what its query string says (`/watch?v=`, `/playlist?list=`,
  `/search?q=`, …): the query is dropped, so such a link is refused.
- Normalized to `https://<host><path>`: lowercase host without `www.`,
  `m.` or `mobile.`, no query string or fragment, no repeated or trailing
  slash. That is the stored target, the same however the link was pasted;
  the sync checks it again (still normalized, host still in the table) and
  gives it to the tool after `--`.

### How a sync runs

Job kind `instaloader-sync`, group `instaloader` (one instaloader at a
time), params `{ "source": "<id>" }` and nothing else. The argument list
comes from the stored source:

```
instaloader --latest-stamps <data_directory>/instaloader/stamps.ini --fast-update
            --no-compress-json --dirname-pattern <folder> --filename-pattern <pattern>
            --title-pattern {date_utc}_UTC_{typename} [session flags] -- <target>
```

- **Incremental.** `--latest-stamps` keeps, per profile, the time of the
  newest post downloaded, in FeedVault's data directory, not next to the
  media: instaloader stops at it whatever files exist (trashed posts are
  not downloaded again). `--fast-update` also stops at the first file that
  exists.
- **First sync.** When `stamps.ini` has no entry for the target yet, it is
  seeded with the newest trustworthy `posted_at` FeedVault has for the
  source's account (and the account's numeric id, when it has one), so the
  first sync only fetches what is newer instead of walking the whole
  profile again. Not with `options.full_history`, nor for a source with no
  account. Trustworthy: a post with metadata, or a filename-only post whose
  name carries a date, counted no later than the end of that day (UTC). A
  name without a date (`{target} - {shortcode}`) only has the file's mtime,
  which a copy may have made later than posts never downloaded: such posts
  are left out, and an account with nothing else gets no seed (the job log
  says `first sync: no reliable date, fetching full history`).
- **Metadata on.** `--no-compress-json` writes each post's JSON beside its
  media, so new posts get captions, stats and the account's numeric id.
  The folder's name becomes an alias of that id (see [People](#people)),
  so old filename-only posts and new ones are one account.
- **Names like the folder's.** `--filename-pattern` is detected from the
  folder's files when the sync is queued, so new files sit beside the old
  ones and a post already there is recognised: `{target}-{date_utc:%Y-%m-%d}-{shortcode}`
  (the common filename-only layout, and the default for an empty or unclear
  folder), `{target} - {shortcode}` (the older layout), or
  `{date_utc}_UTC` (instaloader's own default, in folders it wrote with
  metadata). A folder path holding `{` or `}` is escaped for instaloader.
- **Session**: none (default), or `--load-cookies <browser>`, or
  `--login <user>`; see [instaloader settings](#instaloader-settings).
- **Pause.** After an `instaloader-sync` job ends, the next one waits the
  configured pause (default 60 s) before it starts, so several profiles
  never hit Instagram back to back. A queued job that waits says until when
  in `waits_until`.
- **Result.** The source folder is indexed when the job ends (exit code 0
  or not, but not when cancelled): `result` `{ "added", "updated", "error",
  "line" }`, `message` `"3 new posts"`, or for a failure the plain-language
  message of `error`. The outcome is stored on the source (`last_result`).

### instaloader settings

`GET /api/config` has `"instaloader": { "session": {…}, "pause": 60 }`;
`POST /api/config` with `{ "instaloader": { "session": {…} } }` and/or
`"pause"` changes them.

| `session` | Flags | What it means |
|---|---|---|
| `{ "mode": "none" }` (default) | none | Anonymous: public profiles only, and Instagram rate-limits sooner |
| `{ "mode": "cookies", "browser": "firefox" }` | `--load-cookies firefox` | instaloader reads that browser's Instagram cookies itself. `browser`: `firefox`, `chrome`, `chromium`, `brave`, `edge` |
| `{ "mode": "login", "user": "name" }` | `--login name` | instaloader uses the session file it saved after a `instaloader --login name` run in a terminal. Without one, the sync fails (`login_required`); it never asks for a password |

FeedVault only passes the browser's name or the user name on. It never
stores, reads or sends cookies, passwords or session files. `pause`: whole
seconds from 0 to 3600.

### gallery-dl and yt-dlp syncs

Job kinds `gallery-dl-sync` (group `gallery-dl`) and `yt-dlp-sync` (group
`yt-dlp`), params `{ "source": "<id>" }` and nothing else. The source must
have that tool; its target is checked again (a normalized https link whose
host is still in the routing table; a source keeps its tool when the table
later routes the host to another one) and so is its folder
(inside a media root; for yt-dlp, without `$`, which yt-dlp would expand):

```
gallery-dl --write-metadata --download-archive <data_directory>/gallery-dl/archive.sqlite3
           -o skip=abort:5 -D <folder> [--cookies-from-browser <browser>] -- <link>

yt-dlp --write-info-json --write-thumbnail --download-archive <data_directory>/yt-dlp/archive.txt
       [--break-on-existing] -o <folder>/%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s
       [--match-filters "duration <= <youtube_max_seconds>"] [--cookies-from-browser <browser>] -- <link>
```

- **Incremental.** Each tool's download archive, in FeedVault's data
  directory, lists what it already has: gallery-dl stops after 5 files in
  a row that it has (`skip=abort:5`), yt-dlp at the first video it has
  (`--break-on-existing`, exit code 101, which counts as success). With
  `options.full_history` the whole profile is walked (`-o skip=true`, no
  `--break-on-existing`), still skipping what the archive lists. A YouTube
  channel's own page (`/@name`, `/channel/<id>`, not a tab such as
  `/shorts`) never gets `--break-on-existing`: it lists the Videos tab and
  then the Shorts tab, and stopping in the first would never reach the
  second. Nor does a TikTok profile: it lists its pinned videos (up to 3,
  usually old and so archived) first, and stopping at them would never
  reach a new video. Its sync pages through the whole listing (15 videos
  per request), the archive still skipping what it lists. Which platforms
  may stop is a table in `sync.py` (`STOPS_AT_ARCHIVED`).
- **First sync.** When the source has never synced, the archive is seeded
  first with what FeedVault already indexed for its account (aliases
  included): gallery-dl gets the entry of every file of the account's
  gallery-dl posts (from their metadata JSONs: `<category>` and the
  extractor's archive format, for twitter, tiktok, instagram, reddit,
  bluesky and pixiv), yt-dlp a line `<platform> <id>` for every post. The
  job log says how many. With `options.full_history` too (the profile is
  walked, what is indexed is still not fetched again); not for a source
  with no account yet.
- **YouTube.** A youtube.com source only downloads videos up to
  `youtube_max_seconds` long (the same rule the parser applies: longer
  ones are left to ChannelVault).
- **The folder.** `-D` (gallery-dl) puts every file directly in it; the
  yt-dlp template is the folder with `%` doubled, so it stays literal.
- **Trash.** Trashing a post adds it to the archives so no sync brings it
  back; restoring it takes out what trashing added (see [Deleting](#deleting)).
- **Result**: as for instaloader: the folder is indexed when the job ends,
  `result` `{ "added", "updated", "error", "line" }`, stored on the source.
  A non-zero exit whose error lines are all about single items (yt-dlp's
  `ERROR: [youtube] <id>: Private video`, against `[youtube:tab]` or
  `[tiktok:user]` for the profile; gallery-dl's `[download][error] Failed
  to download …`), and are not a rate limit or a login wall, is `done`:
  `message` says how many items could not be downloaded, `line` the last
  of them.
- **Pause.** The next sync of the same tool waits the tool's `pause`
  (default 30 s); see [gallery-dl and yt-dlp settings](#gallery-dl-and-yt-dlp-settings).

### gallery-dl and yt-dlp settings

`GET /api/config` has `"gallery-dl": { "session": {…}, "pause": 30 }` and
`"yt-dlp": { "session": {…}, "pause": 30 }`; `POST /api/config` with
either (`session` and/or `pause`) changes it.

| `session` | Flags | What it means |
|---|---|---|
| `{ "mode": "none" }` (default) | none | Anonymous: public profiles only |
| `{ "mode": "cookies", "browser": "firefox" }` | `--cookies-from-browser firefox` | The tool reads that browser's cookies itself. `browser`: `firefox`, `chrome`, `chromium`, `brave`, `edge` |

FeedVault only passes the browser's name on. It never stores, reads or
sends cookies.

`pause`: whole seconds from 0 to 3600 (default 30). As for instaloader,
after a `gallery-dl-sync` job ends the next one waits that long before it
starts (`waits_until`), and the same for `yt-dlp-sync`; one tool's pause
never holds the other's syncs.

`youtube_max_seconds`: whole seconds from 1 to 86400 (default 180).

#### Cookies in info JSONs

yt-dlp copies the cookies it sent for a video into that video's info JSON
(`cookies` in each format and at the top, a `Cookie` line in their
`http_headers`), in the media folder. So after every `yt-dlp-sync` job (the
cookies can come from `--cookies-from-browser` or from the user's own
yt-dlp config), once yt-dlp has exited (cancelled or not) and before the
folder is indexed, FeedVault rewrites the info JSONs right in the source's
folder that are new or changed since just before yt-dlp started (compared
with a listing of their names and mtimes taken then, not with the clock):
every `cookies` key at any depth and every `Cookie` header inside an
`http_headers` are dropped, everything else stays as it was once parsed.
The rewrite is atomic (a temporary file in the same folder renamed over the
JSON) and keeps the file's mode, mtime and, when FeedVault may set it, its
owner. Only regular files (never a symlink) that parse as yt-dlp's
(`extractor_key`, `id`, `webpage_url`) are touched. The job log says
`cookies removed from N info JSONs`; a file that cannot be rewritten gets a
log line and does not change how the sync ended.

gallery-dl's metadata JSONs hold no cookies: it keeps request headers and
cookies in private `_http_*` keys, which `--write-metadata` leaves out.

`POST /api/yt-dlp/info-json-cookies` does the same for every `*.info.json`
under the media roots (symlinked folders not followed), for folders
synced before this:

```json
{ "ok": true, "applied": false, "checked": 120, "files": 37, "failures": 0, "failed": [] }
```

`checked`: info JSONs looked at; `files`: yt-dlp ones holding cookies
(with `apply`, cleaned); `failed`: the first 20 `{ path, error }` (a file
or folder that could not be read, a file that could not be rewritten),
`failures` how many in all. `400` for an
`apply` that is not a boolean, `409` while a yt-dlp sync runs or another
check is under way. Settings counts first (`apply` false), then asks to
confirm.

## Storage

`GET /api/storage`:

```json
{
  "totals": { "posts": 43116, "media": 85985, "bytes": 87606399138 },
  "by_author": [
    { "platform": "instagram", "id": "123456", "handle": "somebody", "name": "Some Body",
      "aliases": [], "person": { "id": 3, "name": "Some Body" },
      "posts": 812, "media": 1630, "bytes": 2147483648, "kept_bytes": 104857600, "unreviewed_bytes": 2042626048 }
  ],
  "by_kind": [{ "kind": "video", "posts": 18897, "media": 20442, "bytes": 61203283968 }],
  "by_year": [{ "year": 2023, "posts": 9120, "media": 18004, "bytes": 15032385536 }],
  "largest": [
    { "media_id": 18, "post": "instagram:C8xYzAbCdEf", "platform": "instagram", "post_id": "C8xYzAbCdEf",
      "author": { "id": "123456", "handle": "somebody" }, "kind": "video", "bytes": 524288000,
      "thumb_url": "/media/18/thumb" }
  ],
  "trash": { "files": 12, "bytes": 1048576 }
}
```

- `totals.posts` counts every post in the index (the same number as
  `/api/stats`); `media` and `bytes` leave out media items marked missing.
  `bytes` therefore equals `/api/stats` `bytes`; `media` can be lower than its
  `media`, which counts every indexed item.
- `by_author`: one row per account (posts without an author are only in the
  totals), biggest first, folder-name aliases counted with the account they
  stand for. Handle and name are the newest post's, as in `/api/authors`;
  `aliases` and `person` are the account's (see [People](#people)). `kept_bytes` is the share in posts marked kept,
  `unreviewed_bytes` the share in posts with no decision; today they add up to
  `bytes`.
- `by_kind`: by post kind (the Feed's `kind` filter), biggest first.
- `by_year`: by the year of `posted_at` in UTC, oldest first; posts without a
  date come last with `"year": null`.
- `largest`: the 100 biggest media items, biggest first. `thumb_url` is `null`
  for a video with neither a poster file nor ffmpeg to grab a frame. Trash one
  with `POST /api/delete` and `{ "media": [media_id] }`.
- `trash`: what is waiting in the trash folders (the totals of `GET
  /api/trash`). It still takes disk space until the trash is emptied.
- `?person=<id>`: everything above but `trash` covers that person's posts
  only, filtered as `/api/posts?person=` does, so `totals` equals
  `/api/posts/summary?person=`.

### Sizes

Every size is the `size` the scanner recorded for a media file (the photo or
video itself), and media items marked missing are skipped. Poster images,
thumbnails, metadata JSON, caption and other side files are not counted, so
deleting a post frees a little more than its `bytes`, and the trash total is
measured on disk instead.

`/api/storage`, `/api/authors` and `/api/posts/summary` are computed in SQL
from the index, never by walking the media folders (only the trash total is
read from disk). Their answers are cached until anything in the index changes
(a scan, a delete, a restore, a review decision, a tag change).

## Jobs

Command-line tools run as **jobs**, started from the dashboard (later from
the userscript too). A request never carries a command, a path to run or
shell text: it names a **kind** and gives its parameters, and the kind,
defined in `backend/jobs.py`, checks them and builds the argument list. It
runs without a shell, so every parameter is exactly one argument. An unknown
kind, an unknown parameter or a bad value is a 400.

One job runs at a time per **lock group** (one instaloader session at a
time); jobs of different groups run side by side, at most 2 at once. The
rest wait in the queue, oldest first.

A **job**:

```json
{
  "id": 12,
  "kind": "tool-version",
  "label": "Check a tool's version",
  "params": { "tool": "yt-dlp" },
  "argv": ["yt-dlp", "--version"],
  "cwd": "/home/me/.local/share/feedvault",
  "group": "tool-version",
  "state": "done",
  "created_at": 1727500000, "started_at": 1727500000, "ended_at": 1727500001,
  "exit_code": 0,
  "rescan": null,
  "result": { "version": "2024.08.06" },
  "message": "2024.08.06",
  "waits_until": null
}
```

- `state`: `queued` → `running` → `done` (exit code 0) | `failed` |
  `cancelled` | `interrupted` (FeedVault stopped while it was queued or
  running; set on quit, or on the next start after a crash, and then
  `ended_at` is `null`: when it stopped is unknown).
- `argv`: for display. The first item is the tool's name; the path actually
  run is resolved when the job starts (see [Tools](#tools)).
- `rescan`: a folder inside a media root, or `null`. When the job exits 0,
  that folder (with its subfolders) is indexed and `result` is
  `{ "added": 3, "updated": 0 }`.
- `result`: what the job produced, by kind, or `null` (failed, cancelled).
  A job whose tool was not found has `{ "error": "missing" }`.
- `message`: one line for people: `"3 new posts"`, the version, the last
  line of output of a failed job (or `"exit code 2"`), `"<tool> not found;
  set its path in Settings"`, `"cancelled"`, `"FeedVault stopped while it ran"`.
- `waits_until`: for a queued job held by its kind's pause (see
  [How a sync runs](#how-a-sync-runs)), when it may start; else `null`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/jobs` | `{ "running": 1, "queued": 0, "jobs": [job, …], "sync_all": batch }`: queued and running jobs and the last 100 ended ones, newest first; `sync_all` see [Sync all](#sync-all) |
| GET | `/api/jobs/kinds` | `[{ "kind": "tool-version", "label": "…", "params": { "tool": { "type": "choice", "choices": ["instaloader", "gallery-dl", "yt-dlp", "ffmpeg"] } } }]` |
| POST | `/api/jobs` | body `{ "kind": "tool-version", "params": { "tool": "yt-dlp" } }` → `{ "ok": true, "job": {…} }`; 400 `{ "ok": false, "error": "…" }` |
| GET | `/api/jobs/<id>` | job, or 404 |
| GET | `/api/jobs/<id>/log?after=<n>` | output lines numbered above `n` (default 0), see below; 404 if unknown |
| POST | `/api/jobs/<id>/cancel` | → `{ "ok": true, "job": {…} }`; 404 if unknown, 409 if it has already ended, or if its process has exited and it is indexing what it downloaded |

A parameter is `{ "type": "choice", "choices": […] }` or `{ "type": "text",
"max": 500 }` (1 to `max` characters), required unless it says
`"required": false`.

Built-in kinds:

| Kind | Params | Runs | Group |
|---|---|---|---|
| `tool-version` | `tool`: `instaloader`, `gallery-dl`, `yt-dlp` or `ffmpeg` | `<tool> --version` (`ffmpeg -version`); `result` `{ "version" }` (the first line) | `tool-version` |
| `instaloader-sync` | `source`: a source id | instaloader for that source, see [Sources](#how-a-sync-runs); `result` `{ "added", "updated", "error", "line" }` | `instaloader` |
| `gallery-dl-sync` | `source`: a source id | gallery-dl for that source, see [gallery-dl and yt-dlp syncs](#gallery-dl-and-yt-dlp-syncs); `result` as above | `gallery-dl` |
| `tool-test` | `tool`: `instaloader`, `gallery-dl` or `yt-dlp` | the tool once on a fixed public item, see [Downloaders](#downloaders); `result` `{ "ok", "error", "line" }` | the tool's name |
| `tool-update` | `tool`: `instaloader`, `gallery-dl` or `yt-dlp` | pip or pipx, picked from how the tool is installed, see [Downloaders](#downloaders) | the tool's name |
| `yt-dlp-sync` | `source`: a source id | yt-dlp for that source, see [gallery-dl and yt-dlp syncs](#gallery-dl-and-yt-dlp-syncs); `result` as above | `yt-dlp` |

More download kinds come with the userscript (#10).

### Sync all

`sync_all` in `GET /api/jobs` is the last `POST /api/sources/sync-all`
that queued anything since FeedVault started, or `null`:

```json
{ "id": 51, "started_at": 1727500000, "total": 6, "ended": 2, "failed": 0,
  "added": 31, "profiles": 2, "first": { "label": "Sync @somebody", "source": 4 },
  "current": job, "active": [53, 54, 55, 56], "done": false }
```

- `id`: its first job's id; with `started_at`, what tells two batches apart.
- `ended`: its jobs that are no longer queued or running, whatever their
  state; `failed` those that failed; `added` the new posts they indexed;
  `profiles` how many of them added at least one, and `first` (label and
  source id) the one that added the most, or `null`.
- `current`: the job running, else the next queued one, else `null`;
  `active`: the ids of its jobs still queued or running; `done`: none is.

It is kept in memory only and built from live jobs: a restart (which ends
every queued job, see `interrupted`) forgets it, so the dashboard never
shows syncs that a restored or rebuilt database no longer has. A sync job
whose source no longer exists when it starts (removed, or the database was
replaced) ends `cancelled` with the message `source <id> no longer exists
(removed, or the database was replaced): nothing to sync`, also in its log.

### Log

```json
{ "state": "running", "first": 1, "next": 42, "more": false,
  "lines": [{ "n": 41, "text": "[instagram] Downloading …" }, { "n": 42, "text": "…" }] }
```

Standard output and error, merged, decoded as UTF-8 (bad bytes replaced).
Lines are numbered from 1 and end at `\n` or at a lone `\r`; a line longer
than 4 KB is cut and ends with ` …`. Progress bars redraw with `\r`, often
without a `\n` for minutes: such a redraw is kept at most once a second, so
the live log moves without filling up; a line ended by `\n` is always kept.
Lines FeedVault adds itself start with `[feedvault]`.

- While the job is queued or running: the last 5000 lines. Poll with
  `after` set to the previous `next`. At most 1000 lines per answer; `more`
  says there are others after them.
- Once it has ended: the last 200 lines, kept with the job.
- `first` is the oldest line still kept. A reader whose `after` is below
  `first - 1` missed lines.

Cancelling sends SIGTERM to the job's process group (the tool and anything
it started), then SIGKILL after 10 seconds. Partial files stay, for the tool
to resume. Quitting FeedVault (Quit, Ctrl+C, SIGTERM) stops running jobs the
same way.

If FeedVault itself is killed (SIGKILL, a crash, a power cut), a job's
process may still be running when it starts again. Each running job records
its process id, the process's start time (field 22 of `/proc/<pid>/stat`)
and its executable; on the next start, a job left `running` has its process
group stopped (SIGTERM, then SIGKILL after 10 seconds if it is still there)
only if that pid still exists with the same start time and executable. A pid reused by another program is never
signalled. The job is then `interrupted`.

Jobs are kept in the database (`jobs` table), not with the user data: they
are not exported to `userdata/`.

### Tools

`instaloader`, `gallery-dl`, `yt-dlp` and `ffmpeg` are found on `PATH`, or
at the path set for them in Settings (`tools` in `config.json`, for a tool
installed in a virtualenv). `POST /api/config` with `{ "tools": { "yt-dlp":
"/abs/path" } }` sets one (an empty string clears it, back to `PATH`); the
other tools are left as they are. A path must be absolute, an executable
file, and named after the tool (`yt-dlp`, `yt-dlp_linux`); anything else is
refused. A set path that stops working makes jobs fail with "not found"
rather than fall back to `PATH`.

### Downloaders

What FeedVault knows of each tool, for the Downloaders card in Settings.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/downloaders` | `{ "checked_at": 1727500000, "check_updates": false, "tools": [tool, …] }`, one per tool, in the order `instaloader`, `gallery-dl`, `yt-dlp`, `ffmpeg` |
| POST | `/api/downloaders/check` | the same with `"ok": true`, every tool found again (Check again) |

A tool:

```json
{
  "tool": "yt-dlp",
  "found": true,
  "path": "/home/me/.local/bin/yt-dlp",
  "real_path": "/home/me/.local/share/pipx/venvs/yt-dlp/bin/yt-dlp",
  "configured": null,
  "install": "pipx",
  "venv": "/home/me/.local/share/pipx/venvs/yt-dlp",
  "version": "2026.08.06",
  "version_error": null,
  "latest": { "version": "2026.09.20", "error": null, "checked_at": 1727500000 },
  "outdated": true,
  "login": { "mode": "cookies", "browser": "firefox" },
  "update": { "possible": true, "command": "pipx upgrade yt-dlp", "reason": null },
  "install_hint": null
}
```

- `path`: the executable a job would run (the path set in Settings, else the
  first on `PATH`); `null` when there is none, or the path set no longer
  works. `real_path`: where it really is when `path` is a symlink, else `null`.
- `configured`: the path set in Settings, or `null`.
- `install`, read from `real_path`: `venv` (in the `bin/` folder of a
  virtualenv: `pyvenv.cfg` beside that folder), `pipx` (the same, the
  virtualenv inside pipx's `venvs` folder: `$PIPX_HOME/venvs`, else
  `~/.local/share/pipx/venvs` or `~/.local/pipx/venvs`), `system` (anything
  else: `/usr/bin`, `pip install --user`, a standalone binary) or `missing`.
  `venv`: the virtualenv's folder for `venv` and `pipx`, else `null`.
- `version`: the first line `<path> --version` prints (`ffmpeg -version`, up
  to its copyright notice), run without a shell and stopped after 10
  seconds; else `null` and `version_error` says why.

- `latest`: PyPI's latest release, when `check_updates` is on (see below),
  else `null`; always `null` for ffmpeg. `version` is `null` and `error`
  says why when PyPI could not be asked or answered something unexpected.
- `outdated`: `true` when `latest.version` is newer than `version`
  (comparing their release numbers, `1.30.0` = `1.30`; a nightly yt-dlp
  `2026.08.06.232211` is not older than `2026.08.06`), `false` when not,
  `null` when either is unknown.

The tools are found once and kept in memory; they are found again when the
tools set in Settings (or `PATH`) change, and on `POST /api/downloaders/check`.

- `login`: the session a sync of the tool uses, from its settings (see
  [instaloader settings](#instaloader-settings) and [gallery-dl and yt-dlp
  settings](#gallery-dl-and-yt-dlp-settings)): `{ "mode": "none" }`,
  `{ "mode": "cookies", "browser": "firefox" }`, or for instaloader
  `{ "mode": "login", "user": "name", "session_file": true }`, where
  `session_file` says whether instaloader's session file for that user
  exists (`$XDG_CONFIG_HOME/instaloader/session-<user>`, else
  `~/.config/…`, or its legacy place in the temp folder; the user name
  lowercased, as instaloader does). FeedVault only checks that the file is
  there; it never opens it, nor any cookie. `null` for ffmpeg.

- `update`: whether the Update button can run (`possible`), the command it
  runs or, when it cannot, the command to run yourself (`command`, shown
  with a copy button), and why it cannot (`reason`, else `null`); see
  **Update** below.
- `install_hint`: for a missing tool, the command that installs it
  (`pipx install <name>`, `sudo apt install ffmpeg`), shown with a copy
  button and never run; else `null`.

**Latest versions.** Off by default: `POST /api/config` with
`{ "check_updates": true }` turns it on. This is the only request the
server itself makes to the network. For instaloader, gallery-dl and yt-dlp
it fetches `https://pypi.org/pypi/<name>/json` (fixed URLs, `<name>` one of
the three, never from a request), without following redirects, within 10
seconds and at most 8 MB, and keeps `info.version` if it looks like a
version (a digit, then at most 63 of `0-9A-Za-z.+!_-`). Each package is
asked at most once a day, a failed ask included; the answers are kept in
`<data_directory>/downloaders/pypi.json`, so a restart does not ask again.
They are asked when `GET /api/downloaders` finds them due; Check again
does not ask sooner.

**Test.** Job kind `tool-test`, params `{ "tool": "instaloader" |
"gallery-dl" | "yt-dlp" }` and nothing else, group: the tool's name (the
same as its syncs, so it never runs beside one), with the same pause as its
syncs: a test right after a sync waits it out (`waits_until`), and so does
a sync right after a test. It runs the tool once on a
fixed public item with the session flags its syncs use, in
`<data_directory>/downloaders/test`:

```
instaloader --no-posts --no-profile-pic --no-metadata-json --dirname-pattern <data_directory>/downloaders/test
            [--load-cookies <browser> | --login <user>] -- instagram
gallery-dl --simulate [--cookies-from-browser <browser>] -- https://x.com/jack/status/20
yt-dlp --simulate --no-playlist [--cookies-from-browser <browser>] -- https://www.youtube.com/watch?v=jNQXAC9IVRw
```

instaloader fetches the profile's metadata and nothing else; gallery-dl
and yt-dlp download nothing (`--simulate`). `state` `done`, `result`
`{ "ok": true, "error": null, "line": null }`, `message` `"Works"`; or
`failed`, `result` `{ "ok": false, "error", "line" }` with `error` read from
the output as for a sync (`login_required`, `rate_limited`, `private`,
`not_found`, `generic`) and `message` saying what it means.

**Update.** Job kind `tool-update`, params `{ "tool": "instaloader" |
"gallery-dl" | "yt-dlp" }` and nothing else, group: the tool's name, so an
update never runs during a sync of that tool, nor a sync during its update.
When it is queued, the tool is found again (not from the cache) and the
command picked from how it is installed:

| `install` | Runs |
|---|---|
| `venv` | `<venv>/bin/python -m pip install --no-input --disable-pip-version-check -U <name>` |
| `pipx` | `pipx upgrade <name>` (pipx found on `PATH`) |
| `system`, `missing` | nothing: 400, the error says the command to run instead |

`<name>` is the tool's PyPI name, fixed in code. Refused the same way: a
virtualenv without its `bin/python`, or without pip in its
`lib/python*/site-packages` (one made by uv; no command is offered then),
a pipx virtualenv named otherwise than the package (`pipx install
--suffix`: the command shown upgrades it by its own name), or a pipx
install with no `pipx` on `PATH`. ffmpeg is not a choice: it comes from the system's packages. Once
the job has ended, the tool is found again, so `GET /api/downloaders` shows
its new version.
