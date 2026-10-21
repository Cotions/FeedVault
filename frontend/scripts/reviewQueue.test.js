// Checks for lib/reviewQueue.js, Review's queue. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { initial, listPlace, reducer, undoLabel, undoneToast } from "../src/lib/reviewQueue.js";

const post = (id, cover = { kind: "image", url: `/media/${id}/thumb` }) => ({ id, platform: "x", post_id: String(id), cover });
const load = posts => reducer(initial, { type: "loaded", posts, total: posts.length });

test("trash then undo: the post is current again, and undecided", () => {
  let s = load([post(1), post(2), post(3)]);
  s = reducer(s, { type: "decide", id: 1, decision: "trash" });
  assert.equal(s.pos, 1);
  assert.equal(s.left, 2);
  s = reducer(s, { type: "undecide", id: 1, index: 0 });
  assert.equal(s.pos, 0);
  assert.equal(s.left, 3);
  assert.deepEqual(s.status, {});
});

test("#90: a restored post's old cover is dropped, the others kept", () => {
  let s = load([post(1), post(2)]);
  s = reducer(s, { type: "decide", id: 1, decision: "trash" });
  s = reducer(s, { type: "undecide", id: 1, index: 0 });
  s = reducer(s, { type: "restored", id: 1 });
  assert.equal(s.queue[0].cover, null);
  assert.equal(s.queue[0].id, 1);
  assert.deepEqual(s.queue[1].cover, { kind: "image", url: "/media/2/thumb" });
  assert.equal(s.pos, 0);
});

test("restored: a post with no cover, or not held, leaves the state as it is", () => {
  const s = load([post(1, null), post(2)]);
  assert.equal(reducer(s, { type: "restored", id: 1 }), s);
  assert.equal(reducer(s, { type: "restored", id: 9 }), s);
  assert.equal(reducer(s, { type: "restored", id: 9, cover: { kind: "image", url: "/media/90/thumb" } }), s);
});

test("restored with the post read again: its new cover replaces the stale one", () => {
  let s = load([post(1), post(2, null)]);
  const fresh = { kind: "image", url: "/media/41/thumb" };
  s = reducer(s, { type: "restored", id: 1, cover: fresh });
  assert.deepEqual(s.queue[0].cover, fresh);
  // A post that had none gets one too.
  s = reducer(s, { type: "restored", id: 2, cover: { kind: "video", url: "/media/42/thumb", poster: true } });
  assert.equal(s.queue[1].cover.url, "/media/42/thumb");
});

// The undo stack's entries, as Review.jsx pushes them.
const handled = (id, handle = "someone", decision = null) => ({ ...post(id), post_id: `P${id}`, author: { id: "1", handle }, decision });
const answer = (posts, files, more = {}) => ({ ok: true, posts, files, errors: [], moved: [], recreated: [], decision_unknown: [], ...more });

test("undoLabel: what Z undoes next", () => {
  assert.equal(undoLabel({ type: "keep", post: handled(1) }), "kept @someone");
  assert.equal(undoLabel({ type: "trash", post: handled(1) }), "trashed @someone (post)");
  assert.equal(undoLabel({ type: "item", post: handled(1), itemIndex: 1, postGone: false }), "trashed item 2 of @someone");
  assert.equal(undoLabel({ type: "item", post: handled(1), itemIndex: 0, postGone: true }), "trashed @someone's last item (post)");
  assert.equal(undoLabel({ type: "keep", post: { ...handled(1), author: null } }), "kept @unknown");
});

test("undoneToast: a keep undone", () => {
  assert.deepEqual(undoneToast({ type: "keep", post: handled(1) }, null),
    { kind: "ok", text: "Keep undone: @someone's post is undecided again." });
  // A re-review's post kept before: undoing its K changes nothing on the server.
  assert.equal(undoneToast({ type: "keep", post: handled(1, "someone", "keep"), already: true }, null).text,
    "Back on @someone's post: it stays kept, as before this session.");
});

