# FeedVault

A local catalog of the social posts you have downloaded. Point it at the folders
your downloader writes to, and it indexes every post (text, author, date, media)
into a searchable dashboard at `localhost:3380`. A userscript marks posts you
already have while you browse.

FeedVault does not download anything itself. It reads what
[instaloader](https://instaloader.github.io/) and
[gallery-dl](https://github.com/mikf/gallery-dl) and [yt-dlp](https://github.com/yt-dlp/yt-dlp) write. A post whose files disappear stays in the index, marked missing.

The **Review** page is for sorting: one post at a time, keep or trash it from
the keyboard. Deleting moves files to `.feedvault-trash/` inside the media
folder; only **Empty trash** in Settings → Library removes them for good.

Status: early. Instagram via instaloader, X/Twitter and TikTok via
gallery-dl, and TikTok and YouTube Shorts via yt-dlp work end to end; see
[PLANNING.md](PLANNING.md) for what comes next.

## Start

```bash
./run.sh            # build the UI if needed, start, open the browser
./run.sh --dev      # Vite hot reload + backend, real data through the dev proxy
./run.sh --test     # backend tests
./testapp.sh --demo # throwaway instance on :3389 with invented demo posts
./testapp.sh        # throwaway instance on :3389 on a copy of your database
```

Then open **Settings** (Library tab), add the folder your downloader writes into, and the scan
starts on its own. **Settings → Appearance** changes the accent colour: one of
eight presets, or a theme of your own, saved in that browser.

## Downloading with instaloader

Any instaloader layout works. These flags make life easier:

```bash
instaloader --load-cookies firefox --no-compress-json -- -SHORTCODE   # one post
instaloader --load-cookies firefox --no-compress-json :saved          # your Saved collection
instaloader --load-cookies firefox --no-compress-json some_profile    # a whole profile
```

- `--no-compress-json` writes plain `.json` instead of `.json.xz` (both are read)
- `--load-cookies <browser>` reuses your browser session instead of a password login
- with metadata JSON, any `--filename-pattern` works as long as a post's media
  and its JSON share the base name (the default does)
- without metadata (`save_metadata=False`), FeedVault rebuilds posts from file
  names shaped like `{target}-{date:%Y-%m-%d}-{shortcode}` (or the older
  `{target} - {shortcode} - N`). Media, author, date and highlight title come
  through; captions and likes do not, since they were never saved

Files FeedVault cannot attach to a post show up on the **Unmatched** page.

## Downloading with gallery-dl

gallery-dl writes a metadata JSON per downloaded file when asked to; FeedVault
groups those into posts. X/Twitter and TikTok are fully mapped; other sites
gallery-dl supports become posts from the common keys (id, date, author, text)
when their metadata has an id and a date.

```bash
gallery-dl --write-metadata \
  -d ~/Media/gallery-dl \
  --download-archive ~/.local/share/feedvault/gallery-dl/archive.sqlite3 \
  https://x.com/some_account/media https://www.tiktok.com/@some_account
```

- `--write-metadata` is required: it writes `<file>.<ext>.json` next to each
  file. The `metadata` postprocessor works too, with `"extension-format":
  "json"` (`<file>.json`), as long as the JSON stays in the same folder as
  its file
- `-d` is the folder you add in Settings. gallery-dl's default layout below it
  is already one folder per author (`twitter/<handle>/`, `tiktok/<handle>/`);
  if you set your own `directory`, keep the author in it
- `--download-archive` remembers what was downloaded, so a later run skips it.
  Keep it in FeedVault's data directory (`data_directory` in the config,
  `~/.local/share/feedvault` by default), at the path FeedVault's syncs use:
  a post trashed in FeedVault is added to it, so the next run does not
  download it again
- `-o previews=true` (X) and `-o covers=true` (TikTok) also save a video's
  thumbnail, used as its poster
- retweets (`-o retweets=true`) show under the original author with "Retweeted
  by @<the account you downloaded>"; quoted tweets (`-o quoted=true`) with "Quoted by @…"
- text-only tweets have no file, so `--write-metadata` writes nothing for
  them. To keep them, add to your gallery-dl config:

  ```json
  "twitter": {
      "text-tweets": true,
      "postprocessors": [{"name": "metadata", "event": "post", "filename": "{tweet_id}.json"}]
  }
  ```

X timelines need a logged-in session (`--cookies-from-browser firefox`);
single public tweets and TikTok usually do not.

FeedVault's own syncs read your gallery-dl and yt-dlp config files too. If
something there changes where or how files are saved, turn on "Ignore my
<tool> config" in that tool's card in Settings → Sync. A sync whose new
files FeedVault cannot read (metadata turned off, another layout) says so
in its message, and points at that option.

## Downloading with yt-dlp

yt-dlp writes an info JSON per video when asked to; FeedVault reads it and
the files that share its name: the video, the thumbnail (its poster),
subtitles and the description file. It suits TikTok and short videos;
long YouTube videos are left to ChannelVault (see below).

```bash
yt-dlp --write-info-json --write-thumbnail \
  --download-archive ~/.local/share/feedvault/yt-dlp/archive.txt \
  -o "~/Media/yt-dlp/%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s" \
  https://www.tiktok.com/@some_account
```

- `--write-info-json` is required: `<name>.info.json` beside `<name>.mp4`.
  Keep the two in the same folder with the same base name
- `--write-thumbnail` saves the poster (TikTok's keeps the `.image` its URL
  has; FeedVault serves it as the image it is)
- `--download-archive` remembers what was downloaded; keep it in FeedVault's
  data directory, as for gallery-dl, so a trashed post stays trashed
- a playlist or channel run also writes the playlist's own info JSON: it is
  recognized and ignored, not listed as unmatched
- YouTube videos longer than `youtube_max_seconds` in the config (default
  180) are not indexed: they show on the **Unmatched** page as "left to
  ChannelVault". Shorts and short clips are indexed like TikTok videos


Install [Tampermonkey](https://www.tampermonkey.net/), then open
<http://localhost:3380/userscript/feedvault.user.js>. On Instagram, saved posts
get a green "saved" badge in grids, and a post page shows an "In FeedVault" link.
A post FeedVault does not have gets a **Save to FeedVault** button instead (on
the post page and in the dialog a grid opens): it downloads that one post with
instaloader, into its owner's folder (or `_saved/` in your first media root,
until the owner gets a source: its next sync moves them into its folder),
and turns into the link once it is indexed. It shows Queued, Saving… and, when
it fails, why. A profile page gets a **Sync profile** button: it syncs the
profile's source, or first adds one (it asks, in the button itself), and
links to the person or account in FeedVault.

## Jobs and tools

The **Jobs** page shows what FeedVault is running for you: the queue, a live
log, and the last 100 jobs. Quitting FeedVault stops running jobs; a job
left running by a crash is stopped on the next start.

**Settings → Downloads → Downloaders** shows, for instaloader, gallery-dl, yt-dlp and
ffmpeg, whether each is installed, where, which version, and how (pipx, a
virtualenv, or the system), with the command to install a missing one.
**Test** runs a tool once on a public item with its sync's login and says
whether it works (or needs a login, or is rate limited). **Update** upgrades
a tool installed with pipx or in a virtualenv (`pipx upgrade`, or pip in
that virtualenv) as a job, never during a sync of that tool; for a system
install it shows the command to run instead. Tick **Check PyPI for new
versions** to see when an update is out (off by default). A sync that fails
because its tool is missing, needs a login, or is out of date links there.

## Syncing profiles

A person (or an account) can have **sources**: a profile link and the folder
its posts go to. Paste the link (Instagram, X, TikTok, YouTube, Reddit,
Bluesky, pixiv) and FeedVault picks the tool from **Settings → Downloads → Link
routing**: instaloader for Instagram, gallery-dl for X, Reddit, Bluesky and
pixiv, yt-dlp for YouTube and TikTok. **Sync** runs that tool as a job and
indexes the folder, so new posts show up in the Feed without a terminal.
gallery-dl and yt-dlp keep their download archives in FeedVault's data
directory: a sync stops at what they already have, a first sync skips what
is already indexed, and a post you trash is never downloaded again (until
you restore it).

For Instagram, only new posts are fetched: FeedVault keeps instaloader's `--latest-stamps` file in
its data directory, and a first sync starts after the newest post already
indexed (pick "full history" to fetch everything). **Creators** offers your
existing instaloader folders as sources to confirm, and **Sync all** runs
every source one after another with a pause between them.

Each source has **options** for what it downloads, shown when you add it and
under **Options** later, as far as its tool and platform allow: which parts
of the profile (Instagram posts, reels, stories, highlights, tagged posts; an
X profile's timeline, Media, Posts or Replies tab; and so on), images only or
videos only, nothing older than a date, and for gallery-dl and yt-dlp a first
sync that fetches only the last N posts. Stories, highlights and tagged posts
need an Instagram login (**Settings → Sync**). The source shows a summary
such as "posts, reels · since 2024-01-01".

A source can also sync on its own: its **Schedule** option is off (default),
hourly, daily or weekly (daily as soon as you turn its stories on, since they
last 24 hours). The next sync counts from the end of the last one, so a
FeedVault that was off catches up once at startup; a source that failed waits
twice as long each time, up to a day; sources of one site start a few minutes
apart; and a source whose tool is missing or whose media root is offline is
skipped, with a note. The source shows "daily · next sync in 3 h".
**Settings → Sync → Pause all schedules** stops them all.

When a sync adds posts, a notice says so ("12 new posts from @name") and
links to them. Posts indexed since you last pressed **Mark all seen** are
**new**: the sidebar counts them next to Feed, Creators shows how many each
person and account has, the Feed's "New since last visit" chip (or `is:new`
in the search box) shows only those, and Review can go through just them.
What was already in the archive is never new, whenever it was posted.
**Mark seen** on a Creators card or a person's page does the same for that
person (or that account, when it is linked to nobody) only.

By default syncs run without a login (public profiles only). **Settings → Sync →
Instagram sync** can make instaloader use your browser's Instagram cookies
(`--load-cookies`) or a session it saved after `instaloader --login` in a
terminal. FeedVault only passes the browser or user name on; it never reads
or stores cookies, passwords or session files.

gallery-dl and yt-dlp can use a browser's cookies the same way
(`--cookies-from-browser`, **Settings → Sync → gallery-dl / yt-dlp sync**).
yt-dlp copies the cookies it used into each video's `.info.json`; FeedVault
rewrites the files of each sync without them, and **Settings → Sync → YouTube
and TikTok sync** can do the same for info JSONs written before.

## Where things live

| What | Where |
|---|---|
| Config | `~/.config/feedvault/config.json` (override with `FEEDVAULT_CONFIG`) |
| Index | `~/.local/share/feedvault/feedvault.db`, rebuilt from your folders by a rescan |
| Media | wherever your downloader put it; FeedVault only reads it |

## Security

The server binds to `127.0.0.1` only. Every `/api` call needs an `X-FeedVault`
header and a local `Host`, and no CORS is ever granted, so other websites in
your browser cannot read or change your library. Jobs are started by kind,
with parameters each kind checks; the API never takes a command, and tools run
without a shell. The server itself contacts the network for one thing only,
and only if you turn it on (**Settings → Downloads → Downloaders → check for updates**):
PyPI's JSON page of instaloader, gallery-dl and yt-dlp, at most once a day,
to say when an update is out. API reference:
[docs/API.md](docs/API.md).
