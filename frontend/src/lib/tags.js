/* Tag names compare without regard to case, of ASCII letters only, as on
   the backend (SQLite NOCASE): "Été" and "été" are two tags. */
export const foldTag = s => s.replace(/[A-Z]/g, c => c.toLowerCase());
export const sameTag = (a, b) => foldTag(a) === foldTag(b);

// A name as the backend stores it: spaces collapsed.
export const cleanName = s => s.split(/\s+/).filter(Boolean).join(" ");

// A post's tags after an apply, sorted like the API sorts them.
export function withTags(tags = [], add = [], remove = []) {
  const out = tags.filter(t => !remove.some(r => sameTag(r, t)));
  for (const a of add) if (!out.some(t => sameTag(t, a))) out.push(a);
  return out.sort((a, b) => foldTag(a).localeCompare(foldTag(b)));
}

// Whether tags pass a tag filter: every one of `wanted`, or none at all.
export function tagsMatch(tags = [], wanted = [], untagged = false) {
  if (untagged && tags.length) return false;
  return wanted.every(w => tags.some(t => sameTag(t, w)));
}

// The tag:name and tag:"two words" terms of a search, as the backend reads them.
const TAG_TERM = /(?<!\S)tag:(?:"([^"]*)"?|(\S*))/gi;
export function searchTags(q = "") {
  return [...q.matchAll(TAG_TERM)].map(m => cleanName(m[1] ?? m[2])).filter(Boolean);
}
