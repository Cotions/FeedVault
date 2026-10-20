// ==UserScript==
// @name         FeedVault
// @namespace    https://github.com/Cotions/feedvault
// @version      0.6.1
// @description  Marks Instagram, X and TikTok posts you already have in FeedVault, saves the ones you don't, and keeps link-in-bio pages' links in its Links
// @author       Cotions
// @match        https://www.instagram.com/*
// @match        https://x.com/*
// @match        https://twitter.com/*
// @match        https://www.tiktok.com/*
// @match        https://linktr.ee/*
// @match        https://beacons.ai/*
// @match        https://lnk.bio/*
// @match        https://solo.to/*
// @match        https://campsite.bio/*
// @match        https://linkin.bio/*
// @match        https://allmylinks.com/*
// @match        https://link.me/*
// @connect      localhost
// @connect      127.0.0.1
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @grant        GM_registerMenuCommand
// @grant        GM_getValue
// @grant        GM_setValue
// @run-at       document-idle
// @updateURL    http://localhost:3380/userscript/feedvault.user.js
// @downloadURL  http://localhost:3380/userscript/feedvault.user.js
// ==/UserScript==

// Only this script talks to FeedVault, through GM_xmlhttpRequest: the page
// gets no function, message handler or data from it, and nothing is read
// from the page's own JavaScript. Post ids and profile names come from
// location.pathname and link hrefs, checked against the patterns below.
const API_BASE = "http://localhost:3380";
// Backend denies every API call without this header. Ordinary web pages cannot
// attach a custom header cross-origin; this privileged script can.
const FV_HEADERS = { "X-FeedVault": "1", "Content-Type": "application/json" };
const BADGE_CLASS = "fv-badge";
const TILE_SAVE_CLASS = "fv-tile-save";
const MARKED_ATTR = "data-fv";
// "Not saved" answers go stale when a download lands, so ask again after this.
const MISS_TTL_MS = 30_000;
const JOB_POLL_MS = 1500;
const SETTINGS_ERRORS = new Set(["missing", "login_required"]);   // fixed in Settings → Downloaders

// ---------------------------------------------------------------------------
// The sites. Everything that knows a site's pages is here, one entry each:
// which links are posts (tiles), which page or tile link is a post (a Save
// button) or a profile (Sync profile), and what is sent for them. When a
// site changes its layout or its paths, a selector or pattern stops
// matching and that part does nothing: no button, no badge, no error.
// ---------------------------------------------------------------------------

// An X or TikTok post id: digits, as POST /api/save accepts them.
const DIGITS = "[1-9][0-9]{0,19}";

const SITES = {
  instagram: {
    hosts: ["www.instagram.com"],
    platform: "instagram",
    // Grid tiles and feed links: every anchor that points at a post.
    tiles: 'a[href*="/p/"], a[href*="/reel/"]',
    tileRe: /^\/(?:[^/]+\/)?(?:p|reel|reels|tv)\/([A-Za-z0-9_-]+)/,
    // A post page or dialog the Save button shows on (the grid's dialog
    // sets the same path), and a tile's link its Save is for: the whole
    // path, and a shortcode as POST /api/save accepts it.
    postRe: /^\/(?:[A-Za-z0-9._]{1,30}\/)?(?:p|reel|reels|tv)\/([A-Za-z0-9_-]{5,40})\/?$/,
    saveBody: (m) => ({ platform: "instagram", shortcode: m[1] }),
    // A profile page (or one of its tabs) the Sync profile button shows on.
    profileRe: /^\/([A-Za-z0-9._]{1,30})\/(?:(?:reels|tagged)\/)?$/,
    // First path parts that are Instagram's own pages, not profiles.
    notProfiles: new Set(["p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about",
      "developer", "legal", "web", "emails", "challenge", "session", "graphql", "api", "privacy", "terms",
      "lite", "your_activity", "notifications", "nametag", "settings", "login", "signup", "_n", "_u"]),
    // How GET /api/sources/resolve and POST /api/sources take the profile.
    resolveQuery: (name) => ({ tool: "instaloader", url: name }),
    addBody: (name) => ({ tool: "instaloader", target: name }),
  },
  x: {
    hosts: ["x.com", "twitter.com"],
    platform: "twitter",
    // Photos and videos in timelines and the Media tab link to their post.
    tiles: 'a[href*="/status/"]',
    tileRe: new RegExp(`^/[A-Za-z0-9_]{1,15}/status/(${DIGITS})(?:/|$)`),
    postRe: new RegExp(`^/([A-Za-z0-9_]{1,15})/status/(${DIGITS})(?:/(?:photo|video)/[1-4])?/?$`),
    saveBody: (m) => ({ url: `https://x.com/${m[1]}/status/${m[2]}` }),
    postId: (m) => m[2],
    profileRe: /^\/([A-Za-z0-9_]{1,15})(?:\/(?:media|with_replies|highlights|articles|likes))?\/?$/,
    notProfiles: new Set(["home", "explore", "notifications", "messages", "i", "settings", "search", "compose",
      "login", "logout", "signup", "tos", "privacy", "jobs", "bookmarks", "lists", "communities", "premium",
      "premium_sign_up", "hashtag", "account", "intent", "share", "download", "about", "help", "who_to_follow",
      "connect_people", "topics", "follower_requests", "keyboard_shortcuts", "display", "sw.js", "grok"]),
    resolveQuery: (name) => ({ url: `https://x.com/${name}` }),
    addBody: (name) => ({ target: `https://x.com/${name}` }),
  },
  tiktok: {
    hosts: ["www.tiktok.com"],
    platform: "tiktok",
    // A profile's grid and search results link to the video.
    tiles: 'a[href*="/video/"]',
    tileRe: new RegExp(`^/@[A-Za-z0-9._]{1,24}/video/(${DIGITS})(?:/|$)`),
    postRe: new RegExp(`^/@([A-Za-z0-9._]{1,24})/video/(${DIGITS})/?$`),
    saveBody: (m) => ({ url: `https://www.tiktok.com/@${m[1]}/video/${m[2]}` }),
    postId: (m) => m[2],
    profileRe: /^\/@([A-Za-z0-9._]{1,24})\/?$/,
    notProfiles: new Set(),
    resolveQuery: (name) => ({ url: `https://www.tiktok.com/@${name}` }),
    addBody: (name) => ({ target: `https://www.tiktok.com/@${name}` }),
  },
};

