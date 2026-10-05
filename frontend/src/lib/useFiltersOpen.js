import { useState, useSyncExternalStore } from "react";
import { PHONE } from "./layout";

const phoneQuery = window.matchMedia(PHONE);

function subscribe(onChange) {
  phoneQuery.addEventListener("change", onChange);
  return () => phoneQuery.removeEventListener("change", onChange);
}

// Whether the window is phone-sized, following a rotation.
export function usePhone() {
  return useSyncExternalStore(subscribe, () => phoneQuery.matches);
}

// Whether a page's filters are unfolded: open on a desktop (their toggle is
// hidden there), closed on a phone, where they are rarely changed and would
// push the content a screen down. Held by the page, not by what the filters
// show, so changing a filter does not close them; crossing the breakpoint (a
// rotation) starts over from that width's default.
export function useFiltersOpen() {
  const phone = usePhone();
  const [open, setOpen] = useState(!phone);
  const [was, setWas] = useState(phone);
  if (was !== phone) {
    setWas(phone);
    setOpen(!phone);
  }
  return [open, setOpen];
}
