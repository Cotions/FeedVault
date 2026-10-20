# FeedVault API

Backend listens on `127.0.0.1:3380` (the port is `FEEDVAULT_PORT`'s when
set). The built dashboard is served by the backend itself (same origin). The
Vite dev server (`./run.sh --dev`) proxies `/api`, `/media`, trash
thumbnails and `/userscript` to the backend's port on `127.0.0.1`, so dev
mode also runs same-origin. `run.sh` exports the port it starts the backend
on, and Vite reads `FEEDVAULT_PORT` (3380 when unset); a value that is not a
port from 1 to 65535 (empty too) stops `run.sh`, Vite and the backend
rather than falling back to 3380.

Times are Unix seconds (UTC). Absent values are `null`, never missing keys.

Every route is a table row that starts with its method and its path in
backticks: `` | GET | `/api/posts?…` | … | ``. `backend/tests/test_api_doc.py`
reads those rows and fails when a route of the app is missing from this file,
or a row names a route the app does not have. Keep that format for new
routes.

Bodies and ids, for every route (`backend/tests/test_bad_requests.py` checks
each route of the app):

- A request body is JSON. A JSON body that is not an object (a list, a
  string, a number, `true`) is a 400 `{ "ok": false, "error": "the body must
  be a JSON object" }`, or the route's own 400 for its shape, before an id
  in its path is looked up (the security rules below still come first).
  No body, or one that is not JSON, reads as `{}`: a route whose
  fields are all optional takes it, any other answers 400 for what is
  missing.
- An id in a path (`<id>`) is a whole number up to 2^63 − 1, SQLite's
  largest; a larger one is a 404, whatever the method.
- An id list in a body (`media`, `ids`) takes whole numbers from 0 below
  2^53; anything else in it is a 400.

## Security rules

The rules every request is under (`backend/app.py`, `_origin_guard`,
`_no_frames`, `_foreign_origin`). They keep other websites open in the same
browser from reading or changing the library; they are not a login.

- **Bound to this machine.** The server listens on `127.0.0.1` only. Do not
  expose the port: whoever reaches it can do anything the dashboard can,
  including running the user's [scripts](#scripts).
- **Host.** Every request (pages, media and the userscript included) must
  carry a `Host` of `localhost`, `127.0.0.1` or `[::1]`, any port, or it gets
  403 `{ "ok": false, "error": "forbidden host" }`. This blocks DNS
  rebinding.
- **`X-FeedVault` header.** Every `/api/*` request, whatever its method (GET
  included; OPTIONS aside), must carry a non-empty `X-FeedVault` header
  (the dashboard sends `X-FeedVault: 1`), or it gets 403 `{ "ok": false,
  "error": "missing X-FeedVault header" }`. A page on another origin cannot
  add a custom header without a CORS preflight, and **no CORS header is ever
  sent**. The userscript sends it through `GM_xmlhttpRequest`.
- **Exempt from the header**, because they cannot send one: `/media/*` and
  `/trash/<key>/thumb` (loaded by `<img>` and `<video>`),
  `/userscript/feedvault.user.js` (installed by the userscript manager) and
  the dashboard's own pages and files (`/`, `/<path>`). They are still under
  the Host rule.
- **Another site.** A request comes from another site's page when it has an
  `Origin` that is not `http://` on `localhost`, `127.0.0.1` or `[::1]` (any
  port: the Vite dev server is on another one), or a `Sec-Fetch-Site` other
  than `same-origin` or `none`. The userscript's requests from instagram.com,
  x.com and tiktok.com are; the dashboard's are not, nor a request with
  neither header.
