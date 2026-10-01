export function fmt(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + "K";
  return n.toString();
}

export function fmtInt(n) {
  return n == null ? "—" : Number(n).toLocaleString();
}

export function fmtBytes(bytes) {
  if (bytes == null) return "—";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${i === 0 ? n : n.toFixed(1)} ${units[i]}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* Card dates: "now", "12m", "5h", "3d", then "Mar 4", and "Mar 4, 2023" once
   the year differs. Times are Unix seconds. */
export function fmtShortDate(ts, now = Date.now()) {
  if (ts == null) return "—";
  const d = new Date(ts * 1000);
  const diff = (now - d.getTime()) / 1000;
  if (diff >= 0 && diff < 60)     return "now";
  if (diff >= 0 && diff < 3600)   return `${Math.floor(diff / 60)}m`;
  if (diff >= 0 && diff < 86400)  return `${Math.floor(diff / 3600)}h`;
  if (diff >= 0 && diff < 604800) return `${Math.floor(diff / 86400)}d`;
  const label = `${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return d.getFullYear() === new Date(now).getFullYear() ? label : `${label}, ${d.getFullYear()}`;
}

export function fmtFullDate(ts) {
  if (ts == null) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    weekday: "short", year: "numeric", month: "long", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function fmtIso(ts) {
  if (ts == null) return "";
  return new Date(ts * 1000).toISOString();
}

export function fmtAgo(ts, now = Date.now()) {
  if (ts == null) return "never";
  const s = Math.max(0, Math.round(now / 1000 - ts));
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/* Post URLs come from downloader metadata, i.e. from the platform. React
   renders a javascript: href as-is, so only web schemes get through. */
export function safeUrl(url, schemes = ["http:", "https:"]) {
  if (!url) return undefined;
  try {
    const u = new URL(String(url));
    return schemes.includes(u.protocol) ? u.href : undefined;
  } catch {
    return undefined;
  }
}

const PLATFORMS = { instagram: "Instagram", twitter: "X", x: "X", tiktok: "TikTok", youtube: "YouTube" };
export function platformLabel(p) {
  if (!p) return "—";
  return PLATFORMS[p] || p.charAt(0).toUpperCase() + p.slice(1);
}

const SHORT = { instagram: "IG", twitter: "X", x: "X", tiktok: "TT", youtube: "YT" };
export function platformShort(p) {
  return SHORT[p] || (p || "").slice(0, 2).toUpperCase();
}

/* What a post's album is: an Instagram highlight title, or a note such as
   "Retweeted by @someone" for posts from gallery-dl. */
export function albumLabel(p) {
  return p === "instagram" ? "Highlight" : "Note";
}

export const KINDS = ["image", "video", "carousel", "story", "text"];

/* First words of a post, for alt text and titles. */
export function excerpt(text, max = 120) {
  const t = (text || "").replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  return t.slice(0, max - 1).replace(/\s+\S*$/, "") + "…";
}

export function postPath(post) {
  return `/p/${encodeURIComponent(post.platform)}/${encodeURIComponent(post.post_id)}`;
}

/* Feed link filtered to one author. Author ids are only unique within a
   platform, so the platform travels with the id. */
export function authorFeedPath(platform, author) {
  const s = new URLSearchParams();
  if (author?.id != null) {
    if (platform) s.set("platform", platform);
    s.set("author", author.id);
  } else if (author?.handle) {
    s.set("q", author.handle);
  }
  return `/?${s}`;
}

/* "2026-09-28 22:17", local time. Table cells where a relative "4m" would read
   as months as easily as minutes. */
export function fmtStamp(ts) {
  if (ts == null) return "—";
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// The feed filtered by one tag.
export function tagFeedPath(name) {
  return `/?${new URLSearchParams({ tag: name })}`;
}
