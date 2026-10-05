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


Install [Tampermonkey](https://www.tampermonkey.net/) or
[Violentmonkey](https://violentmonkey.github.io/), then click **Install the
browser userscript** in Settings › About, or open
<http://localhost:3380/userscript/feedvault.user.js>. The script is served
with the port of the instance it comes from (in its `API_BASE` and its update
URLs): installed from the demo on 3389, or with `FEEDVAULT_PORT` set, it
talks to that instance. It runs on Instagram,
X (x.com, twitter.com) and TikTok (www.tiktok.com). Saved posts get a green
"saved" badge in grids, and a post page shows an "In FeedVault" link.
A post FeedVault does not have gets a **Save to FeedVault** button instead (on
the post page and in the dialog a grid opens on Instagram): it downloads that
one post, and turns into the link once it is indexed. It shows Queued,
Saving… and, when it fails, why; with FeedVault stopped it says "FeedVault is
not running". Several saves in a row queue up (at most 20 at once,
`save_queue_max` in the config).

- Instagram: instaloader, into its owner's folder (or `_saved/` in your
  first media root, until the owner gets a source: its next sync moves them
  into its folder).
- X: gallery-dl; TikTok videos: yt-dlp (TikTok photo posts are not saved).
  The file names are the ones a sync gives, and the post goes into gallery-dl's
  and yt-dlp's download archives, so a later sync of the profile does not
  download it again. It goes into the folder of the owner's source, else the
  folder holding the owner's posts, else `<first media root>/twitter/<name>`
  or `tiktok/<name>`: the folder a source for that profile would use. Only
  the post's own link is accepted: a short link (t.co, vm.tiktok.com) is
  refused, open it and save from the post's page. The tool's settings apply
  (browser cookies, ignoring your config).

A profile page gets a **Sync profile** button: it syncs the profile's
source, or first adds one (it asks, in the button itself, naming the tool and
folder), and links to the person or account in FeedVault. A source that runs
a script is only synced from the dashboard: the button says so and links
there.

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

Each sync that brought posts or failed also leaves an entry under the
sidebar's **Notifications** bell ("12 new from X", "X: account not found");
an entry opens exactly the posts that sync brought, or the source. A
scheduled sync that keeps failing the same way is one entry, not one per
retry. **Mute** (the bell on a Creators card or a person's page) keeps a
person or an account out of the list, the toasts and the global new count;
their own page still shows their new posts. **Settings → Sync → Desktop
notifications** (off by default) pops entries up on the desktop: in the
browser while a tab is open (it asks permission when you turn it on), else
with `notify-send` when it is installed.

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

## Scripts

When the built-in commands are not what you want, write your own as files in
`~/.config/feedvault/scripts/` (beside `config.json`). FeedVault never writes
there: you create and edit them in a text editor, and the **Scripts** page
lists them, shows them read-only and runs them as jobs (live log, cancel,
history). The folder is read again each time, so an edit counts at once.

- **A command**, `my-insta.json`: `{"needs": "target", "rescan": "{root}",
  "argv": ["instaloader", "--no-videos", "--dirname-pattern", "{root}", "--", "{target}"]}`.
  The program is instaloader, gallery-dl, yt-dlp or ffmpeg (found as in
  Settings) or an absolute path. `{target}`, `{url}`, `{root}`,
  `{data_dir}` and `{archive}` are replaced inside their own element; the
  list is run as it is, without a shell.
  A placeholder where it would be read as code (yt-dlp `--exec`,
  gallery-dl `--exec`, `-o`, `--filter`, a shell's `-c` text, `env -S`
  or `-iS`, …) is refused: use the tool's own fields (`%(webpage_url)q`,
  `{_path}`) or a shell script. So is one in a gallery-dl format string
  that starts with `\f` (`-f "\fE …"`, `--print "post:\fF …"`: Python or
  a template file); a plain one (`-f "{url}"`) is fine, the value's braces escaped.
  In `--print-to-file FORMAT FILE` only FILE's name is a format string:
  its folder (up to the last `/`) keeps a value as it is, and a run whose
  value puts a `$` there (expanded) or `\f` in the name is refused.
  So is one whose link or target puts a `..` or leading `~` there, or a `..`, `~` or `$` in a path option (`-D`, `-o`, env's `-C`…).
  After `--` nothing is an option, as the tools read it: what follows is never checked or escaped.
- **A shell script**, `my-script.sh`, executable, with a `#!` line and a
  `# needs: url` (or `target`, `none`) header. It gets its inputs only as
  `FV_TARGET`, `FV_URL`, `FV_ROOT`, `FV_DATA_DIR` and `FV_ARCHIVE` in a
  minimal environment: quote them (`"$FV_URL"`). It runs from the bytes
  that were checked (its interpreter reads them from `/dev/fd/N`, so `$0`
  is `/dev/fd/N`; `FV_SCRIPT` is the file's path).

**Copy template** on a built-in (instaloader profile, saved posts, one post,
stories and highlights; gallery-dl profile media, one link; yt-dlp one
video, a channel) shows the file to create and its content, with copy
buttons. A source's **Options → Command** picks a script its Sync (and its
schedule) runs instead of the tool's own command; a script that is missing
or refused fails that sync with the reason, never running the built-in one.

A file is refused, with the reason shown, and never run when it is a
symlink, someone else's, writable by group or others (as is the folder,
or a folder above it that is not sticky or not root's or yours),
over 64 KiB, not named `[a-z0-9_-].json` / `.sh`, or malformed; a mode
refusal says the `chmod go-w '<path>'` to run. FeedVault makes its own
folders `0700` whatever the umask, and tightens its config and scripts
folders on start if they were left group-writable. A script
changed between queueing and starting fails its run. Inputs are checked:
a link must be `http(s)://`, an Instagram target a profile name or
shortcode, and nothing may start with `-`. Details:
[docs/API.md → Scripts](docs/API.md#scripts).

**Anyone who can reach FeedVault's port can run every script in that
folder.** FeedVault binds to `127.0.0.1` for that reason: do not expose it
(`0.0.0.0`, a reverse proxy, a tunnel). Pages on other sites, the
userscript's included, cannot list, run or attach a script.

## Where things live

| What | Where |
|---|---|
| Config | `~/.config/feedvault/config.json` (override with `FEEDVAULT_CONFIG`) |
| Scripts | `~/.config/feedvault/scripts/`, beside the config; FeedVault only reads it |
| Index | `~/.local/share/feedvault/feedvault.db`, rebuilt from your folders by a rescan |
| Media | wherever your downloader put it; FeedVault only reads it |

## Security

The server binds to `127.0.0.1` only. Every `/api` call needs an `X-FeedVault`
header and a local `Host`, and no CORS is ever granted, so other websites in
your browser cannot read or change your library. Jobs are started by kind,
with parameters each kind checks; the API never takes a command, and tools run
without a shell. Your [scripts](#scripts) are the exception you write
yourself: whoever reaches the port can run them, so never expose it. The server itself contacts the network for one thing only,
and only if you turn it on (**Settings → Downloads → Downloaders → check for updates**):
PyPI's JSON page of instaloader, gallery-dl and yt-dlp, at most once a day,
to say when an update is out. API reference:
[docs/API.md](docs/API.md).
