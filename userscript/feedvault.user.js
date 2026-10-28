// ==UserScript==
// @name         FeedVault
// @namespace    https://github.com/Cotions/feedvault
// @version      0.7.2
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
// which links are posts (tiles), which page or tile link is a post (Save
// post, a tile's Save) or a profile (Add profile, Sync now), and what is
// sent for them. When a site changes its layout or its paths, a selector or
// pattern stops matching and that part does nothing: no menu item, no
// badge, no error.
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
    // A post page or dialog Save post is offered on (the grid's dialog
    // sets the same path), and a tile's link its Save is for: the whole
    // path, and a shortcode as POST /api/save accepts it.
    postRe: /^\/(?:[A-Za-z0-9._]{1,30}\/)?(?:p|reel|reels|tv)\/([A-Za-z0-9_-]{5,40})\/?$/,
    saveBody: (m) => ({ platform: "instagram", shortcode: m[1] }),
    // A profile page (or one of its tabs) Add profile and Sync now are offered on.
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
  @keyframes fv-spin { to { transform: rotate(360deg); } }
  @keyframes fv-pulse { 50% { opacity: 0.35; } }
  /* The bubble: where place() puts it (right/bottom, or left/top while it
     is dragged), as big as its round button; the menu hangs off it. */
  .fv-bubble {
    position: fixed; right: 18px; bottom: 18px; z-index: 2147483000;
    width: 36px; height: 36px;
  }
  .fv-bubble-btn {
    all: unset; box-sizing: border-box; position: relative;
    display: flex; align-items: center; justify-content: center;
    width: 36px; height: 36px; border-radius: 50%;
    cursor: pointer; user-select: none; touch-action: none;
    color: #e8ecf6; background: rgba(10, 12, 17, 0.9);
    box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.35), 0 4px 14px rgba(0, 0, 0, 0.35);
    font: 800 12px/1 ui-monospace, "JetBrains Mono", monospace; letter-spacing: -0.5px;
    opacity: 0.6; transition: opacity 0.15s;
  }
  .fv-bubble:hover > .fv-bubble-btn, .fv-bubble[data-open] > .fv-bubble-btn,
  .fv-bubble-btn:focus-visible, .fv-bubble-btn[data-drag] { opacity: 1; }
  .fv-bubble-btn:focus-visible { box-shadow: 0 0 0 2px rgba(232, 236, 246, 0.9); }
  .fv-bubble-btn[data-drag] { cursor: grabbing; }
  /* Its status: a dot (green: in FeedVault, red: failed or not running),
     a turning ring while a save or sync of the page runs. */
  .fv-bubble-btn::after {
    content: ""; display: none; position: absolute; top: -1px; right: -1px;
    width: 10px; height: 10px; border-radius: 50%; box-shadow: 0 0 0 2px #0a0c11;
  }
  .fv-bubble-btn[data-status="ok"]::after { display: block; background: #4ade80; }
  .fv-bubble-btn[data-status="failed"]::after { display: block; background: #f87171; }
  .fv-bubble-btn[data-status="busy"]::after {
    display: block; background: #60a5fa; animation: fv-pulse 1s ease-in-out infinite;
  }
  .fv-bubble-btn[data-status="busy"]::before {
    content: ""; position: absolute; inset: -3px; border-radius: 50%;
    border: 2px solid transparent; border-top-color: #60a5fa; animation: fv-spin 0.9s linear infinite;
  }
  .fv-menu {
    position: absolute; display: none; flex-direction: column; gap: 1px;
    box-sizing: border-box; width: 260px; max-width: calc(100vw - 24px); overflow-y: auto;
    padding: 6px; border-radius: 12px; text-align: left;
    color: #e8ecf6; background: rgba(10, 12, 17, 0.96);
    box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.25), 0 10px 30px rgba(0, 0, 0, 0.45);
    font: 600 12px/16px ui-monospace, "JetBrains Mono", monospace;
  }
  .fv-bubble[data-open] > .fv-menu { display: flex; }
  .fv-bubble[data-y="up"] > .fv-menu { bottom: calc(100% + 8px); }
  .fv-bubble[data-y="down"] > .fv-menu { top: calc(100% + 8px); }
  .fv-bubble[data-x="left"] > .fv-menu { right: 0; }
  .fv-bubble[data-x="right"] > .fv-menu { left: 0; }
  .fv-head { padding: 4px 8px 2px; color: rgba(232, 236, 246, 0.6); font-weight: 700; overflow-wrap: anywhere; }
  .fv-sep { height: 1px; margin: 4px 2px; background: rgba(232, 236, 246, 0.15); }
  .fv-item {
    all: unset; box-sizing: border-box; display: flex; flex-direction: column; gap: 1px;
    width: 100%; padding: 6px 8px; border-radius: 8px; cursor: pointer;
    color: #e8ecf6; font: inherit; text-decoration: none; overflow-wrap: anywhere;
  }
  .fv-item:hover, .fv-item:focus-visible { background: rgba(232, 236, 246, 0.1); }
  .fv-item[aria-disabled="true"] { cursor: default; color: rgba(232, 236, 246, 0.55); background: none; }
  .fv-item[data-state="done"] { box-shadow: inset 2px 0 0 #4ade80; }
  .fv-item[data-state="busy"] { box-shadow: inset 2px 0 0 #60a5fa; }
  .fv-item[data-state="failed"], .fv-item[data-state="offline"] { box-shadow: inset 2px 0 0 #f87171; }
  .fv-item[data-state="confirm"], .fv-item[data-state="dashboard"] { box-shadow: inset 2px 0 0 #facc15; }
  .fv-why { font-weight: 400; font-size: 11px; color: rgba(232, 236, 246, 0.6); }
  .fv-note {
    padding: 2px 8px 6px; font-weight: 400; font-size: 11px; color: rgba(232, 236, 246, 0.75);
    overflow-wrap: anywhere;
  }
  .fv-note a { color: #93c5fd; }
  .fv-row { display: flex; gap: 4px; padding: 2px 0; }
  .fv-row > .fv-item { flex: 1; align-items: center; background: rgba(232, 236, 246, 0.08); }
  /* The link-in-bio person picker, in the menu. */
  .fv-field {
    all: unset; box-sizing: border-box; width: 100%; margin: 2px 0; padding: 4px 8px; border-radius: 8px;
    color: #e8ecf6; background: rgba(232, 236, 246, 0.06); box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.3);
    font: inherit; font-weight: 400;
  }
  select.fv-field { cursor: pointer; }
  .fv-field:focus { box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.8); }
  .fv-field option, .fv-field optgroup { color: #e8ecf6; background: #0a0c11; }
  /* What Add this page did, with the menu closed: across the window from
     the bubble (toast()), never over it. */
  .fv-toast {
    position: fixed; z-index: 2147483000; max-width: 320px;
    padding: 6px 10px; border-radius: 10px; color: #e8ecf6; background: rgba(10, 12, 17, 0.92);
    box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.25);
    font: 400 12px/16px ui-monospace, "JetBrains Mono", monospace; overflow-wrap: anywhere;
  }
  .fv-toast a { color: #93c5fd; }
  /* A tile's Save: a small round icon, bottom right, clear of the "saved"
     badge's corner, of Instagram's carousel and reel icons (top right) and
     of TikTok's view count (bottom left). On X, top right: X puts its own
     button on media bottom right (Grok's edit image), its ALT and duration
     labels bottom left. Shown while the tile is hovered or focused; always
     once a save is under way or failed. Its words are its label and
     tooltip. */
  .fv-tile-save {
    all: unset; box-sizing: border-box;
    position: absolute; right: 6px; bottom: 6px; z-index: 6;
    display: flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 50%; cursor: pointer;
    font: 700 13px/1 ui-monospace, "JetBrains Mono", monospace;
    color: #e8ecf6; background: rgba(10, 12, 17, 0.78);
    box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.4);
    opacity: 0; pointer-events: none; transition: opacity 0.12s;
  }
  /* Its own tile's: the link it sits in (a:hover, whatever of the tile is
     under the pointer), never a neighbour's, nor a tile the site gave focus
     back to (Instagram does, closing a post's dialog): only the keyboard's
     focus shows it. */
  a:hover .fv-tile-save, a:focus-visible .fv-tile-save, .fv-tile-save:focus-visible,
  .fv-tile-save:not([data-state="idle"]) {
    opacity: 1; pointer-events: auto;
  }
  .fv-tile-save[data-fv-site="x"] { top: 6px; bottom: auto; }
  .fv-tile-save:hover, .fv-tile-save:focus-visible {
    background: rgba(10, 12, 17, 0.95); box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.9);
  }
  .fv-tile-save[data-state="busy"] { cursor: default; }
  .fv-tile-save[data-state="busy"]::before {
    content: ""; width: 10px; height: 10px; box-sizing: border-box; border-radius: 50%;
    border: 2px solid rgba(232, 236, 246, 0.3); border-top-color: #e8ecf6; animation: fv-spin 0.8s linear infinite;
  }
  .fv-tile-save[data-state="failed"], .fv-tile-save[data-state="offline"] {
    color: #fca5a5; box-shadow: 0 0 0 2px rgba(248, 113, 113, 0.9);
  }
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

// The box in a tile that holds what is laid over it: the nearest one round
// its image that is laid out as a box (not inline: Instagram's grid links
// are, and a box laid out from an inline link that wraps blocks has no
// width, so what sits at its right sat on the tile to the left) and
// already positioned; else the nearest box, made to hold it. The link
// itself only when it is that box.
function frameOf(a, img) {
  let box = null;
  for (let n = img.parentNode; n; n = n === a ? null : n.parentNode) {
    const s = getComputedStyle(n);
    if (s.display === "inline" || s.display === "contents") continue;
    if (s.position !== "static") return n;
    box ||= n;
  }
  if (box) box.style.position = "relative";
  return box;
}

// Every anchor that points at a post, as the site's entry picks them: a
// saved one gets the badge, one known not saved its own Save, both in the
// tile's own frame.
function paintTiles() {
  for (const a of document.querySelectorAll(SITE.tiles)) {
    if (a.closest(".fv-bubble, .fv-toast")) continue;   // our own links to FeedVault
    const id = postOf(a.getAttribute("href"));
    if (!id) continue;
    const state = known(id);
    if (state === null) { pending.add(id); continue; }
    const has = a.querySelector(`.${BADGE_CLASS}`);
    const ctl = a.querySelector(`.${TILE_SAVE_CLASS}`);
    // Only tiles with an image get a badge or a Save; plain text links
    // (timestamps, "view all comments") stay untouched.
    // A tile's hover preview (a reel's muted <video>) keeps both: the badge
    // takes no pointer, the Save is a small button in its corner with only
    // its own listeners. A link round a real player, one with controls,
    // gets neither: its corners are the player's own buttons (mute, full
    // screen), and nothing of ours goes over them.
    const img = a.querySelector("img");
    const player = [...a.querySelectorAll("video")].some((v) => v.hasAttribute("controls"));
    if (!img || player) { has?.remove(); ctl?.remove(); continue; }
    if (state) {
      ctl?.remove();
      const frame = has ? null : frameOf(a, img);
      if (frame) {
        frame.appendChild(badge("saved"));
        a.setAttribute(MARKED_ATTR, "1");
      }
      continue;
    }
    has?.remove();
    const post = tilePost(a.getAttribute("href"));
    if (!post) { ctl?.remove(); continue; }
    const frame = ctl ? null : frameOf(a, img);
    if (ctl || frame) paintTileSave(ctl || addTileSave(frame), post.id);
  }
}

// A tile's Save, in its frame. The tile is a link (and the sites route on
// their own click and press handlers): no click on it opens the post, a
// real one or not, and only a real one saves. Nothing else is stopped:
// the listeners are the button's own.
function addTileSave(frame) {
  const b = el("button", { class: TILE_SAVE_CLASS, type: "button", "data-fv-site": SITE_KEY });
  b.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!e.isTrusted) return;
    // What is sent is the tile this button is in, read at the click: its
    // link's href now (the sites reuse a tile for another post as they
    // scroll), and only for the post the button showed.
    const a = b.closest(SITE.tiles);
    const post = a ? tilePost(a.getAttribute("href")) : null;
    if (post && post.id === b.dataset.id) startSave(post.id, post);
  });
  for (const type of ["pointerdown", "pointerup", "mousedown", "mouseup"]) {
    b.addEventListener(type, (e) => e.stopPropagation());
  }
  frame.appendChild(b);
  return b;
}

// The same states as the bubble's Save post, as an icon: [its look, its
// glyph (a busy one turns, in CSS), its words (aria-label), its tooltip:
// why it failed, or its job].
function tileLook(s) {
  switch (s?.state) {
    case "sending": return ["busy", "", "Sending…", "Saving to FeedVault"];
    case "queued": return ["busy", "", "Queued", `Queued: ${jobTitle(s)}`];
    case "running": return ["busy", "", "Saving…", `Saving: ${jobTitle(s)}`];
    case "failed": {
      const where = SETTINGS_ERRORS.has(s.error) ? " (FeedVault: Settings → Downloaders)" : "";
      return ["failed", "!", "Failed — retry", `${s.message || "Save failed"}${where}`];
    }
    case "offline": return ["offline", "!", "Offline — retry", "FeedVault is not running — click to retry"];
    default: return ["idle", "↓", "Save to FeedVault", "Save to FeedVault"];
  }
}

function paintTileSave(b, id) {
  const [look, glyph, words, title] = tileLook(saves.get(id));
  const sig = `${id}|${words}|${title}`;
  if (b.dataset.sig === sig) return;
  b.dataset.sig = sig;
  b.dataset.id = id;
  b.dataset.state = look;
  b.textContent = glyph;
  b.setAttribute("aria-label", words);
  b.setAttribute("title", title);
  if (look === "busy") b.setAttribute("aria-disabled", "true");
  else b.removeAttribute("aria-disabled");
}

// ---------------------------------------------------------------------------
// Saving a post: Save post in the bubble's menu (a post page, or the dialog
// a grid opens: it sets the same path), or a tile's Save. Both share one
// state per post: the same post shows the same.
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
// The bubble: one small round button on every page the script runs on, and
// the menu it opens, with what can be done on the page open (its parts,
// below). It stays where the user drags it, per site (hostname), in the
// script's own storage (never the page's); a dot on it says how the page's
// post or profile stands without opening it.
// ---------------------------------------------------------------------------

// Its default spot, per site: its right and bottom offsets. X keeps its own
// floating buttons (Grok, the messages drawer) in the bottom-right corner:
// the bubble starts left of them.
const PANEL_SPOTS = { x: { right: 90, bottom: 18 } };
const PANEL_SPOT = { right: 18, bottom: 18 };
const SPOT_KEY = `bubble:${location.hostname}`;

// The bubble (ROOT: its button and its menu), made once for the page's life.
let ROOT = null, BTN = null, MENU = null;
let menuSig = null;                            // what the menu shows now: rebuilt only when it changes
let dragging = false;                          // moved by the pointer now: place() leaves it there

function savedSpot() {
  const s = GM_getValue(SPOT_KEY, null);
  return s && Number.isFinite(s.right) && Number.isFinite(s.bottom) ? s : null;
}

const clamp = (v, max) => Math.max(0, Math.min(v, Math.max(0, max)));

// The window's size without its scrollbars: what a fixed element's right
// and bottom are measured from (innerWidth counts the scrollbar in).
function view() {
  const d = document.documentElement;
  return [d?.clientWidth || innerWidth, d?.clientHeight || innerHeight];
}

// The bubble at its spot, inside the window, and its menu opening toward
// the side with room: up and left from a bubble in the bottom right, down
// and right from one in the top left. Written only when it changes.
function place(root) {
  if (!root || dragging) return;
  const s = savedSpot() || PANEL_SPOTS[SITE_KEY] || PANEL_SPOT;
  const { width, height } = root.getBoundingClientRect();
  const [vw, vh] = view();
  const right = clamp(s.right, vw - width), bottom = clamp(s.bottom, vh - height);
  const st = root.style;
  if (st.left || st.top || st.right !== `${right}px` || st.bottom !== `${bottom}px`) {
    Object.assign(st, { left: "", top: "", right: `${right}px`, bottom: `${bottom}px` });
  }
  const left = vw - right - width, top = vh - bottom - height;
  const x = left + width / 2 > vw / 2 ? "left" : "right";
  const y = top + height / 2 > vh / 2 ? "up" : "down";
  if (root.dataset.x !== x) root.dataset.x = x;
  if (root.dataset.y !== y) root.dataset.y = y;
  const room = `${Math.max(120, Math.floor((y === "up" ? top : vh - top - height) - 16))}px`;
  if (MENU && MENU.style.maxHeight !== room) MENU.style.maxHeight = room;
}

function resetSpot() {
  GM_setValue(SPOT_KEY, null);
  place(ROOT);
}

const isOpen = () => !!ROOT && ROOT.hasAttribute("data-open");

function setOpen(on) {
  if (!ROOT) return;
  if (on) ROOT.setAttribute("data-open", "");
  else {
    ROOT.removeAttribute("data-open");
    if (ROOT.contains(document.activeElement)) BTN.focus();
  }
  BTN.setAttribute("aria-expanded", on ? "true" : "false");
  place(ROOT);
}

// The bubble, made on the first paint and kept: SPA navigation changes
// what its menu shows, never adds another one.
function bubble() {
  if (ROOT) {
    if (!ROOT.isConnected) document.body.appendChild(ROOT);   // the site redrew the body
    return ROOT;
  }
  ROOT = el("div", { class: "fv-bubble" });
  BTN = el("button", { class: "fv-bubble-btn", type: "button", "aria-expanded": "false" }, "FV");
  MENU = el("div", { class: "fv-menu", role: "group", "aria-label": "FeedVault" });
  ROOT.append(BTN, MENU);
  // What is done in the bubble stays in it: no click there reaches a
  // site's "click outside" handler (Instagram's post dialog), no key typed
  // there one of its shortcuts. (Ours, on the document, see them first.)
  for (const type of ["click", "dblclick", "pointerdown", "pointerup", "mousedown", "mouseup",
                      "keydown", "keyup", "keypress"]) {
    ROOT.addEventListener(type, (e) => e.stopPropagation());
  }
  movable(BTN, ROOT);
  document.body.appendChild(ROOT);
  place(ROOT);
  return ROOT;
}

// The bubble's button: a click opens or closes the menu; drag it to move
// the bubble, double-click it to put the bubble back in its default spot.
// Only the user's own pointer acts: a page script's events do nothing.
function movable(btn, root) {
  let drag = null;
  let dropped = false;                         // a drag just ended: its click opens nothing
  btn.addEventListener("pointerdown", (e) => {
    if (!e.isTrusted || e.button !== 0) return;
    e.preventDefault();
    const r = root.getBoundingClientRect();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, dx: e.clientX - r.left, dy: e.clientY - r.top, moved: false };
    dropped = false;
    btn.setPointerCapture(e.pointerId);
  });
  btn.addEventListener("pointermove", (e) => {
    if (!drag || !e.isTrusted || e.pointerId !== drag.id) return;
    // A few pixels' wobble is a click (a double-click's), not a move.
    if (!drag.moved && Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 4) return;
    const r = root.getBoundingClientRect();
    const [vw, vh] = view();
    drag.moved = dragging = true;
    btn.dataset.drag = "1";
    Object.assign(root.style, { right: "auto", bottom: "auto",
      left: `${clamp(e.clientX - drag.dx, vw - r.width)}px`,
      top: `${clamp(e.clientY - drag.dy, vh - r.height)}px` });
  });
  const drop = (e) => {
    if (!drag || !e.isTrusted || e.pointerId !== drag.id) return;
    const { moved } = drag;
    drag = null;
    dragging = false;
    btn.removeAttribute("data-drag");
    if (moved) {
      const r = root.getBoundingClientRect();
      const [vw, vh] = view();
      GM_setValue(SPOT_KEY, { right: Math.round(vw - r.right), bottom: Math.round(vh - r.bottom) });
      dropped = true;
    }
    place(root);
  };
  btn.addEventListener("pointerup", drop);
  btn.addEventListener("pointercancel", drop);
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    if (!e.isTrusted) return;
    if (dropped) { dropped = false; return; }
    setOpen(!isOpen());
  });
  btn.addEventListener("dblclick", (e) => {
    e.preventDefault();
    if (!e.isTrusted) return;
    resetSpot();
  });
}

