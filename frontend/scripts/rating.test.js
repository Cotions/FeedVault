// Checks for lib/rating.js: a person's rating as the person page sets it
// and Creators shows, sorts and filters it, and the rating colors' bands.
// Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  bandsProblem, byRating, DEFAULT_RATING_COLORS, ratingBand, ratingFilter, ratingFromClick, ratingFromKey, ratingLabel,
  ratingMatch, ratingStyle, starFills, starsShort, starsText, withBand,
} from "../src/lib/rating.js";
import { attributeChoices, attributesMatch, NO_FILTERS } from "../src/lib/people.js";

test("a rating reads as stars out of five, in half steps", () => {
  assert.equal(ratingLabel(7), "Rating: 3.5 of 5");
  assert.equal(ratingLabel(10), "Rating: 5 of 5");
  assert.equal(ratingLabel(null), "Not rated");
  assert.equal(starsText(1), "0.5");
  assert.deepEqual([1, 7, 8, 10].map(starsShort), ["½", "3½", "4", "5"]);
  assert.deepEqual(starFills(7), [1, 1, 1, 0.5, 0]);
  assert.deepEqual(starFills(1), [0.5, 0, 0, 0, 0]);
  assert.deepEqual(starFills(null), [0, 0, 0, 0, 0]);
  assert.deepEqual(starFills(10), [1, 1, 1, 1, 1]);
});

test("a click: the left half of a star is half a star, the same rating again clears it", () => {
  assert.equal(ratingFromClick(null, 3, true), 7);
  assert.equal(ratingFromClick(null, 3, false), 8);
  assert.equal(ratingFromClick(null, 0, true), 1);
  assert.equal(ratingFromClick(7, 3, true), null);
  assert.equal(ratingFromClick(7, 3, false), 8);
});

test("keys: arrows go half a star, Home and End to the ends, Delete clears", () => {
  assert.equal(ratingFromKey(null, "ArrowRight"), 1);
  assert.equal(ratingFromKey(7, "ArrowUp"), 8);
  assert.equal(ratingFromKey(10, "ArrowRight"), 10);
  assert.equal(ratingFromKey(7, "ArrowLeft"), 6);
  assert.equal(ratingFromKey(2, "ArrowDown"), 1);
  assert.equal(ratingFromKey(1, "ArrowDown"), 1);                 // at half a star: stays, never cleared
  assert.equal(ratingFromKey(1, "ArrowLeft"), 1);
  assert.equal(ratingFromKey(null, "ArrowLeft"), null);           // not rated: stays not rated
  assert.equal(ratingFromKey(5, "Home"), 1);
  assert.equal(ratingFromKey(5, "End"), 10);
  assert.equal(ratingFromKey(5, "Delete"), null);
  assert.equal(ratingFromKey(5, "Backspace"), null);
  assert.equal(ratingFromKey(5, "a"), undefined);
});

test("a rating takes the band with the highest start at or below it", () => {
  const colors = { enabled: true, bands: [{ min: 4, color: "#444444" }, { min: 6, color: "#666666" }] };
  assert.equal(ratingBand(5, colors).min, 4);
  assert.equal(ratingBand(6, colors).min, 6);
  assert.equal(ratingBand(10, colors).top, true);
  assert.equal(ratingBand(5, colors).top, false);
  assert.equal(ratingBand(3, colors), null);                          // below the first band
  assert.equal(ratingBand(null, colors), null);                       // unrated
  assert.equal(ratingBand(8, { ...colors, enabled: false }), null);   // switched off
  assert.equal(ratingBand(8, undefined), null);                       // not loaded yet
  const d = DEFAULT_RATING_COLORS;
  assert.deepEqual([1, 3, 4, 7, 9, 10].map(r => ratingBand(r, d).color),
    ["#d9534f", "#d9534f", "#f0ad4e", "#e6c84a", "#5cb85c", "#d4af37"]);
});

test("a card's border: a class and the color as a CSS variable, the top band marked", () => {
  assert.deepEqual(ratingStyle(7, DEFAULT_RATING_COLORS), { className: " is-rated", style: { "--rating-color": "#e6c84a" } });
  assert.deepEqual(ratingStyle(10, DEFAULT_RATING_COLORS),
    { className: " is-rated is-top-band", style: { "--rating-color": "#d4af37" } });
  assert.deepEqual(ratingStyle(null, DEFAULT_RATING_COLORS), { className: "", style: undefined });
});

test("bands as edited: what Save waits for", () => {
  assert.equal(bandsProblem(DEFAULT_RATING_COLORS.bands), null);
  assert.equal(bandsProblem([{ min: 3, color: "#123abc" }]), null);   // the first may start above ½
  assert.match(bandsProblem([]), /at least one/);
  assert.match(bandsProblem([{ min: 1, color: "red" }]), /#rrggbb/);
  assert.match(bandsProblem([{ min: 4, color: "#111111" }, { min: 4, color: "#222222" }]), /above/);
  assert.match(bandsProblem([{ min: 11, color: "#111111" }]), /½ to 5/);
  assert.match(bandsProblem(Array.from({ length: 11 }, (_, i) => ({ min: i + 1, color: "#111111" }))), /At most/);
});

test("a band added goes half a star above the last, none past 5 stars or 10 bands", () => {
  assert.deepEqual(withBand([{ min: 4, color: "#111111" }]), [{ min: 4, color: "#111111" }, { min: 5, color: "#111111" }]);
  assert.equal(withBand(DEFAULT_RATING_COLORS.bands), null);          // the last starts at 5 stars
  assert.equal(withBand([]).length, 1);
});

test("sort by rating: the highest first, the unrated last, by name within one", () => {
  const people = [{ name: "b", rating: null }, { name: "c", rating: 7 }, { name: "a", rating: 7 }, { name: "d", rating: 10 }, { name: "e" }];
  assert.deepEqual([...people].sort(byRating).map(p => p.name), ["d", "a", "c", "b", "e"]);
});

test("the rating filter: rated at least n, or not rated; an account has no rating", () => {
  assert.equal(ratingFilter("7"), "7");
  assert.equal(ratingFilter(""), "");                                // ?rating= : not rated
  for (const bad of [null, "none", "0", "11", "3.5", "x"]) assert.equal(ratingFilter(bad), null);
  assert.equal(ratingMatch({ rating: 7 }, "7"), true);
  assert.equal(ratingMatch({ rating: 6 }, "7"), false);
  assert.equal(ratingMatch({ rating: null }, "1"), false);
  assert.equal(ratingMatch({ rating: null }, ""), true);
  assert.equal(ratingMatch({ rating: 3 }, ""), false);
  assert.equal(ratingMatch({ rating: 3 }, null), true);
  assert.equal(attributesMatch({ rating: 8, labels: [] }, { ...NO_FILTERS, rating: "6" }), true);
  assert.equal(attributesMatch({ rating: 4, labels: [] }, { ...NO_FILTERS, rating: "6" }), false);
  const c = attributeChoices([{ rating: 8 }, { rating: 3 }, { rating: null }]);
  assert.equal(c.none.rating, 1);
  assert.equal(c.ratedAtLeast[0], 2);                                 // ½ star or more
  assert.equal(c.ratedAtLeast[3], 1);                                 // 2 stars or more
  assert.equal(c.ratedAtLeast[8], 0);                                 // 4½ or more
});
