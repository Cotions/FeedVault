/* People and accounts (docs/API.md "People"): matching for the search boxes
   and pickers, and the bits of text every page shows the same way. */
import { ratingMatch } from "./rating.js";

// Everything an account is known by: handles old and new, names, its id and
// folder-name aliases. Lowercase, for substring search.
export function accountText(a) {
  return [a.handle, a.name, a.id, ...(a.aliases || []),
          ...(a.handles || []).map(h => h.handle), ...(a.names || []).map(n => n.name)]
    .filter(Boolean).join(" ").toLowerCase();
}

// A person's name, accounts, gender, country (by name) and labels.
export function personText(p) {
  return [p.name, p.gender, countryName(p.country), ...(p.labels || []), ...(p.accounts || []).map(accountText)]
    .filter(Boolean).join(" ").toLowerCase();
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

// A person card's accounts on one line: each in a chip of its own while
// they fit; past that, one chip per platform (its accounts in a list: the
// card shows "×n" for more than one) in the order they first come, the
// first few of them, and the accounts of the rest for a "+n" chip. The
// person page lists them all.
export const ACCOUNTS_SHOWN = 3;
export function accountChips(accounts, shown = ACCOUNTS_SHOWN) {
  if (accounts.length <= shown) return { chips: accounts.map(a => [a]), rest: [] };
  const byPlatform = new Map();
  for (const a of accounts) byPlatform.set(a.platform, [...(byPlatform.get(a.platform) || []), a]);
  const groups = [...byPlatform.values()];
  return { chips: groups.slice(0, shown), rest: groups.slice(shown).flat() };
}
export const accountRef = a => ({ platform: a.platform, id: a.id });

// What a merge is of (its people and accounts), in any order: the key its
// Don't sync warning is kept under.
export const mergeSet = (people, accounts) =>
  [...people.map(p => `person:${p.id}`), ...accounts.map(accountKey)].sort().join(" ");

// How many sources the merge's Don't sync warning said would stop, while it
// is still about the same set (else 0: it is asked again).
export const mergeStopping = (merge, set) => (merge?.stoppingFor === set && merge.stopping) || 0;

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

/* A link's person picker (components/PersonPicker.jsx). */
const MAX_PICK = 200;
export const NO_PERSON = { key: "none", person: null };

/* The options for ``query``: with none typed, "No person" first, then the
   people most recently given a link (``recent``, [{ id, name }]), then
   everyone else by name; typed, the people it matches (the recent ones
   first), and "No person" last, so it is always there. With ``none``
   false (assigning an Unsorted link: no one is where it is already), no
   "No person" at all. */
export function personOptions(people, recent, query, { none = true } = {}) {
  const out = withNone(people, recent, query);
  return none ? out : out.filter(o => o !== NO_PERSON);
}

function withNone(people, recent, query) {
  const all = people || [];
  const byId = new Map(all.map(p => [p.id, p]));
  // A recent person the full list does not have yet (still loading): the name is enough.
  const top = (recent || []).map(r => byId.get(r.id) || { id: r.id, name: r.name, accounts: [] });
  const topIds = new Set(top.map(p => p.id));
  const rest = all.filter(p => !topIds.has(p.id)).sort((a, b) => a.name.localeCompare(b.name));
  const opt = (p, recentOne) => ({ key: `person:${p.id}`, person: p, recent: recentOne });
  if (!query.trim()) {
    return [NO_PERSON, ...top.map(p => opt(p, true)), ...rest.map(p => opt(p, false))].slice(0, MAX_PICK);
  }
  const hit = p => matches(personText(p), query);
  return [...top.filter(hit).map(p => opt(p, true)), ...rest.filter(hit).map(p => opt(p, false))]
    .slice(0, MAX_PICK - 1).concat(NO_PERSON);
}

/* ── Gender, country and labels (docs/API.md "People") ────── */

// Quick choices; any other gender is kept as typed (the backend stores one
// typed in another case as the preset).
export const GENDER_PRESETS = ["Female", "Male", "Transgender"];
export const MAX_GENDER = 40;

// Genders and labels compare as on the backend (SQLite NOCASE: ASCII letters only).
const fold = s => (s || "").replace(/[A-Z]/g, c => c.toLowerCase());

// A gender as the backend stores it: spaces collapsed, a preset in its own case.
export function cleanGender(g) {
  const clean = (g || "").split(/\s+/).filter(Boolean).join(" ");
  return GENDER_PRESETS.find(x => x.toLowerCase() === clean.toLowerCase()) || clean;
}

// The flag of an ISO 3166-1 alpha-2 code: its two regional indicator letters.
export function countryFlag(code) {
  if (!/^[A-Z]{2}$/.test(code || "")) return "";
  return String.fromCodePoint(...[...code].map(c => 0x1F1A5 + c.charCodeAt(0)));
}

// The country's name in the browser's language, else its code.
let regions;
export function countryName(code) {
  if (!code) return "";
  try {
    regions ||= new Intl.DisplayNames(undefined, { type: "region" });
    return regions.of(code) || code;
  } catch {
    return code;
  }
}

/* The Creators filters: each null for any, "" for "not set", else the
   value wanted (a label: one of theirs). */
/* sync: null (any), "on" (synced) or "off" (set to Don't sync, see
   notSynced). rating: null (any), "" (not rated) or "1" to "10" (rated
   at least that: lib/rating.js). The others: a person's attributes, which
   an account linked to nobody has none of. */
export const NO_FILTERS = { gender: null, country: null, label: null, rating: null, sync: null };
export const attributeFiltersOn = f => f.gender != null || f.country != null || f.label != null || f.rating != null;
export const filtersOn = f => attributeFiltersOn(f) || f.sync != null;
export const SYNC_FILTERS = ["on", "off"];
export const syncFilter = v => (SYNC_FILTERS.includes(v) ? v : null);

/* Set to Don't sync (docs/API.md "Don't sync"): a person or an account
   itself, or a person each of whose accounts is. */
export function notSynced(row) {
  return !!row.no_sync || (row.accounts?.length > 0 && row.accounts.every(a => a.no_sync));
}

// A person, or an account linked to nobody, against the sync filter.
export const syncMatch = (row, sync) => sync == null || (sync === "off") === notSynced(row);

export function attributesMatch(p, f) {
  if (!syncMatch(p, f.sync)) return false;
  if (!ratingMatch(p, f.rating)) return false;
  if (f.gender != null && fold(p.gender) !== fold(f.gender)) return false;
  if (f.country != null && (p.country || "") !== f.country) return false;
  if (f.label === "") return !(p.labels || []).length;
  if (f.label != null && !(p.labels || []).some(l => fold(l) === fold(f.label))) return false;
  return true;
}

/* What the filters offer, from the people listed: [{ value, count }] of
   each (genders and labels as first written, most used first; countries
   by name), and how many have none of each. */
export function attributeChoices(people) {
  const count = (values, key = fold) => {
    const m = new Map();
    for (const v of values) {
      const had = m.get(key(v));
      m.set(key(v), { value: had?.value ?? v, count: (had?.count || 0) + 1 });
    }
    return [...m.values()].sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
  };
  const none = has => people.filter(p => !has(p)).length;
  return {
    genders: count(people.map(p => p.gender).filter(Boolean)),
    countries: count(people.map(p => p.country).filter(Boolean), v => v)
      .sort((a, b) => countryName(a.value).localeCompare(countryName(b.value))),
    labels: count(people.flatMap(p => p.labels || [])),
    none: { gender: none(p => p.gender), country: none(p => p.country), label: none(p => p.labels?.length),
            rating: none(p => p.rating) },
    // How many are rated at least n, for n from 1 to 10 (index n - 1).
    ratedAtLeast: Array.from({ length: 10 }, (_, i) => people.filter(p => (p.rating || 0) > i).length),
  };
}

// The labels with one more (one they have already, in any case, is not added twice).
export function withLabel(labels, name) {
  return labels.some(l => fold(l) === fold(name)) ? labels : [...labels, name];
}

// The same labels, as typed (one retyped in another case is a change to save).
export const sameLabels = (a = [], b = []) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());

/* The countries for a picker matching ``query`` (a name or a code), by
   name, an exact code first, then names starting with it: [{ code, name, flag }]. */
export function countryOptions(codes, query = "") {
  const q = query.trim().toLowerCase();
  const rank = c => !q ? 0 : c.code.toLowerCase() === q ? 2 : c.name.toLowerCase().startsWith(q) ? 1 : 0;
  return codes.map(code => ({ code, name: countryName(code), flag: countryFlag(code) }))
    .filter(c => !q || c.code.toLowerCase() === q || c.name.toLowerCase().includes(q))
    .sort((a, b) => rank(b) - rank(a) || a.name.localeCompare(b.name));
}
