import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

// Forward navigation (PUSH/REPLACE) starts at the top; Back/Forward (POP)
// restores the scroll position of the entry being returned to — like a browser.
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

  // Remember the scroll offset for the current history entry as the user scrolls.
  useEffect(() => {
    const onScroll = () => positions.current.set(location.key, window.scrollY);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [location.key]);

  useLayoutEffect(() => {
    if (navType === "POP") {
      window.scrollTo(0, positions.current.get(location.key) ?? 0);
    } else {
      window.scrollTo(0, 0);
    }
  }, [location.key, navType]);

  return null;
}