// Escape, or a click anywhere else, closes the menu. Nothing else does: an
// action under way shows its progress in it until the user closes it.
function onKey(e) {
  if (e.key !== "Escape" || !e.isTrusted || !isOpen()) return;
  e.stopPropagation();
  setOpen(false);
}

function onOutside(e) {
  if (!e.isTrusted || !isOpen() || ROOT.contains(e.target)) return;
  setOpen(false);
}

function head(text) {
  return el("div", { class: "fv-head" }, text);
}

// ``settings``: true for a link to Settings → Downloaders, or the link's own text.
function note(text, settings) {
  const n = el("div", { class: "fv-note" }, text);
  if (settings) n.append(el("br"), link(`${API_BASE}/settings`, settings === true ? "Open Settings → Downloaders" : settings));
  return n;
}

// A menu item. Only a real click acts: the page's own scripts can call
// element.click() or dispatch events on it, which would queue saves or add
// sources. Without onClick it is disabled, ``why`` under its words.
function item(text, onClick, { state = "idle", title = "", why = "" } = {}) {
  const b = el("button", { class: "fv-item", type: "button", "data-state": state, ...(title ? { title } : {}) }, text);
  if (why) b.append(el("span", { class: "fv-why" }, why));
  if (!onClick) b.setAttribute("aria-disabled", "true");
  b.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.isTrusted && onClick) onClick();
  });
  return b;
}

