import { useState } from "react";
import { Link } from "react-router-dom";
import { getCollections, createCollection } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { cleanName } from "../lib/tags";
import Icon from "../components/Icon";

/* Every collection as a cover tile with its post count, and a field to start
   a new one. Posts are added from the Feed (select mode), a post page or
   Review (C). */
export default function Collections() {
  const { refreshKey } = useScan();
  const { data, error, loading, reload } = useApi(getCollections, refreshKey);
  const toast = useToast();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function create(e) {
    e.preventDefault();
    const clean = cleanName(name);
    if (!clean) return;
    setBusy(true);
    try {
      const r = await createCollection(clean);
      if (!r?.ok) { toast(r?.error || "Could not create the collection.", "err"); return; }
      setName("");
      reload();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="page-head">
        <h2 className="page-title">Collections</h2>
        <span className="page-count">{data ? data.length.toLocaleString() : "…"}</span>
        <div className="page-head-spacer" />
        <form className="collection-new" onSubmit={create}>
          <input type="text" maxLength={64} placeholder="New collection…" aria-label="New collection name"
                 value={name} onChange={e => setName(e.target.value)} />
          <button type="submit" className="btn-secondary" disabled={busy || !name.trim()}>
            <Icon name="plus" size={14} />Create
          </button>
        </form>
      </div>
      {error && !data ? (
        <div className="empty">Could not load collections: {error.message}</div>
      ) : loading ? (
        <div className="empty">Loading…</div>
      ) : data.length === 0 ? (
        <div className="empty">
          No collections yet. Name one above, or pick posts in the Feed's select mode and choose “Collection…”.
        </div>
      ) : (
        <div className="collection-grid">
          {data.map((c, i) => (
            <Link key={c.id} to={`/collections/${c.id}`} className="collection-card" style={{ animationDelay: `${Math.min(i, 30) * 25}ms` }}>
              <span className="collection-cover">
                {c.cover && c.cover.poster !== false
                  ? <img src={c.cover.url} alt="" loading="lazy" decoding="async" />
                  : <Icon name="bookmark" size={28} />}
              </span>
              <span className="collection-card-info">
                <span className="collection-card-name">{c.name}</span>
                <span className="creator-count">{c.count}</span>
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
