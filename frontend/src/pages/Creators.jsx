import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { getAuthors, getPeople, getSuggestions, getNew, markSeen, createPerson, mergePeople, linkAccounts, dismissSuggestion, createSource } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useJobs } from "../lib/jobs";
import { useToast } from "../lib/toast";
import { useSelection } from "../lib/useSelection";
import { platformLabel, authorFeedPath, fmtBytes, fmtInt, plural } from "../lib/fmt";
import {
  accountKey, accountRef, accountText, attributeChoices, attributesMatch, countryFlag, countryName, filtersOn, matchedFormer, matches,
  NO_FILTERS, personPath, personText, suggestName,
} from "../lib/people";
import Icon from "../components/Icon";
import PlatformIcon from "../components/PlatformIcon";
import ConfirmDialog from "../components/ConfirmDialog";
import DiscardDialog from "../components/DiscardDialog";
import SelectionBar from "../components/SelectionBar";
import Suggestions from "../components/Suggestions";
import MuteButton from "../components/MuteButton";
import {
  AddSource, RemoveSourceDialog, ScheduleLine, ScriptWarning, SourceOptionsDialog, SourceRow, SourceStatus, SyncAllBar,
  SyncButton,
} from "../components/Sources";
import { sourceName, useSources, useSyncAll } from "../lib/sources";
import { optionsSummary } from "../lib/sourceOptions";
import { warnings } from "../lib/health";
import PageHeader from "../components/PageHeader";
import { useUnsaved } from "../lib/unsaved";
import { useWindow } from "../lib/windowing";
import { Gap } from "../components/WinGap";

const SUGGESTIONS_SHOWN = 4;

// Person names are unique; a default that is taken gets " 2", " 3"…
function freeName(name, people) {
  const taken = new Set(people.map(p => p.name.toLowerCase()));
  if (!taken.has(name.toLowerCase())) return name;
  for (let i = 2; ; i++) if (!taken.has(`${name} ${i}`.toLowerCase())) return `${name} ${i}`;
}

/* A card's sources: which one to show (syncing, else a failed one, else
   the latest sync), its job, and those not syncing already. */
function cardSync(sources, jobOf) {
  if (!sources?.length) return null;
  const jobs = sources.map(jobOf);
  const job = jobs.find(j => j?.state === "running") || jobs.find(Boolean) || null;
  const shown = sources[jobs.indexOf(job)]
    || sources.find(s => s.last_result?.state === "failed")
    || [...sources].sort((a, b) => (b.last_sync_at || 0) - (a.last_sync_at || 0))[0];
  return { shown, job, idle: sources.filter((s, i) => !jobs[i]), sources };
}

// Under the card's name: how the last sync went, and the schedule of a
// source that has one.
function CardSyncStatus({ sync }) {
  if (!sync) return null;
  const scheduled = sync.sources.find(s => s.schedule?.every && s.schedule.every !== "off");
  return (
    <>
      <SourceStatus source={sync.shown} job={sync.job} compact />
      {scheduled && <ScheduleLine source={scheduled} compact />}
    </>
  );
}

// Sync every source of the card that is not syncing already.
function CardSyncButton({ sync, onSync }) {
  if (!sync) return null;
  return (
    <SyncButton
      source={sync.idle[0] || sync.shown}
      job={sync.idle.length ? null : sync.job}
      small
      onSync={() => sync.idle.forEach(onSync)}
    />
  );
}

// Posts indexed since the last "Mark all seen" (docs/API.md "New posts").
function NewBadge({ count }) {
  if (!count) return null;
  return <span className="side-badge new-badge" title="New since you last marked them seen">{fmtInt(count)} new</span>;
}

// "Mark seen" of one card: its new posts stop being new, the others' stay.
function SeenButton({ fresh, name, onSeen }) {
  if (!fresh?.count) return null;
  return (
    <button type="button" className="icon-btn creator-seen" onClick={onSeen}
            title={`Mark ${name}'s ${plural(fresh.count, "new post")} seen`}
            aria-label={`Mark ${name}'s new posts seen`}>
      <Icon name="check" size={15} />
    </button>
  );
}

// A source of the card needs a look: its account is not found, it needs a
// login, or 3+ syncs failed in a row. The title says which and why.
function WarnBadge({ sync }) {
  const why = warnings(sync?.sources, sourceName);
  if (!why.length) return null;
  return (
    <span className="side-badge warn-badge" title={why.join("\n")} role="img"
          aria-label={`Needs a look: ${why.join("; ")}`}>
      <Icon name="warn" size={11} />{why.length > 1 ? fmtInt(why.length) : "check"}
    </span>
  );
}

// A source of the card whose script would fail its next sync: the same
// line as on a source's row, its See Scripts link with it, under the card's
// name (#149: only the badge's title said why). Outside the card's link:
// a link in a link is not allowed. Named when the card has several sources.
function CardScriptWarnings({ sync }) {
  const bad = (sync?.sources || []).filter(s => s.script_warning);
  if (!bad.length) return null;
  return (
    <span className="creator-card-warn">
      {bad.map(s => <ScriptWarning key={s.id} source={s} name={sync.sources.length > 1 ? sourceName(s) : null} />)}
    </span>
  );
}

