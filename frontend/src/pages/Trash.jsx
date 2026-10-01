import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getTrashItems, purgeTrash, restoreEntries } from "../lib/api";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { useSelection } from "../lib/useSelection";
import { fmtAgo, fmtBytes, fmtFullDate, fmtInt, platformLabel, postPath } from "../lib/fmt";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";
import SelectionBar from "../components/SelectionBar";

const PAGE = 60;
const MAX_PAGE = 500;      // the backend's limit; a reload refetches what was loaded

// "Deleted when" filter → the `since` parameter, in Unix seconds.
const WHEN = [
  { value: "",      label: "Any time" },
  { value: "today", label: "Today" },
  { value: "week",  label: "Last 7 days" },
];

function sinceFor(when, now = Date.now()) {
  if (when === "today") {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return Math.floor(d.getTime() / 1000);
  }
  if (when === "week") return Math.floor(now / 1000) - 7 * 86400;
  return undefined;
}

const plural = (n, word, many = `${word}s`) => `${fmtInt(n)} ${n === 1 ? word : many}`;

function itemsLabel(e) {
  if (!e.partial) return null;
  return e.of ? `${e.items} of ${e.of} items` : `${plural(e.items, "item")} from the post`;
}

function TrashEntry({ entry: e, index, selectMode, selected, onToggle, onRestore, busy }) {
  const [broken, setBroken] = useState(false);
  const who = e.author?.handle ? `@${e.author.handle}` : e.post_id || e.post || "?";
  const partial = itemsLabel(e);
  const deleted = `deleted ${fmtAgo(e.at)}`;
  const label = `${who}, ${partial ? `${partial}, ` : ""}${deleted}, ${fmtBytes(e.bytes)}`;

  function toggle(ev) {
    ev.stopPropagation();
    onToggle(index, ev.shiftKey);
  }

  return (
    <div
      className={`big-file trash-entry${selectMode ? " is-selecting" : ""}${selected ? " is-selected" : ""}`}
      style={{ animationDelay: `${Math.min(index % PAGE, 40) * 18}ms` }}
      onClick={selectMode ? toggle : undefined}
    >
      <div className="big-file-link">
        {e.thumb_url && !broken ? (
          <img src={e.thumb_url} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          <span className="big-file-ph"><Icon name={e.kind === "video" ? "play" : "image"} size={26} /></span>
        )}
        {selectMode && (
          <button type="button" className="select-check" role="checkbox" aria-checked={selected} aria-label={`Select ${label}`} onClick={toggle}>
            {selected && <Icon name="check" size={14} />}
          </button>
        )}
        <span className="trash-badges">
          {partial && <span className="trash-badge" title="Only part of the post was deleted; the rest is still in the feed">{partial}</span>}
          {e.missing && (
            <span className="trash-badge trash-badge-missing" title="A file of this entry is no longer in the trash folder (moved or deleted by hand)">
              <Icon name="warn" size={11} />missing
            </span>
          )}
        </span>
        <span className="big-file-size mono">{fmtBytes(e.bytes)}</span>
      </div>
      <div className="big-file-foot">
        <span className="trash-entry-meta">
          {partial && e.platform && e.post_id ? (
            <Link to={postPath(e)} className="big-file-author" title="Open the rest of the post" onClick={ev => selectMode && ev.preventDefault()}>{who}</Link>
          ) : (
            <span className="big-file-author" title={e.post || ""}>{who}</span>
          )}
          <span className="trash-entry-when" title={`Deleted ${fmtFullDate(e.at)}${e.posted_at ? ` · posted ${fmtFullDate(e.posted_at)}` : ""}`}>
            {deleted}
          </span>
        </span>
        {!selectMode && (
          <button
            type="button"
            className="icon-btn big-file-trash"
            onClick={() => onRestore(e)}
            disabled={busy}
            title="Restore to where it was"
            aria-label={`Restore ${label}`}
          >
            <Icon name="undo" size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

export default function Trash() {
  const { refreshKey } = useScan();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const author = params.get("author") || "";
  const when   = WHEN.some(w => w.value === params.get("when")) ? params.get("when") : "";
  const filterKey = `${author}|${when}`;

  const [result,  setResult]  = useState(null);     // the last /api/trash/items answer, entries accumulated
  const [loaded,  setLoaded]  = useState(null);     // filterKey the result belongs to
  const [error,   setError]   = useState(null);
  const [tick,    setTick]    = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);

  const fetchPage = useCallback((offset, limit) => {
    const since = sinceFor(when);
    return getTrashItems({ author, since, offset, limit });
  }, [author, when]);

  // First page on a new filter; on a reload (after restore or purge) as many
  // entries as were on screen, so the page does not jump back to the top.
  const shown = loaded === filterKey ? result?.entries.length ?? 0 : 0;
  useEffect(() => {
    let alive = true;
    fetchPage(0, Math.min(MAX_PAGE, Math.max(PAGE, shown))).then(
      r  => { if (alive) { setResult(r); setLoaded(filterKey); setError(null); } },
      e  => { if (alive) setError(e); },
    );
    return () => { alive = false; };
    // `shown` is read, not watched: loading more must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchPage, filterKey, refreshKey, tick]);
  const reload = () => setTick(t => t + 1);

  async function loadMore() {
    setLoadingMore(true);
    try {
      const r = await fetchPage(result.entries.length, PAGE);
      setResult(prev => ({ ...r, entries: [...prev.entries, ...r.entries.filter(e => !prev.entries.some(p => p.key === e.key))] }));
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setLoadingMore(false);
    }
  }

  const current = loaded === filterKey;
  const entries = (current && result?.entries) || [];
  const items = entries.map(e => ({ ...e, id: e.key }));

  /* ── Actions ─────────────────────────────────────────── */
  const [busy,      setBusy]      = useState(false);
  const [confirm,   setConfirm]   = useState(false);
  const [dlgError,  setDlgError]  = useState(null);
  const [errors,    setErrors]    = useState(null);     // { list, summary }
  const sel = useSelection(items, { resetKey: filterKey, escapeBlocked: confirm });
  const selBytes = sel.selectedItems.reduce((n, e) => n + (e.bytes || 0), 0);
  const selFiles = sel.selectedItems.reduce((n, e) => n + (e.files || 0), 0);

  function drop(keys) {
    const gone = new Set(keys);
    setResult(prev => prev && ({ ...prev, entries: prev.entries.filter(e => !gone.has(e.key)) }));
    sel.drop(keys);
  }

  async function restore(list) {
    setBusy(true);
    setErrors(null);
    try {
      const r = await restoreEntries(list.map(e => e.key));
      if (!r?.ok) { toast(r?.error || "Could not restore.", "err"); return; }
      if (r.errors?.length) {
        setErrors({ list: r.errors, summary: n => `${plural(n, "file")} could not be restored. They stay in the trash.` });
      } else {
        drop(list.map(e => e.key));
      }
      if (r.files) {
        toast(`Restored ${plural(r.posts?.length ?? 0, "post")} (${plural(r.files, "file")}). ${
          (r.posts?.length ?? 0) === 1 ? "It is" : "They are"} back in the feed.`);
      }
      reload();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function runPurge() {
    const list = sel.selectedItems;
    setBusy(true);
    setDlgError(null);
    setErrors(null);
    try {
      const r = await purgeTrash(list.map(e => e.key));
      if (!r?.ok) { setDlgError(r?.error || "Nothing could be deleted."); return; }
      setConfirm(false);
      drop(r.keys || []);
      if (r.errors?.length) {
        setErrors({ list: r.errors, summary: n => `${plural(n, "file")} could not be deleted. Their entries stay in the trash.` });
      }
      toast(`Deleted ${plural(r.files ?? 0, "file")} for good, freed ${fmtBytes(r.bytes ?? 0)}.${
        r.dropped ? ` Dropped ${plural(r.dropped, "missing file")} from the list.` : ""}`);
      reload();
    } catch (e) {
      setDlgError(e.message);
    } finally {
      setBusy(false);
    }
  }

  /* ── Render ──────────────────────────────────────────── */
  function setParam(changes) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v); else next.delete(k);
    }
    setParams(next, { replace: true });
  }

  const trash = result?.trash;
  const head = (
    <div className="page-head">
      <h2 className="page-title">Trash</h2>
      {trash && (
        <span className="page-count" title="Everything in the trash folders, whatever the filters">
          {plural(trash.entries, "entry", "entries")} · {plural(trash.files, "file")} · {fmtBytes(trash.bytes)}
        </span>
      )}
      <div className="page-head-spacer" />
      {entries.length > 0 && !sel.active && (
        <button type="button" className="btn-secondary" onClick={sel.enter}>
          <Icon name="check" size={14} />Select
        </button>
      )}
      {sel.active && (
        <button type="button" className="btn-secondary" onClick={sel.exit}>Done</button>
      )}
    </div>
  );

  if (!result) {
    return (
      <div className="trash-page">
        {head}
        <div className="card">
          {error ? (
            <div className="empty">
              Could not load the trash: {error.message}{" "}
              <button type="button" className="btn-link" onClick={reload}>Retry</button>
            </div>
          ) : <div className="empty">Loading…</div>}
        </div>
      </div>
    );
  }

  const authors = result.authors || [];
  const authorKnown = !author || authors.some(a => (a.id || a.handle) === author);
  const anyFilter = author || when;

  return (
    <div className="trash-page">
      {head}
      <p className="page-lede">
        Deleted posts and items wait here, in a <code>.feedvault-trash</code> folder inside their media root,
        until you delete them for good. Restoring puts the files back where they were and the posts back in
        the feed. Emptying the whole trash is in <Link to="/settings" className="text-link">Settings</Link>.
      </p>
      {error && <div className="msg err" role="alert">Could not refresh: {error.message}</div>}

      {trash.entries === 0 ? (
        <div className="card">
          <div className="empty">
            The trash is empty. Posts you delete from the <Link to="/" className="text-link">Feed</Link>,{" "}
            <Link to="/review" className="text-link">Review</Link> or <Link to="/storage" className="text-link">Storage</Link> wait
            here until you delete them for good.
          </div>
        </div>
      ) : (
        <>
          <div className="feed-filters" role="group" aria-label="Filters">
            <label className="filter filter-author">
              <span>Creator</span>
              <select className="sort-select" value={authorKnown ? author : "__unknown"} onChange={e => setParam({ author: e.target.value })}>
                <option value="">All</option>
                {!authorKnown && <option value="__unknown" disabled>{author}</option>}
                {authors.map(a => (
                  <option key={`${a.platform}:${a.id || a.handle}`} value={a.id || a.handle}>
                    @{a.handle || a.id} · {platformLabel(a.platform)} ({a.entries}, {fmtBytes(a.bytes)})
                  </option>
                ))}
              </select>
            </label>
            <label className="filter">
              <span>Deleted</span>
              <select className="sort-select" value={when} onChange={e => setParam({ when: e.target.value })}>
                {WHEN.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
              </select>
            </label>
            {anyFilter && (
              <button type="button" className="btn-ghost filter-clear" onClick={() => setParams(new URLSearchParams(), { replace: true })}>
                <Icon name="close" size={12} /> clear filters
              </button>
            )}
            <div className="page-head-spacer" />
            {current && anyFilter && (
              <span className="page-count trash-match" title="What the filters match">
                {plural(result.total, "entry", "entries")} · {fmtBytes(result.bytes)}
              </span>
            )}
          </div>

          <div className="card">
            <DeleteErrors errors={errors?.list} summary={errors?.summary} onDismiss={() => setErrors(null)} />
            {current && entries.length === 0 ? (
              <div className="empty">
                Nothing deleted {when === "today" ? "today" : when === "week" ? "in the last 7 days" : ""}
                {author ? " by this creator" : ""}.{" "}
                <button type="button" className="btn-link" onClick={() => setParams(new URLSearchParams(), { replace: true })}>Show everything</button>
              </div>
            ) : (
              <div className={`big-files${current ? "" : " is-stale"}`} aria-busy={!current}>
                {items.map((e, i) => (
                  <TrashEntry
                    key={e.key}
                    entry={e}
                    index={i}
                    selectMode={sel.active}
                    selected={sel.isSelected(e.key)}
                    onToggle={sel.toggle}
                    onRestore={entry => restore([entry])}
                    busy={busy}
                  />
                ))}
              </div>
            )}
            {current && entries.length < result.total && (
              <div className="feed-more">
                <button type="button" className="btn-secondary" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? "Loading…" : `Load more (${fmtInt(result.total - entries.length)} left)`}
                </button>
              </div>
            )}
          </div>

          {sel.active && (
            <SelectionBar selection={sel} loaded={entries.length}>
              {sel.count > 0 && <span className="select-size mono" title="Files of the selected entries">{fmtBytes(selBytes)}</span>}
              <button type="button" className="btn-keep" onClick={() => restore(sel.selectedItems)} disabled={!sel.count || busy}>
                <Icon name="undo" size={14} />Restore
              </button>
              <button type="button" className="btn-danger" onClick={() => { setDlgError(null); setConfirm(true); }} disabled={!sel.count || busy}>
                <Icon name="trash" size={14} />Delete forever…
              </button>
            </SelectionBar>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirm}
        danger
        busy={busy}
        error={dlgError}
        title={`Delete ${plural(sel.count, "entry", "entries")} forever?`}
        confirmLabel={`Delete ${fmtBytes(selBytes)} forever`}
        onConfirm={runPurge}
        onCancel={() => setConfirm(false)}
      >
        <p>
          This <strong>permanently deletes {plural(selFiles, "file")} ({fmtBytes(selBytes)})</strong> of{" "}
          {plural(sel.count, "trashed entry", "trashed entries")} from disk. They do not go to the system trash,
          and this cannot be undone.
        </p>
      </ConfirmDialog>
    </div>
  );
}