const SITE = Object.values(SITES).find((s) => s.hosts.includes(location.hostname)) || null;
const SITE_KEY = Object.keys(SITES).find((k) => SITES[k] === SITE) || null;

// Link-in-bio sites: a person's page is one path on the host. The same hosts
// as backend/biofetch.py SITES and the @match lines above (a test checks the
// three agree). ``also``: the site's other names, its own pages too.
const BIO_SITES = {
  "linktr.ee": { also: ["linktree.com"] },
  "beacons.ai": {},
  "lnk.bio": {},
  "solo.to": {},
  "campsite.bio": {},
  "linkin.bio": { also: ["later.com"] },
  "allmylinks.com": {},
  "link.me": {},
};
// First path parts that are the sites' own pages, not a person's.
const BIO_NOT_PAGES = new Set(["login", "logout", "signin", "signup", "register", "join", "admin", "dashboard",
  "settings", "account", "auth", "help", "support", "blog", "about", "pricing", "privacy", "terms", "legal",
  "cookies", "s", "features", "templates", "discover", "explore", "marketplace", "careers", "press", "app", "api",
  "contact", "search"]);
const BIO_MAX_LINKS = 200;                     // biofetch.MAX_SAVED: what one import saves at most
const BIO_HOST = !SITE && Object.prototype.hasOwnProperty.call(BIO_SITES, location.hostname) ? location.hostname : null;

// The post id a path (or a link's href) is about: "<platform>:<id>", else
// null. Each site's tileRe captures the id alone.
function postOf(href, re = SITE.tileRe) {
  try {
    const m = new URL(href, location.origin).pathname.match(re);
    return m ? `${SITE.platform}:${m[1]}` : null;
  } catch {
    return null;
  }
}

// The post a path is, with what POST /api/save is sent for it, or null.
function postAt(path) {
  const m = path.match(SITE.postRe);
  if (!m) return null;
  return { id: `${SITE.platform}:${SITE.postId ? SITE.postId(m) : m[1]}`, body: SITE.saveBody(m) };
}

// The post the open page is.
function savePost() {
  return postAt(location.pathname);
}

// The post a tile's link is, read from its href now and held to the same
// pattern as a post page (postRe, the whole path), else null: no Save on it.
// What that leaves, per site: Instagram's /p/<code>/ and /<name>/reel/<code>/
// links carry the shortcode, all a save needs. X's photo and video links
// (/<name>/status/<id>/photo/1) carry the name; one without it does not
// match, and FeedVault reads only the id anyway (/i/status/<id> is sent as
// is, it accepts that link). TikTok's grid and search links carry @<name>,
// which FeedVault needs to build the video's link: one without it, or a
// photo post (yt-dlp downloads videos), gets no Save. A link to another
// host (an absolute href) gets none either.
function tilePost(href) {
  try {
    const u = new URL(href, location.origin);
    if (u.protocol !== "https:" || !SITE.hosts.includes(u.hostname)) return null;
    const post = postAt(u.pathname);
    return post && post.id === postOf(href) ? post : null;
  } catch {
    return null;
  }
}

function postPath(id) {
  const [platform, code] = id.split(":");
  return `/p/${platform}/${code}`;
}

if (SITE || BIO_HOST) GM_addStyle(`
  .${BADGE_CLASS} {
    position: absolute; top: 6px; left: 6px; z-index: 5;
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 7px; border-radius: 999px;
    font: 700 11px/16px ui-monospace, "JetBrains Mono", monospace;
    color: #e8ecf6; background: rgba(10, 12, 17, 0.82);
    box-shadow: 0 0 0 1px rgba(74, 222, 128, 0.55), 0 0 14px -3px rgba(74, 222, 128, 0.6);
    pointer-events: none;
  }
  .${BADGE_CLASS}::before {
    content: ""; width: 7px; height: 7px; border-radius: 50%;
    background: #4ade80; box-shadow: 0 0 6px #4ade80;
  }
  /* The corner panel: where place() puts it (right/bottom, or left/top
     while it is dragged by its grip). */
  .fv-panel {
    position: fixed; right: 18px; bottom: 18px; z-index: 2147483000;
    display: flex; flex-direction: column; align-items: flex-end; gap: 4px;
    max-width: 320px; font: 600 12px/16px ui-monospace, "JetBrains Mono", monospace;
  }
  .fv-body { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
  .fv-grip {
    cursor: grab; user-select: none; touch-action: none;
    padding: 0 8px; border-radius: 999px; color: #e8ecf6; background: rgba(10, 12, 17, 0.88);
    box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.35); font: 700 11px/14px ui-monospace, monospace;
  }
  .fv-grip::before { content: "⠿ FV"; }
  .fv-grip:hover { box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.8); }
  .fv-grip[data-drag] { cursor: grabbing; }
  .fv-btn {
    all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 11px; border-radius: 999px; color: #e8ecf6; background: rgba(10, 12, 17, 0.88);
    box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.35); font: inherit; text-decoration: none;
  }
  .fv-btn:hover { box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.8); }
  .fv-btn[aria-disabled="true"] { cursor: default; opacity: 0.85; }
  .fv-btn[data-state="done"] { box-shadow: 0 0 0 1px rgba(74, 222, 128, 0.7); }
  .fv-btn[data-state="failed"], .fv-btn[data-state="offline"] { box-shadow: 0 0 0 1px rgba(248, 113, 113, 0.8); }
  .fv-btn[data-state="confirm"], .fv-btn[data-state="dashboard"] { box-shadow: 0 0 0 1px rgba(250, 204, 21, 0.8); }
  .fv-btn[data-state="nosync"] { box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.35); opacity: 0.7; }
  .fv-note {
    padding: 4px 9px; border-radius: 8px; color: #e8ecf6; background: rgba(10, 12, 17, 0.88);
    font-weight: 400; overflow-wrap: anywhere;
  }
  .fv-note a { color: #93c5fd; }
  .fv-row { display: flex; gap: 6px; }
  /* The link-in-bio panel's person picker. */
  .fv-field {
    all: unset; box-sizing: border-box; width: 260px; padding: 4px 9px; border-radius: 8px;
    color: #e8ecf6; background: rgba(10, 12, 17, 0.92); box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.35);
    font: inherit; font-weight: 400;
  }
  select.fv-field { cursor: pointer; }
  .fv-field:focus { box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.8); }
  .fv-field option, .fv-field optgroup { color: #e8ecf6; background: #0a0c11; }
  /* What the menu command did: top right, clear of the corner panel. */
  .fv-toast {
    position: fixed; right: 18px; top: 18px; z-index: 2147483000; max-width: 320px;
    font: 600 12px/16px ui-monospace, "JetBrains Mono", monospace;
  }
  /* A tile's Save: bottom right, clear of the "saved" badge's corner, of
     Instagram's carousel and reel icons (top right) and of TikTok's view
     count (bottom left). On X, top right: X puts its own button on media
     bottom right (Grok's edit image), its ALT and duration labels bottom
     left. Shown while the tile is hovered or focused; always once a save
     is under way or failed. */
  .fv-tile-save {
    all: unset; box-sizing: border-box;
    position: absolute; right: 6px; bottom: 6px; z-index: 6;
    padding: 2px 8px; border-radius: 999px; cursor: pointer; white-space: nowrap;
    font: 700 11px/16px ui-monospace, "JetBrains Mono", monospace;
    color: #e8ecf6; background: rgba(10, 12, 17, 0.86);
    box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.45);
    opacity: 0; pointer-events: none; transition: opacity 0.12s;
  }
  :hover > .fv-tile-save, :focus-within > .fv-tile-save, .fv-tile-save:not([data-state="idle"]) {
    opacity: 1; pointer-events: auto;
  }
  .fv-tile-save[data-fv-site="x"] { top: 6px; bottom: auto; }
  .fv-tile-save:hover, .fv-tile-save:focus-visible { box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.9); }
  .fv-tile-save[data-state="busy"] { cursor: default; }
  .fv-tile-save[data-state="failed"], .fv-tile-save[data-state="offline"] { box-shadow: 0 0 0 1px rgba(248, 113, 113, 0.85); }
`);

