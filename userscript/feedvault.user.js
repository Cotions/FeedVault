// ==UserScript==
// @name         FeedVault
// @namespace    https://github.com/Cotions/feedvault
// @version      0.3.0
// @description  Marks Instagram, X and TikTok posts you already have in FeedVault, and saves the ones you don't
// @author       Cotions
// @match        https://www.instagram.com/*
// @match        https://x.com/*
// @match        https://twitter.com/*
// @match        https://www.tiktok.com/*
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
// from the page's own JavaScript. Post ids and profile names come from
// location.pathname and link hrefs, checked against the patterns below.
const API_BASE = "http://localhost:3380";
// Backend denies every API call without this header. Ordinary web pages cannot
// attach a custom header cross-origin; this privileged script can.
const FV_HEADERS = { "X-FeedVault": "1", "Content-Type": "application/json" };
const BADGE_CLASS = "fv-badge";
const MARKED_ATTR = "data-fv";
// "Not saved" answers go stale when a download lands, so ask again after this.
const MISS_TTL_MS = 30_000;
const JOB_POLL_MS = 1500;
const SETTINGS_ERRORS = new Set(["missing", "login_required"]);   // fixed in Settings → Downloaders

// ---------------------------------------------------------------------------
// The sites. Everything that knows a site's pages is here, one entry each:
// which links are posts (tiles), which page is a post (the Save button) or a
// profile (Sync profile), and what is sent for them. When a site changes its
// layout or its paths, a selector or pattern stops matching and that part
// does nothing: no button, no badge, no error.
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
    // sets the same path): the whole path, and a shortcode as POST /api/save
    // accepts it.
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

// The post the open page is, with what POST /api/save is sent for it, or null.
function savePost() {
  const m = location.pathname.match(SITE.postRe);
  if (!m) return null;
  return { id: `${SITE.platform}:${SITE.postId ? SITE.postId(m) : m[1]}`, body: SITE.saveBody(m) };
}

function postPath(id) {
  const [platform, code] = id.split(":");
  return `/p/${platform}/${code}`;
}

if (SITE) GM_addStyle(`
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
  .fv-btn[data-state="confirm"], .fv-btn[data-state="dashboard"] { box-shadow: 0 0 0 1px rgba(250, 204, 21, 0.8); }
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

// Every anchor that points at a post, as the site's entry picks them.
function paintTiles() {
  for (const a of document.querySelectorAll(SITE.tiles)) {
    if (a.closest(".fv-panel")) continue;      // our own links to FeedVault
    const id = postOf(a.getAttribute("href"));
    if (!id) continue;
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
    if (st === "queued") {
      const wait = s.job.waits_until ? `, waits until ${new Date(s.job.waits_until * 1000).toLocaleTimeString()}` : "";
      return [button("busy", "Queued", null, `Job #${s.job.id}${wait}`)];
    }
    if (st === "running") return [button("busy", "Saving…", null, `Job #${s.job.id}`)];
    if (st === "failed") {
      return [button("failed", "Failed — retry", () => startSave(id), s.message || ""),
              note(s.message || "Save failed", SETTINGS_ERRORS.has(s.error))];
    }
    if (st === "offline") return [button("offline", "FeedVault is not running — retry", () => startSave(id))];
    return [button("idle", "Save to FeedVault", () => startSave(id))];
  });
}

function setSave(id, value) {
  saves.set(id, value);
  paint();
}

// Saves the post the page shows, when it is still the one clicked for: what
// is sent is built from the path again, never kept from an earlier page.
async function startSave(id) {
  const post = savePost();
  if (!post || post.id !== id) return;
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
  watchJob(b.job.id, () => saves.get(id)?.job?.id === b.job.id, () => savePost()?.id === id,
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
// its link routing). A source that runs a script is only synced from the
// dashboard: the button says so and links there.
// ---------------------------------------------------------------------------

// name -> { state: "checking" | "idle" | "confirm" | "sending" | "queued" | "running" | "done"
//                  | "failed" | "offline" | "dashboard", source, resolved, checked, job, message, error }
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
      const where = s.resolved?.folder ? `downloading into ${s.resolved.folder}` : "downloading into your first media root";
      out.push(row, note(`New ${s.resolved?.tool || ""} source for @${name}, ${where}.`));
    } else if (s.state === "queued") out.push(button("busy", "Sync queued", null, `Job #${s.job.id}`));
    else if (s.state === "running") out.push(button("busy", "Syncing…", null, `Job #${s.job.id}`));
    else if (s.state === "offline") out.push(button("offline", "FeedVault is not running — retry", () => clickProfile(name)));
    else if (s.state === "dashboard") {
      const n = note(s.message || "This profile's source runs a script, which only FeedVault's own dashboard can start.");
      n.append(el("br"), dashboardLink(s.source));
      out.push(button("dashboard", "Sync from FeedVault", null), n);
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
  setProfile(name, { state: src?.options?.script ? "dashboard" : "idle", source: src, resolved, checked: true });
  if (src?.job && !src.options?.script) followSync(name, src.job.id);   // already syncing: show it
}

function clickProfile(name) {
  const s = profiles.get(name);
  if (s?.source) { syncSource(name, s.source.id); return; }
  if (!s?.checked) { profiles.delete(name); paint(); return; }   // offline, or the check failed: ask again
  setProfile(name, { state: "confirm" });
}

async function addAndSync(name) {
  setProfile(name, { state: "sending", message: null });
  const r = await api("POST", "/api/sources", SITE.addBody(name));
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
  // Refused to this page (a source that runs a script): the dashboard can.
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// A page that does not look as expected leaves nothing behind: no error in
// the page's console, the next change paints again.
function paint() {
  try {
    paintTiles();
    paintPage();
    paintProfile();
  } catch {
    /* the page changed under us */
  }
  if (pending.size) schedule();
}

if (SITE) {
  let raf = null;
  new MutationObserver(() => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = null; paint(); });
  }).observe(document.body, { childList: true, subtree: true });

  // These sites are single-page apps; misses expire, so look again now and then.
  setInterval(paint, MISS_TTL_MS);
  paint();
}
