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

// Just enough of a DOM for the script: elements with attributes, children,
// listeners, and the few selectors it uses (tag, .class, [attr="v"],
// [attr*="v"], lists, ":scope > ").
class Node {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attrs = new Map();
    this.children = [];
    this.parentNode = null;
    this.text = "";
    this.style = {};
    this.listeners = {};
    const attrOf = (k) => `data-${k.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;
    this.dataset = new Proxy({}, {
      get: (_, k) => this.getAttribute(attrOf(k)) ?? undefined,
      set: (_, k, v) => { this.setAttribute(attrOf(k), v); return true; },
    });
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  removeAttribute(k) { this.attrs.delete(k); }
  get className() { return this.getAttribute("class") || ""; }
  set className(v) { this.setAttribute("class", v); }
  get textContent() { return this.text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.replaceChildren(); this.text = String(v); }
  appendChild(c) { c.remove(); c.parentNode = this; this.children.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(typeof c === "string" ? Object.assign(new Node("#text"), { text: c }) : c); }
  replaceChildren(...cs) { for (const c of [...this.children]) c.remove(); this.text = ""; this.append(...cs); }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
    this.parentNode = null;
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  // A click as the browser sends it (isTrusted) or as a page script would.
  dispatch(type, isTrusted) {
    const e = { type, isTrusted, defaultPrevented: false, stopped: false,
                preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; } };
    for (let n = this; n && !e.stopped; n = n.parentNode) (n.listeners[type] || []).forEach((fn) => fn(e));
    return e;
  }
  matches(sel) {
    return sel.split(",").some((one) => {
      const m = one.trim().match(/^([a-z]+)?((?:\.[\w-]+|\[[\w-]+\*?="[^"]*"\])*)$/);
      if (!m) throw new Error(`selector not faked: ${one}`);
      if (m[1] && this.tagName !== m[1].toUpperCase()) return false;
      return [...m[2].matchAll(/\.([\w-]+)|\[([\w-]+)(\*?)="([^"]*)"\]/g)].every(([, cls, attr, star, v]) => {
        if (cls) return this.className.split(" ").includes(cls);
        const have = this.getAttribute(attr);
        return have !== null && (star ? have.includes(v) : have === v);
      });
    });
  }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  querySelectorAll(sel) {
    const child = sel.match(/^:scope > (.+)$/);
    return child ? this.children.filter((c) => c.matches(child[1])) : this.all().filter((c) => c.matches(sel));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) {
    for (let n = this; n; n = n.parentNode) if (n.tagName !== "#DOCUMENT" && n.matches(sel)) return n;
    return null;
  }
}

function hrefs(fixture) {
  return links(fixture).map((l) => l.href);
}

// A fixture's links: href, whether an image is inside, and the text shown.
function links(fixture) {
  const html = readFileSync(new URL(`fixtures/${fixture}`, ROOT), "utf8");
  return [...html.matchAll(/<a [^>]*href="([^"]+)"[^>]*>(.*?)<\/a>/gs)]
    .map((m) => ({ href: m[1], img: m[2].includes("<img"), text: m[2].replace(/<[^>]*>/g, "") }));
}

// The script on a page at ``href`` (with the links of ``fixture`` in it):
// returns its context, its document, the calls it made and its menu commands.
function load(href, fixture = null) {
  const url = new URL(href);
  const calls = [];
  const menu = [];
  const document = new Node("#document");
  document.body = document.appendChild(new Node("body"));
  document.createElement = (tag) => new Node(tag);
  document.title = fixture ? (readFileSync(new URL(`fixtures/${fixture}`, ROOT), "utf8").match(/<title>(.*?)<\/title>/)?.[1] ?? "") : "";
  for (const l of fixture ? links(fixture) : []) {
    const a = document.body.appendChild(new Node("div")).appendChild(new Node("a"));
    a.setAttribute("href", l.href);
    a.text = l.text;
    if (l.img) a.appendChild(new Node("img"));
  }
  const ctx = vm.createContext({
    location: { hostname: url.hostname, pathname: url.pathname, origin: url.origin, href },
    URL, URLSearchParams, Promise, Date, Set, Map, JSON, Object,
    document,
    GM_addStyle: () => {},
    GM_xmlhttpRequest: (req) => calls.push(req),
    GM_registerMenuCommand: (name, fn) => menu.push({ name, fn }),
    MutationObserver: class { observe() {} },
    requestAnimationFrame: () => 0,
    setInterval: () => 0,
    setTimeout: () => 0,                      // nothing fires by itself: the test calls flush()
    getComputedStyle: (n) => ({ position: n.style.position || "static" }),
  });
  vm.runInContext(SOURCE, ctx);
  return { run: (code) => vm.runInContext(code, ctx), calls, document, menu };
}

test("only its sites", () => {
  for (const href of ["https://example.com/someone/status/1800000000000000001", "https://x.com.evil.com/a/status/1",
                      "https://vm.tiktok.com/ZMabcdef/", "https://mobile.x.com/someone/status/1800000000000000001"]) {
    const page = load(href);
    assert.equal(page.run("SITE"), null, href);
    assert.equal(page.run("BIO_HOST"), null, href);
    assert.equal(page.calls.length, 0);
    assert.equal(page.menu.length, 0);
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

// Answers a recorded call as FeedVault would, then lets the script go on.
async function answer(call, status, body) {
  call.onload({ status, responseText: JSON.stringify(body) });
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

test("a profile set to Don't sync in FeedVault: nothing to click, no further call", async () => {
  const page = load("https://x.com/someone");
  await answer(page.calls[0], 200, { ok: true, tool: "gallery-dl", folder: "/m/someone", source: null, no_sync: true });
  assert.equal(page.run("profiles.get('someone').state"), "nosync");
  assert.equal(page.calls.length, 1);
  // Its click does nothing: the state stays, no add is sent.
  page.run("clickProfile('someone')");
  assert.equal(page.calls.length, 1);
});

test("an add or a sync FeedVault refuses for Don't sync shows that, not a failure", async () => {
  const page = load("https://x.com/someone");
  await answer(page.calls[0], 200, { ok: true, tool: "gallery-dl", folder: "/m/someone", source: null, no_sync: false });
  page.run("addAndSync('someone')");
  await answer(page.calls[1], 409, { ok: false, error: "set to Don't sync", no_sync: true });
  assert.equal(page.run("profiles.get('someone').state"), "nosync");
  page.run("syncSource('someone', 4)");
  await answer(page.calls[2], 409, { ok: false, error: "set to Don't sync", no_sync: true });
  assert.equal(page.run("profiles.get('someone').state"), "nosync");
  assert.equal(page.calls.length, 3);
});

test("never a script route", () => {
  assert.doesNotMatch(SOURCE, /\/api\/scripts|script-sync|\/api\/jobs"/);
});

// ---------------------------------------------------------------------------
// A tile's Save
// ---------------------------------------------------------------------------

const call = (page, method, path) => page.calls.find((c) => c.method === method && c.url === `http://localhost:3380${path}`);

// The page with its fixture's tiles, once FeedVault said which it has.
async function tiles(href, fixture, saved) {
  const page = load(href, fixture);
  page.run("flush()");
  await answer(call(page, "POST", "/api/saved"), 200, { saved });
  const tile = (h) => page.document.querySelectorAll("a").find((a) => a.getAttribute("href") === h);
  const save = (h) => tile(h).querySelector(":scope > .fv-tile-save");
  const look = (h) => (tile(h).querySelector(":scope > .fv-badge") ? "saved" : save(h)?.dataset.state || null);
  const sent = () => page.calls.filter((c) => c.url.endsWith("/api/save")).map((c) => JSON.parse(c.data));
  return { page, tile, look, save, sent };
}

test("a Save on every image tile FeedVault does not have, the badge on those it has", async () => {
  const ig = await tiles("https://www.instagram.com/carol.cooks/", "instagram-profile.html", ["instagram:CSAVEME0002"]);
  assert.equal(ig.look("/carol.cooks/p/CSAVEME0001/"), "idle");
  assert.equal(ig.look("/carol.cooks/reel/CSAVEME0002/"), "saved");
  assert.equal(ig.look("/p/CSAVEME0003/"), "idle");
  assert.equal(ig.look("/p/CSAVEME0001/liked_by/"), null);           // a text link
  assert.equal(ig.save("/p/CSAVEME0003/").textContent, "Save");

  const x = await tiles("https://x.com/someone/media", "x-profile.html", ["twitter:1800000000000000002"]);
  assert.equal(x.look("/someone/status/1800000000000000001/photo/1"), "idle");
  assert.equal(x.look("/someone/status/1800000000000000002/photo/1"), "saved");
  assert.equal(x.look("/someone/status/1800000000000000003/video/1"), "idle");
  assert.equal(x.look("/someone/media"), null);

  const xp = await tiles("https://x.com/someone/status/1800000000000000001", "x-post.html", []);
  assert.equal(xp.look("/someone/status/1800000000000000001/photo/1"), "idle");
  assert.equal(xp.look("/someone/status/1800000000000000001"), null);   // the timestamp
  assert.equal(xp.look("/other/status/1800000000000000099"), null);

  const tt = await tiles("https://www.tiktok.com/@someone", "tiktok-profile.html", ["tiktok:7300000000000000002"]);
  assert.equal(tt.look("https://www.tiktok.com/@someone/video/7300000000000000001"), "idle");
  assert.equal(tt.look("https://www.tiktok.com/@someone/video/7300000000000000002"), "saved");
  assert.equal(tt.look("https://www.tiktok.com/@someone/photo/7300000000000000005"), null);
});

test("a tile's link without what a save needs gets no Save", () => {
  const page = load("https://www.tiktok.com/@someone");
  for (const h of ["/@some-one/video/7300000000000000001", "https://www.tiktok.com/video/7300000000000000001",
                   "https://evil.com/@someone/video/7300000000000000001", "http://www.tiktok.com/@someone/video/7300000000000000001",
                   "/@someone/video/7300000000000000001/extra", "/@someone/video/0123"]) {
    assert.equal(page.run(`tilePost(${JSON.stringify(h)})`), null, h);
  }
  const ig = load("https://www.instagram.com/carol.cooks/");
  for (const h of ["/p/abc/", "/p/CSAVEME0001/liked_by/", "https://instagram.com.evil.com/p/CSAVEME0001/"]) {
    assert.equal(ig.run(`tilePost(${JSON.stringify(h)})`), null, h);
  }
});

test("a tile's Save sends that tile's post, built from its href, and does not open it", async () => {
  for (const [href, fixture, tile, body] of [
    ["https://www.instagram.com/carol.cooks/", "instagram-profile.html", "/carol.cooks/reel/CSAVEME0002/",
     { platform: "instagram", shortcode: "CSAVEME0002" }],
    ["https://www.instagram.com/carol.cooks/", "instagram-profile.html", "/p/CSAVEME0003/",
     { platform: "instagram", shortcode: "CSAVEME0003" }],
    ["https://x.com/someone/media", "x-profile.html", "/someone/status/1800000000000000003/video/1",
     { url: "https://x.com/someone/status/1800000000000000003" }],
    ["https://www.tiktok.com/@someone", "tiktok-profile.html", "https://www.tiktok.com/@someone/video/7300000000000000001",
     { url: "https://www.tiktok.com/@someone/video/7300000000000000001" }],
  ]) {
    const t = await tiles(href, fixture, []);
    let opened = false;
    t.tile(tile).addEventListener("click", () => { opened = true; });
    const e = t.save(tile).dispatch("click", true);
    assert.ok(e.defaultPrevented && e.stopped && !opened, tile);
    assert.deepEqual(t.sent(), [body], tile);
    assert.equal(call(t.page, "POST", "/api/save").headers["X-FeedVault"], "1");
    assert.equal(t.look(tile), "busy");
    assert.equal(t.save(tile).textContent, "Sending…");
  }
});

test("a click a page script makes on a tile's Save sends nothing, and opens nothing", async () => {
  const t = await tiles("https://x.com/someone/media", "x-profile.html", []);
  const tile = "/someone/status/1800000000000000001/photo/1";
  let opened = false;
  t.tile(tile).addEventListener("click", () => { opened = true; });
  const e = t.save(tile).dispatch("click", false);
  assert.ok(e.defaultPrevented && !opened);
  assert.deepEqual(t.sent(), []);
  assert.equal(t.look(tile), "idle");
});

test("one save per post at a time, several posts at once, each its own job", async () => {
  const t = await tiles("https://www.tiktok.com/@someone", "tiktok-profile.html", []);
  const one = "https://www.tiktok.com/@someone/video/7300000000000000001";
  const two = "https://www.tiktok.com/@someone/video/7300000000000000002";
  t.save(one).dispatch("click", true);
  t.save(one).dispatch("click", true);                // still sending
  assert.equal(t.sent().length, 1);
  await answer(call(t.page, "POST", "/api/save"), 200, { ok: true, have: false, existing: false, job: { id: 7, state: "queued" } });
  assert.equal(t.save(one).textContent, "Queued");
  assert.equal(t.save(one).getAttribute("title"), "Job #7");
  t.save(one).dispatch("click", true);                // queued
  assert.equal(t.sent().length, 1);
  t.save(two).dispatch("click", true);                // another post: its own save
  assert.deepEqual(t.sent().map((b) => b.url), [one, two]);
  // Its job is followed: running, then done, and the tile turns saved.
  await answer(call(t.page, "GET", "/api/jobs/7"), 200, { id: 7, state: "running" });
  assert.equal(t.save(one).textContent, "Saving…");
  assert.equal(t.page.run("shown('tiktok:7300000000000000001')"), true);
  t.page.run("applyJob('tiktok:7300000000000000001', { id: 7, state: 'done', result: { post: {} } })");
  assert.equal(t.look(one), "saved");
  assert.equal(t.look(two), "busy");
});

test("a post FeedVault had already: the tile turns saved", async () => {
  const t = await tiles("https://www.instagram.com/carol.cooks/", "instagram-profile.html", []);
  const tile = "/carol.cooks/p/CSAVEME0001/";
  t.save(tile).dispatch("click", true);
  await answer(call(t.page, "POST", "/api/save"), 200, { ok: true, have: true, post: { id: "instagram:CSAVEME0001" } });
  assert.equal(t.look(tile), "saved");
});

test("a failed or unanswered tile save says so, and retries on a click", async () => {
  const t = await tiles("https://x.com/someone/media", "x-profile.html", []);
  const tile = "/someone/status/1800000000000000001/photo/1";
  t.save(tile).dispatch("click", true);
  await answer(call(t.page, "POST", "/api/save"), 200, { ok: true, have: false, job: { id: 3, state: "queued" } });
  await answer(call(t.page, "GET", "/api/jobs/3"), 200,
               { id: 3, state: "failed", message: "gallery-dl is not installed", result: { error: "missing" } });
  assert.equal(t.look(tile), "failed");
  assert.equal(t.save(tile).getAttribute("title"), "gallery-dl is not installed (FeedVault: Settings → Downloaders)");
  t.save(tile).dispatch("click", true);
  assert.equal(t.sent().length, 2);
  t.page.calls.filter((c) => c.url.endsWith("/api/save")).at(-1).onerror();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(t.look(tile), "offline");
  assert.equal(t.save(tile).textContent, "Offline — retry");
});

test("a tile reused for another post: its Save sends nothing until painted again", async () => {
  const t = await tiles("https://x.com/someone/media", "x-profile.html", []);
  const a = t.tile("/someone/status/1800000000000000001/photo/1");
  const b = a.querySelector(":scope > .fv-tile-save");
  a.setAttribute("href", "/someone/status/1800000000000000004/photo/1");
  b.dispatch("click", true);
  assert.deepEqual(t.sent(), []);
});

test("the post page's Save and its tile show the same save", async () => {
  const t = await tiles("https://x.com/someone/status/1800000000000000001", "x-post.html", []);
  const panel = () => t.page.document.querySelector(".fv-panel").textContent;
  assert.equal(panel(), "Save to FeedVault");
  t.save("/someone/status/1800000000000000001/photo/1").dispatch("click", true);
  assert.equal(panel(), "Sending…");
  // The panel's own button sends nothing more while it is under way.
  t.page.run("startSave('twitter:1800000000000000001')");
  assert.equal(t.sent().length, 1);
  await answer(call(t.page, "POST", "/api/save"), 200, { ok: true, have: false, job: { id: 5, state: "running" } });
  assert.equal(panel(), "Saving…");
  assert.equal(t.look("/someone/status/1800000000000000001/photo/1"), "busy");
});

// ---------------------------------------------------------------------------
// Link-in-bio pages: Save links to FeedVault
// ---------------------------------------------------------------------------

test("the link-in-bio hosts are biofetch's, and each has its @match", () => {
  const py = readFileSync(new URL("../backend/biofetch.py", ROOT), "utf8");
  const sites = [...py.match(/^SITES = \{([\s\S]*?)^\}/m)[1].matchAll(/^\s+"([^"]+)":/gm)].map((m) => m[1]).sort();
  const page = load("https://linktr.ee/somebody");
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(Object.keys(BIO_SITES).sort())")), sites);
  const matches = [...SOURCE.matchAll(/^\/\/ @match\s+https:\/\/([^/]+)\/\*$/gm)].map((m) => m[1]);
  const social = JSON.parse(page.run("JSON.stringify(Object.values(SITES).flatMap((s) => s.hosts))"));
  assert.deepEqual(matches.filter((h) => !social.includes(h)).sort(), sites);
  assert.doesNotMatch(SOURCE, /@match\s+\*:\/\/\*\/\*|@match\s+https?:\/\/\*\//);   // never every site
});

test("a person's page on a link-in-bio site, not the site's own pages", () => {
  for (const [href, page] of [["https://linktr.ee/somebody", "https://linktr.ee/somebody"],
                              ["https://linktr.ee/somebody/?utm_source=ig", "https://linktr.ee/somebody/"],
                              ["https://beacons.ai/some.body", "https://beacons.ai/some.body"],
                              ["https://allmylinks.com/somebody", "https://allmylinks.com/somebody"],
                              ["https://linktr.ee/", null], ["https://linktr.ee/login", null], ["https://linktr.ee/s/about", null],
                              ["https://linktr.ee/somebody/extra", null], ["https://linktr.ee/...", null]]) {
    const p = load(href);
    assert.equal(p.run("SITE"), null, href);
    assert.equal(p.run("bioPage()"), page, href);
    assert.equal(p.calls.length, 0, href);                          // nothing is asked until a click
    const panel = p.document.querySelector(".fv-panel");
    assert.equal(panel ? panel.textContent : null, page ? "Save links to FeedVault" : null, href);
  }
  assert.equal(load("https://www.instagram.com/somebody/").run("bioPage()"), null);
});

test("the page's outbound links, as rendered, none of the site's own", () => {
  const page = load("https://linktr.ee/somebody", "linktree-page.html");
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(bioLinks())")), [
    { url: "https://www.instagram.com/somebody/", title: "Instagram" },
    { url: "https://x.com/somebody", title: "My X" },
    { url: "https://shop.example/somebody?ref=linktree", title: "Shop my picks" },
    { url: "https://www.youtube.com/@somebody", title: "YouTube" },     // the icon's link has no text: the next one's
    { url: "http://old-site.example/", title: "My old site" },
  ]);
  // At most BIO_MAX_LINKS.
  const many = load("https://linktr.ee/somebody");
  many.run(`for (let i = 0; i < 250; i++) { const a = document.body.appendChild(document.createElement("a"));
            a.setAttribute("href", "https://shop.example/" + i); }`);
  assert.equal(many.run("bioLinks().length"), 200);
});

