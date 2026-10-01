import { Link } from "react-router-dom";

/* The row of big numbers that opens Stats and Storage. Each cell is
   { num, label, warn?, to?, title? }; a `to` makes it a link. */
export default function StatsHero({ cells }) {
  return (
    <div className="stats-hero">
      {cells.map((h, i) => {
        const body = (
          <>
            <span className={`stats-hero-num${h.warn ? " is-warn" : ""}`}>{h.num}</span>
            <span className="stats-hero-label">{h.label}</span>
          </>
        );
        const style = { animationDelay: `${i * 90}ms` };
        return h.to
          ? <Link key={h.label} to={h.to} className="stats-hero-cell is-link" style={style} title={h.title}>{body}</Link>
          : <div key={h.label} className="stats-hero-cell" style={style}>{body}</div>;
      })}
    </div>
  );
}
