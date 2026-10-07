import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

// Forward navigation (PUSH/REPLACE) starts at the top; Back/Forward (POP)
// restores the scroll position of the entry being returned to — like a browser.
const RESTORE_MS = 3000;

export default function ScrollManager() {
  const location = useLocation();
  const navType  = useNavigationType();
  const positions = useRef(new Map());

  // Take over scroll handling from the browser so it doesn't fight us.
  useEffect(() => {
    const prev = window.history.scrollRestoration;
    if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
    return () => { if ("scrollRestoration" in window.history) window.history.scrollRestoration = prev; };
  }, []);

  // Remember the scroll offset for the current history entry as the user
  // scrolls. The entry is the one shown: switched before the new page
  // scrolls (below), so its scroll to the top is never taken for the page
  // just left, as a listener swapped by a later effect sometimes did.
  const keyRef = useRef(location.key);
  useEffect(() => {
    const onScroll = () => positions.current.set(keyRef.current, window.scrollY);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // On Back the page is often still loading, too short to scroll that far
  // (the Feed came back at the top, QA pass 3): the position is applied
  // again as the page grows, until it is reached, the user scrolls or
  // types, or RESTORE_MS went by.
  useLayoutEffect(() => {
    keyRef.current = location.key;
    if (navType !== "POP") {
      window.scrollTo(0, 0);
      return undefined;
    }
    const want = positions.current.get(location.key) ?? 0;
    window.scrollTo(0, want);
    if (Math.abs(window.scrollY - want) < 2) return undefined;
    const USER = ["wheel", "touchstart", "keydown", "mousedown"];
    let timer = 0;
    const ro = new ResizeObserver(() => {
      window.scrollTo(0, want);
      if (Math.abs(window.scrollY - want) < 2) stop();
    });
    function stop() {
      ro.disconnect();
      clearTimeout(timer);
      for (const e of USER) window.removeEventListener(e, stop);
    }
    ro.observe(document.body);
    timer = setTimeout(stop, RESTORE_MS);
    for (const e of USER) window.addEventListener(e, stop, { passive: true });
    return stop;
  }, [location.key, navType]);

  return null;
}