// id -> { saved: bool, at: ms }
const cache = new Map();
let pending = new Set();
let timer = null;
let offline = false;                           // the last /api/saved went unanswered

function known(id) {
  const hit = cache.get(id);
  if (!hit) return null;
  if (!hit.saved && Date.now() - hit.at > MISS_TTL_MS) return null;
  return hit.saved;
}

// One FeedVault call. Resolves { status, body } (body null if not JSON), or
// null when FeedVault does not answer.
function api(method, path, body) {
  return new Promise((resolve) => {
    GM_xmlhttpRequest({
      method,
      url: `${API_BASE}${path}`,
      headers: FV_HEADERS,
      data: body === undefined ? undefined : JSON.stringify(body),
      timeout: 10000,
      onload: (r) => {
        let json = null;
        try { json = JSON.parse(r.responseText); } catch { /* not JSON */ }
        resolve({ status: r.status, body: json });
      },
      onerror: () => resolve(null),
      ontimeout: () => resolve(null),
    });
  });
}

// The ids FeedVault has, or null when it does not answer.
async function ask(ids) {
  const r = await api("POST", "/api/saved", { ids });
  return Array.isArray(r?.body?.saved) ? new Set(r.body.saved) : null;
}

async function flush() {
  timer = null;
  const ids = [...pending].slice(0, 400);
  ids.forEach((id) => pending.delete(id));
  if (!ids.length) return;
  const saved = await ask(ids);
  if ((saved === null) !== offline) { offline = saved === null; paint(); }
  if (saved === null) return;                 // backend offline: try again on the next scan
  const now = Date.now();
  ids.forEach((id) => cache.set(id, { saved: saved.has(id), at: now }));
  paint();
  if (pending.size) schedule();
}

function schedule() {
  if (!timer) timer = setTimeout(flush, 250);
}

function badge(text) {
  const el = document.createElement("span");
  el.className = BADGE_CLASS;
  el.textContent = text;
  return el;
}

// The tile holds what is laid over it.
function hold(a) {
  if (getComputedStyle(a).position === "static") a.style.position = "relative";
}

// Every anchor that points at a post, as the site's entry picks them: a
// saved one gets the badge, one known not saved its own Save.
function paintTiles() {
  for (const a of document.querySelectorAll(SITE.tiles)) {
    if (a.closest(".fv-panel")) continue;      // our own links to FeedVault
    const id = postOf(a.getAttribute("href"));
    if (!id) continue;
    const state = known(id);
    if (state === null) { pending.add(id); continue; }
    const has = a.querySelector(`:scope > .${BADGE_CLASS}`);
    const ctl = a.querySelector(`:scope > .${TILE_SAVE_CLASS}`);
    // Only tiles with an image get a badge or a Save; plain text links
    // (timestamps, "view all comments") stay untouched.
    const img = a.querySelector("img");
    if (state) {
      ctl?.remove();
      if (!has && img) {
        hold(a);
        a.appendChild(badge("saved"));
        a.setAttribute(MARKED_ATTR, "1");
      }
      continue;
    }
    has?.remove();
    const post = img ? tilePost(a.getAttribute("href")) : null;
    if (!post) { ctl?.remove(); continue; }
    paintTileSave(ctl || addTileSave(a), post.id);
  }
}

// A tile's Save. The tile is a link (and the sites route on their own
// click and press handlers): no click on it opens the post, a real one or
// not, and only a real one saves.
function addTileSave(a) {
  hold(a);
  const b = el("button", { class: TILE_SAVE_CLASS, type: "button", "data-fv-site": SITE_KEY });
  b.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!e.isTrusted) return;
    // The sites reuse a tile for another post as they scroll: what is sent
    // is read from its href now, and only for the post the button showed.
    const post = tilePost(a.getAttribute("href"));
    if (post && post.id === b.dataset.id) startSave(post.id, post);
  });
  for (const type of ["pointerdown", "pointerup", "mousedown", "mouseup"]) {
    b.addEventListener(type, (e) => e.stopPropagation());
  }
  a.appendChild(b);
  return b;
}

