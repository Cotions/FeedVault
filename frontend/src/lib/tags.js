/* Tag names compare without regard to (ASCII) case, as on the backend. */
export const sameTag = (a, b) => a.toLowerCase() === b.toLowerCase();

// A post's tags after an apply, sorted like the API sorts them.
export function withTags(tags = [], add = [], remove = []) {
  const out = tags.filter(t => !remove.some(r => sameTag(r, t)));
  for (const a of add) if (!out.some(t => sameTag(t, a))) out.push(a);
  return out.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
}

// Whether tags pass a tag filter: every one of `wanted`, or none at all.
export function tagsMatch(tags = [], wanted = [], untagged = false) {
  if (untagged && tags.length) return false;
  return wanted.every(w => tags.some(t => sameTag(t, w)));
}

// The tag:name and tag:"two words" terms of a search, as the backend reads them.
const TAG_TERM = /(?<!\S)tag:(?:"([^"]*)"?|(\S*))/gi;
export function searchTags(q = "") {
  return [...q.matchAll(TAG_TERM)].map(m => (m[1] ?? m[2]).split(/\s+/).filter(Boolean).join(" ")).filter(Boolean);
}
