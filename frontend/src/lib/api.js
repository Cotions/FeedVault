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

// DELETE, with the error body returned as post() returns it.
async function del(path) {
  try {
    return await request("DELETE", path);
  } catch (e) {
    if (e.body && typeof e.body === "object") return e.body;
    throw e;
  }
}

function qs(params) {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    // A list repeats the parameter (tag=a&tag=b).
    if (Array.isArray(v)) v.forEach(x => s.append(k, String(x)));
    // A flag (untagged, new) is on as 1, which the server reads; off is left out.
    else if (v === true) s.set(k, "1");
    else if (v !== undefined && v !== null && v !== "" && v !== false) s.set(k, String(v));
  }
  const str = s.toString();
  return str ? `?${str}` : "";
}

/* ── Endpoints (docs/API.md) ─────────────────────────────── */

// { total, posts: [summary] }. params: q, platform, author, person, kind, review,
// tag (a list: every one must match), untagged, sort, order, offset, limit
export function getPosts(params = {}) { return get(`/api/posts${qs(params)}`); }
// Full post. Throws ApiError with status 404 when it is not in the index.
export function getPost(platform, postId) {
  return get(`/api/posts/${encodeURIComponent(platform)}/${encodeURIComponent(postId)}`);
}
// { posts, media, bytes } over every post the filters match. params: q,
// platform, author, person, kind, review, tag, untagged (the /api/posts filters; paging is ignored).
export function getPostsSummary(params = {}, opts) { return get(`/api/posts/summary${qs(params)}`, opts); }
// [account], most posts first; see docs/API.md "People".
export function getAuthors()   { return get("/api/authors"); }

// New posts since the last "Mark all seen" (docs/API.md "New posts").
export function getNew()       { return get("/api/new"); }
// at: unix seconds, posts first seen after it stay new; default now.
// whom: {} for every post, { person: id } or { account: { platform, id } } for theirs only.
export function markSeen(at, whom = {}) { return post("/api/new/seen", at == null ? whom : { at, ...whom }); }
// whom: { person: id } or { account: { platform, id } } (one linked to nobody).
// → { ok, muted: { people: [ids], accounts: [{ platform, id }] } }
export function muteNew(whom, muted) { return post("/api/new/mute", { ...whom, muted }); }
// Syncs that brought new posts or failed (docs/API.md "Notifications"):
// { unread, latest, entries: newest first }.
export function getNotifications() { return get("/api/notifications"); }
// upto: an entry's id, those up to it are read; default all.
export function readNotifications(upto) { return post("/api/notifications/read", upto == null ? {} : { upto }); }
export function getStats(person) { return get(`/api/stats${qs({ person })}`); }
// { totals, by_author, by_kind, by_year, largest, trash }, see docs/API.md "Storage".
// person: an id, to cover that person's posts only.
export function getStorage(person) { return get(`/api/storage${qs({ person })}`); }
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
// Trashed entries, newest deletion first → { total, files, bytes, upto, trash, authors,
// entries }. params: platform, author, person, since, before, upto, offset, limit. See docs/API.md "Trash contents".
export function getTrashItems(params = {}) { return get(`/api/trash/items${qs(params)}`); }
// Looks at every trashed file now → { ok, entries, files, bytes, missing }.
export function checkTrash() { return post("/api/trash/check"); }
// Permanently deletes entries' files: { keys } or { filter: { platform, author, person, since, before, upto } }
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
// The background hashing worker → { running, paused, phase, done, total, bytes, hashed, fingerprinted, finished_at, errors }
export function getDuplicatesStatus() { return get("/api/duplicates/status"); }
// [{ group, keep }] → keeps one member per group, trashes the others
// → { ok, resolved, skipped: [{ group, error }], posts, copies, files, bytes, errors }
// A similar group goes alone, with the threshold it was listed at.
export function resolveDuplicates(groups, threshold) { return post("/api/duplicates/resolve", { groups, threshold }); }
// "Not a duplicate", stored for good → { ok }
export function dismissDuplicate(group, threshold) { return post("/api/duplicates/dismiss", { group, threshold }); }

/* ── Tags (see docs/API.md "Tags") ───────────────────────── */

// [{ name, color, count, unused }], most used first
export function getTags() { return get("/api/tags"); }
// color: "#rrggbb" or null → { ok, color }
export function setTagColor(name, color) { return post("/api/tags/color", { name, color }); }
// Deletes those of `names` on no post at all (not even a trashed one) → { ok, deleted: [names] }
export function deleteUnusedTags(names) { return post("/api/tags/delete-unused", { names }); }
// → { ok, posts, added, removed, created }
export function applyTags(posts, { add = [], remove = [] } = {}) {
  return post("/api/tags/apply", { posts, add, remove });
}
// Merges into `to` when that tag exists → { ok, name, merged }
export function renameTag(from, to) { return post("/api/tags/rename", { from, to }); }
// → { ok, posts }
export function deleteTag(name) { return post("/api/tags/delete", { name }); }

/* ── Collections (see docs/API.md "Collections") ─────────── */

// [{ id, name, count, created_at, cover_post, cover }]
export function getCollections() { return get("/api/collections"); }
// The given collections take the places they held, in this order → { ok, collections }
export function reorderCollections(ids) { return post("/api/collections/reorder", { ids }); }
// → { ok, collection } or { ok: false, error } (bad or taken name)
export function createCollection(name) { return post("/api/collections", { name }); }
// → { collection, total, posts: [summary] }, in the collection's order
export function getCollection(id, params = {}) { return get(`/api/collections/${id}${qs(params)}`); }
export function renameCollection(id, name) { return post(`/api/collections/${id}/rename`, { name }); }
export function deleteCollection(id) { return post(`/api/collections/${id}/delete`); }
// → { ok, added: [ids] }
export function addToCollection(id, posts) { return post(`/api/collections/${id}/add`, { posts }); }
export function removeFromCollection(id, posts) { return post(`/api/collections/${id}/remove`, { posts }); }
// The given posts take the places they held, in this order.
export function orderCollection(id, posts) { return post(`/api/collections/${id}/order`, { posts }); }
// post: an id in the collection, or null for the first post
export function setCollectionCover(id, postId) { return post(`/api/collections/${id}/cover`, { post: postId }); }

