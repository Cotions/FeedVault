// What a source downloads (docs/API.md "What a source downloads"): the
// names and one-line effects the form shows, the preset a source follows,
// its one-line summary, and the checks the form makes before the backend
// makes them again.

// Content kinds by platform: [label, effect]. The backend's choices say
// which a source can have; a kind missing here shows its key.
const KINDS = {
  instagram: {
    posts: ["posts", "the profile's feed posts"],
    reels: ["reels", "its short videos"],
    stories: ["stories", "what is up now (they last 24 h)"],
    highlights: ["highlights", "stories it kept on its profile"],
    tagged: ["tagged", "other people's posts it is tagged in"],
  },
  twitter: {
    timeline: ["timeline", "its posts, as gallery-dl walks them by default"],
    media: ["media", "the profile's Media tab"],
    tweets: ["tweets", "the profile's Posts tab"],
    with_replies: ["replies", "the Replies tab: its posts and its replies"],
  },
  bluesky: {
    media: ["media", "the profile's Media tab"],
    posts: ["posts", "the profile's Posts tab"],
    replies: ["replies", "the Replies tab: its posts and its replies"],
    video: ["video", "the profile's Videos tab"],
  },
  tiktok: {
    posts: ["posts", "its videos and photo posts"],
    reposts: ["reposts", "what it reposted"],
    stories: ["stories", "what is up now"],
  },
};

export const MEDIA = [
  ["all", "images and videos"],
  ["images", "images only"],
  ["videos", "videos only"],
];

// What a media choice does: both tools pick file by file (instaloader's
// "videos" is --no-pictures: a carousel keeps its videos).
export function mediaEffect(tool, media) {
  const insta = tool === "instaloader";
  if (media === "images") return insta ? "videos are skipped, carousels keep their images" : "videos are skipped";
  if (media === "videos") return insta ? "images are skipped, carousels keep their videos" : "images are skipped";
  return "everything the posts have";
}

export const FIRST_POSTS_MAX = 10000;

// The metadata files a sync writes beside the media (docs/API.md "Metadata
// files"): [mode, label, effect]; slim is instaloader's only. METADATA_LOST:
// what "none" gives up, per tool.
export const METADATA = [
  ["full", "full", "the tool's own metadata files beside each post"],
  ["slim", "slim", "instaloader's, slimmed to what FeedVault reads"],
  ["none", "none", "no files beside the media: posts are read from their file names"],
];
export const METADATA_LOST = {
  instaloader: "no captions, hashtags, counts, location or numeric account id; stories read as posts",
  "gallery-dl": "no captions, hashtags, counts or retweet notes; the account is its folder's",
  "yt-dlp": "title, description and link stay inside the video (read with ffprobe); no counts, no thumbnail file",
};
export const metadataLabel = m => METADATA.find(([v]) => v === m)?.[1] || m;
export function metadataEffect(tool, m) {
  if (m === "none") return `${METADATA[2][2]}; ${METADATA_LOST[tool] || ""}`;
  return METADATA.find(([v]) => v === m)?.[2] || "";
}

// How often the scheduler syncs a source (docs/API.md "Schedules"): [value, label, effect].
export const SCHEDULES = [
  ["off", "off", "only when you click Sync"],
  ["hourly", "hourly", "an hour after its last sync ended"],
  ["daily", "daily", "a day after its last sync ended"],
  ["weekly", "weekly", "a week after its last sync ended"],
];

// A YouTube channel's tabs (sync.py YOUTUBE_TABS): a link to one is not the channel's own page.
const YOUTUBE_TABS = new Set(["videos", "shorts", "streams", "live", "podcasts", "releases", "playlists", "featured"]);

// "Last N" is per kind with gallery-dl (each kind is its own extractor,
// each with its --post-range) and per tab on a YouTube channel's own page
// (yt-dlp's --playlist-items): the first sync gets up to N of each.
export function firstPostsEach(tool, content, platform, target) {
  if (tool === "gallery-dl" && content?.length > 1) return " of each kind";
  if (tool === "yt-dlp" && platform === "youtube" && target) {
    let parts;
    try { parts = new URL(target).pathname.replace(/^\/+|\/+$/g, "").split("/"); } catch { return ""; }
    if (!YOUTUBE_TABS.has(parts.at(-1).toLowerCase()) && parts[0].toLowerCase() !== "playlist") return " of each tab";
  }
  return "";
}

export const kindLabel = (platform, k) => KINDS[platform]?.[k]?.[0] || k;
export const kindEffect = (platform, k) => KINDS[platform]?.[k]?.[1] || "";

