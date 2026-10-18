// Checks for lib/avatar.js: the square the picture dialog crops, as the
// backend reads it (avatars.clean_crop). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { avatarUrl, centered, clampOffset, cropOf, MAX_ZOOM, zoomTo } from "../src/lib/avatar.js";

const VIEW = 240;
const wide = { w: 1000, h: 500, view: VIEW, zoom: 1 };

test("a picture's URLs, the small one for cards", () => {
  assert.equal(avatarUrl("3f9a1c0b7d2e4f6a8b1c"), "/media/avatars/3f9a1c0b7d2e4f6a8b1c.webp");
  assert.equal(avatarUrl("3f9a1c0b7d2e4f6a8b1c", true), "/media/avatars/3f9a1c0b7d2e4f6a8b1c-96.webp");
  assert.equal(avatarUrl(null), null);
  assert.equal(avatarUrl(""), null);
});

test("at first, the middle square, as large as it can be", () => {
  const s = { ...wide, ...centered(wide) };
  assert.deepEqual(cropOf(s), { x: 0.25, y: 0, size: 1 });
  const tall = { w: 300, h: 900, view: VIEW, zoom: 1 };
  assert.deepEqual(cropOf({ ...tall, ...centered(tall) }), { x: 0, y: 0.3333, size: 1 });
});

test("the picture always covers the view", () => {
  assert.deepEqual(clampOffset({ ...wide, ox: 50, oy: 30 }), { ox: 0, oy: 0 });
  assert.deepEqual(clampOffset({ ...wide, ox: -9999, oy: -10 }), { ox: VIEW - 480, oy: 0 });
  const right = { ...wide, ...clampOffset({ ...wide, ox: -9999, oy: 0 }) };
  assert.deepEqual(cropOf(right), { x: 0.5, y: 0, size: 1 });
});

test("zoom keeps the middle where it is, between 1 and the most", () => {
  const start = { ...wide, ...centered(wide) };
  const z2 = zoomTo(start, 2);
  assert.equal(z2.zoom, 2);
  assert.deepEqual(cropOf(z2), { x: 0.375, y: 0.25, size: 0.5 });
  assert.equal(zoomTo(start, 99).zoom, MAX_ZOOM);
  assert.deepEqual(zoomTo(z2, 0.2), { ...z2, zoom: 1, ...centered(wide) });
  // Never a fraction out of 0..1, nor a size under 0.01.
  for (const c of [cropOf(zoomTo(start, MAX_ZOOM)), cropOf({ ...wide, ox: 1, oy: 1 })]) {
    for (const v of Object.values(c)) assert.ok(v >= 0 && v <= 1);
    assert.ok(c.size >= 0.01);
  }
});
