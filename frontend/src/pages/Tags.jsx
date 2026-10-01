import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getTags, renameTag, deleteTag } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { tagFeedPath } from "../lib/fmt";
import { sameTag } from "../lib/tags";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";

/* Every tag with its post count. Rename in place; renaming to the name of
   another tag merges the two (asked first). Delete asks first too. */
export default function Tags() {
  const { refreshKey } = useScan();
  const { data, error, loading, reload } = useApi(getTags, refreshKey);
  const toast = useToast();
  const [filter,  setFilter]  = useState("");
  const [editing, setEditing] = useState(null);     // { name, text }
  const [confirm, setConfirm] = useState(null);     // { type: "merge" | "delete", name, to?, count }
  const [busy,    setBusy]    = useState(false);
  const [dlgError, setDlgError] = useState(null);

  const tags = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return (data || []).filter(t => !f || t.name.toLowerCase().includes(f));
  }, [data, filter]);

  async function doRename(from, to) {
    setBusy(true);
    setDlgError(null);
    try {
      const r = await renameTag(from, to);
      if (!r?.ok) {
        if (confirm) setDlgError(r?.error || "Could not rename."); else toast(r?.error || "Could not rename.", "err");
        return;
      }
      setEditing(null);
      setConfirm(null);
      toast(r.merged ? `Merged “${from}” into “${r.name}”.` : `Renamed to “${r.name}”.`);
      reload();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  function submitRename(e) {
    e.preventDefault();
    const to = editing.text.split(/\s+/).filter(Boolean).join(" ");
    if (!to || to === editing.name) { setEditing(null); return; }
    const other = (data || []).find(t => sameTag(t.name, to) && t.name !== editing.name && !sameTag(t.name, editing.name));
    if (other) {
      const count = (data || []).find(t => t.name === editing.name)?.count ?? 0;
      setDlgError(null);
      setConfirm({ type: "merge", name: editing.name, to: other.name, count });
      return;
    }
    doRename(editing.name, to);
  }

  async function doDelete() {
    setBusy(true);
    setDlgError(null);
    try {
      const r = await deleteTag(confirm.name);
      if (!r?.ok) { setDlgError(r?.error || "Could not delete."); return; }
      toast(`Deleted “${confirm.name}” from ${r.posts} post${r.posts === 1 ? "" : "s"}.`);
      setConfirm(null);
      reload();
    } catch (e) {
      setDlgError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const total = (data || []).length;
  return (
    <div className="card">
      <div className="page-head">
        <h2 className="page-title">Tags</h2>
        <span className="page-count">{data ? total.toLocaleString() : "…"}</span>
        <div className="page-head-spacer" />
        {total > 8 && (
          <input
            type="text"
            className="page-filter"
            placeholder="Filter tags…"
            aria-label="Filter tags"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          />
        )}
      </div>
      {error && !data ? (
        <div className="empty">Could not load tags: {error.message}</div>
      ) : loading ? (
        <div className="empty">Loading…</div>
      ) : tags.length === 0 ? (
        <div className="empty">
          {filter ? "No tag matches." : <>No tags yet. Add them on a post page, with <kbd className="kbd">T</kbd> in Review, or to many posts at once from the Feed's select mode.</>}
        </div>
      ) : (
        <ul className="tag-list">
          {tags.map(t => (
            <li key={t.name} className="tag-row">
              {editing?.name === t.name ? (
                <form className="tag-rename" onSubmit={submitRename}>
                  <input
                    type="text"
                    autoFocus
                    maxLength={64}
                    value={editing.text}
                    aria-label={`New name for ${t.name}`}
                    onChange={e => setEditing({ ...editing, text: e.target.value })}
                    onKeyDown={e => { if (e.key === "Escape") { e.stopPropagation(); setEditing(null); } }}
                  />
                  <button type="submit" className="del-btn del-btn-confirm" disabled={busy} title="Rename (Enter)" aria-label="Rename">
                    <Icon name="check" />
                  </button>
                  <button type="button" className="del-btn" onClick={() => setEditing(null)} title="Cancel (Esc)" aria-label="Cancel rename">
                    <Icon name="close" />
                  </button>
                </form>
              ) : (
                <Link to={tagFeedPath(t.name)} className="tag-row-main" title={`Show the posts tagged ${t.name}`}>
                  <Icon name="tag" size={14} />
                  <span className="tag-row-name">{t.name}</span>
                  <span className="creator-count">{t.count}</span>
                </Link>
              )}
              {editing?.name !== t.name && (
                <span className="tag-row-actions">
                  <button type="button" className="btn-ghost" onClick={() => setEditing({ name: t.name, text: t.name })}
                          title="Rename; a name that already exists merges the two">
                    Rename
                  </button>
                  <button type="button" className="btn-ghost" onClick={() => { setDlgError(null); setConfirm({ type: "delete", name: t.name, count: t.count }); }}>
                    Delete
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={!!confirm}
        danger={confirm?.type === "delete"}
        busy={busy}
        error={dlgError}
        title={confirm?.type === "merge" ? `Merge “${confirm.name}” into “${confirm.to}”?` : `Delete the tag “${confirm?.name}”?`}
        confirmLabel={confirm?.type === "merge" ? "Merge" : "Delete tag"}
        onConfirm={() => (confirm.type === "merge" ? doRename(confirm.name, confirm.to) : doDelete())}
        onCancel={() => setConfirm(null)}
      >
        {confirm?.type === "merge" ? (
          <p>
            “{confirm.to}” already exists. Its posts and the {confirm.count} post{confirm.count === 1 ? "" : "s"} tagged
            “{confirm.name}” all end up tagged “{confirm.to}”, and “{confirm.name}” is gone.
          </p>
        ) : (
          <p>
            Remove it from {confirm?.count} post{confirm?.count === 1 ? "" : "s"} (and any in the trash)? The posts
            themselves stay.
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}
