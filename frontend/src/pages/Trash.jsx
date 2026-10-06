import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { checkTrash, getPeople, getTrashItems, purgeTrash, restoreEntries } from "../lib/api";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { useSelection } from "../lib/useSelection";
import { useApi } from "../lib/useApi";
import { fmtAgo, fmtBytes, fmtFullDate, fmtInt, platformLabel, postPath } from "../lib/fmt";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";
import SelectionBar from "../components/SelectionBar";
import PageHeader from "../components/PageHeader";

const PAGE = 60;
const MAX_PAGE = 500;      // the backend's limit; a reload refetches what was loaded

// "Deleted when" filter → the `since` / `before` parameters, in Unix seconds.
const WHEN = [
  { value: "",      label: "Any time" },
  { value: "today", label: "Today" },
  { value: "week",  label: "Last 7 days" },
  { value: "older", label: "More than 7 days ago" },
];

function rangeFor(when, now = Date.now()) {
  const weekAgo = Math.floor(now / 1000) - 7 * 86400;
  if (when === "today") {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return { since: Math.floor(d.getTime() / 1000) };
  }
  if (when === "week")  return { since: weekAgo };
  if (when === "older") return { before: weekAgo };
  return {};
}

// The creator filter's value: author ids and handles are only unique within
// a platform, so the platform travels with them ("instagram:123").
const authorValue = a => `${a.platform}:${a.id || a.handle}`;

