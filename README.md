# FeedVault

A local catalog of the social posts you have downloaded. Point it at the folders
your downloader writes to, and it indexes every post (text, author, date, media)
into a searchable dashboard at `localhost:3380`. A userscript marks posts you
already have while you browse.

FeedVault does not download anything itself. It reads what
[instaloader](https://instaloader.github.io/) writes (gallery-dl and yt-dlp are
next). A post whose files disappear stays in the index, marked missing.

The **Review** page is for sorting: one post at a time, keep or trash it from
the keyboard. Deleting moves files to `.feedvault-trash/` inside the media
folder; only **Empty trash** in Settings removes them for good.

Status: early. Instagram via instaloader works end to end; see
[PLANNING.md](PLANNING.md) for what comes next.

## Start

```bash
./run.sh            # build the UI if needed, start, open the browser
./run.sh --dev      # Vite hot reload + backend, real data through the dev proxy
./run.sh --test     # backend tests
./testapp.sh --demo # throwaway instance on :3389 with invented demo posts
./testapp.sh        # throwaway instance on :3389 on a copy of your database
```

Then open **Settings**, add the folder instaloader downloads into, and the scan
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
