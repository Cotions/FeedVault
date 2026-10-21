// lib/api.js builds /api paths from route ids, which come from the page's
// own URL; none of them may step out of its endpoint. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as api from "../src/lib/api.js";

const { getPerson, getCollection, getJob, deleteSource, ApiError } = api;

function stubFetch() {
  const seen = [];
  globalThis.fetch = async (url) => {
    seen.push(url);
    return new Response("{}", { headers: { "content-type": "application/json" } });
  };
  return seen;
}

test("a route id cannot reach another endpoint", async () => {
  // /people/..%2Fbrowse, /people/%252e%252e%2Fbrowse, /collections/.%2F..%2Fbrowse, ...
  const cases = [[() => getPerson("../browse"), "/api/people/"], [() => getPerson("%2e%2e/browse"), "/api/people/"],
                 [() => getPerson(".."), "/api/people/"], [() => getCollection("./../browse"), "/api/collections/"],
                 [() => getJob("../../api/browse"), "/api/jobs/"], [() => getPerson("%2E%2e/jobs?desktop=1"), "/api/people/"],
                 [() => getPerson("."), "/api/people/"]];
  for (const [call, prefix] of cases) {
    const seen = stubFetch();
    const sent = await call().then(() => true, e => {
      assert.ok(e instanceof ApiError && e.status === 404);
      return false;
    });
    // Refused before fetch, or sent as one segment under its endpoint.
    assert.equal(seen.length, sent ? 1 : 0);
    for (const u of seen) {
      const url = new URL(u, "http://h");
      assert.ok(url.pathname.startsWith(prefix) && !url.pathname.slice(prefix.length).includes("/"), u);
      assert.equal(url.pathname, u.split("?")[0], u);           // the browser sends it as built
      assert.equal(url.search, "", u);
    }
  }
  // A DELETE answers like a POST: the error comes back as data, nothing sent.
  const seen = stubFetch();
  assert.deepEqual(await deleteSource(".."), { ok: false, error: "no such page" });
  assert.deepEqual(seen, []);
});

test("ordinary ids and query strings are sent", async () => {
  const seen = stubFetch();
  await getPerson(12);
  await getCollection("3", { offset: 0, limit: 60 });
  // "/", "?" and "#" in an id stay in its segment (the backend's <int:> then 404s).
  await getPerson("5/accounts");
  await getCollection("5#", { offset: 60 });
  assert.deepEqual(seen, ["/api/people/12", "/api/collections/3?offset=0&limit=60",
                          "/api/people/5%2Faccounts", "/api/collections/5%23?offset=60"]);
  assert.ok(api.sentAsIs("/api/posts/x/a%2F..%2Fb"));          // encoded slashes stay one segment
  assert.ok(api.sentAsIs("/api/sources/resolve?url=https%3A%2F%2Fx.com%2F..%2Fa"));
  assert.ok(!api.sentAsIs("/api/people/1/../../browse"));
});

test("a DELETE can carry a JSON body (restoring a dismissed duplicate)", async () => {
  const sent = [];
  globalThis.fetch = async (url, opts) => {
    sent.push([url, opts.method, opts.headers["Content-Type"], opts.body]);
    return new Response('{"ok":false,"error":"no such dismissal; reload"}',
                        { status: 404, headers: { "content-type": "application/json" } });
  };
  assert.deepEqual(await api.undismissDuplicate("4c1d"), { ok: false, error: "no such dismissal; reload" });
  await assert.rejects(api.getDismissedDuplicates("copies"), e => e instanceof ApiError && e.status === 404);
  assert.deepEqual(sent, [["/api/duplicates/dismiss", "DELETE", "application/json", '{"group":"4c1d"}'],
                          ["/api/duplicates/dismissed?kind=copies", "GET", undefined, undefined]]);
});

test("deletes and restores say which page asked, for the trash's History", async () => {
  const sent = [];
  globalThis.fetch = async (url, opts) => {
    sent.push([url, opts?.body && JSON.parse(opts.body)]);
    return new Response('{"ok":true}', { headers: { "content-type": "application/json" } });
  };
  await api.deleteItems({ posts: ["instagram:P1"], from: "review" });
  await api.deleteItems({ media: [3] });
  await api.restorePosts(["instagram:P1"], { from: "undo" });
  await api.restorePosts(["instagram:P1"]);
  await api.restoreEntries(["k1"]);
  await api.getTrashHistory({ offset: 60, limit: 60, upto: 1727500000123, post: undefined });
  assert.deepEqual(sent, [
    ["/api/delete", { posts: ["instagram:P1"], from: "review" }],
    ["/api/delete", { media: [3] }],
    ["/api/trash/restore", { posts: ["instagram:P1"], from: "undo" }],
    ["/api/trash/restore", { posts: ["instagram:P1"] }],
    ["/api/trash/restore", { keys: ["k1"], from: "trash" }],
    ["/api/trash/history?offset=60&limit=60&upto=1727500000123", undefined],
  ]);
});

// Last: the profile this page was loaded in stays for the rest of the file.
test("the page's profile is sent once known, and a switch elsewhere restarts the page", async () => {
  const sent = [];
  let answer = () => new Response("{}", { headers: { "content-type": "application/json" } });
  globalThis.fetch = async (url, opts) => {
    sent.push([opts.method, url, opts.headers["X-FeedVault-Profile"]]);
    return answer(url);
  };
  const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  let restarts = 0;
  api.page.restart = () => { restarts += 1; };

  await api.getTags();                                         // not known yet: no header
  answer = () => json({ active: "work", profiles: [{ id: "work", name: "Work", active: true }] });
  assert.equal((await api.getProfiles()).active, "work");
  // The first id told stays: after a switch in another tab, /api/config names the new profile.
  answer = () => json({ profile: { id: "home", name: "Home" }, media_roots: [] });
  assert.equal((await api.getConfig()).profile.id, "home");
  answer = () => json({ ok: true });
  await api.activateProfile("home");
  assert.deepEqual(sent, [["GET", "/api/tags", undefined], ["GET", "/api/profiles", undefined],
                          ["GET", "/api/config", "work"], ["POST", "/api/profiles/home/activate", "work"]]);
  assert.equal(restarts, 0);

  // Refused as a stale tab: the answer still reaches the caller, and the page starts again.
  const refusal = { ok: false, error: "another profile is active now; reload the page", profile_switched: true };
  answer = () => json(refusal, 409);
  assert.deepEqual(await api.renameProfile("work", "Job"), refusal);
  assert.equal(restarts, 1);
  // Any other 409 (a switch refused while a job runs) is only an answer.
  answer = () => json({ ok: false, error: "a scan is running; try again when it is done", busy: true }, 409);
  assert.equal((await api.activateProfile("home")).busy, true);
  assert.equal(restarts, 1);

  // A profile's id cannot reach another endpoint either.
  sent.length = 0;
  assert.deepEqual(await api.deleteProfile(".."), { ok: false, error: "no such page" });
  await api.deleteProfile("a/b");
  assert.deepEqual(sent, [["DELETE", "/api/profiles/a%2Fb", "work"]]);
});
