# FeedVault — Planning

Status: built and in use. This was the design written before any code
(2026-09-28); it is kept as the record of why things are the way they are,
with each part marked as shipped (with its PR) or still open. Last updated
2026-10-06. What the app does today is in the [README](README.md) and
[docs/API.md](docs/API.md); how to test it in
[docs/TESTING.md](docs/TESTING.md).

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
- **YouTube Shorts**: yt-dlp (short videos only, see open questions)
- **YouTube community posts**: see open questions

Those tools run either from my own shell or cron job, or launched by FeedVault
when I click a save button. Either way FeedVault reads what they write and
indexes it. FeedVault never scrapes a platform itself: if a tool breaks when a
platform changes, that is the tool's upstream problem, and the fix is updating
the tool. (One opt-in exception, not a platform: a person's link-in-bio page,
fetched on request; see Outbound network below.)

Long-form YouTube videos stay in ChannelVault.

## The core difference from ChannelVault

ChannelVault works with one video per file. FeedVault works with a **post**:
text, author, date, link, and **zero to many media items** (a carousel of 10
images, a tweet with 4 photos and a video, a text-only post). The post is the
thing you browse. Media files belong to a post.

The post's text matters as much as its media. A tweet is mostly text, and a
caption is often the reason you saved an Instagram post.

## Decisions

Rows marked settled were decided up front; the others were proposals, and
each now says what was built.

