/* A person's picture (docs/API.md "Profile pictures"): its URLs, and the
   portrait crop (2:3) the picture dialog sends, as the backend reads it. */

export const MAX_UPLOAD = 15 * 1024 * 1024;     // bytes, as the backend caps an upload
export const MAX_ZOOM = 5;
export const ASPECT = 1.5;                      // a portrait's height over its width
export const TOP_BIAS = 0.25;                   // as avatars.TOP_BIAS: the default crop of a tall picture

/* One of a picture's files; null without one. "large": the 600 x 900
   portrait (the person's page); "card": 300 x 450 (Creators' cards);
   "small": the 96 px square of its upper part (round chips). */
const SUFFIX = { large: "", card: "-300", small: "-96" };
export function avatarUrl(name, kind = "large") {
  if (!name) return null;
  return `/media/avatars/${encodeURIComponent(name)}${SUFFIX[kind] ?? ""}.webp`;
}

/* The crop view: a portrait ``vw`` x ``vh`` px, the picture (``w`` x
   ``h``, as the browser shows it, upright) scaled to cover it ``zoom``
   times, moved by ``ox``, ``oy`` (its top left, in view px, 0 or less). */
export function coverScale({ w, h, vw, vh, zoom }) {
  return Math.max(vw / w, vh / h) * zoom;
}

// The offsets, kept so the picture always covers the view.
export function clampOffset(state) {
  const { w, h, vw, vh, ox, oy } = state;
  const s = coverScale(state);
  const clamp = (v, lo) => Math.min(0, Math.max(lo, v));
  return { ox: clamp(ox, vw - w * s), oy: clamp(oy, vh - h * s) };
}

// Where a picture starts, as the backend crops it by default: the middle
// across, and the upper part of a tall one (faces), at that zoom.
export function centered(state) {
  const { w, h, vw, vh } = state;
  const s = coverScale(state);
  return { ox: (vw - w * s) / 2, oy: (vh - h * s) * TOP_BIAS };
}

// A new zoom that keeps the point under the view's middle where it is.
export function zoomTo(state, zoom) {
  const { vw, vh } = state;
  const z = Math.min(MAX_ZOOM, Math.max(1, zoom));
  const before = coverScale(state), after = coverScale({ ...state, zoom: z });
  const cx = (vw / 2 - state.ox) / before, cy = (vh / 2 - state.oy) / before;
  return { ...state, zoom: z, ...clampOffset({ ...state, zoom: z, ox: vw / 2 - cx * after, oy: vh / 2 - cy * after }) };
}

// What the backend crops: { x, y } the portrait's top left as fractions of
// the picture's width and height, ``size`` its width and height as a
// fraction of the largest 2:3 box the picture holds (what zoom 1 shows).
export function cropOf(state) {
  const { w, h, zoom, ox, oy } = state;
  const s = coverScale(state);
  const unit = v => Math.min(1, Math.max(0, Math.round(v * 10000) / 10000));
  return { x: unit(-ox / (w * s)), y: unit(-oy / (h * s)), size: Math.max(0.01, unit(1 / zoom)) };
}
