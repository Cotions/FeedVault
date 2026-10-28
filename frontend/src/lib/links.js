// Saved links (docs/API.md "Links"): the backend's caps (links.py), so a
// field stops where the server would refuse, and the kinds' labels.
import { plural } from "./fmt.js";

export const MAX_URL = 2048;
export const MAX_TITLE = 300;
export const MAX_NOTES = 5000;

export const KIND_LABEL = { social: "Social", other: "Other" };

// What a link is called in a row and in the buttons' names: its title, else
// its site's label (the server's `label`: Settings → Link labels, or the
// name of a site FeedVault cannot sync), else its address without the scheme.
export const linkLabel = l => l.title || l.label || bareUrl(l.url);

// A link's address without its scheme, as a row shows it.
export const bareUrl = url => url.replace(/^https?:\/\//, "");

// A link in the names of its row's buttons and fields ("Edit …"): its title,
// else its address, which tells two links of one site apart (they share its label).
export const linkName = l => l.title || bareUrl(l.url);

// People's names in a sentence: "Ana", "Ana and Bo", "Ana, Bo and The Band".
export function namesText(names) {
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/* The people a link's Undo gives it back to, as the server's `people`
   ([{ id, name }]) → their ids, by id: one key for links that had the
   same people, so one call takes each group back. */
export const peopleKey = people => (people || []).map(p => p.id).sort((a, b) => a - b).join(",");

// Settings → Link labels: the server's caps (linklabels.py).
export const MAX_LINK_LABEL = 80;
export const MAX_LINK_LABELS = 500;

// Second-level labels under a country code that are not a site of their own (links._SECOND_LEVEL).
const SECOND_LEVEL = new Set(["co", "com", "net", "org", "gov", "ac", "edu", "ne", "or"]);

/* The site a link-label rule is for, as the server will save it
   (linklabels.clean_site, links.site): "https://www.patreon.com/x",
   "www.Patreon.com" and "patreon.com" are all "patreon.com", a subdomain
   is cut to the registrable part ("someone.substack.com" is
   "substack.com"). null when the text names no host. The server decides;
   this is for the form to show what will be saved, and to spot a site given
   twice. */
export function linkSite(text) {
  if (typeof text !== "string" || !text.trim()) return null;
  let url = text.trim();
  if (!url.includes("://")) url = `https://${url.replace(/^\/+/, "")}`;
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return null;
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  if (/^\[.*\]$/.test(host)) return host.slice(1, -1);
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return host;
  const labels = host.split(".");
  if (labels.length < 2 || labels.some(l => !l)) return null;
  const keep = labels.length >= 3 && labels.at(-1).length === 2 && SECOND_LEVEL.has(labels.at(-2)) ? 3 : 2;
  const site = labels.slice(-keep).join(".");
  return site.split(".").every(Boolean) ? site : null;
}

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

/* Why externalUrl refuses ``text`` (null: it does not), to say so where
   an address given from outside was not filled in. */
export function urlProblem(text) {
  if (externalUrl(text) || typeof text !== "string" || !text.trim()) return null;
  const url = text.trim();
  if (url.length > MAX_URL) return `it is longer than ${MAX_URL} characters`;
  if (!/^https?:\/\//i.test(url)) return "it is not an http(s) address";
  // eslint-disable-next-line no-control-regex
  if (/[\s\\\x00-\x1f\x7f]/.test(url)) return "it has a space, a backslash or a control character in it";
  let parsed = null;
  try { parsed = new URL(url); } catch { /* not one address */ }
  if (parsed && (parsed.username || parsed.password)) return "it has a user name or password in it";
  return "it is not a web address";
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

/* What a link-in-bio import kept in a person's Links, in words (the
   endpoint's `links`: { saved, already, shared, dropped, over }), one
   part per count that is not zero. Only numbers are read: not a word of
   it comes from the page. */
export function importedParts(links) {
  const n = k => (Number.isInteger(links?.[k]) && links[k] > 0 ? links[k] : 0);
  return [
    n("saved") ? `${plural(n("saved"), "link")} saved to their Links` : "No new link to save",
    n("already") && `${plural(n("already"), "link")} already there`,
    n("shared") && `${plural(n("shared"), "link")} saved before for someone else or no one, now theirs too`,
    n("dropped") && `${plural(n("dropped"), "link")} dropped (not a web address Links keeps)`,
    n("over") && `${plural(n("over"), "link")} over what one import saves: import again for the rest`,
  ].filter(Boolean);
}
