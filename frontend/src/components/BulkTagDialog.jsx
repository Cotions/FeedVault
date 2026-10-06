import { useMemo, useRef, useState } from "react";
import { applyTags } from "../lib/api";
import { sameTag } from "../lib/tags";
import ConfirmDialog from "./ConfirmDialog";
import TagInput from "./TagInput";
import Icon from "./Icon";

/* Add and remove tags on many posts in one request. Mount it when it opens
   (it starts empty). The tags the posts already have are listed with how
   many of them carry each; clicking one marks it for removal.

   Props: posts (summaries), tags (all, from /api/tags), onApplied(result, add, remove), onCancel */
export default function BulkTagDialog({ posts, tags, onApplied, onCancel }) {
  const [add,    setAdd]    = useState([]);
  const [remove, setRemove] = useState([]);
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState(null);
  const inputRef = useRef(null);

  const present = useMemo(() => {
    const counts = new Map();
    for (const p of posts) for (const t of p.tags || []) counts.set(t, (counts.get(t) || 0) + 1);
    return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [posts]);

  function addName(name) {
    if (add.some(a => sameTag(a, name))) return;
    setAdd([...add, name]);
    setRemove(remove.filter(r => !sameTag(r, name)));
  }
  function toggleRemove(name) {
    setRemove(remove.some(r => sameTag(r, name)) ? remove.filter(r => !sameTag(r, name)) : [...remove, name]);
    setAdd(add.filter(a => !sameTag(a, name)));
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await applyTags(posts.map(p => p.id), { add, remove });
      if (!r?.ok) { setError(r?.error || "Could not change the tags."); return; }
      onApplied(r, add, remove);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const n = posts.length;
  return (
    <ConfirmDialog
      open
      busy={busy}
      error={error}
      title={`Tag ${n} post${n === 1 ? "" : "s"}`}
      confirmLabel={add.length || remove.length ? "Apply" : "Nothing to change"}
      confirmDisabled={!add.length && !remove.length}
      initialFocus={inputRef}
      onConfirm={run}
      onCancel={onCancel}
    >
      <div className="bulk-tags">
        <span className="bulk-tags-label">Add</span>
        <TagInput tags={tags} exclude={add} onAdd={addName} inputRef={inputRef}
                  onClose={() => { if (!busy) onCancel(); }}
                  onEmptyEnter={() => { if (!busy && (add.length || remove.length)) run(); }}
                  placeholder="Type a tag, Enter to add it to the list" />
        {add.length > 0 && (
          <ul className="tag-chips">
            {add.map(name => (
              <li key={name} className="tag-chip is-add">
                <span>+ {name}</span>
                <button type="button" className="tag-chip-x" onClick={() => setAdd(add.filter(a => a !== name))} aria-label={`Do not add ${name}`}>
                  <Icon name="close" size={10} />
                </button>
              </li>
            ))}
          </ul>
        )}
        {present.length > 0 && (
          <>
            <span className="bulk-tags-label">Already on these posts · click to remove</span>
            <ul className="tag-chips">
              {present.map(([name, count]) => {
                const on = remove.some(r => sameTag(r, name));
                return (
                  <li key={name} className={`tag-chip${on ? " is-remove" : ""}`}>
                    <button type="button" className="tag-chip-toggle" onClick={() => toggleRemove(name)} aria-pressed={on}
                            title={on ? `Keep ${name}` : `Remove ${name} from the ${count} post${count === 1 ? "" : "s"} that have it`}>
                      <span className="tag-chip-name">{on ? "− " : ""}{name}</span> <span className="dim">{count}/{n}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </ConfirmDialog>
  );
}
