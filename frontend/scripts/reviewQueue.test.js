// Checks for lib/reviewQueue.js, Review's queue. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { initial, listPlace, reducer } from "../src/lib/reviewQueue.js";

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