// The same states as the corner panel's button, in a word; why it failed
// in its tooltip.
function tileLook(s) {
  switch (s?.state) {
    case "sending": return ["busy", "Sending…", "Saving to FeedVault"];
    case "queued": return ["busy", "Queued", jobTitle(s)];
    case "running": return ["busy", "Saving…", jobTitle(s)];
    case "failed": {
      const where = SETTINGS_ERRORS.has(s.error) ? " (FeedVault: Settings → Downloaders)" : "";
      return ["failed", "Failed — retry", `${s.message || "Save failed"}${where}`];
    }
    case "offline": return ["offline", "Offline — retry", "FeedVault is not running"];
    default: return ["idle", "Save", "Save to FeedVault"];
  }
}

function paintTileSave(b, id) {
  const [look, text, title] = tileLook(saves.get(id));
  const sig = `${id}|${text}|${title}`;
  if (b.dataset.sig === sig) return;
  b.dataset.sig = sig;
  b.dataset.id = id;
  b.dataset.state = look;
  b.textContent = text;
  b.setAttribute("title", title);
  if (look === "busy") b.setAttribute("aria-disabled", "true");
  else b.removeAttribute("aria-disabled");
}

// ---------------------------------------------------------------------------
// Save button: a post page, or the dialog a grid opens (it sets the same path).
// A tile's Save (above) shares its state: the same post shows the same.
// ---------------------------------------------------------------------------

const ENDED = new Set(["done", "failed", "cancelled", "interrupted"]);
// post id -> { state: "sending" | "queued" | "running" | "failed" | "offline", job, message, error }
const saves = new Map();

function el(tag, attrs = {}, text = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

function link(href, text, cls = "") {
  return el("a", { href, target: "_blank", rel: "noreferrer", ...(cls ? { class: cls } : {}) }, text);
}

// The panel's spot, per site (hostname), kept in the script's own storage
// (never the page's): its right and bottom offsets, as the user dropped it.
// None: the site's default. X keeps its own floating buttons (Grok, the
// messages drawer) in the bottom-right corner: its panel starts left of them.
const PANEL_SPOTS = { x: { right: 90, bottom: 18 } };
const PANEL_SPOT = { right: 18, bottom: 18 };
const SPOT_KEY = `panel:${location.hostname}`;

function savedSpot() {
  const s = GM_getValue(SPOT_KEY, null);
  return s && Number.isFinite(s.right) && Number.isFinite(s.bottom) ? s : null;
}

const clamp = (v, max) => Math.max(0, Math.min(v, Math.max(0, max)));

// The panel at its spot, inside the window. It is anchored by its bottom
// right corner, as its buttons are: a longer note grows it up and left.
function place(p) {
  const s = savedSpot() || PANEL_SPOTS[SITE_KEY] || PANEL_SPOT;
  const r = p.getBoundingClientRect();
  Object.assign(p.style, { left: "", top: "",
    right: `${clamp(s.right, innerWidth - r.width)}px`, bottom: `${clamp(s.bottom, innerHeight - r.height)}px` });
}

// The panel's grip: drag it to move the panel, double-click it to put the
// panel back in its default spot. Only the user's own pointer acts.
function grip(p) {
  const g = el("div", { class: "fv-grip", title: "Drag to move, double-click to put back" });
  let drag = null;
  g.addEventListener("pointerdown", (e) => {
    e.stopPropagation();
    if (!e.isTrusted || e.button !== 0) return;
    e.preventDefault();
    const r = p.getBoundingClientRect();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, dx: e.clientX - r.left, dy: e.clientY - r.top, moved: false };
    g.setPointerCapture(e.pointerId);
    g.dataset.drag = "1";
  });
  g.addEventListener("pointermove", (e) => {
    if (!drag || !e.isTrusted || e.pointerId !== drag.id) return;
    // A few pixels' wobble is a click (a double-click's), not a move.
    if (!drag.moved && Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 4) return;
    const r = p.getBoundingClientRect();
    drag.moved = true;
    Object.assign(p.style, { right: "auto", bottom: "auto",
      left: `${clamp(e.clientX - drag.dx, innerWidth - r.width)}px`,
      top: `${clamp(e.clientY - drag.dy, innerHeight - r.height)}px` });
  });
  const drop = (e) => {
    if (!drag || !e.isTrusted || e.pointerId !== drag.id) return;
    const { moved } = drag;
    drag = null;
    g.removeAttribute("data-drag");
    if (moved) {
      const r = p.getBoundingClientRect();
      GM_setValue(SPOT_KEY, { right: Math.round(innerWidth - r.right), bottom: Math.round(innerHeight - r.bottom) });
    }
    place(p);
  };
  g.addEventListener("pointerup", drop);
  g.addEventListener("pointercancel", drop);
  g.addEventListener("dblclick", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!e.isTrusted) return;
    GM_setValue(SPOT_KEY, null);
    place(p);
  });
  return g;
}

// The panel in the corner: its grip, then one button (or link) and, below
// it, a note.
function panel(key) {
  let p = document.querySelector(".fv-panel");
  if (p && p.dataset.key === key) return p;
  p?.remove();
  p = el("div", { class: "fv-panel" });
  p.dataset.key = key;
  p.append(grip(p), el("div", { class: "fv-body" }));
  document.body.appendChild(p);
  place(p);
  return p;
}

function render(p, sig, build) {
  if (p.dataset.sig === sig) return;           // unchanged: keep the DOM (and focus) as it is
  p.dataset.sig = sig;
  p.querySelector(":scope > .fv-body").replaceChildren(...build());
  place(p);                                    // its new size, inside the window
}