/* ── People (see docs/API.md "People") ───────────────────── */

// [{ id, name, notes, created_at, accounts: [account], platforms, count, bytes, newest }], by name
export function getPeople() { return get("/api/people"); }
// Throws ApiError with status 404 for an unknown id.
export function getPerson(id) { return get(`/api/people/${id}`); }
// accounts: [{ platform, id }] → { ok, person } or { ok: false, error }
export function createPerson(name, accounts = [], profiles) {
  return post("/api/people", profiles ? { name, accounts, profiles } : { name, accounts });
}
export function syncPerson(id) { return post(`/api/people/${id}/sync`); }
// changes: { name } and/or { notes } → { ok, person }
export function updatePerson(id, changes) { return post(`/api/people/${id}`, changes); }
// Unlinks every account; posts are never touched → { ok, unlinked }
export function deletePerson(id) { return del(`/api/people/${id}`); }
// { add: [{ platform, id }], remove: [...] } → { ok, added, removed, person }
export function linkAccounts(id, { add = [], remove = [] } = {}) {
  return post(`/api/people/${id}/accounts`, { add, remove });
}
// Folds ids[1..] (and accounts) into ids[0] → { ok, person }
export function mergePeople(ids, { name, accounts = [] } = {}) {
  return post("/api/people/merge", { ids, accounts, ...(name ? { name } : {}) });
}
// { suggestions: [{ id, score, reason, reasons: [{ reason, detail }], accounts, person }], dismissed }
export function getSuggestions() { return get("/api/people/suggestions"); }
export function dismissSuggestion(id) { return post("/api/people/suggestions/dismiss", { id }); }

/* ── Jobs (see docs/API.md "Jobs") ───────────────────────── */

// { running, queued, jobs: [job] }, newest first
export function getJobs() { return get("/api/jobs"); }
// [{ kind, label, params }]
export function getJobKinds() { return get("/api/jobs/kinds"); }
export function getJob(id) { return get(`/api/jobs/${id}`); }
// → { ok, job } or { ok: false, error }
export function startJob(kind, params = {}) { return post("/api/jobs", { kind, params }); }
// Lines numbered above `after` → { state, first, next, more, lines: [{ n, text }] }
export function getJobLog(id, after = 0, opts) { return get(`/api/jobs/${id}/log${qs({ after })}`, opts); }
// → { ok, job }; 409 { ok: false } once it has ended
export function cancelJob(id) { return post(`/api/jobs/${id}/cancel`); }
// { tool: "/abs/path" or "" for PATH } → { ok, config } or { ok: false, error }
export function saveToolPaths(tools) { return post("/api/config", { tools }); }

// Downloaders (docs/API.md "Downloaders"): found, version, latest, login, update.
export function getDownloaders()   { return get("/api/downloaders"); }
export function checkDownloaders() { return post("/api/downloaders/check"); }

/* ── Sources (see docs/API.md "Sources") ─────────────────── */

// { sources: [source], suggestions: [suggestion] }
export function getSources() { return get("/api/sources"); }
// What adding a pasted link (or, with tool "instaloader", a profile name) would make:
// { ok, tool, platform, target, folder, source, choices, session }
// or { ok: false, error } (a link that is not accepted, see docs/API.md "Link routing").
export function resolveSource(url, opts, tool) { return get(`/api/sources/resolve${qs({ url, tool })}`, opts); }
// { tool?, target (a profile link, or an Instagram name with tool "instaloader"), folder?, person?,
//   account?, options? } → { ok, source }
export function createSource(body) { return post("/api/sources", body); }
// options: { full_history?, session?, content?, media?, since?, first_posts? } → { ok, source }
export function updateSource(id, options) { return post(`/api/sources/${id}`, { options }); }
// The folder, files and posts stay → { ok }; 409 while its sync is queued or running
export function deleteSource(id) { return del(`/api/sources/${id}`); }
// → { ok, job } or { ok: false, error } (already queued, cannot be synced)
export function syncSource(id) { return post(`/api/sources/${id}/sync`); }
// Accept the new name its tool reported (the target changes, never the folder) → { ok, source }
export function acceptRename(id, to) { return post(`/api/sources/${id}/rename`, { to }); }
// Forget that suggestion → { ok, source }
export function dismissRename(id) { return del(`/api/sources/${id}/rename`); }
// → { ok, jobs, skipped, errors: [{ source, error }] }
export function syncAllSources() { return post("/api/sources/sync-all"); }
// { session?, pause? } → { ok, config } or { ok: false, error }
export function saveInstaloaderSettings(settings) { return post("/api/config", { instaloader: settings }); }
// Any of { "gallery-dl": { session }, "yt-dlp": { session }, youtube_max_seconds, routes } → { ok, config }
export function saveSettings(changes) { return post("/api/config", changes); }
// Cookies out of every yt-dlp info JSON under the media roots; apply false only counts.
// → { ok, applied, checked, files, failures, failed: [{ path, error }] } or { ok: false, error }
export function cleanInfoJsonCookies(apply) { return post("/api/yt-dlp/info-json-cookies", { apply }); }
