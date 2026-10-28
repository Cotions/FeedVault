// Checks for userscript/feedvault.user.js: which pages and links it reads on
// each site, what it sends, and its bubble. The script runs in a bare
// context (no page): location as given, every FeedVault call recorded and
// left unanswered, no timer firing by itself. The links come from the
// hand-written page shapes in userscript/fixtures. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../userscript/", import.meta.url);
const SOURCE = readFileSync(new URL("feedvault.user.js", ROOT), "utf8");

// The window, the bubble's box, and every other element's.
const VW = 1280, VH = 800, BUBBLE = 36, BOX_W = 200, BOX_H = 60;
const px = (v) => (v && v !== "auto" ? parseFloat(v) : null);

// Just enough of a DOM for the script: elements with attributes, children,
// listeners, and the few selectors it uses (tag, .class, [attr="v"],
// [attr*="v"], lists, ":scope > "), and a box laid out from style's
// left/top, else right/bottom.
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
  hasAttribute(k) { return this.attrs.has(k); }
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
  contains(n) {
    for (let x = n; x; x = x.parentNode) if (x === this) return true;
    return false;
  }
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n.tagName === "#DOCUMENT";
  }
  focus() {}
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  // Laid out in the window less its scrollbars (documentElement's client
  // size, when a test gives one), as a browser lays out a fixed box.
  getBoundingClientRect() {
    const [w, h] = this.className.split(" ").includes("fv-bubble") ? [BUBBLE, BUBBLE] : [BOX_W, BOX_H];
    let doc = this;
    while (doc.parentNode) doc = doc.parentNode;
    const vw = doc.documentElement?.clientWidth ?? VW, vh = doc.documentElement?.clientHeight ?? VH;
    const left = px(this.style.left) ?? vw - (px(this.style.right) ?? 0) - w;
    const top = px(this.style.top) ?? vh - (px(this.style.bottom) ?? 0) - h;
    return { left, top, right: left + w, bottom: top + h, width: w, height: h };
  }
  setPointerCapture(id) { this.captured = id; }
  // An event as the browser sends it (isTrusted) or as a page script would,
  // bubbling up to the document.
  dispatch(type, isTrusted, props = {}) {
    const e = { type, isTrusted, target: this, ...props, defaultPrevented: false, stopped: false,
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

// Lets the script go on after an answer (its awaits, a few deep).
async function settle() {
  for (let i = 0; i < 12; i++) await Promise.resolve();
}

// The script on a page at ``href`` (with the links of ``fixture`` in it):
// returns its context, its document, the calls it made, its menu commands
// and its GM_setValue storage (``store``, given to start from one).
// ``tick()`` fires the timers set so far; ``fire(type)`` a window event.
function load(href, fixture = null, store = new Map()) {
  const url = new URL(href);
  const calls = [];
  const menu = [];
  const timers = [];
  const win = {};
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
    GM_getValue: (k, d) => (store.has(k) ? store.get(k) : d),
    GM_setValue: (k, v) => store.set(k, v),
    innerWidth: VW, innerHeight: VH,
    addEventListener: (type, fn) => (win[type] ||= []).push(fn),
    MutationObserver: class { observe() {} },
    requestAnimationFrame: () => 0,
    setInterval: () => 0,
    setTimeout: (fn) => timers.push(fn),     // nothing fires by itself: the test calls tick() or flush()
    getComputedStyle: (n) => ({ position: n.style.position || "static", display: n.style.display || "block" }),
  });
  vm.runInContext(SOURCE, ctx);
  return {
    run: (code) => vm.runInContext(code, ctx), calls, document, menu, store,
    tick: async () => { for (const fn of timers.splice(0)) fn(); await settle(); },
    fire: (type) => (win[type] || []).forEach((fn) => fn({ type, isTrusted: true })),
  };
}

// Answers a recorded call as FeedVault would, then lets the script go on.
async function answer(call, status, body) {
  call.onload({ status, responseText: JSON.stringify(body) });
  await settle();
}

const call = (page, method, path) => page.calls.find((c) => c.method === method && c.url === `http://localhost:3380${path}`);
const calls = (page, method, path) => page.calls.filter((c) => c.method === method && c.url === `http://localhost:3380${path}`);

// SPA navigation: the address changes, the page paints.
function go(page, href) {
  const u = new URL(href);
  page.run(`Object.assign(location, ${JSON.stringify({ pathname: u.pathname, origin: u.origin, href })}); paint();`);
}

// ---------------------------------------------------------------------------
// The bubble, as a test sees it
// ---------------------------------------------------------------------------

const bubbles = (page) => page.document.querySelectorAll(".fv-bubble");
const bubbleOf = (page) => page.document.querySelector(".fv-bubble");
const btnOf = (page) => page.document.querySelector(".fv-bubble-btn");
const menuOf = (page) => page.document.querySelector(".fv-menu");
const opened = (page) => bubbleOf(page).hasAttribute("data-open");
// The menu's items (buttons and links), and an item by its own words.
const items = (page) => menuOf(page).all().filter((n) => n.className.split(" ").includes("fv-item"));
const labels = (page) => items(page).map((n) => n.text);
const itemOf = (page, text) => items(page).find((n) => n.text === text);
const why = (n) => n.querySelector(".fv-why")?.textContent ?? null;
const off = (n) => n.getAttribute("aria-disabled") === "true";
const heads = (page) => menuOf(page).querySelectorAll(".fv-head").map((n) => n.textContent);
const notes = (page) => menuOf(page).querySelectorAll(".fv-note");
const noteText = (page) => notes(page).map((n) => n.textContent).join("\n");
// The status on the closed bubble: its dot's level, and its tooltip's first line.
const status = (page) => [btnOf(page).dataset.status || "", btnOf(page).getAttribute("title").split("\n")[0]];
const spot = (page) => [bubbleOf(page).style.right, bubbleOf(page).style.bottom];
const COMMON = ["Add this page to Links", "Open FeedVault", "Reset bubble position"];

function open(page) {
  if (!opened(page)) btnOf(page).dispatch("click", true);
  assert.ok(opened(page));
}

function click(page, text, isTrusted = true) {
  const n = itemOf(page, text);
  assert.ok(n, `no item "${text}" in ${JSON.stringify(labels(page))}`);
  return n.dispatch("click", isTrusted);
}

