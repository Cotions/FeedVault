// Saved links (docs/API.md "Links"): the backend's caps (links.py), so a
// field stops where the server would refuse, and the kinds' labels.
import { plural } from "./fmt.js";

export const MAX_URL = 2048;
export const MAX_TITLE = 300;
export const MAX_NOTES = 5000;

export const KIND_LABEL = { social: "Social", other: "Other" };

// What a link is called in a row and in the buttons' names: its title, else its address without the scheme.
export const linkLabel = l => l.title || l.url.replace(/^https?:\/\//, "");

/* The address to send for what was typed: "example.org/x" (no scheme) is
   https://example.org/x, as a browser's address bar reads it. A typed
   scheme stays, any (http:, javascript:, mailto:), for the server to take
   or refuse; so does text that names no host. */
export function withScheme(text) {
  const url = text.trim();
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) return url;
  if (url.startsWith("//")) return `https:${url}`;
  // "mailto:x", "javascript:x": a scheme. "example.org:8080", "localhost:8080": a host and port.
  const scheme = /^([a-z][a-z0-9+.-]*):(.?)/i.exec(url);
  if (scheme && !scheme[1].includes(".") && !/\d/.test(scheme[2])) return url;
  const host = url.split(/[/?#]/, 1)[0];
  const named = host.includes(".") || /:\d+$/.test(host) || /^localhost$/i.test(host) || host.startsWith("[");
  if (/\s/.test(url) || !named) return url;
  return `https://${url}`;
}

/* The web address in text from outside the app (the clipboard, a drop), or
   null: one http(s) address with a host and nothing else, as the server
   would take it (links.clean_url: no user name, backslash, space or control
   character inside, at most MAX_URL characters). Text that only looks like
   a host ("notes.txt") is not one: nothing is guessed from what was not
   typed. */
export function externalUrl(text) {
  if (typeof text !== "string") return null;
  const url = text.trim();
  if (!url || url.length > MAX_URL || !/^https?:\/\//i.test(url)) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\s\\\x00-\x1f\x7f]/.test(url)) return null;
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) return null;
  return url;
}

/* The links' addresses, one per line, in the order given: what "Copy URLs"
   puts on the clipboard, to paste into another app. */
export function urlLines(links) {
  return (links || []).map(l => l.url).join("\n");
}

// Copy URLs: the addresses on the clipboard, and a toast that says so (or why not).
export async function copyUrls(links, toast) {
  try {
    await navigator.clipboard.writeText(urlLines(links));
    toast(`Copied ${plural(links.length, "link")}`);
  } catch (e) {
    toast(`Could not copy the links: ${e.message}`, "err");
  }
}

// Whether a drag may carry a web address, from its types alone (its data
// is only readable on drop): a link (text/uri-list) or text.
export function mayCarryUrl(types) {
  const list = [...(types || [])];
  return list.includes("text/uri-list") || list.includes("text/plain");
}

/* The address a drop carries, or null: the first entry of its text/uri-list
   (lines starting with # are comments), else its text, if either is one
   web address (externalUrl). ``get(type)`` reads the drop's data. */
export function droppedUrl(get) {
  const first = (get("text/uri-list") || "").split(/\r?\n/).map(l => l.trim()).find(l => l && !l.startsWith("#"));
  return externalUrl(first) || externalUrl(get("text/plain"));
}

/* A page's title as given to /links/add (the bookmarklet's document.title):
   plain text, its whitespace and control characters collapsed to single
   spaces, at most MAX_TITLE characters (never half of a surrogate pair). */
export function sharedTitle(text) {
  if (typeof text !== "string") return "";
  // eslint-disable-next-line no-control-regex
  let title = text.replace(/[\s\x00-\x1f\x7f]+/g, " ").trim();
  if (title.length > MAX_TITLE) title = title.slice(0, MAX_TITLE).replace(/[\uD800-\uDBFF]$/, "").trimEnd();
  return title;
}
