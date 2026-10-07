import { useLayoutEffect, useRef } from "react";

// A left or right swipe of one finger: at least MIN_DX across, mostly
// sideways, in under MAX_MS. index.css gives what takes it touch-action:
// pan-y, so an up or down move still scrolls the page (and the browser
// cancels the pointer, which drops the swipe) and a pinch still zooms.
export const MIN_DX = 48;
const MAX_MS = 900;
const SIDEWAYS = 2;                           // |dx| at least twice |dy|
// The band at a video's bottom where its own controls (the timeline) are:
// a drag there scrubs, it does not page.
export const VIDEO_CONTROLS = 64;

// Spread the handlers it returns on the element that takes swipes;
// ``onSwipe(step)`` gets 1 for a swipe to the left (the next item), -1
// for one to the right. A mouse never swipes, nor does a touch that starts
// on a button or a link (the arrows and dots page on their own), or one
// that becomes a second finger.
export function useSwipe(onSwipe) {
  const start = useRef(null);
  const fingers = useRef(new Set());
  const latest = useRef(onSwipe);
  useLayoutEffect(() => { latest.current = onSwipe; });

  function onPointerDown(e) {
    if (e.pointerType === "mouse") return;
    fingers.current.add(e.pointerId);
    if (fingers.current.size > 1) { start.current = null; return; }
    const t = e.target;
    if (t.closest?.("button, a[href], input, select, textarea")) return;
    if (t.tagName === "VIDEO" && e.clientY > t.getBoundingClientRect().bottom - VIDEO_CONTROLS) return;
    start.current = { id: e.pointerId, x: e.clientX, y: e.clientY, at: e.timeStamp };
  }
  function end(e, cancelled) {
    fingers.current.delete(e.pointerId);
    const s = start.current;
    if (!s || s.id !== e.pointerId) return;
    start.current = null;
    if (cancelled) return;
    const dx = e.clientX - s.x, dy = e.clientY - s.y;
    if (Math.abs(dx) >= MIN_DX && Math.abs(dx) >= SIDEWAYS * Math.abs(dy) && e.timeStamp - s.at <= MAX_MS) {
      latest.current(dx < 0 ? 1 : -1);
    }
  }
  return {
    onPointerDown,
    onPointerUp: e => end(e, false),
    onPointerCancel: e => end(e, true),
  };
}
