import { useRef, useState } from "react";
import { getCollections, createCollection, addToCollection, removeFromCollection } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useToast } from "../lib/toast";
import ConfirmDialog from "./ConfirmDialog";
import Icon from "./Icon";

/* "Add to collection…": every collection, clicked to add the posts at once,
   and a field to create one (Enter creates it and adds them). With one post,
   `member` (collection ids it is in) marks those, and clicking one takes the
   post out again. Mount it when it opens.

   Props: posts (ids), member (ids, optional), onChanged(), onClose */
export default function CollectionDialog({ posts, member, onChanged, onClose }) {
  const toast = useToast();
  const { data, error, reload } = useApi(getCollections, 0);
  const [inside, setInside] = useState(() => new Set(member || []));
  const [name,   setName]   = useState("");
  const [busy,   setBusy]   = useState(false);
  const [msg,    setMsg]    = useState(null);
  const inputRef = useRef(null);
  const single = !!member;
  const n = posts.length;

  async function run(fn) {
    setBusy(true);
    setMsg(null);
    try { await fn(); }
    catch (e) { setMsg(e.message); }
    finally { setBusy(false); }
  }

  const toggle = c => run(async () => {
    if (single && inside.has(c.id)) {
      const r = await removeFromCollection(c.id, posts);
      if (!r?.ok) { setMsg(r?.error || "Could not remove."); return; }
      setInside(s => { const x = new Set(s); x.delete(c.id); return x; });
      toast(`Removed from “${c.name}”.`);
    } else {
      const r = await addToCollection(c.id, posts);
      if (!r?.ok) { setMsg(r?.error || "Could not add."); return; }
      setInside(s => new Set(s).add(c.id));
      const skipped = n - (r.added?.length ?? 0);
      toast(`${r.added?.length ?? 0} post${r.added?.length === 1 ? "" : "s"} added to “${c.name}”`
            + (skipped ? ` (${skipped} already there)` : "") + ".");
    }
    reload();
    onChanged?.();
  });

  const create = () => run(async () => {
    const clean = name.split(/\s+/).filter(Boolean).join(" ");
    if (!clean) return;
    const r = await createCollection(clean);
    if (!r?.ok) { setMsg(r?.error || "Could not create the collection."); return; }
    setName("");
    const added = await addToCollection(r.collection.id, posts);
    if (!added?.ok) { setMsg(added?.error || "Created, but the posts could not be added."); reload(); return; }
    setInside(s => new Set(s).add(r.collection.id));
    toast(`Created “${r.collection.name}” with ${added.added.length} post${added.added.length === 1 ? "" : "s"}.`);
    reload();
    onChanged?.();
  });

  const list = data || [];
  return (
    <ConfirmDialog
      open
      title={single ? "Collections" : `Add ${n} post${n === 1 ? "" : "s"} to a collection`}
      confirmLabel="Done"
      cancelLabel="Close"
      error={msg}
      initialFocus={inputRef}
      onConfirm={onClose}
      onCancel={onClose}
    >
      <div className="collection-pick">
        <form className="collection-new" onSubmit={e => { e.preventDefault(); create(); }}>
          <input
            ref={inputRef}
            type="text"
            maxLength={64}
            placeholder="New collection…"
            aria-label="New collection name"
            value={name}
            onChange={e => setName(e.target.value)}
          />
          <button type="submit" className="btn-secondary" disabled={busy || !name.trim()}>
            <Icon name="plus" size={14} />Create
          </button>
        </form>
        {error && !data ? (
          <p className="dim">Could not load collections: {error.message}</p>
        ) : !data ? (
          <p className="dim">Loading…</p>
        ) : list.length === 0 ? (
          <p className="dim">No collections yet: name one above.</p>
        ) : (
          <ul className="collection-pick-list">
            {list.map(c => {
              const on = inside.has(c.id);
              return (
                <li key={c.id}>
                  <button type="button" className={`collection-pick-item${on ? " is-on" : ""}`} onClick={() => toggle(c)}
                          disabled={busy || (on && !single)} aria-pressed={on}
                          title={on ? (single ? `Remove from ${c.name}` : `Added to ${c.name}`) : `Add to ${c.name}`}>
                    <span className="collection-pick-thumb" aria-hidden="true">
                      {c.cover && c.cover.poster !== false ? <img src={c.cover.url} alt="" loading="lazy" /> : <Icon name="bookmark" size={14} />}
                    </span>
                    <span className="collection-pick-name">{c.name}</span>
                    <span className="tag-suggest-count">{c.count}</span>
                    {on && <Icon name="check" size={14} />}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </ConfirmDialog>
  );
}
