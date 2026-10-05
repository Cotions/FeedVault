import { useEffect, useState, useSyncExternalStore } from "react";
import { PHONE } from "./layout";

function subscribe(onChange) {
  const phone = window.matchMedia(PHONE);
  phone.addEventListener("change", onChange);
  return () => phone.removeEventListener("change", onChange);
}

// Whether the window is phone-sized, following a rotation.
export function usePhone() {
  return useSyncExternalStore(subscribe, () => window.matchMedia(PHONE).matches);
}

// Whether a page's filters are unfolded: open on a desktop (their toggle is
// hidden there), closed on a phone, where they are rarely changed and would
// push the content a screen down. Held by the page, not by what the filters
// show, so changing a filter does not close them; crossing the breakpoint (a
// rotation) starts over from that width's default.
export function useFiltersOpen() {
  const [open, setOpen] = useState(() => !window.matchMedia(PHONE).matches);
  useEffect(() => subscribe(() => setOpen(!window.matchMedia(PHONE).matches)), []);
  return [open, setOpen];
}
