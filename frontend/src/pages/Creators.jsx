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
  accountChips, accountKey, accountRef, mergeSet, mergeStopping, accountText, attributeChoices, attributeFiltersOn, attributesMatch, countryFlag, countryName, filtersOn,
  matchedFormer, matches, NO_FILTERS, notSynced, personPath, personText, suggestName, syncFilter, syncMatch,
} from "../lib/people";
import Icon from "../components/Icon";
import PlatformIcon from "../components/PlatformIcon";
import PersonAvatar from "../components/PersonAvatar";
import ConfirmDialog from "../components/ConfirmDialog";
import DiscardDialog from "../components/DiscardDialog";
import SelectionBar from "../components/SelectionBar";
import Suggestions from "../components/Suggestions";
import ActionMenu from "../components/ActionMenu";
import StateChips from "../components/StateChips";
import { useNoSync } from "../components/NoSync";
import { useMute } from "../lib/useMute";
import {
  AddSource, RemoveSourceDialog, ScheduleLine, ScriptWarning, SourceOptionsDialog, SourceRow, SourceStatus,
  SyncAllBar, SyncButton,
} from "../components/Sources";
import { sourceName, useSources, useSyncAll } from "../lib/sources";
import { optionsSummary } from "../lib/sourceOptions";
import { warnings } from "../lib/health";
import PageHeader from "../components/PageHeader";
import { useUnsaved } from "../lib/unsaved";
import { useWindow } from "../lib/windowing";
import { Gap } from "../components/WinGap";
import CreatorSearch from "../components/CreatorSearch";

const SUGGESTIONS_SHOWN = 4;

/* How people are shown, kept in this browser: "portraits" (the default, a
   grid of 2:3 pictures as Stash shows performers) or "compact" (rows).
   Accounts linked to nobody are rows either way. */
const VIEW_KEY = "feedvault.creators.view";
const VIEWS = [["portraits", "Portraits"], ["compact", "Compact"]];
function storedView() {
  try { return localStorage.getItem(VIEW_KEY) === "compact" ? "compact" : "portraits"; } catch { return "portraits"; }
}
// A card's height before any is measured (lib/windowing), by view.
const ESTIMATE = { portraits: 400, compact: 66 };

// Person names are unique; a default that is taken gets " 2", " 3"…
function freeName(name, people) {
  const taken = new Set(people.map(p => p.name.toLowerCase()));
  if (!taken.has(name.toLowerCase())) return name;
  for (let i = 2; ; i++) if (!taken.has(`${name} ${i}`.toLowerCase())) return `${name} ${i}`;
}

/* A card's sources: which one to show (syncing, else a failed one, else
   the latest sync), its job, and those not syncing already. One set to
   Don't sync never runs: no status, Sync, Options or warning for it; nor
   does a profile only one (#218). */