const PEOPLE = { people: [{ id: 3, name: "Some Body" }, { id: 4, name: "Other One" }], recent: [4] };
const options = (select) => select.all().filter((n) => n.tagName === "OPTION").map((o) => o.textContent);

// The link-in-bio page with its fixture, the picker open.
async function picker() {
  const page = load("https://linktr.ee/somebody", "linktree-page.html");
  const panel = () => page.document.querySelector(".fv-panel");
  const btn = (text) => panel().querySelectorAll("button").find((b) => b.textContent === text);
  btn("Save links to FeedVault").dispatch("click", true);
  await answer(call(page, "GET", "/api/links/people"), 200, PEOPLE);
  const select = panel().querySelector("select");
  const imports = () => page.calls.filter((c) => c.url.endsWith("/api/links/import"));
  return { page, panel, btn, select, imports };
}

test("Save links: a person picked, the page's links sent, what was saved said", async () => {
  const t = await picker();
  assert.equal(t.page.calls[0].headers["X-FeedVault"], "1");
  assert.match(t.panel().textContent, /the 5 links it shows/);
  // No one by default; the recent person first, then everyone else.
  assert.equal(t.select.value, "none");
  assert.deepEqual(options(t.select), ["No person (Unsorted)", "Other One", "Some Body"]);
  // The search narrows the list; the person chosen stays in it.
  const find = t.panel().querySelector("input");
  t.select.value = "3";
  t.select.dispatch("change", true);
  find.value = "zzz";
  find.dispatch("input", true);
  assert.deepEqual(options(t.select), ["No person (Unsorted)", "Some Body"]);
  find.value = "OTH";
  find.dispatch("input", true);
  assert.deepEqual(options(t.select), ["No person (Unsorted)", "Other One", "Some Body"]);
  assert.equal(t.select.value, "3");

  t.btn("Save links").dispatch("click", true);
  assert.equal(t.imports().length, 1);
  assert.deepEqual(JSON.parse(t.imports()[0].data), {
    page_url: "https://linktr.ee/somebody", person: 3, links: [
      { url: "https://www.instagram.com/somebody/", title: "Instagram" },
      { url: "https://x.com/somebody", title: "My X" },
      { url: "https://shop.example/somebody?ref=linktree", title: "Shop my picks" },
      { url: "https://www.youtube.com/@somebody", title: "YouTube" },
      { url: "http://old-site.example/", title: "My old site" }] });
  assert.equal(t.panel().textContent, "…");
  t.btn("…").dispatch("click", true);                                  // under way: nothing more
  assert.equal(t.imports().length, 1);
  await answer(t.imports()[0], 200, { ok: true, url: "https://linktr.ee/somebody", person: 3,
                                      links: { saved: 4, already: 1, elsewhere: 1, over: 0, dropped: 0 } });
  assert.match(t.panel().textContent, /4 saved, 2 already there/);
  assert.equal(t.panel().querySelector("a").getAttribute("href"), "http://localhost:3380/people/3");
});

