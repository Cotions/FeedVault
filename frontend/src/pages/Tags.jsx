import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getTags, renameTag, deleteTag, setTagColor, deleteUnusedTags } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { tagFeedPath } from "../lib/fmt";
import { cleanName, sameTag } from "../lib/tags";
import { setTagColors } from "../lib/tagColors";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import PageHeader from "../components/PageHeader";

// The colours a tag can wear: distinct on the dark surface, readable as text.
const COLORS = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#14b8a6", "#3b82f6", "#8b5cf6", "#ec4899", "#94a3b8"];

/* Every tag with its post count. Rename in place; renaming to the name of
   another tag merges the two (asked first). Delete asks first too. A tag
   can wear a colour, shown on its chips everywhere. Tags on no post are
   dimmed; "Delete unused" removes them (asked first, never on its own). */
export default function Tags() {
  const { refreshKey } = useScan();
  const { data, error, loading, reload } = useApi(getTags, refreshKey);
  useEffect(() => { if (data) setTagColors(data); }, [data]);
  const [coloring, setColoring] = useState(null);   // the tag whose palette is open
  const toast = useToast();
  const [filter,  setFilter]  = useState("");
  const [editing, setEditing] = useState(null);     // { name, text }
  const [confirm, setConfirm] = useState(null);     // { type: "merge" | "delete" | "unused", name?, to?, count?, names? }
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
    const to = cleanName(editing.text);
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

  async function pickColor(name, color) {
    setBusy(true);
    try {
      const r = await setTagColor(name, color);
      if (!r?.ok) { toast(r?.error || "Could not change the colour.", "err"); return; }
      setColoring(null);
      reload();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function doDeleteUnused() {
    setBusy(true);
    setDlgError(null);
    try {
      const r = await deleteUnusedTags(confirm.names);
      if (!r?.ok) { setDlgError(r?.error || "Could not delete."); return; }
      const n = r.deleted.length;
      toast(n === confirm.names.length
        ? `Deleted ${n} unused tag${n === 1 ? "" : "s"}.`
        : `Deleted ${n} of ${confirm.names.length}: the others are on a post again.`);
      setConfirm(null);
      reload();
    } catch (e) {
      setDlgError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const total = (data || []).length;
  const unused = (data || []).filter(t => t.unused).map(t => t.name);
  return (
    <>
      <PageHeader
        title="Tags"
        sub={data ? total.toLocaleString() : "…"}
        actions={<>
          {unused.length > 0 && (
            <button type="button" className="btn-secondary" disabled={busy}
                    onClick={() => { setDlgError(null); setConfirm({ type: "unused", names: unused }); }}
                    title="Delete the tags that are on no post at all (not even one in the trash)">
              <Icon name="trash" size={14} />Delete unused ({unused.length})
            </button>
          )}
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
        </>}
      />
      <div className="card">
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
              <li key={t.name} className={`tag-row${t.count === 0 ? " is-unused" : ""}`}
                  title={t.count > 0 ? undefined : t.unused ? "On no post" : "Only on posts in the trash: it comes back with them"}>
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
                  <Link to={tagFeedPath(t.name)} className="tag-row-main" title={`Show the posts tagged ${t.name}`}
                        style={t.color ? { "--tag": t.color } : undefined}>
                    <Icon name="tag" size={14} className={t.color ? "tag-row-icon has-color" : "tag-row-icon"} />
                    <span className="tag-row-name">{t.name}</span>
                    <span className="creator-count">{t.count}</span>
                  </Link>
                )}
                {editing?.name !== t.name && (
                  <span className="tag-row-actions">
                    <button type="button" className="btn-ghost tag-color-btn" aria-expanded={coloring === t.name}
                            onClick={() => setColoring(coloring === t.name ? null : t.name)} title="Pick a colour for this tag's chips">
                      <span className={`tag-swatch${t.color ? "" : " is-none"}`} style={t.color ? { background: t.color } : undefined} />
                      Colour
                    </button>
                    <button type="button" className="btn-ghost" onClick={() => setEditing({ name: t.name, text: t.name })}
                            title="Rename; a name that already exists merges the two">
                      Rename
                    </button>
                    <button type="button" className="btn-ghost" onClick={() => { setDlgError(null); setConfirm({ type: "delete", name: t.name, count: t.count }); }}>
                      Delete
                    </button>
                  </span>
                )}
                {coloring === t.name && (
                  <div className="tag-palette" role="group" aria-label={`Colour of ${t.name}`}>
                    {COLORS.map(c => (
                      <button key={c} type="button" className={`tag-swatch-btn${t.color === c ? " is-on" : ""}`} disabled={busy}
                              style={{ background: c }} aria-label={`Colour ${c}`} aria-pressed={t.color === c}
                              onClick={() => pickColor(t.name, c)} />
                    ))}
                    <button type="button" className="btn-ghost" disabled={busy || !t.color} onClick={() => pickColor(t.name, null)}>
                      No colour
                    </button>
                  </div>
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
          title={confirm?.type === "merge" ? `Merge “${confirm.name}” into “${confirm.to}”?`
            : confirm?.type === "unused" ? `Delete ${confirm.names.length} unused tag${confirm.names.length === 1 ? "" : "s"}?`
            : `Delete the tag “${confirm?.name}”?`}
          confirmLabel={confirm?.type === "merge" ? "Merge" : confirm?.type === "unused" ? "Delete unused tags" : "Delete tag"}
          onConfirm={() => (confirm.type === "merge" ? doRename(confirm.name, confirm.to)
            : confirm.type === "unused" ? doDeleteUnused() : doDelete())}
          onCancel={() => setConfirm(null)}
        >
          {confirm?.type === "unused" ? (
            <p>
              {confirm.names.slice(0, 12).map(n => `“${n}”`).join(", ")}
              {confirm.names.length > 12 ? ` and ${confirm.names.length - 12} more` : ""}{" "}
              {confirm.names.length === 1 ? "is" : "are"} on no post, not even one in the trash. Tags only on trashed posts
              are kept: they come back with them.
            </p>
          ) : confirm?.type === "merge" ? (
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
    </>
  );
}