test("undoneToast: a trash undone says what came back and what is next", () => {
  const e = { type: "trash", post: handled(1) };
  assert.deepEqual(undoneToast(e, answer([1], 3)),
    { kind: "ok", text: "Restored @someone's post (3 files), back on it. Press K to keep it.", link: undefined });
  // No keyboard (a phone): no K to name.
  assert.equal(undoneToast(e, answer([1], 3), { keys: false }).text, "Restored @someone's post (3 files), back on it.");
  // A re-review's kept post comes back kept.
  assert.equal(undoneToast({ type: "trash", post: handled(1, "someone", "keep") }, answer([1], 1)).text,
    "Restored @someone's post (1 file), back on it. It is kept, as before.");
  // An item, the post still there; and the last item, which took the post.
  assert.equal(undoneToast({ type: "item", post: handled(1), itemIndex: 2, postGone: false }, answer([1], 2)).text,
    "Restored item 3 of @someone's post (2 files), back on it.");
  assert.equal(undoneToast({ type: "item", post: handled(1), itemIndex: 0, postGone: true }, answer([1], 2)).text,
    "Restored @someone's post (2 files), back on it. Press K to keep it.");
});

test("undoneToast: files that stayed in the trash and folder notes are told", () => {
  const t = undoneToast({ type: "trash", post: handled(1) },
    answer([1], 2, { errors: [{ path: "/m/a/x.jpg", error: "a file is in the way" }], recreated: [{ post: 1, folder: "someone" }] }));
  assert.equal(t.kind, "err");
  assert.equal(t.link.to, "/trash");
  assert.equal(t.text, "Restored @someone's post (2 files), back on it. Press K to keep it. "
    + "1 file could not be restored: a file is in the way (/m/a/x.jpg). It stays in the trash. "
    + "Their folder was gone and was made again (someone).");
});

// listPlace: where the next page starts, in each mode.
const ids = n => Array.from({ length: n }, (_, i) => i + 1);
const tagged = (...out) => p => out.includes(p.id);

test("listPlace, normal: the undecided posts held, less those tagged out of the scope", () => {
  const queue = ids(5).map(id => post(id));
  assert.deepEqual(listPlace(queue, {}), { offset: 5, decided: 0 });
  assert.deepEqual(listPlace(queue, { 1: "keep", 2: "trash" }), { offset: 3, decided: 0 });
  assert.deepEqual(listPlace(queue, { 1: "keep", 2: "trash" }, { outOfScope: tagged(4) }), { offset: 2, decided: 0 });
  // Decided and tagged out: off the list once, not twice.
  assert.deepEqual(listPlace(queue, { 1: "keep" }, { outOfScope: tagged(1) }), { offset: 4, decided: 0 });
});

test("listPlace, again: every post held but the trashed and those tagged out; the kept ones are decided", () => {
  const queue = ids(5).map(id => post(id));
  const again = { again: true };
  assert.deepEqual(listPlace(queue, {}, again), { offset: 5, decided: 0 });
  assert.deepEqual(listPlace(queue, { 1: "keep", 2: "trash" }, again), { offset: 4, decided: 1 });
  assert.deepEqual(listPlace(queue, { 1: "keep", 2: "trash" }, { ...again, outOfScope: tagged(4) }), { offset: 3, decided: 1 });
  // Kept, then tagged out of the scope: off the list, so not counted as decided on it.
  assert.deepEqual(listPlace(queue, { 1: "keep", 3: "keep" }, { ...again, outOfScope: tagged(1) }), { offset: 4, decided: 1 });
});

// A fake server list: after a keep, a trash and a tag that takes a post out
// of the scope, the next page starts at the first post not held yet, with
// none skipped and none fetched twice.
test("listPlace: the next page follows the posts held, in both modes", () => {
  for (const again of [false, true]) {
    const keptBefore = new Set([2]);           // only a re-review lists it
    const list = status => ids(12).filter(id => (again ? status[id] !== "trash" : !status[id] && !keptBefore.has(id)));
    const first = list({}).slice(0, 5);
    let s = reducer(initial, { type: "loaded", posts: first.map(id => post(id)), total: list({}).length });
    const [a, b, c] = first;
    s = reducer(s, { type: "decide", id: a, decision: "keep" });
    s = reducer(s, { type: "decide", id: b, decision: "trash" });
    const server = list(s.status).filter(id => id !== c);
    const { offset, decided } = listPlace(s.queue, s.status, { again, outOfScope: tagged(c) });
    const last = first[first.length - 1];
    assert.deepEqual(server.slice(offset, offset + 3), server.filter(id => id > last).slice(0, 3), `again: ${again}`);
    // What is left in this pass: the server's total less the posts kept this session.
    assert.equal(server.length - decided, s.queue.filter(p => !s.status[p.id] && p.id !== c).length + server.filter(id => id > last).length);
  }
});