// ``settings``: true for a link to Settings → Downloaders, or the link's own text.
function note(text, settings) {
  const n = el("div", { class: "fv-note" }, text);
  if (settings) n.append(el("br"), link(`${API_BASE}/settings`, settings === true ? "Open Settings → Downloaders" : settings));
  return n;
}

// Only a real click acts: the page's own scripts can call element.click()
// or dispatch events on the buttons, which would queue saves or add sources.
function button(state, text, onClick, title = "") {
  const b = el("button", { class: "fv-btn", type: "button", "data-state": state, ...(title ? { title } : {}) }, text);
  if (onClick) {
    b.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.isTrusted) onClick();
    });
  } else b.setAttribute("aria-disabled", "true");
  return b;
}

function dropPanel(prefix) {
  const p = document.querySelector(".fv-panel");
  if (p && p.dataset.key.startsWith(prefix)) p.remove();
}

// A post FeedVault has gets a link to it (on any page of the post); another,
// the Save button and its state (on the post itself).
function paintPage() {
  const id = postOf(location.pathname);
  const post = savePost();
  const fresh = id ? known(id) : null;
  if (id && fresh === null) pending.add(id);
  // While a stale "not saved" is asked again, keep showing it (no flicker).
  const have = fresh ?? (id ? cache.get(id)?.saved ?? null : null);
  if (have !== true && id !== post?.id) { dropPanel("post:"); return; }
  if (id && have === null && !saves.has(id) && offline) {
    const p = panel(`post:${id}`);
    render(p, "offline", () => [button("offline", "FeedVault is not running — retry", () => startSave(id))]);
    return;
  }
  if (!id || (have === null && !saves.has(id))) { dropPanel("post:"); return; }
  const p = panel(`post:${id}`);
  if (have) {
    render(p, "done", () => {
      const a = link(`${API_BASE}${postPath(id)}`, "In FeedVault", "fv-btn");
      a.dataset.state = "done";
      return [a];
    });
    return;
  }
  const s = saves.get(id);
  const st = s?.state || "idle";
  render(p, `${st}|${s?.job?.id || ""}|${s?.message || ""}`, () => {
    if (st === "sending") return [button("busy", "Sending…", null)];
    if (st === "queued") return [button("busy", "Queued", null, jobTitle(s))];
    if (st === "running") return [button("busy", "Saving…", null, jobTitle(s))];
    if (st === "failed") {
      return [button("failed", "Failed — retry", () => startSave(id), s.message || ""),
              note(s.message || "Save failed", SETTINGS_ERRORS.has(s.error))];
    }
    if (st === "offline") return [button("offline", "FeedVault is not running — retry", () => startSave(id))];
    return [button("idle", "Save to FeedVault", () => startSave(id))];
  });
}

function jobTitle(s) {
  const wait = s.state === "queued" && s.job.waits_until
    ? `, waits until ${new Date(s.job.waits_until * 1000).toLocaleTimeString()}` : "";
  return `Job #${s.job.id}${wait}`;
}

function setSave(id, value) {
  saves.set(id, value);
  paint();
}

const UNDER_WAY = new Set(["sending", "queued", "running"]);

// Is the post on screen: the page, or one of its tiles?
function shown(id) {
  return savePost()?.id === id || !!document.querySelector(`.${TILE_SAVE_CLASS}[data-id="${id}"]`);
}

// Saves a post, when it is still the one clicked for: the page's (what is
// sent is built from the path again, never kept from an earlier page), or
// a tile's, as tilePost read it from the tile's href at the click. One
// whose save is under way is not sent again: one job per post, as many
// posts at once as are clicked (FeedVault queues them).
async function startSave(id, post = savePost()) {
  if (!post || post.id !== id || UNDER_WAY.has(saves.get(id)?.state)) return;
  setSave(id, { state: "sending" });
  const r = await api("POST", "/api/save", post.body);
  if (r === null) { setSave(id, { state: "offline" }); return; }
  const b = r.body || {};
  if (!b.ok) { setSave(id, { state: "failed", message: b.error || `FeedVault answered ${r.status}` }); return; }
  if (b.have) {
    cache.set(id, { saved: true, at: Date.now() });
    saves.delete(id);
    paint();
    return;
  }
  if (!b.job || typeof b.job.id !== "number") { setSave(id, { state: "failed", message: "unexpected answer" }); return; }
  applyJob(id, b.job);
  if (ENDED.has(b.job.state)) return;
  watchJob(b.job.id, () => saves.get(id)?.job?.id === b.job.id, () => shown(id),
    (job) => (job ? applyJob(id, job) : setSave(id, { state: "failed", message: GONE })));
}

// A Save job's state, as GET /api/jobs/<id> says it.
function applyJob(id, job) {
  if (job.state === "done" && job.result?.post) {
    cache.set(id, { saved: true, at: Date.now() });
    saves.delete(id);
    paint();
    return;
  }
  if (ENDED.has(job.state)) {
    setSave(id, { state: "failed", job, message: job.message || job.state, error: job.result?.error || null });
    return;
  }
  setSave(id, { state: job.state === "running" ? "running" : "queued", job });
}

const GONE = "the job is gone (FeedVault restarted?)";

// Poll GET /api/jobs/<id> until the job has ended: onUpdate(job) on each
// answer, onUpdate(null) if it is unknown. Stops once still() is false (a
// retry started another job); slower while shown() is false (its page is
// not the one open), or when FeedVault does not answer or answers an error.
async function watchJob(jobId, still, shown, onUpdate) {
  for (;;) {
    if (!still()) return;
    const r = await api("GET", `/api/jobs/${jobId}`);
    if (!still()) return;
    if (r?.status === 404) { onUpdate(null); return; }
    const job = r?.status === 200 && typeof r.body?.id === "number" ? r.body : null;
    if (job) {
      onUpdate(job);
      if (ENDED.has(job.state)) return;
    }
    await sleep(job && shown() ? JOB_POLL_MS : JOB_POLL_MS * 6);
  }
}