test("Save links to no one", async () => {
  const t = await picker();
  t.btn("Save links").dispatch("click", true);
  assert.equal(JSON.parse(t.imports()[0].data).person, null);
  await answer(t.imports()[0], 200, { ok: true, person: null, links: { saved: 6, already: 0, elsewhere: 0, over: 2, dropped: 1 } });
  assert.match(t.panel().textContent, /6 saved, 0 already there, 2 over the limit, 1 refused/);
  assert.equal(t.panel().querySelector("a").getAttribute("href"), "http://localhost:3380/links");
});

test("a page script's clicks and changes on the panel do nothing", async () => {
  const page = load("https://linktr.ee/somebody", "linktree-page.html");
  page.document.querySelector(".fv-panel").querySelector("button").dispatch("click", false);
  assert.equal(page.calls.length, 0);
  const t = await picker();
  t.select.value = "4";
  t.select.dispatch("change", false);                                  // not the user's: the choice stays
  assert.equal(t.select.value, "none");
  assert.equal(t.page.run("bios.get('https://linktr.ee/somebody').person"), "none");
  t.btn("Save links").dispatch("click", false);
  t.btn("Cancel").dispatch("click", false);
  assert.equal(t.imports().length, 0);
  assert.ok(t.btn("Save links"));
});

