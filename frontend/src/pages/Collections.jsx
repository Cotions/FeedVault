import { useState } from "react";
import { Link } from "react-router-dom";
import { getCollections, createCollection, reorderCollections } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { cleanName } from "../lib/tags";
import { useFollowFocus } from "../lib/layout";
import Icon from "../components/Icon";
import PageHeader from "../components/PageHeader";

/* Every collection as a cover tile with its post count, and a field to start
   a new one. Drag a tile onto another (or use its arrows) to move it there.
   Posts are added from the Feed (select mode), a post page or Review (C). */
export default function Collections() {
  const { refreshKey } = useScan();
  const { data, error, loading, reload } = useApi(getCollections, refreshKey);
  const toast = useToast();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(null);               // { from, over }
  const [order, setOrder] = useState(null);             // { of, list }: the list as moved, until data reloads
  const list = (order && order.of === data ? order.list : data) || [];
  const { listRef, follow } = useFollowFocus();

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

  // ``arrow``: moved with its Move earlier/later button, which keeps focus
  // on the moved tile, so Enter again moves it on (#136). The arrows are
  // never disabled (aria-disabled, at an end and while a move is saved),
  // so focus never falls off one.
  async function move(from, to, arrow) {
    if (busy || from === to || to < 0 || to >= list.length) return;
    const next = [...list];
    const moved = next.splice(from, 1)[0];
    next.splice(to, 0, moved);
    const refocus = () => { if (arrow) follow(moved.id, arrow); };
    refocus();
    setOrder({ of: data, list: next });
    setBusy(true);
    try {
      const r = await reorderCollections(next.map(c => c.id));
      if (!r?.ok) { refocus(); setOrder(null); toast(r?.error || "Could not save the new order.", "err"); }
      reload();
    } catch (err) {
      refocus();
      setOrder(null);
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Collections"
        sub={data ? list.length.toLocaleString() : "…"}
        actions={(
          <form className="collection-new" onSubmit={create}>
            <input type="text" maxLength={64} placeholder="New collection…" aria-label="New collection name"
                   value={name} onChange={e => setName(e.target.value)} />
            <button type="submit" className="btn-secondary" disabled={busy || !name.trim()}>
              <Icon name="plus" size={14} />Create
            </button>
          </form>
        )}
      />
      <div className="card">
        {error && !data ? (
          <div className="empty">Could not load collections: {error.message}</div>
        ) : loading ? (
          <div className="empty">Loading…</div>
        ) : list.length === 0 ? (
          <div className="empty">
            No collections yet. Name one above, or pick posts in the Feed's select mode and choose “Collection…”.
          </div>
        ) : (
          <>
            {list.length > 1 && <p className="dim collection-hint">Drag a collection onto another to move it there.</p>}
            <ol className="collection-grid" ref={listRef}>
              {list.map((c, i) => (
                <li
                  key={c.id}
                  data-key={c.id}
                  className={`collection-card${drag?.from === i ? " is-dragging" : ""}${drag && drag.over === i && drag.from !== i ? " is-over" : ""}`}
                  style={{ animationDelay: `${Math.min(i, 30) * 25}ms` }}
                  draggable={!busy && list.length > 1}
                  onDragStart={e => { e.dataTransfer.effectAllowed = "move"; setDrag({ from: i, over: i }); }}
                  onDragOver={e => { if (drag) { e.preventDefault(); if (drag.over !== i) setDrag({ ...drag, over: i }); } }}
                  onDrop={e => { e.preventDefault(); if (drag) move(drag.from, i); setDrag(null); }}
                  onDragEnd={() => setDrag(null)}
                >
                  <Link to={`/collections/${c.id}`} className="collection-card-link" draggable={false}>
                    <span className="collection-cover">
                      {c.cover && c.cover.poster !== false
                        ? <img src={c.cover.url} alt="" loading="lazy" decoding="async" draggable={false} />
                        : <Icon name="bookmark" size={28} />}
                    </span>
                    <span className="collection-card-info">
                      <span className="collection-card-name" title={c.name}>{c.name}</span>
                      <span className="creator-count">{c.count}</span>
                    </span>
                  </Link>
                  {list.length > 1 && (
                    <div className="collection-tile-bar">
                      <span className="collection-grip" aria-hidden="true"><Icon name="grip" size={14} /></span>
                      <button type="button" className="del-btn" onClick={() => move(i, i - 1, "earlier")}
                              aria-disabled={busy || i === 0 || undefined} data-move="earlier"
                              title="Move earlier" aria-label={`Move ${c.name} earlier`}><Icon name="chevLeft" size={14} /></button>
                      <button type="button" className="del-btn" onClick={() => move(i, i + 1, "later")}
                              aria-disabled={busy || i === list.length - 1 || undefined} data-move="later"
                              title="Move later" aria-label={`Move ${c.name} later`}><Icon name="chevRight" size={14} /></button>
                    </div>
                  )}
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </>
  );
}