// A menu item that opens FeedVault in a new tab, on a real click only.
function linkItem(href, text, { state = "idle", title = "" } = {}) {
  const a = link(href, text, "fv-item");
  a.dataset.state = state;
  if (title) a.setAttribute("title", title);
  a.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!e.isTrusted) { e.preventDefault(); return; }
    setOpen(false);
  });
  return a;
}

// [level, words]: the first part's status that is failed, else busy, else
// ok (in FeedVault), else none. FeedVault not answering is a failure.
function status(parts) {
  const all = parts.map((p) => p.status).filter(Boolean);
  if (offline) all.push(["failed", "FeedVault is not running"]);
  for (const level of ["failed", "busy", "ok"]) {
    const hit = all.find((s) => s[0] === level);
    if (hit) return hit;
  }
  return ["", ""];
}

function paintBubble() {
  const parts = (SITE ? [postPart(), profilePart()] : [bioPart()]).filter(Boolean);
  parts.push(pagePart());
  const root = bubble();
  const [level, words] = status(parts);
  const title = `FeedVault${words ? `: ${words}` : ""}\nClick for actions, drag to move, double-click to put back`;
  if (BTN.getAttribute("title") !== title) {
    BTN.setAttribute("title", title);
    BTN.setAttribute("aria-label", `FeedVault${words ? `: ${words}` : ""}`);
  }
  if ((BTN.dataset.status ?? "") !== level) BTN.dataset.status = level;
  const sig = parts.map((p) => p.sig).join("\n");
  if (menuSig !== sig) {                       // unchanged: keep the DOM (and focus) as it is
    menuSig = sig;
    MENU.replaceChildren(...parts.flatMap((p, i) => [...(i ? [el("div", { class: "fv-sep", role: "separator" })] : []),
                                                     ...p.build()]));
  }
  place(root);                                 // inside the window, as it is now
}

