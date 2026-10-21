import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { checkTrash, getPeople, getTrashHistory, getTrashItems, purgeTrash, restoreEntries } from "../lib/api";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { useJobs } from "../lib/jobs";
import { useSelection } from "../lib/useSelection";
import { useApi } from "../lib/useApi";
import { fmtAgo, fmtBytes, fmtClock, fmtDay, fmtFullDate, fmtInt, platformLabel, postPath } from "../lib/fmt";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";
import SelectionBar from "../components/SelectionBar";
import PageHeader from "../components/PageHeader";

const PAGE = 60;
const MAX_PAGE = 500;      // the backend's limit; a reload refetches what was loaded
const HISTORY_MAX = 200;   // the History's

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

// What a restore says besides the counts: where posts went when their
// folder was gone, and posts whose earlier review decision is not known
// (trashed before decisions were kept with them).
function restoreNotes(r) {
  const notes = [];
  const folders = list => [...new Set(list)].join(", ");
  if (r.moved?.length) {
    notes.push(`Their folder was gone: put back in the account's folder now (${folders(r.moved.map(m => m.to))}).`);
  }
  if (r.recreated?.length) {
    notes.push(`Their folder was gone and was made again (${folders(r.recreated.map(m => m.folder))}).`);
  }
  const unknown = r.decision_unknown?.length ?? 0;
  if (unknown) {
    notes.push(`Earlier review decision not known for ${plural(unknown, "post")} (trashed by an older FeedVault).`);
  }
  return notes.length ? ` ${notes.join(" ")}` : "";
}

// The toast a restore that put files back shows; a single post gets a link to it.
function restoredToast(r, entries) {
  const n = r.posts?.length ?? 0;
  const one = n === 1 ? entries.find(e => e.post === r.posts[0] && e.platform && e.post_id) : null;
  return [
    `Restored ${plural(n, "post")} (${plural(r.files, "file")}). ${n === 1 ? "It is" : "They are"} back in the feed.${restoreNotes(r)}`,
    "ok",
    one ? { to: postPath(one), label: "Open post" } : undefined,
  ];
}

function itemsLabel(e) {
  if (!e.partial) return null;
  return e.of ? `${e.items} of ${e.of} items` : `${plural(e.items, "item")} from the post`;
}

const whoOf = e => (e.author?.handle ? `@${e.author.handle}` : e.post_id || e.post || "?");

// The two views, in the URL (?view=history) so a link or Back lands on the same one.
const VIEWS = [
  { value: "",        label: "In the trash" },
  { value: "history", label: "History" },
];

function ViewTabs({ view }) {
  const [, setParams] = useSearchParams();
  return (
    <div className="feed-filters trash-views" role="tablist" aria-label="Trash view">
      {VIEWS.map(v => (
        <button
          key={v.value}
          type="button"
          role="tab"
          aria-selected={view === v.value}
          className={`btn-secondary select-toggle${view === v.value ? " is-on" : ""}`}
          // The other view's filters mean nothing here: each starts clean.
          onClick={() => view !== v.value && setParams(v.value ? { view: v.value } : {}, { replace: true })}
        >
          {v.label}
        </button>
      ))}
    </div>
  );
}

export default function Trash() {
  const [params] = useSearchParams();
  const view = params.get("view") === "history" ? "history" : "";
  const tabs = <ViewTabs view={view} />;
  return view === "history" ? <TrashHistory tabs={tabs} /> : <TrashContents tabs={tabs} />;
}

/* ── In the trash ──────────────────────────────────────── */

