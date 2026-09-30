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
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Path</th>
                <th scope="col">Reason</th>
                <th scope="col" className="num">Size</th>
                <th scope="col" className="num">Modified</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.path} style={{ animationDelay: `${Math.min(i, 30) * 20}ms` }}>
                  <td className="path" title={r.path}>{r.path}</td>
                  <td className="reason">{r.reason || "—"}</td>
                  <td className="num">{fmtBytes(r.size)}</td>
                  <td className="num" title={fmtFullDate(r.mtime)}>{fmtStamp(r.mtime)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
