import { useLayoutEffect } from "react";

// The phone breakpoint, the same as the CSS's max-width: 640px rules.
export const PHONE = "(max-width: 640px)";

// What a focus trap cycles through.
export const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Gives focus back to what had it before a dialog opened, without a
// scroll: the page is still where it was when the dialog opened. Scrolled
// into view, a button in the sticky selection bar can never get there (it
// sits in the room html's scroll-padding keeps for the bar), and each
// close moved the page down by that room (#119).
export function restoreFocus(el) {
  if (el && el.focus && document.contains(el)) el.focus({ preventScroll: true });
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
