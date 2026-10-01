// Always same-origin: the backend serves the built dashboard, and in dev the
// Vite server proxies /api and /media to it (see vite.config.js).
const BASE = "";

// The backend refuses any API request that lacks this header, GET included. A
// hostile page in another tab cannot add a custom header without a CORS
// preflight, which the backend never grants, nor through an <img> or <video>.
// Same-origin fetch adds it freely. /media URLs are the exception: they load
// via src and the backend exempts them.
const CSRF_HEADERS = { "X-FeedVault": "1" };

/* Connection state, shared by every call. A fetch that never reaches the
   backend (network error, or the dev proxy answering 5xx with a non-JSON body
   because the backend is down) flips it offline; any answer flips it back. */
const listeners = new Set();
let online = null;
function setOnline(v) {
  if (online === v) return;
  online = v;
  for (const fn of listeners) fn(v);
}
export function onConnectionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request(method, path, body, { signal } = {}) {
  const opts = { method, headers: { ...CSRF_HEADERS }, signal };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(`${BASE}${path}`, opts);
  } catch (e) {
    if (e.name === "AbortError") throw e;      // cancelled by the caller, not a lost backend
    setOnline(false);
    throw new ApiError(`backend unreachable (${e.message})`, 0, null);
  }
  const isJson = (r.headers.get("content-type") || "").includes("application/json");
  if (!isJson && r.status >= 500) {
    setOnline(false);
    throw new ApiError("backend unreachable", r.status, null);
  }
  setOnline(true);
  const data = isJson ? await r.json() : null;
  if (!r.ok) {
    throw new ApiError((data && data.error) || `${method} ${path} → ${r.status}`, r.status, data);
  }
  return data;
}

const get  = (path, opts) => request("GET", path, undefined, opts);
// POST endpoints answer { ok: false, error } for refusals the UI should show
// (bad config, scan already running), so those come back as data, not throws.
async function post(path, body = {}) {
  try {
    return await request("POST", path, body);
  } catch (e) {
    if (e.body && typeof e.body === "object") return e.body;
    throw e;
  }
}

function qs(params) {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  }
  const str = s.toString();
  return str ? `?${str}` : "";
}

/* ── Endpoints (docs/API.md) ─────────────────────────────── */

// { total, posts: [summary] }. params: q, platform, author, kind, sort, offset, limit
export function getPosts(params = {}) { return get(`/api/posts${qs(params)}`); }
// Full post. Throws ApiError with status 404 when it is not in the index.
export function getPost(platform, postId) {
  return get(`/api/posts/${encodeURIComponent(platform)}/${encodeURIComponent(postId)}`);
}
// { posts, media, bytes } over every post the filters match. params: q,
// platform, author, kind, review (the /api/posts filters; paging is ignored).
export function getPostsSummary(params = {}, opts) { return get(`/api/posts/summary${qs(params)}`, opts); }
export function getAuthors()   { return get("/api/authors"); }
export function getStats()     { return get("/api/stats"); }
// { totals, by_author, by_kind, by_year, largest, trash }, see docs/API.md "Storage".
export function getStorage()   { return get("/api/storage"); }
export function getUnmatched() { return get("/api/unmatched"); }
export function getScan()      { return get("/api/scan"); }
export function startScan()    { return post("/api/scan"); }
export function getConfig()    { return get("/api/config"); }
export function saveConfig(mediaRoots) { return post("/api/config", { media_roots: mediaRoots }); }
// Opens a native folder picker on the backend machine; { path } or { path: null }.
export function browse()       { return get("/api/browse"); }
export function getSaved(ids)  { return post("/api/saved", { ids }); }
export function quitApp()      { return post("/api/quit"); }

/* ── Deleting (files move to <root>/.feedvault-trash/) ───── */

// body { posts: [id], media: [mediaId] }; either list may be omitted.
// → { ok, posts, media, files, bytes, errors: [{ path, error }] }
export function deleteItems({ posts, media } = {}) {
  const body = {};
  if (posts?.length) body.posts = posts;
  if (media?.length) body.media = media;
  return post("/api/delete", body);
}
export function getTrash()   { return get("/api/trash"); }
export function emptyTrash() { return post("/api/trash/empty"); }
// Trashed entries, newest deletion first → { total, files, bytes, trash, authors,
// entries }. params: author, since, before, offset, limit. See docs/API.md "Trash contents".
export function getTrashItems(params = {}) { return get(`/api/trash/items${qs(params)}`); }
// Permanently deletes entries' files: { keys } or { filter: { author, since, before } }
// → { ok, entries, keys, files, bytes, dropped, errors }
export function purgeTrash(body) { return post("/api/trash/purge", body); }
// Puts exactly those entries back (partial deletes too) → { ok, posts, files, errors }
export function restoreEntries(keys) { return post("/api/trash/restore", { keys }); }

/* ── Review (keep or trash) ──────────────────────────────── */

// decision: "keep" or null to clear → { ok, posts }
export function setDecision(posts, decision) { return post("/api/review", { posts, decision }); }
// Moves posts' files back out of the trash → { ok, posts, files, errors }
export function restorePosts(posts) { return post("/api/trash/restore", { posts }); }

/* ── Duplicates (see docs/API.md "Duplicates") ───────────── */

// kind: "copies" | "content" | "similar" (+ threshold) → { kind, threshold, total, reposts, identical, pending, frees, identical_frees, dismissed, groups }
export function getDuplicates(params = {}) { return get(`/api/duplicates${qs(params)}`); }
// The background hashing worker → { running, paused, phase, done, total, bytes, hashed, finished_at, errors }
export function getDuplicatesStatus() { return get("/api/duplicates/status"); }
// [{ group, keep }] → keeps one member per group, trashes the others
// → { ok, resolved, skipped: [{ group, error }], posts, copies, files, bytes, errors }
export function resolveDuplicates(groups) { return post("/api/duplicates/resolve", { groups }); }
// "Not a duplicate", stored for good → { ok }
export function dismissDuplicate(group) { return post("/api/duplicates/dismiss", { group }); }