test("Save links when the import is off, or FeedVault does not answer", async () => {
  const t = await picker();
  t.btn("Save links").dispatch("click", true);
  await answer(t.imports()[0], 403, { ok: false, error: "link-in-bio import is off: turn it on in Settings → Downloads" });
  assert.match(t.panel().textContent, /import is off/);
  assert.equal(t.panel().querySelector("a").textContent, "Open Settings → Downloads");
  const off = load("https://linktr.ee/somebody");
  off.document.querySelector(".fv-panel").querySelector("button").dispatch("click", true);
  off.calls[0].onerror();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(off.document.querySelector(".fv-panel").textContent, "FeedVault is not running — retry");
});

// ---------------------------------------------------------------------------
// The menu command: Add this page to FeedVault Links
// ---------------------------------------------------------------------------

test("the menu command adds the page's address and title, to no one", async () => {
  for (const [href, fixture] of [["https://x.com/someone/status/1800000000000000001", "x-post.html"],
                                 ["https://linktr.ee/somebody", "linktree-page.html"]]) {
    const page = load(href, fixture);
    assert.deepEqual(page.menu.map((m) => m.name), ["Add this page to FeedVault Links"]);
    const before = page.calls.length;
    page.menu[0].fn();
    const sent = page.calls.slice(before);
    assert.deepEqual(sent.map((c) => [c.method, c.url]), [["POST", "http://localhost:3380/api/links"]]);
    assert.deepEqual(JSON.parse(sent[0].data), { url: href, title: page.document.title });
    assert.ok(page.document.title);
    assert.equal(sent[0].headers["X-FeedVault"], "1");
    await answer(sent[0], 409, { ok: false, error: "that link is saved already", link: { person: { id: 3, name: "Some Body" } } });
    assert.match(page.document.querySelector(".fv-toast").textContent, /Already in FeedVault Links, with Some Body/);
  }
  const page = load("https://www.tiktok.com/@someone");
  page.menu[0].fn();
  await answer(page.calls.at(-1), 200, { ok: true, link: { id: 9 } });
  assert.match(page.document.querySelector(".fv-toast").textContent, /Added to FeedVault Links \(Unsorted\)/);
});