// ---------------------------------------------------------------------------
// The post open (a post page, or the dialog a grid opens): Save post, and
// View in FeedVault once FeedVault has it (on any page of the post).
// ---------------------------------------------------------------------------

function postPart() {
  const id = postOf(location.pathname);
  const post = savePost();
  const fresh = id ? known(id) : null;
  if (id && fresh === null) pending.add(id);
  // While a stale "not saved" is asked again, keep showing it (no flicker).
  const have = fresh ?? (id ? cache.get(id)?.saved ?? null : null);
  if (!id || (have !== true && id !== post?.id)) return null;
  const s = saves.get(id);
  const st = have ? "have" : s?.state || (have === null ? (offline ? "offline" : "checking") : "idle");
  const save = () => startSave(id);
  let status = null;
  if (st === "have") status = ["ok", "this post is in FeedVault"];
  else if (UNDER_WAY.has(st)) status = ["busy", `saving this post (${{ sending: "sending", queued: "queued", running: "saving" }[st]})`];
  else if (st === "failed") status = ["failed", `this post's save failed: ${s.message || "failed"}`];
  else if (st === "offline") status = ["failed", "FeedVault is not running"];
  return {
    sig: JSON.stringify(["post", id, st, s?.job?.id, s?.message]),
    status,
    build: () => {
      const out = [head("This post")];
      if (st === "have") out.push(linkItem(`${API_BASE}${postPath(id)}`, "View in FeedVault", { state: "done", title: "This post in FeedVault" }));
      else if (st === "checking") out.push(item("Save post", null, { state: "busy", why: "Asking FeedVault…" }));
      else if (st === "sending") out.push(item("Sending…", null, { state: "busy" }));
      else if (st === "queued") out.push(item("Queued", null, { state: "busy", title: jobTitle(s) }));
      else if (st === "running") out.push(item("Saving…", null, { state: "busy", title: jobTitle(s) }));
      else if (st === "failed") {
        out.push(item("Failed — retry", save, { state: "failed", title: s.message || "" }),
                 note(s.message || "Save failed", SETTINGS_ERRORS.has(s.error)));
      } else if (st === "offline") out.push(item("FeedVault is not running — retry", save, { state: "offline" }));
      else out.push(item("Save post", save, { title: "Download this one post into FeedVault" }));
      return out;
    },
  };
}