// ---------------------------------------------------------------------------
// Sync profile button: adds the profile's source (after asking, in the
// button itself) unless there is one, then syncs it. Which tool, link and
// folder: FeedVault's answer for the profile (GET /api/sources/resolve,
// its link routing). A source that runs a script or logs in is only synced
// from the dashboard ("dashboard_only"): the button says so and links there.
// One set to Don't sync in FeedVault (its account or person) offers nothing:
// FeedVault refuses to add or sync it, and only its dashboard turns that off.
// ---------------------------------------------------------------------------

// name -> { state: "checking" | "idle" | "confirm" | "sending" | "queued" | "running" | "done"
//                  | "failed" | "offline" | "dashboard" | "nosync", source, resolved, checked, job, message, error }
const profiles = new Map();

function profileName() {
  const m = location.pathname.match(SITE.profileRe);
  if (!m || SITE.notProfiles.has(m[1].toLowerCase()) || !m[1].replace(/\./g, "")) return null;
  return m[1].toLowerCase();
}

function setProfile(name, value) {
  profiles.set(name, { ...profiles.get(name), ...value });
  paint();
}

// Where FeedVault shows the profile: its person, else its account's posts.
function sourceLink(src) {
  if (src?.person) return link(`${API_BASE}/people/${encodeURIComponent(src.person.id)}`, `${src.person.name} in FeedVault`);
  if (src?.account) {
    const q = new URLSearchParams({ platform: src.account.platform, author: src.account.id });
    return link(`${API_BASE}/?${q}`, "Posts in FeedVault");
  }
  return null;
}

// Where its source is shown in the dashboard: its person's page, else Creators.
function dashboardLink(src) {
  const to = src?.person ? `${API_BASE}/people/${encodeURIComponent(src.person.id)}` : `${API_BASE}/creators`;
  return link(to, "Open it in FeedVault");
}

function paintProfile() {
  const name = profileName();
  if (!name) { dropPanel("profile:"); return; }
  if (!profiles.has(name)) {
    profiles.set(name, { state: "checking" });
    checkProfile(name);
  }
  const s = profiles.get(name);
  const p = panel(`profile:${name}`);
  render(p, `${s.state}|${s.source?.id || ""}|${s.job?.id || ""}|${s.message || ""}`, () => {
    const out = [];
    const sync = s.source ? "Sync profile" : "Add & sync profile";
    if (s.state === "checking" || s.state === "sending") out.push(button("busy", "…", null));
    else if (s.state === "confirm") {
      const row = el("div", { class: "fv-row" });
      row.append(button("confirm", `Add @${name}`, () => addAndSync(name)),
                 button("idle", "Cancel", () => setProfile(name, { state: "idle" })));
      const where = s.resolved?.folder ? `downloading into its folder ${s.resolved.folder.split(/[\\/]/).filter(Boolean).pop()}` : "downloading into your first media root";
      out.push(row, note(`New ${s.resolved?.tool || ""} source for @${name}, ${where}.`));
    } else if (s.state === "queued") out.push(button("busy", "Sync queued", null, `Job #${s.job.id}`));
    else if (s.state === "running") out.push(button("busy", "Syncing…", null, `Job #${s.job.id}`));
    else if (s.state === "offline") out.push(button("offline", "FeedVault is not running — retry", () => clickProfile(name)));
    else if (s.state === "dashboard") {
      const n = note(s.message || "This profile's source runs a script or logs in, which only FeedVault's own dashboard can start.");
      n.append(el("br"), dashboardLink(s.source));
      out.push(button("dashboard", "Sync from FeedVault", null), n);
      return out;
    } else if (s.state === "nosync") {
      const n = note("Set to Don't sync in FeedVault: it downloads nothing from this profile. Its dashboard can turn that off.");
      if (s.source) n.append(el("br"), dashboardLink(s.source));
      out.push(button("nosync", "Not synced in FeedVault", null), n);
      return out;
    } else if (s.state === "failed") {
      out.push(button("failed", `${sync} — retry`, () => clickProfile(name), s.message || ""),
               note(s.message || "Sync failed", SETTINGS_ERRORS.has(s.error)));
    } else {
      out.push(button(s.state === "done" ? "done" : "idle", sync, () => clickProfile(name)));
      if (s.state === "done") out.push(note(s.message || "Synced"));
    }
    const to = sourceLink(s.source);
    if (to) {
      const n = el("div", { class: "fv-note" });
      n.append(to);
      out.push(n);
    }
    return out;
  });
}

// Is there a source for it already? (Asked once per profile visited.)
async function checkProfile(name) {
  const r = await api("GET", `/api/sources/resolve?${new URLSearchParams(SITE.resolveQuery(name))}`);
  if (r === null) { setProfile(name, { state: "offline" }); return; }
  if (!r.body?.ok) { setProfile(name, { state: "failed", message: r.body?.error || `FeedVault answered ${r.status}` }); return; }
  const resolved = { tool: r.body.tool, folder: r.body.folder };
  let src = null;
  if (typeof r.body.source === "number") {
    const got = await api("GET", `/api/sources/${encodeURIComponent(r.body.source)}`);
    if (got === null) { setProfile(name, { state: "offline" }); return; }
    if (got.status !== 200 || typeof got.body?.id !== "number") {
      setProfile(name, { state: "failed", message: got.body?.error || `FeedVault answered ${got.status}` });
      return;
    }
    src = got.body;
  }
  const off = r.body.no_sync === true || src?.no_sync === true;
  setProfile(name, { state: off ? "nosync" : src?.dashboard_only ? "dashboard" : "idle", source: src, resolved, checked: true });
  if (src?.job && !src.dashboard_only && !off) followSync(name, src.job.id);   // already syncing: show it
}

function clickProfile(name) {
  const s = profiles.get(name);
  if (s?.state === "nosync") return;           // FeedVault refuses it: nothing to send
  if (s?.source) { syncSource(name, s.source.id); return; }
  if (!s?.checked) { profiles.delete(name); paint(); return; }   // offline, or the check failed: ask again
  setProfile(name, { state: "confirm" });
}

