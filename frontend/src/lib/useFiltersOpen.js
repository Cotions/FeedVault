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

// Whether a page's filters are unfolded. Folded by default at every width
// (#ui-declutter): the toggle counts the active ones, the page's title says
// the main one (a creator, a tag), and Sort stays in sight. Held by the page, not by what the filters
// show, so changing a filter does not close them; crossing the breakpoint (a
// rotation) starts over from that width's default.
export function useFiltersOpen() {
  const phone = usePhone();
  const [open, setOpen] = useState(false);
  const [was, setWas] = useState(phone);
  if (was !== phone) {
    setWas(phone);
    setOpen(false);
  }
  return [open, setOpen];
}
