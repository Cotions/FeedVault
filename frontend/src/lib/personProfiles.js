/* A person's profiles, the one list of their page: each account of theirs
   with the sources that are of it (most have one; two tabs of an X profile
   are two), then each source of theirs with no account among them (never
   synced yet, or a site no downloader takes), on a row of its own. */
import { accountKey } from "./people.js";
import { accountMatches, sourceMatches } from "./search.js";

/* [{ key, account | null, sources: [...] }], the accounts in the person's
   order, then the sources left. ``sources``: theirs (source.person is them). */
export function personProfiles(accounts, sources) {
  const byAccount = new Map((accounts || []).map(a => [accountKey(a), []]));
  const alone = [];
  for (const s of sources || []) {
    const of = s.account && byAccount.get(accountKey(s.account));
    if (of) of.push(s); else alone.push(s);
  }
  return [
    ...(accounts || []).map(a => ({ key: accountKey(a), account: a, sources: byAccount.get(accountKey(a)) })),
    ...alone.map(s => ({ key: `source:${s.id}`, account: null, sources: [s] })),
  ];
}

// The header's search: a profile shows when its account or any of its sources matches.
export function profileMatches(profile, q) {
  return (!!profile.account && accountMatches(profile.account, q)) || profile.sources.some(s => sourceMatches(s, q));
}

// The sources Sync and Sync all run: not profile only, not set to Don't sync.
export const syncs = s => !s.profile_only && !s.no_sync;
