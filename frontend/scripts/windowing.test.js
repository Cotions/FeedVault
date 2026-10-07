// Checks for lib/windowing.js's arithmetic: which lines a windowed list
// renders for a scroll position, and the spacers standing for the rest.
// Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { lineAt, lineOffsets, segments, visibleLines } from "../src/lib/windowing.js";

const even = (n, pitch) => lineOffsets(Array(n).fill(pitch));

test("offsets add up the lines' pitches", () => {
  assert.deepEqual(lineOffsets([10, 20, 5]), [0, 10, 30, 35]);
  assert.deepEqual(lineOffsets([]), [0]);
});

test("the line at a position is the last one starting at or above it", () => {
  const o = lineOffsets([10, 20, 5]);
  assert.equal(lineAt(o, 0), 0);
  assert.equal(lineAt(o, 9.9), 0);
  assert.equal(lineAt(o, 10), 1);
  assert.equal(lineAt(o, 34), 2);
  assert.equal(lineAt(o, 1000), 2);              // past the end: the last
  assert.equal(lineAt([0], 5), 0);               // no lines
});

test("10,000 rows of 40 px: a 900 px viewport and 800 px each side render 63 lines, wherever it is", () => {
  const o = even(10_000, 40);
  assert.deepEqual(visibleLines(o, 0, 900 + 800), [0, 43]);
  const [a, b] = visibleLines(o, 200_000 - 800, 200_000 + 900 + 800);
  assert.deepEqual([a, b], [4980, 5043]);
  assert.deepEqual(visibleLines(o, 399_000, 401_000), [9975, 10_000]);   // the end
  assert.deepEqual(visibleLines(o, -5000, -10), [0, 1]);                   // above the list: its first line
  assert.deepEqual(visibleLines(lineOffsets([]), 0, 900), [0, 0]);
});

test("spacers stand for the lines not rendered, gap included", () => {
  const o = even(100, 40);
  assert.deepEqual(segments(100, [10, 13], [], o), [
    { gap: 400, key: "gap:0" }, { line: 10 }, { line: 11 }, { line: 12 }, { gap: 87 * 40, key: "gap:13" },
  ]);
  // A flex or grid gap of 6 px sits between the spacer and the next line:
  // the spacer leaves it out, so every line lands where it would.
  const g = even(100, 46);
  const s = segments(100, [10, 12], [], g, 6);
  assert.equal(s[0].gap + 6, g[10]);
  assert.equal(s.at(-1).gap + 6, g[100] - g[12]);
  // Nothing to stand for: no spacer.
  assert.deepEqual(segments(3, [0, 3], [], even(3, 40)), [{ line: 0 }, { line: 1 }, { line: 2 }]);
  assert.deepEqual(segments(0, [0, 0], [], [0]), []);
});

test("a pinned line (focus, an edit) is rendered between spacers, wherever the window is", () => {
  const o = even(100, 40);
  assert.deepEqual(segments(100, [50, 52], [3, 51, 200, -1], o), [
    { gap: 120, key: "gap:0" }, { line: 3 }, { gap: 46 * 40, key: "gap:4" }, { line: 50 }, { line: 51 },
    { gap: 48 * 40, key: "gap:52" },
  ]);
  const total = s => s.reduce((h, x) => h + (x.gap ?? 40), 0);
  assert.equal(total(segments(100, [50, 52], [3], o)), 100 * 40);
});
