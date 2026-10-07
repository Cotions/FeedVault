// Saved links (docs/API.md "Links"): the backend's caps (links.py), so a
// field stops where the server would refuse, and the kinds' labels.
export const MAX_URL = 2048;
export const MAX_TITLE = 300;
export const MAX_NOTES = 5000;

export const KIND_LABEL = { social: "Social", other: "Other" };

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
