# yt-dlp fixtures

Info JSON as yt-dlp 2026.08.19 writes it, one folder per case, files named
with FeedVault's sync template `%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s`.
Tests copy a folder and create a tiny video and thumbnail next to each JSON
(`fakes.yt_dlp_case`); no downloaded media is committed.

Real output, from public videos fetched without any login or cookies
(`yt-dlp --ignore-config --skip-download --write-info-json --write-thumbnail
-o ... <url>`; TikTok through the same yt-dlp version with `curl_cffi`
installed, which its extractor needs), then anonymized: every id, handle,
display name, text and URL is replaced (consistently across files), the
`formats` and `thumbnails` lists are cut to one item, `subtitles` and
`automatic_captions` emptied, the `cookies` yt-dlp copies into the JSON
cleared and counters replaced. Key sets, value types, dates, `duration`,
`_type`, `extractor_key` and file names are kept.

| Case | What | Thumbnail |
|---|---|---|
| `tiktok/video` | a TikTok video: numeric `uploader_id`, handle in `uploader`, title cut short from the description | `.image` (JPEG data; yt-dlp keeps the URL's extension) |
| `youtube/short` | the first item of a channel's Shorts tab (`media_type: short`, 5 s) and the tab's own playlist info JSON (`_type: playlist`, `YoutubeTab`, `NA` for its date) | `.webp`, the playlist's `.jpg` |
| `youtube/video` | a regular 19 s video: `uploader_id` is the `@handle`, `channel_id` the `UC…` id | `.webp` |

Long videos (the ChannelVault rule) are the `youtube/video` JSON with a
larger `duration`, set in the test.
