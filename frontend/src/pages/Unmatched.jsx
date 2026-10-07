import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getUnmatched } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { fmtBytes, fmtFullDate, fmtInt, fmtStamp } from "../lib/fmt";
import { matches } from "../lib/people";
import { useWindow } from "../lib/windowing";
import PageHeader from "../components/PageHeader";
import { GapRow } from "../components/WinGap";

const COLUMNS = 4;

export default function Unmatched() {
  const { refreshKey } = useScan();
  const { data, error, loading } = useApi(getUnmatched, refreshKey);
  // The search: every word in the path or the reason. With thousands of
  // files only the rows near the viewport are rendered (lib/windowing.js),
  // so the browser's find bar does not see the others; this box does.
  const [query, setQuery] = useState("");
  const all = useMemo(() => data || [], [data]);
  const rows = useMemo(
    () => (query.trim() ? all.filter(r => matches(`${r.path} ${r.reason || ""}`.toLowerCase(), query)) : all),
    [all, query]);
  const bytes = useMemo(() => all.reduce((s, r) => s + (r.size || 0), 0), [all]);
  const { listRef, focusProps, render, settled } = useWindow(rows.length, { estimate: 37 });
  const files = n => `${fmtInt(n)} file${n === 1 ? "" : "s"}`;

  return (
    <>
      <PageHeader
        title="Unmatched"
        sub={!data ? "…" : query.trim() ? `${fmtInt(rows.length)} of ${files(all.length)} · ${fmtBytes(bytes)}`
          : `${files(all.length)} · ${fmtBytes(bytes)}`}
        actions={all.length > 8 && (
          <input
            type="text"
            className="page-filter"
            placeholder="Search paths and reasons…"
            aria-label="Search unmatched files"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        )}
      />
      <div className="card">
        <p className="page-lede">
          Files inside your media roots that no parser could attach to a post. FeedVault never guesses
          where they belong, and it never moves or deletes them: they are listed here so nothing goes
          missing silently.
        </p>
        {error && !data ? (
          <div className="empty">Could not load unmatched files: {error.message}</div>
        ) : loading ? (
          <div className="empty">Loading…</div>
        ) : all.length === 0 ? (
          <div className="empty">Every file is attached to a post.</div>
        ) : rows.length === 0 ? (
          <div className="empty">No file matches.</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table card-table unmatched-table" role="table">
              <thead role="rowgroup">
                <tr role="row">
                  <th scope="col" role="columnheader">Path</th>
                  <th scope="col" role="columnheader">Reason</th>
                  <th scope="col" role="columnheader" className="num">Size</th>
                  <th scope="col" role="columnheader" className="num">Modified</th>
                </tr>
              </thead>
              <tbody role="rowgroup" ref={listRef} className={settled ? "is-settled" : undefined} {...focusProps}>
                {render(i => {
                  const r = rows[i];
                  return (
                    <tr key={r.path} role="row" data-index={i} style={settled ? undefined : { animationDelay: `${Math.min(i, 30) * 20}ms` }}>
                      <td role="cell" className="path cell-main" data-label="Path" title={r.path}>{r.path}</td>
                      <td role="cell" className="reason" data-label="Reason">
                        {r.reason || "—"}
                        {/* A dismissed group is not listed in Duplicates: point at its Dismissed list instead. */}
                        {r.reason?.startsWith("duplicate of ") && (r.dismissed ? (
                          <> · marked not a duplicate{" "}
                            <Link to="/duplicates#dismissed" className="text-link" title="List this group again in Duplicates">restore in Duplicates</Link></>
                        ) : (
                          <> <Link to="/duplicates" className="text-link" title="Compare the copies and keep one">compare in Duplicates</Link></>
                        ))}
                      </td>
                      <td role="cell" className="num" data-label="Size">{fmtBytes(r.size)}</td>
                      <td role="cell" className="num" data-label="Modified" title={fmtFullDate(r.mtime)}>{fmtStamp(r.mtime)}</td>
                    </tr>
                  );
                }, (height, key) => <GapRow key={key} height={height} cols={COLUMNS} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