// ---------------------------------------------------------------------------
// The profile open: Add profile (adds its source, after asking in the menu,
// and downloads nothing), Sync now (syncs that source) and View in
// FeedVault. Which tool, link and folder: FeedVault's answer for the
// profile (GET /api/sources/resolve, its link routing). A source that runs
// a script or logs in is only synced from the dashboard ("dashboard_only"):
// the menu says so and links there. One set to Don't sync in FeedVault (its
// account or person) offers neither: FeedVault refuses to add or sync it,
// and only its dashboard turns that off.
// ---------------------------------------------------------------------------

// name -> { state: "checking" | "ready" | "failed" | "offline" (asking about it), message,
//           source, resolved, nosync,
//           add: { state: "confirm" | "sending" | "added" | "failed" | "offline", message } | null,
//           sync: { state: "sending" | "queued" | "running" | "done" | "failed" | "offline" | "dashboard",
//                   job, message, error } | null }
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

// Where FeedVault shows a profile's source: its person, else its account's
// posts, else (not synced yet) the source itself, in Creators. FeedVault
// lists an account's posts by its id, which only a source knows: without
// one there is nothing to link to.
function sourceView(src) {
  if (src?.person) return { href: `${API_BASE}/people/${encodeURIComponent(src.person.id)}`, title: `${src.person.name} in FeedVault` };
  if (src?.account) {
    const q = new URLSearchParams({ platform: src.account.platform, author: src.account.id });
    return { href: `${API_BASE}/?${q}`, title: "The account's posts in FeedVault" };
  }
  return src ? { href: `${API_BASE}/creators`, title: "Its source, in FeedVault's Creators" } : null;
}

// Where its source is shown in the dashboard: its person's page, else Creators.
function dashboardLink(src) {
  const to = src?.person ? `${API_BASE}/people/${encodeURIComponent(src.person.id)}` : `${API_BASE}/creators`;
  return link(to, "Open it in FeedVault");
}

