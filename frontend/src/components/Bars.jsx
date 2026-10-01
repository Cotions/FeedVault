import { Link } from "react-router-dom";
import { fmtInt } from "../lib/fmt";

/* A card of horizontal bars, one per [name, value] entry, scaled to the
   largest. Rows rise in one after another from `delay`. Used by Stats and
   Storage. */
export default function Bars({ title, entries, label, linkFor, format = fmtInt, delay = 0 }) {
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
                <span className="channel-bar-count">{format(count)}</span>
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
