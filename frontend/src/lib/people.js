/* People and accounts (docs/API.md "People"): matching for the search boxes
   and pickers, and the bits of text every page shows the same way. */

// Everything an account is known by: handles old and new, names, its id and
// folder-name aliases. Lowercase, for substring search.
export function accountText(a) {
  return [a.handle, a.name, a.id, ...(a.aliases || []),
          ...(a.handles || []).map(h => h.handle), ...(a.names || []).map(n => n.name)]
    .filter(Boolean).join(" ").toLowerCase();
}

export function personText(p) {
  return [p.name, ...(p.accounts || []).map(accountText)].join(" ").toLowerCase();
}

// Every word of the query must appear (so "alice ig" is not needed, but
// "alice old" finds an account once called alice.old).
export function matches(text, query) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return words.every(w => text.includes(w));
}

// Handles the account used before its current one, most recent first.
export function formerHandles(a) {
  return (a.handles || []).map(h => h.handle).filter(h => h !== a.handle);
}

// The handle the query matched when it is not the current one, for "was @…".
export function matchedFormer(a, query) {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  return formerHandles(a).find(h => h.toLowerCase().includes(q)) || null;
}

export const accountKey = a => `${a.platform}:${a.id}`;
export const accountRef = a => ({ platform: a.platform, id: a.id });

// A default name for a new person: the most used display name, else a handle.
export function suggestName(accounts) {
  const names = {};
  for (const a of accounts) if (a.name) names[a.name] = (names[a.name] || 0) + (a.count || 1);
  const best = Object.entries(names).sort((x, y) => y[1] - x[1])[0]?.[0];
  return (best || accounts[0]?.handle || accounts[0]?.id || "").replace(/"/g, "").slice(0, 64).trim();
}

export const REASONS = {
  bio_link: "Bio link",
  same_handle: "Same handle",
  similar_handle: "Similar handle",
  same_name: "Same name",
};

export function personPath(id) { return `/people/${id}`; }