async function addAndSync(name) {
  setProfile(name, { state: "sending", message: null });
  const r = await api("POST", "/api/sources", SITE.addBody(name));
  if (r === null) { setProfile(name, { state: "offline" }); return; }
  const b = r.body || {};
  if (r.status === 409 && b.no_sync === true) { setProfile(name, { state: "nosync" }); return; }
  if (!b.ok) { setProfile(name, { state: "failed", message: b.error || `FeedVault answered ${r.status}` }); return; }
  setProfile(name, { source: b.source });
  syncSource(name, b.source.id);
}

async function syncSource(name, sid) {
  setProfile(name, { state: "sending", message: null, error: null });
  const r = await api("POST", `/api/sources/${encodeURIComponent(sid)}/sync`);
  if (r === null) { setProfile(name, { state: "offline" }); return; }
  const b = r.body || {};
  if (r.status === 409 && b.no_sync === true) { setProfile(name, { state: "nosync" }); return; }
  if (r.status === 409) {                      // already queued or running: follow that one
    const got = await api("GET", `/api/sources/${encodeURIComponent(sid)}`);
    if (got?.body?.job) { followSync(name, got.body.job.id); return; }
  }
  // Refused to this page (a source that runs a script or logs in): the dashboard can.
  if (r.status === 403) { setProfile(name, { state: "dashboard", message: b.error || null }); return; }
  if (!b.ok) { setProfile(name, { state: "failed", message: b.error || `FeedVault answered ${r.status}` }); return; }
  followSync(name, b.job.id);
}

function followSync(name, jobId) {
  setProfile(name, { state: "queued", job: { id: jobId } });
  watchJob(jobId, () => profiles.get(name)?.job?.id === jobId, () => profileName() === name, async (job) => {
    if (!job) { setProfile(name, { state: "failed", message: GONE }); return; }
    if (!ENDED.has(job.state)) { setProfile(name, { state: job.state === "running" ? "running" : "queued", job }); return; }
    // The source after it: its account and person may be known only now.
    const sid = profiles.get(name)?.source?.id;
    const got = sid === undefined ? null : await api("GET", `/api/sources/${encodeURIComponent(sid)}`);
    const source = got?.status === 200 ? got.body : profiles.get(name)?.source;
    if (job.state === "done") setProfile(name, { state: "done", job, source, message: `Synced: ${job.message}` });
    else setProfile(name, { state: "failed", job, source, message: job.message || job.state, error: job.result?.error || null });
  });
}

// ---------------------------------------------------------------------------
// Save links to FeedVault: on a person's link-in-bio page, its address and
// the links it shows, as the browser rendered them (nothing is fetched, here
// or by FeedVault), saved to a person's Links or to no one's (Unsorted) by
// POST /api/links/import. Only new links are added: FeedVault never changes
// or removes one saved before. Links are bookmarks: nothing is downloaded.
// ---------------------------------------------------------------------------

// page -> { state: "idle" | "loading" | "pick" | "sending" | "done" | "failed" | "offline",
//           people, recent, person, count, result, message, settings }
const bios = new Map();

// The person's page open, as sent (no query, no fragment), else null.
function bioPage() {
  if (!BIO_HOST) return null;
  const m = location.pathname.match(/^\/@?([A-Za-z0-9._-]{1,64})\/?$/);
  if (!m || BIO_NOT_PAGES.has(m[1].toLowerCase()) || !m[1].replace(/\./g, "")) return null;
  return `https://${BIO_HOST}${location.pathname}`;
}

// A host that is the bio site's own (its nav, legal and login pages).
function bioOwn(host) {
  return [BIO_HOST, ...(BIO_SITES[BIO_HOST].also || [])].some((s) => host === s || host.endsWith(`.${s}`));
}

// The text a link shows, else its aria-label or title: plain, bounded.
function linkLabel(a) {
  const text = (a.innerText ?? a.textContent ?? "").replace(/\s+/g, " ").trim();
  return (text || a.getAttribute("aria-label") || a.getAttribute("title") || "").replace(/\s+/g, " ").trim().slice(0, 300);
}

// The page's outbound links, read from the rendered anchors now: http(s)
// only, no login part, none of the site's own, each once (its first label
// that is not empty), at most BIO_MAX_LINKS. FeedVault filters them again.
function bioLinks() {
  const out = new Map();
  for (const a of document.querySelectorAll("a")) {
    if (a.closest(".fv-panel, .fv-toast")) continue;
    let u;
    try { u = new URL(a.getAttribute("href") || "", location.href); } catch { continue; }
    if ((u.protocol !== "https:" && u.protocol !== "http:") || u.username || u.password || bioOwn(u.hostname)) continue;
    if (u.href.length > 2048) continue;
    if (!out.has(u.href) && out.size >= BIO_MAX_LINKS) break;
    if (!out.get(u.href)) out.set(u.href, linkLabel(a));
  }
  return [...out].map(([url, title]) => ({ url, title }));
}

function setBio(page, value) {
  bios.set(page, { ...bios.get(page), ...value });
  paint();
}

// The picker's choices: no one, the recent people, then everyone whose name
// has ``query`` in it. The person chosen stays in it whatever the query, so
// what it shows is what is sent.
function fillPeople(select, s, query) {
  const q = query.trim().toLowerCase();
  const byId = new Map(s.people.map((p) => [String(p.id), p]));
  const keep = (p) => String(p.id) === s.person || !q || p.name.toLowerCase().includes(q);
  const option = (p) => {
    const o = el("option", { value: String(p.id) }, p.name);
    if (String(p.id) === s.person) o.setAttribute("selected", "");
    return o;
  };
  const none = el("option", { value: "none" }, "No person (Unsorted)");
  if (s.person === "none") none.setAttribute("selected", "");
  const recent = s.recent.map((id) => byId.get(String(id))).filter((p) => p && keep(p));
  const rest = s.people.filter((p) => keep(p) && !s.recent.includes(p.id)).slice(0, 300);
  const out = [none];
  if (recent.length) {
    const g = el("optgroup", { label: "Recent" });
    g.append(...recent.map(option));
    out.push(g);
  }
  if (rest.length) {
    const g = el("optgroup", { label: recent.length ? "Everyone" : "People" });
    g.append(...rest.map(option));
    out.push(g);
  }
  select.replaceChildren(...out);
  select.value = s.person;
}

