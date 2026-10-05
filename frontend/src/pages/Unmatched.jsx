import { Link } from "react-router-dom";
import { getUnmatched } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { fmtBytes, fmtFullDate, fmtStamp } from "../lib/fmt";

export default function Unmatched() {
  const { refreshKey } = useScan();
  const { data, error, loading } = useApi(getUnmatched, refreshKey);
  const rows = data || [];
  const bytes = rows.reduce((s, r) => s + (r.size || 0), 0);

  return (
    <div className="card">
      <div className="page-head">
        <h2 className="page-title">Unmatched</h2>
        <span className="page-count">{data ? `${rows.length.toLocaleString()} file${rows.length === 1 ? "" : "s"} · ${fmtBytes(bytes)}` : "…"}</span>
      </div>
      <p className="page-lede">
        Files inside your media roots that no parser could attach to a post. FeedVault never guesses
        where they belong, and it never moves or deletes them: they are listed here so nothing goes
        missing silently.
      </p>
      {error && !data ? (
        <div className="empty">Could not load unmatched files: {error.message}</div>
      ) : loading ? (
        <div className="empty">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="empty">Every file is attached to a post.</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table card-table" role="table">
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">Path</th>
                <th scope="col" role="columnheader">Reason</th>
                <th scope="col" role="columnheader" className="num">Size</th>
                <th scope="col" role="columnheader" className="num">Modified</th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {rows.map((r, i) => (
                <tr key={r.path} role="row" style={{ animationDelay: `${Math.min(i, 30) * 20}ms` }}>
                  <td role="cell" className="path cell-main" data-label="Path" title={r.path}>{r.path}</td>
                  <td role="cell" className="reason" data-label="Reason">
                    {r.reason || "—"}
                    {r.reason?.startsWith("duplicate of ") && (
                      <> <Link to="/duplicates" className="text-link" title="Compare the copies and keep one">compare in Duplicates</Link></>
                    )}
                  </td>
                  <td role="cell" className="num" data-label="Size">{fmtBytes(r.size)}</td>
                  <td role="cell" className="num" data-label="Modified" title={fmtFullDate(r.mtime)}>{fmtStamp(r.mtime)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
