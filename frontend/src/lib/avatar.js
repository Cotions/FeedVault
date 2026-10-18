/* A person's picture (docs/API.md "Profile pictures"): its URLs, and the
   square crop the picture dialog sends, as the backend reads it. */

export const MAX_UPLOAD = 15 * 1024 * 1024;     // bytes, as the backend caps an upload
export const MAX_ZOOM = 5;

// The 512 px picture, or the 96 px one (cards, chips); null without one.
export function avatarUrl(name, small = false) {
  if (!name) return null;
  return `/media/avatars/${encodeURIComponent(name)}${small ? "-96" : ""}.webp`;
}

/* The crop view: a square ``view`` px wide, the picture (``w`` x ``h``, as
   the browser shows it, upright) scaled to cover it ``zoom`` times, moved
   by ``ox``, ``oy`` (its top left, in view px, 0 or less). */
export function coverScale(w, h, view, zoom) {
  return (view / Math.min(w, h)) * zoom;
}

// The offsets, kept so the picture always covers the view.
export function clampOffset({ w, h, view, zoom, ox, oy }) {
  const s = coverScale(w, h, view, zoom);
  const clamp = (v, lo) => Math.min(0, Math.max(lo, v));
  return { ox: clamp(ox, view - w * s), oy: clamp(oy, view - h * s) };
}

// The middle of the picture, at that zoom.
export function centered({ w, h, view, zoom }) {
  const s = coverScale(w, h, view, zoom);
  return { ox: (view - w * s) / 2, oy: (view - h * s) / 2 };
}

// A new zoom that keeps the point under the view's middle where it is.
export function zoomTo(state, zoom) {
  const { w, h, view } = state;
  const z = Math.min(MAX_ZOOM, Math.max(1, zoom));
  const before = coverScale(w, h, view, state.zoom), after = coverScale(w, h, view, z);
  const cx = (view / 2 - state.ox) / before, cy = (view / 2 - state.oy) / before;
  return { ...state, zoom: z, ...clampOffset({ ...state, zoom: z, ox: view / 2 - cx * after, oy: view / 2 - cy * after }) };
}

// What the backend crops: { x, y } the square's top left as fractions of
// the picture's width and height, ``size`` its side as a fraction of the
// shorter side.
export function cropOf({ w, h, view, zoom, ox, oy }) {
  const s = coverScale(w, h, view, zoom);
  const unit = v => Math.min(1, Math.max(0, Math.round(v * 10000) / 10000));
  return { x: unit(-ox / (w * s)), y: unit(-oy / (h * s)), size: Math.max(0.01, unit(1 / zoom)) };
}
