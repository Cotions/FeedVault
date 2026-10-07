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
