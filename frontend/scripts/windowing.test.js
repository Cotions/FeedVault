// Checks for lib/windowing.js's arithmetic: which lines a windowed list
// renders for a scroll position, and the spacers standing for the rest.
// Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { lineAt, lineOffsets, rowLines, segments, visibleLines } from "../src/lib/windowing.js";

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

// The rows of each line, as the hook renders them.
const laidOut = m => Array.from({ length: m.lines }, (_, l) => { const [a, b] = m.rowsOf(l); return range(a, b); });
const range = (a, b) => Array.from({ length: b - a }, (_, i) => a + i);

test("without full rows, a grid's lines are plain arithmetic, as before", () => {
  const m = rowLines(10, 3);
  assert.equal(m.lines, 4);
  assert.deepEqual(laidOut(m), [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]);
  assert.deepEqual(range(0, 10).map(m.lineOf), [0, 0, 0, 1, 1, 1, 2, 2, 2, 3]);
  // One column (a list, a table): a row a line.
  const list = rowLines(4, 1);
  assert.deepEqual(laidOut(list), [[0], [1], [2], [3]]);
  assert.equal(rowLines(0, 3).lines, 0);
  assert.equal(rowLines(5, 0).lines, 5);            // columns not read yet: one
});

test("a full row is a line of its own; the cards between fill lines from the first column", () => {
  // H c c c c H c H H c c c (H: a heading), three columns.
  const heads = new Set([0, 5, 7, 8]);
  const m = rowLines(12, 3, i => heads.has(i));
  assert.deepEqual(laidOut(m), [[0], [1, 2, 3], [4], [5], [6], [7], [8], [9, 10, 11]]);
  assert.deepEqual(range(0, 12).map(m.lineOf), [0, 1, 1, 1, 2, 3, 4, 5, 6, 7, 7, 7]);
  // Every row is in one line, once, in order.
  assert.deepEqual(laidOut(m).flat(), range(0, 12));
  // No full row: the same lines as without the option.
  assert.deepEqual(laidOut(rowLines(10, 3, () => false)), laidOut(rowLines(10, 3)));
  // All full: a row a line, whatever the columns.
  assert.deepEqual(laidOut(rowLines(3, 4, () => true)), [[0], [1], [2]]);
  assert.equal(rowLines(0, 3, () => true).lines, 0);
});

test("a row opened over the whole line (an edit) moves the cards after it, not those before", () => {
  const closed = rowLines(9, 3, () => false);
  const open = rowLines(9, 3, i => i === 4);
  assert.deepEqual(laidOut(closed), [[0, 1, 2], [3, 4, 5], [6, 7, 8]]);
  assert.deepEqual(laidOut(open), [[0, 1, 2], [3], [4], [5, 6, 7], [8]]);
});

test("the columns changing lays the same rows out again", () => {
  const heads = i => i === 0 || i === 4;
  assert.deepEqual(laidOut(rowLines(8, 2, heads)), [[0], [1, 2], [3], [4], [5, 6], [7]]);
  assert.deepEqual(laidOut(rowLines(8, 4, heads)), [[0], [1, 2, 3], [4], [5, 6, 7]]);
  assert.deepEqual(laidOut(rowLines(8, 1, heads)), range(0, 8).map(i => [i]));
});

test("pins and scrollTo go by the row's line: a row behind a full row, a pinned edit far away", () => {
  // 1,000 rows, a heading every 10th (0, 10, 20…), three columns: each
  // group is a heading's line and three lines of cards (3 + 3 + 3).
  const m = rowLines(1000, 3, i => i % 10 === 0);
  assert.equal(m.lines, 400);
  assert.equal(m.lineOf(500), 200);                  // a heading
  assert.equal(m.lineOf(501), 201);                  // the first card behind it: the next line, not the heading's
  assert.deepEqual(m.rowsOf(201), [501, 504]);
  assert.equal(m.lineOf(509), 203);
  assert.deepEqual(m.rowsOf(203), [507, 510]);
  // The line scrollTo(501) centres starts where the 201 lines before it end.
  const o = lineOffsets(Array.from({ length: m.lines }, (_, l) => (l % 4 ? 90 : 30)));
  assert.equal(o[m.lineOf(501)], 50 * (30 + 3 * 90) + 30);
  // A pinned row (an edit at row 997) is rendered with its line, between spacers.
  const s = segments(m.lines, [0, 2], [m.lineOf(997)], o);
  assert.deepEqual(s.map(x => x.line ?? "gap"), [0, 1, "gap", 399]);
  assert.deepEqual(m.rowsOf(399), [997, 1000]);
  // A row past the end (a list that just shrank) is on the last line, never past it.
  assert.equal(m.lineOf(5000), 399);
});
