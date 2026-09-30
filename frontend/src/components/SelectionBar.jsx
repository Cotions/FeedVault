/* The bar pinned under a list in select mode: count, select all, clear, and
   the page's own bulk actions as children. Pair with lib/useSelection. */
export default function SelectionBar({ selection, loaded, children }) {
  const { count, selectAll, clear } = selection;
  return (
    <div className="select-bar" role="toolbar" aria-label="Selection">
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
