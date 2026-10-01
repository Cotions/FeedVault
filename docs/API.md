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
| GET | `/api/config` | `{ "media_roots": ["/abs/path"], "data_directory": "/abs", "version": "0.0.0-dev", "tools": { "yt-dlp": "/abs/yt-dlp" } }` |
| POST | `/api/config` | body `{ "media_roots": [...] }` and/or `{ "tools": { "yt-dlp": "/abs/path" } }`; `{ "ok": true, "config": {…} }` or `{ "ok": false, "error": "…" }`. See [Tools](#tools) |
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
- `author`: author id (from `/api/authors`); takes in the account's folder-name
  aliases (see [People](#people))
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
the `author` and `person` filters take the aliases' posts in.

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
  "message": "2024.08.06"
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
- `message`: one line for people: `"3 new posts"`, the version, the last
  line of output of a failed job (or `"exit code 2"`), `"<tool> not found;
  set its path in Settings"`, `"cancelled"`, `"FeedVault stopped while it ran"`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/jobs` | `{ "running": 1, "queued": 0, "jobs": [job, …] }`: queued and running jobs and the last 100 ended ones, newest first |
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

Download kinds come with profile sync (#4) and the userscript (#10).

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
