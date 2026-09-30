// ==UserScript==
// @name         FeedVault
// @namespace    https://github.com/Cotions/feedvault
// @version      0.1.0
// @description  Marks Instagram posts you already have in FeedVault
// @author       Cotions
// @match        https://www.instagram.com/*
// @connect      localhost
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @run-at       document-idle
// @updateURL    http://localhost:3380/userscript/feedvault.user.js
// @downloadURL  http://localhost:3380/userscript/feedvault.user.js
// ==/UserScript==

const API_BASE = "http://localhost:3380";
// Backend denies every API call without this header. Ordinary web pages cannot
// attach a custom header cross-origin; this privileged script can.
const FV_HEADERS = { "X-FeedVault": "1", "Content-Type": "application/json" };
const BADGE_CLASS = "fv-badge";
const MARKED_ATTR = "data-fv";
// "Not saved" answers go stale when a download lands, so ask again after this.
const MISS_TTL_MS = 30_000;
const POST_PATH_RE = /^\/(?:[^/]+\/)?(?:p|reel|reels|tv)\/([A-Za-z0-9_-]+)/;

GM_addStyle(`
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
  .fv-page-badge {
    position: fixed; right: 18px; bottom: 18px; z-index: 9999;
    pointer-events: auto; padding: 5px 11px; font-size: 12px;
    text-decoration: none;
  }
`);

// id -> { saved: bool, at: ms }
const cache = new Map();
let pending = new Set();
let timer = null;

function shortcodeFromHref(href) {
  try {
    const m = new URL(href, location.origin).pathname.match(POST_PATH_RE);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

function known(id) {
  const hit = cache.get(id);
  if (!hit) return null;
  if (!hit.saved && Date.now() - hit.at > MISS_TTL_MS) return null;
  return hit.saved;
}

function ask(ids) {
  return new Promise((resolve) => {
    GM_xmlhttpRequest({
      method: "POST",
      url: `${API_BASE}/api/saved`,
      headers: FV_HEADERS,
      data: JSON.stringify({ ids }),
      timeout: 5000,
      onload: (r) => {
        try { resolve(new Set(JSON.parse(r.responseText).saved || [])); }
        catch { resolve(null); }
      },
      onerror: () => resolve(null),
      ontimeout: () => resolve(null),
    });
  });
}

async function flush() {
  timer = null;
  const ids = [...pending].slice(0, 400);
  ids.forEach((id) => pending.delete(id));
  if (!ids.length) return;
  const saved = await ask(ids);
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

// Grid tiles and feed links: every anchor that points at a post.
function paintTiles() {
  for (const a of document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]')) {
    const code = shortcodeFromHref(a.getAttribute("href"));
    if (!code) continue;
    const id = `instagram:${code}`;
    const state = known(id);
    if (state === null) { pending.add(id); continue; }
    const has = a.querySelector(`:scope > .${BADGE_CLASS}`);
    // Only tiles with an image get a badge; plain text links (timestamps,
    // "view all comments") stay untouched.
    if (state && !has && a.querySelector("img")) {
      if (getComputedStyle(a).position === "static") a.style.position = "relative";
      a.appendChild(badge("saved"));
      a.setAttribute(MARKED_ATTR, "1");
    } else if (!state && has) {
      has.remove();
    }
  }
}

// A single post page gets one fixed badge linking to the FeedVault copy.
function paintPage() {
  const code = shortcodeFromHref(location.pathname);
  let el = document.querySelector(".fv-page-badge");
  const id = code ? `instagram:${code}` : null;
  const state = id ? known(id) : false;
  if (id && state === null) pending.add(id);
  if (!state) { el?.remove(); return; }
  if (el && el.dataset.id === id) return;
  el?.remove();
  el = document.createElement("a");
  el.className = `${BADGE_CLASS} fv-page-badge`;
  el.dataset.id = id;
  el.href = `${API_BASE}/p/instagram/${code}`;
  el.target = "_blank";
  el.rel = "noreferrer";
  el.textContent = "in FeedVault";
  document.body.appendChild(el);
}

function paint() {
  paintTiles();
  paintPage();
  if (pending.size) schedule();
}

let raf = null;
new MutationObserver(() => {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = null; paint(); });
}).observe(document.body, { childList: true, subtree: true });

// Instagram is a single-page app; misses expire, so look again now and then.
setInterval(paint, MISS_TTL_MS);
paint();
