# FeedVault — Planning

Status: design phase, nothing built. Last updated 2026-09-28.

## Goal

A database of the social posts I have already downloaded. Browse them in a
dashboard, search their text, and see in the browser what is already saved.

The main day-to-day use is **sorting**: going through a big archive post by
post and deciding what to keep and what to throw away, instead of juggling a
file manager and an image viewer. See "Review and cleanup" below.

FeedVault is **a catalog first**. Downloading is a secondary convenience, and
the downloading itself stays with existing tools that other people maintain:

- **Instagram**: instaloader (posts, carousels, Reels, stories, highlights, `:saved`)
- **Twitter / X**: gallery-dl
- **TikTok**: gallery-dl or yt-dlp
- **YouTube community posts**: see open questions

Those tools run either from my own shell or cron job, or launched by FeedVault
when I click a save button. Either way FeedVault reads what they write and
indexes it. FeedVault never scrapes a platform itself: if a tool breaks when a
platform changes, that is the tool's upstream problem, and the fix is updating
the tool.

Long-form YouTube videos stay in ChannelVault.

## The core difference from ChannelVault

ChannelVault works with one video per file. FeedVault works with a **post**:
text, author, date, link, and **zero to many media items** (a carousel of 10
images, a tweet with 4 photos and a video, a text-only post). The post is the
thing you browse. Media files belong to a post.

The post's text matters as much as its media. A tweet is mostly text, and a
caption is often the reason you saved an Instagram post.

## Decisions

Rows marked settled are decided; the rest are proposals.

| Question | Proposal |
|---|---|
| Scope | **Settled.** Catalog first. FeedVault reads folders. It may *launch* a downloader CLI as a subprocess, but never contains download logic, never calls a platform API itself, and never handles cookies (each tool keeps its own session) |
| Download trigger | Optional. One small runner module turns `platform + URL` into a CLI command from the config (e.g. `instaloader --no-compress-json -- -{shortcode}`, `gallery-dl --write-metadata {url}`), runs it one at a time, and triggers a rescan of that folder when it exits. Commands live in the config, so fixing a flag never needs a code change |
| Stack | Same as ChannelVault: Flask + SQLite backend, React + Vite dashboard, Tampermonkey userscript, PyInstaller single binary |
| Code vs data | Separate, as in ChannelVault and RecipeVault. The repo holds the app only. Media folders are listed in the config |
| Source of truth | **Settled.** Files on disk, as the downloaders wrote them. Each tool already writes metadata next to the media: instaloader `.json` / `.json.xz`, gallery-dl `--write-metadata`, yt-dlp `--write-info-json`. FeedVault reads those; it does not write its own sidecars |
| Index | **Settled.** Its own SQLite file, never shared with ChannelVault. Derived from the folders and regenerable: lose it and it rebuilds from a rescan. FTS5 on post text and author |
| Own data | Tags and creator links are the only things FeedVault creates. They live in the SQLite file, so that part is not regenerable: export them to a small JSON file on change so a rebuild can restore them |
| Post identity | `platform:post_id` (e.g. `instagram:C8xYz…`, `twitter:1834…`) |
| Parsers | One parser per tool, not per platform: `instaloader`, `gallery-dl`, `yt-dlp`. Each turns a tool's metadata file plus its media files into a normalized post. Unknown files are listed as unmatched, never guessed at |
| Ports | Live on 3380, test instance on 3389 (ChannelVault uses 3360 and 3399, RecipeVault 3370 and 3399 for its demo vault) |
| Outbound network | **Settled.** The server makes one kind of request itself: PyPI's JSON page of instaloader, gallery-dl and yt-dlp (`https://pypi.org/pypi/<name>/json`), to say when an update is out. Off until turned on in Settings → Downloaders, at most once a day per package, fixed URLs, no redirects, a timeout and a size cap. Everything else that reaches the network is a downloader (or pip / pipx updating one) started as a job |
| Security | Copy ChannelVault's origin lockdown: no CORS, a required `X-FeedVault` header on every API call, a Host allowlist |

### Recommended downloader settings

FeedVault reads any layout, but these settings make parsing reliable. They go in
the README, not in code.

- **instaloader**: `--no-compress-json` (plain `.json`, easier to read by hand),
  `--load-cookies firefox` for the session, `--dirname-pattern` per profile
- **gallery-dl**: `--write-metadata` (one JSON per file), or the `metadata`
  postprocessor with `mode: json`
- **yt-dlp**: `--write-info-json`

## How posts get in