test("only its sites", () => {
  for (const href of ["https://example.com/someone/status/1800000000000000001", "https://x.com.evil.com/a/status/1",
                      "https://vm.tiktok.com/ZMabcdef/", "https://mobile.x.com/someone/status/1800000000000000001"]) {
    const page = load(href);
    assert.equal(page.run("SITE"), null, href);
    assert.equal(page.run("BIO_HOST"), null, href);
    assert.equal(page.calls.length, 0);
    assert.equal(page.menu.length, 0);
    assert.equal(bubbles(page).length, 0);
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

test("never a script route, nor the old panel and its combined Add & sync", () => {
  assert.doesNotMatch(SOURCE, /\/api\/scripts|script-sync|\/api\/jobs"/);
  assert.doesNotMatch(SOURCE, /fv-panel|fv-grip|Add & sync|addAndSync/);
  assert.match(SOURCE, /^\/\/ @version\s+0\.7\.2$/m);
});

// ---------------------------------------------------------------------------
// One bubble, everywhere the script runs
// ---------------------------------------------------------------------------

test("one bubble on every page, also with no post or profile; navigating changes its menu, never adds one", async () => {
  const page = load("https://x.com/home");
  assert.equal(bubbles(page).length, 1);
  assert.equal(btnOf(page).text, "FV");
  assert.equal(opened(page), false);
  assert.deepEqual(labels(page), COMMON);
  assert.deepEqual(status(page), ["", "FeedVault"]);
  assert.equal(page.calls.length, 0);

  go(page, "https://x.com/someone");
  assert.equal(bubbles(page).length, 1);
  assert.deepEqual(heads(page), ["@someone"]);
  assert.deepEqual(labels(page), ["Asking FeedVault…", ...COMMON]);

  go(page, "https://x.com/someone/status/1800000000000000001");
  assert.equal(bubbles(page).length, 1);
  assert.deepEqual(heads(page), ["This post"]);
  assert.deepEqual(labels(page), ["Save post", ...COMMON]);
  assert.ok(off(itemOf(page, "Save post")));
  assert.equal(why(itemOf(page, "Save post")), "Asking FeedVault…");

  go(page, "https://x.com/explore");
  assert.deepEqual(labels(page), COMMON);
  // The site redrew the body: the same bubble is put back, not a second one.
  const was = bubbleOf(page);
  was.remove();
  page.run("paint()");
  assert.equal(bubbles(page).length, 1);
  assert.equal(bubbleOf(page), was);

  for (const href of ["https://linktr.ee/", "https://linktr.ee/login", "https://www.instagram.com/explore/",
                      "https://www.tiktok.com/foryou"]) {
    const p = load(href);
    assert.equal(bubbles(p).length, 1, href);
    assert.deepEqual(labels(p), COMMON, href);
  }
});

test("the menu opens on a real click, closes on another, on Escape or a click outside; a page script's do nothing", () => {
  const page = load("https://x.com/home");
  btnOf(page).dispatch("click", false);
  assert.equal(opened(page), false);
  btnOf(page).dispatch("click", true);
  assert.equal(opened(page), true);
  assert.equal(btnOf(page).getAttribute("aria-expanded"), "true");
  btnOf(page).dispatch("click", true);
  assert.equal(opened(page), false);
  assert.equal(btnOf(page).getAttribute("aria-expanded"), "false");

  open(page);
  page.document.body.dispatch("keydown", false, { key: "Escape" });
  assert.equal(opened(page), true);
  page.document.body.dispatch("keydown", true, { key: "Enter" });
  assert.equal(opened(page), true);
  page.document.body.dispatch("keydown", true, { key: "Escape" });
  assert.equal(opened(page), false);

  open(page);
  // A click in the menu (seen by the document first, as the browser does) keeps it open.
  page.document.dispatch("pointerdown", true, { target: itemOf(page, "Open FeedVault") });
  itemOf(page, "Open FeedVault").dispatch("pointerdown", true);
  assert.equal(opened(page), true);
  page.document.body.dispatch("pointerdown", false);
  assert.equal(opened(page), true);
  page.document.body.dispatch("pointerdown", true);
  assert.equal(opened(page), false);
});

test("what is done in the bubble does not reach the page's own handlers", () => {
  const page = load("https://x.com/home");
  let seen = 0;
  for (const type of ["click", "pointerdown", "mousedown", "keydown"]) page.document.body.addEventListener(type, () => { seen++; });
  open(page);
  for (const type of ["click", "pointerdown", "mousedown", "keydown"]) itemOf(page, "Reset bubble position").dispatch(type, true, { key: "j" });
  btnOf(page).dispatch("click", true);
  assert.equal(seen, 0);
});

// ---------------------------------------------------------------------------
// Moving it: it stays there on this site
// ---------------------------------------------------------------------------

// The spot kept for x.com (copied out of the script's context).
const kept = (page) => { const v = page.store.get("bubble:x.com"); return v && { ...v }; };

// A press, a move and a release, then the click the browser sends after
// them (the pointer captured, the release is on the bubble too).
function drag(g, isTrusted, [x0, y0], [x1, y1]) {
  g.dispatch("pointerdown", isTrusted, { button: 0, pointerId: 1, clientX: x0, clientY: y0 });
  g.dispatch("pointermove", isTrusted, { pointerId: 1, clientX: x1, clientY: y1 });
  g.dispatch("pointerup", isTrusted, { pointerId: 1, clientX: x1, clientY: y1 });
  g.dispatch("click", isTrusted);
}

test("the bubble starts in the site's spot: on X, left of X's own corner buttons; its menu opens toward the room", () => {
  assert.deepEqual(spot(load("https://x.com/someone/status/1800000000000000001")), ["90px", "18px"]);
  assert.deepEqual(spot(load("https://www.tiktok.com/@someone/video/7300000000000000001")), ["18px", "18px"]);
  assert.deepEqual(spot(load("https://www.instagram.com/")), ["18px", "18px"]);
  assert.deepEqual(spot(load("https://linktr.ee/somebody")), ["18px", "18px"]);
  // Bottom right: the menu opens up and to the left.
  const page = load("https://x.com/home");
  assert.equal(bubbleOf(page).dataset.x, "left");
  assert.equal(bubbleOf(page).dataset.y, "up");
  assert.equal(menuOf(page).style.maxHeight, `${VH - 18 - BUBBLE - 16}px`);
});

test("a dragged bubble keeps its spot on that site; clamped inside the window; a double-click puts it back", () => {
  const page = load("https://x.com/someone/status/1800000000000000001");
  // The bubble's box: left 1154, top 746 (90 from the right, 18 from the bottom, 36 across).
  drag(btnOf(page), true, [1170, 760], [670, 360]);
  assert.equal(btnOf(page).captured, 1);
  assert.deepEqual(kept(page), { right: 590, bottom: 418 });
  assert.deepEqual(spot(page), ["590px", "418px"]);
  // Now in the top half, right of the middle: the menu opens down and to the left.
  assert.deepEqual([bubbleOf(page).dataset.x, bubbleOf(page).dataset.y], ["left", "down"]);
  // The click that ends a drag opens nothing; the next one does.
  assert.equal(opened(page), false);
  btnOf(page).dispatch("pointerdown", true, { button: 0, pointerId: 2, clientX: 680, clientY: 360 });
  btnOf(page).dispatch("pointerup", true, { pointerId: 2, clientX: 680, clientY: 360 });
  btnOf(page).dispatch("click", true);
  assert.equal(opened(page), true);
  btnOf(page).dispatch("click", true);

  // Another page of the site: the same spot; another site has its own.
  const next = load("https://x.com/home", null, page.store);
  assert.deepEqual(spot(next), ["590px", "418px"]);
  assert.deepEqual(spot(load("https://twitter.com/home", null, page.store)), ["90px", "18px"]);
  assert.deepEqual(spot(load("https://www.instagram.com/", null, page.store)), ["18px", "18px"]);

  // However far it is dragged, it stays inside the window; top left, the menu opens down and right.
  drag(btnOf(next), true, [660, 350], [-500, -5000]);
  assert.deepEqual(kept(next), { right: VW - BUBBLE, bottom: VH - BUBBLE });
  assert.deepEqual([bubbleOf(next).dataset.x, bubbleOf(next).dataset.y], ["right", "down"]);
  drag(btnOf(next), true, [10, 10], [5000, 5000]);
  assert.deepEqual(kept(next), { right: 0, bottom: 0 });
  assert.deepEqual([bubbleOf(next).dataset.x, bubbleOf(next).dataset.y], ["left", "up"]);

  // A window made smaller keeps it inside, on a resize and on every paint.
  next.store.set("bubble:x.com", { right: 590, bottom: 418 });
  next.run("innerWidth = 400; innerHeight = 300;");
  next.fire("resize");
  assert.deepEqual(spot(next), [`${400 - BUBBLE}px`, `${300 - BUBBLE}px`]);
  next.run("innerWidth = 1280; innerHeight = 800; paint();");
  assert.deepEqual(spot(next), ["590px", "418px"]);

  btnOf(next).dispatch("dblclick", true);
  assert.equal(next.store.get("bubble:x.com"), null);
  assert.deepEqual(spot(next), ["90px", "18px"]);
  // A click on it, a few pixels' wobble, saves nothing and opens the menu.
  drag(btnOf(next), true, [1170, 760], [1172, 761]);
  assert.equal(next.store.get("bubble:x.com"), null);
  assert.equal(opened(next), true);
});

test("with a scrollbar, a dropped bubble stays where it was dropped", () => {
  const page = load("https://x.com/home");
  // A 15px scrollbar: innerWidth counts it in, the fixed box's right does not.
  page.document.documentElement = { clientWidth: VW - 15, clientHeight: VH };
  page.run("paint()");
  assert.deepEqual(spot(page), ["90px", "18px"]);
  const before = bubbleOf(page).getBoundingClientRect();
  assert.equal(before.left, VW - 15 - 90 - BUBBLE);
  drag(btnOf(page), true, [before.left + 10, before.top + 10], [610, 410]);
  const dropped = bubbleOf(page).getBoundingClientRect();
  assert.deepEqual([dropped.left, dropped.top], [600, 400]);
  assert.deepEqual(kept(page), { right: VW - 15 - 600 - BUBBLE, bottom: VH - 400 - BUBBLE });
  // Clamped to that window too.
  drag(btnOf(page), true, [610, 410], [5000, 5000]);
  assert.deepEqual(kept(page), { right: 0, bottom: 0 });
  assert.equal(bubbleOf(page).getBoundingClientRect().right, VW - 15);
});

test("Reset bubble position, in the menu, puts it back", () => {
  const page = load("https://www.tiktok.com/foryou");
  drag(btnOf(page), true, [1240, 760], [100, 100]);
  assert.notDeepEqual(spot(page), ["18px", "18px"]);
  open(page);
  click(page, "Reset bubble position", false);
  assert.ok(page.store.get("bubble:www.tiktok.com"));
  click(page, "Reset bubble position");
  assert.equal(page.store.get("bubble:www.tiktok.com"), null);
  assert.deepEqual(spot(page), ["18px", "18px"]);
  assert.equal(opened(page), true);
});

test("a page script's pointer events and double-clicks on the bubble do nothing", () => {
  const page = load("https://x.com/someone/status/1800000000000000001");
  drag(btnOf(page), false, [1170, 760], [670, 360]);
  assert.equal(page.store.has("bubble:x.com"), false);
  assert.deepEqual(spot(page), ["90px", "18px"]);
  assert.equal(bubbleOf(page).style.left || "", "");
  // Dragged by the user, then a page script's double-click: the spot stays.
  drag(btnOf(page), true, [1170, 760], [670, 360]);
  btnOf(page).dispatch("dblclick", false);
  assert.deepEqual(kept(page), { right: 590, bottom: 418 });
});

// ---------------------------------------------------------------------------
// A profile: Add profile, Sync now, View in FeedVault
// ---------------------------------------------------------------------------

const RESOLVED = { ok: true, tool: "gallery-dl", platform: "twitter", target: "https://x.com/someone",
                   folder: "/m/twitter/someone", source: null, no_sync: false };
const SOURCE_4 = { id: 4, person: { id: 3, name: "Some Body" }, account: { platform: "twitter", id: "123" },
                   job: null, dashboard_only: false, no_sync: false };

// x.com/someone once FeedVault said what it knows of the profile, the menu open.
async function profile(resolved = RESOLVED, source = null) {
  const page = load("https://x.com/someone");
  await answer(page.calls[0], 200, resolved);
  if (typeof resolved.source === "number") await answer(call(page, "GET", `/api/sources/${resolved.source}`), 200, source);
  open(page);
  return page;
}

test("a profile FeedVault has no source for: Add profile, not Sync now, nothing to view", async () => {
  const page = await profile();
  assert.deepEqual(heads(page), ["@someone"]);
  assert.deepEqual(labels(page), ["Add profile", "Sync now", "View in FeedVault", ...COMMON]);
  assert.equal(off(itemOf(page, "Add profile")), false);
  assert.ok(off(itemOf(page, "Sync now")));
  assert.equal(why(itemOf(page, "Sync now")), "Add the profile first");
  assert.ok(off(itemOf(page, "View in FeedVault")));
  assert.equal(itemOf(page, "View in FeedVault").tagName, "BUTTON");
  assert.equal(why(itemOf(page, "View in FeedVault")), "Not in FeedVault yet");
  assert.deepEqual(status(page), ["", "FeedVault"]);
  click(page, "Sync now");
  assert.equal(page.calls.length, 1);
});

test("Add profile asks, then adds the source and only that: no sync is sent", async () => {
  const page = await profile();
  click(page, "Add profile", false);
  assert.deepEqual(labels(page), ["Add profile", "Sync now", "View in FeedVault", ...COMMON]);
  click(page, "Add profile");
  // The confirm, in the menu: what it adds.
  assert.deepEqual(labels(page), ["Add @someone", "Cancel", "Sync now", "View in FeedVault", ...COMMON]);
  assert.match(noteText(page), /New gallery-dl source for @someone, downloading into its folder someone\. Nothing is downloaded until you sync it\./);
  assert.equal(page.calls.length, 1);
  click(page, "Cancel");
  assert.deepEqual(labels(page), ["Add profile", "Sync now", "View in FeedVault", ...COMMON]);
  click(page, "Add profile");
  click(page, "Add @someone", false);
  assert.equal(page.calls.length, 1);
  click(page, "Add @someone");
  assert.deepEqual(page.calls.slice(1).map((c) => [c.method, c.url]), [["POST", "http://localhost:3380/api/sources"]]);
  assert.deepEqual(JSON.parse(page.calls[1].data), { target: "https://x.com/someone" });
  assert.equal(page.calls[1].headers["X-FeedVault"], "1");
  assert.deepEqual(labels(page), ["Adding…", "Sync now", "View in FeedVault", ...COMMON]);
  assert.deepEqual(status(page), ["busy", "FeedVault: adding @someone"]);
  await answer(page.calls[1], 200, { ok: true, source: { id: 4, person: null, account: null, job: null, dashboard_only: false } });
  // Added, not synced: Sync now is the user's next click.
  assert.equal(page.calls.length, 2);
  assert.ok(!page.calls.some((c) => c.url.includes("/sync")));
  assert.ok(off(itemOf(page, "Add profile")));
  assert.equal(why(itemOf(page, "Add profile")), "Added. Sync now downloads it.");
  assert.equal(off(itemOf(page, "Sync now")), false);
  assert.equal(itemOf(page, "View in FeedVault").getAttribute("href"), "http://localhost:3380/creators");
  assert.deepEqual(status(page), ["ok", "FeedVault: @someone is in FeedVault"]);
  assert.equal(opened(page), true);
});

test("an Instagram profile is added with instaloader, by its name", async () => {
  const page = load("https://www.instagram.com/carol.cooks/");
  await answer(page.calls[0], 200, { ok: true, tool: "instaloader", folder: "/m/instagram/carol.cooks", source: null, no_sync: false });
  open(page);
  click(page, "Add profile");
  click(page, "Add @carol.cooks");
  assert.deepEqual(JSON.parse(call(page, "POST", "/api/sources").data), { tool: "instaloader", target: "carol.cooks" });
});

test("an add that fails says why and retries; FeedVault not answering says so", async () => {
  const page = await profile();
  click(page, "Add profile");
  click(page, "Add @someone");
  await answer(page.calls[1], 400, { ok: false, error: "this link syncs with yt-dlp (Settings → Link routing)" });
  assert.match(noteText(page), /syncs with yt-dlp/);
  assert.deepEqual(status(page), ["failed", "FeedVault: adding @someone failed: this link syncs with yt-dlp (Settings → Link routing)"]);
  click(page, "Add profile — retry");
  page.calls.at(-1).onerror();
  await settle();
  assert.equal(why(itemOf(page, "Add profile — retry")), "FeedVault is not running");
  assert.deepEqual(status(page), ["failed", "FeedVault: FeedVault is not running"]);
  assert.equal(calls(page, "POST", "/api/sources").length, 2);
});

test("a profile with a source: Add profile disabled, Sync now syncs it and shows how it goes, View links to its person", async () => {
  const page = await profile({ ...RESOLVED, source: 4 }, SOURCE_4);
  assert.deepEqual(labels(page), ["Add profile", "Sync now", "View in FeedVault", ...COMMON]);
  assert.ok(off(itemOf(page, "Add profile")));
  assert.equal(why(itemOf(page, "Add profile")), "Already in FeedVault");
  const view = itemOf(page, "View in FeedVault");
  assert.equal(view.tagName, "A");
  assert.equal(view.getAttribute("href"), "http://localhost:3380/people/3");
  assert.equal(view.getAttribute("title"), "Some Body in FeedVault");
  assert.equal(view.getAttribute("target"), "_blank");
  assert.deepEqual(status(page), ["ok", "FeedVault: @someone is in FeedVault"]);
  page.run("addProfile('someone')");
  click(page, "Add profile");
  click(page, "Sync now", false);
  assert.equal(page.calls.length, 2);

  click(page, "Sync now");
  assert.deepEqual(page.calls.slice(2).map((c) => [c.method, c.url]), [["POST", "http://localhost:3380/api/sources/4/sync"]]);
  assert.ok(!page.calls.some((c) => c.method === "POST" && c.url.endsWith("/api/sources")));
  assert.deepEqual(labels(page), ["Add profile", "Starting sync…", "View in FeedVault", ...COMMON]);
  click(page, "Starting sync…");
  assert.equal(calls(page, "POST", "/api/sources/4/sync").length, 1);
  assert.equal(status(page)[0], "busy");
  await answer(call(page, "POST", "/api/sources/4/sync"), 200, { ok: true, job: { id: 9, state: "queued" } });
  assert.equal(itemOf(page, "Sync queued").getAttribute("title"), "Job #9");
  assert.deepEqual(status(page), ["busy", "FeedVault: syncing @someone (queued)"]);
  await answer(call(page, "GET", "/api/jobs/9"), 200, { id: 9, state: "running" });
  assert.ok(itemOf(page, "Syncing…"));
  assert.deepEqual(status(page), ["busy", "FeedVault: syncing @someone (running)"]);
  // The menu stays open while it runs.
  assert.equal(opened(page), true);
  await page.tick();
  await answer(calls(page, "GET", "/api/jobs/9").at(-1), 200, { id: 9, state: "done", message: "3 new posts" });
  await answer(calls(page, "GET", "/api/sources/4").at(-1), 200, SOURCE_4);
  assert.equal(why(itemOf(page, "Sync now")), "Synced: 3 new posts");
  assert.equal(off(itemOf(page, "Sync now")), false);
  assert.deepEqual(status(page), ["ok", "FeedVault: @someone is in FeedVault; Synced: 3 new posts"]);
});

test("a sync that fails says why, links to Settings for a missing tool, and retries", async () => {
  const page = await profile({ ...RESOLVED, source: 4 }, SOURCE_4);
  click(page, "Sync now");
  await answer(call(page, "POST", "/api/sources/4/sync"), 200, { ok: true, job: { id: 9, state: "queued" } });
  await answer(call(page, "GET", "/api/jobs/9"), 200,
               { id: 9, state: "failed", message: "gallery-dl is not installed", result: { error: "missing" } });
  await answer(calls(page, "GET", "/api/sources/4").at(-1), 200, SOURCE_4);
  assert.match(noteText(page), /gallery-dl is not installed/);
  assert.ok(notes(page).some((n) => n.querySelector("a")?.textContent === "Open Settings → Downloaders"));
  assert.deepEqual(status(page), ["failed", "FeedVault: syncing @someone failed: gallery-dl is not installed"]);
  click(page, "Sync failed — retry");
  assert.equal(calls(page, "POST", "/api/sources/4/sync").length, 2);
});

test("a profile already syncing shows it; the account's posts when it has no person", async () => {
  const page = await profile({ ...RESOLVED, source: 5 },
                             { ...SOURCE_4, id: 5, person: null, job: { id: 11, state: "running" } });
  assert.ok(itemOf(page, "Sync queued"));
  assert.ok(call(page, "GET", "/api/jobs/11"));
  assert.equal(itemOf(page, "View in FeedVault").getAttribute("href"), "http://localhost:3380/?platform=twitter&author=123");
});

test("a source only the dashboard syncs: Sync now disabled, with why and a link there", async () => {
  const page = await profile({ ...RESOLVED, source: 4 }, { ...SOURCE_4, dashboard_only: true });
  assert.ok(off(itemOf(page, "Sync now")));
  assert.equal(why(itemOf(page, "Sync now")), "Only from FeedVault's dashboard");
  assert.match(noteText(page), /runs a script or logs in/);
  const to = notes(page).map((n) => n.querySelector("a")).find(Boolean);
  assert.deepEqual([to.textContent, to.getAttribute("href")], ["Open it in FeedVault", "http://localhost:3380/people/3"]);
  click(page, "Sync now");
  assert.equal(page.calls.length, 2);
  // And when FeedVault refuses it to this page (403).
  const refused = await profile({ ...RESOLVED, source: 4 }, SOURCE_4);
  click(refused, "Sync now");
  await answer(call(refused, "POST", "/api/sources/4/sync"), 403,
               { ok: false, error: "this source syncs with a login, which only FeedVault's own dashboard can start" });
  assert.ok(off(itemOf(refused, "Sync now")));
  assert.match(noteText(refused), /syncs with a login/);
});

test("a profile set to Don't sync in FeedVault: nothing to click, no further call", async () => {
  const page = await profile({ ...RESOLVED, no_sync: true });
  assert.equal(page.run("profiles.get('someone').nosync"), true);
  for (const text of ["Add profile", "Sync now"]) {
    assert.ok(off(itemOf(page, text)), text);
    assert.equal(why(itemOf(page, text)), "Set to Don't sync in FeedVault");
    click(page, text);
  }
  assert.match(noteText(page), /Set to Don't sync in FeedVault: it downloads nothing from this profile/);
  page.run("addProfile('someone'); syncSource('someone')");
  assert.equal(page.calls.length, 1);
});

test("an add or a sync FeedVault refuses for Don't sync shows that, not a failure", async () => {
  const page = await profile();
  click(page, "Add profile");
  click(page, "Add @someone");
  await answer(page.calls[1], 409, { ok: false, error: "set to Don't sync", no_sync: true });
  assert.equal(page.run("profiles.get('someone').nosync"), true);
  assert.equal(why(itemOf(page, "Add profile")), "Set to Don't sync in FeedVault");
  const synced = await profile({ ...RESOLVED, source: 4 }, SOURCE_4);
  click(synced, "Sync now");
  await answer(call(synced, "POST", "/api/sources/4/sync"), 409, { ok: false, error: "set to Don't sync", no_sync: true });
  assert.equal(synced.run("profiles.get('someone').nosync"), true);
  assert.ok(off(itemOf(synced, "Sync now")));
  assert.equal(synced.calls.length, 3);
});

test("FeedVault not answering the profile's lookup: the menu says so, and asks again on a click", async () => {
  const page = load("https://x.com/someone");
  page.calls[0].onerror();
  await settle();
  open(page);
  assert.deepEqual(labels(page), ["FeedVault is not running — retry", ...COMMON]);
  assert.deepEqual(status(page), ["failed", "FeedVault: FeedVault is not running"]);
  click(page, "FeedVault is not running — retry");
  assert.equal(calls(page, "GET", "/api/sources/resolve?url=https%3A%2F%2Fx.com%2Fsomeone").length, 2);
});

// ---------------------------------------------------------------------------
// A post: Save post, View in FeedVault
// ---------------------------------------------------------------------------

// The page with its fixture's tiles, once FeedVault said which it has.
async function tiles(href, fixture, saved) {
  const page = load(href, fixture);
  page.run("flush()");
  await answer(call(page, "POST", "/api/saved"), 200, { saved });
  const tile = (h) => page.document.querySelectorAll("a").find((a) => a.getAttribute("href") === h);
  const save = (h) => tile(h).querySelector(".fv-tile-save");
  const look = (h) => (tile(h).querySelector(".fv-badge") ? "saved" : save(h)?.dataset.state || null);
  const sent = () => page.calls.filter((c) => c.url.endsWith("/api/save")).map((c) => JSON.parse(c.data));
  return { page, tile, look, save, sent };
}

test("a post FeedVault does not have: Save post, its states, then View in FeedVault", async () => {
  const t = await tiles("https://x.com/someone/status/1800000000000000001", "x-post.html", []);
  const page = t.page;
  open(page);
  assert.deepEqual(heads(page), ["This post"]);
  assert.deepEqual(labels(page), ["Save post", ...COMMON]);
  assert.deepEqual(status(page), ["", "FeedVault"]);
  click(page, "Save post", false);
  assert.deepEqual(t.sent(), []);
  click(page, "Save post");
  assert.deepEqual(t.sent(), [{ url: "https://x.com/someone/status/1800000000000000001" }]);
  assert.deepEqual(labels(page), ["Sending…", ...COMMON]);
  assert.deepEqual(status(page), ["busy", "FeedVault: saving this post (sending)"]);
  await answer(call(page, "POST", "/api/save"), 200, { ok: true, have: false, job: { id: 5, state: "queued" } });
  assert.equal(itemOf(page, "Queued").getAttribute("title"), "Job #5");
  await answer(call(page, "GET", "/api/jobs/5"), 200, { id: 5, state: "running" });
  assert.deepEqual(labels(page), ["Saving…", ...COMMON]);
  assert.equal(opened(page), true);
  page.run("applyJob('twitter:1800000000000000001', { id: 5, state: 'done', result: { post: {} } })");
  assert.deepEqual(labels(page), ["View in FeedVault", ...COMMON]);
  const view = itemOf(page, "View in FeedVault");
  assert.equal(view.getAttribute("href"), "http://localhost:3380/p/twitter/1800000000000000001");
  assert.deepEqual(status(page), ["ok", "FeedVault: this post is in FeedVault"]);
  assert.equal(t.look("/someone/status/1800000000000000001/photo/1"), "saved");
});

test("a post FeedVault has: View in FeedVault only, also on another page of the post", async () => {
  const t = await tiles("https://x.com/someone/status/1800000000000000001", "x-post.html", ["twitter:1800000000000000001"]);
  assert.deepEqual(labels(t.page), ["View in FeedVault", ...COMMON]);
  assert.deepEqual(status(t.page), ["ok", "FeedVault: this post is in FeedVault"]);
  go(t.page, "https://x.com/someone/status/1800000000000000001/analytics");
  assert.deepEqual(labels(t.page), ["View in FeedVault", ...COMMON]);
  // A page of a post FeedVault does not have, that is not the post itself: nothing.
  const other = await tiles("https://x.com/someone/status/1800000000000000001/analytics", null, []);
  assert.deepEqual(labels(other.page), COMMON);
});

test("a failed post save links to Settings for a missing tool; FeedVault not running turns the dot red", async () => {
  const t = await tiles("https://x.com/someone/status/1800000000000000001", "x-post.html", []);
  open(t.page);
  click(t.page, "Save post");
  await answer(call(t.page, "POST", "/api/save"), 200, { ok: true, have: false, job: { id: 3, state: "queued" } });
  await answer(call(t.page, "GET", "/api/jobs/3"), 200,
               { id: 3, state: "failed", message: "gallery-dl is not installed", result: { error: "missing" } });
  assert.deepEqual(labels(t.page), ["Failed — retry", ...COMMON]);
  assert.ok(notes(t.page).some((n) => n.querySelector("a")?.textContent === "Open Settings → Downloaders"));
  assert.deepEqual(status(t.page), ["failed", "FeedVault: this post's save failed: gallery-dl is not installed"]);
  click(t.page, "Failed — retry");
  assert.equal(t.sent().length, 2);

  const page = load("https://x.com/someone/status/1800000000000000001");
  page.run("flush()");
  call(page, "POST", "/api/saved").onerror();
  await settle();
  assert.deepEqual(status(page), ["failed", "FeedVault: FeedVault is not running"]);
  open(page);
  assert.deepEqual(labels(page), ["FeedVault is not running — retry", ...COMMON]);
  click(page, "FeedVault is not running — retry");
  assert.ok(call(page, "POST", "/api/save"));
  // Not a post page, FeedVault not running: the dot says it there too.
  const home = load("https://x.com/home", "x-profile.html");
  home.run("flush()");
  call(home, "POST", "/api/saved").onerror();
  await settle();
  assert.deepEqual(status(home), ["failed", "FeedVault: FeedVault is not running"]);
});

// ---------------------------------------------------------------------------
// A tile's Save
// ---------------------------------------------------------------------------

test("a Save on every image tile FeedVault does not have, the badge on those it has", async () => {
  const ig = await tiles("https://www.instagram.com/carol.cooks/", "instagram-profile.html", ["instagram:CSAVEME0002"]);
  assert.equal(ig.look("/carol.cooks/p/CSAVEME0001/"), "idle");
  assert.equal(ig.look("/carol.cooks/reel/CSAVEME0002/"), "saved");
  assert.equal(ig.look("/p/CSAVEME0003/"), "idle");
  assert.equal(ig.look("/p/CSAVEME0001/liked_by/"), null);           // a text link

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

// What a tile's Save shows: [look, glyph, aria-label, tooltip].
const icon = (b) => [b.dataset.state, b.textContent, b.getAttribute("aria-label"), b.getAttribute("title")];

test("a tile's Save is a small icon, its words in its label and tooltip", async () => {
  const t = await tiles("https://www.tiktok.com/@someone", "tiktok-profile.html", []);
  const one = "https://www.tiktok.com/@someone/video/7300000000000000001";
  assert.deepEqual(icon(t.save(one)), ["idle", "↓", "Save to FeedVault", "Save to FeedVault"]);
  assert.equal(t.save(one).getAttribute("aria-disabled"), null);
  t.save(one).dispatch("click", true);
  assert.deepEqual(icon(t.save(one)), ["busy", "", "Sending…", "Saving to FeedVault"]);
  assert.equal(t.save(one).getAttribute("aria-disabled"), "true");
  await answer(call(t.page, "POST", "/api/save"), 200, { ok: true, have: false, job: { id: 7, state: "queued" } });
  assert.deepEqual(icon(t.save(one)), ["busy", "", "Queued", "Queued: Job #7"]);
  await answer(call(t.page, "GET", "/api/jobs/7"), 200, { id: 7, state: "running" });
  assert.deepEqual(icon(t.save(one)), ["busy", "", "Saving…", "Saving: Job #7"]);
  t.page.run("applyJob('tiktok:7300000000000000001', { id: 7, state: 'failed', message: 'yt-dlp failed', result: {} })");
  assert.deepEqual(icon(t.save(one)), ["failed", "!", "Failed — retry", "yt-dlp failed"]);
  assert.match(SOURCE, /\.fv-tile-save \{[^}]*width: 22px; height: 22px; border-radius: 50%;/);
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
    assert.equal(t.save(tile).getAttribute("aria-label"), "Sending…");
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
  assert.equal(t.save(one).getAttribute("aria-label"), "Queued");
  t.save(one).dispatch("click", true);                // queued
  assert.equal(t.sent().length, 1);
  t.save(two).dispatch("click", true);                // another post: its own save
  assert.deepEqual(t.sent().map((b) => b.url), [one, two]);
  // Its job is followed: running, then done, and the tile turns saved.
  await answer(call(t.page, "GET", "/api/jobs/7"), 200, { id: 7, state: "running" });
  assert.equal(t.save(one).getAttribute("aria-label"), "Saving…");
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
  await settle();
  assert.deepEqual(icon(t.save(tile)), ["offline", "!", "Offline — retry", "FeedVault is not running — click to retry"]);
});

test("a tile reused for another post: its Save sends nothing until painted again", async () => {
  const t = await tiles("https://x.com/someone/media", "x-profile.html", []);
  const a = t.tile("/someone/status/1800000000000000001/photo/1");
  const b = a.querySelector(".fv-tile-save");
  a.setAttribute("href", "/someone/status/1800000000000000004/photo/1");
  b.dispatch("click", true);
  assert.deepEqual(t.sent(), []);
});

test("the bubble's Save post and the post's tile show the same save", async () => {
  const t = await tiles("https://x.com/someone/status/1800000000000000001", "x-post.html", []);
  assert.deepEqual(labels(t.page), ["Save post", ...COMMON]);
  t.save("/someone/status/1800000000000000001/photo/1").dispatch("click", true);
  assert.deepEqual(labels(t.page), ["Sending…", ...COMMON]);
  assert.equal(status(t.page)[0], "busy");
  // Save post sends nothing more while it is under way.
  t.page.run("startSave('twitter:1800000000000000001')");
  assert.equal(t.sent().length, 1);
  await answer(call(t.page, "POST", "/api/save"), 200, { ok: true, have: false, job: { id: 5, state: "running" } });
  assert.deepEqual(labels(t.page), ["Saving…", ...COMMON]);
  assert.equal(t.look("/someone/status/1800000000000000001/photo/1"), "busy");
});

test("X: a tile's Save sits top right, clear of X's own button; elsewhere bottom right", async () => {
  const x = await tiles("https://x.com/someone/media", "x-profile.html", []);
  assert.equal(x.save("/someone/status/1800000000000000001/photo/1").getAttribute("data-fv-site"), "x");
  const ig = await tiles("https://www.instagram.com/carol.cooks/", "instagram-profile.html", []);
  assert.equal(ig.save("/p/CSAVEME0003/").getAttribute("data-fv-site"), "instagram");
  const tt = await tiles("https://www.tiktok.com/@someone", "tiktok-profile.html", []);
  assert.equal(tt.save("https://www.tiktok.com/@someone/video/7300000000000000001").getAttribute("data-fv-site"), "tiktok");
  assert.match(SOURCE, /\.fv-tile-save\[data-fv-site="x"\] \{ top: 6px; bottom: auto; \}/);
  assert.doesNotMatch(SOURCE, /data-fv-site="(?!x")/);
});

test("the bubble's own links are not tiles", async () => {
  // On Instagram, a link to FeedVault's /p/<platform>/<id> looks like a post's path.
  const t = await tiles("https://www.instagram.com/p/CSAVEME0001/", null, ["instagram:CSAVEME0001"]);
  assert.equal(itemOf(t.page, "View in FeedVault").getAttribute("href"), "http://localhost:3380/p/instagram/CSAVEME0001");
  t.page.run("paint()");
  assert.equal(itemOf(t.page, "View in FeedVault").querySelector(":scope > .fv-badge"), null);
  assert.equal(t.page.run("pending.size"), 0);
});

// Instagram's profile grid as it is laid out: a row of tiles, each an
// inline link round a positioned media box (the image in it) and the
// hover overlay; ``video``: the tile that plays one.
async function igGrid(codes, { saved = [], video = null } = {}) {
  const page = load("https://www.instagram.com/carol.cooks/");
  const row = page.document.body.appendChild(new Node("div"));
  const grid = codes.map((code) => {
    const a = row.appendChild(new Node("div")).appendChild(new Node("a"));
    a.setAttribute("href", `/carol.cooks/p/${code}/`);
    a.style.display = "inline";
    const media = a.appendChild(new Node("div"));
    media.style.position = "relative";
    const img = media.appendChild(new Node("div")).appendChild(new Node("img"));
    a.appendChild(new Node("div"));
    const player = code === video ? media.appendChild(new Node("video")) : null;
    return { a, media, img, player, save: () => a.querySelector(".fv-tile-save") };
  });
  page.run("paint(); flush()");
  await answer(call(page, "POST", "/api/saved"), 200, { saved: saved.map((c) => `instagram:${c}`) });
  const sent = () => page.calls.filter((c) => c.url.endsWith("/api/save")).map((c) => JSON.parse(c.data));
  return { page, grid, sent };
}

test("Instagram's grid: each tile's Save and badge sit on its own image, and save its own post", async () => {
  const { grid: [left, right], sent } = await igGrid(["CLEFT00001", "CRIGHT0001"]);
  // In the tile's media box, not the inline link (whose box has no width:
  // what sat at its right sat on the tile to the left); the link untouched.
  for (const t of [left, right]) {
    assert.equal(t.save().parentNode, t.media);
    assert.equal(t.save().closest("a"), t.a);
    assert.equal(t.a.style.position, undefined);
    assert.equal(t.media.style.position, "relative");
  }
  const e = right.save().dispatch("click", true);
  assert.ok(e.defaultPrevented && e.stopped);
  assert.deepEqual(sent(), [{ platform: "instagram", shortcode: "CRIGHT0001" }]);
  left.save().dispatch("click", true);
  assert.deepEqual(sent().map((b) => b.shortcode), ["CRIGHT0001", "CLEFT00001"]);

  // Shown for the tile under the pointer, or the keyboard's focus: not for
  // one that only holds focus (Instagram gives it back to the tile whose
  // dialog closed).
  assert.match(SOURCE, /a:hover \.fv-tile-save, a:focus-visible \.fv-tile-save, \.fv-tile-save:focus-visible,/);
  assert.doesNotMatch(SOURCE, /:focus-within|:hover > \.fv-tile-save/);

  const saved = await igGrid(["CLEFT00001", "CRIGHT0001"], { saved: ["CRIGHT0001"] });
  assert.equal(saved.grid[1].a.querySelector(".fv-badge").parentNode, saved.grid[1].media);
  assert.equal(saved.grid[0].a.querySelector(".fv-badge"), null);
});

test("a tile's Save is sent for the tile it is in at the click", async () => {
  const { page, grid: [left, right], sent } = await igGrid(["CLEFT00001", "CRIGHT0001"]);
  // The site moved the left tile's box into the right one: its Save sends
  // nothing (it showed another post) until painted again, then the right.
  const b = left.save();
  right.media.appendChild(b);
  right.save().remove();
  b.dispatch("click", true);
  assert.deepEqual(sent(), []);
  page.run("paint()");
  b.dispatch("click", true);
  assert.deepEqual(sent(), [{ platform: "instagram", shortcode: "CRIGHT0001" }]);
});

test("a reel tile's hover preview keeps its Save and badge; the video's clicks and keys go through", async () => {
  const { page, grid: [still, reel], sent } = await igGrid(["CSTILL0001", "CREEL00001"], { video: "CREEL00001" });
  assert.ok(still.save());
  assert.equal(reel.save().parentNode, reel.media);
  assert.equal(reel.a.style.position, undefined);
  const e0 = reel.save().dispatch("click", true);
  assert.ok(e0.defaultPrevented && e0.stopped);
  assert.deepEqual(sent(), [{ platform: "instagram", shortcode: "CREEL00001" }]);
  // Our nodes listen to nothing but the Save's own events: none on the
  // video, the tile or its boxes.
  for (const n of [reel.a, reel.media, reel.player]) assert.deepEqual(Object.keys(n.listeners), []);
  assert.match(SOURCE, /\.\$\{BADGE_CLASS\} \{[^}]*pointer-events: none;/);
  let played = 0;
  reel.a.addEventListener("click", () => { played += 1; });
  open(page);
  for (const [type, props] of [["pointerdown", {}], ["mousedown", {}], ["pointerup", {}], ["click", {}],
                               ["keydown", { key: " " }], ["keydown", { key: "k" }], ["keydown", { key: "Escape" }]]) {
    const e = reel.player.dispatch(type, true, props);
    assert.ok(!e.defaultPrevented && !e.stopped, type);
  }
  assert.equal(played, 1);
  assert.ok(!opened(page));                          // a click outside still closes the menu
  // A preview showing up on hover keeps the very same Save: nothing flickers.
  const b = still.save();
  const preview = still.media.appendChild(new Node("video"));
  page.run("paint()");
  assert.equal(still.save(), b);
  preview.remove();
  page.run("paint()");
  assert.equal(still.save(), b);
  // A click on a tile's image opens the post as it would.
  const e = still.img.dispatch("click", true);
  assert.ok(!e.defaultPrevented && !e.stopped);
});

test("a saved reel's badge stays through its hover preview", async () => {
  const { page, grid: [reel] } = await igGrid(["CREEL00001"], { saved: ["CREEL00001"] });
  const badge = reel.a.querySelector(".fv-badge");
  assert.ok(badge);
  reel.media.appendChild(new Node("video"));
  page.run("paint()");
  assert.equal(reel.a.querySelector(".fv-badge"), badge);
});

test("a link round a real player, with its controls, gets nothing over them", async () => {
  const { page, grid: [post] } = await igGrid(["CPLAYER001"]);
  assert.ok(post.save());
  const v = post.media.appendChild(new Node("video"));
  v.setAttribute("controls", "");
  page.run("paint()");
  assert.equal(post.save(), null);
  const e = v.dispatch("click", true);
  assert.ok(!e.defaultPrevented && !e.stopped);
});

// ---------------------------------------------------------------------------
// Link-in-bio pages: Save links
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
                              ["https://link.me/somebody", "https://link.me/somebody"],
                              ["https://linktr.ee/", null], ["https://linktr.ee/login", null], ["https://linktr.ee/s/about", null],
                              ["https://linktr.ee/somebody/extra", null], ["https://linktr.ee/...", null]]) {
    const p = load(href);
    assert.equal(p.run("SITE"), null, href);
    assert.equal(p.run("bioPage()"), page, href);
    assert.equal(p.calls.length, 0, href);                          // nothing is asked until a click
    assert.deepEqual(labels(p), page ? ["Save links", ...COMMON] : COMMON, href);
    assert.deepEqual(heads(p), page ? ["Link-in-bio page"] : [], href);
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

test("a Linkme page's links, none of its media CDN or footer", () => {
  const page = load("https://link.me/somebody");
  page.run(`for (const [href, text] of [["https://media.link.me/_resize/image/width=64/images/a.png", ""],
                                        ["https://twitch.tv/somebody", "twitch"],
                                        ["https://www.instagram.com/somebody?igsh=AbCd&utm_source=qr", "instagram"],
                                        ["https://about.link.me/privacypolicy", "Privacy Policy"],
                                        ["https://about.link.me/termsandconditions", "Terms"],
                                        ["https://link.me/somebody", "LinkMe"]]) {
              const a = document.body.appendChild(document.createElement("a"));
              a.setAttribute("href", href);
              a.text = text; }`);
  assert.deepEqual(JSON.parse(page.run("JSON.stringify(bioLinks())")), [
    { url: "https://twitch.tv/somebody", title: "twitch" },
    { url: "https://www.instagram.com/somebody?igsh=AbCd&utm_source=qr", title: "instagram" },
  ]);
});

const PEOPLE = { people: [{ id: 3, name: "Some Body" }, { id: 4, name: "Other One" }], recent: [4] };
const options = (select) => select.all().filter((n) => n.tagName === "OPTION").map((o) => o.textContent);
// The links in the menu's notes (the bubble's own items are links too).
const noteLink = (page) => notes(page).map((n) => n.querySelector("a")).find(Boolean);

// The link-in-bio page with its fixture, the picker open in the menu.
async function picker() {
  const page = load("https://linktr.ee/somebody", "linktree-page.html");
  open(page);
  click(page, "Save links");
  assert.deepEqual(labels(page), ["Loading people…", ...COMMON]);
  await answer(call(page, "GET", "/api/links/people"), 200, PEOPLE);
  const select = menuOf(page).querySelector("select");
  const imports = () => page.calls.filter((c) => c.url.endsWith("/api/links/import"));
  return { page, select, imports };
}

test("Save links: a person picked, the page's links sent, what was saved said", async () => {
  const t = await picker();
  assert.equal(t.page.calls[0].headers["X-FeedVault"], "1");
  assert.match(noteText(t.page), /the 5 links it shows/);
  assert.deepEqual(labels(t.page), ["Save links", "Cancel", ...COMMON]);
  // No one by default; the recent person first, then everyone else.
  assert.equal(t.select.value, "none");
  assert.deepEqual(options(t.select), ["No person (Unsorted)", "Other One", "Some Body"]);
  // The search narrows the list; the person chosen stays in it.
  const find = menuOf(t.page).querySelector("input");
  t.select.value = "3";
  t.select.dispatch("change", true);
  find.value = "zzz";
  find.dispatch("input", true);
  assert.deepEqual(options(t.select), ["No person (Unsorted)", "Some Body"]);
  find.value = "OTH";
  find.dispatch("input", true);
  assert.deepEqual(options(t.select), ["No person (Unsorted)", "Other One", "Some Body"]);
  assert.equal(t.select.value, "3");

  click(t.page, "Save links");
  assert.equal(t.imports().length, 1);
  assert.deepEqual(JSON.parse(t.imports()[0].data), {
    page_url: "https://linktr.ee/somebody", person: 3, links: [
      { url: "https://www.instagram.com/somebody/", title: "Instagram" },
      { url: "https://x.com/somebody", title: "My X" },
      { url: "https://shop.example/somebody?ref=linktree", title: "Shop my picks" },
      { url: "https://www.youtube.com/@somebody", title: "YouTube" },
      { url: "http://old-site.example/", title: "My old site" }] });
  assert.deepEqual(labels(t.page), ["Saving links…", ...COMMON]);
  assert.equal(status(t.page)[0], "busy");
  click(t.page, "Saving links…");                                      // under way: nothing more
  assert.equal(t.imports().length, 1);
  await answer(t.imports()[0], 200, { ok: true, url: "https://linktr.ee/somebody", person: 3,
                                      links: { saved: 4, already: 1, shared: 1, over: 0, dropped: 0 } });
  assert.match(noteText(t.page), /4 saved, 1 already there, 1 now theirs too/);
  assert.equal(noteLink(t.page).getAttribute("href"), "http://localhost:3380/people/3");
  assert.deepEqual(status(t.page), ["ok", "FeedVault: this page's links are in FeedVault"]);
  assert.equal(opened(t.page), true);
});

test("Save links to no one", async () => {
  const t = await picker();
  click(t.page, "Save links");
  assert.equal(JSON.parse(t.imports()[0].data).person, null);
  await answer(t.imports()[0], 200, { ok: true, person: null, links: { saved: 6, already: 0, shared: 0, over: 2, dropped: 1 } });
  assert.match(noteText(t.page), /6 saved, 0 already there, 2 over the limit, 1 refused/);
  assert.equal(noteLink(t.page).getAttribute("href"), "http://localhost:3380/links");
});

test("a page script's clicks and changes in the menu do nothing", async () => {
  const page = load("https://linktr.ee/somebody", "linktree-page.html");
  open(page);
  click(page, "Save links", false);
  assert.equal(page.calls.length, 0);
  const t = await picker();
  t.select.value = "4";
  t.select.dispatch("change", false);                                  // not the user's: the choice stays
  assert.equal(t.select.value, "none");
  assert.equal(t.page.run("bios.get('https://linktr.ee/somebody').person"), "none");
  click(t.page, "Save links", false);
  click(t.page, "Cancel", false);
  assert.equal(t.imports().length, 0);
  assert.ok(itemOf(t.page, "Cancel"));
  click(t.page, "Cancel");
  assert.deepEqual(labels(t.page), ["Save links", ...COMMON]);
});

test("Save links when the import is off, or FeedVault does not answer", async () => {
  const t = await picker();
  click(t.page, "Save links");
  await answer(t.imports()[0], 403, { ok: false, error: "link-in-bio import is off: turn it on in Settings → Downloads" });
  assert.match(noteText(t.page), /import is off/);
  assert.equal(noteLink(t.page).textContent, "Open Settings → Downloads");
  assert.equal(status(t.page)[0], "failed");
  const gone = load("https://linktr.ee/somebody");
  open(gone);
  click(gone, "Save links");
  gone.calls[0].onerror();
  await settle();
  assert.deepEqual(labels(gone), ["Save links — retry", ...COMMON]);
  assert.equal(why(itemOf(gone, "Save links — retry")), "FeedVault is not running");
});

// ---------------------------------------------------------------------------
// Every page: Add this page to Links, Open FeedVault
// ---------------------------------------------------------------------------

test("the menu command adds the page's address and title, to no one; the toast stays clear of the bubble", async () => {
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
    await answer(sent[0], 409, { ok: false, error: "that link is saved already", link: { people: [{ id: 3, name: "Some Body" }, { id: 4, name: "Their Band" }] } });
    const toast = page.document.querySelector(".fv-toast");
    assert.match(toast.textContent, /Already in FeedVault Links, with Some Body, Their Band/);
    // The bubble bottom right: the toast top right.
    assert.deepEqual([toast.style.top, toast.style.right, toast.style.bottom, toast.style.left],
                     ["18px", "18px", undefined, undefined]);
  }
  const page = load("https://www.tiktok.com/@someone");
  page.menu[0].fn();
  await answer(page.calls.at(-1), 200, { ok: true, link: { id: 9 } });
  assert.match(page.document.querySelector(".fv-toast").textContent, /Added to FeedVault Links \(Unsorted\)/);
  // The bubble moved top left: the toast goes bottom left.
  drag(btnOf(page), true, [1240, 760], [0, 0]);
  page.menu[0].fn();
  const toast = page.document.querySelector(".fv-toast");
  assert.deepEqual([toast.style.bottom, toast.style.left, toast.style.top, toast.style.right],
                   ["18px", "18px", undefined, undefined]);
});

test("Add this page to Links in the bubble's menu: its progress there, no toast over it", async () => {
  const page = load("https://x.com/home");
  open(page);
  click(page, "Add this page to Links", false);
  assert.equal(page.calls.length, 0);
  click(page, "Add this page to Links");
  assert.deepEqual(page.calls.map((c) => [c.method, c.url]), [["POST", "http://localhost:3380/api/links"]]);
  assert.deepEqual(labels(page), ["Adding to Links…", ...COMMON.slice(1)]);
  assert.equal(status(page)[0], "busy");
  click(page, "Adding to Links…");
  assert.equal(page.calls.length, 1);
  await answer(page.calls[0], 200, { ok: true, link: { id: 9 } });
  assert.equal(page.document.querySelector(".fv-toast"), null);
  assert.match(noteText(page), /Added to FeedVault Links \(Unsorted\)/);
  assert.equal(noteLink(page).getAttribute("href"), "http://localhost:3380/links");
  assert.equal(opened(page), true);
  // Another page: its own Add.
  go(page, "https://x.com/explore");
  assert.deepEqual(labels(page), COMMON);
  assert.equal(noteText(page), "");
});

test("Open FeedVault and the other links open FeedVault on a real click only", () => {
  const page = load("https://x.com/home");
  open(page);
  const a = itemOf(page, "Open FeedVault");
  assert.deepEqual([a.tagName, a.getAttribute("href"), a.getAttribute("target")], ["A", "http://localhost:3380/", "_blank"]);
  assert.equal(click(page, "Open FeedVault", false).defaultPrevented, true);
  assert.equal(opened(page), true);
  assert.equal(click(page, "Open FeedVault").defaultPrevented, false);
  assert.equal(opened(page), false);                                   // it opened in a new tab
});
