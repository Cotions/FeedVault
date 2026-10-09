// Checks for lib/selection.js: shift ranges and stale ids of useSelection (#165). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { pruned, toggled } from "../src/lib/selection.js";

const list = ids => ids.map(id => ({ id }));
const sorted = set => [...set].sort((a, b) => a - b);

test("a click toggles one item", () => {
  const items = list([1, 2, 3]);
  assert.deepEqual(sorted(toggled(new Set(), items, 2, false, null)), [2]);
  assert.deepEqual(sorted(toggled(new Set([2]), items, 2, false, null)), []);
});

test("a shift-click adds the range from the anchor, either way", () => {
  const items = list([1, 2, 3, 4, 5]);
  assert.deepEqual(sorted(toggled(new Set([2]), items, 4, true, 2)), [2, 3, 4]);
  assert.deepEqual(sorted(toggled(new Set([4]), items, 2, true, 4)), [2, 3, 4]);
});

test("the anchor is an id: rows added or removed above it do not shift the range", () => {
  // Anchor was id 3 at index 2; then a row came in on top and one left.
  const items = list([9, 1, 3, 4, 5].filter(id => id !== 1));   // [9, 3, 4, 5]
  assert.deepEqual(sorted(toggled(new Set([3]), items, 5, true, 3)), [3, 4, 5]);
});

test("a shift-click whose anchor left the list toggles just the item", () => {
  const items = list([1, 2, 3]);
  assert.deepEqual(sorted(toggled(new Set(), items, 3, true, 7)), [3]);
  assert.deepEqual(sorted(toggled(new Set(), items, 3, true, null)), [3]);
});

test("a click on an id not in the list changes nothing", () => {
  assert.deepEqual(sorted(toggled(new Set([1]), list([1, 2]), 8, false, null)), [1]);
});

test("ids that left the list are pruned; null when nothing to drop", () => {
  assert.equal(pruned(new Set(), list([1])), null);
  assert.equal(pruned(new Set([1, 2]), list([1, 2, 3])), null);
  assert.deepEqual(sorted(pruned(new Set([1, 2, 4]), list([2, 3]))), [2]);
  assert.deepEqual(sorted(pruned(new Set([1]), [])), []);
});
