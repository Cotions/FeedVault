import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

// The phone layout: a narrow window, or a short one on a touch screen (a
// phone on its side, #159). The same query as index.css's phone rules.
export const PHONE = "(max-width: 640px), (max-height: 500px) and (pointer: coarse)";

// What a focus trap cycles through.
export const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Keeps Tab inside ``box`` (a dialog): from its last control to its first,
// from its first back to its last with Shift, and from outside it (focus
// lost to <body>) into it. For a keydown handler.
export function trapTab(e, box) {
  if (e.key !== "Tab" || !box) return;
  const items = [...box.querySelectorAll(FOCUSABLE)];
  if (items.length === 0) { e.preventDefault(); return; }
  const first = items[0], last = items[items.length - 1];
  const a = document.activeElement;
  if (!box.contains(a)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
  else if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
}

// Gives focus back to what had it before a dialog opened, without a
// scroll: the page is still where it was when the dialog opened. Scrolled
// into view, a button in the sticky selection bar can never get there (it
// sits in the room html's scroll-padding keeps for the bar), and each
// close moved the page down by that room (#119).
export function restoreFocus(el) {
  if (el && el.focus && document.contains(el)) el.focus({ preventScroll: true });
}

// Focus that went nowhere: on <body> (or none), as when the element that
// had it was disabled or removed (#136).
export function focusLost() {
  const a = document.activeElement;
  return !a || a === document.body || a === document.documentElement;
}

// Keeps focus on a reorder arrow after its item moved (#136): React moves
// a keyed item's node to its new place, and a node taken out of the page
// loses focus. Put ``data-key`` on each item and ``data-move`` on its
// arrows, ``listRef`` on the list, and call follow(key, move) when an
// arrow moves its item: after the next render, focus goes back to that
// arrow, unless it went somewhere else meanwhile.
export function useFollowFocus() {
  const listRef = useRef(null);
  const pending = useRef(null);
  useLayoutEffect(() => {
    const p = pending.current;
    const list = listRef.current;
    if (!p || !list) return;
    pending.current = null;
    if (!focusLost() && !list.contains(document.activeElement)) return;
    restoreFocus(list.querySelector(`[data-key="${CSS.escape(String(p.key))}"] [data-move="${p.move}"]`));
  });
  const follow = useCallback((key, move) => { pending.current = { key, move }; }, []);
  return { listRef, follow };
}

// Keeps focus off <body> when the control that has it goes away (a row
// that ends, a button that is no longer offered): focus then goes to
// ``fallback`` (a ref to an element with tabIndex -1 that stays, or a
// function that returns the element to use at that time). Spread
// the handlers it returns on the part of the page to watch. After every
// render, and after a dialog opened from there gave focus back (its
// effect's cleanup runs before this). As Jobs did first (#136, #139).
export function useKeepFocus(fallback) {
  const last = useRef(null);                   // what last had focus in the part
  useEffect(() => {
    const el = last.current;
    if (el && !document.contains(el) && focusLost()) {
      restoreFocus(typeof fallback === "function" ? fallback() : fallback.current);
    }
  });
  const onFocus = useCallback(e => { last.current = e.target; }, []);
  // Into a dialog it is still the part's: the dialog gives it back.
  const onBlur = useCallback(e => {
    if (e.relatedTarget && !e.relatedTarget.closest(".modal-overlay")) last.current = null;
  }, []);
  return { onFocus, onBlur };
}

// Moves focus onto <main> (made focusable for it, without a ring) after
// the app went to another page from something that then went away, as a
// link in the Notifications panel (#139): the next Tab starts at the new
// page's top, not at the document's start.
export function focusMain() {
  const main = document.querySelector("main");
  if (!main) return;
  if (!main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
  main.focus({ preventScroll: true });
}

// Keeps the toasts above the controls of ``ref`` (the selection bar,
// Review's buttons): --toast-clear is how far up from the window's bottom
// its top is while it shows, 0 when it is out of sight, and index.css
// stacks the toasts above that (#121). Again on every scroll and resize,
// and once its entry animation is over (it rises into place).
export function useToastClearance(ref) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const root = document.documentElement.style;
    let frame = 0;
    function measure() {
      frame = 0;
      const r = el.getBoundingClientRect();
      const h = window.innerHeight;
      const room = r.height && r.top < h && r.bottom > 0 ? Math.ceil(h - r.top) : 0;
      if (root.getPropertyValue("--toast-clear") !== `${room}px`) root.setProperty("--toast-clear", `${room}px`);
    }
    function later() { if (!frame) frame = requestAnimationFrame(measure); }
    measure();
    const ro = new ResizeObserver(later);
    ro.observe(el);
    ro.observe(document.body);
    // Captured: a panel that scrolls (Review's) moves its buttons too.
    window.addEventListener("scroll", later, { passive: true, capture: true });
    window.addEventListener("resize", later);
    el.addEventListener("animationend", later);
    return () => {
      ro.disconnect();
      window.removeEventListener("scroll", later, { capture: true });
      window.removeEventListener("resize", later);
      el.removeEventListener("animationend", later);
      cancelAnimationFrame(frame);
      root.removeProperty("--toast-clear");
    };
  }, [ref]);
}