// Today as YYYY-MM-DD in local time, as the backend's date.today().
export function today(now = new Date()) {
  const p = n => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/* The form's state from a set of options (a source's, a preset's):
   { content: [kinds], media, since: "" | date, first: "new" | "last", count: "" | text, schedule,
     script: "" | script id, session: null (the tool's, from Settings) | its own, metadata: "" | mode }.
   Full history is not in it: a one-off, asked from a source's ⋯ menu. */
export function formOf(options, choices) {
  const o = options || {};
  return {
    content: o.content || choices?.content_default || [],
    media: o.media || "all",
    since: o.since || "",
    first: o.first_posts ? "last" : "new",
    count: o.first_posts ? String(o.first_posts) : "",
    schedule: o.schedule || "off",
    script: o.script || "",
    session: o.session || null,
    metadata: o.metadata || "",
  };
}

/* The first problem with the form, or null. */
export function formError(form, choices, now = new Date()) {
  if (choices?.content?.length && !form.content.length) return "Pick at least one thing to download.";
  if (form.since) {
    const d = /^\d{4}-\d{2}-\d{2}$/.test(form.since) && new Date(`${form.since}T00:00:00Z`);
    if (!d || Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== form.since) {
      return "The date must be a day, as YYYY-MM-DD.";
    }
    if (form.since < "1970-01-01" || form.since > today(now)) return "The date must be from 1970-01-01 to today.";
  }
  if (form.first === "last") {
    const n = Number(form.count);
    if (!/^\d+$/.test(form.count) || n < 1 || n > FIRST_POSTS_MAX) {
      return `The number of posts must be from 1 to ${FIRST_POSTS_MAX}.`;
    }
  }
  return null;
}

/* The options a form holds, to send (a preset's, a source's own with
   ``custom``), from a form formError passed. ``firstSync``: the "last N"
   choice is sent (refused once a source has synced). */
export function optionsOf(form, choices, firstSync = true) {
  const out = { media: choices?.media ? form.media : "all", since: form.since || null, schedule: form.schedule,
                script: form.script || null, session: form.session || null, metadata: form.metadata || null };
  if (choices?.content?.length) out.content = choices.content.filter(k => form.content.includes(k));
  if (firstSync) out.first_posts = form.first === "last" ? Number(form.count) : null;
  return out;
}

/* Whether two forms would save the same options (a box ticked off and on
   again is no change). */
export const sameOptions = (a, b, choices, firstSync = true) =>
  JSON.stringify(optionsOf(a, choices, firstSync)) === JSON.stringify(optionsOf(b, choices, firstSync));

/* The form after a content box was clicked. Stories last 24 h: turning
   them on makes an off schedule daily, as the backend does; the user can
   change it after. */
export function toggleKind(form, k) {
  const on = !form.content.includes(k);
  const content = on ? [...form.content, k] : form.content.filter(x => x !== k);
  return { ...form, content, ...(on && k === "stories" && form.schedule === "off" ? { schedule: "daily" } : {}) };
}

/* The kinds picked that need a logged-in session, when the session is
   none: their labels, else []. ``session``: the one a sync would use
   as saved; the form's own, once picked, comes first. */
export function needsLogin(form, choices, session) {
  session = form.session || session;
  if (session?.mode && session.mode !== "none") return [];
  return (choices?.login || []).filter(k => form.content.includes(k));
}

/* A script's entry on the Scripts page (its element's id, and the link to
   it): ids are [a-z0-9_-], a built-in's "builtin:<name>"; ":" becomes ".",
   which no file's id holds. */
export const scriptAnchor = id => `script-${String(id).replace(/[^a-z0-9_-]/gi, ".")}`;
export const scriptHref = id => `/scripts#${scriptAnchor(id)}`;

/* One line on a set of options, a source's ({ tool, platform, target,
   options, choices }) or a preset's: "posts, reels · images only · since
   2024-01-01 · daily", or "script my-insta" when a script runs instead of
   the tool's command. Only what differs from the built-in choices (the
   kinds always, where there are some: the platform's own when none is
   set); "" when nothing does. */
export function optionsSummary(s) {
  const o = s.options || {};
  const content = o.content || (s.choices?.content?.length ? s.choices.content_default : null);
  const parts = [];
  if (o.script) parts.push(`script ${o.script}`);
  else {
    if (content?.length) parts.push(content.map(k => kindLabel(s.platform, k)).join(", "));
    if (o.media && o.media !== "all") parts.push(`${o.media} only`);
    if (o.since) parts.push(`since ${o.since}`);
    if (o.first_posts) parts.push(`first sync: last ${o.first_posts} posts${firstPostsEach(s.tool, content, s.platform, s.target)}`);
    else if (o.full_history) parts.push("full history");
    if (o.session) parts.push(o.session.mode === "none" ? "no login" : o.session.mode === "cookies" ? `${o.session.browser} login` : `session of ${o.session.user}`);
    if (o.metadata) parts.push(`metadata ${o.metadata}`);
  }
  if (o.schedule && o.schedule !== "off") parts.push(o.schedule);
  return parts.join(" · ");
}

/* The preset select (docs/API.md "Presets"): what a source follows. Its
   values: "" (Default), a named preset's id, PRESET_CUSTOM (its own
   options) and PRESET_ONLY (profile only: never downloaded). */
// A named preset's limits (sources.py PRESETS_MAX, PRESET_NAME_MAX).
export const PRESETS_MAX = 20;
export const PRESET_NAME_MAX = 40;
export const PRESET_CUSTOM = "custom";
export const PRESET_ONLY = "only";

/* The select's value for a source. Profile only comes first: a source
   that does not download keeps the preset it followed, unused. */
export function presetValue(s) {
  if (s.profile_only) return PRESET_ONLY;
  return s.custom ? PRESET_CUSTOM : s.preset?.id || "";
}

/* What a source follows, in a word. */
export function presetName(s) {
  if (s.profile_only) return "Profile only";
  return s.custom ? "Custom" : s.preset?.name || "Default";
}

/* Downloading turned on for a source that is profile only: "on" when its
   platform is listed in Settings → Profile only, else back to "platform". */
export const downloadOn = platformOnly => (platformOnly ? "on" : "platform");

/* What picking ``value`` in a source's select saves (POST
   /api/sources/<id>): a preset (its own options go), with downloading
   turned on when it was profile only; or download off. null for
   PRESET_CUSTOM: its dialog saves. */
export function presetBody(s, value) {
  if (value === PRESET_CUSTOM) return null;
  if (value === PRESET_ONLY) return { options: { download: "off" } };
  return { preset: value || null, ...(s.profile_only ? { options: { download: downloadOn(s.platform_profile_only) } } : {}) };
}

/* What a Custom form saves: every option as the form has it, the
   source's own from then on; downloading turned on as presetBody does. */
export function customBody(s, form, firstSync = true) {
  return { custom: true, options: { ...optionsOf(form, s.choices, firstSync),
                                    ...(s.profile_only ? { download: downloadOn(s.platform_profile_only) } : {}) } };
}

/* What puts a source back as it was before a pick (its toast's Undo):
   the preset it followed, or its own options, and its Download. */
export function restoreBody(s) {
  const download = s.options?.download || "platform";
  if (!s.custom) return { preset: s.preset?.id || null, options: { download } };
  const firstSync = !s.synced || s.options.first_posts != null;
  return { custom: true, options: { ...optionsOf(formOf(s.options, s.choices), s.choices, firstSync), download } };
}

/* The options and session a new source would have with ``value`` picked
   (GET /api/sources/resolve): a named preset's, else Default's. */
export function resolvedPreset(resolved, value) {
  const p = (resolved.presets || []).find(x => x.id === value);
  return p ? { options: p.options, session: p.session } : { options: resolved.defaults, session: resolved.session };
}

// "in 40 min", "in 3 h", "in 2 d"
export function fmtUntil(ts, now = Date.now()) {
  const s = Math.max(0, Math.round(ts - now / 1000));
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `in ${Math.round(s / 3600)} h`;
  return `in ${Math.round(s / 86400)} d`;
}

/* One line on a source's schedule, or "" when it has none: "daily · next
   sync in 3 h", "hourly, last failed: rate limited · next try in 2 h".
   ``errors``: last_result.error → its short name. */
export function scheduleText(s, errors = {}, now = Date.now()) {
  const sch = s.schedule;
  if (!sch || sch.every === "off") return "";
  if (sch.stopped) return `${sch.every} · ${sch.stopped}`;
  if (sch.next_at == null) return "";
  if (sch.paused) return `${sch.every} · all schedules paused`;
  if (sch.skipped) return `${sch.every} · ${sch.skipped}`;
  const r = s.last_result;
  const failed = r?.state === "failed" && sch.failures > 0;
  const head = failed ? `${sch.every}, last failed: ${errors[r.error] || "failed"}` : sch.every;
  if (sch.next_at * 1000 <= now) return `${head} · due, starts soon`;
  return `${head} · ${failed ? "next try" : "next sync"} ${fmtUntil(sch.next_at, now)}`;
}

/* scheduleText for a card: "in 3 h", "due", "paused", "stopped" or "skipped". */
export function scheduleShort(s, now = Date.now()) {
  const sch = s.schedule;
  if (sch.stopped) return "stopped";
  if (sch.paused) return "paused";
  if (sch.skipped) return "skipped";
  return sch.next_at * 1000 <= now ? "due" : fmtUntil(sch.next_at, now);
}