// "from @someone, deleted today" for the filters in effect.
function filterText(authors, author, when, person) {
  const parts = [];
  if (person) parts.push(`from ${person.name}`);
  else if (author) {
    const a = authors?.find(x => authorValue(x) === author);
    parts.push(a ? `from @${a.handle || a.id}` : "from this creator");
  }
  const w = WHEN.find(x => x.value === when);
  if (when && w) parts.push(`deleted ${w.label.toLowerCase()}`);
  return parts.join(", ");
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
          {e.copy && <span className="trash-badge" title="An extra copy of a post, trashed from Duplicates; restoring puts it back as a copy">copy</span>}
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
            <Link to={postPath(e)} className="big-file-author" title={`${who} · open the rest of the post`} onClick={ev => selectMode && ev.preventDefault()}>{who}</Link>
          ) : (
            <span className="big-file-author" title={e.post && e.post !== who ? `${who} · ${e.post}` : who}>{who}</span>
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
  const { data: peopleData } = useApi(getPeople, 0);
  const [params, setParams] = useSearchParams();
  const author = params.get("author") || "";
  const person = /^\d+$/.test(params.get("person") || "") ? params.get("person") : "";
  const when   = WHEN.some(w => w.value === params.get("when")) ? params.get("when") : "";
  const filterKey = `${author}|${person}|${when}`;

  const [result,  setResult]  = useState(null);     // the last /api/trash/items answer, entries accumulated
  const [loaded,  setLoaded]  = useState(null);     // filterKey the result belongs to
  const [error,   setError]   = useState(null);
  const [tick,    setTick]    = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);

  // The range is fixed when a filter's first page loads, so later pages and
  // a bulk purge mean exactly what the user saw: `upto` is the newest
  // deletion that list had (the server's stamp, not this browser's clock).
  const [range, setRange] = useState(null);       // { key, since, before, upto }
  const rangeNow = useCallback(() => ({ key: filterKey, ...rangeFor(when) }), [filterKey, when]);
  const [platform, authorId] = author.includes(":") ? [author.slice(0, author.indexOf(":")), author.slice(author.indexOf(":") + 1)] : ["", ""];
  const fetchPage = useCallback((offset, limit, r) => getTrashItems({
    platform, author: authorId, person: person || undefined, since: r.since, before: r.before, upto: r.upto, offset, limit,
  }), [platform, authorId, person]);

  // First page on a new filter; on a reload (after restore or purge) as many
  // entries as were on screen, so the page does not jump back to the top.
  const shown = loaded === filterKey ? result?.entries.length ?? 0 : 0;
  useEffect(() => {
    let alive = true;
    const r0 = rangeNow();
    fetchPage(0, Math.min(MAX_PAGE, Math.max(PAGE, shown)), r0).then(
      r  => { if (alive) { setResult(r); setLoaded(filterKey); setRange({ ...r0, upto: r.upto }); setError(null); } },
      e  => { if (alive) setError(e); },
    );
    return () => { alive = false; };
    // `shown` is read, not watched: loading more must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchPage, rangeNow, filterKey, refreshKey, tick]);
  const reload = () => setTick(t => t + 1);

  async function loadMore() {
    setLoadingMore(true);
    try {
      const r = await fetchPage(result.entries.length, PAGE, range);
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
  const [bulk,      setBulk]      = useState(null);     // { total, files, bytes, filter } awaiting confirmation
  const sel = useSelection(items, { resetKey: filterKey, escapeBlocked: confirm || !!bulk });
  // Nothing left to select (all restored or purged): leave select mode.
  // Adjusting state during render, not in an effect.
  if (sel.active && current && result.trash.entries === 0) sel.exit();
  const selBytes = sel.selectedItems.reduce((n, e) => n + (e.bytes || 0), 0);
  const selFiles = sel.selectedItems.reduce((n, e) => n + (e.files || 0), 0);

  function drop(keys) {
    const gone = new Set(keys);
    setResult(prev => prev && ({ ...prev, entries: prev.entries.filter(e => !gone.has(e.key)) }));
    sel.drop(keys);
  }

  // Entries are measured as they are shown; this looks at every file now,
  // so the totals and "missing" badges catch files moved out by hand.
  async function checkFiles() {
    setBusy(true);
    try {
      const r = await checkTrash();
      toast(r.missing
        ? `Checked ${plural(r.entries, "entry", "entries")}: ${fmtInt(r.missing)} with files missing, marked "missing".`
        : `Checked ${plural(r.entries, "entry", "entries")}: no file is missing.`);
      reload();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setBusy(false);
    }
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

  // Everything the filters match, not just what is loaded. Nothing deleted
  // after the list was loaded goes with it: `upto` is that list's.
  function askBulk() {
    const filter = { upto: range.upto };
    if (range.before != null) filter.before = range.before;
    if (authorId) Object.assign(filter, { platform, author: authorId });
    if (person) filter.person = Number(person);
    if (range.since != null) filter.since = range.since;
    setDlgError(null);
    setBulk({ total: result.total, files: result.files, bytes: result.bytes, filter });
  }

  async function runPurge() {
    setBusy(true);
    setDlgError(null);
    setErrors(null);
    try {
      const r = await purgeTrash(bulk ? { filter: bulk.filter } : { keys: sel.selectedItems.map(e => e.key) });
      if (!r?.ok) { setDlgError(r?.error || "Nothing could be deleted."); return; }
      setConfirm(false);
      setBulk(null);
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
    <PageHeader
      title="Trash"
      sub={trash && (
        <span className="page-count" title="Everything in the trash folders, whatever the filters">
          {plural(trash.entries, "entry", "entries")} · {plural(trash.files, "file")} · {fmtBytes(trash.bytes)}
        </span>
      )}
      actions={<>
        {trash?.entries > 0 && !sel.active && (
          <button type="button" className="btn-secondary" onClick={checkFiles} disabled={busy}
            title="Look at every trashed file now. The list checks the entries it shows; this finds files moved or deleted by hand anywhere in the trash">
            <Icon name="search" size={14} />Check for missing files
          </button>
        )}
        {entries.length > 0 && !sel.active && (
          <button type="button" className="btn-secondary" onClick={sel.enter}>
            <Icon name="check" size={14} />Select
          </button>
        )}
        {sel.active && (
          <button type="button" className="btn-secondary" onClick={sel.exit}>Done</button>
        )}
      </>}
    />
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
  const authorKnown = !author || authors.some(a => authorValue(a) === author);
  const people = (peopleData || []).filter(p => p.accounts.length);
  const shownPerson = person ? people.find(p => String(p.id) === person) || { id: person, name: `person ${person}` } : null;
  const creator = person ? `person:${person}` : authorKnown ? author : "__unknown";
  const anyFilter = author || person || when;

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
              <select className="sort-select" value={creator} onChange={e => {
                const v = e.target.value;
                setParam(v.startsWith("person:") ? { person: v.slice(7), author: "" } : { author: v, person: "" });
              }}>
                <option value="">All</option>
                {!authorKnown && !person && <option value="__unknown" disabled>{authorId}</option>}
                {shownPerson && !people.some(p => String(p.id) === person) && (
                  <option value={`person:${person}`} disabled>{shownPerson.name}</option>
                )}
                {people.length > 0 && (
                  <optgroup label="People">
                    {people.map(p => <option key={p.id} value={`person:${p.id}`}>{p.name}</option>)}
                  </optgroup>
                )}
                <optgroup label="Accounts">
                  {authors.map(a => (
                    <option key={authorValue(a)} value={authorValue(a)}>
                      @{a.handle || a.id} · {platformLabel(a.platform)} ({a.entries}, {fmtBytes(a.bytes)})
                    </option>
                  ))}
                </optgroup>
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
            {current && anyFilter && result.total > 0 && (
              <button type="button" className="btn-danger-soft" onClick={askBulk} disabled={busy}
                title="Permanently delete every entry these filters match, loaded or not">
                <Icon name="trash" size={14} />Delete all {fmtInt(result.total)} forever…
              </button>
            )}
          </div>

          <div className="card">
            <DeleteErrors errors={errors?.list} summary={errors?.summary} onDismiss={() => setErrors(null)} />
            {current && entries.length === 0 ? (
              <div className="empty">
                Nothing in the trash {filterText(authors, author, when, shownPerson)}.{" "}
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
        open={confirm || !!bulk}
        danger
        busy={busy}
        error={dlgError}
        title={`Delete ${plural(bulk ? bulk.total : sel.count, "entry", "entries")} forever?`}
        confirmLabel={`Delete ${fmtBytes(bulk ? bulk.bytes : selBytes)} forever`}
        onConfirm={runPurge}
        onCancel={() => { setConfirm(false); setBulk(null); }}
      >
        <p>
          This <strong>permanently deletes {bulk ? "up to " : ""}{plural(bulk ? bulk.files : selFiles, "file")} ({fmtBytes(bulk ? bulk.bytes : selBytes)})</strong> of{" "}
          {plural(bulk ? bulk.total : sel.count, "trashed entry", "trashed entries")}
          {bulk ? `, every one the filters match (${filterText(result?.authors, author, when, shownPerson)})` : ""} from
          disk. They do not go to the system trash, and this cannot be undone.
          {bulk ? " Entries off the page count as last measured: a file removed by hand since is not freed again." : ""}
        </p>
      </ConfirmDialog>
    </div>
  );
}