1. **Rescan.** FeedVault walks the configured folders, parses new or changed
   metadata files (by mtime and size), and updates the index. Runs on startup,
   on a button in the dashboard, and optionally on a timer.
2. **Save button (optional).** The userscript sends a post URL; the backend
   runs the configured CLI command, then rescans. The runner keeps a small job
   list (queued, running, done, failed with the tool's last stderr lines) so
   failures are visible in the dashboard.
3. **Watch (later).** A filesystem watcher picks up new files as the
   downloaders write them, so a cron run shows up without a manual rescan.

## Userscript

On Instagram, X and TikTok it marks posts that are already in the index with a
green "saved" badge, like ChannelVault's. It asks the backend in batches for the
post IDs visible on the page.

Posts not saved yet get a "Save to FeedVault" button that hands the URL to the
download trigger. The button shows queued / running / failed, then turns into
the badge once the rescan picks the post up.

## Dashboard

- **Feed**: masonry grid of saved posts, newest first. Filter by platform,
  creator, media type, tag. Same visual style as ChannelVault: mono numerals,
  green glow, `statRise` stagger
- **Post page**: media carousel, full text, date, a link back to the original,
  the stats snapshot the downloader captured, tags
- **Creators**: one page per person, **across platforms**. You link
  `@foo` on Instagram, `@foo_` on X and `@Foo` on TikTok to one creator, by hand
- **Search**: full text over captions and tweets
- **Tags**: on posts, as in ChannelVault
- **Stats**: posts per platform and creator, storage used, saves over time
- **Unmatched**: files the parsers could not attach to a post, so nothing goes
  missing silently

## Keeping what disappears

FeedVault never deletes media on its own. If a file disappears from disk, the
post is marked missing in the index rather than dropped. Whether a post still
exists upstream is out of scope, since FeedVault does not talk to the platforms.

## Review and cleanup

Deleting is always the user's explicit action, and always goes through a trash:

- **Review screen**: one post at a time, fullscreen, keyboard driven. Keep,
  trash the post, trash one carousel item, skip, undo. Scoped by creator, kind
  and order. Only undecided posts are queued, so progress carries over.
- **Decisions** ("kept") are user data: their own table, never touched by
  rescans, mirrored to `userdata/decisions.json` and restored from it after a
  rebuild (see `backend/userdata.py`, which every user table goes through).
- **Trash**: files move to `<media_root>/.feedvault-trash/` with the same
  relative path (a rename, instant on the same disk), logged in a manifest.
  Undo restores the latest deletion of a post and re-indexes just its folder.
  "Empty trash" in Settings is the only permanent delete.
- Only files under a configured media root can be trashed.

Known gap: a downloader re-run without its `latest-stamps` could download a
trashed post again. A "never again" list the userscript and a future download
trigger both respect would close that.

## Link with ChannelVault (later)

- A ChannelVault artist page can show that creator's FeedVault posts
  (read-only API call, creator mapping by channel ID)
- The two apps stay separate. Each runs without the other

## Phases

0. **Spike.** Run instaloader and gallery-dl on a few real posts of each kind
   (single image, carousel, Reel, text tweet, tweet with video, quote tweet,
   TikTok video and slideshow). Record the file names and metadata fields each
   tool writes. Design the normalized post from that real output.
1. **Skeleton + instaloader parser.** Backend, config, rescan, SQLite index with
   FTS, Feed grid, Post page, search. *Done*, including filename-only parsing
   for downloads made without metadata, and grid thumbnails.
1b. **Review and cleanup.** Review screen, keep decisions, trash with undo,
   bulk select in the feed. *In progress.*
2. **gallery-dl and yt-dlp parsers.** X and TikTok.
3. **Userscript badge, then save button.** Badge first (read-only), then the
   download trigger and job list.
4. **Creators, tags, stats.** Cross-platform creator linking, tag export file.
5. **Upkeep.** Watcher, duplicate media detection (sha1, then dHash, as in
   ChannelVault's thumbnails), test instance script, bundle and release.

## Open questions

- Existing downloads: which folders, and which tool made them? Old runs with
  different settings may need parser tolerance.
- YouTube community posts: no common tool downloads them well. Skip, or accept
  whatever gallery-dl / yt-dlp manage?
- Shorts: here or in ChannelVault? Leaning ChannelVault, since yt-dlp video
  already lives there.
- Threads and quote tweets: one post or several linked posts? Decide from the
  phase 0 output.
- Public repo like ChannelVault? If so, no real handles or media in fixtures
  (same rule as RecipeVault).
