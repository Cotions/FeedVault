import { useLayoutEffect, useRef } from "react";

/* The bar pinned under a list in select mode: count, select all, clear, and
   the page's own bulk actions as children. Pair with lib/useSelection. */
export default function SelectionBar({ selection, loaded, children }) {
  const { count, selectAll, clear } = selection;
  // Its height (one row or two, by the width) in --select-bar-h, for the
  // room index.css keeps under focus scrolled into view.
  const ref = useRef(null);
  useLayoutEffect(() => {
    const root = document.documentElement.style;
    const ro = new ResizeObserver(() => {
      if (ref.current) root.setProperty("--select-bar-h", `${Math.ceil(ref.current.offsetHeight)}px`);
    });
    ro.observe(ref.current);
    return () => { ro.disconnect(); root.removeProperty("--select-bar-h"); };
  }, []);
  return (
    <div className="select-bar" role="toolbar" aria-label="Selection" ref={ref}>
      <span className="select-count"><b>{count}</b> selected</span>
      <button type="button" className="btn-ghost" onClick={selectAll}>
        Select all loaded ({loaded})
      </button>
      <button type="button" className="btn-ghost" onClick={clear} disabled={!count}>
        Clear
      </button>
      <div className="page-head-spacer" />
      <span className="select-hint">Shift-click selects a range · Esc exits</span>
      {children}
    </div>
  );
}