function profilePart() {
  const name = profileName();
  if (!name) return null;
  if (!profiles.has(name)) {
    profiles.set(name, { state: "checking" });
    checkProfile(name);
  }
  const s = profiles.get(name);
  const src = s.source;
  return {
    sig: JSON.stringify(["profile", name, s.state, s.message, s.nosync, src?.id, src?.person?.id, src?.person?.name,
                         src?.account?.id, src?.dashboard_only, s.add?.state, s.add?.message,
                         s.sync?.state, s.sync?.job?.id, s.sync?.message]),
    status: profileStatus(name, s),
    build: () => profileItems(name, s),
  };
}

function profileStatus(name, s) {
  const add = s.add?.state, sync = s.sync?.state;
  if (s.state === "offline" || add === "offline" || sync === "offline") return ["failed", "FeedVault is not running"];
  if (s.state === "failed") return ["failed", `could not look up @${name}: ${s.message || "failed"}`];
  if (add === "failed") return ["failed", `adding @${name} failed: ${s.add.message || "failed"}`];
  if (sync === "failed") return ["failed", `syncing @${name} failed: ${s.sync.message || "failed"}`];
  if (add === "sending") return ["busy", `adding @${name}`];
  if (UNDER_WAY.has(sync)) return ["busy", `syncing @${name} (${sync === "running" ? "running" : "queued"})`];
  if (s.source) {
    const more = s.nosync ? " (set to Don't sync)" : sync === "done" ? `; ${s.sync.message}` : "";
    return ["ok", `@${name} is in FeedVault${more}`];
  }
  return null;
}

function profileItems(name, s) {
  const out = [head(`@${name}`)];
  if (s.state === "checking") {
    out.push(item("Asking FeedVault…", null, { state: "busy" }));
    return out;
  }
  if (s.state === "offline") {
    out.push(item("FeedVault is not running — retry", () => recheck(name), { state: "offline" }));
    return out;
  }
  if (s.state === "failed") {
    out.push(item("Lookup failed — retry", () => recheck(name), { state: "failed" }), note(s.message || "Failed"));
    return out;
  }
  const src = s.source;
  const add = s.add?.state;
  // Add profile: adds the source, nothing more.
  if (src) {
    out.push(item("Add profile", null, add === "added" ? { state: "done", why: "Added. Sync now downloads it." }
                                                       : { why: "Already in FeedVault" }));
  } else if (s.nosync) out.push(item("Add profile", null, { why: "Set to Don't sync in FeedVault" }));
  else if (add === "confirm") {
    const where = s.resolved?.folder ? `downloading into its folder ${s.resolved.folder.split(/[\\/]/).filter(Boolean).pop()}`
                                     : "downloading into your first media root";
    const row = el("div", { class: "fv-row" });
    row.append(item(`Add @${name}`, () => addProfile(name), { state: "confirm" }),
               item("Cancel", () => setProfile(name, { add: null })));
    out.push(note(`New ${s.resolved?.tool || ""} source for @${name}, ${where}. Nothing is downloaded until you sync it.`), row);
  } else if (add === "sending") out.push(item("Adding…", null, { state: "busy" }));
  else if (add === "failed") {
    out.push(item("Add profile — retry", () => addProfile(name), { state: "failed", title: s.add.message || "" }),
             note(s.add.message || "Add failed"));
  } else if (add === "offline") {
    out.push(item("Add profile — retry", () => addProfile(name), { state: "offline", why: "FeedVault is not running" }));
  } else {
    out.push(item("Add profile", () => setProfile(name, { add: { state: "confirm" } }),
                  { title: `Add @${name} to FeedVault as a ${s.resolved?.tool || ""} source, without syncing it` }));
  }
  // Sync now: the source's sync, its progress in the item.
  const sync = s.sync;
  const go = () => syncSource(name);
  if (!src) out.push(item("Sync now", null, { why: s.nosync ? "Set to Don't sync in FeedVault" : "Add the profile first" }));
  else if (s.nosync) out.push(item("Sync now", null, { why: "Set to Don't sync in FeedVault" }));
  else if (src.dashboard_only || sync?.state === "dashboard") {
    const n = note(sync?.message || "This profile's source runs a script or logs in, which only FeedVault's own dashboard can start.");
    n.append(el("br"), dashboardLink(src));
    out.push(item("Sync now", null, { state: "dashboard", why: "Only from FeedVault's dashboard" }), n);
  } else if (sync?.state === "sending") out.push(item("Starting sync…", null, { state: "busy" }));
  else if (sync?.state === "queued") out.push(item("Sync queued", null, { state: "busy", title: `Job #${sync.job.id}` }));
  else if (sync?.state === "running") out.push(item("Syncing…", null, { state: "busy", title: `Job #${sync.job.id}` }));
  else if (sync?.state === "done") out.push(item("Sync now", go, { state: "done", why: sync.message || "Synced" }));
  else if (sync?.state === "failed") {
    out.push(item("Sync failed — retry", go, { state: "failed", title: sync.message || "" }),
             note(sync.message || "Sync failed", SETTINGS_ERRORS.has(sync.error)));
  } else if (sync?.state === "offline") out.push(item("Sync now — retry", go, { state: "offline", why: "FeedVault is not running" }));
  else out.push(item("Sync now", go, { title: `Download what is new from @${name}` }));
  if (s.nosync) {
    const n = note("Set to Don't sync in FeedVault: it downloads nothing from this profile. Its dashboard can turn that off.");
    if (src) n.append(el("br"), dashboardLink(src));
    out.push(n);
  }
  // View in FeedVault.
  const to = sourceView(src);
  out.push(to ? linkItem(to.href, "View in FeedVault", { title: to.title })
              : item("View in FeedVault", null, { why: "Not in FeedVault yet" }));
  return out;
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
  const nosync = r.body.no_sync === true || src?.no_sync === true;
  setProfile(name, { state: "ready", source: src, resolved, nosync });
  if (src?.job && !src.dashboard_only && !nosync) followSync(name, src.job.id);   // already syncing: show it
}

