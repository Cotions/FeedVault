// Checks for lib/avatar.js: the portrait the picture dialog crops, as the
// backend reads it (avatars.clean_crop). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { ASPECT, avatarUrl, centered, clampOffset, coverScale, cropOf, MAX_ZOOM, zoomTo } from "../src/lib/avatar.js";

const VW = 200, VH = VW * ASPECT;
const wide = { w: 1000, h: 500, vw: VW, vh: VH, zoom: 1 };
const tall = { w: 400, h: 1000, vw: VW, vh: VH, zoom: 1 };

test("a picture's URLs: the portrait, the card's, the chips' square", () => {
  assert.equal(avatarUrl("3f9a1c0b7d2e4f6a8b1c"), "/media/avatars/3f9a1c0b7d2e4f6a8b1c.webp");
  assert.equal(avatarUrl("3f9a1c0b7d2e4f6a8b1c", "card"), "/media/avatars/3f9a1c0b7d2e4f6a8b1c-300.webp");
  assert.equal(avatarUrl("3f9a1c0b7d2e4f6a8b1c", "small"), "/media/avatars/3f9a1c0b7d2e4f6a8b1c-96.webp");
  assert.equal(avatarUrl(null), null);
  assert.equal(avatarUrl(""), null);
});

test("the view is 2:3, the picture covers it", () => {
  assert.equal(VH, 300);
  assert.equal(coverScale(wide), 300 / 500);          // the height fills it, the sides are cut
  assert.equal(coverScale(tall), 200 / 400);          // the width fills it
  assert.equal(coverScale({ ...wide, zoom: 2 }), 2 * 300 / 500);
});

test("at first, as the backend's default: the middle across, the upper part of a tall one", () => {
  // Wide: 600 x 300 shown, 400 px cut, 200 each side.
  assert.deepEqual(centered(wide), { ox: -200, oy: 0 });
  assert.deepEqual(cropOf({ ...wide, ...centered(wide) }), { x: 0.3333, y: 0, size: 1 });
  // Tall: 200 x 500 shown, 200 px cut, a quarter of it above.
  assert.deepEqual(centered(tall), { ox: 0, oy: -50 });
  assert.deepEqual(cropOf({ ...tall, ...centered(tall) }), { x: 0, y: 0.1, size: 1 });
  // A square (instaloader's profile picture): its sides cut off, its top kept.
  const square = { w: 320, h: 320, vw: VW, vh: VH, zoom: 1 };
  assert.deepEqual(cropOf({ ...square, ...centered(square) }), { x: 0.1667, y: 0, size: 1 });
});

test("the picture always covers the view", () => {
  assert.deepEqual(clampOffset({ ...wide, ox: 50, oy: 30 }), { ox: 0, oy: 0 });
  assert.deepEqual(clampOffset({ ...wide, ox: -9999, oy: -10 }), { ox: VW - 600, oy: 0 });
  const right = { ...wide, ...clampOffset({ ...wide, ox: -9999, oy: 0 }) };
  assert.deepEqual(cropOf(right), { x: 0.6667, y: 0, size: 1 });
  const bottom = { ...tall, ...clampOffset({ ...tall, ox: 0, oy: -9999 }) };
  assert.deepEqual(cropOf(bottom), { x: 0, y: 0.4, size: 1 });
});

test("zoom keeps the middle where it is, between 1 and the most", () => {
  const start = { ...wide, ...centered(wide) };
  const z2 = zoomTo(start, 2);
  assert.equal(z2.zoom, 2);
  // Half the portrait (167 x 250 of the picture), around the same middle (500, 250).
  assert.deepEqual(cropOf(z2), { x: 0.4167, y: 0.25, size: 0.5 });
  assert.equal(zoomTo(start, 99).zoom, MAX_ZOOM);
  assert.deepEqual(zoomTo(z2, 0.2), { ...z2, zoom: 1, ...centered(wide) });
  // Never a fraction out of 0..1, nor a size under 0.01.
  for (const c of [cropOf(zoomTo(start, MAX_ZOOM)), cropOf({ ...wide, ox: 1, oy: 1 }), cropOf(zoomTo({ ...tall, ...centered(tall) }, 3))]) {
    for (const v of Object.values(c)) assert.ok(v >= 0 && v <= 1);
    assert.ok(c.size >= 0.01);
  }
});
