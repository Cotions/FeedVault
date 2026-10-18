import { useLayoutEffect } from "react";

/* A field's suggestion list as a popover (the top layer), placed under the
   field, or over it when there is no room below: a scrolling box around the
   field (Review's info panel, a dialog) does not cut it off. ``shown``: the
   list is rendered; ``listRef`` (popover="manual") and ``fieldRef`` its
   elements; ``content`` changes when the list's items do. */
export function usePopoverUnder(shown, listRef, fieldRef, content) {
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!shown || !list) return undefined;
    if (list.showPopover && !list.matches(":popover-open")) list.showPopover();
    function place() {
      const r = fieldRef.current?.getBoundingClientRect();
      if (!r) return;
      // The width first: at the field's width a long name wraps, and the
      // list is taller than at its own.
      list.style.left = `${r.left}px`;
      list.style.width = `${r.width}px`;
      const h = list.offsetHeight;
      const below = r.bottom + 4 + h <= window.innerHeight || r.top - 4 - h < 0;
      list.style.top = `${below ? r.bottom + 4 : r.top - 4 - h}px`;
    }
    place();
    // A late font changes its height too.
    const sized = typeof ResizeObserver === "function" ? new ResizeObserver(place) : null;
    sized?.observe(list);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      sized?.disconnect();
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [shown, content, listRef, fieldRef]);
}
