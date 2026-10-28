/* A person's rating (docs/API.md "People"): 1 to 10, the half stars of
   five (7 is ★★★½☆), null for not rated; and the rating colors Settings
   keeps (docs/API.md "Rating colors"), which give a Creators card its border. */

export const MAX_RATING = 10;
export const MAX_BANDS = 10;

// The backend's defaults (config.RATING_COLORS_DEFAULT): Settings' "Reset to defaults".
export const DEFAULT_RATING_COLORS = {
  enabled: true,
  bands: [
    { min: 1, color: "#d9534f" },
    { min: 4, color: "#f0ad4e" },
    { min: 6, color: "#e6c84a" },
    { min: 8, color: "#5cb85c" },
    { min: 10, color: "#d4af37" },
  ],
};

// 7 → "3.5"; 8 → "4".
export const starsText = r => String(r / 2);

// Short, for a threshold or a filter: 7 → "3½", 1 → "½", 8 → "4".
export const starsShort = r => `${Math.floor(r / 2) || ""}${r % 2 ? "½" : ""}`;

// What a screen reader hears: "Rating: 3.5 of 5", "Not rated".
export const ratingLabel = r => (r ? `Rating: ${starsText(r)} of 5` : "Not rated");

// Each of the five stars: 1 full, 0.5 half, 0 empty.
export function starFills(r) {
  return [0, 1, 2, 3, 4].map(i => Math.max(0, Math.min(1, ((r || 0) - i * 2) / 2)));
}

/* A click on star ``index`` (0 to 4), on its left half or not: the rating
   it gives, or null when that is the rating already (a second click on
   the same half clears it). */
export function ratingFromClick(current, index, leftHalf) {
  const r = index * 2 + (leftHalf ? 1 : 2);
  return r === current ? null : r;
}

// A key on the widget: arrows go up or down half a star, Home/End to the
// ends, Delete or Backspace clears. Down stops at half a star: only Delete,
// Backspace or a click on the rating it has clear it, never a stray arrow.
// undefined when the key does nothing.
export function ratingFromKey(current, key) {
  const r = current || 0;
  if (key === "ArrowRight" || key === "ArrowUp") return Math.min(MAX_RATING, r + 1);
  if (key === "ArrowLeft" || key === "ArrowDown") return r ? Math.max(1, r - 1) : null;
  if (key === "Home") return 1;
  if (key === "End") return MAX_RATING;
  if (key === "Delete" || key === "Backspace") return null;
  return undefined;
}

/* The band a rating takes: the one with the highest min at or below it
   (with its index), or null: not rated, below the first band, or the
   colors switched off. */
export function ratingBand(rating, colors) {
  if (!rating || !colors?.enabled) return null;
  const bands = colors.bands || [];
  for (let i = bands.length - 1; i >= 0; i--) {
    if (bands[i].min <= rating) return { ...bands[i], index: i, top: i === bands.length - 1 };
  }
  return null;
}

// A card's border for its rating: a class and the CSS variable that colors it.
export function ratingStyle(rating, colors) {
  const band = ratingBand(rating, colors);
  if (!band) return { className: "", style: undefined };
  return {
    className: ` is-rated${band.top ? " is-top-band" : ""}`,
    style: { "--rating-color": band.color },
  };
}

/* What is wrong with the bands as edited, or null: Save waits until none
   is (the backend says the same, config.clean_rating_colors). */
export function bandsProblem(bands) {
  if (!bands.length) return "Keep at least one band";
  if (bands.length > MAX_BANDS) return `At most ${MAX_BANDS} bands`;
  for (let i = 0; i < bands.length; i++) {
    const b = bands[i];
    if (!/^#[0-9a-fA-F]{6}$/.test(b.color)) return "Each color is #rrggbb";
    if (!Number.isInteger(b.min) || b.min < 1 || b.min > MAX_RATING) return "Each band starts at ½ to 5 stars";
    if (i > 0 && b.min <= bands[i - 1].min) return "Each band starts above the one before it";
  }
  return null;
}

// A band added at the end: half a star above the last, or the last moved
// down to make room when it starts at 5 stars. null when none fits.
export function withBand(bands) {
  if (bands.length >= MAX_BANDS) return null;
  const last = bands[bands.length - 1];
  if (!last) return [{ min: 1, color: "#888888" }];
  if (last.min < MAX_RATING) return [...bands, { min: last.min + 1, color: last.color }];
  return null;
}

/* Sort and filter on Creators. Sort "rating": the highest first, the
   unrated last, by name within one rating. Filter, as Creators' others:
   null (any), "" (not rated), or "1" to "10" (rated at least that). */
export const RATING_FILTERS = ["", ...Array.from({ length: MAX_RATING }, (_, i) => String(i + 1))];
export const ratingFilter = v => (RATING_FILTERS.includes(v) ? v : null);
export function ratingMatch(p, f) {
  if (f == null) return true;
  if (f === "") return !p.rating;
  return (p.rating || 0) >= Number(f);
}
export function byRating(a, b) {
  return (b.rating || 0) - (a.rating || 0) || a.name.localeCompare(b.name);
}
