// What a source downloads (docs/API.md "What a source downloads"): the
// names and one-line effects the form shows, the row's summary, and the
// checks the form makes before the backend makes them again.

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

/* The form's state from a source's stored options (or the defaults):
   { content: [kinds], media, since: "" | date, first: "new" | "full" | "last", count: "" | text, schedule } */
export function formOf(options, choices) {
  const o = options || {};
  return {
    content: o.content || choices?.content_default || [],
    media: o.media || "all",
    since: o.since || "",
    first: o.first_posts ? "last" : o.full_history ? "full" : "new",
    count: o.first_posts ? String(o.first_posts) : "",
    schedule: o.schedule || "off",
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

/* The options to send (POST /api/sources, POST /api/sources/<id>), from a
   form formError passed. ``firstSync``: the first-sync choice is sent
   (only last_posts is refused once a source has synced). */
export function optionsOf(form, choices, firstSync = true) {
  const out = { media: choices?.media ? form.media : "all", since: form.since || null, schedule: form.schedule };
  if (choices?.content?.length) out.content = choices.content.filter(k => form.content.includes(k));
  out.full_history = form.first === "full";
  if (firstSync) out.first_posts = form.first === "last" ? Number(form.count) : null;
  return out;
}

/* The form after a content box was clicked. Stories last 24 h: turning
   them on makes an off schedule daily, as the backend does; the user can
   change it after. */
export function toggleKind(form, k) {
  const on = !form.content.includes(k);
  const content = on ? [...form.content, k] : form.content.filter(x => x !== k);
  return { ...form, content, ...(on && k === "stories" && form.schedule === "off" ? { schedule: "daily" } : {}) };
}

/* The kinds picked that need a logged-in session, when the session is
   none: their labels, else []. */
export function needsLogin(form, choices, session) {
  if (session?.mode && session.mode !== "none") return [];
  return (choices?.login || []).filter(k => form.content.includes(k));
}

/* One line for a source's row: "posts, reels · images · since 2024-01-01".
   Only what differs from the defaults; "" when nothing does. */
export function optionsSummary(s) {
  const o = s.options || {};
  const parts = [];
  if (o.content) parts.push(o.content.map(k => kindLabel(s.platform, k)).join(", "));
  if (o.media && o.media !== "all") parts.push(`${o.media} only`);
  if (o.since) parts.push(`since ${o.since}`);
  if (o.first_posts) parts.push(`first sync: last ${o.first_posts} posts${firstPostsEach(s.tool, o.content, s.platform, s.target)}`);
  else if (o.full_history) parts.push("full history");
  return parts.join(" · ");
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
  if (!sch || sch.every === "off" || sch.next_at == null) return "";
  if (sch.paused) return `${sch.every} · all schedules paused`;
  if (sch.skipped) return `${sch.every} · ${sch.skipped}`;
  const r = s.last_result;
  const failed = r?.state === "failed" && sch.failures > 0;
  const head = failed ? `${sch.every}, last failed: ${errors[r.error] || "failed"}` : sch.every;
  if (sch.next_at * 1000 <= now) return `${head} · due, starts soon`;
  return `${head} · ${failed ? "next try" : "next sync"} ${fmtUntil(sch.next_at, now)}`;
}

/* scheduleText for a card: "in 3 h", "due", "paused" or "skipped". */
export function scheduleShort(s, now = Date.now()) {
  const sch = s.schedule;
  if (sch.paused) return "paused";
  if (sch.skipped) return "skipped";
  return sch.next_at * 1000 <= now ? "due" : fmtUntil(sch.next_at, now);
}
