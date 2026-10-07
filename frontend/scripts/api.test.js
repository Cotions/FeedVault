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
  const seen = stubFetch();
  // /people/..%2Fbrowse, /people/%252e%252e%2Fbrowse, /collections/.%2F..%2Fbrowse
  for (const call of [() => getPerson("../browse"), () => getPerson("%2e%2e/browse"), () => getPerson(".."),
                      () => getCollection("./../browse"), () => getJob("../../api/browse"),
                      () => getPerson("%2E%2e/jobs?desktop=1")]) {
    await assert.rejects(call(), e => e instanceof ApiError && e.status === 404);
  }
  // A DELETE answers like a POST: the error comes back as data, nothing sent.
  assert.equal((await deleteSource("..").catch(e => e)).status, 404);
  assert.deepEqual(seen, []);
});

test("ordinary ids and query strings are sent", async () => {
  const seen = stubFetch();
  await getPerson(12);
  await getCollection("3", { offset: 0, limit: 60 });
  assert.deepEqual(seen, ["/api/people/12", "/api/collections/3?offset=0&limit=60"]);
  assert.ok(api.sentAsIs("/api/posts/x/a%2F..%2Fb"));          // encoded slashes stay one segment
  assert.ok(api.sentAsIs("/api/sources/resolve?url=https%3A%2F%2Fx.com%2F..%2Fa"));
  assert.ok(!api.sentAsIs("/api/people/1/../../browse"));
});
