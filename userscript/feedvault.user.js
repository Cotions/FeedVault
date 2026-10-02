// ==UserScript==
// @name         FeedVault
// @namespace    https://github.com/Cotions/feedvault
// @version      0.2.1
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
const SAVE_PATH_RE = /^\/(?:[A-Za-z0-9._]{1,30}\/)?(?:p|reel|reels|tv)\/([A-Za-z0-9_-]{5,40})\/?$/;
// A profile page (or one of its tabs) the Sync profile button shows on.
const PROFILE_PATH_RE = /^\/([A-Za-z0-9._]{1,30})\/(?:(?:reels|tagged)\/)?$/;
// First path parts that are Instagram's own pages, not profiles.
const NOT_PROFILES = new Set(["p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about",
  "developer", "legal", "web", "emails", "challenge", "session", "graphql", "api", "privacy", "terms",
  "lite", "your_activity", "notifications", "nametag", "settings", "login", "signup", "_n", "_u"]);
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
  .fv-row { display: flex; gap: 6px; }
`);

// id -> { saved: bool, at: ms }
const cache = new Map();
let pending = new Set();
let timer = null;
let offline = false;                           // the last /api/saved went unanswered

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
  if ((saved === null) !== offline) { offline = saved === null; paintPage(); }
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
  if (settings) n.append(el("br"), link(`${API_BASE}/settings`, "Open Settings → Downloaders"));
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
  const code = shortcodeFromHref(location.pathname);
  const id = code && `instagram:${code}`;
  const fresh = code ? known(id) : null;
  if (code && fresh === null) pending.add(id);
  // While a stale "not saved" is asked again, keep showing it (no flicker).
  const have = fresh ?? (code ? cache.get(id)?.saved ?? null : null);
  if (have !== true && code !== savePathCode()) { dropPanel("post:"); return; }
  if (code && have === null && !saves.has(code) && offline) {
    const p = panel(`post:${code}`);
    render(p, "offline", () => [button("offline", "FeedVault is not running — retry", () => startSave(code))]);
    return;
  }
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
    if (st === "sending") return [button("busy", "Sending…", null)];
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
  if (!b.job || typeof b.job.id !== "number") { setSave(code, { state: "failed", message: "unexpected answer" }); return; }
  applyJob(code, b.job);
  if (ENDED.has(b.job.state)) return;
  watchJob(b.job.id, () => saves.get(code)?.job?.id === b.job.id, () => savePathCode() === code,
    (job) => (job ? applyJob(code, job) : setSave(code, { state: "failed", message: GONE })));
}

// A Save job's state, as GET /api/jobs/<id> says it.
function applyJob(code, job) {
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
// Sync profile button: adds the profile's instaloader source (after asking,
// in the button itself) unless there is one, then syncs it
// ---------------------------------------------------------------------------

// name -> { state: "checking" | "idle" | "confirm" | "sending" | "queued" | "running" | "done"
//                  | "failed" | "offline", source, checked, job, message, error }
const profiles = new Map();

function profileName() {
  const m = location.pathname.match(PROFILE_PATH_RE);
  if (!m || NOT_PROFILES.has(m[1].toLowerCase()) || !m[1].replace(/\./g, "")) return null;
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
      out.push(row, note(`New instaloader source for @${name}, downloading into the folder ${name} of your first media root.`));
    } else if (s.state === "queued") out.push(button("busy", "Sync queued", null, `Job #${s.job.id}`));
    else if (s.state === "running") out.push(button("busy", "Syncing…", null, `Job #${s.job.id}`));
    else if (s.state === "offline") out.push(button("offline", "FeedVault is not running — retry", () => clickProfile(name)));
    else if (s.state === "failed") {
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

// Is there an instaloader source for it already? (Asked once per profile
// visited; whatever the link routing says, the button adds an instaloader one.)
async function checkProfile(name) {
  const r = await api("GET", "/api/sources");
  if (r === null) { setProfile(name, { state: "offline" }); return; }
  if (!Array.isArray(r.body?.sources)) { setProfile(name, { state: "failed", message: `FeedVault answered ${r.status}` }); return; }
  const src = r.body.sources.find((x) => x.tool === "instaloader" && x.target === name) || null;
  setProfile(name, { state: "idle", source: src, checked: true });
  if (src?.job) followSync(name, src.job.id);   // already syncing: show it
}

function clickProfile(name) {
  const s = profiles.get(name);
  if (s?.source) { syncSource(name, s.source.id); return; }
  if (!s?.checked) { profiles.delete(name); paint(); return; }   // offline, or the check failed: ask again
  setProfile(name, { state: "confirm" });
}

async function addAndSync(name) {
  setProfile(name, { state: "sending", message: null });
  const r = await api("POST", "/api/sources", { tool: "instaloader", target: name });
  if (r === null) { setProfile(name, { state: "offline" }); return; }
  const b = r.body || {};
  if (!b.ok) { setProfile(name, { state: "failed", message: b.error || `FeedVault answered ${r.status}` }); return; }
  setProfile(name, { source: b.source });
  syncSource(name, b.source.id);
}

async function syncSource(name, sid) {
  setProfile(name, { state: "sending", message: null, error: null });
  const r = await api("POST", `/api/sources/${encodeURIComponent(sid)}/sync`);
  if (r === null) { setProfile(name, { state: "offline" }); return; }
  const b = r.body || {};
  if (r.status === 409) {                      // already queued or running: follow that one
    const got = await api("GET", `/api/sources/${encodeURIComponent(sid)}`);
    if (got?.body?.job) { followSync(name, got.body.job.id); return; }
  }
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function paint() {
  paintTiles();
  paintPage();
  paintProfile();
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
