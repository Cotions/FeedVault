// Checks for userscript/feedvault.user.js: which pages and links it reads on
// each site, and what it sends. The script runs in a bare context (no
// page): location as given, every FeedVault call recorded and left
// unanswered. The links come from the hand-written page shapes in
// userscript/fixtures. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../userscript/", import.meta.url);
const SOURCE = readFileSync(new URL("feedvault.user.js", ROOT), "utf8");

// The script on a page at ``href``: returns its context and the calls it made.
function load(href) {
  const url = new URL(href);
  const calls = [];
  const node = () => ({ querySelector: () => null, querySelectorAll: () => [], appendChild() {}, remove() {} });
  const ctx = vm.createContext({
    location: { hostname: url.hostname, pathname: url.pathname, origin: url.origin, href },
    URL, URLSearchParams, Promise, Date, Set, Map, JSON, Object,
    document: { ...node(), body: node(), createElement: () => ({ ...node(), setAttribute() {}, dataset: {} }) },
    GM_addStyle: () => {},
    GM_xmlhttpRequest: (req) => calls.push(req),
    MutationObserver: class { observe() {} },
    requestAnimationFrame: () => 0,
    setInterval: () => 0,
    setTimeout: () => 0,
    getComputedStyle: () => ({ position: "static" }),
  });
  vm.runInContext(SOURCE, ctx);
  return { run: (code) => vm.runInContext(code, ctx), calls };
}

function hrefs(fixture) {
  const html = readFileSync(new URL(`fixtures/${fixture}`, ROOT), "utf8");
  return [...html.matchAll(/<a [^>]*href="([^"]+)"/g)].map((m) => m[1]);
}

test("only its sites", () => {
  for (const href of ["https://example.com/someone/status/1800000000000000001", "https://x.com.evil.com/a/status/1",
                      "https://vm.tiktok.com/ZMabcdef/", "https://mobile.x.com/someone/status/1800000000000000001"]) {
    const page = load(href);
    assert.equal(page.run("SITE"), null, href);
    assert.equal(page.calls.length, 0);
  }
});

test("X: the post page's Save sends a link built from the path", () => {
  const page = load("https://x.com/someone/status/1800000000000000001?s=20");
  assert.equal(page.run("SITE.platform"), "twitter");
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(savePost())")),
                   { id: "twitter:1800000000000000001", body: { url: "https://x.com/someone/status/1800000000000000001" } });
  assert.equal(page.run("profileName()"), null);
  for (const path of ["/someone/status/1800000000000000001/photo/2", "/someone/status/1800000000000000001/"]) {
    assert.equal(load(`https://twitter.com${path}`).run("savePost()?.id"), "twitter:1800000000000000001");
  }
  for (const path of ["/someone/status/abc", "/someone/status/0123", "/someone/status/1800000000000000001/analytics",
                      "/a_name_far_too_long/status/1", "/someone/status/$(id)", "/someone/likes/1"]) {
    assert.equal(load(`https://x.com${path}`).run("savePost()"), null, path);
  }
});

test("X: post links on the pages", () => {
  const page = load("https://x.com/someone/media");
  const ids = (fixture) => hrefs(fixture).map((h) => page.run(`postOf(${JSON.stringify(h)})`)).filter(Boolean);
  assert.deepEqual(ids("x-profile.html"),
                   ["twitter:1800000000000000001", "twitter:1800000000000000002", "twitter:1800000000000000003"]);
  assert.deepEqual(ids("x-post.html"),
                   ["twitter:1800000000000000001", "twitter:1800000000000000001", "twitter:1800000000000000099"]);
});

test("X: profiles, not the site's own pages", () => {
  for (const [path, name] of [["/someone", "someone"], ["/SomeOne/media", "someone"], ["/someone/with_replies", "someone"],
                              ["/home", null], ["/explore", null], ["/i/bookmarks", null], ["/someone/status/1", null],
                              ["/notifications", null], ["/some-one", null], ["/someone/followers", null]]) {
    assert.equal(load(`https://x.com${path}`).run("profileName()"), name, path);
  }
  const page = load("https://x.com/someone");
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(SITE.resolveQuery('someone'))")), { url: "https://x.com/someone" });
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(SITE.addBody('someone'))")), { target: "https://x.com/someone" });
});

test("TikTok: video pages, links and profiles", () => {
  const page = load("https://www.tiktok.com/@someone/video/7300000000000000001?is_from_webapp=1");
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(savePost())")),
                   { id: "tiktok:7300000000000000001",
                     body: { url: "https://www.tiktok.com/@someone/video/7300000000000000001" } });
  const ids = (fixture) => hrefs(fixture).map((h) => page.run(`postOf(${JSON.stringify(h)})`)).filter(Boolean);
  assert.deepEqual(ids("tiktok-profile.html"), ["tiktok:7300000000000000001", "tiktok:7300000000000000002"]);
  assert.deepEqual(ids("tiktok-video.html"), ["tiktok:7300000000000000002"]);
  for (const path of ["/@someone/photo/7300000000000000005", "/@some-one/video/7300000000000000001",
                      "/@someone/video/73x", "/someone/video/7300000000000000001"]) {
    assert.equal(load(`https://www.tiktok.com${path}`).run("savePost()"), null, path);
  }
  for (const [path, name] of [["/@someone", "someone"], ["/@Some.One_2/", "some.one_2"], ["/@...", null],
                              ["/foryou", null], ["/@someone/video/7300000000000000001", null]]) {
    assert.equal(load(`https://www.tiktok.com${path}`).run("profileName()"), name, path);
  }
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(SITE.addBody('someone'))")),
                   { target: "https://www.tiktok.com/@someone" });
});

test("Instagram as before", () => {
  const page = load("https://www.instagram.com/p/CSAVEME0001/");
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(savePost())")),
                   { id: "instagram:CSAVEME0001", body: { platform: "instagram", shortcode: "CSAVEME0001" } });
  assert.equal(load("https://www.instagram.com/carol.cooks/").run("profileName()"), "carol.cooks");
  assert.equal(load("https://www.instagram.com/explore/").run("profileName()"), null);
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(SITE.addBody('carol.cooks'))")),
                   { tool: "instaloader", target: "carol.cooks" });
});

test("a profile page asks FeedVault about the profile, nothing else", () => {
  const page = load("https://x.com/someone");
  assert.deepEqual(page.calls.map((c) => [c.method, c.url]),
                   [["GET", "http://localhost:3380/api/sources/resolve?url=https%3A%2F%2Fx.com%2Fsomeone"]]);
  assert.equal(page.calls[0].headers["X-FeedVault"], "1");
});

test("never a script route", () => {
  assert.doesNotMatch(SOURCE, /\/api\/scripts|script-sync|\/api\/jobs"/);
});