function bioPicker(page, s) {
  const find = el("input", { class: "fv-field", type: "search", placeholder: "Find a person", "aria-label": "Find a person" });
  const select = el("select", { class: "fv-field", "aria-label": "Save to" });
  fillPeople(select, s, "");
  // Only the user's own choice counts: a page script can set the select's
  // value or send it a change, which changes nothing here.
  select.addEventListener("change", (e) => {
    if (!e.isTrusted) { select.value = bios.get(page).person; return; }
    bios.get(page).person = select.value;
  });
  find.addEventListener("input", () => fillPeople(select, bios.get(page), find.value || ""));
  const row = el("div", { class: "fv-row" });
  row.append(button("confirm", "Save links", () => sendBio(page)),
             button("idle", "Cancel", () => setBio(page, { state: "idle" })));
  return [find, select, row,
          note(`This page and the ${s.count} link${s.count === 1 ? "" : "s"} it shows, to the person's Links; links saved already stay as they are.`)];
}

function bioDone(s) {
  const r = s.result;
  const parts = [`${r.saved} saved`, `${r.already + r.elsewhere} already there`];
  if (r.over) parts.push(`${r.over} over the limit`);
  if (r.dropped) parts.push(`${r.dropped} refused`);
  const n = note(parts.join(", "));
  const who = s.people?.find((p) => String(p.id) === String(r.person));
  n.append(el("br"), who ? link(`${API_BASE}/people/${encodeURIComponent(who.id)}`, `${who.name} in FeedVault`)
                         : link(`${API_BASE}/links`, "Links in FeedVault"));
  return n;
}

function paintBio() {
  const page = bioPage();
  if (!page) { dropPanel("bio:"); return; }
  if (!bios.has(page)) bios.set(page, { state: "idle" });
  const s = bios.get(page);
  const p = panel(`bio:${page}`);
  render(p, `${s.state}|${s.message || ""}|${JSON.stringify(s.result || null)}`, () => {
    if (s.state === "loading" || s.state === "sending") return [button("busy", "…", null)];
    if (s.state === "pick") return bioPicker(page, s);
    if (s.state === "offline") return [button("offline", "FeedVault is not running — retry", () => openBio(page))];
    if (s.state === "failed") {
      return [button("failed", "Save links to FeedVault — retry", () => openBio(page)),
              note(s.message || "Failed", s.settings ? "Open Settings → Downloads" : false)];
    }
    const out = [button(s.state === "done" ? "done" : "idle", "Save links to FeedVault", () => openBio(page))];
    if (s.state === "done") out.push(bioDone(s));
    return out;
  });
}

// The people to choose from (ids and names), then the picker.
async function openBio(page) {
  setBio(page, { state: "loading", message: null });
  const r = await api("GET", "/api/links/people");
  if (r === null) { setBio(page, { state: "offline" }); return; }
  if (r.status !== 200 || !Array.isArray(r.body?.people) || !Array.isArray(r.body?.recent)) {
    setBio(page, { state: "failed", message: r.body?.error || `FeedVault answered ${r.status}` });
    return;
  }
  const people = r.body.people.filter((p) => typeof p?.id === "number" && typeof p?.name === "string");
  const was = bios.get(page).person;
  const person = people.some((p) => String(p.id) === was) ? was : "none";
  setBio(page, { state: "pick", people, recent: r.body.recent, person, count: bioLinks().length });
}

// The links are read again now, from the page as it is.
async function sendBio(page) {
  const s = bios.get(page);
  if (s?.state !== "pick" || bioPage() !== page) return;
  const person = s.person === "none" ? null : Number(s.person);
  const body = { page_url: page, links: bioLinks(), person };
  setBio(page, { state: "sending" });
  const r = await api("POST", "/api/links/import", body);
  if (r === null) { setBio(page, { state: "offline" }); return; }
  const b = r.body || {};
  if (!b.ok || !b.links) {
    setBio(page, { state: "failed", message: b.error || `FeedVault answered ${r.status}`, settings: r.status === 403 });
    return;
  }
  setBio(page, { state: "done", result: { ...b.links, person } });
}

// ---------------------------------------------------------------------------
// Add this page to FeedVault Links (the userscript manager's menu, on every
// page the script runs on): its address and title, to no one (Unsorted).
// Only the user can pick a menu command: no page script reaches it.
// ---------------------------------------------------------------------------

function toast(text, to = null) {
  document.querySelector(".fv-toast")?.remove();
  const t = el("div", { class: "fv-toast" });
  const n = note(text);
  if (to) n.append(el("br"), to);
  t.append(n);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 6000);
}

async function addPage() {
  const title = (document.title || "").replace(/\s+/g, " ").trim().slice(0, 300);
  toast("Saving to FeedVault Links…");
  const r = await api("POST", "/api/links", { url: location.href, title });
  if (r === null) { toast("FeedVault is not running"); return; }
  const b = r.body || {};
  const to = link(`${API_BASE}/links`, "Links in FeedVault");
  if (r.status === 409) {
    const who = b.link?.person?.name;
    toast(who ? `Already in FeedVault Links, with ${who}` : "Already in FeedVault Links", to);
  } else if (!b.ok) toast(b.error || `FeedVault answered ${r.status}`);
  else toast("Added to FeedVault Links (Unsorted)", to);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// A page that does not look as expected leaves nothing behind: no error in
// the page's console, the next change paints again.
function paint() {
  for (const part of SITE ? [paintTiles, paintPage, paintProfile] : [paintBio]) {
    try {
      part();
    } catch {
      /* the page changed under us: the other parts still paint */
    }
  }
  if (pending.size) schedule();
}

if (SITE || BIO_HOST) {
  GM_registerMenuCommand("Add this page to FeedVault Links", addPage);
  addEventListener("resize", () => {
    const p = document.querySelector(".fv-panel");
    if (p) place(p);
  });
  let raf = null;
  new MutationObserver(() => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = null; paint(); });
  }).observe(document.body, { childList: true, subtree: true });

  // These sites are single-page apps; misses expire, so look again now and then.
  setInterval(paint, MISS_TTL_MS);
  paint();
}
