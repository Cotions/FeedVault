// Checks for lib/reviewQueue.js, Review's queue. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { initial, reducer } from "../src/lib/reviewQueue.js";

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