// Asked again, from the start (FeedVault was not running, or the answer failed).
function recheck(name) {
  profiles.delete(name);
  paint();
}

// Adds the profile's source, and only that: Sync now is its own item.
async function addProfile(name) {
  const s = profiles.get(name);
  if (s?.state !== "ready" || s.source || s.nosync || !["confirm", "failed", "offline"].includes(s.add?.state)) return;
  setProfile(name, { add: { state: "sending" } });
  const r = await api("POST", "/api/sources", SITE.addBody(name));
  if (r === null) { setProfile(name, { add: { state: "offline" } }); return; }
  const b = r.body || {};
  if (r.status === 409 && b.no_sync === true) { setProfile(name, { nosync: true, add: null }); return; }
  if (!b.ok || typeof b.source?.id !== "number") {
    setProfile(name, { add: { state: "failed", message: b.error || `FeedVault answered ${r.status}` } });
    return;
  }
  setProfile(name, { source: b.source, add: { state: "added" } });
}

async function syncSource(name) {
  const s = profiles.get(name);
  const src = s?.source;
  if (!src || s.nosync || src.dashboard_only || UNDER_WAY.has(s.sync?.state)) return;
  setProfile(name, { sync: { state: "sending" } });
  const r = await api("POST", `/api/sources/${encodeURIComponent(src.id)}/sync`);
  if (r === null) { setProfile(name, { sync: { state: "offline" } }); return; }
  const b = r.body || {};
  if (r.status === 409 && b.no_sync === true) { setProfile(name, { nosync: true, sync: null }); return; }
  if (r.status === 409) {                      // already queued or running: follow that one
    const got = await api("GET", `/api/sources/${encodeURIComponent(src.id)}`);
    if (got?.body?.job) { followSync(name, got.body.job.id); return; }
  }
  // Refused to this page (a source that runs a script or logs in): the dashboard can.
  if (r.status === 403) { setProfile(name, { sync: { state: "dashboard", message: b.error || null } }); return; }
  if (!b.ok || typeof b.job?.id !== "number") {
    setProfile(name, { sync: { state: "failed", message: b.error || `FeedVault answered ${r.status}` } });
    return;
  }
  followSync(name, b.job.id);
}

function followSync(name, jobId) {
  setProfile(name, { sync: { state: "queued", job: { id: jobId } } });
  watchJob(jobId, () => profiles.get(name)?.sync?.job?.id === jobId, () => profileName() === name, async (job) => {
    if (!job) { setProfile(name, { sync: { state: "failed", message: GONE } }); return; }
    if (!ENDED.has(job.state)) { setProfile(name, { sync: { state: job.state === "running" ? "running" : "queued", job } }); return; }
    // The source after it: its account and person may be known only now.
    const sid = profiles.get(name)?.source?.id;
    const got = sid === undefined ? null : await api("GET", `/api/sources/${encodeURIComponent(sid)}`);
    const source = got?.status === 200 && typeof got.body?.id === "number" ? got.body : profiles.get(name)?.source;
    if (job.state === "done") setProfile(name, { source, sync: { state: "done", job, message: job.message ? `Synced: ${job.message}` : "Synced" } });
    else setProfile(name, { source, sync: { state: "failed", job, message: job.message || job.state, error: job.result?.error || null } });
  });
}

// ---------------------------------------------------------------------------
// Save links: on a person's link-in-bio page, its address and the links it
// shows, as the browser rendered them (nothing is fetched, here or by
// FeedVault), saved to a person's Links or to no one's (Unsorted) by POST
// /api/links/import. Only new links are added: FeedVault never changes or
// removes one saved before. Links are bookmarks: nothing is downloaded.
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
    if (a.closest(".fv-bubble, .fv-toast")) continue;
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
  row.append(item("Save links", () => sendBio(page), { state: "confirm" }),
             item("Cancel", () => setBio(page, { state: "idle" })));
  return [find, select, row,
          note(`This page and the ${s.count} link${s.count === 1 ? "" : "s"} it shows, to the person's Links; links saved already stay as they are.`)];
}

function bioDone(s) {
  const r = s.result;
  // shared: saved already for someone else, now this person's too (#215);
  // an older FeedVault counted those as elsewhere.
  const parts = [`${r.saved} saved`, `${r.already + (r.elsewhere || 0)} already there`];
  if (r.shared) parts.push(`${r.shared} now theirs too`);
  if (r.over) parts.push(`${r.over} over the limit`);
  if (r.dropped) parts.push(`${r.dropped} refused`);
  const n = note(parts.join(", "));
  const who = s.people?.find((p) => String(p.id) === String(r.person));
  n.append(el("br"), who ? link(`${API_BASE}/people/${encodeURIComponent(who.id)}`, `${who.name} in FeedVault`)
                         : link(`${API_BASE}/links`, "Links in FeedVault"));
  return n;
}