| Question | Proposal |
|---|---|
| Scope | **Settled.** Catalog first. FeedVault reads folders. It may *launch* a downloader CLI as a subprocess, but never contains download logic, never calls a platform API itself, and never handles cookies (each tool keeps its own session) |
| Download trigger | **Built, differently** (#24, #26, #28, #58). A job runner (`backend/jobs.py`) starts jobs *by kind*, each kind building its own argv in code from checked parameters, runs them without a shell, and rescans the target folder when a job ends. Commands do **not** live in the config: the API never takes a command. Custom commands are files the user writes in the scripts folder (README, "Scripts") |
| Stack | Flask + SQLite backend, React + Vite dashboard, Tampermonkey userscript: **built**. PyInstaller single binary: **open** (`config.py` already looks for a frozen bundle, but there is no build spec or release yet) |
| Code vs data | Separate, as in ChannelVault and RecipeVault. The repo holds the app only. Media folders are listed in the config |
| Source of truth | **Settled.** Files on disk, as the downloaders wrote them. Each tool already writes metadata next to the media: instaloader `.json` / `.json.xz`, gallery-dl `--write-metadata`, yt-dlp `--write-info-json`. FeedVault reads those; it does not write its own sidecars |
| Index | **Settled.** Its own SQLite file, never shared with ChannelVault. Derived from the folders and regenerable: lose it and it rebuilds from a rescan. FTS5 on post text and author |
| Own data | **Built** (#14 and later). Everything the user creates (decisions, tags, collections, people, sources, dismissed duplicates and suggestions, seen marks, …) lives in the SQLite file and goes through `backend/userdata.py`: each table is exported to `<data_directory>/userdata/<name>.json` on change and restored from it when its table is empty |
| Post identity | `platform:post_id` (e.g. `instagram:C8xYz…`, `twitter:1834…`) |
| Parsers | **Built** (`backend/parsers/`: instaloader, gallery-dl #23, yt-dlp #28). One parser per tool, not per platform. Each turns a tool's metadata file plus its media files into a normalized post. Unknown files are listed as unmatched, never guessed at |
| Ports | **Built.** Live on 3380 (`FEEDVAULT_PORT`), test instance on 3389 (`FEEDVAULT_TEST_PORT`). The browser tests use a free port of their own (ChannelVault uses 3360 and 3399, RecipeVault 3370 and 3399 for its demo vault) |
| Outbound network | **Settled.** The server makes two kinds of request itself. PyPI's JSON page of instaloader, gallery-dl and yt-dlp (`https://pypi.org/pypi/<name>/json`), to say when an update is out: off until turned on in Settings → Downloads → Downloaders, at most once a day per package, fixed URLs, no redirects, a timeout and a size cap. And the one exception to "FeedVault never fetches a page" (#11): a person's link-in-bio page, when the user pastes it and clicks Import (`backend/biofetch.py`). Off until turned on in Settings → Downloads → Link-in-bio import; one page per click, never a link found in it; https on port 443 to a short list of hosts (linktr.ee, beacons.ai, lnk.bio, solo.to, campsite.bio, linkin.bio, allmylinks.com), public addresses only, connected to the address checked; no proxy, cookies or credentials; at most 3 redirects checked again, 10 s in all, 2 MB, uncompressed HTML only; one at a time, 5 s apart. The page is parsed (never run) for profile links the routing table knows, each offered for the user to add; nothing is stored. Everything else that reaches the network is a downloader (or pip / pipx updating one) started as a job |
| Security | **Built**, and hardened since (#72, #74, #76): no CORS, a required `X-FeedVault` header on every API call, a Host allowlist, no framing, media refused to other sites and never served as a page. The rules are listed in [docs/API.md → Security rules](docs/API.md#security-rules) |

### Recommended downloader settings

FeedVault reads any layout, but these settings make parsing reliable. They
are in the README ("Downloading with …"), not in code.

- **instaloader**: `--no-compress-json` (plain `.json`, easier to read by hand),
  `--load-cookies firefox` for the session, `--dirname-pattern` per profile
- **gallery-dl**: `--write-metadata` (one JSON per file), or the `metadata`
  postprocessor with `mode: json`
- **yt-dlp**: `--write-info-json`

## How posts get in

1. **Rescan.** FeedVault walks the configured folders, parses new or changed
   metadata files (by mtime and size), and updates the index. *Built*: runs on
   startup, on a button in the dashboard, and after each sync or save job.
   A rescan on a timer is **open** (scheduled syncs, #47, rescan what they
   download, which covers most of it).
2. **Save button.** *Built* (#37, #60): the userscript sends a post; the
   backend runs the tool for it as a job, then rescans. The Jobs page shows
   the queue, a live log and the history.
3. **Watch.** **Open.** A filesystem watcher would pick up new files as an
   outside cron run writes them, without a manual rescan.

## Userscript

*Built* (#37, #60, #80). On Instagram, X and TikTok it marks posts that are
already in the index with a green "saved" badge, like ChannelVault's. It asks
the backend in batches for the post IDs visible on the page.

Posts not saved yet get a "Save to FeedVault" button that hands the post to a
save job. The button shows queued / saving / failed, then turns into a link
once the rescan picks the post up. Profile pages get a "Sync profile" button.

## Dashboard

*Built*, except where an item says otherwise.

- **Feed**: masonry grid of saved posts, newest first. Filter by platform,
  creator, media type, tag. Same visual style as ChannelVault: mono numerals,
  green glow, `statRise` stagger
- **Post page**: media carousel, full text, date, a link back to the original,
  the stats snapshot the downloader captured, tags
- **Creators**: one page per person, **across platforms**. You link
  `@foo` on Instagram, `@foo_` on X and `@Foo` on TikTok to one creator, by hand
  or from a suggestion (same or similar handle, same display name, a bio link
  already in the downloaded metadata). Accounts are linked by the platform's
  own account id where the metadata has one, so a renamed account stays one
  account; every handle it had is kept and searchable. The link lives only in
  the database: files stay where each tool wrote them. A person's link-in-bio
  page (linktr.ee and the like) can be imported, opt-in: FeedVault fetches
  that one page on request and suggests the accounts it lists (see Outbound
  network above for its limits)
- **Search**: full text over captions and tweets
- **Tags**: on posts, as in ChannelVault
- **Stats**: posts per platform and kind (Stats), storage used per creator,
  kind and year (Storage, #15). Saves over time: **open**
- **Unmatched**: files the parsers could not attach to a post, so nothing goes
  missing silently

## Keeping what disappears

FeedVault never deletes media on its own. If a file disappears from disk, the
post is marked missing in the index rather than dropped. Whether a post still
exists upstream is out of scope, since FeedVault does not talk to the platforms.

## Review and cleanup

*Built* (Review, Trash page #16).

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
  "Empty trash" in Settings and purging an entry on the Trash page (#16)
  are the only permanent deletes.
- Only files under a configured media root can be trashed.

The gap noted here at first (a downloader re-run could download a trashed
post again) is closed for FeedVault's own syncs and saves: trashing a
gallery-dl or yt-dlp post adds it to that tool's download archive in the data
directory (#28), and a post an instaloader sync downloads again while it is
in the trash is moved back to the trash after the sync (#40, #42). A tool run
from your own shell with other archives is not covered.

## Link with ChannelVault

**Open**, nothing built.

- A ChannelVault artist page can show that creator's FeedVault posts
  (read-only API call, creator mapping by channel ID)
- The two apps stay separate. Each runs without the other

## Phases

0. **Spike.** Run instaloader and gallery-dl on a few real posts of each kind
   (single image, carousel, Reel, text tweet, tweet with video, quote tweet,
   TikTok video and slideshow). Record the file names and metadata fields each
   tool writes. Design the normalized post from that real output. *Done.*
1. **Skeleton + instaloader parser.** Backend, config, rescan, SQLite index with
   FTS, Feed grid, Post page, search. *Done*, including filename-only parsing
   for downloads made without metadata, and grid thumbnails.
1b. **Review and cleanup.** Review screen, keep decisions, trash with undo,
   bulk select in the feed. *Done* (with the Trash page, #16, and Review on a
   phone, #82).
2. **gallery-dl and yt-dlp parsers.** X and TikTok. *Done* (#23, #28).
3. **Userscript badge, then save button.** *Done* (#37, #60), with the job
   runner and Jobs page (#24).
4. **Creators, tags, stats.** Cross-platform creator linking, tag export file.
   *Done* (#21, #25, #52; user data export #14).
5. **Upkeep.** Duplicate media detection: *done* (#18, #20). Test instance
   script: *done* (`testapp.sh`), plus browser tests in CI (#91, #97).
   Watcher: **open**. Bundle and release: **open**.

## Open questions

Answered:

- Existing downloads: read as they are; filename-only instaloader downloads
  are rebuilt from their names, and what cannot be attached is listed as
  unmatched.
- Shorts: indexed here. A yt-dlp video up to `youtube_max_seconds` (default
  180) is a post; a longer one is left to ChannelVault and listed as such on
  the Unmatched page (#28).
- Quote tweets and retweets: one post per tweet, under its own author, with a
  "Quoted by @…" or "Retweeted by @…" note (#23).
- Public repo: yes. No real handles or media in fixtures or docs (same rule
  as RecipeVault).

Still open:

- YouTube community posts: no common tool downloads them well. Skip, or accept
  whatever gallery-dl / yt-dlp manage?
- Threads (a reply chain as one post or several linked posts): not decided;
  each tweet is its own post today.
