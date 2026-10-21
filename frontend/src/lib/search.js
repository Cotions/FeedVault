import { createContext, useContext, useEffect } from "react";
import { accountText, matches } from "./people.js";

/* The header's search box searches the page it is on, when that page has
   something to search (as ChannelVault's does): Creators (people, accounts
   and links), a person's page (their accounts, sources, links and posts) and
   Links. Each keeps the search in its own `?q=`, so reload and Back restore
   it. Anywhere else the box searches the Feed, as it always did: typing
   there opens the Feed with `?q=`.

   searchScope(pathname): { placeholder, label } for a page that searches
   itself (the Feed included), null for one that sends a search to the Feed.
   A person's placeholder names them once their page says who they are
   (useSearchName). */

export const FEED_SCOPE = {
  feed: true,
  placeholder: "Search captions, authors, tag:name…",
  label: "Search posts",
};

const SCOPES = [
  [/^\/$/, FEED_SCOPE],
  [/^\/creators$/, { placeholder: "Search creators, accounts, links…", label: "Search creators, accounts and links" }],
  [/^\/links$/, { placeholder: "Search links…", label: "Search links" }],
  [/^\/people\/[^/]+$/, { person: true, placeholder: "Search their accounts, links…", label: "Search this person's accounts, sources and links" }],
];

export function searchScope(pathname) {
  return SCOPES.find(([re]) => re.test(pathname))?.[1] || null;
}

// The words the box shows for a scope; ``name`` is the person's, once known.
export function scopeText(scope, name) {
  if (!scope) return FEED_SCOPE;
  if (scope.person && name) {
    return { placeholder: `Search ${name}'s accounts, links…`, label: `Search ${name}'s accounts, sources and links` };
  }
  return scope;
}

// What /api/links takes as `q` (docs/API.md "Links": at most 200 characters).
export const LINKS_Q_MAX = 200;

/* What a search finds, every word of it somewhere (lib/people.js matches):
   a person by name or label, an account by any handle, name, id or folder
   it was known by, a source by its target or address, a link by its
   address, title or notes. */
export const personMatches  = (p, q) => matches([p.name, ...(p.labels || [])].join(" ").toLowerCase(), q);
export const accountMatches = (a, q) => matches(accountText(a), q);
export const sourceMatches  = (s, q) => matches([s.target, s.url, s.tool].filter(Boolean).join(" ").toLowerCase(), q);
export const linkMatches    = (l, q) => matches([l.url, l.title, l.notes].filter(Boolean).join(" ").toLowerCase(), q);

/* The Creators page's search: { people, accounts } that match, in the
   order the page lists them (people as given, accounts most posts first,
   linked to someone or not). Links come from /api/links?q=. */
export function searchCreators(people, accounts, q) {
  if (!q.trim()) return { people: [], accounts: [] };
  return {
    people: (people || []).filter(p => personMatches(p, q)),
    accounts: (accounts || []).filter(a => accountMatches(a, q)),
  };
}

/* Provided by App: { setName(name|null), reset() }. reset() puts the box
   back to the page's `?q=` (a search a page refused: the Links page's
   "Keep editing"). */
export const SearchContext = createContext(null);

export function useHeaderSearch() {
  return useContext(SearchContext);
}

// A person's page names them in the box's placeholder while it is shown.
export function useSearchName(name) {
  const ctx = useContext(SearchContext);
  const setName = ctx?.setName;
  useEffect(() => {
    if (!setName) return undefined;
    setName(name || null);
    return () => setName(null);
  }, [setName, name]);
}
