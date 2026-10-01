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
  "missing": false
}
```

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
  "source": { "tool": "instaloader", "version": "4.15.1", "meta_path": "/abs/path/2024-06-01_12-00-00_UTC.json" }
}
```

- `album`: highlight title (or other collection name), `null` otherwise
- `source.tool` is `"instaloader (filenames)"` when the post was rebuilt from
  file names alone (downloads made with `save_metadata=False`); `meta_path`
  is then the first media file

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/posts?q=&platform=&author=&kind=&sort=&offset=&limit=` | `{ "total": 123, "posts": [summary, …] }` |
| GET | `/api/posts/<platform>/<post_id>` | full post, or 404 `{ "ok": false, "error": "not found" }` |
| GET | `/api/posts/summary?q=&platform=&author=&kind=&review=` | `{ "posts": 12, "media": 30, "bytes": 1048576 }`, see below |
| GET | `/api/authors` | `[{ "platform", "id", "handle", "name", "count", "bytes" }]`, most posts first |
| GET | `/api/storage` | disk use by creator, kind and year, and the largest files, see [Storage](#storage) |
| GET | `/api/stats` | `{ "posts", "media", "authors", "bytes", "missing", "unmatched", "by_platform": { "instagram": 12 }, "by_kind": { "image": 5 } }`; `bytes` leaves out media marked missing |
| GET | `/api/unmatched` | `[{ "path", "size", "mtime", "reason" }]` |
| GET | `/api/scan` | scan status, see below |
| POST | `/api/scan` | starts a rescan in the background; `{ "ok": true }`, or `{ "ok": false, "error": "already running" }` |
| GET | `/api/config` | `{ "media_roots": ["/abs/path"], "data_directory": "/abs", "version": "0.0.0-dev" }` |
| POST | `/api/config` | body `{ "media_roots": [...] }`; `{ "ok": true, "config": {…} }` or `{ "ok": false, "error": "…" }` |
| GET | `/api/browse` | native folder picker (zenity): `{ "path": "/abs" }` or `{ "path": null }` if cancelled |
| POST | `/api/saved` | body `{ "ids": ["instagram:C8x…"] }` → `{ "saved": ["instagram:C8x…"] }` (used by the userscript) |
| POST | `/api/quit` | stops the backend |
| GET | `/media/<id>` | the media file bytes (Range supported, for video) |
| GET | `/media/<id>/poster` | poster image for a video, 404 if none |
| GET | `/media/<id>/thumb` | small JPEG, cached in the data directory; falls back to the original for images, 404 for a video with no frame |

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
| GET | `/api/trash/items?offset=&limit=&platform=&author=&since=&before=` | what is in the trash, one entry per deletion, see [Trash contents](#trash-contents) |
| POST | `/api/trash/purge` | body `{ "keys": ["…"] }` or `{ "filter": { "platform": …, "author": …, "since": …, "before": … } }` → permanently deletes those entries' files, see [Trash contents](#trash-contents) |
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
      "items": 1, "of": 5, "partial": true, "missing": false, "thumb_url": "/trash/9b1f0c7d2e4a6b8c0d1e/thumb" }
  ]
}
```

- Query parameters, all optional: `platform`, `author` (an author id, or a
  handle for authors without one; ids are only unique within a platform, so
  send `platform` with it), `since` (Unix seconds: deleted at or after),
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
as `/api/trash/items` match (`platform`, `author`, `since`, `before`; each optional, but
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
  FTS5; plain words, prefix match on the last word)
- `platform`: e.g. `instagram`
- `author`: author id (from `/api/authors`)
- `kind`: one of the kinds above
- `sort`: `posted` (default) or `saved`
- `order`: `desc` (default, newest first) or `asc`
- `review`: `unreviewed` or `kept`
- `offset` (default 0), `limit` (default 60, max 200)

`/api/posts/summary` takes the same filter parameters as `/api/posts` (`q`,
`platform`, `author`, `kind`, `review`; `sort`, `order`, `offset` and `limit`
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

## Storage

`GET /api/storage`:

```json
{
  "totals": { "posts": 43116, "media": 85985, "bytes": 87606399138 },
  "by_author": [
    { "platform": "instagram", "id": "123456", "handle": "somebody", "name": "Some Body",
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
- `by_author`: one row per author id (posts without an author are only in the
  totals), biggest first. Handle and name are the newest post's, as in
  `/api/authors`. `kept_bytes` is the share in posts marked kept,
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

### Sizes

Every size is the `size` the scanner recorded for a media file (the photo or
video itself), and media items marked missing are skipped. Poster images,
thumbnails, metadata JSON, caption and other side files are not counted, so
deleting a post frees a little more than its `bytes`, and the trash total is
measured on disk instead.

`/api/storage`, `/api/authors` and `/api/posts/summary` are computed in SQL
from the index, never by walking the media folders (only the trash total is
read from disk). Their answers are cached until anything in the index changes
(a scan, a delete, a restore, a review decision).