function bioPart() {
  const page = bioPage();
  if (!page) return null;
  if (!bios.has(page)) bios.set(page, { state: "idle" });
  const s = bios.get(page);
  const open = () => openBio(page);
  let status = null;
  if (s.state === "loading" || s.state === "sending") status = ["busy", "saving this page's links"];
  else if (s.state === "failed") status = ["failed", `saving links failed: ${s.message || "failed"}`];
  else if (s.state === "offline") status = ["failed", "FeedVault is not running"];
  else if (s.state === "done") status = ["ok", "this page's links are in FeedVault"];
  return {
    sig: JSON.stringify(["bio", page, s.state, s.message, s.result]),
    status,
    build: () => {
      const out = [head("Link-in-bio page")];
      if (s.state === "loading") out.push(item("Loading people…", null, { state: "busy" }));
      else if (s.state === "sending") out.push(item("Saving links…", null, { state: "busy" }));
      else if (s.state === "pick") out.push(...bioPicker(page, s));
      else if (s.state === "offline") out.push(item("Save links — retry", open, { state: "offline", why: "FeedVault is not running" }));
      else if (s.state === "failed") {
        out.push(item("Save links — retry", open, { state: "failed" }),
                 note(s.message || "Failed", s.settings ? "Open Settings → Downloads" : false));
      } else {
        out.push(item("Save links", open, { state: s.state === "done" ? "done" : "idle",
                                            title: "This page and the links it shows, to a person's Links" }));
        if (s.state === "done") out.push(bioDone(s));
      }
      return out;
    },
  };
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
// On every page: Add this page to Links (also the userscript manager's menu
// command), Open FeedVault and Reset bubble position. Add this page sends
// its address and title, to no one (Unsorted). Only the user can pick a
// menu command: no page script reaches it.
// ---------------------------------------------------------------------------

// page address -> { state: "sending" | "added" | "already" | "failed" | "offline", message }
const pageAdds = new Map();

// What Add this page did, said where it was asked: in the menu while it is
// open, else in a toast across the window from the bubble (the other half,
// top or bottom, on its side), never over it.
function toast(text, to = null) {
  if (isOpen()) return;
  document.querySelector(".fv-toast")?.remove();
  const t = el("div", { class: "fv-toast" }, text);
  if (to) t.append(el("br"), to);
  const r = ROOT?.getBoundingClientRect();
  const [vw, vh] = view();
  const low = !r || r.top + r.height / 2 > vh / 2;
  const right = !r || r.left + r.width / 2 > vw / 2;
  Object.assign(t.style, { [low ? "top" : "bottom"]: "18px", [right ? "right" : "left"]: "18px" });
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 6000);
}

function setPageAdd(href, value) {
  pageAdds.set(href, value);
  paint();
}

async function addPage() {
  const href = location.href;
  if (pageAdds.get(href)?.state === "sending") return;
  const title = (document.title || "").replace(/\s+/g, " ").trim().slice(0, 300);
  setPageAdd(href, { state: "sending" });
  toast("Saving to FeedVault Links…");
  const r = await api("POST", "/api/links", { url: href, title });
  if (r === null) {
    setPageAdd(href, { state: "offline" });
    toast("FeedVault is not running");
    return;
  }
  const b = r.body || {};
  const to = () => link(`${API_BASE}/links`, "Links in FeedVault");
  if (r.status === 409) {
    const people = b.link?.people || (b.link?.person ? [b.link.person] : []);   // an older FeedVault: person
    const who = people.map((p) => p.name).join(", ");
    const text = who ? `Already in FeedVault Links, with ${who}` : "Already in FeedVault Links";
    setPageAdd(href, { state: "already", message: text });
    toast(text, to());
  } else if (!b.ok) {
    const text = b.error || `FeedVault answered ${r.status}`;
    setPageAdd(href, { state: "failed", message: text });
    toast(text);
  } else {
    setPageAdd(href, { state: "added", message: "Added to FeedVault Links (Unsorted)" });
    toast("Added to FeedVault Links (Unsorted)", to());
  }
}

function pagePart() {
  const href = location.href;
  const s = pageAdds.get(href);
  let status = null;
  if (s?.state === "sending") status = ["busy", "adding this page to Links"];
  else if (s?.state === "failed") status = ["failed", `adding this page to Links failed: ${s.message}`];
  else if (s?.state === "offline") status = ["failed", "FeedVault is not running"];
  return {
    sig: JSON.stringify(["page", href, s?.state, s?.message]),
    status,
    build: () => {
      const out = [];
      const add = () => addPage();
      if (s?.state === "sending") out.push(item("Adding to Links…", null, { state: "busy" }));
      else if (s?.state === "added" || s?.state === "already") {
        const n = note(s.message);
        n.append(el("br"), link(`${API_BASE}/links`, "Links in FeedVault"));
        out.push(item("Add this page to Links", add, { state: "done" }), n);
      } else if (s?.state === "failed") out.push(item("Add this page to Links — retry", add, { state: "failed" }), note(s.message));
      else if (s?.state === "offline") {
        out.push(item("Add this page to Links — retry", add, { state: "offline", why: "FeedVault is not running" }));
      } else out.push(item("Add this page to Links", add, { title: "Its address and title, to Unsorted in FeedVault's Links" }));
      out.push(linkItem(`${API_BASE}/`, "Open FeedVault", { title: "The dashboard, in a new tab" }),
               item("Reset bubble position", resetSpot, { title: "Back to its default corner on this site" }));
      return out;
    },
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// A page that does not look as expected leaves nothing behind: no error in
// the page's console, the next change paints again.
function paint() {
  for (const part of SITE ? [paintTiles, paintBubble] : [paintBubble]) {
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
  addEventListener("resize", () => place(ROOT));
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("pointerdown", onOutside, true);
  let raf = null;
  new MutationObserver(() => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = null; paint(); });
  }).observe(document.body, { childList: true, subtree: true });

  // These sites are single-page apps; misses expire, so look again now and then.
  setInterval(paint, MISS_TTL_MS);
  paint();
}
