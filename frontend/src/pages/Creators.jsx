import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getAuthors } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { platformLabel, authorFeedPath } from "../lib/fmt";
import Icon from "../components/Icon";

export default function Creators() {
  const { refreshKey } = useScan();
  const { data, error, loading } = useApi(getAuthors, refreshKey);
  const [filter, setFilter] = useState("");

  const authors = useMemo(() => {
    const list = data || [];
    const f = filter.trim().toLowerCase();
    if (!f) return list;
    return list.filter(a => `${a.handle || ""} ${a.name || ""}`.toLowerCase().includes(f));
  }, [data, filter]);
  const total = (data || []).reduce((s, a) => s + (a.count || 0), 0);

  return (
    <div className="card">
      <div className="page-head">
        <h2 className="page-title">Creators</h2>
        <span className="page-count">{data ? `${data.length.toLocaleString()} · ${total.toLocaleString()} posts` : "…"}</span>
        <div className="page-head-spacer" />
        {data?.length > 8 && (
          <input
            type="text"
            className="page-filter"
            placeholder="Filter creators…"
            aria-label="Filter creators"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          />
        )}
      </div>
      {error && !data ? (
        <div className="empty">Could not load creators: {error.message}</div>
      ) : loading ? (
        <div className="empty">Loading…</div>
      ) : authors.length === 0 ? (
        <div className="empty">{filter ? "No creator matches." : "No creators yet. They appear once posts are indexed."}</div>
      ) : (
        <div className="creator-grid">
          {authors.map((a, i) => (
            <div
              key={`${a.platform}:${a.id ?? a.handle}`}
              className="creator-card"
              style={{ animationDelay: `${Math.min(i, 30) * 25}ms` }}
            >
              <Link to={authorFeedPath(a.platform, a)} className="creator-main" title={`Show posts by @${a.handle}`}>
                <span className="avatar-letter" aria-hidden="true">{(a.handle || a.name || "?").charAt(0).toUpperCase()}</span>
                <span className="creator-id">
                  <span className="creator-name">@{a.handle || a.id}</span>
                  <span className="creator-sub">
                    {a.name && a.name !== a.handle ? `${a.name} · ` : ""}{platformLabel(a.platform)}
                  </span>
                </span>
                <span className="creator-count">{a.count}</span>
              </Link>
              {a.id != null && (
                <Link
                  to={`/review?${new URLSearchParams({ platform: a.platform, author: a.id })}`}
                  className="icon-btn creator-review"
                  title={`Review @${a.handle}'s unreviewed posts`}
                  aria-label={`Review @${a.handle}`}
                >
                  <Icon name="review" size={15} />
                </Link>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
