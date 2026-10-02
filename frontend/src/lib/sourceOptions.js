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
  ["all", "images and videos", "everything the posts have"],
  ["images", "images only", "videos are skipped"],
  ["videos", "videos only", "images are skipped"],
];

export const FIRST_POSTS_MAX = 10000;

export const kindLabel = (platform, k) => KINDS[platform]?.[k]?.[0] || k;
export const kindEffect = (platform, k) => KINDS[platform]?.[k]?.[1] || "";

// Today as YYYY-MM-DD in local time, as the backend's date.today().
export function today(now = new Date()) {
  const p = n => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/* The form's state from a source's stored options (or the defaults):
   { content: [kinds], media, since: "" | date, first: "new" | "full" | "last", count: "" | text } */
export function formOf(options, choices) {
  const o = options || {};
  return {
    content: o.content || choices?.content_default || [],
    media: o.media || "all",
    since: o.since || "",
    first: o.first_posts ? "last" : o.full_history ? "full" : "new",
    count: o.first_posts ? String(o.first_posts) : "",
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
  const out = { media: choices?.media ? form.media : "all", since: form.since || null };
  if (choices?.content?.length) out.content = choices.content.filter(k => form.content.includes(k));
  out.full_history = form.first === "full";
  if (firstSync) out.first_posts = form.first === "last" ? Number(form.count) : null;
  return out;
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
  if (o.first_posts) parts.push(`first sync: last ${o.first_posts} posts`);
  else if (o.full_history) parts.push("full history");
  return parts.join(" · ");
}