// A card's labels on one line: the first few, cut to fit, and "+n" for
// the rest (the line's title names them all).
const LABELS_SHOWN = 3;
function CardLabels({ labels }) {
  if (!labels?.length) return null;
  const more = labels.length - LABELS_SHOWN;
  return (
    <span className="person-labels" title={`Labels: ${labels.join(", ")}`}>
      {labels.slice(0, LABELS_SHOWN).map(l => <span key={l} className="card-label">{l}</span>)}
      {more > 0 && <span className="card-label is-more">+{more}</span>}
    </span>
  );
}

function PersonCard({ person: p, index, settled, selectMode, selected, onToggle, sync, onSync, fresh, onSeen, muted, onMuted, flash }) {
  const where = countryName(p.country);
  const body = (
    <>
      <span className="avatar-letter" aria-hidden="true">{(p.name || "?").charAt(0).toUpperCase()}</span>
      <span className="creator-id">
        <span className="creator-name">
          {p.country && <span className="person-flag" role="img" aria-label={where} title={where}>{countryFlag(p.country)}</span>}
          {p.name}
        </span>
        <span className="person-chips">
          {p.accounts.length === 0 && <span className="creator-sub">No account linked</span>}
          {p.accounts.map(a => (
            <span key={accountKey(a)} className="chip platform-chip" title={`${platformLabel(a.platform)} @${a.handle || a.id}`}>
              <PlatformIcon platform={a.platform} size={12} inChip /><span className="chip-text">@{a.handle || a.id}</span>
            </span>
          ))}
        </span>
        <CardLabels labels={p.labels} />
        <CardSyncStatus sync={sync} />
      </span>
      <span className="person-stats">
        <span className="creator-count">{fmtInt(p.count)}</span>
        <span className="creator-sub">{fmtBytes(p.bytes)}</span>
        <NewBadge count={fresh?.count} />
        <WarnBadge sync={sync} />
      </span>
    </>
  );
  return (
    <div
      className={`creator-card person-card${selected ? " is-selected" : ""}${flash ? " is-flash" : ""}`}
      data-index={index}
      style={settled ? undefined : { animationDelay: `${Math.min(index, 30) * 25}ms` }}
    >
      {selectMode ? (
        <button type="button" className="creator-main" aria-pressed={selected} onClick={e => onToggle(e.shiftKey)}>
          {body}
        </button>
      ) : (
        <Link to={personPath(p.id)} className="creator-main" title={`Open ${p.name}`}>{body}</Link>
      )}
      {!selectMode && <SeenButton fresh={fresh} name={p.name} onSeen={onSeen} />}
      {!selectMode && muted != null && <MuteButton compact muted={muted} whom={{ person: p.id }} name={p.name} onDone={onMuted} />}
      {!selectMode && <CardSyncButton sync={sync} onSync={onSync} />}
      <CardScriptWarnings sync={sync} />
    </div>
  );
}

