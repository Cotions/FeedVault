// ==UserScript==
// @name         FeedVault
// @namespace    https://github.com/Cotions/feedvault
// @version      0.2.0
// @description  Marks Instagram posts you already have in FeedVault, and saves the ones you don't
// @author       Cotions
// @match        https://www.instagram.com/*
// @connect      localhost
// @connect      127.0.0.1
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @run-at       document-idle
// @updateURL    http://localhost:3380/userscript/feedvault.user.js
// @downloadURL  http://localhost:3380/userscript/feedvault.user.js
// ==/UserScript==

// Only this script talks to FeedVault, through GM_xmlhttpRequest: the page
// gets no function, message handler or data from it, and nothing is read
// from the page's own JavaScript. Shortcodes and profile names come from
// location.pathname and link hrefs, checked against the patterns below.
const API_BASE = "http://localhost:3380";
// Backend denies every API call without this header. Ordinary web pages cannot
// attach a custom header cross-origin; this privileged script can.
const FV_HEADERS = { "X-FeedVault": "1", "Content-Type": "application/json" };
const BADGE_CLASS = "fv-badge";
const MARKED_ATTR = "data-fv";
// "Not saved" answers go stale when a download lands, so ask again after this.
const MISS_TTL_MS = 30_000;
const POST_PATH_RE = /^\/(?:[^/]+\/)?(?:p|reel|reels|tv)\/([A-Za-z0-9_-]+)/;
// A post page or dialog the Save button shows on: the whole path, and a
// shortcode as POST /api/save accepts it.
const SAVE_PATH_RE = /^\/(?:[A-Za-z0-9._]{1,30}\/)?(?:p|reel)\/([A-Za-z0-9_-]{5,40})\/?$/;
const JOB_POLL_MS = 1500;
const SETTINGS_ERRORS = new Set(["missing", "login_required"]);   // fixed in Settings → Downloaders

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
  .fv-panel {
    position: fixed; right: 18px; bottom: 18px; z-index: 2147483000;
    display: flex; flex-direction: column; align-items: flex-end; gap: 4px;
    max-width: 320px; font: 600 12px/16px ui-monospace, "JetBrains Mono", monospace;
  }
  .fv-btn {
    all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 11px; border-radius: 999px; color: #e8ecf6; background: rgba(10, 12, 17, 0.88);
    box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.35); font: inherit; text-decoration: none;
  }
  .fv-btn:hover { box-shadow: 0 0 0 1px rgba(232, 236, 246, 0.8); }
  .fv-btn[aria-disabled="true"] { cursor: default; opacity: 0.85; }
  .fv-btn[data-state="done"] { box-shadow: 0 0 0 1px rgba(74, 222, 128, 0.7); }
  .fv-btn[data-state="failed"], .fv-btn[data-state="offline"] { box-shadow: 0 0 0 1px rgba(248, 113, 113, 0.8); }
  .fv-btn[data-state="confirm"] { box-shadow: 0 0 0 1px rgba(250, 204, 21, 0.8); }
  .fv-note {
    padding: 4px 9px; border-radius: 8px; color: #e8ecf6; background: rgba(10, 12, 17, 0.88);
    font-weight: 400; overflow-wrap: anywhere;
  }
  .fv-note a { color: #93c5fd; }
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
    if (a.closest(".fv-panel")) continue;      // our own links to FeedVault
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

// ---------------------------------------------------------------------------
// Save button: a post page, or the dialog a grid opens (it sets the same path)
// ---------------------------------------------------------------------------

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

const ENDED = new Set(["done", "failed", "cancelled", "interrupted"]);
// shortcode -> { state: "sending" | "queued" | "running" | "failed" | "offline", job, message, error }
const saves = new Map();

function savePathCode() {
  const m = location.pathname.match(SAVE_PATH_RE);
  return m ? m[1] : null;
}

function el(tag, attrs = {}, text = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

function link(href, text, cls = "") {
  return el("a", { href, target: "_blank", rel: "noreferrer", ...(cls ? { class: cls } : {}) }, text);
}

// The panel in the corner: one button (or link) and, below it, a note.
function panel(key) {
  let p = document.querySelector(".fv-panel");
  if (p && p.dataset.key === key) return p;
  p?.remove();
  p = el("div", { class: "fv-panel" });
  p.dataset.key = key;
  document.body.appendChild(p);
  return p;
}

function render(p, sig, build) {
  if (p.dataset.sig === sig) return;           // unchanged: keep the DOM (and focus) as it is
  p.dataset.sig = sig;
  p.replaceChildren(...build());
}

function note(text, settings) {
  const n = el("div", { class: "fv-note" }, text);
  if (settings) {
    n.append(" — see ");
    n.append(link(`${API_BASE}/settings`, "Settings → Downloaders"));
  }
  return n;
}

function button(state, text, onClick, title = "") {
  const b = el("button", { class: "fv-btn", type: "button", "data-state": state, ...(title ? { title } : {}) }, text);
  if (onClick) b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); onClick(); });
  else b.setAttribute("aria-disabled", "true");
  return b;
}