- **Only the userscript's routes from another site (default-deny).** The
  `X-FeedVault` header does not say who sends it: any userscript can, from
  any site. So a `/api/*` request from another site reaches only what the
  userscript calls, matched by route and method (`FOREIGN_ALLOWED` in
  `backend/app.py`):
  - `POST /api/saved`
  - `POST /api/save`
  - `GET /api/jobs/<id>`
  - `GET /api/sources/resolve` (without `defaults`, `session` and
    `metadata`)
  - `POST /api/sources` (403 when it sets an `options.script`, or any
    `options.session` but `{ "mode": "none" }`; the source gets no script
    and no login as its own, see [Download defaults](#download-defaults))
  - `GET /api/sources/<id>` (without `defaults`, `session`, `metadata` and
    `script_warning`, and with `options.script` and `options.session`
    `null`; `"dashboard_only": true` when its syncs only start from the
    dashboard: it runs a script or logs in)
  - `POST /api/sources/<id>/sync` (403 when the source runs a script, or
    its sync would use a login: cookies or a saved session)
  - `GET /api/links/people` (ids and names only, see [Save links from the
    browser](#save-links-from-the-browser))
  - `POST /api/links/import` (a link-in-bio page's links, insert-only,
    under the `bio_import` switch)
  - `POST /api/links` (403 with anything but `url` and `title`: from
    another site a link is saved to no one, the "Add this page" menu
    command)

  Every other `/api/*` request from another site, whatever its method
  (`HEAD` and `OPTIONS` included), a path no route has, and any route added
  later, answers 403 `{ "ok": false, "error": "this can only be called from
  FeedVault's own dashboard" }` before the route runs, after the Host and
  header rules. Settings, scripts, deleting, the trash, people, tags,
  collections, the other job and source routes and Quit are the
  dashboard's alone. `backend/tests/test_foreign_origin.py` checks the list
  against the userscript's calls. See [Who can run one](#scripts).
- **The page's profile.** The dashboard sends the id of the
  [profile](#profiles) it was loaded in as `X-FeedVault-Profile`. A request
  that is not a `GET` and names another profile than the active one is
  answered 409 `{ "ok": false, "error": "another profile is active now;
  reload the page", "profile_switched": true }` before the route runs: a
  tab left open across a switch never writes into the other library. See
  [Profiles](#profiles).
- **Media from other sites.** `/media/*` and `/trash/<key>/thumb` answer 403
  before doing any work when the request comes from another site. The
  dashboard's pages and `/userscript/feedvault.user.js` still open from a
  link on any site (the userscript links to them).
- **No framing.** Every response carries `X-Frame-Options: DENY` and a
  `Content-Security-Policy` with `frame-ancestors 'none'`, so no page can put
  the dashboard under its own and steer clicks into it.
- **Nothing from other sites.** The dashboard loads all it needs from
  FeedVault itself: its two fonts (Bricolage Grotesque, JetBrains Mono,
  both SIL OFL, licences in `dist/assets/fonts-*.LICENSE.txt`) are woff2
  files the build puts in `frontend/dist/assets`. Responses with no policy
  of their own add `font-src 'self'` to that `Content-Security-Policy`. The
  build fails if `dist/index.html` or a built style sheet points at another
  origin (`frontend/vite.config.js`), and the browser tests abort and fail
  any request to one (`frontend/e2e/fixtures.js`).
- **Media is never a page.** A media file is served only from a path the
  scanner recorded (the URL carries a row id, never a path), only when what
  it opens as is inside a media root and outside its trash (see the end of
  [Endpoints](#endpoints)), with `X-Content-Type-Options: nosniff`,
  `Content-Security-Policy: sandbox; default-src 'none'` and
  `Cross-Origin-Resource-Policy: same-origin`. Its type comes from its own
  file name, from a fixed list of image, video and audio types (a poster
  named `.image`, as yt-dlp keeps TikTok's, is typed by its first bytes, as
  JPEG, PNG, WebP or GIF only); anything else
  is sent as an `application/octet-stream` download, so an HTML file in a
  media folder never runs on this origin.
- **No commands from the API.** Jobs are started by kind, with parameters
  each kind checks, and every program runs without a shell. The only
  user-written commands are the files in the scripts folder, which no route
  writes.

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
  "cover": { "kind": "image", "url": "/media/17/thumb" },
  "missing": false,
  "decision": null,
  "tags": ["outfits", "summer"]
}
```

- `platform`: `instagram`, `twitter` (X; `url` points at x.com), `tiktok`; posts
  from other gallery-dl sites carry the gallery-dl category (`reddit`, …), and
  yt-dlp posts their extractor's name in lower case (`youtube`, `tiktok`, …)
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
- `source.tool`: `"instaloader"`, `"gallery-dl"` or `"yt-dlp"` (`version` is
  `null` for gallery-dl, which does not record it); `meta_path` of a gallery-dl
  post is the JSON of its first media file, or the post-level JSON of a
  text-only tweet; of a yt-dlp post, its `.info.json`
- `source.tool` is `"instaloader (filenames)"` when the post was rebuilt from
  file names alone (downloads made with `save_metadata=False`); `meta_path`
  is then the first media file

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/posts?q=&platform=&author=&person=&collection=&kind=&review=&tag=&untagged=&new=&notification=&sort=&order=&offset=&limit=` | `{ "total": 123, "posts": [summary, …] }` |
| GET | `/api/posts/<platform>/<post_id>` | full post, or 404 `{ "ok": false, "error": "not found" }` |
| GET | `/api/posts/summary?q=&platform=&author=&person=&collection=&kind=&review=&tag=&untagged=&new=&notification=` | `{ "posts": 12, "media": 30, "bytes": 1048576 }`, see below |
| GET | `/api/new` | posts new since the last "Mark all seen", see [New posts](#new-posts) |
| POST | `/api/new/seen` | body `{ "at": 1727500000 }` or nothing (now) → `{ "ok": true, "since": 1727500000 }`; with `"person": 3` or `"account": { "platform", "id" }`, theirs only; see [New posts](#new-posts) |
| POST | `/api/new/mute` | body `{ "person": 3, "muted": true }` or `{ "account": { "platform", "id" }, "muted": false }` → `{ "ok": true, "muted": { "people": [3], "accounts": [] } }`; see [New posts](#new-posts) |
| GET | `/api/notifications` | syncs that brought new posts or failed, see [Notifications](#notifications) |
| POST | `/api/notifications/read` | body `{ "upto": 41 }` or nothing (all) → `{ "ok": true, "read": 3 }`, see [Notifications](#notifications) |
| GET | `/api/authors` | `[account, …]`, most posts first, see [People](#people) |
| GET | `/api/storage?person=` | disk use by creator, kind and year, and the largest files, see [Storage](#storage) |
| GET | `/api/stats` | `{ "posts", "media", "authors", "bytes", "missing", "unmatched", "by_platform": { "instagram": 12 }, "by_kind": { "image": 5 } }`; `bytes` leaves out media marked missing. `?person=<id>`: counts over that person's posts only, as `/api/posts?person=` (`unmatched` stays the whole archive's: those files belong to nobody) |
| GET | `/api/unmatched` | `[{ "path", "size", "mtime", "reason", "dismissed" }]` (`dismissed`: an extra copy whose group is marked not a duplicate, see [Duplicates](#duplicates)) |
| GET | `/api/scan` | scan status, see below |
| POST | `/api/scan` | starts a rescan in the background; `{ "ok": true }`, or `{ "ok": false, "error": "already running" }` |
| GET | `/api/config` | `{ "media_roots": ["/abs/path"], "data_directory": "/abs", "profile": { "id": "default", "name": "Default" }, "version": "0.0.0-dev", "tools": { "yt-dlp": "/abs/yt-dlp" }, "instaloader": { "session": { "mode": "none" }, "pause": 60, "metadata": "full" }, "gallery-dl": { "session": { "mode": "none" }, "pause": 30, "ignore_config": false, "metadata": "full" }, "yt-dlp": { "session": { "mode": "none" }, "pause": 30, "ignore_config": false, "metadata": "full" }, "youtube_max_seconds": 180, "routes": {…}, "check_updates": false, "schedules_paused": false, "desktop_notifications": false, "bio_import": false, "source_defaults": [block, …] }` (see [Download defaults](#download-defaults)) |
| POST | `/api/config` | body `{ "media_roots": [...] }` and/or `{ "tools": { "yt-dlp": "/abs/path" } }` and/or `{ "instaloader": {…} }`, `{ "gallery-dl": {…} }`, `{ "yt-dlp": {…} }`, `{ "youtube_max_seconds": 180 }`, `{ "routes": {…} }`, `{ "check_updates": true }`, `{ "schedules_paused": true }` (see [Schedules](#schedules)), `{ "desktop_notifications": true }` (see [Notifications](#notifications)), `{ "bio_import": true }` (see [Link-in-bio import](#link-in-bio-import)), `{ "source_defaults": { "gallery-dl": { "twitter": {…} } } }` (see [Download defaults](#download-defaults)); each tool's `metadata` (see [Metadata modes](#metadata-modes)); `{ "ok": true, "config": {…} }` or `{ "ok": false, "error": "…" }`; 403 from another site, as every route the userscript does not call (see [Security rules](#security-rules)). See [Tools](#tools), [Downloaders](#downloaders), [instaloader settings](#instaloader-settings), [gallery-dl and yt-dlp settings](#gallery-dl-and-yt-dlp-settings) and [Link routing](#link-routing) |
| GET | `/api/profiles` | `{ "active": "default", "profiles": [profile, …] }`, see [Profiles](#profiles) |
| POST | `/api/profiles` | body `{ "name", "data_directory", "media_roots": [...] }` → `{ "ok": true, "profile": profile, "active", "profiles" }`: a new profile, not switched to; see [Profiles](#profiles) |
| POST | `/api/profiles/<id>` | body `{ "name" }` → `{ "ok": true, "active", "profiles" }`: renames it |
| DELETE | `/api/profiles/<id>` | forgets it (its files stay on disk) → `{ "ok": true, "active", "profiles" }` |
| POST | `/api/profiles/<id>/activate` | switches to it → `{ "ok": true, "active", "profiles" }`, or 409 `{ "ok": false, "error", "busy": true }` while a job or a scan runs; see [Profiles](#profiles) |
| POST | `/api/yt-dlp/info-json-cookies` | body `{ "apply": false }` (default: only counts) or `{ "apply": true }`; see [Cookies in info JSONs](#cookies-in-info-jsons) |
| GET | `/api/browse` | native folder picker (zenity): `{ "path": "/abs" }` or `{ "path": null }` if cancelled; `{ "ok": false, "error": "zenity is not installed", "path": null }` without zenity |
| POST | `/api/saved` | body `{ "ids": ["instagram:C8x…"] }` (the first 500 are looked up) → `{ "saved": ["instagram:C8x…"] }` (used by the userscript) |
| POST | `/api/save` | body `{ "platform": "instagram", "shortcode": "…" }` or `{ "url": "<an X or TikTok post's link>" }`: download one post (used by the userscript), see [Save from the browser](#save-from-the-browser-userscript) |
| POST | `/api/quit` | stops the backend |
| GET | `/media/<id>` | the media file bytes (Range supported, for video) |
| GET | `/media/<id>/poster` | poster image for a video, 404 if none |
| GET | `/media/<id>/thumb` | small JPEG, cached in the data directory; falls back to the original for images, 404 for a video with no frame |
| GET | `/media/copy/<copy_id>/thumb` | the same for the first item of an extra copy (see [Duplicates](#duplicates)) |
| GET | `/media/avatars/<file>` | a person's picture: `<avatar>.webp` (the 600 x 900 portrait), `<avatar>-300.webp` (300 x 450, the cards') or `<avatar>-96.webp` (a 96 px square), cached for good (`immutable`: a new picture has a new name); see [Profile pictures](#profile-pictures) |
| GET | `/media/people/<id>/profile-pic/<key>` | one of the profile pictures instaloader saved for a person's accounts, as listed by `/api/people/<id>/avatar/choices` |
| GET | `/userscript/feedvault.user.js` | the userscript (no header needed), with this instance's port in `API_BASE`, `@updateURL` and `@downloadURL` (the port the server listens on, never one from the request), see [Save from the browser](#save-from-the-browser-userscript) |
| GET | `/` | the dashboard (`frontend/dist/index.html`; no header needed). Without a built dashboard, a plain-text note to run `./run.sh --build` |
| GET | `/<path>` | a file of the built dashboard, else `index.html` (the dashboard's own routes, such as `/review`); `api/…`, `media/…` and `userscript/…` paths are a 404 here |

A media file (its poster, what a thumbnail is made from) is served only when
what it opens as, symlinks followed, is inside a media root and outside its
`.feedvault-trash`: else 404. It is opened once and that is what is checked
and sent, so a file swapped for a symlink meanwhile is not served. A scan
never indexes a symlink that leads out of every media root or into a trash;
it is listed as unmatched ("a symlink that leads out of the media roots (or
into a trash): not indexed"). A symlink that stays inside a media root (its
own or another) is indexed and served as any file.

## Profiles

A profile is a whole separate library: its own data directory (its
`feedvault.db`, thumbnails, `userdata/*.json`, the downloaders' archives and
stamps, so its own posts, tags, people, collections, links, sources and
jobs) and its own media roots (each with its own `.feedvault-trash`).
Exactly one is active, and every other route of this file reads and writes
the active one. The rest of the config is shared by all profiles: tool
paths, the instaloader, gallery-dl and yt-dlp settings, link routing,
`check_updates`, `desktop_notifications`, `bio_import`, the download defaults
(`source_defaults`) and `schedules_paused` (one switch for all: only the active profile's schedules
run anyway). So is the scripts folder.

`config.json` keeps them as `"profiles": [{ "id", "name", "data_directory",
"media_roots" }]` and `"active_profile": "<id>"`. A config from before
profiles (`data_directory` and `media_roots` at the top level) is read as
one profile, `{ "id": "default", "name": "Default" }`, on the same data
directory and roots: nothing is moved, and the file is only rewritten in
the new shape the next time something is saved (Settings, or a route
below). With one profile the dashboard looks as it did before (the header's
switcher is not shown).

`profile`:

```json
{
  "id": "3f9a1c2e", "name": "Work", "active": false,
  "data_directory": "/home/me/.local/share/feedvault-work",
  "media_roots": ["/mnt/archive/work"],
  "posts": 1204, "people": 37
}
```

`posts` and `people` are counted in that profile's database, opened
read-only for the ones that are not active: 0 when it has no database yet,
`null` when it cannot be read (an unmounted disk).

- **Create** (`POST /api/profiles`). `name`: 1 to 60 characters, not
  another profile's (case aside). `data_directory`: a folder's path, made
  (0700) when it is not there; never the data directory of another
  profile, however it is written (symlinks followed). One that already
  holds a library (a removed profile's) opens as it was left.
  `media_roots`: as `POST /api/config`'s, and it may be empty. A media root
  may not be, hold or sit inside a media root of another profile (the same
  files would be indexed into two libraries, and a delete in one would
  empty the other's); `POST /api/config` refuses that for the active
  profile's roots too. A refusal is a 400 `{ "ok": false, "error" }` and
  nothing is made. The new profile is not switched to, and gets no
  database until it is.
- **Rename** (`POST /api/profiles/<id>`): the name only; 404 for an unknown
  id, 400 for a bad or taken name.
- **Remove** (`DELETE /api/profiles/<id>`): the profile is forgotten, and
  **nothing on disk is touched**: its data directory and its media stay as
  they are, and a new profile on that data directory has it all back. 400
  for the active profile ("switch to another profile before removing this
  one") and for the last one; 404 for an unknown id.
- **Switch** (`POST /api/profiles/<id>/activate`). The backend points
  everything at the other library, as a start does: its database is opened
  (made or migrated), its user data read back into empty tables, the jobs
  a stopped FeedVault left in it are marked interrupted, a scan of its
  media roots starts. Before that, the user data changes not yet written
  are written into the library being left. The hashing pass is stopped
  (what it had read is kept) and starts again in the new library. Notes of
  skipped schedules and the last "Sync all" are forgotten.

  Refused with 409 `{ "ok": false, "error": "…", "busy": true }`, and
  nothing changes, while a job is queued or running ("2 jobs are queued or
  running; wait for them to end, or cancel them on the Jobs page") or a
  scan runs ("a scan is running; try again when it is done"): what a job
  downloads and what a scan reads belong to the library they started in.
  Requests being answered are waited for (up to 10 s, then the same 409),
  and those that arrive during the switch wait for it. 404 for an unknown
  id; the active one is a 200 that changes nothing. A library that cannot
  be opened (a database from a newer FeedVault, a disk that is not
  mounted) is a 500 `{ "ok": false, "error": "cannot open that profile: …"
  }`, and the profile that was active stays active.
- **A tab left open across a switch.** Every id on its pages is the other
  library's. The dashboard sends the profile it was loaded in as
  `X-FeedVault-Profile: <id>` (the first `GET /api/profiles` or `GET
  /api/config` tells it); a request that is not a `GET`, carries that
  header and names another profile than the active one gets 409 `{ "ok":
  false, "error": "another profile is active now; reload the page",
  "profile_switched": true }`, and the dashboard reloads at `/`.
  `POST /api/profiles/<id>/activate` and `POST /api/quit` are not checked.
  A caller that sends no such header is not checked either.
- **Background work is the active profile's only.** Scheduled syncs run
  for the active profile's sources; the others' wait until theirs is
  active again (a schedule long overdue then runs once, as after a start).
  The [userscript](#save-from-the-browser-userscript) sends no profile:
  what it saves, and the sources it adds, go to the active profile.

All five routes are the dashboard's alone (403 from another site).

## New posts

A post is **new** when the index first had it after the user last marked
everything seen. Each post keeps when that was (`first_seen`, set when it is
first indexed and never changed by a rescan); the user has one high-water
mark, `seen_at`. `posted_at` plays no part: an old post downloaded today is
new.

- What a download adds (a sync job's rescan) and what a full scan finds that
  the index did not have are new. A scan that builds the index from nothing
  (first run, deleted or replaced database), the first scan of a media root
  with nothing indexed under it yet (a root just added: its part of the
  index is built from nothing), files back from the trash and files moved
  by Duplicates are not: they were there before. Files put by hand into a
  root that never had a post count as that root's first scan too. Such a
  build is noted in the database (`meta` key `building:<root>`) until it
  ends, so when FeedVault is stopped or killed halfway, the next scan goes
  on building and what it finds is not new either.
- `seen_at` is user data: table `seen_at`, one row, written to
  `<data_directory>/userdata/seen_at.json` (`{"version": 1, "rows": [{"id":
  1, "at": 1727500000}]}`) like the others and read back into a database
  that has none. A database upgraded to this version starts with it at the
  time of the upgrade, and a new one with nothing to restore at its first
  start, so an existing archive is never all new.
- Strictly after: a post indexed in the same second as the mark is not new.
- **Per person.** "Mark seen" on a person, or on an account linked to
  nobody (it counts as its own), gives each of its accounts a mark of its
  own (folder-name aliases included): a post is new when it came after the
  global mark *and* after its account's mark. These marks are user data
  too: table `seen_marks`, keyed by platform and account id, written to
  `userdata/seen_marks.json` (`{"rows": [{"platform": "instagram",
  "author_id": "123456", "at": 1727503600}]}`), so a rebuilt index keeps
  them. An account linked to a person later keeps the mark it had until
  the person's next "Mark seen". A global "Mark all seen" drops the marks it
  has passed (they no longer change anything).
- **Mute.** A muted person, or account linked to nobody, makes no
  [notification](#notifications) and no toast, and its new posts are left
  out of the global count (`count` here, `new` in `GET /api/jobs`) and of
  `new=1` / `is:new` without an `author` or `person` filter: its own page
  (`?person=`, `?author=`) still shows them, and "Sync all" leaves its
  syncs out of what it added and what failed. A global "Mark all seen" did
  not show them, so it does not cover them: a muted account keeps a mark of
  its own at the global mark it passes, which stands instead of the global
  one until its own "Mark seen". Unmuted, what is still unseen counts again.
  User data: `muted_people` (exported by person name, as
  `person_accounts`; deleting the person drops it) and `muted_accounts`
  (platform and account id, its folder-name aliases follow), in
  `userdata/muted_people.json` and `userdata/muted_accounts.json`.

`GET /api/new`:

```json
{ "count": 12, "since": 1727500000,
  "by_person": [{ "id": 3, "name": "Some Body", "count": 9, "until": 1727503600, "muted": false }],
  "by_account": [{ "platform": "instagram", "id": "123456", "handle": "somebody", "person": 3, "count": 9, "until": 1727503600, "muted": false },
                 { "platform": "tiktok", "id": "6900000000000000777", "handle": "demo.clips", "person": null, "count": 3, "until": 1727502000, "muted": false }],
  "muted": { "people": [], "accounts": [] } }
```

- `count`: every new post, those without an author included and muted
  ones left out; `since`: the mark (`null` only before the first start has
  set it).
- `by_account`: accounts as on the Creators page (folder-name aliases count
  for the id they stand for), `person` the id of the person linked, else
  `null`; `by_person`: the same added up per person. Most new posts first.
  `until`: the newest of those posts' `first_seen`, to send as `at` with a
  "Mark seen" of that person or account. `muted`: muted (the account on its
  own or through its person); such rows are listed, not counted in `count`.
- `muted`: who was muted, as muted: person ids, and accounts linked to
  nobody.

`POST /api/new/mute` mutes (`"muted": true`) or unmutes a person or an
account linked to nobody (muting an account linked to a person is a 400:
mute the person; unmuting one is always allowed, since it may have been
muted before it was linked). The body is `muted` and one of `person` or `account`, as for
`/api/new/seen`; anything else is a 400.

`POST /api/new/seen` marks everything seen: `at` (Unix seconds, optional,
default now) becomes the mark, unless the mark is already later: it never
moves backwards, nor past now. `{ "ok": true, "since": <the mark> }`.

With `"person": <id>` or `"account": { "platform": "instagram", "id":
"123456" }` (an indexed account, or a folder-name alias of one), only that
person's or account's posts are marked seen, up to `at` the same way:
`{ "ok": true, "since": <the global mark>, "at": <the at asked for, at most now> }`
(a mark of theirs that was already later stays). A body
that is not `{}`, empty, or made of `at` and one of `person` or `account`
is a 400, and so is a person or account that does not exist.
The dashboard's **Mark all seen** sends `new_until` from `GET /api/jobs` (the
newest new post's `first_seen` when it counted them), so a post indexed
since, which it has not shown, stays new. A full scan stamps `first_seen`
folder by folder, so a mark set while it runs leaves the folders it commits
afterwards new.

## Notifications

Each sync, started by hand, by "Sync all" or by the scheduler, that brought
new posts or failed leaves an entry: "12 new posts from Some Body", "@name:
account not found". A sync that brought nothing leaves none, and neither
does a cancelled or interrupted one. A **scheduled** sync that fails in the
same [health state](#account-health) as the source's sync before it leaves
none either: a source the scheduler retries while it stays rate limited is
one entry, not one per retry. Another state, a sync by hand, or a sync that
worked in between makes the next failure an entry again.

Entries are kept in the database (table `notifications`), the newest 200;
older ones are dropped as new ones come. They are not user data (not
exported): like the jobs list, they say what happened.

`GET /api/notifications`:

```json
{ "unread": 1, "latest": 42,
  "entries": [{ "id": 42, "at": 1727503600, "kind": "new", "text": "3 new posts from Some Body",
                "job_id": 118, "source_id": 5, "person_id": 3,
                "account": { "platform": "instagram", "id": "123456" },
                "state": "ok", "count": 3, "scheduled": true, "read": false },
              { "id": 41, "at": 1727500000, "kind": "failed", "text": "x.com/someone: rate limited",
                "job_id": 117, "source_id": 6, "person_id": null, "account": null,
                "state": "rate_limited", "count": 0, "scheduled": false, "read": true }] }
```

- `kind`: `new` or `failed`; `state`: the sync's health state (`ok`,
  `renamed`, `rate_limited`, `private`, `login_required`, `not_found`,
  `error`).
- `text` names the source by its person, else `@handle` (instaloader) or its
  link without `https://`; a failure the health states do not name gives the
  job's message. It is built from handles and tool output, so it is
  scrubbed (as the [sync log](#log): no cookie, token or session path, no
  control character, at most 200 characters) and the dashboard shows it as
  text, never as HTML.
- `GET /api/posts?notification=<id>` (and `/api/posts/summary`) gives the
  posts that entry's sync brought: those first indexed by it, under the
  source's folder, whether or not they were marked seen since. An unknown
  id, or a failure's, matches nothing.
- The entry's id is in its job's `result` as `notification` (the
  dashboard's toast links to it).

`POST /api/notifications/read` marks the entries up to `upto` (an id) read,
or all of them with an empty body; `read` says how many were unread. Any
other body is a 400.

`GET /api/jobs` carries `"notifications": { "unread": 1, "latest": 42,
"desktop": false }` for the sidebar's bell (`desktop`: the setting below).

**Desktop notifications** are off by default (`desktop_notifications` in
the config, Settings → Sync). On, a new entry pops up on the desktop:

- An open dashboard tab shows it with the browser's Notification API (text
  only; a click opens what the entry leads to) while it is hidden or not
  focused; a focused tab has its toast. The browser asks for permission
  only when the switch is turned on, never on load. Such a tab, and any
  visible tab (it has its toasts), polls `GET /api/jobs?desktop=1` (a
  hidden one once a minute), which tells the backend a tab tells of them.
- When no tab has said so for 150 seconds, the backend runs `notify-send`
  instead, if it is on the PATH (else nothing, quietly): the argument list
  is fixed (`notify-send --app-name=FeedVault -- FeedVault <text>`), no
  shell, the text one argument, scrubbed, at most 200 characters and its
  `&`, `<`, `>` escaped (notification servers may read markup), with 5
  seconds to finish.

## Deleting

Deleting never destroys a file directly. Files move to a trash folder inside
the media root they came from (`<root>/.feedvault-trash/`, same relative path),
which is an instant rename on the same disk. The scanner skips that folder.
Only **Empty trash** and **purge** (below) remove files for good.

| Method | Path | Returns |
|---|---|---|
| POST | `/api/delete` | body `{ "posts": ["instagram:C8x…"], "media": [17, 18] }` (either list may be omitted, not both; at most 5000 of each are taken) → see below |
| GET | `/api/trash` | `{ "files": 12, "bytes": 1048576, "roots": [{ "root": "/abs", "path": "/abs/.feedvault-trash", "files": 12, "bytes": 1048576 }] }`: the trash as `GET /api/trash/items` counts it (`trash` there), from the manifests, without looking at any trashed file (see [Trash contents](#trash-contents)) |
| POST | `/api/trash/empty` | permanently removes every trash folder → `{ "ok": true, "files": 12, "bytes": 1048576 }`: every file that was in them, counted on disk right before |
| GET | `/api/trash/items?offset=&limit=&platform=&author=&person=&since=&before=&upto=` | what is in the trash, one entry per deletion, see [Trash contents](#trash-contents) |
| POST | `/api/trash/check` | looks at every trashed file now → `{ "ok": true, "entries": 7, "files": 21, "bytes": 52428800, "missing": 1 }`, see [Trash contents](#trash-contents) |
| POST | `/api/trash/purge` | body `{ "keys": ["…"] }` or `{ "filter": { "platform": …, "author": …, "person": …, "since": …, "before": …, "upto": … } }` → permanently deletes those entries' files, see [Trash contents](#trash-contents) |
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
post newer than the stamp (trashed before any sync passed it, such as a
post saved on its own then deleted) is downloaded again by the next sync.
That sync puts it straight back in the trash once its folder is indexed
(a post in the trash as a whole, not in the index when the sync started,
in the source's folder after it, and still in the trash), says so in its
log (`1 trashed post came back with this sync …: back in the trash`) and
does not count it in `added`. A sync cancelled while instaloader ran has
its folder indexed when it ends, for the same check. The list of trashed
posts it checks against is kept in
`<data_directory>/instaloader/retrash/<source id>.json` while the sync
runs, so a sync that FeedVault stopped (Quit, or killed) gets the same
check at the next start: its folder is indexed and the posts it brought
back go back to the trash (the server log says so), then the file goes.
A file whose source is gone is dropped; one whose folder is missing
(its media root offline) waits for a later start.

Such a post keeps one entry in the trash: the one it was deleted with,
and the files it had then. The copy the sync downloaded is deleted for
good right after it went to the trash (its entry purged), only when the
first entry is the same post (same id, as many media items), in the same
trash folder, whole (every file still there, none shared with the copy)
and the copy added no archive entries; the log says so (`1 of them kept
its first trash entry …`). Otherwise both entries stay, and restoring the
post brings back the latest. Restore and purge work on the entry left as
on any other.

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
and posters, and `size` in bytes. A post that was kept also has
`"decision": "keep"` and `decided_at` (when it was kept) on its lines: the
decision leaves the index with the post, and a restore puts it back (unless
the post was decided on again since), so it comes back kept. Lines written before these fields existed
still work: the platform comes from the post id, the media kind from the file
extension, the size from the file on disk, and `author` is `null`.

`from` and `to` are absolute. A media root that moved with its trash
(renamed, or its disk mounted elsewhere, and the root set to the new place
in Settings) keeps working: a line whose `to` is in a `.feedvault-trash`
folder of another place than its root (nor the root's symlink target) is
read with that place replaced by the root, in `to` and, when it starts with
the same place, in `from`. A restore or purge writes the lines back that
way. Restore checks both paths as for any line.

`GET /api/trash/items` groups the lines by trash folder, post and batch, newest
deletion first:

```json
{
  "total": 3, "files": 9, "bytes": 15728640, "upto": 1727500000123,
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
  `before` (deleted strictly before), `upto` (a list's `upto`: nothing
  deleted after that list was made), `offset` (default 0), `limit`
  (default 60, max 500).
- `upto`: the newest deletion in the trash when the list was made (with
  `upto` given, the smaller of the two). It is a stamp, not a time to
  compare with a clock: each manifest line written has `at_ms`, the
  deletion time in milliseconds but always above every stamp FeedVault
  wrote or read before, so a deletion made after a list is never inside
  its `upto`, even within the same millisecond. Lines written before it
  count as their whole second (`at` × 1000). Send it with later pages and
  with a purge by filter, so both mean the list the user saw.
- `total`, `files` and `bytes` add up every entry the filters match, not just
  one page. `trash` and `authors` cover the whole trash, whatever the filters.
- `key` is opaque. It names one entry and is what restore and purge take.
- `files` and `bytes` (here and in the totals) count the entry's files still
  in the trash. Looking costs one `lstat` per file, so a list only looks at
  the entries of the page it returns (again once their last look is 30 s
  old); every other entry counts as its last look, or, never looked at, as
  its manifest lines recorded it (every file there, their recorded sizes).
  `POST /api/trash/check` looks at every file now (the Trash page's "Check
  for missing files"). The manifest is parsed once per version; one that
  only grew (a delete appends to it) is read from where the last read
  stopped. `items` counts its media items, `of` the post's media count when it
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

A file goes back where it was, never over another file (that one is
reported and stays in the trash). When its folder is gone (the account's
folder renamed since), it goes to the folder the account's other indexed
posts are in, if they are all in one folder of the same root, and the
response lists it in `moved` (`{"post", "from", "to"}`, folders relative to
the root); otherwise the old folder is made again and listed in `recreated`
(`{"post", "folder"}`). `decision_unknown` lists the posts back without a
decision whose manifest lines were written before deletions kept the
decision: they may have been kept. The Trash page's toast says all three.

A restore marks its manifest lines (`"restoring"`: where the file goes)
before any file moves, and drops them only once the files are back, indexed
and their Keep put back. A restore cut short (FeedVault killed) is finished
by the next restore, purge, empty or scan (one runs at startup): a marked
line whose file is at its destination and gone from the trash is done as
the restore would have (indexed, not new, its Keep back) and dropped; any
other marked line loses its mark.

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
`upto` is optional and does not count as one: alone it would be the whole
trash. The match is made under the same lock as the purge itself. The page
sends its list's `upto`, so nothing trashed after the user saw the totals is
purged with them, not even within the same second.

`entries` counts the entries fully purged and `keys` names them, `dropped`
the lines removed for files that were already missing. An entry with an error
keeps its lines.
Delete, restore and purge never run at the same time (they share the
manifest). A scan takes the same lock one folder at a time (and for its
end), so a delete or a restore during a long rescan waits for the folder
being read, not for the scan. The folders they change after the scan read
them are read again at its end, before it marks anything missing.

A delete moves each post's files, writes their lines, then drops the posts
from the index in one commit at the end. When FeedVault is stopped or killed
in between, the next scan finishes the job: a post whose metadata file is
not where the index has it, but in a trash folder on a line of that post
(not of an extra copy), is dropped from the index (with its decision, which
its lines keep) instead of being kept as missing.

## Review (keep or trash)

A post can be marked **kept**. Deciding to trash it is just `/api/delete`.
Decisions live in their own table, untouched by rescans, and are also written
to `<data_directory>/userdata/decisions.json` (2 s after the last change) so
they survive rebuilding the index. An older `<data_directory>/decisions.json`
is still read when the new file does not exist. Every `userdata/*.json` file,
like `config.json`, is written readable by you only (0600, a file that was
more open is tightened on its next write), through a temp file of a unique
name in the same folder, fsynced, renamed over it, the folder fsynced.

| Method | Path | Returns |
|---|---|---|
| POST | `/api/review` | body `{ "posts": ["instagram:C8x…"], "decision": "keep" }` or `"decision": null` to clear → `{ "ok": true, "posts": [...] }` |
| POST | `/api/trash/restore` | body `{ "posts": ["instagram:C8x…"] }` → moves those posts' files back out of the trash (their most recent deletion) and re-indexes them at once → `{ "ok": true, "posts": [...], "files": 4, "errors": [], "moved": [], "recreated": [], "decision_unknown": [] }` |

Every post summary carries `"decision": "keep"` or `"decision": null`.
`/api/posts` takes `review=unreviewed` (no decision yet) or `review=kept`, and
`order=asc` for oldest first (default `desc`). `/api/stats` adds
`"kept"` and `"unreviewed"` counts.

Undo in the review screen: after a trash, call `/api/trash/restore` with that
post id; after a keep, call `/api/review` with `decision: null`. A re-review
(`/review?again=1`) lists the scope's posts with no `review` filter, kept ones
too; a keep on a post that was already kept sends nothing, so its undo has
nothing to clear either.

`/api/posts` query parameters, all optional:

- `q`: full-text search over post text, author handle, author name and album (SQLite
  FTS5; plain words, prefix match on the last word). `tag:name` and
  `tag:"two words"` in it are tag filters, not words (see [Tags](#tags)), and
  mix freely with text: `tag:outfits red dress`. `is:new` in it is the same
  as `new=1`
- `tag`: a tag name, matched without regard to (ASCII) case. Repeat it for several:
  a post must have all of them (`tag=a&tag=b`). Combined with any `tag:` in `q`;
  a value that cannot be a tag name matches nothing
- `untagged=1`: only posts with no tag
- `new=1`: only posts new since the last "Mark all seen" (see
  [New posts](#new-posts))
- `platform`: `instagram`, `twitter` (X, x.com included), `tiktok`, `youtube`,
  or another gallery-dl category or yt-dlp extractor name for sites without
  their own mapping (`reddit`, `bluesky`, …)
- `author`: author id (from `/api/authors`) or one of its folder-name aliases;
  either way the whole account's posts (see [People](#people))
- `person`: a person id: posts of every account linked to that person, across
  platforms (see [People](#people)); a value that is not an id matches nothing
- `collection`: a collection id: only posts in that collection (see
  [Collections](#collections)), in the feed's order, not the collection's;
  a value that is not an id matches nothing
- `kind`: one of the kinds above
- `sort`: `posted` (default) or `saved`
- `order`: `desc` (default, newest first) or `asc`
- `review`: `unreviewed` or `kept`
- `offset` (default 0), `limit` (default 60, max 200)

`/api/posts/summary` takes the same filter parameters as `/api/posts` (`q`,
`platform`, `author`, `person`, `collection`, `kind`, `review`, `tag`, `untagged`, `new`, `notification`; `sort`, `order`, `offset` and `limit`
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
CPU and disk priority, and cached by path, size and mtime: each phase reads
only the files that are new or changed since, so a pass after a rescan that
changed nothing reads none. Only files whose size another file shares, and
the files of posts that have extra copies, are read. It pauses while a scan
or a delete runs, and before each file it waits (up to a second) while a
request is being answered. A file that changes while it is read gets no
hash until the next pass; rows of files that are gone are dropped. Stopping
the app mid-pass loses at most the last few seconds of work.

After the content hashes, the same worker takes a perceptual hash (a 64-bit
dHash) of every image and video in the index, for the `similar` kind: from
the cached grid thumbnail when there is one, else from the image or the
video's poster, else from a frame ffmpeg extracts (kept as the thumbnail).
A picture that cannot be decoded is tried again only when its file changes.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/duplicates?kind=&offset=&limit=&threshold=` | groups of one kind, see below |
| GET | `/api/duplicates/status` | the hashing worker's progress, see below |
| POST | `/api/duplicates/resolve` | body `{ "group": "…", "keep": "…" }` (trash every other member) or `{ "group": "…", "trash": ["member id", { "member": "…", "items": [2] }, …] }` (trash just those, or just those items of a post), or `{ "groups": [those, …] }` (at most 500), plus `"threshold"` for a similar group; see below |
| POST | `/api/duplicates/dismiss` | body `{ "group": "…" }`, plus `"threshold"` for a similar group → `{ "ok": true }`: "not a duplicate", until restored |
| DELETE | `/api/duplicates/dismiss` | body `{ "group": "…" }` (a dismissal's `id`) → `{ "ok": true }`: forgets the dismissal, the group shows again (the dashboard's **Undo** and **Restore**); 404 when there is no such dismissal |
| GET | `/api/duplicates/dismissed?kind=` | `{ "dismissed": [dismissal, …] }`, newest first, of one kind or (no `kind`) all; see below |

`GET /api/duplicates`: `kind` is `copies` (default), `content` or
`similar` (anything else is a 400), `offset` (default 0), `limit` (default
50, max 500), `threshold` (used by similar only: 0 to 10, default from the
config; anything else is a 400, whatever the kind).
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
        "folder": "/abs/somebody", "meta_path": "/abs/somebody/…_1.jpg",
        "paths": ["/abs/somebody/…_1.jpg"],
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
`total` count the files of that phase that are new or changed (those it
reads; `0` and `0` when there is nothing to do), `bytes` what was read, `hashed` the
files with a content hash, `fingerprinted` those with a perceptual hash,
`finished_at` when the last complete pass ended, `errors` the last 20
files that could not be read.

`POST /api/duplicates/resolve` moves members of each group to the trash,
exactly like `/api/delete` (a copy's files go the same way, with a `copy`
field in the manifest, so the Trash page lists and restores it). A member
goes **whole**: a post with every item of it, also the ones no other member
has. Each group says what goes:

- `"keep": "<member id>"`: every other member, whole (the dashboard's bulk
  "Keep suggested, trash the rest").
- `"trash": [...]`: just these, one entry per member. A member id trashes
  that member whole; `{ "member": "<post id>", "items": [idx, …] }` trashes
  just those items of a post (as `/api/delete` with `media`), the post and
  its other items stay. An item list that names every item of the post is
  the whole post (its last item takes the post anyway). Items are for
  `content` and `similar` groups only: a `copies` group holds one post
  twice, so its members go whole.

Under the same lock as the move, each group is rebuilt from the index and
checked first, and refused (listed in `skipped`, nothing of it moved) when:

- it no longer exists with these members (a scan or another resolve
  changed it): reload;
- the member to keep, a member to trash or an item to trash is not in the
  group (or its post);
- `trash` is empty, or would trash every member whole (one at least must
  stay; a member that only loses items stays);
- items are asked of a copy, or in a `copies` group;
- it is still being hashed;
- a file of any member changed (size or mtime) since it was hashed;
- a file of a member that stays is gone;
- a member to trash whole is one another group of the same call keeps
  (every member that group does not trash whole);
- it is a similar group sent along with other groups (similar groups are
  resolved one at a time).

What stays has one heir: the suggested member if it stays, else the first
that does. The heir takes on the tags and collections of the posts trashed
whole for it (theirs stay, for a restore; posts that only lose items keep
theirs and pass nothing on). When the heir is a copy and the post is
trashed, the copy becomes the post at once, and the post's "keep" decision
moves with it (the other copies left stay copies of it).

```json
{ "ok": true, "resolved": ["4c1d9e0b7a2f3e5d6c8b"],
  "skipped": [{ "group": "…", "error": "a file changed since it was hashed; wait for the next pass" }],
  "posts": ["instagram:R9…"], "copies": [3], "media": [812], "files": 7, "bytes": 10485760, "errors": [] }
```

`posts`, `copies` and `media` are what was moved (`media`: media ids of
items trashed on their own). `resolved` lists the groups where every member
but one had something trashed: they are gone from the list. A group that
kept more members untouched only shrinks (its id changes with its
members): reload. `errors` lists files that could not be moved, as in
`/api/delete`. `ok` is `false` only when nothing could be done (every group
skipped or failed, a bad body, or a scan held the index for more than 30 s).

`POST /api/duplicates/dismiss` stores the group as "not a duplicate". It is
user data, written to `<data_directory>/userdata/dismissed_duplicates.json`
like decisions, and keyed by the members' post ids and copy metadata paths,
so it survives rebuilding the index. It also covers the
same group after a member leaves; a group that gains a member shows again.
Unknown group: 404 `{ "ok": false, "error": "…" }`.

`GET /api/duplicates/dismissed` lists the dismissals (`kind` is `copies`,
`content` or `similar`, anything else is a 400; left out, every kind):

```json
{ "dismissed": [{
  "id": "4c1d9e0b7a2f3e5d6c8b", "kind": "copies", "at": 1727500000,
  "members": [
    { "type": "copy", "id": "copy:3", "post": null, "path": "/abs/somebody/….json", "thumb_url": "/media/copy/3/thumb" },
    { "type": "post", "id": "instagram:C8x…", "post": { "…": "post summary" }, "path": "/abs/cherrieskyl/….json",
      "thumb_url": "/media/17/thumb" }
  ]
}] }
```

- `id` is the id the group had when it was dismissed (the same members
  give the same id), so the dashboard's Undo sends back the id it just
  dismissed. `at` is when it was dismissed.
- `members` are what the dismissal names, in its key's order: post ids and
  copies' metadata paths. A member that is no longer indexed (trashed,
  deleted) is still listed, with `post` and `thumb_url` `null` (and `id`
  `null` for a copy).

`DELETE /api/duplicates/dismiss` takes a dismissal's `id` and removes it,
from the index and from `dismissed_duplicates.json`; the group is listed
again at the next `GET /api/duplicates` if its members still match. Not a
string: 400; no such dismissal: 404 `{ "ok": false, "error": "…" }`.

`GET /api/unmatched` marks an extra copy whose `copies` group is dismissed
with `"dismissed": true` (every other row has `false`): Duplicates does not
list it, so the dashboard points to its Dismissed list instead.

## Tags

Free-form labels, many per post. They are the user's own data: kept in the
`tags` and `post_tags` tables, never touched by a rescan, and written to
`<data_directory>/userdata/tags.json` and `post_tags.json` (2 s after the
last change, `post_tags.json` naming tags by name, not id) so a rebuilt
index gets them back.

Every `userdata/*.json` file is written at once, not 2 s later, when the
change left its table empty: an empty table is read back from its file at
startup, so a file still listing the rows just removed would bring them
back. `tags.json`, `collections.json`, `people.json` and `links.json` keep
each row's `id`, and a rebuilt index gives it back (page URLs are built
from it); a file without ids (older) or with an id that is not a positive
whole number, or one given twice, still loads every row, those under new ids.

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
| GET | `/api/tags` | `[{ "name": "outfits", "color": "#3b82f6", "count": 12, "unused": false }]`, most used first |
| POST | `/api/tags/apply` | body `{ "posts": ["instagram:C8x…"], "add": ["outfits"], "remove": ["todo"] }` → see below |
| POST | `/api/tags/rename` | body `{ "from": "outfit", "to": "outfits" }` → `{ "ok": true, "name": "outfits", "merged": true }` |
| POST | `/api/tags/delete` | body `{ "name": "outfits" }` → `{ "ok": true, "posts": 12 }`: removes the tag from every post |
| POST | `/api/tags/color` | body `{ "name": "outfits", "color": "#3b82f6" }`, or `null` for none → `{ "ok": true, "color": "#3b82f6" }`; a body without `color` is a 400 |
| POST | `/api/tags/delete-unused` | body `{ "names": ["old", "todo"] }` → `{ "ok": true, "deleted": ["old"] }`, see below |

`/api/tags/apply` adds and removes tags on up to 5000 posts at once (more is
a 400). `add` and `remove` are lists of names, either may be omitted but not
both; a name in `add` that does not exist yet is created (unless none of
the ids is in the index: then nothing is created). Ids that are not
in the index are ignored. Response:

```json
{ "ok": true, "posts": ["instagram:C8x…"], "added": 3, "removed": 1, "created": ["outfits"] }
```

`posts` lists the ids that exist, `added` and `removed` count the links that
actually changed, `created` the new tags.

`/api/tags/rename` renames a tag. When `to` already names another tag, the
two are merged: every post of `from` gets `to`, and `from` is gone
(`merged: true`). Changing only the case of a name is a rename. An unknown
`from` is a 404, a `from` or `to` that cannot be a tag name a 400. `/api/tags/delete` of an unknown name is a
404.

`color` is the colour the tag's chips wear, `#rrggbb` (stored in lower
case), or `null` for none. `/api/tags/color` of a colour in any other form is
a 400, of an unknown name a 404.

`unused` is true for a tag on no post at all, not even one in the trash; a
tag only on trashed posts has `count` 0 but is not unused, as it comes back
with them. `/api/tags/delete-unused` deletes those of the names given that
are unused when it runs (a tag put on a post since the list was read is
kept, and left out of `deleted`); nothing is deleted on its own. At most
5000 names; a bad body is a 400.

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
  (or while the chosen post is in the trash) `cover` is that of the first
  indexed post in the collection. Removing the cover post from the
  collection clears `cover_post`. `cover` is a post
  summary's `cover` (see [Post](#post)), `null` for an empty collection.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/collections` | `[collection, …]`, in the user's order (new ones last) |
| POST | `/api/collections` | body `{ "name": "Moodboard" }` → `{ "ok": true, "collection": {…} }`; 400 for a bad or taken name |
| GET | `/api/collections/<id>?offset=&limit=` | `{ "collection": {…}, "total": 24, "posts": [summary, …] }` in the collection's order; `limit` default 60, max 200; 404 if unknown |
| POST | `/api/collections/<id>/rename` | body `{ "name": "…" }` → `{ "ok": true, "collection": {…} }`; 400 for a bad or taken name |
| POST | `/api/collections/<id>/delete` | → `{ "ok": true, "posts": 24 }`; the posts stay |
| POST | `/api/collections/<id>/add` | body `{ "posts": ["instagram:C8x…"] }` (at most 5000) → `{ "ok": true, "added": ["instagram:C8x…"] }`, appended at the end in the order given; posts not in the index or already there are skipped |
| POST | `/api/collections/<id>/remove` | body `{ "posts": […] }` (1 to 5000 ids) → `{ "ok": true, "removed": 2 }` |
| POST | `/api/collections/<id>/order` | body `{ "posts": […] }` (1 to 5000 ids) → `{ "ok": true }`, see below |
| POST | `/api/collections/<id>/cover` | body `{ "post": "instagram:C8x…" }`, or `null` for the first post → `{ "ok": true, "collection": {…} }`; 400 if the post is not in it |
| POST | `/api/collections/reorder` | body `{ "ids": [3, 1, 2] }` (1 to 5000 ids) → `{ "ok": true, "collections": [collection, …] }` in the new order |

Every `/api/collections/<id>/…` call answers 404 `{ "ok": false, "error": … }`
for an unknown id.

`/order` takes posts of the collection in their new order and puts them in
the places those same posts held before, the rest staying where they are.
Sending one page in its new order reorders that page; sending every post
reorders the whole collection. Ids not in the collection are ignored.

`/api/collections/reorder` does the same for the collections themselves:
the ids given take the places they held between them, in the order given;
unknown ids are ignored, a body that is not a list of ids is a 400. The order
is user data, written to `collections.json` with the rest.

## People

One person often posts on several platforms, under different handles. An
**account** is a platform and an author id, as indexed (instaloader's
`owner.id`, gallery-dl's `author.id`); a **person** links accounts into one.
An account with no person is shown on its own, as before.

People and their links are user data: tables `people`, `person_accounts`
and `person_labels`, never touched by a rescan, and written to
`<data_directory>/userdata/people.json`, `person_accounts.json` and
`person_labels.json` (accounts by platform and author id, people by name)
2 s after the last change, so a
rebuilt index gets them back. Links are by account, not by post: a post
trashed and restored, or replaced by a duplicate copy, keeps its person.
Nothing moves on disk.

An account is its platform's own id where the metadata has one
(instaloader's `owner.id`, gallery-dl's `author.id`, yt-dlp's `channel_id`
for YouTube), never its handle: a renamed account stays one account, with
its person and one feed.

Posts rebuilt from file names have no id: their author id is the profile
folder's name. That folder name becomes an **alias** of the account's id
when instaloader's id file in the folder (`<folder>/id`, or `<folder>_id`
beside it; not in a folder of another tool's posts) names the id, or else when the same folder also holds posts with
metadata whose handle is that name or one in its file names (exactly one
account). Aliases are derived on every scan, not stored as user data. An account and its aliases read as one: one row in `/api/authors` and
Storage, one link (linking or unlinking an alias acts on the account), and
the `author` and `person` filters take the aliases' posts in. No alias is
made between two ids linked to different people (merging them is the
user's call), and once every post of the id is gone the folder name is an
account of its own again.

A link (and a source's `account`) made to a folder name moves to the id
once the id's posts are indexed: a scan rewrites it, and the userdata files
with it, so a folder renamed later (instaloader renames a profile's folder
after a rename) keeps nobody from their person. A link to an account no
post has any more moves too, when exactly one account on its platform had
that handle (the same account, its folder renamed), and never to an account
linked to someone else: two folders naming one id, linked to two people,
stay apart until the user merges them. A media root not found on a scan
keeps its id files, as its posts stay.

An **account** (`/api/authors` rows, a person's `accounts`):

```json
{ "platform": "instagram", "id": "123456", "handle": "somebody", "name": "Some Body",
  "aliases": ["somebody"], "count": 812, "bytes": 2147483648, "newest": 1727481600,
  "url": "https://www.instagram.com/somebody/", "person": { "id": 3, "name": "Some Body" },
  "handles": [{ "handle": "somebody", "first": 1700000000, "last": 1727481600 },
              { "handle": "some.body.old", "first": 1600000000, "last": 1690000000 }],
  "names": [{ "name": "Some Body", "first": 1600000000, "last": 1727481600 }], "no_sync": false }
```

- `handle` and `name` are those of the newest post (handles change), or the
  new handle of a rename the user accepted after it. An account whose posts
  are all rebuilt from file names, in a profile folder (one of its files is
  named after the folder), reads as that folder's handle instead, whatever
  its newest file is named: a file name holds the target it was downloaded
  for, which may be someone else's or an older name. A folder no file is
  named after (`saved`, a profile renamed since) keeps the newest post's
  handle. Each post keeps the handle its file name gives (its
  `author.handle`, and in `handles` below).
- `count` and `bytes` cover the posts in the index, aliases included;
  `newest` is the newest `posted_at`.
- `url`: the profile's address for `instagram`, `twitter`, `tiktok` and
  `youtube`, `null` otherwise.
- `person`: the person the account is linked to, or `null`.
- `no_sync`: [Don't sync](#dont-sync) is on for the account itself. Its
  person's own switch is the person's `no_sync`; either one stops its
  sources.
- `handles` and `names`: **handle history**, every handle and display name
  the account's posts (aliases included) carry, with the `posted_at` of the
  first and last post under it, the most recent first. Handles also come
  from instaloader's id files (the folder's name when the file was written,
  dated by the file's mtime) and from renames the user accepted for a
  source (`POST /api/sources/<id>/rename`: the old handle last seen and the
  new one first seen then). Derived from the index on every request
  (cached), so a rescan rebuilds it; accepted renames are user data, table
  `handle_renames`, written to `<data_directory>/userdata/handle_renames.json`
  and restored after a rebuild. A renamed account keeps its id, so its
  posts stay one account, and its old handles are listed here. `first` and
  `last` are `null` when no post under it has a date. The dashboard's
  Creators search and Feed author picker match any of them, so a person is
  found by an old handle.

A **person**:

```json
{ "id": 3, "name": "Some Body", "notes": "", "created_at": 1727500000,
  "gender": "Female", "country": "FR", "labels": ["cosplay", "Travel"],
  "avatar": "3f9a1c0b7d2e4f6a8b1c",
  "accounts": [account, …], "platforms": ["instagram", "twitter"],
  "count": 900, "bytes": 2347483648, "newest": 1727481600, "no_sync": false }
```

- `name`: 1 to 64 characters, no `"` or control characters, unique without
  regard to (ASCII) case. `notes`: free text, at most 5000 characters.
- `gender`: free text, at most 40 characters, spaces collapsed, `""` when
  unset. `Female`, `Male` and `Transgender` are presets: one sent in another
  case is stored as the preset; anything else is kept as sent.
- `country`: an ISO 3166-1 alpha-2 code, uppercase (`"fr"` is stored as
  `"FR"`), `""` when unset. A code that is not assigned is a 400.
- `labels`: free-form labels describing the person, by name, at most 50,
  each a name as a tag's (1 to 64 characters, no `"` or control
  characters), once each without regard to (ASCII) case. They are not
  [tags](#tags): post tags and the Tags page never see them.
- `avatar`: the name of the person's picture, or `null`; see
  [Profile pictures](#profile-pictures).
- `accounts`: most posts first. An account linked but not in the index
  (before the first scan of a rebuilt index, or every post trashed) is listed
  with `count` 0 and `handle` `null`.
- `count`, `bytes`, `newest`: over every account.
- `no_sync`: [Don't sync](#dont-sync) is on for the person: none of their
  sources syncs.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/people` | `[person, …]`, by name |
| POST | `/api/people` | body `{ "name": "…", "accounts": [{ "platform": "instagram", "id": "123456" }], "profiles": ["https://x.com/somebody"] }` (`accounts` and `profiles` may be omitted; at most 500 accounts and 20 profiles; `profiles` with no media root set is a 400) → `{ "ok": true, "person": {…}, "sources": [source, …] }` |
| POST | `/api/people/<id>/sync` | → `{ "ok": true, "sources": 3, "jobs": [job, …], "skipped": 0, "errors": [{ "source", "error" }], "not_synced": 0 }`: a sync of each of the person's sources, as Sync all does for every source (`not_synced`: those left out, set to [Don't sync](#dont-sync)); 404 for an unknown id |
| GET | `/api/people/attributes` | `{ "labels": [{ "name", "count" }], "genders": [{ "name", "count" }], "presets": ["Female", "Male", "Transgender"], "countries": ["AD", …] }`: the labels and genders people have, most used first (what the person page suggests and the Creators filters offer), and every country code a person can have |
| GET | `/api/people/<id>` | person, with its `links` (see [Links](#links)), or 404 |
| POST | `/api/people/<id>` | body with any of `{ "name": "…", "notes": "…", "gender": "…", "country": "FR", "labels": ["…"] }` (none is a 400; `""` clears gender or country; `labels` replaces the person's labels, those kept keep their date) → `{ "ok": true, "person": {…} }` |
| DELETE | `/api/people/<id>` | → `{ "ok": true, "unlinked": 2 }`: the person and its links are gone, never a post; its saved [links](#links) stay, tied to no one |
| POST | `/api/people/<id>/accounts` | body `{ "add": [{ "platform", "id" }], "remove": [{ "platform", "id" }] }` (either may be omitted; at most 500 each) → `{ "ok": true, "added": 1, "removed": 0, "person": {…} }` |
| POST | `/api/people/merge` | body `{ "ids": [3, 7], "name": "…", "accounts": [{ "platform", "id" }], "confirm": true }` → `{ "ok": true, "person": {…} }`, see below; 409 `{ "ok": false, "confirm": true, "sources": 2, "error" }` when one of them is set to [Don't sync](#dont-sync) and sources that sync now would stop, until sent with `"confirm": true` |

- An account belongs to one person at most: adding it to a person (create,
  add, merge) takes it from any other. An account that is not in the index
  (no post, no alias) is a 400.
- `added` and `removed` count the links that changed.
- `profiles` (at most 20): profile links, or Instagram names, each made a
  [source](#sources) of the new person, as `POST /api/sources` with
  `person` would, with default options. Nothing is downloaded until a sync,
  and no folder is made; two links to one profile make one source. A
  profile whose account is indexed already (its folder holds posts) links
  that account too. All or nothing: a link that is not a profile in the
  routing table, that already has a source, or whose account is another
  person's (and not in `accounts`) is a 400 naming it, and nothing is
  created or moved.
- Sync on a person queues its sources (those shown with it, see
  [Sources](#sources)) through the normal [job](#jobs) queue: one at a
  time per tool, the tool's pause between two, a source already queued or
  running counted in `skipped`, a refused one in `errors`.
- Merge keeps the first id: its name (or `name`, when given), and the
  others' notes appended to its own. Its labels are those of all of them;
  its gender, country and picture are the first person's (in `ids` order)
  that has one (the others' pictures are deleted). Every account of the others, and
  `accounts`, move to it, and so do the others' [links](#links), after its
  own; the others are gone. `ids` must all exist (404
  otherwise); with one id, `accounts` must not be empty.
- A bad name, a name taken by another person, bad notes, a gender over 40
  characters, a country that is not a code, bad labels or a malformed
  account list is a 400 `{ "ok": false, "error": "…" }`; an unknown id a 404.

### Profile pictures

A person can have a picture, shown instead of their initial wherever the
dashboard shows them. It is made from what is on this machine, never
fetched: a picture uploaded, an image of one of their posts (a video's
poster), or a profile picture instaloader saved beside their posts
(`<date>_UTC_profile_pic.jpg`, in the folders of their posts and of their
sources). `backend/avatars.py`.

Whatever it was, it is opened as JPEG, PNG, WebP, GIF or AVIF by its
content (never by its name), turned upright (EXIF), cropped to a 2:3
portrait (as Stash shows a performer) and saved again as WebP: 600 x 900
for the person's page, 300 x 450 for the Creators cards, and a 96 px square
of the portrait's upper part for the round chips (pickers, a page scoped
to the person); a smaller picture is not enlarged. They are
`<data_directory>/avatars/<avatar>.webp`, `<avatar>-300.webp` and
`<avatar>-96.webp`, never in a media folder. A picture saved before
portraits (a 512 px square and its 96 px one) keeps working: asked for
`-300`, its large file is served, and the dashboard shows it covering the
portrait's box. `avatar` is a hash of the picture: two people with the
same one share its files. It is user data, a column of `people` exported
with the person in `people.json`, so a rebuilt index gets it back (the
`avatars` folder is not touched). Its files are deleted once no person has
it any more: a new picture, Remove, the person deleted or merged into
another.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/people/<id>/avatar/choices` | `{ "profile_pics": [{ "key", "name", "at", "account": { "platform", "id", "handle" }, "url" }], "media": [{ "id", "post", "kind", "posted_at", "thumb" }] }`: instaloader's profile pictures of their accounts, newest first (at most 6), and the newest pictures of their posts (at most 60: images, and videos with a poster) |
| POST | `/api/people/<id>/avatar` | the picture as the body (`Content-Type` `image/*` or `application/octet-stream`, at most 15 MB; the crop in the query, `?x=&y=&size=`), or JSON `{ "media": 17 }` or `{ "profile_pic": "<key>" }` with an optional `"crop": { "x", "y", "size" }` → `{ "ok": true, "person": {…} }` |
| DELETE | `/api/people/<id>/avatar` | → `{ "ok": true, "person": {…} }`, the person without a picture |

- The crop: `x` and `y`, the portrait's left and top as fractions (0 to
  1) of the upright picture's width and height, `size` its width and height
  as a fraction of the largest 2:3 box the picture holds (0.01 to 1). A
  portrait that would leave the picture is moved back in. None: as large as
  it can be, in the middle across and, down a tall picture, nearer the top
  (a quarter of what is cut off is above, where faces are): a square
  profile picture loses its sides only.
- Refused with a 400: what is not one of those formats, a picture over 50
  million pixels (read from its header, before anything is decoded), a
  `media` that is not one of the person's (or a video without a poster), a
  `profile_pic` not listed; with a 413, an upload over 15 MB. Nothing
  changes then.

### Link suggestions

Accounts likely to be one person, found in the index and in metadata already
downloaded. Nothing is ever fetched (a link-in-bio page is, on request
only: see [Link-in-bio import](#link-in-bio-import)).

| Method | Path | Returns |
|---|---|---|
| GET | `/api/people/suggestions` | `{ "suggestions": [suggestion, …], "dismissed": 2 }`, most likely first |
| POST | `/api/people/suggestions/dismiss` | body `{ "id": "…" }` → `{ "ok": true }`: "not the same person" (the dashboard's **Not them**), for good; 404 when the id is not listed (reload) |

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
    `youtube.com/@<handle>`, any of its handles, old ones too). Read on every scan from instaloader's
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
  A group found for several reasons scores 0.02 more per extra reason. A
  group of more than 8 accounts is left out: they share something common
  (a name like "Official"), not a person. Computed from the index alone
  (cached until the next change), so its cost grows with the number of
  accounts, not posts.
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

### Link-in-bio import

The one web page FeedVault fetches itself, and only when asked: a person's
link-in-bio page (linktr.ee and the like), read for the profiles it links
to, which are suggested, and for the links it shows a visitor, which are
saved to that person's [Links](#links) so they outlast the page. Off until
`{ "bio_import": true }` is set (Settings → Downloads → Link-in-bio
import); `backend/biofetch.py`.

| Method | Path | Returns |
|---|---|---|
| POST | `/api/people/<id>/bio-import` | body `{ "url": "https://linktr.ee/somebody" }` → `{ "ok": true, "url": "https://linktr.ee/somebody", "accounts": [found, …], "links": { "saved": 7, "already": 2, "elsewhere": 0, "dropped": 1, "over": 0 } }` |

```json
{ "platform": "twitter", "handle": "somebody", "url": "https://x.com/somebody", "tool": "gallery-dl",
  "status": "indexed", "profile_url": "https://x.com/somebody", "account": { "platform": "twitter", "id": "641286", "handle": "somebody",
  "name": "Some Body", "count": 12, "url": "https://x.com/somebody" }, "person": null, "source": null }
```

- **No account or source is added.** Each account found is a suggestion; the dashboard
  adds the ones the user picks with the usual calls:
  `POST /api/people/<id>/accounts` for an account in the index
  (`status` `indexed`), `POST /api/sources` with `person` for one not
  downloaded yet (`new`).
- `status`: `linked` (this person's already, as an account or a source),
  `other` (linked to `person`, someone else), `indexed` (an account in the
  index linked to nobody), `source` (a source linked to nobody yet: link it
  from its account or the Sources page), `new` (nothing of it here).
  `account` is the indexed account whose handle (any it had), alias or id
  is the link's, when exactly one is; `source` the id of a source already
  there for it.
- What counts as an account: a link that `POST /api/sources` would take
  (its host in the [routing table](#link-routing)) and that names a profile:
  the profile's own page or one of its tabs (`youtube.com/@name/videos`),
  never a post, a video, a search, an intent link or a site's home page;
  TikTok only as `tiktok.com/@name`. Two links to one profile count once.
  `url` is the link as FeedVault normalizes it (https, no query), what
  `POST /api/sources` is sent. `profile_url` is the profile's address as
  FeedVault builds it from the platform and the handle (the indexed
  account's `url`, else Instagram, X, TikTok or YouTube `@name`), or
  `null`: the only link the dashboard shows in this list, never one taken
  from the page. At most 100 accounts, from at most 1000 distinct links.
- **The page's links are saved** to the person's [Links](#links): first the
  page's own address (`url`, after redirects), then every link the page
  shows a visitor, profiles and others alike, in the page's order. `links`
  counts them:
  - `saved`: new links of theirs, last in their order, `notes` empty.
  - `already`: links they have already, by URL as `POST /api/links` stores
    it, or in another spelling of it (`http`/`https`, with or without
    `www.`, with or without a last slash). They are left exactly as they
    are: the title, notes and place the user gave them are never written
    over.
  - `elsewhere`: URLs saved for another person or for no one (a URL is
    saved once): left where they are, never moved.
  - `dropped`: links refused, see below.
  - `over`: new links past the 200 one import saves; importing again
    saves the next ones (what is saved already does not count).

  **Nothing is ever removed or replaced**: a link the page no longer shows
  stays saved, which is the point, and importing again only adds what is
  new. Nothing is saved when the import fails (any `ok: false` answer).
  The saved links are user data like any other link
  (`userdata/links.json`), and none is ever fetched: saving is text.
- **What counts as a link the page shows**: the `href` of an `<a>` or
  `<area>`; in a JSON block, a text under a key named for a link (`url`,
  `href`, `link`, `uri`, a name ending in one of them such as `linkUrl`,
  their plurals, `sameAs`, `website`), which is where Linktree keeps its
  buttons and icons; and any other address in the page that is an account
  as above. Left out, silently:
  - anything in a JSON block under a key named for a part of the page or
    for the site's own workings (`image`, `avatar`, `thumbnail`, `picture`,
    `icon`, `logo`, `background`, `banner`, `font`, `video`, `theme`,
    `style`, `seo`, `tracking`, `analytics`, `endpoint`, `config`,
    `environment`, `statsig`, `sponsor`… anywhere in the key, at any depth
    above the link) or under an `ALL_CAPS` settings key;
  - the bio site's own pages: the host of the page fetched and its
    subdomains (`linktr.ee/s/about`, `ugc.production.linktr.ee`,
    `media.link.me`, `about.link.me`), and
    `linktree.com` / `later.com` for Linktree / Linkin.bio. Another
    person's page on the same site is left out with them; a page on
    another bio site is kept;
  - hosts that serve a page's parts or count its visitors (`cloudfront.net`,
    `amazonaws.com`, `googleapis.com`, `gstatic.com`, `googletagmanager.com`,
    `fbcdn.net`, `sentry.io`, `schema.org`, `w3.org` and the like, and any
    subdomain named `cdn`, `static`, `assets`, `img`, `images`, `media`,
    `fonts`, `js`, `css`, `ugc`, `api`, `analytics`, `tracking`… on any
    site);
  - files by their ending: images, fonts, scripts, styles, `.json`,
    `.map`, video and audio (`.png`, `.woff2`, `.js`, `.css`, `.mp4`…). A
    `.pdf` or `.zip` is kept.

  **Dropped and counted** (`dropped`), never saved: what `POST /api/links`
  refuses (any scheme but `http`/`https`: `mailto:`, `tel:`,
  `javascript:`, `data:`; a login part; a backslash, whitespace or control
  character; more than 2048 characters), and, since the address comes from
  someone else's page, a port of its own and a host that is not a public
  site's name: an IP address in any spelling (`127.0.0.1`, `[::1]`,
  `2130706433`, `0x7f.1`), a single word (`localhost`), and names ending in
  `.local`, `.localhost`, `.internal`, `.intranet`, `.lan`, `.home`,
  `.corp`, `.arpa`, `.test` or `.invalid`. An IDN host is saved in its
  ASCII form (`xn--…`), and what is not ASCII in the path, query or
  fragment percent-encoded as a browser sends it, so a look-alike name
  shows as what it is and no right-to-left override is stored.
- **Titles**: the label the page gives a link (an anchor's text, else its
  `aria-label`, `title` or image's `alt`; in JSON the `title`, `label`,
  `text`, `name`, `buttonText` or `caption` beside the link), as plain
  text: control and format characters removed (escape sequences,
  right-to-left overrides, zero-width spaces), whitespace collapsed, cut to
  300 characters. A label that spells another site's address than the
  link's (`https://…`, `www.…`, `site.tld/…`) is not used. No label, or
  one not used: `""`, as a link saved without a title. The page's own
  address is saved without one.
- How the page is read: with Python's `html.parser`, the `href` of every
  `<a>` and `<area>`, then every `http(s)` string in the page's JSON blocks
  (`<script id="__NEXT_DATA__">`, `type="application/json"` or
  `application/ld+json`), which some sites draw their links from. No script
  runs, the page itself is never stored or logged (the server's log has
  its URL and the counts above), and no link in it is fetched.
- **The fetch.** One page, the URL given, and its redirects:
  - **Sites**: `https` on port 443, no login part, the host exactly one of
    `linktr.ee`, `beacons.ai`, `lnk.bio`, `solo.to`, `campsite.bio`,
    `linkin.bio`, `allmylinks.com`, `link.me` (a leading `www.` and a
    trailing dot are dropped, and an IDN is compared in its ASCII form, so
    a look-alike name matches nothing). Sites where a profile is a subdomain or the user's
    own domain are left out: a host check cannot tell them from any site.
    A link with spaces, control characters or `\` is refused, and the
    pasted one must name a page, not the site's home.
  - **Addresses**: every address the host resolves to must be public
    (Python's `is_global`, and not multicast, IPv4-mapped or -compatible,
    6to4, NAT64 or Teredo); one that is not refuses the whole fetch. The
    connection goes to one of those checked addresses, never through a
    second lookup, while TLS sends the real host name and checks the
    certificate against it.
  - **Nothing from the environment**: no proxy (`HTTP(S)_PROXY`,
    `ALL_PROXY` are not read), no `.netrc`, no cookies, no `Referer`; the
    request carries `Host`, a `User-Agent` naming FeedVault, `Accept:
    text/html`, `Accept-Encoding: identity` and `Connection: close` only.
  - **Limits**: at most 3 redirects (301, 302, 303, 307, 308), each checked
    again from scratch (site, port, addresses); 10 s for the whole fetch
    (lookups, connections, TLS and every byte, redirects included); 2 MB of
    body, counted while reading; 32 KB of headers; `text/html` only; any
    `Content-Encoding` but `identity` refused; any status that is not 2xx
    (or a redirect) is an error.
  - **One at a time**, at least 5 s apart (a link refused before any
    request does not count).
- Errors are `{ "ok": false, "error": "…" }`, short and specific: 400 for
  the link (`only https links`, `not an allowed site: …`), 403 when the
  switch is off or the request comes from another site's page (see
  [Security rules](#security-rules)), 404 for an unknown person, 429 while another
  import runs or within 5 s of the last, 502 for the site (`linktr.ee's
  address is not public (10.0.0.1)`, `timed out`, `page too large`, `not a
  web page (application/json)`, `the site answered 404`, `too many
  redirects`, `the page redirects elsewhere: …`). The server log names the
  URL fetched and what came of it; the page itself is never logged or
  stored.

### Save links from the browser

The same import from the [userscript](#save-from-the-browser-userscript),
with the links as the browser rendered them (so a page drawn by JavaScript
works too) and **nothing fetched**, by the browser's extension or by
FeedVault: on a person's page of one of the sites above, the userscript
bubble's **Save links** sends the page's address and its
outbound `<a href>` links with their text, to a person's Links or to no
one's (**Unsorted**). Under the same switch (`bio_import`, Settings →
Downloads → Link-in-bio import): it is the same feature, from another
place. Two of the few routes another site may call (see [Security
rules](#security-rules)).

| Method | Path | Returns |
|---|---|---|
| POST | `/api/links/import` | body `{ "page_url": "https://linktr.ee/somebody", "links": [{ "url": "https://x.com/somebody", "title": "X" }, …], "person": 3 }` → `{ "ok": true, "url": "https://linktr.ee/somebody", "person": 3, "links": { "saved": 7, "already": 2, "elsewhere": 0, "dropped": 1, "over": 0 } }` |
| GET | `/api/links/people` | `{ "people": [{ "id": 3, "name": "Some Body" }, …], "recent": [3, …] }`: every person's id and name, by name, and the ids of the ten most recently given a link, latest first (the userscript's picker) |

- `page_url`: what the bio-import fetch accepts as the pasted link (https,
  port 443, the host one of the sites above exactly, a page and not the
  site's home), else a 400 with why (`not an allowed site: …`, `only https
  links`…). It is saved first, untitled.
- `links`: at most 1000, each `{ "url", "title" }` (`title` may be left
  out or `null`): `url` text of at most 2048 characters starting
  `http://` or `https://`, `title` text of at most 2000. Any other key,
  type, scheme or size, or a missing `page_url` or `links`, is a 400 and
  nothing is saved.
- `person`: a person's id, or `null` / left out for no one (their
  `position` is `null`, as for any link of no one). An unknown id is a 400.
- What is kept is what the fetched import would keep from the same links:
  each once (its first label that is not empty), through the same filters
  (the bio site's own pages, a page's parts and trackers left out
  silently; a port, an IP address or a private name `dropped`), titles
  cleaned the same way, and **insert-only** the same way: `already` and
  `elsewhere` links are left exactly as they are, nothing is removed, at
  most 200 are saved (`over`). With no person, `already` counts the links
  of no one, `elsewhere` a person's.
- 403 when the switch is off (`link-in-bio import is off: turn it on in
  Settings → Downloads`). No account or source is suggested or added: the
  links are bookmarks. The server log has the page's URL and the counts.

## Links

Web addresses worth keeping that are not an account FeedVault downloads: a
creator's Linktree, Patreon, personal site or Discord invite, an interview,
an article. A link may be tied to one person, or to no one.

Links are user data: table `links`, never touched by a rescan, and written to
`<data_directory>/userdata/links.json` (by URL, the person by name, as
`person_accounts`) 2 s after the last change, so a rebuilt index gets them
back; a person named there and missing from `people.json` is created again,
and a row whose URL is not `http://` or `https://` is skipped.

FeedVault never fetches a saved link: no title, preview or icon is looked
up, and nothing leaves the machine. (The [link-in-bio
import](#link-in-bio-import) fetches only the page pasted into it, when
switched on; it saves that page's address and the links it shows to the
person's Links, as text, and fetches none of them.) The dashboard opens a
link in a new tab (`rel="noopener noreferrer"`), and only an `http:` or
`https:` one.

A **link**:

```json
{ "id": 12, "url": "https://www.patreon.com/somebody", "title": "Patreon", "notes": "",
  "site": "patreon.com", "kind": "social", "person": { "id": 3, "name": "Some Body" },
  "position": 2, "created_at": 1727500000 }
```

- `url`: stored cleaned, and saved once. Only `http` and `https`, with a
  host; no user name or password, backslash, whitespace or control
  character inside; at most 2048 characters. Spaces around it are dropped,
  the scheme and host lowercased, a default port dropped, and so is the
  `/` of a bare host (`HTTPS://Example.com/` is `https://example.com`).
  Anything else is a 400.
- `site`: the host without `www.`, `m.` or `mobile.`, cut to its registrable
  part (`someone.substack.com` is `substack.com`, `www.bbc.co.uk` is
  `bbc.co.uk`). `kind`: `social` when `site` is a social or creator
  platform (`instagram.com`, `x.com`, `tiktok.com`, `youtube.com`,
  `patreon.com`, `linktr.ee`, `discord.gg` and the like; the list is
  `links.SOCIAL_HOSTS`), else `other`. Both are read from the URL on every
  request, never stored.
- `title`: at most 300 characters, its whitespace collapsed; `""` for none.
  `notes`: at most 5000 characters.
- `person`: `{ id, name }` or `null`. `position`: the link's place among its
  person's links, `null` for a link of no one.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/links?person=&kind=&site=&q=` | `{ "links": [link, …], "sites": [{ "site": "patreon.com", "count": 4 }, …] }`, see below |
| POST | `/api/links` | body `{ "url": "…", "title": "…", "notes": "…", "person": 3 }` (all but `url` may be omitted; `person` `null` for no one; from another site, the userscript's "Add this page", `url` and `title` only, else a 403) → `{ "ok": true, "link": {…} }` |
| POST | `/api/links/<id>` | body any of `url`, `title`, `notes`, `person` (`null` unties it; none is a 400) → `{ "ok": true, "link": {…} }` |
| DELETE | `/api/links/<id>` | → `{ "ok": true }` |
| POST | `/api/links/assign` | body `{ "ids": [12, 9], "person": 3 }` (1 to 5000 ids; `person` `null` unties them; optional `if_person`) → `{ "ok": true, "moved": 2, "skipped": [], "links": [link, …] }`, see below |
| POST | `/api/links/delete` | body `{ "ids": [12, 9] }` (1 to 5000 ids) → `{ "ok": true, "deleted": 2 }`; ids no link has are passed over |
| POST | `/api/people/<id>/links/order` | body `{ "ids": [12, 9] }` (1 to 5000 ids) → `{ "ok": true, "links": [link, …] }`, the person's links in their new order |
| GET | `/api/people/recent-links?limit=10` | `{ "people": [{ "id": 3, "name": "Some Body", "at": 1727500000 }, …] }`: the people most recently given a link, latest first, see below |

- `GET /api/links`: newest first; with `person`, in that person's order.
  `person` is a person id (one that is not an id matches nothing) or `none`
  for links of no one; `kind` is `social` or `other` (anything else is a
  400); `site` matches `site` exactly; `q` (at most 200 characters) matches
  any part of the URL, title or notes, without regard to (ASCII) case.
  `sites` counts every link by site, whatever the filters, for a site
  picker, most links first.
- A URL saved already (once cleaned) is a 409
  `{ "ok": false, "error": "that link is saved already", "id": 12, "link": {…} }`
  with the saved link's id and the link itself (its `person` says whose it
  is, `null` for no one), on create and on edit.
- `/api/people/recent-links` (the quick-add's picker): each person who has a
  link, by `at`, when a link was last given to them: created with them, or
  moved to them from no one or from someone else (an edit that keeps the
  person does not count). A person with no link left is not listed.
  `limit` is 10 by default, kept between 1 and 50. The tie time is kept per
  link (`tied_at`, in `links.json` too; a link restored from an older file
  counts from `created_at`).
- `/api/links/assign` (the Links page's Unsorted queue and its selection)
  gives every link listed to one person, or to no one, in one transaction:
  each as `POST /api/links/<id>` with `person` would (last in the person's
  order, in the order of `ids`; `tied_at` now, so the person comes first in
  `recent-links`). A link that is theirs already does not move and is not
  counted in `moved`; `links` are the links listed that still exist, as they
  are now. `person` must be in the body. An id no link has is a 404
  `{ "ok": false, "error": "no such link", "missing": [7] }`, and nothing
  moves. With `"if_person": 3` (or `null`; the page's Undo), only the links
  that are that person's right now move; the others, and ids no link has,
  are passed over and listed in `skipped` (no 404). Without it `skipped` is
  `[]`. The batch holds the database's write lock from the check to the end.
- The links tied to no one (`?person=none`, the Links page's **Unsorted**)
  are counted in `unsorted_links` of `GET /api/jobs`, for the sidebar's badge.
- A person's links (`GET /api/people/<id>`, `/links/order`) come socials
  first, then the others, each in the person's order. A new link, or one
  given to another person, goes last in its person's order; one untied
  from its person loses its place.
- `/links/order` puts the ids given in the places they held between them,
  in the order given; the person's other links do not move, and ids not
  among their links are ignored (as `/api/collections/<id>/order`).
- Deleting a person keeps their links, tied to no one. Merging people moves
  the others' links to the one kept, after its own.
- A bad body is a 400 `{ "ok": false, "error": "…" }`, a JSON body that is
  not an object too (see the top of this file); an unknown link
  or person id in the path a 404, an unknown `person` in a body a 400.

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
`account` `null` and keeps that person; the first indexing that finds posts
in its folder sets `account` (its folder's account), and links that account
to the person when the account has no person yet (never taken from another
person). Any indexing counts, not only its sync's: a post saved into the
folder (the Save button), files put there and a rescan. A rescan also
repairs a source left without its account by an older version.

```json
{ "id": 4, "tool": "instaloader", "platform": "instagram", "target": "somebody",
  "url": "https://www.instagram.com/somebody/",
  "folder": "/archive/instaloader/somebody",
  "account": { "platform": "instagram", "id": "somebody" },
  "person": { "id": 3, "name": "Some Body" },
  "options": { "full_history": false, "session": null, "content": ["posts", "reels"], "media": "all",
               "since": "2024-01-01", "first_posts": null, "script": null, "metadata": null, "schedule": "daily" },
  "overrides": ["content", "since"],
  "defaults": { "full_history": false, "session": null, "content": null, "media": "all", "since": null,
                "first_posts": null, "script": null, "metadata": null, "schedule": "daily" },
  "metadata": { "mode": "full", "inherited": "full", "setting": "full" },
  "choices": { "content": ["posts", "reels", "stories", "highlights", "tagged"], "content_default": ["posts"],
               "login": ["stories", "highlights", "tagged"], "media": true, "since": true, "first_posts": false,
               "metadata": ["full", "slim", "none"] },
  "created_at": 1727500000, "last_sync_at": 1727503600, "last_job_id": 41, "synced": true,
  "last_result": { "state": "failed", "error": "rate_limited",
                   "message": "Instagram is limiting requests: wait a while before syncing again",
                   "line": "…429 - Too Many Requests…", "added": 0, "job": 41, "outdated": false,
                   "failures": 1, "health": "rate_limited", "ok_at": 1727420000 },
  "health": { "state": "rate_limited", "result": "failed", "ok_at": 1727420000, "last_sync_at": 1727503600,
              "line": "…429 Too Many Requests…", "failures": 1, "rename": null,
              "login": { "mode": "login", "found": true, "accepted": null }, "paused": null, "warning": null },
  "job": { "id": 42, "state": "queued", "waits_until": 1727503660 },
  "session": { "mode": "login", "user": "me", "session_file": true },
  "schedule": { "every": "daily", "next_at": 1727510800, "paused": false, "skipped": null, "stopped": null,
                "failures": 1 },
  "script_warning": null, "no_sync": false }
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
- `no_sync`: its account (before it has one, its folder's account,
  else the one with its link's handle), the person it shows under, or the
  person it was added to is set to [Don't sync](#dont-sync): the source is
  kept but never synced.
- `options`: what its syncs use, option by option: the source's own value
  when it sets one (`overrides` names those), else its platform's
  [download default](#download-defaults), else the default below.
  `defaults`: what it would have without any of its own (the form shows
  them as "Default: …"). A source stores only its `overrides`.
  - `full_history`: `false` (default): the first sync starts after the
    newest post FeedVault already has for the account, see below. `true`:
    the next sync walks the whole profile and downloads every post not in
    the folder yet (without `--fast-update`, the post, reels and tagged
    stamps dropped first); once it succeeds, it is set back to `false`.
  - `session`: `null` to use the global setting (see
    [instaloader settings](#instaloader-settings) and
    [gallery-dl and yt-dlp settings](#gallery-dl-and-yt-dlp-settings)), or
    one of the session values (gallery-dl and yt-dlp: none or cookies); a
    cookies one may name a [browser profile](#browser-profiles).
  - `content`: `null` (default: what `choices.content_default` names), or
    a non-empty list of what to fetch, from `choices.content`; sent in any
    order, stored in that order, and the default alone is stored as `null`.
    See [What a source downloads](#what-a-source-downloads).
  - `media`: `"all"` (default), `"images"` or `"videos"`; only `"all"`
    where `choices.media` is `false` (yt-dlp).
  - `since`: `null` (default), or a date `YYYY-MM-DD` from `1970-01-01` to
    today: nothing posted before it (UTC) is downloaded.
  - `first_posts`: `null` (default), or a whole number from 1 to 10000: the
    first sync gets only that many of the newest posts instead of the full
    history. Only where `choices.first_posts` (gallery-dl and yt-dlp:
    instaloader cannot stop after a number of a profile's posts), not with
    `full_history` (a 400: one or the other), and once a sync of the
    source has worked (`synced`) it cannot be set (while syncs have only
    failed or been cancelled, it can still be set or change). Set
    back to `null` once a sync succeeds, like `full_history`, and `since`
    set to the day of the oldest post that sync added or listed (see
    [What a source downloads](#what-a-source-downloads)).
  - `metadata`: `null` (default: the tool's setting, see
    [Metadata modes](#metadata-modes)), or one of `choices.metadata`:
    `"full"`, `"slim"` (instaloader only) or `"none"`. `metadata.mode` is
    the one its syncs use, `metadata.inherited` the one it would have
    without its own, `metadata.setting` the tool's.
  - `schedule`: `"off"` (default), `"hourly"`, `"daily"` or `"weekly"`: how
    often the scheduler syncs the source (see [Schedules](#schedules)).
    Stories last 24 hours: options whose `content` turns `stories` on (it
    was off) make an `"off"` schedule `"daily"`, unless they send a
    `schedule` too. A source stored without one (sources.json from before)
    has `"off"`.
  - Any other key, or a value outside the above, is a 400 naming the
    option; a stored value that is not valid (sources.json edited by hand)
    counts as its default, the other options as stored.
  - Stories, highlights and tagged posts on Instagram (`choices.login`)
    need a logged-in session: asking for them when the session the sync
    would use (the source's, else the tool's setting) is `none` is a 400
    (`Stories need a logged-in session: …`), and a sync of a source that
    asks for them with no session (the setting changed since) is refused
    with the same message.
- `choices`: what this source's options can be, for the form: `content`
  (empty when the link itself picks what is downloaded: a gallery-dl link
  that is not the profile's own page, any yt-dlp link), `content_default`,
  `login` (those of `content` that need a logged-in session), whether
  `media`, `since` and `first_posts` can be set, and the `metadata` modes
  of its tool.
- `last_sync_at`: when the last sync ended (any outcome), or `null`.
- `synced`: whether a sync of it has worked (`health.ok_at` set), or it
  synced before outcomes were stored (`last_sync_at` set, `last_result`
  with no `state`): then `first_posts` and its default no longer apply.
- `last_result`: how it ended, or `null` before the first sync:
  - `state`: the job's (`done`, `failed`, `cancelled`, `interrupted`)
  - `error`: `null` when it worked, else what the output says went wrong:
    `login_required` (the site wants a logged-in session),
    `private` (a private profile the session in use does not follow; when
    the sync used no session or cookies, `message` says so and points to
    Settings → Sync instead: "Private profile and no login in use: …"),
    `not_found` (no such profile: renamed or deleted),
    `rate_limited` (HTTP 429, "Please wait a few minutes"), or `generic`.
    An HTTP 403 counts as `login_required`: it is how Instagram turns away
    an anonymous client, after which instaloader reports the profile as
    missing. gallery-dl and yt-dlp failures are classified from their
    error lines the same way (gallery-dl's `AuthRequired`, `NotFoundError`,
    "Tweets are protected"; yt-dlp's "Sign in to confirm", "Private video",
    "Video unavailable"). `missing`: the tool was not found (no output).
    `setup`: the tool's install lacks something it was asked to use:
    instaloader's `browser_cookie3 library is needed to load cookies from
    browsers` (`--load-cookies` without its optional `browser_cookie3`
    module); `message` then names the command that adds it, from how
    instaloader is installed (see `browser_cookie3` in
    [Downloaders](#downloaders)), and `health.state` is `error`: the site
    was never asked. The dashboard links `missing` and `setup` to
    Settings → Downloaders and
    `login_required` to Settings → Sync, where logins and cookies are set
  - `message`: one line for people; `line`: the tool's last line of output
    behind it, or `null`. When the latest-version check is on (see
    [Downloaders](#downloaders)) and the tool is older than PyPI's latest
    release, a failed sync's message ends with
    `. yt-dlp 2026.01.01 is out of date (2026.08.06 is out): update it in
    Settings → Downloaders`, and `outdated` is `true` (else `false`)
  - `added`: new posts indexed (also after a failure: what was downloaded
    before it stopped is indexed)
  - `failures`: failed syncs in a row (a failure adds one, a sync that
    worked sets it to 0, a cancelled or interrupted one leaves it), for the
    scheduler's back-off
  - `blocking`: `not_found` or `login_required` results in a row
    (whichever; a cancelled or interrupted sync leaves it, any other
    result removes it; after a resume, see [Schedules](#schedules), it
    counts from none), for the scheduler's stop; one stored before it
    with such a state counts as 1
  - `health` and `ok_at`: see `health` below (stored here, with the
    sync's outcome, not with the options)
  - `line` and `message` are scrubbed before they are stored: see `health.line`
- `health`: how the source's syncs have been going (account health), read
  from `last_result` and `last_sync_at`; every key `null` (`failures` 0)
  before the first sync, and for a `last_result` that is missing or not
  what it should be (sources.json edited by hand, or stored before this):
  - `state`: what the last sync's output said: `ok` (it worked), `error`
    (it failed and the output says nothing known: `line` is what it said),
    or a detected state (see [Account health](#account-health)). A
    cancelled or interrupted sync keeps the state before it. A
    `last_result` stored before this has its `error` read instead.
  - `result`: the last sync's job state (`done`, `failed`, `cancelled`,
    `interrupted`)
  - `ok_at`: when the last sync that worked ended (kept while later ones
    fail); `last_sync_at`: when the last one ended, any outcome
  - `line`: the output line behind a state other than `ok`, one line, at
    most 300 characters, **scrubbed** before it is stored (in the job's
    `result` and `message` too): escape codes and control characters are
    removed; the values of cookies, session ids, tokens, passwords and
    `Authorization` headers, a `Cookie:` header's whole value and any
    opaque string of 40 characters or more become `…`; a path under a
    browser profile or a session or cookie folder becomes
    `<private path>`, whole, browser folder names with a space in them
    (`Application Support`, `User Data`, `Profile 1`…) included. Tool output is untrusted text: the page shows it as
    text only.
  - `failures`: as `last_result.failures`
  - `paused`: `"account not found"` or `"login required"` while the
    scheduler no longer syncs it (see [Schedules](#schedules)), else `null`
  - `warning`: why the Creators list warns about it: what stops the
    scheduler (as `paused`, also when its schedule is off) or `"<n> failed
    syncs in a row"` (n of 3 or more), else `null`. A lone `not_found` or
    `login_required` only backs off (the scheduler tries again), so it
    shows as the state's badge, not as a warning
  - `rename`: `{ "from": "old.name", "to": "new.name", "at": 1727503600 }`
    when the tool reported that the profile `target` names is now called
    `to` (instaloader only, see [Account health](#account-health)), else
    `null`. Stored as `last_result.rename`; shown while `from` is still the
    target. FeedVault never renames on its own: the user accepts it
    (`POST /api/sources/<id>/rename`) or dismisses it.
  - `login`: the session the last sync used, as its output told
    (`last_result.login`): `mode` (`none`, `cookies`, `login`), `found`
    (the session file or the browser's cookies were there) and `accepted`
    (the site took them), each `true`, `false` or `null` when the output
    did not say; `null` before a sync. Never from a request of FeedVault's.
- `job`: the source's sync while it is queued or running (`waits_until`,
  see [Jobs](#jobs)), else `null`.
- `session`: the session its sync would use (`options.session`, else the
  tool's setting), for the form's login hint. A saved login
  (`"mode": "login"`) has `session_file`: whether instaloader's session
  file for that user exists (only looked for, never opened).
- `schedule`: its schedule, see [Schedules](#schedules): `every` (as
  `options.schedule`), `next_at` (UTC seconds; in the past, or `0`, when it
  is due; `null` when `every` is `"off"`), `paused` (`schedules_paused` is
  on), `skipped` (why the scheduler did not queue it when it was due, or
  `null`), `stopped` (`"paused: account not found"` or `"paused: login
  required"` while the scheduler no longer syncs it, `next_at` then
  `null`; else `null`), `failures` (as `last_result.failures`, `0` when none).
- `script_warning`: `null`, or why its script (`options.script`) would
  fail its next sync, read from the scripts folder now: `{ "state":
  "refused", "reason": "greet.sh is refused: writable by group or others
  (chmod go-w …)" }`; `state` is `refused` (the file, or the scripts
  folder), `missing` (no such script now) or `other_tool` (it runs
  another tool than the source's, see [Scripts](#scripts) "On a source").

| Method | Path | Returns |
|---|---|---|
| GET | `/api/sources` | `{ "sources": [source, …], "suggestions": [suggestion, …] }`, sources by target |
| GET | `/api/sources/resolve?url=…` | what adding that link would make, shown before saving: `{ "ok": true, "tool": "yt-dlp", "platform": "tiktok", "target": "https://tiktok.com/@someone", "folder": "/archive/tiktok/someone", "source": null, "no_sync": false, "choices": {…}, "defaults": {…}, "metadata": {…}, "session": { "mode": "none" } }` (`source`: the id of the source already there for it; `no_sync`: that source, or the account the profile's folder or handle belongs to, is set to [Don't sync](#dont-sync), so it can be neither added nor synced; `choices`: as a source's; `defaults`: the options a new source starts from, its platform's [download defaults](#download-defaults); `metadata`: as a source's; `session`: the one a new source uses). `{ "ok": false, "error" }` (still a 200: it answers the question) for a link that is not accepted. With `&tool=instaloader`, `url` is a profile name or `@name` instead |
| POST | `/api/sources` | body `{ "target": "…", "tool": "…", "folder": "/abs", "person": 3, "account": { "platform", "id" }, "options": {…} }` → `{ "ok": true, "source": {…} }` (an option left out, or sent as its platform's default, is not stored: it follows the default); 409 `{ "ok": false, "error", "no_sync": true }` when its account (given, or found from its folder or handle) or its person is set to [Don't sync](#dont-sync), from the dashboard and another site alike; 400 for an unknown `person`, for a `folder` that is not the account's own (a media root, a platform's folder, one holding other accounts' posts), and for an `options.script` that does not exist, is refused or runs another tool than the source's; 403 for an `options.script`, or an `options.session` other than `{ "mode": "none" }` (a [browser profile](#browser-profiles) included), set from another site (see [Security rules](#security-rules)) |
| GET | `/api/sources/<id>` | source, or 404 |
| POST | `/api/sources/<id>` | body `{ "options": {…}, "reset": true \| ["media", …] }` (an option sent with another value than it has now becomes its own, even one equal to its default; one sent as it is now, or not sent, stays as it was, set or inherited; `reset`: those options, or all with `true`, follow the defaults again, before `options` applies), and/or `"person": id \| null`: the person it is added to, or none. A source shows with its account's person first, so one tied to an account takes it along: linked to that person (taken from anyone else), or with `null` unlinked from the person it showed with (an account unlinked from someone set to [Don't sync](#dont-sync) keeps the switch); `person` alone is taken while its sync runs, and with `options` it is written only when they are → `{ "ok": true, "source": {…} }`; 400 (`person` not an id, or no such person); 400 `{ "ok": false, "error" }` naming what is refused; 409 while its sync is queued or running (its end sets `full_history` and `first_posts` back), unless only `schedule` is sent or reset; 403 from another site (see [Security rules](#security-rules)) |
| POST | `/api/source-defaults/apply` | body `{ "tool": "gallery-dl", "platform": "twitter", "option": "media" }` → `{ "ok": true, "applied": 3, "skipped": 1 }`: every source of that tool and platform that sets that option itself stops doing so and follows the [download default](#download-defaults); those syncing now are skipped (but for `schedule`). 400 for another option than the defaults'; 403 from another site |
| DELETE | `/api/sources/<id>` | → `{ "ok": true }`: the source is forgotten; its folder, files and posts stay. 409 while its sync is queued or running; 403 from another site |
| POST | `/api/sources/<id>/rename` | body `{ "to": "new.name" }` (the suggested name, as `health.rename.to`) → `{ "ok": true, "source": {…} }`: the target becomes `to` and the suggestion goes; the folder, its files and the posts stay where they are. 400 when there is no suggestion or `to` is not it; 409 while its sync is queued or running, or when another source of that tool has that target, or the source's target changed meanwhile; 403 from another site |
| DELETE | `/api/sources/<id>/rename` | → `{ "ok": true, "source": {…} }`: the suggestion is forgotten (a later sync that reports it again brings it back); 403 from another site |
| POST | `/api/sources/<id>/sync` | → `{ "ok": true, "job": {…} }` (already `failed`, with the reason, for a script that cannot run, see [Scripts](#scripts) "On a source"); 409 when its sync is already queued or running; 409 `{ "ok": false, "error", "no_sync": true }` when it is set to [Don't sync](#dont-sync); 400 when it cannot be synced (its folder is no longer inside a media root); 403 when the source runs a script or would sync with a login and the request comes from another site; 404 for an unknown id |
| POST | `/api/sources/sync-all` | → `{ "ok": true, "jobs": [job, …], "skipped": 1, "errors": [{ "source": 5, "error": "…" }], "not_synced": 2 }`: a sync per source, by target, queued one after another; sources already queued or running are skipped, those set to [Don't sync](#dont-sync) left out (counted in `not_synced`), and those that cannot be synced (folder no longer inside a media root) listed in `errors`; 403 from another site |
| GET | `/api/no-sync` | `{ "people": [3], "accounts": [{ "platform": "instagram", "id": "123456" }] }`: who [Don't sync](#dont-sync) is on for, as set; 403 from another site |
| POST | `/api/no-sync` | body `{ "person": 3, "no_sync": true }` or `{ "account": { "platform", "id" }, "no_sync": false }`, and `"confirm": true` once asked → `{ "ok": true, "no_sync": { "people": [3], "accounts": [] } }`. Turning it on for one whose sources would stop syncing is 409 `{ "ok": false, "confirm": true, "sources": 2, "error" }` until sent again with `"confirm": true`; 409 while one of those sources' sync is queued or running; 400 for an unknown person, an account not indexed, or a malformed body; 403 from another site. See [Don't sync](#dont-sync) |

Every `/api/sources/<id>…` call answers 404 `{ "ok": false, "error": "no
such source" }` for an unknown id.

- `target`: a profile link. Its host picks the tool from the routing
  table; `tool` is optional, and when sent must be that tool (else a 400
  naming it). With `"tool": "instaloader"`, a profile name or `@name` works
  too. An Instagram link (`https://www.instagram.com/name/`, with or without
  `www.`, a query string or a trailing slash) gives instaloader the profile
  name. Anything else (a post or reel link, a name with other characters, a
  host not in the table) is a 400.
- `folder`: optional. Absent: `<first media root>/<platform>/<name>`,
  `<first media root>/instagram/<target>` for instaloader; for a link, the
  name is its first path part that is not a page kind (`/user/`,
  `/media`, `/en/`…), lowercase, without `@`. Sources made before this
  default keep the folder they have (`<first media root>/<target>` for
  instaloader). Present: an absolute path
  that resolves inside a media root (symlinks followed); anything else is
  a 400. Never a media root's `_saved/` or a folder inside it (where
  [Save](#save-from-the-browser-userscript) keeps posts of accounts with no
  folder), nor a link to it: a 400 that says to pick another folder, also
  when the default is there (`<root>/instagram` a link into `_saved`).
  `GET /api/sources/resolve` answers `ok: false` for it, and a sync of a
  stored source whose folder is there (sources.json edited by hand) is
  refused with the same message.
- The folder is the account's own, as its syncs write into it. A 400 that
  names the default folder instead: for a media root itself; for a
  platform's or a tool's folder right under a media root
  (`<root>/instagram`, `<root>/gallery-dl`, `<root>/youtube`…, a name from
  the routing table's platforms or a tool's); and for a folder
  that holds another account's own folder, itself included (`… holds other
  accounts' posts (name, …)`). An account's own folder, as for
  suggestions below: posts right in it, most of them that account's, most
  of the posts under it that account's, and no folder under it whose posts
  are 90% or more another account's.
- `person` (an id) and `account` are optional. With `account`, the source
  belongs to that indexed account (unknown: 400); without, to the account of
  the folder's posts when there are some, else to the one account of that
  platform whose handle, current or old, is the profile name (the link's,
  or instaloader's target), any case: its posts are in another folder, and
  the first sync does not download them again. For instaloader, only an
  account its posts' metadata names (a numeric id), not one known from file
  names alone.
- A second source for the same tool and target is a 400, the target
  compared in any case, and so is one for the same tool and folder (an
  x.com and a twitter.com link to one profile).
- The request never carries flags, paths to run or a command: the sync job
  takes the source id only and builds everything from the stored source.

**Suggestions.** Profile folders that already hold instaloader posts but no
source (nor are inside a source's folder), for the user to confirm
(`POST /api/sources` with the suggestion's `tool`, `target`, `folder` and
`account`). FeedVault never creates a source on its own. One per account:
its own folder (see `folder` above) wherever it is
(`<root>/instagram/<name>`, a new source's, or `<root>/<name>`, an
instaloader source's before), the one with the most of its posts, and the topmost when they nest
(highlights in a subfolder). A folder that holds other accounts' folders
(a platform's folder with one per account) is never suggested, nor
`_saved/` (its posts are left out). `count` is that account's instaloader
posts in the folder only.

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
time), params `{ "source": "<id>" }` (plus `"scheduled": "1"` when the
scheduler queued it), nothing from the request. The argument list
comes from the stored source:

```
instaloader --latest-stamps <data_directory>/instaloader/stamps.ini [--fast-update]
            --dirname-pattern <folder> --filename-pattern <pattern>
            --title-pattern {date_utc}_UTC_{typename} [--sanitize-paths] [content flags] [session flags] -- <target>
```

(content flags: see [What a source downloads](#what-a-source-downloads);
`--sanitize-paths` only when the folder is on exFAT, FAT or NTFS, where the
`:` in tagged posts' and highlights' names is not allowed)

- **Incremental.** `--latest-stamps` keeps, per profile, the time of the
  newest post downloaded, in FeedVault's data directory, not next to the
  media: instaloader stops at it whatever files exist (trashed posts are
  not downloaded again), and skips the files that already exist one by one.
- **`--fast-update` only without a stamp.** It stops at the first post
  whose files exist, so with a stamp it would stop at a post saved on its
  own (the userscript's Save) newer than the stamp, and the posts between
  them would never be fetched. It is passed only when `stamps.ini` has no
  entry for the target (a first sync that could not be seeded, and not
  with `full_history`, with reels, without posts, with `media: videos`, or
  with a `since` floor, which sets the stamp); decided from that file when the job is queued and
  again right before it starts, after seeding (the job's `argv` shows what
  ran).
- **Saved posts join the folder.** Right before each sync, the posts of
  the source's account (its account, else the one account whose handle,
  any it had, is the target) that the Save button put in `_saved/` move
  into the source's folder, renamed as the sync names its files, so the
  sync finds them and does not download them again. A post moves whole or
  not at all, never over a file already there (it then stays in `_saved/`).
  Both folders are indexed again: the posts keep their ids, so their
  `first_seen`, tags, decisions and collections stay with them. A source
  with no account when its sync starts gets them after that sync too, once
  it has one (its folder indexed, see [Sources](#sources)); one that got
  its account outside a sync (a post saved into its folder, a rescan) gets
  them right before its next sync, as above. The job log says how many
  moved.
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
  says `first sync: no reliable date, fetching full history`). Posts saved
  one by one never seed it: those the Save button added, whatever folder
  they went to (listed in `saved_posts`, see
  [Save from the browser](#save-from-the-browser-userscript)),
  and any post in `_saved/` or just moved out of it. When they are all the
  account has, the stamp is set before every post (1970), so the first
  sync walks the whole profile, skipping the files already there, and a
  retry does the same.
- **Metadata on.** instaloader writes each post's JSON beside its media,
  compressed (`.json.xz`; syncs before this asked for plain `.json`, and a
  folder may hold both), so new posts get captions, stats and the
  account's numeric id. In the `slim` [metadata mode](#metadata-modes) the
  files a sync wrote are slimmed when it ends, see
  [instaloader metadata](#instaloader-metadata); in `none` it writes none.
  The folder's name becomes an alias of that id (see [People](#people)),
  so old filename-only posts and new ones are one account.
- **Names like the folder's.** `--filename-pattern` is detected from the
  folder's files when the sync is queued, so new files sit beside the old
  ones and a post already there is recognised: `{target}-{date_utc:%Y-%m-%d}-{shortcode}`
  (the common filename-only layout, and the default for an empty or unclear
  folder), `{target} - {shortcode}` (the older layout), or
  `{date_utc}_UTC` (instaloader's own default, in folders it wrote with
  metadata). A folder path holding `{` or `}` is escaped for instaloader.
- **Session**: none (default), or `--load-cookies <browser>` (with
  `--cookiefile <file>` for a [browser profile](#browser-profiles)), or
  `--login <user>`; see [instaloader settings](#instaloader-settings).
- **Pause.** After an `instaloader-sync` job ends, the next one waits the
  configured pause (default 60 s) before it starts, so several profiles
  never hit Instagram back to back. A queued job that waits says until when
  in `waits_until`.
- **Result.** The source folder is indexed when the job ends (exit code 0
  or not, but not when cancelled): `result` `{ "added", "updated", "error",
  "line", "account", "person", "login" }`, plus `seen` (the `first_seen`
  range of the posts it added), `rename`, `outdated`, `muted` and
  `notification` when they apply, `message` `"3 new posts"`, or for a failure the plain-language
  message of `error`. The outcome is stored on the source (`last_result`).
  `account` (`{ "platform", "id" }`: the source's account, else that of its
  folder's posts, else `null`) and `person` (an id or `null`) are for the
  dashboard's toast to link to: the Feed's new posts of that account, or the
  person's page for a failure.

### instaloader settings

`GET /api/config` has `"instaloader": { "session": {…}, "pause": 60,
"metadata": "full" }`; `POST /api/config` with `{ "instaloader": {
"session": {…}, "pause": 60, "metadata": "slim" } }` (any key may be left
out; any other key there is refused) changes them. `metadata`: see
[Metadata modes](#metadata-modes).
A refused value answers `{ "ok": false, "error" }` with a 200, as every
`POST /api/config` error does.

| `session` | Flags | What it means |
|---|---|---|
| `{ "mode": "none" }` (default) | none | Anonymous: public profiles only, and Instagram rate-limits sooner |
| `{ "mode": "cookies", "browser": "firefox" }` | `--load-cookies firefox` | instaloader reads that browser's Instagram cookies itself, from the profile it picks. `browser`: `firefox`, `chrome`, `chromium`, `brave`, `edge` |
| `{ "mode": "cookies", "browser": "firefox", "profile": "test" }` | `--load-cookies firefox --cookiefile <profile folder>/cookies.sqlite` | The same from one [browser profile](#browser-profiles); `Cookies` instead of `cookies.sqlite` for the other browsers |
| `{ "mode": "login", "user": "name" }` (1 to 30 of `A-Z a-z 0-9 . _`) | `--login name` | instaloader uses the session file it saved after a `instaloader --login name` run in a terminal. Without one, the sync fails (`login_required`); it never asks for a password |

FeedVault only passes on the browser's name, the path of the browser
profile named, or the user name. It never stores, opens, reads or sends
cookies, passwords or session files. `pause`: whole seconds from 0 to 3600.

### Browser profiles

A cookies session (the three tools' settings, and a source's own
`options.session`) may name which of the browser's profiles the cookies
come from: `"profile"`, left out or `null` for the profile the tool picks
itself (the most recently used one, in general).

| Method | Path | Returns |
|---|---|---|
| GET | `/api/browsers/profiles` | `{ "profiles": { "firefox": ["default", "test"], "chrome": ["Default", "Profile 1"], "chromium": [], "brave": [], "edge": [] } }`: per browser, the names `"profile"` can take on this machine, sorted; names only, no path |

- **Firefox**: the profile's name as Firefox shows it (`about:profiles`),
  which is `Name=` of a `[ProfileN]` section of its `profiles.ini`. The
  folder comes from that section's `Path` (under the `profiles.ini`'s folder
  when `IsRelative=1`), never from a folder's name: `xxxxxxxx.test` may be
  there twice, one a leftover no profile uses. Looked for in
  `$XDG_CONFIG_HOME/mozilla/firefox`, `~/.mozilla/firefox`, the Flatpak's
  (`~/.var/app/org.mozilla.firefox/…`) and the Snap's
  (`~/snap/firefox/common/.mozilla/firefox`).
- **chrome, chromium, brave, edge**: the profile's folder name in the
  browser's folder under `$XDG_CONFIG_HOME` (`Default`, `Profile 1`):
  letters, digits, spaces, `.`, `_` and `-`. Listed when the folder holds a
  `Preferences` file.
- A name is 1 to 100 characters, with no `/`, `\` or control character, no
  leading `-` and no space at either end; anything else is refused where
  the session is saved.
- The name is resolved to its folder each time a command is built (a sync,
  a save, a tool's test), and the tool is given the folder:
  `--cookies-from-browser firefox:/home/me/.mozilla/firefox/9k879us5.test`
  for gallery-dl and yt-dlp (both read a profile holding a `/` as a path),
  `--cookiefile <folder>/cookies.sqlite` for instaloader. The folder goes
  to the tool only: a job's `argv`, as shown and stored, has
  `<browser profile>` in its place (`firefox:<browser profile>`,
  `--cookiefile <browser profile>`), for every job kind.
- A profile that is no longer there (renamed or removed in the browser, an
  unreadable `profiles.ini`, a folder that is gone) refuses the job with a
  message naming it (`the firefox profile "test" was not found …`): a 400
  for a sync or a test asked by hand, a skipped run for a scheduled sync.
  FeedVault never falls back to another profile. Refused too: a name two
  Firefox installations both have, and a folder whose path holds `::`, `$`,
  `*`, `?`, `[` or a control character (the tools would read those as something else).
- FeedVault reads `profiles.ini` (names and paths) and which folders exist.
  It never opens a cookie database: the tool does.
- The dashboard only: `GET /api/browsers/profiles` answers 403 to another
  site, as does `POST /api/sources` with a `"profile"` in `options.session`
  (see [Security rules](#security-rules)).

### gallery-dl and yt-dlp syncs

Job kinds `gallery-dl-sync` (group `gallery-dl`) and `yt-dlp-sync` (group
`yt-dlp`), params `{ "source": "<id>" }` (plus `"scheduled": "1"` from the
scheduler), nothing from the request. The source must
have that tool; its target is checked again (a normalized https link whose
host is still in the routing table; a source keeps its tool when the table
later routes the host to another one) and so is its folder
(inside a media root, without `$`, which both tools would expand):

```
gallery-dl [--config-ignore] --write-metadata --download-archive <data_directory>/gallery-dl/archive.sqlite3
           -o skip=abort:5 [content flags] -D <folder> [--cookies-from-browser <browser>] -- <link>

yt-dlp [--ignore-config] --write-info-json --write-thumbnail --download-archive <data_directory>/yt-dlp/archive.txt
       [--break-on-existing] [content flags] -o <folder>/%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s
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
  extractor's archive format), yt-dlp a line `<platform> <id>` for every
  post. The job log says how many. The archive formats are read from the
  installed gallery-dl: its own Python runs a short script listing each
  extractor's `archive_fmt` (argv only, no shell, in an empty folder, 20 s
  at most, output checked: plain `{field}` and `{field[key]}` formats
  only; a category gets one format for all its files only when every
  extractor of it was accepted), cached until the gallery-dl file changes
  and read once when FeedVault starts if a gallery-dl source exists or
  gallery-dl posts are indexed (else by the first seed). Trashing a gallery-dl post uses the last formats
  read (it never starts gallery-dl itself), else FeedVault's table. When that cannot be
  done (no Python found beside it, a timeout, odd output), FeedVault's own
  table is used (twitter, tiktok, instagram, reddit, bluesky, pixiv) and
  the log says so. Files of a category with no known format, or whose
  metadata lacks a key the format needs, are not seeded, and the log says
  how many and that this sync may download them again. With `options.full_history` too (the profile is
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
  `result` with the same keys as instaloader's, stored on the source.
  A non-zero exit whose error lines are all about single items (yt-dlp's
  `ERROR: [youtube] <id>: Private video`, against `[youtube:tab]` or
  `[tiktok:user]` for the profile; gallery-dl's `[download][error] Failed
  to download …`), and are not a rate limit or a login wall, is `done`:
  `message` says how many items could not be downloaded, `line` the last
  of them.
- **Pause.** The next sync of the same tool waits the tool's `pause`
  (default 30 s); see [gallery-dl and yt-dlp settings](#gallery-dl-and-yt-dlp-settings).

### What a source downloads

A source's `options` (see [Sources](#sources)) say what its syncs fetch.
What each tool and platform offers (`choices`):

| Tool | Platform | `content` (default first) | `media` | `since` | `first_posts` |
|---|---|---|---|---|---|
| instaloader | instagram | `posts`, `reels`, `stories`, `highlights`, `tagged` | yes | yes | no |
| gallery-dl | instagram | `posts`, `reels`, `stories`, `highlights`, `tagged` | yes | yes | yes |
| gallery-dl | twitter | `timeline`, `media`, `tweets`, `with_replies` | yes | yes | yes |
| gallery-dl | bluesky | `media`, `posts`, `replies`, `video` | yes | yes | yes |
| gallery-dl | tiktok | `posts`, `reposts`, `stories` | yes | yes | yes |
| gallery-dl | any other | none | yes | yes | yes |
| yt-dlp | any | none | no | yes | yes |

gallery-dl offers `content` only for a link to the profile's own page
(`instagram.com/<name>`, `x.com/<name>`, `bsky.app/profile/<name>`,
`tiktok.com/@<name>`); a link to one of its pages (`x.com/<name>/media`)
already picks what is downloaded. yt-dlp downloads what the link lists (a
YouTube channel's tab, for instance).

The flags each option becomes (the date `2024-01-01` as an example; `N`
the number of posts):

| Option | instaloader | gallery-dl | yt-dlp |
|---|---|---|---|
| `content` | `--reels`, `--stories`, `--highlights`, `--tagged`; `--no-posts` without `posts` | `-o include=<kinds>` (`with_replies` is `with-replies`), only when not the default | — |
| `media: images` | `--no-videos --no-video-thumbnails --post-filter "not is_video"` | `--filter "extension in exts_image"` | — |
| `media: videos` | `--no-pictures` (file by file: a carousel keeps its videos; an image post leaves only its metadata, which is not indexed), and `--storyitem-filter "is_video"` with stories or highlights (story items ignore `--no-pictures`). Never with `--fast-update`, which instaloader refuses with it | `--filter "extension in exts_video"` (file by file) | — |
| `since` | `--post-filter "date_utc >= datetime(2024, 1, 1)"`, and the stamps (below) | X: `--date-after 2023-12-31T23:59:59` (stops at the first older post); others: `--filter "(not date or date >= datetime(2024, 1, 1))"` | `--dateafter 20240101`, plus `--break-match-filters "upload_date >=? 20240101"` where the listing is newest first (not TikTok, not a YouTube channel's own page), with `full_history` too |
| `first_posts` | — | `--post-range 1-N` (N per kind of `content`: each is its own extractor) | `--playlist-items 1:N` |

instaloader's filter terms are joined with `and` into one `--post-filter`,
also given as `--storyitem-filter` with stories or highlights (both have
`is_video` and `date_utc`; `is_video` is a story-item term only); gallery-dl's into one `--filter`. Both tools
evaluate those as Python: they are built from fixed text and the three
numbers of a date checked as above, never from text a request sent, and a
stored value that is not valid counts as the default (no flag).

- **Stopping points.** instaloader keeps a stamp per kind in
  `stamps.ini`: `post-timestamp`, `reels-timestamp`, `tagged-timestamp`,
  `story-timestamp` (highlights have none: each is walked, the files there
  skipped). A kind turned on later has no stamp yet, so its first sync
  walks it all. `--fast-update` is only for a first sync of posts (see
  below) and never with reels: reels are walked first, and a reel on the
  grid is the same file, so the posts would stop at it.
- **The floor and the stamps.** Right before an instaloader sync, after
  the first-sync seed, each stamp of posts, reels and tagged posts the
  source fetches that is missing or older than `since` is set to just
  before it, so the walk stops there; the filter drops anything older that
  still comes through (a pinned post). A newer stamp stays. Lowering
  `since` later, or widening `media`, does not bring back what earlier
  syncs walked past: `full_history` does (back to `since`, when set).
  gallery-dl and yt-dlp skip what their archive lists (only what was
  downloaded is in it), so for them too only `full_history` walks back.
- **Last N.** gallery-dl and yt-dlp seed their archive first as usual, so
  posts already indexed are skipped within those N. yt-dlp applies it at
  each level: a YouTube channel's own page gets N per tab; TikTok's pinned
  videos (listed first) count among them. gallery-dl applies it per kind
  of `content`. `first_posts` is set back to `null` once a sync succeeds,
  and `since` becomes the day (UTC) of the oldest post that sync added or
  its archive skipped as already there (the N newest may all be indexed
  already: nothing added), today at the latest, unless it is later already.
  A sync that did neither keeps `first_posts` for the next run: the archive alone
  would not keep the next sync from going on to older posts (gallery-dl
  stops only at 5 files in a row it has; TikTok and a YouTube channel's
  page are walked to the end).
- The job log says when a floor or "last N" applies.
- **`script`**: `null`, or the id of a [script](#scripts) the source's
  Sync runs instead of the flags above (they then do nothing: the script
  is the whole command). Set from FeedVault's own dashboard only (403
  otherwise), and only to a script that exists, is not refused and does
  not run another tool than the source's (400).
  A script that goes missing or is refused later fails the source's next
  sync, never falling back to the tool's command.

### Metadata modes

What each tool writes beside the media it downloads. One mode per tool in
its settings (`"metadata"`, default `"full"`), and a source's own in its
`options.metadata` (`null`: the tool's, or its platform's
[download default](#download-defaults)). Saves follow the mode of the
folder they go to. A change applies to new downloads only: the files
already there stay as they are (the "Slim existing metadata" job aside).

| Mode | instaloader | gallery-dl | yt-dlp |
|---|---|---|---|
| `full` (default) | each post's JSON (`.json.xz`) and caption (`.txt`) | `--write-metadata`: a JSON beside each file | `--write-info-json --write-thumbnail` |
| `slim` | as `full`, the JSON slimmed when the job ends ([instaloader metadata](#instaloader-metadata)) | — | — |
| `none` | `--no-metadata-json --no-captions`; new files named `{target}-{date_utc:%Y-%m-%d}-{shortcode}` whatever the folder held | no `--write-metadata`; `-f "{category} {retweet_id\|tweet_id\|post_id\|id} {num:>02}{date:? //%Y%m%d-%H%M%S}.{extension}"` | `--no-write-info-json --no-write-thumbnail --embed-metadata --embed-chapters --embed-thumbnail --no-embed-info-json --remux-video webm>mkv`; `-o "%(uploader_id)s-%(upload_date)s-%(id)s [%(extractor_key)s %(channel_id,uploader_id)s].%(ext)s"` (TikTok and others: `uploader_id` first) |

In `none`, posts are read from their file names (tool `"<tool>
(filenames)"`): the platform, the post's id and number, the date, and the
account (the name's, else the folder's). yt-dlp's title, description,
uploader name and link are read back from the video with ffprobe when it
is there (`ffprobe -show_entries format_tags`, 10 s at most per file). The
thumbnail is inside the video (webm is remuxed to mkv first, losslessly:
webm takes none); no thumbnail file is left. gallery-dl files get no exif:
writing it would need another tool. Only names of that exact shape are
read so: the category with its real id format (twitter, instagram and
tiktok digits, a bluesky TID, …), a two-digit number and the date;
categories without a reliable id format are not read from names.

yt-dlp embeds with ffmpeg, passed as `--ffmpeg-location` (the one
FeedVault finds, see [Tools](#tools)). With none found, a yt-dlp sync or
save in `none` writes the files as `full` instead, and its log says
`ffmpeg not found: …` (embedding would fail every video, which yt-dlp
then never records in its archive). `GET /api/config` has `"ffmpeg":
true|false`, and a yt-dlp source's `metadata.ffmpeg` the same (the other
tools have none), for the dashboard to warn.

A post first read from its names whose metadata file appears later
(another mode, a save) is the same post: indexed again with its
metadata, keeping its id and when it was first seen.

What `none` loses:

- instaloader: captions, hashtags, likes and comments counts, location,
  the account's numeric id (the folder's name is the account), and the
  story / highlight / tagged distinction (they read as posts).
- gallery-dl: captions, hashtags, counts, the author's display name, the
  "Retweeted by" note of a retweet (it is indexed as the original), and
  Instagram's post link.
- yt-dlp: likes, views and comments counts, the thumbnail file, and the
  title and description without ffprobe; YouTube videos longer than
  `youtube_max_seconds` are still not downloaded, but one already there is
  no longer recognised as too long when indexed.

What keeps working: gallery-dl's and yt-dlp's download archives (the tools
write them; a trashed post adds its yt-dlp line, and gallery-dl's entries
from the staged metadata of a save), instaloader's stamps, the account a
post belongs to, duplicates, the Trash and the save's gathering of files.

### Download defaults

The options a source does not set itself follow its platform's download
defaults: one block per tool and platform (gallery-dl × twitter, bluesky,
tiktok, instagram, …; instaloader × instagram; yt-dlp × youtube, tiktok,
…), in config.json as `"source_defaults": { "gallery-dl": { "twitter": {
"content": ["media"], "schedule": "daily" } } }`, only the options set.
Set once, every source of that platform that does not set an option
follows it from its next sync; one that does keeps its own. New sources
start from them (the form shows them filled in), and those added without
options (a person's profiles, a suggestion, the userscript) set nothing.
An option a source sets stays set until it is reset (`reset` in `POST
/api/sources/<id>`, or Reset and "Reset all to default" in its Options,
where picking "Default" resets too), even when the default comes to
equal it: a later default does not move it. Saving its Options writes
only the options changed there: one sent as it is now stays set or
inherited as it was, so a form opened before a default changed does not
pin the old one.

But from another site (the userscript): a source added from there does
not inherit a default `script`, nor any login: it gets `"script": null`
and `"session": { "mode": "none" }` as its own, whatever the defaults or
the tool's setting say then or later (the dashboard alone picks those,
see [Security rules](#security-rules)).

`GET /api/config` has `"source_defaults": [block, …]`, one per tool and
platform the routing table routes to, a source has, or defaults are set
for:

```json
{ "tool": "gallery-dl", "platform": "twitter", "choices": {…},
  "defaults": { "session": null, "content": ["media"], "media": "all", "since": null, "first_posts": null,
                "script": null, "metadata": null, "schedule": "daily" },
  "set": ["content", "schedule"], "sources": 41,
  "overrides": { "session": 0, "content": 3, "media": 0, "since": 1, "first_posts": 0, "script": 0,
                 "metadata": 0, "schedule": 2 } }
```

`choices` as a source's (for the platform's own page), `defaults` every
option (the built-in value where none is set), `set` those set, `sources`
how many sources it has, `overrides` how many of them set each option
themselves (`POST /api/source-defaults/apply` makes them follow).

`POST /api/config` with `{ "source_defaults": { tool: { platform: {
option: value } } } }` replaces the blocks sent (`null` or `{}` removes
one); the options are a source's but `full_history` (a one-off), checked
as a source's (a 200 `{ "ok": false, "error" }` for a value refused, a
script that does not exist or runs another tool, or content that needs a
login the tool's setting does not have). A value equal to the built-in one
is not kept. A `first_posts` default applies until a sync of the source
has worked (ended `done`; a failed or cancelled first one leaves it);
a `content` one to sources whose link does not pick what is downloaded. A
hand-edited value that is not valid is skipped.

Stored options from before were every option: those equal to the built-in
defaults then became inherited (migration 26), the others stay the
source's own, so nothing changed until a default is set.

### Account health

A source's `health` (see [Sources](#sources)) is kept with its sync state:
in `last_result`, written when a sync ends, so it goes into sources.json
with the rest of the outcome and a rebuilt index gets it back. Nothing is
fetched to know it: it is what the tool printed during the last sync.

`health.state`, read from the tool's output by fixed patterns (one table
per tool in `backend/health.py`, whose docstring lists the exact strings
and the tool version they were seen on). The first state found wins, in
this order:

| state | instaloader | gallery-dl | yt-dlp |
|---|---|---|---|
| `rate_limited` | `429 Too Many Requests`, `Please wait a few minutes` | `HttpError: '429 …'`, X `Rate limit exceeded` | `HTTP Error 429` |
| `private` | `Private but not followed`, `private but not followed` | `AuthorizationError: … Tweets are protected` | TikTok `This user's account is (likely either) private`, YouTube `Private video` |
| `login_required` | `Login required`, `requires login`, `Redirected to login page`, `Session file does not exist yet`, `Login error:`, `checkpoint_required`, `challenge_required`, `No cookies found for Instagram`, `Not logged in.`, `403 Forbidden` | `AuthRequired:`, `AuthenticationError:`, other `AuthorizationError:`, X `'Could not authenticate you`, TikTok `…: Login required to access this profile` | `Sign in to confirm you're not a bot`, `Sign in to confirm your age`, `TikTok is requiring login`, `Use --cookies-from-browser or --cookies for the authentication` |
| `not_found` | `Profile … does not exist.` | `NotFoundError:`, TikTok `…: User account could not be found` | `The channel/playlist does not exist`, `HTTP Error 404`, `Video unavailable`, TikTok `Video not available, status code <n>`, `YouTube said: This channel does not exist` / `This account has been terminated` |

Anything else is `error`, with its line: also instaloader's
`browser_cookie3 library is needed to load cookies from browsers`, which
says nothing of the account (the sync's `last_result.error` is `setup`, and
its `message` says how to add the module). A TikTok user that does not exist
only gives yt-dlp's `Unable to extract secondary user ID`, which a private
account can give too: it stays an `error`. gallery-dl's strings are
those of its 1.32.14 source.

`renamed`: the sync worked, and the tool said the profile now has another
name. Only instaloader says so: with `--latest-stamps` it keeps each
profile's id, and when the name is gone it looks the id up and prints
`Profile <old> has changed its name to <new>.` (it exits 1 for that line
alone; FeedVault counts that run as done). It moves the stamps to the new
name but not the files (that is only without `--latest-stamps`). The new
name is kept as `health.rename`, a suggestion: accepting it changes the
source's target only. Until then each sync finds the profile by its id
again. gallery-dl and yt-dlp print nothing that names a new handle: a
renamed X, TikTok or YouTube profile is `not_found` (or `error`).

`login` comes from the lines each tool prints about its session:
instaloader's `Loaded session from …` / `Cookies loaded successfully from
…` (found), `Session file does not exist yet` / `No cookies found for
Instagram` (missing), `Logged in as …` / `… has been successfully logged
in.` (accepted: instaloader checks the session itself before saying so),
`Not logged in.` / `Redirected to login page. You've been logged out`
(refused); gallery-dl's `[cookies][info] Extracted <n> cookies from …` and
yt-dlp's `Extracted <n> cookies from …` (found; 0 is missing), gallery-dl's
`cookies: Unable to find … cookies database`, yt-dlp's
`could not find … cookies database` / `failed to load cookies` (missing),
gallery-dl's X `'Could not authenticate you` (refused).
Then a sync that ended `login_required` had its session refused, and one
that ended otherwise (`ok`, `renamed`, `private`) with its session found
had it accepted. One that ended `not_found` says nothing of the session
(the site says it to a throttled or logged-out client too): only a line
above marks it accepted.

### Don't sync

Some accounts are never to be downloaded from. **Don't sync** is a switch
on a person (it covers each of their accounts) or on an account (whoever it
is linked to, its folder-name aliases included); off by default. While it
is on (on the account itself, or on its person):

- no source can be added for it: `POST /api/sources` answers 409 with
  `"no_sync": true`, from the dashboard and from another site (the
  userscript) alike, for an account given or found from the source's
  folder or handle, and for a source added to such a person;
- it gets no source suggestion (`GET /api/sources` `suggestions`);
- a source it already has is **kept**, never deleted, and marked
  `"no_sync": true`, but never runs: `POST /api/sources/<id>/sync` answers
  409 with `"no_sync": true`, Sync all and a person's Sync leave it out
  (`not_synced` counts them), the scheduler never queues it, and no sync
  job for it can be started any other way (`POST /api/jobs` with a sync
  kind answers 400);
- its posts stay in the library, in the Feed, Review, Storage and every
  page, exactly as before.

Turned on for one whose sources would stop syncing, `POST /api/no-sync`
asks first (409, `confirm`, how many) and acts once sent again with
`"confirm": true`; it is refused while one of those sources' sync is queued
or running. Turned off, its sources sync again as they did, schedules
included. Another site can neither read nor set it (it is not in the
userscript's routes, see [Security rules](#security-rules)).

A source counts as its account's, and as the person it shows under; one
added to a person set to Don't sync stays not synced even when its account
is linked to someone else. One not tied to an account yet counts as its
folder's account, else, its folder still empty, as the account with its
link's handle.

A person who leaves keeps nothing syncing that did not: when a person set
to Don't sync is deleted, or an account leaves them (unlinked, added to
someone else, or taken by a new person or a merge), that account gets the
switch of its own. Merging people keeps it on when any of them had it;
when that would stop sources that sync now, `POST /api/people/merge`
asks first (409 `{ "ok": false, "confirm": true, "sources": n, "error" }`)
and merges once sent again with `"confirm": true`.

Deliberate one-off actions that are not a source's sync stay allowed:
saving one post (`POST /api/save`) and running a script by hand with an
explicit link or target (`POST /api/scripts/<id>/run`). A script attached
to a source runs as that source's sync, and is refused as it is.

User data: `no_sync_people` (exported by person name, as `muted_people`;
deleting the person drops it) and `no_sync_accounts` (by platform and
account id, stored as the id it stands for), written to
`userdata/no_sync_people.json` and `userdata/no_sync_accounts.json`.

### Schedules

A source whose `options.schedule` is `"hourly"`, `"daily"` or `"weekly"`
is synced on its own by the scheduler, a thread of the backend that looks
every minute (the first time 20 s after startup). A scheduled sync is an
ordinary sync job (the tool's pause applies; a source already queued or
running is never queued again).

- When it is due (UTC): never synced, or its last sync was interrupted
  (FeedVault stopped): now. Else `last_sync_at` (the end of its last sync,
  whatever the outcome) plus the interval (1 h, 24 h, 7 days); after n
  failed syncs in a row (`last_result.failures`) the interval × 2^n, at most
  24 h and never less than the interval: a failing hourly source waits
  2 h, 4 h, 8 h, 16 h, then once a day, until a sync works.
- A time missed while FeedVault was off is only due: one sync at startup,
  not one per missed slot.
- At most one source per platform is queued at a time, the most overdue
  first, and only when no sync of that platform is queued or running and
  the scheduler queued the last one 5 minutes ago or more.
- A source whose syncs ended `not_found` or `login_required` (see
  [Account health](#account-health)) twice in a row
  (`last_result.blocking` ≥ 2), or once with a session the site accepted
  (`health.login.accepted` is `true`), is no longer synced on its own:
  trying again would not change that. `schedule.stopped` says so. A lone
  one with no accepted session is backed off like any failure (instaloader
  says "does not exist" and "403 Forbidden" to a throttled anonymous
  client too). It is
  scheduled again once a sync of it works (Sync clicked), or when its
  schedule or its session changes (for a source without a session of its
  own, also the tool's session in `POST /api/config`: Settings → Sync or
  Downloaders, which also starts again the count of one not stopped yet)
  or a rename is accepted (`last_result.resumed` is set until a sync of it
  ends `done` or `failed`, and the count starts again). Only a state read by these tables stops it: a `last_result`
  stored before them (an `error` only) keeps the back-off. `rate_limited` is not stopped: the back-off
  above applies.
- A due source is skipped, with `schedule.skipped` saying why, while its
  tool is not found (as [Downloaders](#downloaders) looks for it) or the
  media root holding its folder is offline (missing, or empty while posts
  are indexed under it); it is tried again a minute later. A sync refused
  (its folder no longer inside a media root) is noted the same way and
  tried again one interval later.
- A source set to [Don't sync](#dont-sync) is never queued: it keeps its
  `schedule`, which applies again once the switch is off;
  `schedule.next_at` is `null` meanwhile.
- `GET /api/config` has `"schedules_paused": false`; `POST /api/config`
  with `{ "schedules_paused": true }` (a boolean, else an error) pauses
  every schedule (Settings → Sync), the syncs already queued go on.
- With several [profiles](#profiles), only the active one's sources are
  looked at; `schedules_paused` is one setting for all of them.

### gallery-dl and yt-dlp settings

`GET /api/config` has `"gallery-dl": { "session": {…}, "pause": 30,
"ignore_config": false, "metadata": "full" }` and `"yt-dlp": { "session":
{…}, "pause": 30, "ignore_config": false, "metadata": "full" }`; `POST
/api/config` with either (`session`, `pause`, `ignore_config` and/or
`metadata`, see [Metadata modes](#metadata-modes)) changes it.

| `session` | Flags | What it means |
|---|---|---|
| `{ "mode": "none" }` (default) | none | Anonymous: public profiles only |
| `{ "mode": "cookies", "browser": "firefox" }` | `--cookies-from-browser firefox` | The tool reads that browser's cookies itself, from the profile it picks. `browser`: `firefox`, `chrome`, `chromium`, `brave`, `edge` |
| `{ "mode": "cookies", "browser": "firefox", "profile": "test" }` | `--cookies-from-browser firefox:<profile folder>` | The same from one [browser profile](#browser-profiles) |

FeedVault only passes on the browser's name and the path of the browser
profile named. It never stores, opens, reads or sends cookies.

`pause`: whole seconds from 0 to 3600 (default 30). As for instaloader,
after a `gallery-dl-sync` job ends the next one waits that long before it
starts (`waits_until`), and the same for `yt-dlp-sync`; one tool's pause
never holds the other's syncs.

`ignore_config`: `true` or `false` (default `false`). With `true`, the
tool's syncs and its Test skip the user's own config files
(`--config-ignore` for gallery-dl, `--ignore-config` for yt-dlp), so
options set there (another output folder, a different archive, cookies)
do not change what FeedVault runs. With `false`, a sync that wrote media
files no parser could read (changed since the tool started, in the
source's folder, with no metadata FeedVault knows beside them) says the
user's config is the likely cause at the end of its `message` (`…; 2 files
it wrote could not be read: your own gallery-dl config is the likely cause
…`); with `true`, only that they could not be read.

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
`apply` that is not a boolean, `409` while a job of the `yt-dlp` lock group runs (a sync, a TikTok save, a yt-dlp source's script sync, or a script running it) or another
check is under way. Settings counts first (`apply` false), then asks to
confirm.

## Save from the browser (userscript)

The userscript puts one small movable bubble on every page it runs on, its
menu holding what the page offers: **Save post** on a post's page (and a
Save on each grid tile linking to a post), **Add profile** and **Sync now**
on a profile page, on Instagram, X (x.com, twitter.com) and TikTok
(www.tiktok.com). A tile's
Save sends the same body as the post page's, built from the tile link's
`href` (read again at the click, held to the same whole-path pattern as a
post page, on the site's own host), and stops the click from opening the
post. What they can ask for is as narrow as it can be: a
checked id, or a post's link parsed down to its id, never a path, a flag or
a command.

On a person's page of a link-in-bio site (`linktr.ee`, `beacons.ai`,
`lnk.bio`, `solo.to`, `campsite.bio`, `linkin.bio`, `allmylinks.com`, `link.me`: the
bio import's sites, each with its own `@match`), the bubble's **Save
links** asks for a person (`GET /api/links/people`; recent ones
first, a search box, or "No person (Unsorted)") and sends the page's
outbound links as rendered, read from its `<a href>`s at the click
(`http`/`https`, no login part, none on the site's own hosts, each once, at
most 200), to `POST /api/links/import`; it says what came of it ("4 saved,
2 already there"). See [Save links from the browser](#save-links-from-the-browser).
On every page the script runs on (those sites and Instagram, X and
TikTok, nowhere else), the bubble's **Add this page to Links** and the
userscript manager's menu item **Add this page to FeedVault Links** send the
page's address and title, to no one, by `POST /api/links`.

**Who talks to FeedVault.** Only the userscript, through
`GM_xmlhttpRequest` (`@connect localhost`, `@connect 127.0.0.1`). The page
itself never does, and is given nothing to do it with: no function on
`unsafeWindow`, no `window.postMessage` handler, nothing read from the
page's JavaScript objects. The post id comes from `location.pathname` or
a post link's `href`, the profile name from `location.pathname`, each
checked against a strict pattern before it is sent; an X or TikTok post's
link is built again from the name and id it found, not taken from the page. The buttons act on a
real click only (`event.isTrusted`): the page's scripts can call
`element.click()` or dispatch a click on them, and that does nothing.

**Why a page cannot forge the request.** Every `/api` call needs the
`X-FeedVault` header and a `Host` naming this machine (see the top of this
file). A script on instagram.com (or any site) that calls
`http://localhost:3380/api/save` with that header makes the browser send a
CORS preflight first (`OPTIONS`, a custom header is not "simple"), and
FeedVault never answers one with `Access-Control-Allow-*`, so the browser
never sends the real request. Without the header, the request is a 403. A
DNS-rebinding page has the wrong `Host`: a 403. `GM_xmlhttpRequest` runs
in the extension, outside the page's origin, so CORS does not apply to it:
that is what lets the userscript, and only it, send the header.

**Where it comes from.** Settings › About links to
`/userscript/feedvault.user.js`, which serves `userscript/feedvault.user.js`
with `http://localhost:3380` replaced, in `API_BASE`, `@updateURL` and
`@downloadURL` only, by `http://localhost:<port>`, the port FeedVault
listens on (`FEEDVAULT_PORT`, 3380 by default; the demo's 3389). On 3380 it
is the file as written. The port never comes from the request: the `Host`
header, forwarding headers and the query are not read, so a request cannot
choose where an installed script sends its calls, and a `Host` not naming
this machine is a 403 as for any URL. The host stays `localhost`, and
`@connect` (localhost, 127.0.0.1) and `@match` are served as written.

| Method | Path | Returns |
|---|---|---|
| POST | `/api/save` | body `{ "platform": "instagram", "shortcode": "C8xYzAbCdEf" }` or `{ "url": "https://x.com/someone/status/1800000000000000001" }` → `{ "ok": true, "have": true, "post": { "id": "instagram:C8xYzAbCdEf", "path": "/p/instagram/C8xYzAbCdEf" } }` when FeedVault already has the post (nothing runs), else `{ "ok": true, "have": false, "job": {…}, "existing": false }` |

- Instagram: `platform` must be `"instagram"`; `shortcode` must match
  `^[A-Za-z0-9_-]{5,40}$` (ASCII only, the whole string).
- X and TikTok: `url` alone, a post's link, parsed as below. Any other key,
  value or type is a 400 (with why, for a link). Nothing else is read from
  the request.
- `existing`: `true` when a Save job for that post was already queued
  or running: that job is returned and no other is queued (a second click
  is not a second download).
- At most `save_queue_max` Save jobs (in `config.json` only, not in
  `/api/config`; default 20, 1 to 500), of
  every platform together, are queued or running at once; one more is a
  429 `{ "ok": false, "error": "20 posts are already waiting to be saved;
  try again once some are done" }`.
- `POST /api/jobs` refuses kinds `instaloader-post`, `gallery-dl-post` and
  `yt-dlp-post`: they are only started here, with the checks above.
- `POST /api/saved` and `have` answer for `twitter:<id>` and `tiktok:<id>`
  as for `instagram:<shortcode>` (`path` `/p/twitter/<id>`).
- **Add profile** and **Sync now** use the source endpoints as they are:
  `GET /api/sources/resolve` (`?tool=instaloader&url=<name>` on Instagram,
  `?url=https://x.com/<name>` or `?url=https://www.tiktok.com/@<name>`
  elsewhere: the tool, the target, the folder, and the source already
  there, if any) and `GET /api/sources/<id>` for that source. **Add
  profile** sends `POST /api/sources` with `{ "tool": "instaloader",
  "target": "<name>" }` or `{ "target": "<the profile's link>" }` (the
  routing table picks the tool) after the user confirms, and nothing else:
  it downloads nothing. **Sync now**, its own click, sends `POST
  /api/sources/<id>/sync`; its state is read from `GET /api/jobs/<id>`. A
  source with a script (`options.script`) is refused there with a 403 (see
  [Scripts](#scripts)): Sync now says it is synced from the dashboard and
  links to its person, else Creators.

**Post links (X and TikTok).** Parsed strictly, nothing fetched:

| | X | TikTok |
|---|---|---|
| Hosts (whole, any case) | `x.com`, `www.x.com`, `mobile.x.com`, `twitter.com`, `www.twitter.com`, `mobile.twitter.com` | `tiktok.com`, `www.tiktok.com`, `m.tiktok.com` |
| Path | `/<name>/status/<id>`, `/i/web/status/<id>`, `/i/status/<id>`, then optionally `/photo/<1-4>` or `/video/<1-4>`, a trailing `/` | `/@<name>/video/<id>`, a trailing `/` |
| Name | `[A-Za-z0-9_]{1,15}` (not used) | `[A-Za-z0-9._]{1,24}` |
| Tool, and the link it gets after `--` | gallery-dl, `https://x.com/i/web/status/<id>` | yt-dlp, `https://www.tiktok.com/@<name>/video/<id>` |

- `<id>`: ASCII digits, no leading zero, at most 20 (`^[1-9][0-9]{0,19}$`).
- `http` or `https`; no login part, no port but 80 or 443, at most 500
  characters, ASCII, no spaces or control characters. Query and fragment are
  dropped.
- Refused: any other host, a host that only ends with one of these
  (`x.com.evil.com`), and short links (`t.co`, `vm.tiktok.com`,
  `vt.tiktok.com`: only the site can resolve them, and FeedVault does not
  fetch them; the error says to open the post and save from there). TikTok
  photo posts (`/@<name>/photo/<id>`) are refused: yt-dlp downloads videos.
- X and TikTok always use these tools, whatever the link routing says for
  their hosts.

### How a save runs

Job kind `instaloader-post`, params `{ "shortcode": "…" }`, group
`instaloader` (never beside a sync), with the same pause as syncs. The
owner of a post is only known once instaloader has fetched it, so it
downloads into a folder of its own in the data directory first:

```
instaloader --dirname-pattern <data_directory>/instaloader/saving/<shortcode>
            --filename-pattern {shortcode} [session flags] -- -<shortcode>
```

- `-<shortcode>` is instaloader's target for one post; it comes after `--`,
  so it is never read as an option whatever it holds. The session flags
  are those of [instaloader settings](#instaloader-settings).
- Once it exits (not when cancelled), the post's JSON names its owner, and
  the files move to the owner's folder: the folder of the owner's
  instaloader source, else the folder holding most of the owner's
  instaloader posts (right under a media root, or right under its
  `instagram` folder), else `<first media root>/instagram/<handle>` when
  that folder exists, else `<first media root>/<handle>` when that one does
  (where instaloader sources went before), else
  `<first media root>/_saved`. They are named as that
  folder's files are, the layout a sync would detect (see
  [How a sync runs](#how-a-sync-runs)), the handle in place of the target.
  A file already there is never overwritten (the copy downloaded is
  dropped). Then that folder is indexed: a source of that folder with no
  account yet takes the owner's, and links it to its person when the
  account has none (see [Sources](#sources)), so `result.person` names them.
- A post this adds to the index (not one a sync got first) is listed in
  table `saved_posts` (`post_id`, `saved_at`), user data written to
  `<data_directory>/userdata/saved_posts.json` 2 s after the last change:
  saved one by one, it never seeds a first sync's stamp (see
  [How a sync runs](#how-a-sync-runs)), or a newer saved post would make
  that sync skip the posts in between. An entry is dropped once the
  account's stamp is later than its post (a sync has walked past it), or
  when the post is deleted for good. Saves made before this table existed
  are taken from the Save jobs still kept (the last 100 jobs).
- `result`: `{ "post": "instagram:C8xYzAbCdEf" | null, "folder", "added",
  "updated", "error", "line", "account", "person" }`: `post` the post's
  FeedVault id once it is indexed; `error` as for a sync (`login_required`,
  `private`, `not_found`, `rate_limited`, `setup`, `generic`; `missing` when
  instaloader is not found), with a message for people. The userscript
  says "see Settings → Downloaders" for `missing` and `login_required`.
- The saving folder is emptied before each run and removed after.

### How an X or TikTok save runs

Job kinds `gallery-dl-post` and `yt-dlp-post`, params `{ "platform":
"twitter" | "tiktok", "id": "<digits>", "handle": "<name>" (TikTok only) }`,
checked again when the job is built; groups `gallery-dl` and `yt-dlp`
(never beside a sync of the same tool), with the tool's pause. Each runs
as its tool's sync does (the tool's `ignore_config` and cookies settings),
into a folder of its own in the data directory, with the file names a sync
gives:

```
gallery-dl [--config-ignore] --write-metadata -D <data_directory>/gallery-dl/saving/twitter-<id>
           [--cookies-from-browser <browser>] -- https://x.com/i/web/status/<id>
yt-dlp [--ignore-config] --write-info-json --write-thumbnail --no-playlist
       -o <data_directory>/yt-dlp/saving/tiktok-<id>/%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s
       [--cookies-from-browser <browser>] -- https://www.tiktok.com/@<name>/video/<id>
```

- A data directory holding a `$` is refused (both tools expand `$NAME` in
  the folder they are given).
- After yt-dlp, the cookies it copies into the info JSON are removed, as
  after a sync.
- Once it exits (not when cancelled), the metadata names the owner, and the
  files move to: the folder of the owner's gallery-dl or yt-dlp source (by
  account, else by the name in its link), else the folder holding most of
  the owner's gallery-dl and yt-dlp posts, else `<first media
  root>/<platform>/<name>` (made: the folder a new source for the profile
  would download into), else `<first media root>/_saved`. A file already
  there is never overwritten. Then that folder is indexed, as for an
  Instagram save (a source of it with no account yet takes the owner's).
- The indexed post's entries go into the download archives as trashing
  would add them (gallery-dl's per file, yt-dlp's `<platform> <id>`
  line): a later sync of the profile skips it (with either tool for X;
  with yt-dlp for TikTok: a TikTok save is a yt-dlp post, which has no
  gallery-dl entry). Only a run that exits 0 moves or records anything.
- `result`: as for an Instagram save, plus `archived` (how many archive
  entries were new). A tweet with no media leaves no post: `generic`,
  "gallery-dl saved no post (a post with no media?)".
- The saving folder is emptied before each run and removed after.

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
  /api/trash`). It still takes disk space until the trash is emptied. Read
  from the manifests and the Trash list's last looks, never from the files
  (no `lstat` per trashed file): a file moved out of the trash by hand still
  counts until **Check for missing files**. The manifests are read once in
  the background at startup, and again only when one changes (a delete
  reads just the lines it added).
- `?person=<id>`: everything above but `trash` covers that person's posts
  only, filtered as `/api/posts?person=` does, so `totals` equals
  `/api/posts/summary?person=`.

### Sizes

Every size is the `size` the scanner recorded for a media file (the photo or
video itself), and media items marked missing are skipped. Poster images,
thumbnails, metadata JSON, caption and other side files are not counted, so
deleting a post frees a little more than its `bytes`, and the trash total
counts every trashed file by the size it had when it was trashed.

`/api/storage`, `/api/authors` and `/api/posts/summary` are computed in SQL
from the index, never by walking the media folders (the trash total comes
from the trash manifests). Their answers are cached until anything in the index changes
(a scan, a delete, a restore, a review decision, a tag change).

## instaloader metadata

instaloader writes Instagram's whole answer beside each post
(`<base>.json.xz`, or plain `<base>.json` from `--no-compress-json`) and a
`<handle>_<id>.json[.xz]` per profile: tens of kilobytes each, most of it
nothing FeedVault reads. FeedVault reads both forms, and a folder may hold
both. A post with both files is one post, read from the `.json.xz`; the
`.json` is one of its files (trashed, restored and moved with it).

The **slim form** of such a file is the same structure with only this left
(`parsers/instaloader.py`, `slim`):

- `instaloader` (`version`, `node_type`), whole;
- of a Post's or a StoryItem's `node`: `__typename`, `id`, `shortcode`,
  `code`, `product_type`, `date`, `taken_at_timestamp`, `video_view_count`,
  `video_play_count`; `owner` (`id`, `pk`, `username`, `full_name`); the
  caption (`edge_media_to_caption.edges[0].node.text`, `caption.text` or
  `caption`); the `count` of `edge_media_preview_like`, `edge_liked_by`,
  `edge_media_to_comment` and `edge_media_to_parent_comment`;
  `location.name`; of `iphone_struct`: `user` (`id`, `pk`, `username`,
  `full_name`), `caption.text`, `taken_at`, `like_count`, `comment_count`,
  `play_count`, `view_count`;
- of a Profile's `node`: `id`, `username`, `biography`, `external_url`,
  each `bio_links[].url`;
- besides, every value right in `node` that is a number, `true`, `false`,
  `null`, or a text of at most 200 characters that is not a link
  (`is_video`, `accessibility_caption`, `full_name`, …).

Everything else is removed: picture and video URLs (`display_url`,
`display_resources`, `video_url`, `thumbnail_*`), `edge_sidecar_to_children`
(a carousel's items), `edge_media_to_tagged_user`, comment previews and
`edge_media_to_comment.edges`, `dimensions`, `media_preview`, the rest of
`owner` and of `iphone_struct` (`image_versions2`, `video_versions`,
`carousel_media`, …), a Profile's `edge_*` lists and pictures. That is the
list of what the parser reads: a slim file parses to exactly the post the
whole one did. A structure the parser makes no post or profile of (a
Hashtag, an iterator's state, a node with no post id) is left as it is. A
slim file is written xz-compressed, a few hundred bytes.

What is removed cannot be put back: for a post Instagram no longer has, it
is gone.

- **New files.** instaloader's `slim` [metadata mode](#metadata-modes)
  (the setting, `"instaloader": { "metadata": "slim" }`, or a source's
  own): the instaloader metadata a sync, a save or an instaloader
  source's script wrote is slimmed when the download ends,
  before the folder is indexed. The job's log says `N files of metadata
  slimmed, … freed`; a file that could not be rewritten gets a log line
  (`could not slim <path>: …`) and does not change how the job ended. In
  `full`, new files are compressed and whole. It was a switch before,
  `slim_metadata` (config): `true` reads as `slim`, and the next
  `POST /api/config` writes it as the mode and drops the old key. Sent
  in `POST /api/config`, it is a 400 naming `instaloader.metadata`.
- **The files on disk.** Job kind `slim-metadata`, group `instaloader`
  (never while instaloader runs), started with `POST /api/jobs`. `mode`
  `preview` changes nothing; `apply` rewrites every instaloader metadata
  file under the media roots that is not slim and compressed already: a
  `.json` becomes a `.json.xz`, a `.json.xz` is replaced. Both end `done`
  with `result`:

```json
{ "mode": "preview", "checked": 1204, "clean": 4, "files": 1200, "bytes_before": 48211004,
  "bytes_after": 811200, "failures": 0, "failed": [], "stopped": false }
```

`checked`: instaloader files read; `clean`: slim and compressed already;
`files`: to rewrite (`preview`) or rewritten (`apply`); `bytes_before` and
`bytes_after`: the size of those files and what they take slim, exact in
both modes (a preview slims and compresses each in memory); `failed`: the
first 20 `{ path, error }`, `failures` how many in all (each is in the
job's log); `stopped`: cancelled, or FeedVault quit, before the end.
`message`: `1200 files to slim: 46.0 MB now, 792.2 KB after`, `1200 files
slimmed, 45.2 MB freed; 2 files failed and were left as they were`. `400`
with no media root.

One file at a time: the slim form goes to a temporary file in the same
folder (`.<name>.<random>.feedvault-slim.tmp`) with the original's mode,
times and, when FeedVault may set it, owner, and is fsynced; that file is
read back from the disk and must hold the slim form and parse to the same
post; it is then renamed over the `.json.xz`, or put beside the `.json`
under the name `.json.xz` (never over a file that is there); only then is
the `.json` removed. A failure at any step leaves the original as it was,
is counted, and the job goes on. A file that changed while it was being
rewritten is left. The index follows as files change (`meta_path` of posts
and copies, a profile's source, dismissed duplicates naming a copy), and
each folder is indexed again: no rescan is needed.

Cancelled or killed, every file is either done or as it was. Running it
again skips what is done, removes a temporary file a kill left behind, and
finishes a post left with both files (the `.json` goes once the `.json.xz`
beside it reads as the same post; a `.json.xz` that reads as anything else
is a failure, and both stay).

Only regular files in the folders a scan reads (no dot-folder, so not the
trash; a symlink is neither followed nor rewritten) whose content is
instaloader's are touched: gallery-dl's and yt-dlp's JSONs,
`_comments.json`, captions and media never are.

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
  running): set on quit (`ended_at` is then when it stopped), or on the
  next start after a crash (`ended_at` is then `null`: when it stopped is
  unknown).
- `argv`: for display. The first item is the tool's name; the path actually
  run is resolved when the job starts (see [Tools](#tools)). A kind that is
  FeedVault's own work (`slim-metadata`) runs no process: its first item is
  `feedvault`, its `exit_code` stays `null`, and cancelling it stops it
  after the file it is on, with its `result` so far.
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
  Only the next job of its group has one: those queued behind it start
  after it, at a time not known yet, and have `null`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/jobs` | `{ "running": 1, "queued": 0, "jobs": [job, …], "sync_all": batch, "new": 12, "new_until": 1727500000, "unsorted_links": 3, "notifications": { "unread": 1, "latest": 42, "desktop": false } }`: queued and running jobs and the last 100 ended ones, newest first; `sync_all` see [Sync all](#sync-all); `new` the number of new posts and `new_until` the newest one's `first_seen` (or `null`), see [New posts](#new-posts); `unsorted_links` the number of links tied to no one, see [Links](#links); `notifications` the unread entries and the newest id, see [Notifications](#notifications); for the sidebar, which polls this |
| GET | `/api/jobs/kinds` | `[{ "kind": "tool-version", "label": "…", "params": { "tool": { "type": "choice", "choices": ["instaloader", "gallery-dl", "yt-dlp", "ffmpeg"] } } }]` |
| POST | `/api/jobs` | body `{ "kind": "tool-version", "params": { "tool": "yt-dlp" } }` → `{ "ok": true, "job": {…} }`; 400 `{ "ok": false, "error": "…" }`, also for a body that is not an object, a `kind` that is not text, and a kind another route starts (the saves: `POST /api/save`; `script`, `script-sync`: a script's Run or a source's Sync), and a sync kind for a source set to [Don't sync](#dont-sync) |
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
| `instaloader-sync` | `source`: a source id; `scheduled`: `"1"` when the scheduler queued it (optional, see [Notifications](#notifications)) | instaloader for that source, see [Sources](#how-a-sync-runs); `result` `{ "added", "updated", "error", "line", "account", "person", "login" }`, plus `seen`, `rename`, `outdated`, `muted` and `notification` when they apply (`seen`: the `first_seen` range of the posts it added; `notification`: its entry's id), see [How a sync runs](#how-a-sync-runs) | `instaloader` |
| `gallery-dl-sync` | `source`, `scheduled` as above | gallery-dl for that source, see [gallery-dl and yt-dlp syncs](#gallery-dl-and-yt-dlp-syncs); `result` as above | `gallery-dl` |
| `tool-test` | `tool`: `instaloader`, `gallery-dl` or `yt-dlp` | the tool once on a fixed public item, see [Downloaders](#downloaders); `result` `{ "ok", "error", "line" }` | the tool's name |
| `tool-update` | `tool`: `instaloader`, `gallery-dl` or `yt-dlp` | pip or pipx, picked from how the tool is installed, see [Downloaders](#downloaders) | the tool's name |
| `yt-dlp-sync` | `source`, `scheduled` as above | yt-dlp for that source, see [gallery-dl and yt-dlp syncs](#gallery-dl-and-yt-dlp-syncs); `result` as above | `yt-dlp` |
| `instaloader-post` | `shortcode` | instaloader for one post, see [How a save runs](#how-a-save-runs); started by `POST /api/save` only | `instaloader` |
| `gallery-dl-post` | `platform` (`twitter`), `id`, `handle` (optional); the same params as `yt-dlp-post` | gallery-dl for one X post, see [How an X or TikTok save runs](#how-an-x-or-tiktok-save-runs); started by `POST /api/save` only | `gallery-dl` |
| `yt-dlp-post` | `platform` (`tiktok`), `id`, `handle` (optional) | yt-dlp for one TikTok video, as above | `yt-dlp` |
| `slim-metadata` | `mode`: `preview` or `apply` | no tool: FeedVault's own work, see [instaloader metadata](#instaloader-metadata); `argv` is `["feedvault", "slim-metadata", mode]`, `exit_code` `null` | `instaloader` |
| `script` | `script`, `target`, `url`, `folder`, `sha256` | a script on its own, see [Scripts](#scripts); started by `POST /api/scripts/<id>/run` only | the tool's name for a downloader's command, else `scripts` |
| `script-sync` | `source`, `script`, `target`, `sha256` or `why`, `scheduled` | a source's script instead of its tool's command, see [Scripts](#scripts); started by the source's Sync (or the scheduler) only; `result` as a sync's; one with `why` is `failed` at once, never queued | the source's tool |

### Sync all

`sync_all` in `GET /api/jobs` is the last `POST /api/sources/sync-all` (or
`POST /api/people/<id>/sync`, which goes through the same batch) that queued
anything since FeedVault started, or `null`. Muted sources count in
`ended`, but not in `failed`, `added`, `profiles` or `first`. One sent while the
last is still running adds its jobs to it (one batch, one summary):

```json
{ "id": 51, "started_at": 1727500000, "total": 6, "ended": 2, "failed": 0, "cancelled": 0,
  "added": 31, "profiles": 2, "first": { "label": "Sync @somebody", "source": 4 },
  "current": job, "jobs": [51, 52, 53, 54, 55, 56], "active": [53, 54, 55, 56], "done": false }
```

- `id`: its first job's id; with `started_at`, what tells two batches apart.
- `ended`: its jobs that are no longer queued or running, whatever their
  state; `failed` those that failed; `cancelled` those cancelled (Stop,
  a job's Cancel, a source gone), which never synced, muted ones too;
  `added` the new posts they indexed;
  `profiles` how many of them added at least one, and `first` (label and
  source id) the one that added the most, or `null`.
- `current`: the job running, else the next queued one, else `null`;
  `jobs`: the ids of its jobs; `active`: those still queued or running;
  `done`: none is. The dashboard toasts one summary when a batch it saw
  running is done ("31 new posts from 6 profiles"), not one per job.

It is kept in memory only and built from live jobs: a restart (which ends
every queued job, see `interrupted`) forgets it, so the dashboard never
shows syncs that a restored or rebuilt database no longer has. A sync job
whose source no longer exists when it starts (removed, or the database was
replaced, including when its id now names another profile) ends `cancelled` with the message `source <id> no longer exists
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

Every line is **scrubbed** as it is read, before it is kept, shown or
stored (live, in the kept tail, and in the job's `message`), as
`health.line` is (see [Sources](#sources)): cookie, session id, token and
password values, a `Cookie:` header's value, `Authorization` values and
opaque strings of 40 characters or more become `…`, a path under a browser
profile or a session or cookie folder `<private path>`, escape codes and
control characters go and runs of spaces become one. One line stays one
line (an empty one stays, empty), so the numbering is the tool's. Only
FeedVault's own parsing of a run's outcome sees the output as printed; it
is never stored.

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
signalled: the process is pinned (a pidfd) when it is checked, and each
signal goes only while that same process is alive. The server log says
which processes were stopped (and which pids are another program's now),
and the job's own log gets a line (`process <pid> was still running after
FeedVault stopped: stopped at the next start`). Linux only: without
`/proc`, nothing is looked for or signalled. The job is then
`interrupted`.

Jobs are kept in the database (`jobs` table), not with the user data: they
are not exported to `userdata/`.

### Tools

`instaloader`, `gallery-dl`, `yt-dlp` and `ffmpeg` are found on `PATH` (its
absolute folders only: an empty or relative entry, `.`, is skipped, so a
tool is never looked up in a job's working folder), or at the path set for
them in Settings (`tools` in `config.json`, for a tool
installed in a virtualenv). `POST /api/config` with `{ "tools": { "yt-dlp":
"/abs/path" } }` sets one (an empty string clears it, back to `PATH`); the
other tools are left as they are. A path (`~` expanded, made absolute) must
be an executable file whose name starts with the tool's (`yt-dlp`,
`yt-dlp_linux`); the file and its
folder (the one it is in, and where it leads when it is a symlink) must be
root's or yours and not writable by group or others, and every folder above
those root's or yours and not writable by group or others unless sticky
(`/tmp`), so nobody else can swap the program FeedVault runs; anything else
is refused. This is checked
again each time the tool is looked for: a set path that stops working, or
that someone else could swap by now, makes jobs fail with the reason
("not found at the path set in Settings", "the path set in Settings is
refused: …") rather than fall back to `PATH`.

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
  "path_error": null,
  "install": "pipx",
  "venv": "/home/me/.local/share/pipx/venvs/yt-dlp",
  "version": "2026.08.06",
  "version_error": null,
  "latest": { "version": "2026.09.20", "error": null, "checked_at": 1727500000 },
  "outdated": true,
  "login": { "mode": "cookies", "browser": "firefox" },
  "update": { "possible": true, "command": "pipx upgrade yt-dlp", "reason": null },
  "install_hint": null,
  "browser_cookie3": null
}
```

- `path`: the executable a job would run (the path set in Settings, else the
  first on `PATH`); `null` when there is none, or the path set no longer
  works or is refused (see [Tools](#tools)). `real_path`: where it really is
  when `path` is a symlink, else `null`.
- `configured`: the path set in Settings, or `null`.
- `path_error`: why the path set in Settings is not used (shown in
  Settings instead of "not found"), else `null`.
- `install`, read from `real_path`: `venv` (in the `bin/` folder of a
  virtualenv: `pyvenv.cfg` beside that folder), `pipx` (the same, the
  virtualenv inside pipx's `venvs` folder: `$PIPX_HOME/venvs`, else
  `$XDG_DATA_HOME/pipx/venvs` (`~/.local/share/pipx/venvs`) or
  `~/.local/pipx/venvs`), `system` (anything
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
  `{ "mode": "cookies", "browser": "firefox" }` (with `"profile"` when a
  [browser profile](#browser-profiles) is set), or for instaloader
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
  (`pipx install <name>`, `sudo apt install ffmpeg`; for instaloader
  `pipx install 'instaloader[browser_cookie3]'`, with the extra that reads
  a browser's cookies), shown with a copy button and never run; else `null`.
- `browser_cookie3`: for instaloader when it is found, else `null`:
  `{ "found": true | false | null, "command": "pipx inject instaloader browser_cookie3" }`.
  instaloader reads a browser's cookies (`--load-cookies`) with this
  optional module; without it such a run fails at once. `found`: whether it
  is in the virtualenv's `lib/python*/site-packages` (a folder or dist-info
  whose name starts with `browser_cookie3`; looked for, nothing is run or
  imported); `null` when that cannot be told: a `system` install, or a
  virtualenv with `include-system-site-packages = true` that has none of
  its own. `command`: what adds it, shown with a copy button and never run:
  `pipx inject <the pipx virtualenv's name> browser_cookie3` (`pipx`),
  `<venv>/bin/python -m pip install browser_cookie3` (`venv`), and for a
  `system` install, which FeedVault cannot add it to, instaloader's
  `install_hint` (a copy that has it, its path then set in Settings). The
  card warns only when `found` is `false` and `login.mode` is `cookies`.

**Latest versions.** Off by default: `POST /api/config` with
`{ "check_updates": true }` turns it on. Besides a
[link-in-bio import](#link-in-bio-import) the user asks for, this is the
only request the server itself makes to the network. For instaloader, gallery-dl and yt-dlp
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
            [--load-cookies <browser> [--cookiefile <file>] | --login <user>] -- instagram
gallery-dl [--config-ignore] --simulate [--cookies-from-browser <browser>[:<profile folder>]] -- https://x.com/jack/status/20
yt-dlp [--ignore-config] --simulate --no-playlist [--cookies-from-browser <browser>[:<profile folder>]] -- https://www.youtube.com/watch?v=jNQXAC9IVRw
```

`<file>` and `<profile folder>` are there when the session names a
[browser profile](#browser-profiles).

instaloader fetches the profile's metadata and nothing else; gallery-dl
and yt-dlp download nothing (`--simulate`). `state` `done`, `result`
`{ "ok": true, "error": null, "line": null }`, `message` `"Works"`; or
`failed`, `result` `{ "ok": false, "error", "line" }` with `error` read from
the output as for a sync (`login_required`, `rate_limited`, `private`,
`not_found`, `setup`, `generic`) and `message` saying what it means (for
`setup`, the command that adds instaloader's `browser_cookie3`).

**Update.** Job kind `tool-update`, params `{ "tool": "instaloader" |
"gallery-dl" | "yt-dlp" }` and nothing else, group: the tool's name, so an
update never runs during a sync of that tool, nor a sync during its update.
When it is queued, the tool is found again (not from the cache) and the
command picked from how it is installed:

| `install` | Runs |
|---|---|
| `venv` | `<venv>/bin/python -m pip install --no-input --disable-pip-version-check -U <name>` |
| `pipx` | `pipx upgrade <name>` (pipx found on `PATH`) |
| `system`, `missing` | nothing: 400, the error says the command to run instead (the tool's `install_hint`) |

`<name>` is the tool's PyPI name, fixed in code. Refused the same way: a
virtualenv without its `bin/python`, or without pip in its
`lib/python*/site-packages` (one made by uv; no command is offered then),
a pipx virtualenv named otherwise than the package (`pipx install
--suffix`: the command shown upgrades it by its own name), or a pipx
install with no `pipx` on `PATH`. ffmpeg is not a choice: it comes from the system's packages. Once
the job has ended, the tool is found again, so `GET /api/downloaders` shows
its new version.

## Scripts

Your own download commands and shell scripts. They are **files on disk
only**: they are written and edited in a text editor in
`<config dir>/scripts/`, the folder of `config.json`
(`$XDG_CONFIG_HOME/feedvault/scripts/`, by default
`~/.config/feedvault/scripts/`, or beside `FEEDVAULT_CONFIG`). The API
lists them, shows them read-only and runs them. No request writes,
renames or deletes a script, and none ever carries a command. The folder
is read again on every request, so an edit counts right away.

A script's **id** is its file name without the suffix (`my-insta.json` is
`my-insta`). Built-in templates are `builtin:<name>`, read-only. They can
be run as they are, or copied into a file.

**Command**, `<id>.json`:

```json
{ "name": "instaloader, no videos", "description": "…", "needs": "target", "rescan": "{root}",
  "argv": ["instaloader", "--no-videos", "--latest-stamps", "{archive}",
           "--dirname-pattern", "{root}", "--", "{target}"] }
```

- `argv[0]`: `instaloader`, `gallery-dl`, `yt-dlp` or `ffmpeg`, found as
  every job finds them (see [Tools](#tools)), or an absolute path to a
  program. Any other name is refused.
- The placeholders `{target}`, `{url}`, `{root}`, `{data_dir}` and
  `{archive}` are replaced inside the element they are in. Every other
  `{…}` stays as it is (instaloader's `{profile}`). The list is run as it
  is: a value is never split, joined into a shell string or read as an
  option. In an option the tool formats, a value is escaped as the tool's
  own sync does: instaloader's patterns, yt-dlp `-o` and `--exec` (`%`),
  gallery-dl `-f`, `-N`, `--Print`, `--print-to-file`, `--rename` (`{`, `}`).
  `--print-to-file` / `--Print-to-file` FILE: only what lands after its
  last `/` (the file name gallery-dl formats); its folder is left as it is.
  A run is refused (400) when a value puts a `$` in that folder (gallery-dl
  expands `$NAME` there) or `\f` in the file name. This holds for a
  gallery-dl named by its path, run behind a launcher (`env`, `nice` …) or by Python too (as
  for the lock group, below).
  So is a `{url}` or `{target}` putting a `..` or leading `~` in that folder, or a `..` in a path option (`-D`, `-o`, env's `-C`…), or a leading `~` or a `$` in one where the tool expands them (gallery-dl, yt-dlp).
- `needs`: `target`, `url` or `none`. A script that uses `{target}` or
  `{url}` without needing it is refused.
- `rescan`: a folder template indexed once it has run (it must be inside
  a media root), or `null`.
- `name` (optional, the id by default), `description` (optional).

**Shell script**, `<id>.sh`, executable (`chmod +x`), its first line
`#!/absolute/interpreter`, then a header of comment lines:

```sh
#!/bin/sh
# name: Upper-case a link
# needs: url
# rescan: {root}/inbox
echo "$FV_URL" | while read -r l; do echo "got: $l"; done
```

It runs from the exact bytes whose SHA-256 was checked right before it
starts, never from its file again (nor as `sh -c` of its text): they are
put in a sealed memfd, and its `#!` interpreter (read as the kernel reads
it: the path, then at most one argument, the rest of the line) runs
`/dev/fd/N`. So `$0` is `/dev/fd/N`, not the file's path; `FV_SCRIPT` is
the file's path. Its inputs are only environment variables: `FV_TARGET`,
`FV_URL`, `FV_ROOT`, `FV_DATA_DIR` and `FV_ARCHIVE` (plus `FV_SCRIPT`, its path). The rest of its environment is minimal:
`PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`, `USER`, `LOGNAME`,
`TMPDIR` and the `XDG_*_HOME` folders (`PATH` the system default when
FeedVault has none), plus `PYTHONUNBUFFERED=1`, which every job gets. The
header keys are `name`,
`description`, `needs` and `rescan`; `needs` is required.

**Values:**

- `{root}`: the source's folder for a source's script. On its own, the
  `folder` sent (inside a media root), else the first media root.
- `{data_dir}`: the data directory.
- `{archive}`: instaloader's stamps file (`--latest-stamps`), gallery-dl's
  or yt-dlp's download archive (the ones syncs use), else
  `<data_dir>/scripts/<id>.archive`.
- `url`: an `http(s)://` link of at most 500 characters, without spaces
  or control characters.
- `target`, by the program run:
  - instaloader: a profile name or a post's shortcode;
  - gallery-dl and yt-dlp (by name, path, behind a launcher or run by Python): a link, as for `url`;
  - a shell script, or a command that runs a shell or `echo` / `printf`
    (the only other programs a placeholder may reach, see Refused below):
    any text of 1 to 200 characters, without control characters.

  It never starts with `-`. It reaches the program as one literal
  argument or env value.

**Refused**, listed with `refused` saying why and never run (a mode
refusal ends with the command that fixes it, such as
`(chmod go-w '/home/me/.config/feedvault')`). The listing makes the folder
(and the config folder) `0700` when it is not there yet, whatever the
umask; on start FeedVault runs `chmod go-w` on its scripts folder, and on
its config folder when that is the default `~/.config/feedvault`, when
they are yours and writable by group or others (a umask of `002`), and
logs it. It never changes a folder above them.

- the folder itself when it is a symlink, someone else's, or writable by
  group or others, or when a folder above it, up to `/` (along its path as
  written and as resolved), is neither root's nor yours, or is writable by
  group or others without being sticky (`/tmp`);
- a file that is a symlink, not a regular file, someone else's, writable
  by group or others, over 64 KiB, or named otherwise than
  `[a-z0-9_-]{1,64}` + `.json` / `.sh` (anything else in the folder is
  listed too);
- two files with the same id;
- a file that is not UTF-8, or changed while it was read;
- a command whose JSON is malformed: not an object, an unknown key, an
  `argv` that is not 1 to 200 strings (each at most 4096 characters, no
  NUL), an `argv[0]` that is neither a tool's name nor an absolute path
  without a placeholder, a `name` over 100 characters or on several lines,
  a `description` over 500, or `needs` other than `target`, `url` or
  `none`;
- a command with a placeholder where it would be read as code: in the
  value of yt-dlp `--exec`, `--exec-before-download`, `--netrc-cmd`,
  `--use-postprocessor` (a shell), `--downloader-args` or
  `--postprocessor-args` (split into aria2c's or ffmpeg's arguments), or
  anywhere beside `--alias`; of gallery-dl `--exec`, `--exec-after`,
  `-o` / `--option` and `-O` / `--postprocessor-option` (either can set an
  exec post processor's command), `--filter`, `--post-filter`,
  `--child-filter`, `--file-filter`, `--image-filter` or `--chapter-filter`
  (Python); of yt-dlp also `--external-downloader-args` and `--ppa`; of
  instaloader `--post-filter`, `--only-if` and `--storyitem-filter` (Python
  it evaluates). The
  downloader may be named by its path, run behind a launcher (`env`, `nice`, `timeout` …) or by Python
  (`python3 -m yt_dlp`; see the lock group below for every form read); joined
  (`--exec=…`, `-o…`) and abbreviated forms count; after `--` nothing is
  an option (yt-dlp's optparse and gallery-dl's and instaloader's
  argparse read it so), there or in a run's escaping and path checks. Also a shell's `-c` text
  (`sh`, `bash`, …: pass the value after it, as `"$1"`) and `env -S`
  (`-iS` and other clusters, `--split-string=…` too). Use
  the tool's own fields (`%(webpage_url)q`, `{_path}`), or a shell script
  and its `FV_*` variables;
- a command with a placeholder that a program FeedVault does not read
  could get (#103). Every program on the way to the one that reads the
  arguments must be one FeedVault parses, and that one a downloader (its
  options checked as above), a shell (its `-c` text checked as above) or
  `echo` / `printf`:
  - launchers are read with their options as coreutils 9.4 and
    util-linux 2.39.3 parse them, one inside another: `env`, `nice`,
    `nohup`, `timeout`, `stdbuf`, `ionice`, `taskset`, by their bare name
    or a path in `/bin`, `/usr/bin`, `/usr/local/bin`, `/sbin` or
    `/usr/sbin` (a program of yours under one of these names is not one;
    with a placeholder, nor is a path through `..`, which the kernel reads
    past a symlink).
    A placeholder may not be in their own items: options, `timeout`'s
    duration, `taskset`'s mask, `env`'s `NAME=value` (a program can read
    a variable as code: `LD_PRELOAD`, `BASH_ENV`) and `env -C` (the folder
    relative names, configs such as yt-dlp's `yt-dlp.conf` and Python's
    modules are found in). One run so that it runs no program (`--help`,
    `ionice -p`, `taskset -p`), with an option it does not have, or `env
    -S`: refused with a placeholder anywhere;
  - a shell (`sh`, `bash`, `dash`, `zsh`, `ksh`, `mksh`, `ash`) and
    Python (`python3`, `python3.12`, `pypy3` …) count, as launchers, only
    by their bare name or a path in those folders (#105): `/home/me/bin/sh
    -c … {url}`, `env /home/me/bin/bash -s {url}` and
    `/opt/venv/bin/python -m yt_dlp {url}` are refused, the reason naming
    the path (run the venv's `yt-dlp` itself instead: a downloader counts
    by any path). Such a path is also followed through its symlinks, even
    though its own name is known, and must end at a program of the same
    kind: `/bin/sh` → `dash`, `/bin/ksh` → `ksh93`, `/usr/bin/python3` →
    `python3.12` or `python3.13t` count, and so does a `sh` or `ash`
    linked to `busybox` (it runs as `ash` by that name); a `sh` linked to
    `perl` does not. A bare name is not followed: the job's `PATH` decides.
    A shell linked to another is read with the options of both (a `dash`
    linked to `bash` takes `-O`'s value);
  - a placeholder never names the program to run (`argv[0]`, the item a
    launcher runs, a shell's script file when it has no `-c` or `-s`,
    what Python runs), nor is among Python's options (`-W` imports a
    module);
  - any other program is refused with a placeholder anywhere in `argv`,
    and the reason names it: an interpreter (`python3 -c`, `python3
    script.py`, `perl`, `ruby`, `node`, `awk`, `php`, `lua`, `Rscript`,
    `fish`), a program that runs another one FeedVault does not follow
    (`xargs`, `sudo`, `doas`, `su`, `runuser`, `ssh`, `watch`, `script`,
    `parallel`, `find -exec`, `chrt`, `flock`, `setsid`: a job leads its
    own process group, so `setsid` forks and exits at once, its program out
    of the job's reach), or any other program, by its path or behind a
    launcher. `echo` and `printf` count by their bare name or a path in
    those folders, as launchers; `printf` takes one after its format,
    never in it. Use a shell script and its `FV_*` variables, or pass the
    value to a shell after its `-c` text (`sh -c 'perl x.pl "$1"' sh
    {url}`);
- a gallery-dl format string starting with `\f` (Python, a template file)
  that holds a placeholder: `-f` / `--filename`, `--rename`, `--rename-to`,
  and `-N` / `--print`, `--Print`, `--print-to-file`, `--Print-to-file`
  (the FORMAT after `EVENT:`, and FILE's name). Plain format strings stay;
- a shell script that is not executable, has no absolute `#!` or has no
  `needs`.

A script saved before a check that refuses it now (a new FeedVault) is
shown the same way: listed with its reason and its path, where it is
edited or deleted (the app never writes it), its text still readable
(`GET /api/scripts/<id>`); a run of it is refused with that reason, and a
source's sync of it, scheduled too, fails with it (see On a source).

A file is opened without following symlinks and checked on what was
opened. Its SHA-256 is kept when its job is queued, and the file is read
again right before the job starts. A script that changed, or is refused
by then, fails the job.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/scripts` | `{ "dir": "/home/me/.config/feedvault/scripts", "dir_refused": null, "shell_template": "#!/bin/sh …", "scripts": [script, …] }`, built-ins first, then the files by name; 403 from another site |
| GET | `/api/scripts/<id>` | script with `content` (the file's text, or the built-in's JSON), or 404 (its `error` the scripts folder's refusal when it is refused); 403 from another site, as for the listing |
| POST | `/api/scripts/<id>/run` | body `{ "target"?, "url"?, "folder"? }` → `{ "ok": true, "job": {…} }`; 400 bad input, refused script or refused scripts folder (its reason); 403 from another origin (see below); 404 unknown id |

A script:

```json
{ "id": "my-insta", "builtin": false, "kind": "command", "file": "my-insta.json",
  "path": "/home/me/.config/feedvault/scripts/my-insta.json",
  "name": "instaloader, no videos", "description": "…", "needs": "target", "rescan": "{root}",
  "tool": "instaloader", "argv": ["instaloader", "…"], "refused": null, "sha256": "…",
  "size": 312, "mtime": 1727500000, "readable": true, "program": "instaloader", "group": "instaloader" }
```

`kind`: `command` or `shell`; `tool`: `argv[0]` of a command, `null` for a
shell script; `argv`: `null` for a shell script. `program`: the name of
the program a command runs, read as for its lock group (below), `null`
for a shell script; `group`: the lock group a run of it takes
(`instaloader`, `gallery-dl`, `yt-dlp`, `ffmpeg` or `scripts`). A refused
file has `refused` set, `program` and `group` `null`, and only `id`,
`file`, `path`, `refused` and `readable` filled when it could not be
read. `readable`: whether its text was read, and so can be shown
(`GET /api/scripts/<id>` `content`), refused or not: `false` for a file
refused before it is opened (a symlink, someone else's, writable by
group or others, too large).

**Runs.** Job kind `script`, started by `POST /api/scripts/<id>/run` only
(`POST /api/jobs` refuses it), shown in the [Jobs](#jobs) list as any job.

- Lock group: the tool's when the program the command runs is
  `instaloader`, `gallery-dl`, `yt-dlp` or `ffmpeg` (never beside a sync of
  that tool, its pause between them), else `scripts`, with no pause. The
  program is read as the checks read it: by its bare name or its path
  (`/opt/venv/bin/yt-dlp`, `env ./yt-dlp`), behind `env` and its options
  and variables (`env -i`, `env LANG=C yt-dlp`), run by Python (`python3
  -m yt_dlp`, `-m gallery_dl`, `-m instaloader`, with Python's own options
  before `-m`, `-m runpy yt_dlp`, `python3 /path/yt-dlp`, or the package's
  folder or its `__main__.py`), or an absolute path that is a symlink to
  one of them under another name (`~/bin/ytdl`; links are followed one at
  a time, so the first known name along them counts, whenever the command
  is read), behind the launchers the checks read (`nice`, `timeout 60`,
  `ionice -c3`, `stdbuf -oL`, `nohup`, `taskset`, `env`, one in another:
  `nice timeout 60 env X=1 yt-dlp`). Not read: `env -S` (its
  text is split by env's own rules), a name looked up on `PATH` that is a
  symlink, and other programs (`setsid`, `chrt`, `flock`, `sudo`, a wrapper script):
  those run in `scripts`. Python is read so wherever it is: a command
  with no placeholder such as `/opt/venv/bin/python -m yt_dlp` runs in
  `yt-dlp`'s group (one with a placeholder is refused, see above), as
  reading a program as a downloader only ever keeps it beside that
  tool's syncs.
- `argv` is the command as run, or the script's path. `argv` and `params`
  are scrubbed as output is (`health.scrub`).
- The log starts with `[feedvault]` lines: the script's path and the
  first 16 hex digits of its SHA-256 (a built-in: its id) and, for a shell
  script, each `FV_*` value it was given (`FV_SCRIPT` too), scrubbed as
  every log line is.
- The `rescan` folder is indexed once it exits 0, as for any job.

**On a source.** A source's `script` option (a script id, or `null`; see
[What a source downloads](#what-a-source-downloads)) makes its Sync run
that script instead of the built-in command, with `{target}` the source's
target, `{url}` its `url` and `{root}` its folder (`FV_*` likewise). Its
`rescan` is the declared folder, else the source's folder. It runs as job
kind `script-sync`, params `{ "source", "script", "target", "sha256"?, "why"?,
"scheduled"? }` (`sha256` the script's when queued, else `why` it could not run),
in the tool's lock group with its pause. It reads its outcome, account
health, notifications and schedule like the tool's own sync, the tool
being the source's; a failure that output does not explain is named after
the program that ran (`echo failed: …`, a shell script's file:
`greet.sh failed: …`), not the tool. It never falls back to the built-in
command.

- A script that is missing or refused when the sync is queued fails that
  run at once: the job is `failed` as it is created, `message` `"the
  source's script: <why>"`, in its log and on the source too. It is never
  queued, so it neither waits out the tool's pause nor waits for (or
  holds) the tool's lock group. One that is missing, refused or changed
  when the sync starts (read again then) fails it the same way.
- A source's script may not run another tool FeedVault locks (`group`
  other than `scripts` and the source's tool: a `builtin:yt-dlp-video` or
  a gallery-dl command on an instaloader source). Its sync runs in the
  source's tool's group with that tool's pause and output reading, so
  the other tool would run beside its own syncs and without their pause.
  Attaching one is a 400, and a sync of one stored before (sources.json
  edited by hand) fails at once, as above. A program that is not one of
  those tools (a shell script, `echo`) can be any source's.
- The source's `script_warning` says why its next sync would fail, before
  it runs (see [Sources](#sources)).

**Who can run one.** The dashboard only. A request from another site (an
`Origin` that is present and is not `http://` on `localhost`, `127.0.0.1`
or `[::1]`, any port, or a `Sec-Fetch-Site` that is anything but
`same-origin` or `none`) never reaches the script routes, Sync all, a
person's Sync or a source's settings (see [Security rules](#security-rules)).
Of what it does reach, `POST /api/sources` refuses (403) an `options.script`
and a source's Sync refuses (403) a source that has one, so the
userscript's requests from instagram.com get nothing new here. FeedVault
binds to `127.0.0.1` only. Exposing the port
(`0.0.0.0`, a reverse proxy) would hand every script on disk to whoever
reaches it.
