# gallery-dl fixtures

Metadata JSON as gallery-dl 1.32.14 writes it, one folder per case, files named
the way gallery-dl names them. Tests copy a folder and create a tiny media file
next to each JSON (`fakes.gallery_dl_case`); no downloaded media is committed.

Real output, from public posts fetched without any login or cookies
(`gallery-dl --config-ignore --no-download --write-metadata <url>`), then
anonymized: every id, handle, display name, text and URL is replaced
(consistently across files), TikTok item structs are cut down to the keys a
reader would use. Key sets, value types, dates, `num`, `type`, `count` and file
names are kept.

| Case | What |
|---|---|
| `twitter/photo` | a tweet with one photo, default `<file>.<ext>.json` |
| `twitter/json_mode` | the same tweet with the metadata postprocessor's `"extension-format": "json"` (`<file>.json`) |
| `twitter/four_photos` | a tweet with four photos |
| `twitter/video` | a video tweet with `previews=true`: the `.mp4` and its `preview` `.jpg` |
| `twitter/multi_source` | two photos whose media came from another tweet (`source_id`) |
| `twitter/text_only` | `text-tweets=true` with a metadata postprocessor on `"event": "post"` and `"filename": "{tweet_id}.json"`: the post-level JSON of a tweet without media |
| `tiktok/video` | a video with `covers=true`: the `.mp4` and its `cover` `.jpg` |
| `tiktok/photos` | a two-photo slideshow and its music (`.mp3`, `type: audio`) |

Derived from the real files above, following gallery-dl's twitter extractor
(`_pagination_tweets`, `_transform_tweet`), because both need a logged-in
timeline to download for real:

| Case | What |
|---|---|
| `twitter/retweet` | `multi_source` retweeted by another account: `tweet_id` is the retweet's own id, `retweet_id` the original, `author` the original author, `user` the retweeter, `content` starts with `RT @author: `, `date_original` added |
| `twitter/quote` | `photo` quoting `four_photos`: the quoting tweet has `quoted_id`; the quoted one (saved in the quoter's folder with `quoted=true`) has `quote_id` and `quote_by` |