function cardSync(all, jobOf) {
  const sources = (all || []).filter(s => !s.no_sync && !s.profile_only);
  if (!sources.length) return null;
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

// "Mark seen" of one card, in its ⋯ menu: its new posts stop being new,
// the others' stay.
function seenItem(fresh, name, onSeen) {
  return fresh?.count > 0 && { label: "Mark seen", icon: "check", onSelect: onSeen,
    title: `Mark ${name}'s ${plural(fresh.count, "new post")} seen` };
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

// A card's accounts on one line (lib/people accountChips): a platform with
// several shows its icon and "×n", and "+n" counts the platforms past the
// first few. Each chip's title names its accounts; the person page lists them.
const accountLine = a => `${platformLabel(a.platform)} @${a.handle || a.id}`;
function CardAccounts({ accounts }) {
  if (!accounts.length) return <span className="person-chips"><span className="creator-sub">No account linked</span></span>;
  const { chips, rest } = accountChips(accounts);
  return (
    <span className="person-chips">
      {chips.map(([a, ...more]) => more.length ? (
        <span key={a.platform} className="chip platform-chip is-group"
              title={`${platformLabel(a.platform)}: ${[a, ...more].map(x => `@${x.handle || x.id}`).join(", ")}`}>
          <PlatformIcon platform={a.platform} size={12} inChip /><span className="chip-text">×{more.length + 1}</span>
        </span>
      ) : (
        <span key={accountKey(a)} className="chip platform-chip" title={accountLine(a)}>
          <PlatformIcon platform={a.platform} size={12} inChip /><span className="chip-text">@{a.handle || a.id}</span>
        </span>
      ))}
      {rest.length > 0 && (
        <span className="chip platform-chip is-more" title={rest.map(accountLine).join(", ")}>+{rest.length}</span>
      )}
    </span>
  );
}

function PersonCard({ person: p, index, settled, portrait, selectMode, selected, onToggle, sync, onSync, fresh, onSeen, muted, onMuted,
  onNoSync, flash }) {
  const where = countryName(p.country);
  const mute = useMute({ muted: !!muted, whom: { person: p.id }, name: p.name, onDone: onMuted });
  const noSync = useNoSync({ on: !!p.no_sync, whom: { person: p.id }, name: p.name, onDone: onNoSync });
  const badges = <><NewBadge count={fresh?.count} /><WarnBadge sync={sync} /></>;
  const body = (
    <>
      {portrait ? (
        // A fixed 2:3 box, the picture lazy-loaded in it: nothing moves as it comes.
        <span className="portrait-box">
          <PersonAvatar person={p} size="portrait" />
          <span className="portrait-badges">{badges}</span>
        </span>
      ) : <PersonAvatar person={p} />}
      <span className="creator-id">
        <span className="creator-name">
          {p.country && <span className="person-flag" role="img" aria-label={where} title={where}>{countryFlag(p.country)}</span>}
          {p.name}
        </span>
        <CardAccounts accounts={p.accounts} />
        <CardLabels labels={p.labels} />
        <StateChips name={p.name} muted={!!muted} noSync={notSynced(p)}
                    noSyncTitle={p.no_sync ? `${p.name} is set to Don't sync: nothing is downloaded from them. Sync again from their ⋯ menu`
                      : "Each of their accounts is set to Don't sync"} />
        <CardSyncStatus sync={sync} />
      </span>
      {!portrait && (
        <span className="person-stats">
          <span className="creator-count">{fmtInt(p.count)}</span>
          <span className="creator-sub">{fmtBytes(p.bytes)}</span>
          {badges}
        </span>
      )}
    </>
  );
  // A portrait's last line: the count. Under its link, with the card's
  // buttons at its right; in select mode, inside the card's button, so a
  // click anywhere on the card toggles it (there are no buttons then).
  const stats = portrait && (
    <span className="person-stats">
      <span className="creator-count" title={plural(p.count, "post")}>{fmtInt(p.count)}</span>
      <span className="creator-sub">{fmtBytes(p.bytes)}</span>
    </span>
  );
  // Sync in sight (when they have a source), the rest in the ⋯ menu; none
  // in select mode, where a portrait is one button.
  const actions = !selectMode && (
    <>
      <CardSyncButton sync={sync} onSync={onSync} />
      <ActionMenu label={`More for ${p.name}`} items={[
        seenItem(fresh, p.name, onSeen),
        "separator",
        muted != null && mute,
        noSync.item,
      ]} />
    </>
  );
  return (
    <div
      className={`creator-card person-card${portrait ? " is-portrait" : ""}${selected ? " is-selected" : ""}${flash ? " is-flash" : ""}`}
      data-index={index}
      style={settled ? undefined : { animationDelay: `${Math.min(index, 30) * 25}ms` }}
    >
      {selectMode ? (
        <button type="button" className="creator-main" aria-pressed={selected} onClick={e => onToggle(e.shiftKey)}>
          {body}
          {stats}
        </button>
      ) : (
        <>
          <Link to={personPath(p.id)} className="creator-main" title={`Open ${p.name}`}>{body}</Link>
          {stats}
        </>
      )}
      {actions && <span className={portrait ? "portrait-actions" : "card-actions"}>{actions}</span>}
      <CardScriptWarnings sync={sync} />
      {noSync.dialog}
    </div>
  );
}

// An account card's Options, in its ⋯ menu: its source's, or one item per
// source when it has several. Off while that source's sync is queued or
// running (its end would undo what changes now).
function optionsItems(sync, onEdit) {
  if (!sync) return [];
  const label = s => `${sourceName(s)} (${s.tool})`;
  return sync.sources.map(s => {
    const idle = sync.idle.includes(s);
    const summary = optionsSummary(s);
    return {
      label: sync.sources.length === 1 ? "Options" : `Options of ${label(s)}`,
      icon: "settings",
      disabled: !idle,
      title: idle ? `What ${sourceName(s)} downloads with ${s.tool}${summary ? `: ${summary}` : ""}` : `${label(s)}: its sync is queued or running`,
      onSelect: () => onEdit(s),
    };
  });
}

function AccountCard({ account: a, index, settled, query, selectMode, selected, onToggle, sync, onSync, onEdit, onMake, fresh, onSeen, muted, onMuted,
  onNoSync, flash }) {
  const former = matchedFormer(a, query);
  const sub = `${a.name && a.name !== a.handle ? `${a.name} · ` : ""}${platformLabel(a.platform)}${former ? ` · was @${former}` : ""}`;
  const name = `@${a.handle || a.id}`;
  const known = a.id != null;                   // one without an id has no Review, Make creator, Mute or Don't sync
  const mute = useMute({ muted: !!muted, whom: { account: accountRef(a) }, name, onDone: onMuted });
  const noSync = useNoSync({ on: !!a.no_sync, whom: { account: accountRef(a) }, name, onDone: onNoSync });
  const body = (
    <>
      <span className="avatar-letter" aria-hidden="true">{(a.handle || a.name || "?").charAt(0).toUpperCase()}</span>
      <span className="creator-id">
        <span className="creator-name">{name}</span>
        <span className="creator-sub" title={sub}>{sub}</span>
        <StateChips name={name} muted={known && !!muted} noSync={!!a.no_sync} />
        <CardSyncStatus sync={sync} />
      </span>
      <span className="person-stats">
        <span className="creator-count">{a.count}</span>
        <NewBadge count={fresh?.count} />
        <WarnBadge sync={sync} />
      </span>
    </>
  );
  const selectable = selectMode && known;
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
        <Link to={authorFeedPath(a.platform, a)} className="creator-main" title={`Show posts by ${name}`}>{body}</Link>
      )}
      {!selectMode && (
        // One quick action in sight, Sync (when it has a source); Review,
        // Options, Make creator (a person of its own, in one click) and the
        // switches in its ⋯ menu, so the card stays one row.
        <span className="card-actions">
          <CardSyncButton sync={sync} onSync={onSync} />
          <ActionMenu label={`More for ${name}`} items={[
            known && { label: "Review", icon: "review", to: `/review?${new URLSearchParams({ platform: a.platform, author: a.id })}`,
                       title: `Review ${name}'s unreviewed posts` },
            ...optionsItems(sync, onEdit),
            known && { label: "Make creator", icon: "userPlus", onSelect: onMake, title: `Make ${name} a creator: a person of its own` },
            seenItem(fresh, name, onSeen),
            "separator",
            known && muted != null && mute,
            known && noSync.item,
          ]} />
        </span>
      )}
      <CardScriptWarnings sync={sync} />
      {noSync.dialog}
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
   set". One the people give no value for is left out, unless it is on.
   Sync: synced or set to Don't sync, people and accounts linked to nobody
   alike; shown once one is not synced, or while it is on. */
function AttributeFilters({ people, accounts, value, onChange }) {
  const c = useMemo(() => attributeChoices(people), [people]);
  const off = useMemo(() => people.filter(notSynced).length + accounts.filter(a => !a.person && a.no_sync).length,
    [people, accounts]);
  const all = people.length + accounts.filter(a => !a.person).length;
  const fields = [
    { key: "gender", label: "Gender", any: "Any gender", none: "Not set", list: c.genders, text: v => v },
    { key: "country", label: "Country", any: "Any country", none: "Not set", list: c.countries,
      text: v => `${countryFlag(v)} ${countryName(v)}` },
    { key: "label", label: "Label", any: "Any label", none: "No label", list: c.labels, text: v => v },
  ].filter(f => f.list.length || value[f.key] != null);
  const syncShown = off > 0 || value.sync != null;
  if (!fields.length && !syncShown) return null;
  return (
    <div className="feed-filters creator-filters" role="group" aria-label="Filter people">
      {syncShown && (
        <label className="filter">
          <span>Sync</span>
          <select className="sort-select" aria-label="Sync" value={value.sync || "any"}
                  onChange={e => onChange({ ...value, sync: syncFilter(e.target.value) })}>
            <option value="any">Any</option>
            <option value="on">Synced ({fmtInt(all - off)})</option>
            <option value="off">Not synced ({fmtInt(off)})</option>
          </select>
        </label>
      )}
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
    sync: syncFilter(params.get("sync")),
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
  // passes those filters. The sync one, by its own switch.
  const unlinked = useMemo(() => {
    if (attributeFiltersOn(attrs)) return [];
    const list = (data || []).filter(a => !a.person && syncMatch(a, attrs.sync));
    return filter ? list.filter(a => matches(accountText(a), filter)) : list;
  }, [data, filter, attrs]);
  const total = (data || []).reduce((s, a) => s + (a.count || 0), 0);
  // Each grid renders the lines of cards near the viewport once it has many
  // (lib/windowing.js). The browser's find bar sees those only: the filter
  // box searches every name and handle.
  const [view, setView] = useState(storedView);
  const portraits = view === "portraits";
  function changeView(v) {
    setView(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* private mode */ }
  }
  const winPeople = useWindow(people.length, { estimate: ESTIMATE[view], grid: true });
  const winAccounts = useWindow(unlinked.length, { estimate: 66, grid: true });

  // One selection over both grids, in the order shown.
  const items = useMemo(() => [
    ...people.map(p => ({ id: `person:${p.id}`, person: p })),
    ...unlinked.map(a => ({ id: a.id != null ? `account:${accountKey(a)}` : null, account: a })),
  ], [people, unlinked]);
  const sel = useSelection(items, { resetKey: `${filter}|${JSON.stringify(attrs)}|${key}`, escapeBlocked: !!merge || !!each });

  // The header's search (lib/search.js), in ?q=: while it has something in
  // it the page shows what matches (CreatorSearch) instead of its grids, and
  // leaves select mode. The sources panel stays, hidden, so a source being
  // typed is still there after.
  const q = params.get("q") || "";
  const searching = !!q.trim();
  const [wasSearching, setWasSearching] = useState(searching);
  if (searching !== wasSearching) {                         // adjusting state during render, not in an effect
    setWasSearching(searching);
    if (searching && sel.active) sel.exit();
  }

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
  // The merge's Don't sync warning holds for the set it was given for: a
  // person or account added or dropped since, and it is asked again.
  const mergeKey = mergeSet(mergePeopleList, mergeAccounts);
  const stopping = mergeStopping(merge, mergeKey);

  function reload() { setVersion(v => v + 1); }
  // Don't sync changed: the cards, and the sources marked Not synced.
  const sourcesReload = sources.reload;
  const onNoSync = useCallback(() => { setVersion(v => v + 1); sourcesReload(); }, [sourcesReload]);

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
        ? await mergePeople(chosenPeople.map(p => p.id), { name, accounts, confirm: stopping > 0 })
        : await createPerson(name, accounts);
      // One of them is set to Don't sync: the one kept will be, so their
      // sources that sync now would stop. Said in the dialog; Merge again goes on.
      if (r?.confirm && !stopping) { setMerge(m => ({ ...m, stopping: r.sources, stoppingFor: mergeKey, error: null })); return; }
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
          {!searching && (data?.length > 8 || peopleAll.length > 8) && (
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
          {!searching && data?.length > 1 && (
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
        {/* Hidden, not gone, while filtering, searching or selecting: a source being typed stays. */}
        <section className="sources-panel" aria-label="Sources" hidden={sel.active || filtering || searching}>
          <AddSource onAdded={sources.reload} onDirty={setNewSource} />
          <SyncAllBar count={sourceList.filter(src => !src.no_sync && !src.profile_only).length} syncAll={syncAll} />
          {loose.length > 0 && (
            <ul className="source-list">
              {loose.map(src => (
                // Linked to a person: it leaves this list for their card, which the cards' reload shows.
                <SourceRow key={src.id} source={src} job={sources.jobOf(src)} onSync={sources.sync} onRemove={setRemoving}
                           onSaved={sources.reload} onLinked={onNoSync} flash={flashKey === `source:${src.id}`} />
              ))}
            </ul>
          )}
        </section>

        {!sel.active && !filtering && !searching && (
          <SourceSuggestions
            list={sources.data?.suggestions}
            busy={busy}
            onAdd={s => addSuggested([s])}
            onAddAll={() => setAddAll({ error: null })}
          />
        )}

        {!sel.active && !filtering && !searching && (
          <Suggestions data={suggestApi.data} busy={busy} onLink={linkSuggestion} onDismiss={dismiss} />
        )}

        {peopleApi.data && !searching && <AttributeFilters people={peopleAll} accounts={data || []} value={attrs} onChange={changeAttrs} />}

        {error && !data ? (
          <div className="empty">Could not load creators: {error.message}</div>
        ) : searching ? (
          <CreatorSearch query={q} people={peopleApi.data} accounts={data} refreshKey={key} />
        ) : loading ? (
          <div className="empty">Loading…</div>
        ) : empty ? (
          <div className="empty">{filtering ? "No creator matches." : "No creators yet. They appear once posts are indexed."}</div>
        ) : (
          <>
            {people.length > 0 && (
              <>
                <div className="creators-people-head">
                  <h3 className="card-title creators-section">People <span className="page-count">{fmtInt(people.length)}</span></h3>
                  <div className="links-tabs creators-view" role="group" aria-label="Show people as">
                    {VIEWS.map(([v, label]) => (
                      <button key={v} type="button" className={`links-tab${view === v ? " is-on" : ""}`} aria-pressed={view === v}
                              onClick={() => changeView(v)}>
                        <Icon name={v === "portraits" ? "portraits" : "rows"} size={14} />{label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className={`creator-grid${portraits ? " is-portraits" : ""}${winPeople.settled ? " is-settled" : ""}`}
                     ref={winPeople.listRef} {...winPeople.focusProps}>
                  {winPeople.render(i => { const p = people[i]; return (
                    <PersonCard
                      key={p.id}
                      person={p}
                      index={i}
                      settled={winPeople.settled}
                      portrait={portraits}
                      selectMode={sel.active}
                      selected={sel.isSelected(`person:${p.id}`)}
                      onToggle={shift => sel.toggle(index(`person:${p.id}`), shift)}
                      sync={syncOf(`person:${p.id}`)}
                      onSync={sources.sync}
                      fresh={fresh.get(`person:${p.id}`)}
                      onSeen={() => markCardSeen(fresh.get(`person:${p.id}`), { person: p.id }, p.name)}
                      muted={mutedSet && mutedSet.has(`person:${p.id}`)}
                      onMuted={onMuted}
                      onNoSync={onNoSync}
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
                      onNoSync={onNoSync}
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
            {mergeAccounts.length > 0 && `${plural(mergeAccounts.length, "account")} ${mergeAccounts.length === 1 ? "gets" : "get"} linked. `}
            Posts and files are not touched.
          </p>
          {stopping > 0 && (
            <p className="msg merge-nosync" role="status">
              One of them is set to Don&apos;t sync, so the person they become is too: {plural(stopping, "source")}{" "}
              that {stopping === 1 ? "syncs" : "sync"} now will stop syncing (kept, not deleted). Merge again to go on.
            </p>
          )}
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
