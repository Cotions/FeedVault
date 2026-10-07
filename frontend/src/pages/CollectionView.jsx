import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getCollection, renameCollection, deleteCollection, removeFromCollection,
         orderCollection, setCollectionCover } from "../lib/api";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { cleanName } from "../lib/tags";
import { useUnsaved } from "../lib/unsaved";
import { excerpt, postPath } from "../lib/fmt";
import { focusLost, restoreFocus, useFollowFocus, useKeepFocus } from "../lib/layout";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DiscardDialog from "../components/DiscardDialog";
import PageHeader from "../components/PageHeader";

const PAGE = 60;

/* One collection, in its order. Drag a tile onto another to move it there
   (or use its arrows); the new order of the loaded posts is saved at once. */
export default function CollectionView() {
  const { id } = useParams();
  const { refreshKey } = useScan();
  const navigate = useNavigate();
  const toast = useToast();
  const [state, setState] = useState({ id: null, collection: null, posts: [], total: 0, error: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const [renaming, setRenaming] = useState(null);       // the name being typed
  const [confirmDel, setConfirmDel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(null);               // { from, over }
  const { listRef, follow } = useFollowFocus();
  // A tile's Use as cover goes once used, its Remove takes the tile: focus
  // follows to that tile's picture, or to the next tile's Remove (cover,
  // remove below), else to the card, never onto <body> (#139).
  const cardRef = useRef(null);
  const { onFocus, onBlur } = useKeepFocus(cardRef);
  // The rename form shut (saved, cancelled, Esc): focus on it goes back to Rename.
  const renameRef = useRef(null);
  const wasRenaming = useRef(false);
  useEffect(() => {
    if (wasRenaming.current && renaming == null && focusLost()) restoreFocus(renameRef.current);
    wasRenaming.current = renaming != null;
  }, [renaming]);

  const load = useCallback((offset, limit) => getCollection(id, { offset, limit }), [id]);

  useEffect(() => {
    let alive = true;
    load(0, PAGE).then(
      r => { if (alive) setState({ id, collection: r.collection, posts: r.posts, total: r.total, error: null }); },
      error => { if (alive) setState(s => ({ ...s, id, error })); },
    );
    return () => { alive = false; };
  }, [id, load, refreshKey]);

  const { collection, posts, total, error } = state;
  const current = state.id === id;
  // A new name typed and not saved: asked about before the app leaves the page.
  const unsaved = useUnsaved(renaming != null && !!collection && renaming !== collection.name);

  async function loadMore() {
    setLoadingMore(true);
    try {
      const r = await load(posts.length, PAGE);
      setState(s => {
        const seen = new Set(s.posts.map(p => p.id));
        return { ...s, posts: [...s.posts, ...r.posts.filter(p => !seen.has(p.id))], total: r.total };
      });
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setLoadingMore(false);
    }
  }

  async function act(fn) {
    setBusy(true);
    try { await fn(); }
    catch (e) { toast(e.message, "err"); }
    finally { setBusy(false); }
  }

  // ``arrow``: moved with its Move earlier/later button, which keeps focus
  // on the moved tile, so Enter again moves it on (#136). The arrows are
  // never disabled (aria-disabled, at an end and while a move is saved),
  // so focus never falls off one.
  const move = (from, to, arrow) => {
    if (busy || from === to || to < 0 || to >= posts.length) return;
    const before = posts;
    const next = [...posts];
    const moved = next.splice(from, 1)[0];
    next.splice(to, 0, moved);
    const refocus = () => { if (arrow) follow(moved.id, arrow); };
    refocus();
    setState(s => ({ ...s, posts: next }));
    act(async () => {
      const r = await orderCollection(id, next.map(p => p.id));
      if (!r?.ok) {
        refocus();
        setState(s => ({ ...s, posts: before }));
        toast(r?.error || "Could not save the new order.", "err");
      }
    });
  };

  // Off while busy, but never disabled (aria-disabled): a disabled button
  // drops the focus it has.
  const remove = (p, i) => !busy && act(async () => {
    const r = await removeFromCollection(id, [p.id]);
    if (!r?.ok) { toast(r?.error || "Could not remove.", "err"); return; }
    const next = posts[i + 1] ?? posts[i - 1];
    if (next) follow(next.id, "remove");
    setState(s => ({
      ...s,
      posts: s.posts.filter(x => x.id !== p.id),
      total: Math.max(0, s.total - r.removed),
      collection: s.collection.cover_post === p.id ? { ...s.collection, cover_post: null } : s.collection,
    }));
  });

  const cover = p => !busy && act(async () => {
    const r = await setCollectionCover(id, p.id);
    if (!r?.ok) { toast(r?.error || "Could not set the cover.", "err"); return; }
    follow(p.id, "open");
    setState(s => ({ ...s, collection: r.collection }));
    toast("Cover set.");
  });

  const rename = e => {
    e.preventDefault();
    const name = cleanName(renaming);
    if (!name || name === collection.name) { setRenaming(null); return; }
    act(async () => {
      const r = await renameCollection(id, name);
      if (!r?.ok) { toast(r?.error || "Could not rename.", "err"); return; }
      setState(s => ({ ...s, collection: r.collection }));
      setRenaming(null);
    });
  };

  const runDelete = () => act(async () => {
    const r = await deleteCollection(id);
    if (!r?.ok) { toast(r?.error || "Could not delete.", "err"); return; }
    setConfirmDel(false);
    toast(`Deleted “${collection.name}”. Its posts stay.`);
    unsaved.leave(() => navigate("/collections"));
  });

  // Before the collection loads, or when it cannot: the same head, with a
  // stand-in title, so nothing jumps when it comes.
  const back = <Link to="/collections" className="btn-secondary btn-back"><Icon name="back" size={15} />Collections</Link>;
  if (error && (!current || !collection)) {
    return (
      <>
        <PageHeader title="Collection" back={back} />
        <div className="card"><div className="empty">
          {error.status === 404 ? "No such collection." : `Could not load the collection: ${error.message}`}{" "}
          <Link to="/collections" className="text-link">All collections</Link>
        </div></div>
      </>
    );
  }
  if (!current || !collection) {
    return (
      <>
        <PageHeader title="Collection" back={back} />
        <div className="card"><div className="empty">Loading…</div></div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={collection.name}
        heading={renaming != null && (
          <form className="tag-rename" onSubmit={rename}>
            <input type="text" autoFocus maxLength={64} value={renaming} aria-label="Collection name"
                   onChange={e => setRenaming(e.target.value)}
                   onKeyDown={e => { if (e.key === "Escape") setRenaming(null); }} />
            <button type="submit" className="del-btn del-btn-confirm" disabled={busy} aria-label="Rename"><Icon name="check" /></button>
            <button type="button" className="del-btn" onClick={() => setRenaming(null)} aria-label="Cancel rename"><Icon name="close" /></button>
          </form>
        )}
        sub={total.toLocaleString()}
        back={back}
        actions={<>
          {renaming == null && (
            <button type="button" ref={renameRef} className="btn-ghost" onClick={() => setRenaming(collection.name)}>Rename</button>
          )}
          <button type="button" className="btn-danger-soft" onClick={() => setConfirmDel(true)}>
            <Icon name="trash" size={14} />Delete collection
          </button>
        </>}
      />
      <div className="card" ref={cardRef} tabIndex={-1} role="region" aria-label="Posts in this collection"
           onFocus={onFocus} onBlur={onBlur}>
        {posts.length === 0 ? (
          <div className="empty">
            Nothing here yet. Add posts from the <Link to="/" className="text-link">Feed</Link> (Select, then
            “Collection…”), from a post page, or with <kbd className="kbd">C</kbd> in Review.
          </div>
        ) : (
          <>
            <p className="dim collection-hint">Drag a post onto another to move it there, or use its arrows.</p>
            <ol className="collection-posts" ref={listRef}>
              {posts.map((p, i) => {
                const alt = excerpt(p.text, 100) || `Post by @${p.author?.handle || "unknown"}`;
                const isCover = collection.cover_post === p.id || (!collection.cover_post && i === 0);
                return (
                  <li
                    key={p.id}
                    data-key={p.id}
                    className={`collection-tile${drag?.from === i ? " is-dragging" : ""}${drag && drag.over === i && drag.from !== i ? " is-over" : ""}`}
                    draggable={!busy}
                    onDragStart={e => { e.dataTransfer.effectAllowed = "move"; setDrag({ from: i, over: i }); }}
                    onDragOver={e => { if (drag) { e.preventDefault(); if (drag.over !== i) setDrag({ ...drag, over: i }); } }}
                    onDrop={e => { e.preventDefault(); if (drag) move(drag.from, i); setDrag(null); }}
                    onDragEnd={() => setDrag(null)}
                  >
                    <Link to={postPath(p)} className="collection-tile-media" draggable={false} title={alt} data-move="open">
                      {p.cover && p.cover.poster !== false
                        ? <img src={p.cover.url} alt={alt} loading="lazy" decoding="async" draggable={false} />
                        : <span className="collection-tile-text">{excerpt(p.text, 120) || "(no text)"}</span>}
                      {isCover && <span className="collection-cover-badge">cover</span>}
                    </Link>
                    <div className="collection-tile-bar">
                      <span className="collection-grip" aria-hidden="true"><Icon name="grip" size={14} /></span>
                      <button type="button" className="del-btn" onClick={() => move(i, i - 1, "earlier")}
                              aria-disabled={busy || i === 0 || undefined} data-move="earlier"
                              title="Move earlier" aria-label="Move earlier"><Icon name="chevLeft" size={14} /></button>
                      <button type="button" className="del-btn" onClick={() => move(i, i + 1, "later")}
                              aria-disabled={busy || i === posts.length - 1 || undefined} data-move="later"
                              title="Move later" aria-label="Move later"><Icon name="chevRight" size={14} /></button>
                      <div className="page-head-spacer" />
                      {!isCover && (
                        <button type="button" className="del-btn" onClick={() => cover(p)} aria-disabled={busy || undefined}
                                title="Use as cover" aria-label="Use as cover"><Icon name="image" size={14} /></button>
                      )}
                      <button type="button" className="del-btn" onClick={() => remove(p, i)} aria-disabled={busy || undefined}
                              data-move="remove"
                              title="Remove from this collection (the post stays)" aria-label="Remove from collection">
                        <Icon name="close" size={14} />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ol>
            {posts.length < total && (
              <div className="feed-more">
                <button type="button" className="btn-secondary" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? "Loading…" : `Load more (${(total - posts.length).toLocaleString()} left)`}
                </button>
              </div>
            )}
          </>
        )}

        <ConfirmDialog
          open={confirmDel}
          danger
          busy={busy}
          title={`Delete the collection “${collection.name}”?`}
          confirmLabel="Delete collection"
          onConfirm={runDelete}
          onCancel={() => setConfirmDel(false)}
        >
          <p>The {total} post{total === 1 ? "" : "s"} in it stay where they are; only the collection goes.</p>
        </ConfirmDialog>
        <DiscardDialog open={unsaved.asking} onDiscard={unsaved.discard} onKeep={unsaved.keep}>
          <p>The new name for “{collection.name}” is not saved.</p>
        </DiscardDialog>
      </div>
    </>
  );
}