// An account card's sources have no row of their own: one Options icon
// here, which opens the source's options, or with several sources a menu
// to pick one, so the card's name keeps its room.
function CardOptionsButton({ sync, onEdit }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  // A click elsewhere or Escape closes the menu (a clicked button is not
  // focused in every browser, so blur alone would not).
  useEffect(() => {
    if (!open) return undefined;
    const away = e => { if (!wrap.current?.contains(e.target)) setOpen(false); };
    const key = e => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", key); };
  }, [open]);
  if (!sync) return null;
  const label = s => `${sourceName(s)} (${s.tool})`;
  const title = s => {
    if (!sync.idle.includes(s)) return `${label(s)}: its sync is queued or running`;
    const summary = optionsSummary(s);
    return `What ${sourceName(s)} downloads with ${s.tool}${summary ? `: ${summary}` : ""}`;
  };
  const one = sync.sources.length === 1 ? sync.sources[0] : null;
  return (
    <span className="creator-opts-wrap" ref={wrap}
          onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
      <button type="button" className="icon-btn creator-opts"
              // Its sync's end would undo what changes now.
              disabled={one ? !sync.idle.includes(one) : !sync.idle.length}
              onClick={() => (one ? onEdit(one) : setOpen(o => !o))}
              title={one ? title(one) : `Options of its ${sync.sources.length} sources`}
              aria-label={one ? `Options of ${label(one)}` : "Options of its sources"}
              aria-haspopup={one ? undefined : "menu"} aria-expanded={one ? undefined : open}>
        <Icon name="settings" size={15} />
      </button>
      {open && (
        <span className="creator-opts-menu" role="menu">
          {sync.sources.map(s => (
            <button key={s.id} type="button" role="menuitem" className="btn-ghost" disabled={!sync.idle.includes(s)}
                    title={title(s)} onClick={() => { setOpen(false); onEdit(s); }}>
              {label(s)}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}

function AccountCard({ account: a, index, settled, query, selectMode, selected, onToggle, sync, onSync, onEdit, onMake, fresh, onSeen, muted, onMuted, flash }) {
  const former = matchedFormer(a, query);
  const sub = `${a.name && a.name !== a.handle ? `${a.name} · ` : ""}${platformLabel(a.platform)}${former ? ` · was @${former}` : ""}`;
  const body = (
    <>
      <span className="avatar-letter" aria-hidden="true">{(a.handle || a.name || "?").charAt(0).toUpperCase()}</span>
      <span className="creator-id">
        <span className="creator-name">@{a.handle || a.id}</span>
        <span className="creator-sub" title={sub}>{sub}</span>
        <CardSyncStatus sync={sync} />
      </span>
      <span className="person-stats">
        <span className="creator-count">{a.count}</span>
        <NewBadge count={fresh?.count} />
        <WarnBadge sync={sync} />
      </span>
    </>
  );
  const selectable = selectMode && a.id != null;
  return (
    <div
      className={`creator-card${selected ? " is-selected" : ""}${flash ? " is-flash" : ""}`}
      data-index={index}
      style={settled ? undefined : { animationDelay: `${Math.min(index, 30) * 25}ms` }}
    >
      {selectable ? (
        <button type="button" className="creator-main" aria-pressed={selected} onClick={e => onToggle(e.shiftKey)}>
          {body}
        </button>
      ) : (
        <Link to={authorFeedPath(a.platform, a)} className="creator-main" title={`Show posts by @${a.handle}`}>{body}</Link>
      )}
      {a.id != null && !selectMode && (
        // Review, Options under it when the account has a source, and Make
        // creator beside them: a person of its own, in one click.
        <span className="creator-tools">
          <Link
            to={`/review?${new URLSearchParams({ platform: a.platform, author: a.id })}`}
            className="icon-btn creator-review"
            title={`Review @${a.handle}'s unreviewed posts`}
            aria-label={`Review @${a.handle}`}
          >
            <Icon name="review" size={15} />
          </Link>
          <CardOptionsButton sync={sync} onEdit={onEdit} />
          <button type="button" className="icon-btn creator-make" onClick={onMake}
                  title={`Make @${a.handle || a.id} a creator: a person of its own`}
                  aria-label={`Make @${a.handle || a.id} a creator`}>
            <Icon name="userPlus" size={15} />
          </button>
        </span>
      )}
      {!selectMode && <SeenButton fresh={fresh} name={`@${a.handle || a.id}`} onSeen={onSeen} />}
      {!selectMode && a.id != null && muted != null && (
        <MuteButton compact muted={muted} whom={{ account: accountRef(a) }} name={`@${a.handle || a.id}`} onDone={onMuted} />
      )}
      {!selectMode && <CardSyncButton sync={sync} onSync={onSync} />}
      <CardScriptWarnings sync={sync} />
    </div>
  );
}

// "Sync these?": profile folders already downloaded with instaloader, offered
// as sources. Nothing is added until the user says so.
function SourceSuggestions({ list, busy, onAdd, onAddAll }) {
  const [all, setAll] = useState(false);
  if (!list?.length) return null;
  const shown = all ? list : list.slice(0, SUGGESTIONS_SHOWN);
  return (
    <section className="suggestions" aria-label="Source suggestions">
      <div className="suggestions-head">
        <h3 className="card-title">Sync these?</h3>
        <span className="page-count">{fmtInt(list.length)} instaloader folder{list.length === 1 ? "" : "s"} without a source</span>
        <div className="page-head-spacer" />
        {list.length > SUGGESTIONS_SHOWN && (
          <button type="button" className="btn-ghost" onClick={() => setAll(a => !a)}>
            {all ? "Show fewer" : `Show all ${fmtInt(list.length)}`}
          </button>
        )}
        <button type="button" className="btn-secondary" disabled={busy} onClick={onAddAll}>
          <Icon name="plus" size={13} />Add all
        </button>
      </div>
      <ul className="suggestion-list">
        {shown.map(s => (
          <li key={s.folder} className="suggestion source-suggestion">
            <div className="suggestion-accounts">
              <span className="chip platform-chip" title={platformLabel(s.platform)}><PlatformIcon platform={s.platform} size={12} inChip />@{s.target}</span>
              {s.person && <span className="suggestion-into">for <Link to={personPath(s.person.id)} className="text-link">{s.person.name}</Link></span>}
            </div>
            <div className="suggestion-why">
              <code className="source-folder" title={s.folder}>{s.folder}</code>
              <span>{fmtInt(s.count)} posts indexed</span>
            </div>
            <div className="suggestion-actions">
              <button type="button" className="btn-primary" disabled={busy} onClick={() => onAdd(s)}>
                <Icon name="plus" size={14} /> Add
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function suggestionBody(s) {
  return { tool: s.tool, target: s.target, folder: s.folder, account: s.account };
}

// A filter's <select> value: "any", "none" (not set), or "is:" and the value.
const pickValue = v => (v == null ? "any" : v === "" ? "none" : `is:${v}`);
const pickedValue = v => (v === "any" ? null : v === "none" ? "" : v.slice(3));

/* Gender, country and label filters for the people, beside the text
   filter: each offers what the people have (with how many), and "not
   set". One the people give no value for is left out, unless it is on. */
function AttributeFilters({ people, value, onChange }) {
  const c = useMemo(() => attributeChoices(people), [people]);
  const fields = [
    { key: "gender", label: "Gender", any: "Any gender", none: "Not set", list: c.genders, text: v => v },
    { key: "country", label: "Country", any: "Any country", none: "Not set", list: c.countries,
      text: v => `${countryFlag(v)} ${countryName(v)}` },
    { key: "label", label: "Label", any: "Any label", none: "No label", list: c.labels, text: v => v },
  ].filter(f => f.list.length || value[f.key] != null);
  if (!fields.length) return null;
  return (
    <div className="feed-filters creator-filters" role="group" aria-label="Filter people">
      {fields.map(f => {
        const on = value[f.key];
        // The option of the value on, whatever its case (?label=travel shows "Travel").
        const listed = on ? f.list.find(o => o.value.toLowerCase() === on.toLowerCase()) : null;
        return (
          <label key={f.key} className="filter">
            <span>{f.label}</span>
            <select className="sort-select" value={pickValue(listed ? listed.value : on)}
                    onChange={e => onChange({ ...value, [f.key]: pickedValue(e.target.value) })}>
              <option value="any">{f.any}</option>
              {f.list.map(o => <option key={o.value} value={pickValue(o.value)}>{f.text(o.value)} ({fmtInt(o.count)})</option>)}
              {/* A value no one has any more (a label in the address): still shown while on. */}
              {on && !listed && <option value={pickValue(on)}>{f.text(on)} (0)</option>}
              <option value="none">{f.none} ({fmtInt(c.none[f.key])})</option>
            </select>
          </label>
        );
      })}
      {filtersOn(value) && (
        <button type="button" className="btn-ghost filter-clear" onClick={() => onChange(NO_FILTERS)}>Clear filters</button>
      )}
    </div>
  );
}

export default function Creators() {
  const { refreshKey } = useScan();
  const toast = useToast();
  const navigate = useNavigate();
  const [version, setVersion] = useState(0);                 // bumped after a link changes people
  const key = `${refreshKey}:${version}`;
  const authorsApi = useApi(getAuthors, key);
  const peopleApi  = useApi(getPeople, key);
  const suggestApi = useApi(getSuggestions, key);
  // New counts per card, again whenever the jobs poll's total moves.
  const { newCount, started } = useJobs();
  const newApi = useApi(getNew, `${key}:${newCount}`);
  const fresh = useMemo(() => {
    const m = new Map();
    for (const p of newApi.data?.by_person || []) m.set(`person:${p.id}`, p);
    for (const a of newApi.data?.by_account || []) m.set(`account:${accountKey(a)}`, a);
    return m;
  }, [newApi.data]);
  // Who is muted: null until /api/new has answered (no button before).
  const mutedSet = useMemo(() => newApi.data?.muted ? new Set([
    ...newApi.data.muted.people.map(id => `person:${id}`),
    ...newApi.data.muted.accounts.map(a => `account:${accountKey(a)}`),
  ]) : null, [newApi.data]);
  const reloadNew = newApi.reload;
  const onMuted = useCallback(() => { reloadNew(); started(); }, [reloadNew, started]);

  // Up to the newest one counted: a post indexed since stays new.
  async function markCardSeen(row, whom, name) {
    try {
      const r = await markSeen(row.until, whom);
      if (!r?.ok) { toast(r?.error || "Could not mark them seen.", "err"); return; }
      toast(`${name}: ${plural(row.count, "new post")} marked seen.`);
      newApi.reload();
      started();                               // the sidebar's count, now
    } catch (err) {
      toast(err.message, "err");
    }
  }
  const [filter, setFilter] = useState("");
  const [params, setParams] = useSearchParams();
  // Gender, country and label filters: from the address at first (a
  // person's label links here with ?label=), kept in it as they change.
  const [attrs, setAttrs] = useState(() => ({
    gender: params.get("gender"), country: params.get("country"), label: params.get("label"),
  }));
  const attrsOn = filtersOn(attrs);
  const filtering = !!filter || attrsOn;
  function changeAttrs(next) {
    setAttrs(next);
    setParams(prev => {
      const out = new URLSearchParams(prev);
      for (const k of Object.keys(NO_FILTERS)) {
        if (next[k] == null) out.delete(k);
        else out.set(k, next[k]);
      }
      return out;
    }, { replace: true });
  }
  const [busy,   setBusy]   = useState(false);
  const [merge,  setMerge]  = useState(null);                // { name, error, account? } while the dialog is open
  const [each,   setEach]   = useState(null);                // { error } while "One creator each" asks
  const nameRef = useRef(null);
  const [adding, setAdding] = useState(null);                // { name, links, error }: "New person" dialog
  const addRef = useRef(null);
  const sources = useSources();
  const syncAll = useSyncAll();
  const [removing, setRemoving] = useState(null);           // the source to remove
  const [editing,  setEditing]  = useState(null);           // the source whose options are open
  const [addAll,   setAddAll]   = useState(null);           // { error } while that dialog is open
  const [newSource, setNewSource] = useState(false);         // the new source's field has something typed
  // A typed source is asked about before the app leaves the page; the
  // address changing on this page (?source= cleared) leaves it be.
  const unsaved = useUnsaved(newSource, (from, to) => from.pathname === to.pathname);

  const data = authorsApi.data;
  const peopleAll = useMemo(() => peopleApi.data || [], [peopleApi.data]);
  const people = useMemo(
    () => peopleAll.filter(p => (!filter || matches(personText(p), filter)) && attributesMatch(p, attrs)),
    [peopleAll, filter, attrs]);
  // An account linked to no one has no gender, country or label: none
  // passes those filters.
  const unlinked = useMemo(() => {
    if (attrsOn) return [];
    const list = (data || []).filter(a => !a.person);
    return filter ? list.filter(a => matches(accountText(a), filter)) : list;
  }, [data, filter, attrsOn]);
  const total = (data || []).reduce((s, a) => s + (a.count || 0), 0);
  // Each grid renders the lines of cards near the viewport once it has many
  // (lib/windowing.js). The browser's find bar sees those only: the filter
  // box searches every name and handle.
  const winPeople = useWindow(people.length, { estimate: 66, grid: true });
  const winAccounts = useWindow(unlinked.length, { estimate: 66, grid: true });

  // One selection over both grids, in the order shown.
  const items = useMemo(() => [
    ...people.map(p => ({ id: `person:${p.id}`, person: p })),
    ...unlinked.map(a => ({ id: a.id != null ? `account:${accountKey(a)}` : null, account: a })),
  ], [people, unlinked]);
  const sel = useSelection(items, { resetKey: `${filter}|${JSON.stringify(attrs)}|${key}`, escapeBlocked: !!merge || !!each });

  // ?source=<id>, from a failed sync's notification (#123): that source's
  // row or card in view, and lit for a moment, as Links does a link saved
  // already. Nothing filtered or in select mode hides it.
  const asked = params.get("source");
  const [flash, setFlash] = useState(null);                  // the id of the source to point at
  const [askedSeen, setAskedSeen] = useState(null);
  if (asked !== askedSeen) {                                 // adjusting state during render, not in an effect
    setAskedSeen(asked);
    if (asked != null) {
      setFlash(Number(asked));
      setFilter("");
      setAttrs(NO_FILTERS);
      if (sel.active) sel.exit();
    }
  }
  const index = id => items.findIndex(i => i.id === id);
  const chosen = sel.selectedItems;
  const chosenPeople = chosen.filter(i => i.person).map(i => i.person);
  const chosenAccounts = chosen.filter(i => i.account).map(i => i.account);
  const canMerge = chosen.length > 0 && !(chosenPeople.length === 1 && chosenAccounts.length === 0);
  const canEach = chosenAccounts.length > 0 && !chosenPeople.length;
  // What the name dialog is about: one card's account (its Make creator
  // button), else the selection.
  const mergePeopleList = merge?.account ? [] : chosenPeople;
  const mergeAccounts = merge?.account ? [merge.account] : chosenAccounts;

  function reload() { setVersion(v => v + 1); }

  // Sources by the card that shows them: a person's (theirs or their
  // accounts'), else an unlinked account's. The rest (no posts yet, no
  // person) are listed on their own, and so is one with a new name to
  // accept or dismiss (a card has no room for that).
  const sourceList = useMemo(() => sources.data?.sources || [], [sources.data]);
  const cardSources = useMemo(() => {
    const m = new Map();
    for (const src of sourceList) {
      const k = src.person ? `person:${src.person.id}` : src.account ? `account:${accountKey(src.account)}` : null;
      if (k) m.set(k, [...(m.get(k) || []), src]);
    }
    return m;
  }, [sourceList]);
  const shownAccounts = useMemo(() => new Set((data || []).map(accountKey)), [data]);
  const loose = sourceList.filter(src => src.health?.rename
    || (!src.person && !(src.account && shownAccounts.has(accountKey(src.account)))));
  const syncOf = k => cardSync(cardSources.get(k), sources.jobOf);
  // Where the source asked for is shown: its own row, else its card.
  const flashSource = flash == null ? null : sourceList.find(src => src.id === flash);
  const flashKey = !flashSource ? null
    : loose.includes(flashSource) ? `source:${flashSource.id}`
    : flashSource.person ? `person:${flashSource.person.id}`
    : flashSource.account ? `account:${accountKey(flashSource.account)}` : null;

  async function addSuggested(list) {
    setBusy(true);
    let added = 0, failed = null;
    try {
      for (const s of list) {
        const r = await createSource(suggestionBody(s));
        if (r?.ok) added++;
        else failed = failed || `@${s.target}: ${r?.error || "could not add"}`;
      }
    } catch (err) {
      failed = err.message;
    } finally {
      setBusy(false);
      sources.reload();
    }
    if (added) toast(`${added} source${added === 1 ? "" : "s"} added.`);
    if (failed) toast(failed, "err");
    return !failed;
  }

  function askMerge() {
    const name = chosenPeople[0]?.name || freeName(suggestName(chosenAccounts) || "New person", peopleAll);
    setMerge({ name, error: null });
  }

  // One card's account as a person of its own. The page stays: the next
  // card to go through is still there.
  function askMake(a) {
    setMerge({ name: freeName(suggestName([a]) || "New person", peopleAll), error: null, account: a });
  }

  async function runMerge() {
    const name = merge.name.trim();
    const accounts = mergeAccounts.map(accountRef);
    setBusy(true);
    try {
      if (merge.account) {
        const r = await createPerson(name, accounts);
        if (!r?.ok) { setMerge(m => ({ ...m, error: r?.error || "Could not create them." })); return; }
        setMerge(null);
        toast(`@${merge.account.handle || merge.account.id} is now a creator: ${r.person.name}.`);
        reload();
        return;
      }
      const r = chosenPeople.length
        ? await mergePeople(chosenPeople.map(p => p.id), { name, accounts })
        : await createPerson(name, accounts);
      if (!r?.ok) { setMerge(m => ({ ...m, error: r?.error || "Could not merge." })); return; }
      setMerge(null);
      sel.exit();
      navigate(personPath(r.person.id));
    } catch (err) {
      setMerge(m => ({ ...m, error: err.message }));
    } finally {
      setBusy(false);
    }
  }

  // "One creator each": a person per selected account, named from it. A
  // name taken already, or by one made earlier in the run, gets " 2"…
  async function runEach() {
    const taken = [...peopleAll];
    let made = 0, failed = null;
    setBusy(true);
    try {
      for (const a of chosenAccounts) {
        const r = await createPerson(freeName(suggestName([a]) || "New person", taken), [accountRef(a)]);
        if (r?.ok) { made++; taken.push(r.person); }
        else failed = failed || `@${a.handle || a.id}: ${r?.error || "could not create them"}`;
      }
    } catch (err) {
      failed = err.message;
    } finally {
      setBusy(false);
    }
    setEach(null);
    sel.exit();
    reload();
    if (made) toast(`${plural(made, "creator")} made.`);
    if (failed) toast(failed, "err");
  }

  // A person by hand: a name and the profile links to sync, before anything is downloaded.
  async function runAdd() {
    const profiles = adding.links.split(/\s+/).filter(Boolean);
    setBusy(true);
    try {
      const r = await createPerson(adding.name.trim(), [], profiles);
      if (!r?.ok) { setAdding(a => ({ ...a, error: r?.error || "Could not add." })); return; }
      setAdding(null);
      sources.reload();
      navigate(personPath(r.person.id));
    } catch (err) {
      setAdding(a => ({ ...a, error: err.message }));
    } finally {
      setBusy(false);
    }
  }

  async function linkSuggestion(s) {
    const add = s.accounts.filter(a => !a.person).map(accountRef);
    setBusy(true);
    try {
      const r = s.person
        ? await linkAccounts(s.person.id, { add })
        : await createPerson(freeName(suggestName(s.accounts), peopleAll), s.accounts.map(accountRef));
      if (!r?.ok) { toast(r?.error || "Could not link.", "err"); return; }
      const name = r.person?.name || s.person?.name;
      toast(`Linked ${s.accounts.length} accounts as ${name}.`);
      reload();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function dismiss(s) {
    setBusy(true);
    try {
      const r = await dismissSuggestion(s.id);
      if (!r?.ok) { toast(r?.error || "Could not dismiss.", "err"); return; }
      reload();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  const error = authorsApi.error || peopleApi.error;
  const loading = !data || !peopleApi.data;
  const empty = !people.length && !unlinked.length;

  // Once the sources and the cards are in: scroll to it, light it, then
  // drop ?source= so the same notification can point at it again.
  const ready = !!sources.data && !loading;
  const flashPerson = flashKey ? people.findIndex(p => `person:${p.id}` === flashKey) : -1;
  const flashAccount = flashKey ? unlinked.findIndex(a => a.id != null && `account:${accountKey(a)}` === flashKey) : -1;
  const scrollPeople = winPeople.scrollTo, scrollAccounts = winAccounts.scrollTo;
  useEffect(() => {
    if (flash == null || !ready) return undefined;
    // A card may not be rendered yet (a windowed grid): its grid brings it.
    if (flashPerson >= 0) scrollPeople(flashPerson);
    else if (flashAccount >= 0) scrollAccounts(flashAccount);
    else if (flashKey) document.querySelector(".source-row.is-flash")?.scrollIntoView({ block: "center", behavior: "smooth" });
    if (!flashKey) toast("That source is not there any more.", "err");
    const t = setTimeout(() => {
      setFlash(null);
      setParams(p => {
        const next = new URLSearchParams(p);
        for (const k of ["source", ...Object.keys(NO_FILTERS)]) next.delete(k);
        return next;
      }, { replace: true });
    }, flashKey ? 2400 : 0);
    return () => clearTimeout(t);
  }, [flash, flashKey, flashPerson, flashAccount, scrollPeople, scrollAccounts, ready, setParams, toast]);

  return (
    <>
      <PageHeader
        title="Creators"
        sub={data && peopleApi.data
          ? `${plural(peopleAll.length, "person", "people")} · ${plural(data.length, "account")} · ${plural(total, "post")}`
          : "…"}
        actions={<>
          {(data?.length > 8 || peopleAll.length > 8) && (
            <input
              type="text"
              className="page-filter"
              placeholder="Name or any handle…"
              aria-label="Filter creators"
              value={filter}
              onChange={e => setFilter(e.target.value)}
            />
          )}
          {!sel.active && (
            <button type="button" className="btn-secondary" onClick={() => setAdding({ name: "", links: "", error: null })}
                    title="A person and their profile links, before anything is downloaded">
              <Icon name="plus" size={14} /> New person
            </button>
          )}
          {data?.length > 1 && (
            <button
              type="button"
              className={`btn-secondary select-toggle${sel.active ? " is-on" : ""}`}
              aria-pressed={sel.active}
              onClick={() => (sel.active ? sel.exit() : sel.enter())}
              title={sel.active ? "Leave select mode (Esc)" : "Select people and accounts to merge"}
            >
              <Icon name={sel.active ? "close" : "check"} size={14} />
              {sel.active ? "Done" : "Select"}
            </button>
          )}
        </>}
      />
      <div className="card">
        {/* Hidden, not gone, while filtering or selecting: a source being typed stays. */}
        <section className="sources-panel" aria-label="Sources" hidden={sel.active || filtering}>
          <AddSource onAdded={sources.reload} onDirty={setNewSource} />
          <SyncAllBar count={sourceList.length} syncAll={syncAll} />
          {loose.length > 0 && (
            <ul className="source-list">
              {loose.map(src => (
                <SourceRow key={src.id} source={src} job={sources.jobOf(src)} onSync={sources.sync} onRemove={setRemoving}
                           onSaved={sources.reload} flash={flashKey === `source:${src.id}`} />
              ))}
            </ul>
          )}
        </section>

        {!sel.active && !filtering && (
          <SourceSuggestions
            list={sources.data?.suggestions}
            busy={busy}
            onAdd={s => addSuggested([s])}
            onAddAll={() => setAddAll({ error: null })}
          />
        )}

        {!sel.active && !filtering && (
          <Suggestions data={suggestApi.data} busy={busy} onLink={linkSuggestion} onDismiss={dismiss} />
        )}

        {peopleApi.data && <AttributeFilters people={peopleAll} value={attrs} onChange={changeAttrs} />}

        {error && !data ? (
          <div className="empty">Could not load creators: {error.message}</div>
        ) : loading ? (
          <div className="empty">Loading…</div>
        ) : empty ? (
          <div className="empty">{filtering ? "No creator matches." : "No creators yet. They appear once posts are indexed."}</div>
        ) : (
          <>
            {people.length > 0 && (
              <>
                <h3 className="card-title creators-section">People <span className="page-count">{fmtInt(people.length)}</span></h3>
                <div className={`creator-grid${winPeople.settled ? " is-settled" : ""}`} ref={winPeople.listRef} {...winPeople.focusProps}>
                  {winPeople.render(i => { const p = people[i]; return (
                    <PersonCard
                      key={p.id}
                      person={p}
                      index={i}
                      settled={winPeople.settled}
                      selectMode={sel.active}
                      selected={sel.isSelected(`person:${p.id}`)}
                      onToggle={shift => sel.toggle(index(`person:${p.id}`), shift)}
                      sync={syncOf(`person:${p.id}`)}
                      onSync={sources.sync}
                      fresh={fresh.get(`person:${p.id}`)}
                      onSeen={() => markCardSeen(fresh.get(`person:${p.id}`), { person: p.id }, p.name)}
                      muted={mutedSet && mutedSet.has(`person:${p.id}`)}
                      onMuted={onMuted}
                      flash={flashKey === `person:${p.id}`}
                    />
                  ); }, (height, key) => <Gap key={key} height={height} />)}
                </div>
              </>
            )}
            {unlinked.length > 0 && (
              <>
                {people.length > 0 && (
                  <h3 className="card-title creators-section">Accounts <span className="page-count">{fmtInt(unlinked.length)} not linked</span></h3>
                )}
                <div className={`creator-grid${winAccounts.settled ? " is-settled" : ""}`} ref={winAccounts.listRef} {...winAccounts.focusProps}>
                  {winAccounts.render(i => { const a = unlinked[i]; return (
                    <AccountCard
                      key={`${a.platform}:${a.id ?? a.handle}`}
                      account={a}
                      index={i}
                      settled={winAccounts.settled}
                      query={filter}
                      selectMode={sel.active}
                      selected={sel.isSelected(`account:${accountKey(a)}`)}
                      onToggle={shift => sel.toggle(index(`account:${accountKey(a)}`), shift)}
                      sync={a.id != null ? syncOf(`account:${accountKey(a)}`) : null}
                      onSync={sources.sync}
                      onEdit={setEditing}
                      onMake={() => askMake(a)}
                      fresh={a.id != null ? fresh.get(`account:${accountKey(a)}`) : null}
                      onSeen={() => markCardSeen(fresh.get(`account:${accountKey(a)}`), { account: accountRef(a) },
                                                 `@${a.handle || a.id}`)}
                      muted={mutedSet && mutedSet.has(`account:${accountKey(a)}`)}
                      onMuted={onMuted}
                      flash={a.id != null && flashKey === `account:${accountKey(a)}`}
                    />
                  ); }, (height, key) => <Gap key={key} height={height} />)}
                </div>
              </>
            )}
          </>
        )}

        {sel.active && (
          <SelectionBar selection={sel} loaded={items.filter(i => i.id).length}>
            <button type="button" className="btn-secondary" disabled={!canEach || busy} onClick={() => setEach({ error: null })}
                    title={canEach ? "A person for each selected account, named from it" : "Select accounts only"}>
              <Icon name="userPlus" size={14} /> One creator each
            </button>
            <button type="button" className="btn-primary" disabled={!canMerge || busy} onClick={askMerge}
                    title={canMerge ? "" : "Select accounts, or two people"}>
              <Icon name={chosen.length === 1 && chosenAccounts.length === 1 ? "userPlus" : "users"} size={14} />
              {chosen.length === 1 && chosenAccounts.length === 1 ? " Make creator" : " Merge into one person"}
            </button>
          </SelectionBar>
        )}

        <RemoveSourceDialog source={removing} onRemove={sources.remove} onClose={() => setRemoving(null)} />
        {editing && <SourceOptionsDialog source={editing} onClose={() => setEditing(null)} onSaved={sources.reload} />}

        <ConfirmDialog
          open={!!addAll}
          title={`Add ${fmtInt(sources.data?.suggestions?.length || 0)} sources?`}
          confirmLabel="Add all"
          busy={busy}
          error={addAll?.error}
          onConfirm={async () => {
            if (await addSuggested(sources.data?.suggestions || [])) setAddAll(null);
            else setAddAll({ error: "Some could not be added; see the messages." });
          }}
          onCancel={() => setAddAll(null)}
        >
          Every instaloader folder listed becomes a source, with the profile name shown. Nothing is
          downloaded until you sync; each first sync starts after the newest post already in the folder.
        </ConfirmDialog>

        <ConfirmDialog
          open={!!adding}
          title="New person"
          confirmLabel="Create"
          busy={busy}
          error={adding?.error}
          onConfirm={runAdd}
          onCancel={() => setAdding(null)}
          initialFocus={addRef}
          confirmDisabled={!adding?.name.trim()}
        >
          <p>Each profile link becomes a source of theirs. Nothing is downloaded until you sync them.</p>
          <label className="dialog-field">
            <span>Name</span>
            <input
              ref={addRef}
              type="text"
              className="page-filter"
              maxLength={64}
              value={adding?.name || ""}
              onChange={e => setAdding(a => ({ ...a, name: e.target.value, error: null }))}
              onKeyDown={e => { if (e.key === "Enter" && adding?.name.trim() && !busy) runAdd(); }}
            />
          </label>
          <label className="dialog-field">
            <span>Profile links, one per line</span>
            <textarea
              className="person-notes"
              rows={4}
              placeholder={"https://www.instagram.com/…\nhttps://x.com/…\nhttps://www.youtube.com/@…"}
              value={adding?.links || ""}
              onChange={e => setAdding(a => ({ ...a, links: e.target.value, error: null }))}
            />
          </label>
        </ConfirmDialog>

        <ConfirmDialog
          open={!!each}
          title={`Make ${plural(chosenAccounts.length, "creator")}?`}
          confirmLabel="Create creators"
          busy={busy}
          error={each?.error}
          onConfirm={runEach}
          onCancel={() => setEach(null)}
        >
          <p>
            Each selected account becomes a person of its own, named from the account. Posts and files are
            not touched.
          </p>
        </ConfirmDialog>

        <ConfirmDialog
          open={!!merge}
          title={merge?.account ? `Make @${merge.account.handle || merge.account.id} a creator`
            : mergePeopleList.length > 1 ? "Merge people" : mergePeopleList.length ? `Add to ${mergePeopleList[0].name}` : "New person"}
          confirmLabel={mergePeopleList.length ? "Merge" : "Create creator"}
          busy={busy}
          error={merge?.error}
          onConfirm={runMerge}
          onCancel={() => setMerge(null)}
          initialFocus={nameRef}
          confirmDisabled={!merge?.name.trim()}
        >
          <p>
            {mergePeopleList.length > 1 && `${mergePeopleList.length} people become one; their notes are kept. `}
            {mergeAccounts.length > 0 && `${mergeAccounts.length} account${mergeAccounts.length === 1 ? "" : "s"} get linked. `}
            Posts and files are not touched.
          </p>
          <label className="dialog-field">
            <span>Name</span>
            <input
              ref={nameRef}
              type="text"
              className="page-filter"
              maxLength={64}
              value={merge?.name || ""}
              onChange={e => setMerge(m => ({ ...m, name: e.target.value, error: null }))}
              onKeyDown={e => { if (e.key === "Enter" && merge?.name.trim() && !busy) runMerge(); }}
            />
          </label>
        </ConfirmDialog>

        <DiscardDialog open={unsaved.asking} onDiscard={unsaved.discard} onKeep={unsaved.keep}>
          <p>The new source is not added yet.</p>
        </DiscardDialog>
      </div>
    </>
  );
}
