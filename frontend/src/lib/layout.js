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
