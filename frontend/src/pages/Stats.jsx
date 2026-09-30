import { Link } from "react-router-dom";
import { getStats } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { fmtBytes, fmtInt, platformLabel } from "../lib/fmt";

function Bars({ title, entries, label, linkFor, delay = 0 }) {
  const max = entries.length ? Math.max(...entries.map(([, c]) => c), 1) : 1;
  return (
    <div className="card">
      <div className="card-title">{title}</div>
      {entries.length === 0 ? (
        <div className="empty">No data yet.</div>
      ) : (
        <div className="channel-bars">
          {entries.map(([name, count], i) => {
            const d = `${delay + i * 50}ms`;
            const inner = (
              <>
                <span className="channel-bar-name">{label(name)}</span>
                <span className="channel-bar-track" aria-hidden="true">
                  <span className="channel-bar-fill" style={{ width: `${(count / max) * 100}%` }} />
                </span>
                <span className="channel-bar-count">{fmtInt(count)}</span>
              </>
            );
            const props = { className: "channel-bar-row", style: { "--d": d, animationDelay: d } };
            return linkFor
              ? <Link key={name} to={linkFor(name)} {...props} title={`Show ${label(name)} posts`}>{inner}</Link>
              : <div key={name} {...props}>{inner}</div>;
          })}
        </div>
      )}
    </div>
  );
}

export default function Stats() {
  const { refreshKey } = useScan();
  const { data: s, error } = useApi(getStats, refreshKey);

  if (!s) {
    return (
      <div className="stats-page">
        <div className="page-head page-head-bare"><h2 className="page-title">Stats</h2></div>
        <div className="card"><div className="empty">{error ? `Could not load stats: ${error.message}` : "Loading…"}</div></div>
      </div>
    );
  }

  const hero = [
    { num: fmtInt(s.posts),   label: "posts" },
    { num: fmtInt(s.media),   label: "media files" },
    { num: fmtInt(s.authors), label: "creators", to: "/creators" },
    { num: fmtBytes(s.bytes), label: "on disk" },
    { num: fmtInt(s.missing), label: "missing", warn: s.missing > 0 },
    { num: fmtInt(s.unmatched), label: "unmatched files", warn: s.unmatched > 0, to: "/unmatched" },
    { num: fmtInt(s.kept),       label: "kept", to: "/?review=kept" },
    { num: fmtInt(s.unreviewed), label: "to review", to: "/review" },
  ];
  const sortDesc = obj => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="stats-page">
      <div className="page-head page-head-bare"><h2 className="page-title">Stats</h2></div>
      <div className="stats-hero">
        {hero.map((h, i) => {
          const body = (
            <>
              <span className={`stats-hero-num${h.warn ? " is-warn" : ""}`}>{h.num}</span>
              <span className="stats-hero-label">{h.label}</span>
            </>
          );
          const style = { animationDelay: `${i * 90}ms` };
          return h.to
            ? <Link key={h.label} to={h.to} className="stats-hero-cell is-link" style={style}>{body}</Link>
            : <div key={h.label} className="stats-hero-cell" style={style}>{body}</div>;
        })}
      </div>
      <div className="stats-split">
        <Bars
          title="Posts per platform"
          entries={sortDesc(s.by_platform)}
          label={platformLabel}
          linkFor={p => `/?platform=${encodeURIComponent(p)}`}
          delay={500}
        />
        <Bars
          title="Posts per kind"
          entries={sortDesc(s.by_kind)}
          label={k => k}
          linkFor={k => `/?kind=${encodeURIComponent(k)}`}
          delay={600}
        />
      </div>
    </div>
  );
}
