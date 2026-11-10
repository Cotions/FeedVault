// Checks for lib/personProfiles.js: a person's accounts and sources as one
// list of profiles, and what the header's search keeps of it. Run with
// `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { personProfiles, profileMatches, syncs } from "../src/lib/personProfiles.js";

const account = (platform, id, handle) => ({ platform, id, handle });
const source = (id, target, of = null, more = {}) => ({
  id, tool: "gallery-dl", target, url: target, account: of && { platform: of.platform, id: of.id }, ...more,
});

test("an account and its source are one profile; a source with no account of theirs is its own", () => {
  const ada = account("instagram", "1", "ada");
  const adaX = account("twitter", "9", "ada_x");
  const bare = account("tiktok", "5", "ada.tt");
  const sources = [
    source(11, "https://x.com/ada_x/media", adaX),
    source(12, "ada", ada, { tool: "instaloader" }),
    source(13, "https://x.com/ada_x", adaX),
    source(14, "https://bsky.app/profile/ada"),                       // never synced: no account yet
    source(15, "https://x.com/gone", account("twitter", "404", "gone")), // its account is not theirs
  ];
  const rows = personProfiles([ada, adaX, bare], sources);
  assert.deepEqual(rows.map(r => r.key), ["instagram:1", "twitter:9", "tiktok:5", "source:14", "source:15"]);
  assert.deepEqual(rows.map(r => r.sources.map(s => s.id)), [[12], [11, 13], [], [14], [15]]);
  assert.equal(rows[0].account, ada);
  assert.equal(rows[3].account, null);
  assert.equal(rows[4].account, null);
});

test("nobody's: no profile; accounts alone: a profile each, no source", () => {
  assert.deepEqual(personProfiles([], []), []);
  assert.deepEqual(personProfiles(undefined, undefined), []);
  const rows = personProfiles([account("instagram", "1", "ada")], []);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].sources, []);
});

test("the search keeps a profile whose account or any source matches", () => {
  const adaX = { ...account("twitter", "9", "ada_x"), name: "Ada", handles: [{ handle: "old_ada" }] };
  const [row] = personProfiles([adaX], [source(11, "https://x.com/ada_x/media", adaX), source(13, "https://x.com/ada_x", adaX)]);
  assert.ok(profileMatches(row, "ada_x"));
  assert.ok(profileMatches(row, "old_ada"), "a former handle");
  assert.ok(profileMatches(row, "media"), "one of its sources");
  assert.ok(profileMatches(row, "gallery-dl"), "a source's tool");
  assert.ok(!profileMatches(row, "tiktok"));
  const [alone] = personProfiles([], [source(14, "https://bsky.app/profile/ada")]);
  assert.ok(profileMatches(alone, "bsky"));
  assert.ok(!profileMatches(alone, "ada_x"));
});

test("Sync and Sync all run what is neither profile only nor set to Don't sync", () => {
  assert.ok(syncs({ id: 1 }));
  assert.ok(!syncs({ id: 1, profile_only: true }));
  assert.ok(!syncs({ id: 1, no_sync: true }));
});