// ``state``: "restoring" while its restore runs (the rest of the page stays
// usable), "restored" once back: it stays listed, with a link to the post,
// until the list is next loaded.
function TrashEntry({ entry: e, index, selectMode, selected, onToggle, onRestore, busy, state }) {
  const [broken, setBroken] = useState(false);
  const who = whoOf(e);
  const partial = itemsLabel(e);
  const deleted = `deleted ${fmtAgo(e.at)}`;
  const label = `${who}, ${partial ? `${partial}, ` : ""}${deleted}, ${fmtBytes(e.bytes)}`;
  const selectable = selectMode && !state;

  function toggle(ev) {
    ev.stopPropagation();
    onToggle(index, ev.shiftKey);
  }

  return (
    <div
      className={`big-file trash-entry${selectable ? " is-selecting" : ""}${selected ? " is-selected" : ""}${state ? ` is-${state}` : ""}`}
      style={{ animationDelay: `${Math.min(index % PAGE, 40) * 18}ms` }}
      onClick={selectable ? toggle : undefined}
      aria-busy={state === "restoring" || undefined}
    >
      <div className="big-file-link">
        {e.thumb_url && !broken ? (
          <img src={e.thumb_url} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          <span className="big-file-ph"><Icon name={e.kind === "video" ? "play" : "image"} size={26} /></span>
        )}
        {selectable && (
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
        {state && (
          <span className="trash-entry-state" role="status">
            {state === "restoring"
              ? <><Icon name="refresh" size={13} className="spin" />Restoring…</>
              : <><Icon name="check" size={13} />Restored</>}
          </span>
        )}
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
        {state === "restored" ? (
          e.platform && e.post_id && (
            <Link to={postPath(e)} className="icon-btn big-file-trash" title="Open the post" aria-label={`Open ${who}'s post`}>
              <Icon name="external" size={14} />
            </Link>
          )
        ) : !selectMode && (
          <button
            type="button"
            className="icon-btn big-file-trash"
            onClick={() => onRestore(e)}
            disabled={busy || state === "restoring"}
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

function TrashContents({ tabs }) {
  const { refreshKey } = useScan();
  const toast = useToast();
  const { started } = useJobs();
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
  const [states,  setStates]  = useState({});       // entry key -> "restoring" | "restored"

  // The range is fixed when a filter's first page loads, so later pages and
  // a bulk purge mean exactly what the user saw: `upto` is the newest
  // deletion that list had (the server's stamp, not this browser's clock).
  const [range, setRange] = useState(null);       // { key, since, before, upto }
  const rangeNow = useCallback(() => ({ key: filterKey, ...rangeFor(when) }), [filterKey, when]);
  const [platform, authorId] = author.includes(":") ? [author.slice(0, author.indexOf(":")), author.slice(author.indexOf(":") + 1)] : ["", ""];
  const fetchPage = useCallback((offset, limit, r) => getTrashItems({
    platform, author: authorId, person: person || undefined, since: r.since, before: r.before, upto: r.upto, offset, limit,
  }), [platform, authorId, person]);

  // First page on a new filter; on a reload (after a purge or a check) as
  // many entries as were on screen, so the page does not jump back to the top.
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

  const current = loaded === filterKey;
  const entries = (current && result?.entries) || [];
  // Restored entries stay on screen but are no longer in the trash: the
  // next page starts after the ones that are.
  const inTrash = entries.filter(e => states[e.key] !== "restored");

  async function loadMore() {
    setLoadingMore(true);
    try {
      const r = await fetchPage(inTrash.length, PAGE, range);
      setResult(prev => ({ ...r, entries: [...prev.entries, ...r.entries.filter(e => !prev.entries.some(p => p.key === e.key))] }));
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setLoadingMore(false);
    }
  }

  // What select mode can pick: not an entry being restored or back already.
  const items = entries.filter(e => !states[e.key]).map(e => ({ ...e, id: e.key }));
  const indexOf = new Map(items.map((e, i) => [e.key, i]));

  /* ── Actions ─────────────────────────────────────────── */
  const [busy,      setBusy]      = useState(false);
  const [confirm,   setConfirm]   = useState(false);
  const [dlgError,  setDlgError]  = useState(null);
  const [errors,    setErrors]    = useState(null);     // { list, summary }
  const [bulk,      setBulk]      = useState(null);     // { total, files, bytes, filter } awaiting confirmation
  const sel = useSelection(items, { resetKey: filterKey, escapeBlocked: confirm || !!bulk });
  const restoring = Object.values(states).includes("restoring");
  // Nothing left to select (all restored or purged): leave select mode.
  // Adjusting state during render, not in an effect.
  if (sel.active && current && items.length === 0 && !restoring) sel.exit();
  const selBytes = sel.selectedItems.reduce((n, e) => n + (e.bytes || 0), 0);
  const selFiles = sel.selectedItems.reduce((n, e) => n + (e.files || 0), 0);

  function drop(keys) {
    const gone = new Set(keys);
    setResult(prev => prev && ({ ...prev, entries: prev.entries.filter(e => !gone.has(e.key)) }));
    sel.drop(keys);
  }

  function setState(keys, state) {
    setStates(prev => {
      const next = { ...prev };
      for (const k of keys) {
        if (state) next[k] = state; else delete next[k];
      }
      return next;
    });
  }

  // Entries just restored leave the counts here, not by loading the whole
  // list again (up to 500 entries). Only those still listed: a list loaded
  // since the restore counts without them already.
  function countOut(keys) {
    setResult(prev => {
      const gone = prev?.entries.filter(e => keys.has(e.key)) ?? [];
      if (!gone.length) return prev;
      const sum = f => gone.reduce((n, e) => n + (e[f] || 0), 0);
      const files = sum("files"), bytes = sum("bytes"), n = gone.length;
      const authors = (prev.authors || []).map(a => {
        const mine = gone.filter(e => e.platform === a.platform && (e.author?.id || e.author?.handle) === (a.id || a.handle));
        return mine.length ? { ...a, entries: a.entries - mine.length, bytes: a.bytes - mine.reduce((s, e) => s + (e.bytes || 0), 0) } : a;
      }).filter(a => a.entries > 0 || authorValue(a) === author);
      return {
        ...prev, authors,
        total: prev.total - n, files: prev.files - files, bytes: prev.bytes - bytes,
        trash: { entries: prev.trash.entries - n, files: prev.trash.files - files, bytes: prev.trash.bytes - bytes },
      };
    });
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

  // At once on screen: the entries say "Restoring…" while the rest of the
  // page stays usable, then "Restored" with a link to the post.
  async function restore(list) {
    const keys = list.map(e => e.key);
    setErrors(null);
    setState(keys, "restoring");
    const failed = (which, message) => {
      setState(which.map(e => e.key), null);
      setErrors({
        list: which.map(e => ({ path: `${whoOf(e)} · ${plural(e.files, "file")}`, error: message })),
        summary: n => `${plural(n, "entry", "entries")} could not be restored.`,
      });
    };
    try {
      const r = await restoreEntries(keys);
      if (!r?.ok) { failed(list, r?.error || "Could not restore."); return; }
      if (r.errors?.length) {
        // The errors name files, not entries: a fresh list says which are left.
        setState(keys, null);
        setErrors({ list: r.errors, summary: n => `${plural(n, "file")} could not be restored. They stay in the trash.` });
        reload();
      } else {
        // An entry whose post did not come back was no longer there to
        // restore (deleted for good or restored from another tab).
        const back = list.filter(e => r.posts?.includes(e.post));
        const not = list.filter(e => !back.includes(e));
        setState(back.map(e => e.key), "restored");
        countOut(new Set(back.map(e => e.key)));
        if (not.length) {
          failed(not, "no longer in the trash");
          reload();
        }
      }
      if (r.files) {
        started();   // the new counts (jobs poll) follow now
        toast(...restoredToast(r, list));
      }
    } catch (e) {
      failed(list, e.message);
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
        {items.length > 0 && !sel.active && (
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
        {tabs}
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
      {tabs}
      <p className="page-lede">
        Deleted posts and items wait here, in a <code>.feedvault-trash</code> folder inside their media root,
        until you delete them for good. Restoring puts the files back where they were and the posts back in
        the feed. What was deleted lately, restored or not, is in the{" "}
        <Link to="/trash?view=history" className="text-link">History</Link>. Emptying the whole trash is
        in <Link to="/settings" className="text-link">Settings</Link>.
      </p>
      {error && <div className="msg err" role="alert">Could not refresh: {error.message}</div>}

      {trash.entries === 0 && entries.length === 0 ? (
        <div className="card">
          <DeleteErrors errors={errors?.list} summary={errors?.summary} onDismiss={() => setErrors(null)} />
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
              <button type="button" className="btn-danger-soft" onClick={askBulk} disabled={busy || restoring}
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
                {entries.map((e, i) => (
                  <TrashEntry
                    key={e.key}
                    entry={e}
                    index={indexOf.get(e.key) ?? i}
                    selectMode={sel.active}
                    selected={sel.isSelected(e.key)}
                    onToggle={sel.toggle}
                    onRestore={entry => restore([entry])}
                    busy={busy}
                    state={states[e.key]}
                  />
                ))}
              </div>
            )}
            {current && inTrash.length < result.total && (
              <div className="feed-more">
                <button type="button" className="btn-secondary" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? "Loading…" : `Load more (${fmtInt(result.total - inTrash.length)} left)`}
                </button>
              </div>
            )}
          </div>

          {sel.active && (
            <SelectionBar selection={sel} loaded={items.length}>
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

/* ── History ───────────────────────────────────────────── */

// Where a deletion was asked from, and what put a post back.
const DELETED_FROM = {
  review: "Deleted from Review", feed: "Deleted from the Feed", post: "Deleted from its post page",
  storage: "Deleted from Storage", duplicates: "Deleted from Duplicates",
};
const RESTORED_BY = {
  trash: "Restored from the trash", undo: "Restored by Undo", scan: "Restore finished by a scan",
};

function whatHappened(e) {
  if (e.action === "deleted")  return DELETED_FROM[e.from] || "Deleted";
  if (e.action === "restored") return RESTORED_BY[e.from] || "Restored";
  if (e.action === "emptied")  return "Emptied the trash";
  return e.entries != null ? `Deleted ${plural(e.entries, "entry", "entries")} forever` : "Deleted forever";
}

const STATE = {
  in_trash: { label: "In the trash", title: "Still in the trash: Restore puts it back" },
  restored: { label: "Restored", title: "Back where it was, and in the feed" },
  gone:     { label: "Gone", title: "Deleted for good: nothing left to restore" },
};

function HistoryRow({ event: e, pending, onRestore }) {
  const [broken, setBroken] = useState(false);
  const summary = e.action === "emptied" || e.entries != null;   // counts, no post
  const state = pending === "restored" ? "restored" : e.state;
  const open = e.open || pending === "restored";
  const who = summary ? null : whoOf(e);
  const parts = [
    who,
    summary ? plural(e.entries ?? 0, "entry", "entries") : itemsLabel(e) || (e.items > 1 ? plural(e.items, "item") : null),
    e.copy ? "extra copy" : null,
    e.files ? plural(e.files, "file") : null,
    e.bytes ? fmtBytes(e.bytes) : null,
  ].filter(Boolean);
  // A purge or an empty is the row that says it is gone: no badge to repeat it.
  const badge = e.action === "deleted" || e.action === "restored" ? STATE[state] : null;

  return (
    <li className={`trash-event is-${state}`}>
      <span className="trash-event-thumb">
        {e.thumb_url && !broken && state !== "gone" ? (
          <img src={e.thumb_url} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          <Icon name={summary ? "trash" : e.kind === "video" ? "play" : "image"} size={18} />
        )}
      </span>
      <div className="trash-event-text">
        <div className="trash-event-what">
          <span className="trash-event-title">{whatHappened(e)}</span>
          {badge && <span className={`trash-state trash-state-${state}`} title={badge.title}>{badge.label}</span>}
        </div>
        <div className="trash-event-meta">
          <time dateTime={new Date(e.at * 1000).toISOString()} title={fmtFullDate(e.at)} className="mono">{fmtClock(e.at)}</time>
          {parts.map((p, i) => <span key={i}>{p}</span>)}
        </div>
      </div>
      <div className="trash-event-act">
        {state === "in_trash" && e.restore_key && (
          <button type="button" className="btn-secondary trash-event-btn" onClick={() => onRestore(e)} disabled={pending === "restoring"}
            aria-label={`Restore ${who || "it"}`}>
            {pending === "restoring"
              ? <><Icon name="refresh" size={13} className="spin" />Restoring…</>
              : <><Icon name="undo" size={13} />Restore</>}
          </button>
        )}
        {state === "restored" && open && e.platform && e.post_id && (
          <Link to={postPath(e)} className="btn-secondary trash-event-btn" aria-label={`Open ${who}'s post`}>
            <Icon name="external" size={13} />Open post
          </Link>
        )}
      </div>
    </li>
  );
}

function TrashHistory({ tabs }) {
  const { refreshKey } = useScan();
  const toast = useToast();
  const { started } = useJobs();
  const [data,    setData]    = useState(null);     // the last /api/trash/history answer, events accumulated
  const [error,   setError]   = useState(null);
  const [tick,    setTick]    = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [pending, setPending] = useState({});       // restore key -> "restoring" | "restored"
  const [errors,  setErrors]  = useState(null);

  // As many events as were on screen on a reload (after a restore), so the
  // page stays where it was; the new event comes in at the top.
  const shown = data?.events.length ?? 0;
  useEffect(() => {
    let alive = true;
    getTrashHistory({ offset: 0, limit: Math.min(HISTORY_MAX, Math.max(PAGE, shown)) }).then(
      r => { if (alive) { setData(r); setError(null); } },
      e => { if (alive) setError(e); },
    );
    return () => { alive = false; };
    // `shown` is read, not watched: loading more must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, tick]);

  async function loadMore() {
    setLoadingMore(true);
    try {
      // `upto`: the events logged since the first page do not shift this one.
      const r = await getTrashHistory({ offset: data.events.length, limit: PAGE, upto: data.upto });
      setData(prev => ({ ...prev, events: [...prev.events, ...r.events.filter(e => !prev.events.some(p => p.at_ms === e.at_ms))] }));
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setLoadingMore(false);
    }
  }

  async function restore(e) {
    const key = e.restore_key;
    setErrors(null);
    setPending(p => ({ ...p, [key]: "restoring" }));
    const unpend = () => setPending(p => { const next = { ...p }; delete next[key]; return next; });
    const failed = message => {
      unpend();
      setErrors({ list: [{ path: `${whoOf(e)} · ${plural(e.files, "file")}`, error: message }],
                  summary: () => "Could not restore it." });
    };
    try {
      const r = await restoreEntries([key]);
      if (!r?.ok) { failed(r?.error || "Could not restore."); return; }
      if (r.errors?.length) {
        // Some files came back, some not: the refetch below says where it stands.
        unpend();
        setErrors({ list: r.errors, summary: n => `${plural(n, "file")} could not be restored. They stay in the trash.` });
      } else if (!r.files) {
        failed("no longer in the trash");
      } else {
        setPending(p => ({ ...p, [key]: "restored" }));
      }
      if (r.files) {
        started();
        toast(...restoredToast(r, [e]));
      }
      setTick(t => t + 1);                      // the restore's own event, at the top
    } catch (err) {
      failed(err.message);
    }
  }

  const events = data?.events || [];
  const days = [];
  for (const e of events) {
    const day = fmtDay(e.at);
    if (days.at(-1)?.day !== day) days.push({ day, events: [] });
    days.at(-1).events.push(e);
  }

  return (
    <div className="trash-page">
      <PageHeader
        title="Trash"
        sub={data && (
          <span className="page-count" title="Kept for 90 days, at most 10,000 events">
            {plural(data.total, "event")} · last 90 days
          </span>
        )}
      />
      {tabs}
      <p className="page-lede">
        What was deleted, restored and deleted for good, newest first. A post still in the trash can be
        restored from here; one that is back opens from its row.
      </p>
      <div className="card">
        <DeleteErrors errors={errors?.list} summary={errors?.summary} onDismiss={() => setErrors(null)} />
        {!data ? (
          error ? (
            <div className="empty">
              Could not load the history: {error.message}{" "}
              <button type="button" className="btn-link" onClick={() => setTick(t => t + 1)}>Retry</button>
            </div>
          ) : <div className="empty">Loading…</div>
        ) : events.length === 0 ? (
          <div className="empty">
            Nothing deleted in the last 90 days. What you delete from the <Link to="/" className="text-link">Feed</Link>,{" "}
            <Link to="/review" className="text-link">Review</Link> or <Link to="/storage" className="text-link">Storage</Link> shows
            up here, with whether it was restored.
          </div>
        ) : (
          <>
            {error && <div className="msg err" role="alert">Could not refresh: {error.message}</div>}
            {days.map(d => (
              <section key={d.day} className="trash-day" aria-label={d.day}>
                <h3 className="trash-day-head">{d.day}</h3>
                <ul className="trash-events">
                  {d.events.map(e => (
                    <HistoryRow key={e.at_ms} event={e} pending={e.restore_key ? pending[e.restore_key] : undefined} onRestore={restore} />
                  ))}
                </ul>
              </section>
            ))}
            {events.length < data.total && (
              <div className="feed-more">
                <button type="button" className="btn-secondary" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? "Loading…" : `Load more (${fmtInt(data.total - events.length)} left)`}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