function dropPanel(prefix) {
  const p = document.querySelector(".fv-panel");
  if (p && p.dataset.key.startsWith(prefix)) p.remove();
}

// A post FeedVault has gets a link to it; another, the Save button and its state.
function paintPage() {
  const code = savePathCode();
  const id = code && `instagram:${code}`;
  const have = code ? known(id) : null;
  if (code && have === null) pending.add(id);
  if (!code || (have === null && !saves.has(code))) { dropPanel("post:"); return; }
  const p = panel(`post:${code}`);
  if (have) {
    render(p, "done", () => {
      const a = link(`${API_BASE}/p/instagram/${code}`, "In FeedVault", "fv-btn");
      a.dataset.state = "done";
      return [a];
    });
    return;
  }
  const s = saves.get(code);
  const st = s?.state || "idle";
  render(p, `${st}|${s?.job?.id || ""}|${s?.message || ""}`, () => {
    if (st === "sending") return [button("busy", "Saving…", null)];
    if (st === "queued") {
      const wait = s.job.waits_until ? `, waits until ${new Date(s.job.waits_until * 1000).toLocaleTimeString()}` : "";
      return [button("busy", "Queued", null, `Job #${s.job.id}${wait}`)];
    }
    if (st === "running") return [button("busy", "Saving…", null, `Job #${s.job.id}`)];
    if (st === "failed") {
      return [button("failed", "Failed — retry", () => startSave(code), s.message || ""),
              note(s.message || "Save failed", SETTINGS_ERRORS.has(s.error))];
    }
    if (st === "offline") return [button("offline", "FeedVault is not running — retry", () => startSave(code))];
    return [button("idle", "Save to FeedVault", () => startSave(code))];
  });
}

function setSave(code, value) {
  saves.set(code, value);
  paint();
}

async function startSave(code) {
  if (!SAVE_PATH_RE.test(`/p/${code}/`)) return;
  setSave(code, { state: "sending" });
  const r = await api("POST", "/api/save", { platform: "instagram", shortcode: code });
  if (r === null) { setSave(code, { state: "offline" }); return; }
  const b = r.body || {};
  if (!b.ok) { setSave(code, { state: "failed", message: b.error || `FeedVault answered ${r.status}` }); return; }
  if (b.have) {
    cache.set(`instagram:${code}`, { saved: true, at: Date.now() });
    saves.delete(code);
    paint();
    return;
  }
  applyJob(code, b.job);
}

// A Save job's state, as GET /api/jobs/<id> says it; polls until it has ended.
function applyJob(code, job) {
  if (!job || typeof job.id !== "number") return;
  if (job.state === "done" && job.result?.post) {
    cache.set(`instagram:${code}`, { saved: true, at: Date.now() });
    saves.delete(code);
    paint();
    return;
  }
  if (ENDED.has(job.state)) {
    setSave(code, { state: "failed", job, message: job.message || job.state, error: job.result?.error || null });
    return;
  }
  setSave(code, { state: job.state === "running" ? "running" : "queued", job });
  setTimeout(() => pollJob(code, job.id), JOB_POLL_MS);
}

async function pollJob(code, jobId) {
  if (saves.get(code)?.job?.id !== jobId) return;   // retried since: another job
  const r = await api("GET", `/api/jobs/${jobId}`);
  if (r === null) { setTimeout(() => pollJob(code, jobId), JOB_POLL_MS * 4); return; }   // FeedVault restarting
  if (r.status === 404) { setSave(code, { state: "failed", message: "the job is gone (FeedVault restarted?)" }); return; }
  applyJob(code, r.body);
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
