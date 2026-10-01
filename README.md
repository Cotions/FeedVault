# FeedVault

A local catalog of the social posts you have downloaded. Point it at the folders
your downloader writes to, and it indexes every post (text, author, date, media)
into a searchable dashboard at `localhost:3380`. A userscript marks posts you
already have while you browse.

FeedVault does not download anything itself. It reads what
[instaloader](https://instaloader.github.io/) and
[gallery-dl](https://github.com/mikf/gallery-dl) write (yt-dlp is next). A post whose files disappear stays in the index, marked missing.

The **Review** page is for sorting: one post at a time, keep or trash it from
the keyboard. Deleting moves files to `.feedvault-trash/` inside the media
folder; only **Empty trash** in Settings removes them for good.

Status: early. Instagram via instaloader and X/Twitter and TikTok via
gallery-dl work end to end; see
[PLANNING.md](PLANNING.md) for what comes next.

## Start

```bash
./run.sh            # build the UI if needed, start, open the browser
./run.sh --dev      # Vite hot reload + backend, real data through the dev proxy
./run.sh --test     # backend tests
./testapp.sh --demo # throwaway instance on :3389 with invented demo posts
./testapp.sh        # throwaway instance on :3389 on a copy of your database
```

Then open **Settings**, add the folder your downloader writes into, and the scan
starts on its own.

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
  --download-archive ~/.local/share/feedvault/gallery-dl.sqlite3 \
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
  `~/.local/share/feedvault` by default), not next to the media, so trashing a
  post in FeedVault does not make the next run download it again (see
  [#4](https://github.com/Cotions/FeedVault/issues/4))
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

## Userscript

Install [Tampermonkey](https://www.tampermonkey.net/), then open
<http://localhost:3380/userscript/feedvault.user.js>. On Instagram, saved posts
get a green "saved" badge in grids, and a post page shows an "in FeedVault" link.

## Where things live

| What | Where |
|---|---|
| Config | `~/.config/feedvault/config.json` (override with `FEEDVAULT_CONFIG`) |
| Index | `~/.local/share/feedvault/feedvault.db`, rebuilt from your folders by a rescan |
| Media | wherever your downloader put it; FeedVault only reads it |

## Security

The server binds to `127.0.0.1` only. Every `/api` call needs an `X-FeedVault`
header and a local `Host`, and no CORS is ever granted, so other websites in
your browser cannot read or change your library. API reference:
[docs/API.md](docs/API.md).
