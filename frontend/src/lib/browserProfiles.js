// Which browser profile a cookies session reads (docs/API.md "Browser
// profiles"): the select's lines, and the session it makes.

export const BROWSERS = ["firefox", "chrome", "chromium", "brave", "edge"];

/* The select's lines for a browser: [value, label], "" (the tool picks)
   first, then the names GET /api/browsers/profiles listed. A stored name
   that is not among them stays, marked: its syncs are refused until
   another is picked. ``listed``: null while the list is loading (the
   stored name is then shown as it is). */
export function profileChoices(listed, value) {
  const names = listed || [];
  const out = [["", "Default (the tool picks)"]];
  if (value && !names.includes(value)) out.push([value, listed ? `${value} · not found` : value]);
  return out.concat(names.map(n => [n, n]));
}

/* Whether a stored profile is missing from a loaded list. */
export const profileMissing = (listed, value) => !!(value && listed && !listed.includes(value));

/* The cookies session to save: no "profile" key without one, as before.
   Keys in the order the API answers them (sorted), so a form compares
   equal to what it loaded. */
export function cookiesSession(browser, profile) {
  return profile ? { browser, mode: "cookies", profile } : { browser, mode: "cookies" };
}
