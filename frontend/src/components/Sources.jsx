import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { acceptRename, createSource, cancelJob, dismissRename, getScripts, resolveSource, updateSource } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { useJobs } from "../lib/jobs";
import { ERRORS, SETUP_ERRORS, sourceName } from "../lib/sources";
import { healthBadge, lastGood, loginText } from "../lib/health";
import { fmtAgo, fmtFullDate, fmtIn, fmtInt, platformLabel, platformShort, safeUrl } from "../lib/fmt";
import {
  FIRST_POSTS_MAX, MEDIA, SCHEDULES, firstPostsEach, formError, formOf, kindEffect, kindLabel, mediaEffect, needsLogin,
  optionsOf, optionsSummary, scheduleShort, scheduleText, scriptHref, toggleKind, today,
} from "../lib/sourceOptions";
import Icon from "./Icon";
import ConfirmDialog from "./ConfirmDialog";
import DiscardDialog from "./DiscardDialog";
import { useUnsaved } from "../lib/unsaved";

/* Where a source stands: its sync now (queued, waiting out the pause,
   running), else how its last one went. */
export function SourceStatus({ source: s, job, compact = false }) {
  if (job) {
    return (
      <span className="source-status">
        {job.state === "running"
          ? <><Icon name="refresh" size={12} className="spin" /> syncing…</>
          : job.waits_until ? `waiting, starts ${fmtIn(job.waits_until)}` : "queued"}
      </span>
    );
  }
  const r = s.last_result;
  if (!r) return <span className="source-status dim">never synced</span>;
  const failed = r.state === "failed";
  const title = [r.message, r.line, s.last_sync_at && fmtFullDate(s.last_sync_at)].filter(Boolean).join("\n");
  // The account's state (health), else (an old result) the error's badge.
  const badge = healthBadge(s.health) || (failed ? { label: ERRORS[r.error] || "failed", tone: "failed" } : null);
  return (
    <span className="source-status" title={title}>
      {badge && <span className={`chip job-state-${badge.tone} source-badge`}>{badge.label}</span>}
      {(r.state === "cancelled" || r.state === "interrupted") && <span className="chip job-state-interrupted source-badge">{r.state}</span>}
      {!compact && r.state === "done" && <span className="source-note">{r.message}</span>}
      {!compact && failed && <span className="source-message">{r.message}</span>}
      {!compact && failed && (SETUP_ERRORS.has(r.error) || r.outdated) && (
        r.error === "login_required" && !r.outdated
          ? <Link to="/settings#sync" className="text-link source-setup">Settings → Sync</Link>
          : <Link to="/settings#downloaders" className="text-link source-setup">Settings → Downloaders</Link>
      )}
      {s.last_sync_at && <span className="dim">{compact ? "synced " : " · "}{fmtAgo(s.last_sync_at)}</span>}
    </span>
  );
}

export function SyncButton({ source: s, job, onSync, small = false }) {
  const label = job ? (job.state === "running" ? "Syncing" : "Queued") : "Sync";
  return (
    <button
      type="button"
      className={small ? "icon-btn source-sync" : "btn-secondary source-sync"}
      disabled={!!job}
      onClick={e => { e.preventDefault(); onSync(s); }}
      title={job ? "Its sync is queued or running; see Jobs" : `Download ${sourceName(s)}'s new posts with ${s.tool}`}
      aria-label={`Sync ${sourceName(s)}`}
    >
      <Icon name="refresh" size={small ? 15 : 13} className={job?.state === "running" ? "spin" : undefined} />
      {!small && label}
    </button>
  );
}

/* The tool that syncs a source. */
export function ToolBadge({ tool }) {
  return <span className={`chip tool-chip tool-chip-${tool}`} title={`Synced with ${tool}`}>{tool}</span>;
}

/* A source's schedule in one line ("daily · next sync in 3 h"), or
   nothing when it has none. ``compact`` (a card): "in 3 h", "due",
   "paused" or "skipped", the whole line in its title. */
export function ScheduleLine({ source: s, compact = false }) {
  const text = scheduleText(s, ERRORS);
  if (!text) return null;
  const sch = s.schedule;
  const hint = sch.stopped ? "Syncing again on its own would not help: Sync it once it is fixed, or change its schedule"
    : sch.paused ? "Settings → Sync → Pause all schedules is on"
    : sch.skipped ? "Tried again every minute while it is due"
      : sch.next_at ? `Next: ${fmtFullDate(sch.next_at)}${sch.failures ? ` (${sch.failures} failed in a row: it waits longer)` : ""}`
        : "";
  return (
    <span className={`source-schedule${sch.skipped || sch.stopped || sch.failures ? " warn" : ""}`}
          title={compact ? `${text}\n${hint}` : hint}>
      <Icon name="clock" size={11} />{compact ? scheduleShort(s) : text}
    </span>
  );
}

/* How its account is doing (docs/API.md "Account health"): the last good
   sync, the output line behind a bad state (untrusted text, already
   scrubbed: shown as text only), the session the last sync used, and a
   new name its tool reported, to accept (the target changes, never the
   folder) or dismiss. */
export function SourceHealth({ source: s, job, onSaved }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const h = s.health;
  if (!h?.result) return null;
  const good = lastGood(h);
  const login = loginText(h);
  const rename = h.rename;

  async function answer(accept) {
    setBusy(true);
    try {
      const r = accept ? await acceptRename(s.id, rename.to) : await dismissRename(s.id);
      if (!r?.ok) { toast(r?.error || "Could not save.", "err"); return; }
      toast(accept ? `${sourceName(s)} is now ${sourceName(r.source)}; its folder stays as it is.`
        : `${sourceName(s)}: suggestion dismissed.`);
      onSaved?.(r.source);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="source-health">
      <span className="source-health-head">
        {good && <span className={h.ok_at ? undefined : "is-err"} title={h.ok_at ? fmtFullDate(h.ok_at) : undefined}>{good}</span>}
        {h.failures > 1 && <span className="is-err">{h.failures} failed in a row</span>}
        {login && <span title="From the last sync's output">{login}</span>}
      </span>
      {h.line && <code className="source-health-line" title={h.line}>{h.line}</code>}
      {rename && (
        <span className="source-rename" role="status">
          <span>Now called <b>{s.tool === "instaloader" ? `@${rename.to}` : rename.to}</b>?</span>
          {/* Together: never Dismiss alone on a line (#139). */}
          <span className="source-rename-actions">
            <button type="button" className="btn-link" disabled={busy || !!job}
                    title={job ? "Wait for its sync to end" : `Sync @${rename.to} from now on; the folder and its files stay as they are`}
                    onClick={() => answer(true)}>Accept</button>
            <button type="button" className="btn-link" disabled={busy} onClick={() => answer(false)}>Dismiss</button>
          </span>
        </span>
      )}
    </span>
  );
}

/* One source: its profile, what it downloads, tool, folder, schedule, last
   sync, Options, Sync and Remove. ``onSaved``: after its options changed. */
export function SourceRow({ source: s, job, onSync, onRemove, onSaved, flash = false }) {
  const [editing, setEditing] = useState(false);
  const url = safeUrl(s.url);
  const summary = optionsSummary(s);
  return (
    <li className={`source-row${flash ? " is-flash" : ""}`} data-source-id={s.id}>
      <span className="chip platform-chip" title={platformLabel(s.platform)}>{platformShort(s.platform)}</span>
      <span className="source-id">
        <span className="creator-name" title={sourceName(s)}>
          {sourceName(s)}
          {s.person && !s.account && <span className="creator-sub"> · first sync not done yet</span>}
        </span>
        {summary && <span className="source-summary" title={`What it downloads: ${summary}`}>{summary}</span>}
        <ScriptWarning source={s} />
        <ScheduleLine source={s} />
        <SourceHealth source={s} job={job} onSaved={onSaved} />
        <code className="source-folder" title={s.folder}>{s.folder}</code>
      </span>
      <ToolBadge tool={s.tool} />
      <SourceStatus source={s} job={job} />
      {url && (
        <a href={url} className="icon-btn" target="_blank" rel="noreferrer noopener"
           title={`Profile on ${platformLabel(s.platform)} (opens the site)`}
           aria-label={`${sourceName(s)} on ${platformLabel(s.platform)}`}>
          <Icon name="external" size={15} />
        </a>
      )}
      {job && <Link to="/jobs" className="btn-ghost">Log</Link>}
      <button type="button" className="btn-ghost" disabled={!!job} onClick={() => setEditing(true)}
              title={job ? "Its sync is queued or running" : "What this source downloads"}>
        Options
      </button>
      <SyncButton source={s} job={job} onSync={onSync} />
      <button type="button" className="btn-ghost" disabled={!!job} onClick={() => onRemove(s)}
              title={job ? "Cancel its sync first" : "Forget this source; files and posts stay"}>
        Remove
      </button>
      {editing && <SourceOptionsDialog source={s} onClose={() => setEditing(false)} onSaved={onSaved} />}
    </li>
  );
}

/* What a source downloads, as far as its tool and platform let it choose:
   content kinds, images or videos, a date floor and the first sync. Each
   with what it does in one line. ``firstSync``: the source has not synced
   yet (last N posts is for its first sync only). */
export function SourceOptions({ tool, platform, target, choices, session, form, onChange, firstSync = true }) {
  const id = useId();
  const each = firstPostsEach(tool, form.content, platform, target);
  const set = patch => onChange({ ...form, ...patch });
  const login = needsLogin(form, choices, session);
  const toggle = k => onChange(toggleKind(form, k));
  // A script runs instead of the tool's command: the choices before the
  // picker are not used (its note says so), so they are dimmed and
  // disabled while one is picked; the schedule still is (#149). Download
  // stays open while it needs a login the sync lacks: that is refused
  // either way, and unticking is the way out.
  const unused = form.script
    ? { disabled: true, className: "source-opt is-unused", title: "Not used: the script runs instead" }
    : { className: "source-opt" };
  return (
    <div className="source-options">
      {choices.content.length > 0 && (
        <fieldset {...(login.length ? { className: "source-opt" } : unused)}>
          <legend>Download</legend>
          {choices.content.map(k => (
            <label key={k} className="source-opt-line">
              <input type="checkbox" checked={form.content.includes(k)} onChange={() => toggle(k)} />
              <b>{kindLabel(platform, k)}</b>
              <span className="dim">{kindEffect(platform, k)}{choices.login.includes(k) && " · needs a login"}</span>
            </label>
          ))}
          {login.length > 0 && (
            <div className="msg err source-msg" role="alert">
              {login.map(k => kindLabel(platform, k)).join(", ")} need a logged-in session: set one in{" "}
              <Link to="/settings#sync" className="text-link">Settings → Sync</Link>.
            </div>
          )}
        </fieldset>
      )}
      {choices.media && (
        <fieldset {...unused}>
          <legend>Media</legend>
          {MEDIA.map(([v, label]) => (
            <label key={v} className="source-opt-line">
              <input type="radio" name={`${id}-media`} checked={form.media === v} onChange={() => set({ media: v })} />
              <b>{label}</b><span className="dim">{mediaEffect(tool, v)}</span>
            </label>
          ))}
        </fieldset>
      )}
      <fieldset {...unused}>
        <legend>Not older than</legend>
        <label className="source-opt-line">
          <input type="date" min="1970-01-01" max={today()} value={form.since} aria-label="Not older than"
                 onChange={e => set({ since: e.target.value })} />
          <span className="dim">{form.since ? "posts from before this day are never downloaded" : "no limit"}</span>
          {form.since && <button type="button" className="btn-link" onClick={() => set({ since: "" })}>Clear</button>}
        </label>
      </fieldset>
      <fieldset {...unused}>
        <legend>{firstSync ? "First sync" : "Next sync"}</legend>
        <label className="source-opt-line">
          <input type="radio" name={`${id}-first`} checked={form.first === "new"} onChange={() => set({ first: "new" })} />
          <b>new posts</b>
          <span className="dim">stops at the newest post FeedVault already has from this profile</span>
        </label>
        <label className="source-opt-line">
          <input type="radio" name={`${id}-first`} checked={form.first === "full"} onChange={() => set({ first: "full" })} />
          <b>full history</b><span className="dim">walks the whole profile, once</span>
        </label>
        {firstSync && choices.first_posts && (
          <label className="source-opt-line">
            <input type="radio" name={`${id}-first`} checked={form.first === "last"}
                   onChange={() => set({ first: "last", count: form.count || "50" })} />
            <b>only the last</b>
            <input type="number" min="1" max={FIRST_POSTS_MAX} step="1" className="source-opt-count"
                   aria-label="Number of posts" value={form.count}
                   onChange={e => set({ first: "last", count: e.target.value })} />
            <b>posts</b>
            <span className="dim">
              {each && `${each.trim()} (up to ${each.includes("kind") ? form.content.length : "a few"} × the number); `}
              later syncs only fetch newer ones
            </span>
          </label>
        )}
      </fieldset>
      <ScriptPicker tool={tool} value={form.script} onChange={script => set({ script })} />
      <fieldset className="source-opt">
        <legend>Schedule</legend>
        {SCHEDULES.map(([v, label, effect]) => (
          <label key={v} className="source-opt-line">
            <input type="radio" name={`${id}-schedule`} checked={form.schedule === v} onChange={() => set({ schedule: v })} />
            <b>{label}</b><span className="dim">{effect}</span>
          </label>
        ))}
      </fieldset>
    </div>
  );
}

// A script's line in the picker: short, its reason (if any) shown below it.
const OPTION_MAX = 60;
function scriptLabel(sc) {
  const text = `${sc.id}${sc.name && sc.name !== sc.id ? ` · ${sc.name}` : ""}`;
  return text.length > OPTION_MAX ? `${text.slice(0, OPTION_MAX - 1)}…` : text;
}

/* Which command a sync runs: the tool's own (the choices above), or a
   script from the Scripts page, grouped by what it runs. A script that runs
   another tool FeedVault locks (its ``group``) is not offered: a source's
   sync runs in its own tool's lock group, with that tool's pause, so it
   would run that tool beside its own syncs and without their pause (the
   backend refuses it too, docs/API.md "On a source"). A refused one is
   listed apart and cannot be picked; one gone from disk, refused or running
   another tool stays shown when it is the current one, with why its syncs
   fail. */
function ScriptPicker({ tool, value, onChange }) {
  const { data, error } = useApi(getScripts);
  const scripts = data?.scripts || [];
  const current = scripts.find(sc => sc.id === value);
  const usable = scripts.filter(sc => !sc.refused);
  const own = usable.filter(sc => sc.group === tool);
  const any = usable.filter(sc => sc.group === "scripts");
  const others = usable.filter(sc => sc.group !== tool && sc.group !== "scripts");
  const refused = scripts.filter(sc => sc.refused);
  const otherTools = [...new Set(others.map(sc => sc.group))].sort();
  const foreign = current && !current.refused && others.includes(current);
  const option = (sc, disabled = false) => (
    <option key={sc.id} value={sc.id} disabled={disabled} title={sc.name || sc.id}>
      {scriptLabel(sc)}{sc.refused ? " · refused" : ""}
    </option>
  );
  return (
    <fieldset className="source-opt">
      <legend>Command</legend>
      <label className="source-opt-line">
        <select className="sort-select script-pick" value={value} onChange={e => onChange(e.target.value)}
                aria-label="Command a sync runs" disabled={!data && !value}>
          <option value="">{tool}&rsquo;s own command, with the choices above</option>
          {value && !current && <option value={value}>{value} · not found</option>}
          {own.length > 0 && <optgroup label={`Runs ${tool}`}>{own.map(sc => option(sc))}</optgroup>}
          {any.length > 0 && <optgroup label="Runs another program (not a downloader)">{any.map(sc => option(sc))}</optgroup>}
          {foreign && <optgroup label={`Runs ${current.group}, not ${tool}`}>{option(current)}</optgroup>}
          {refused.length > 0 && (
            <optgroup label="Refused (see Scripts)">
              {refused.map(sc => option(sc, sc.id !== value))}
            </optgroup>
          )}
        </select>
      </label>
      {error && <div className="msg err source-msg" role="alert">Could not list the scripts: {error.message}</div>}
      {value && data && (current?.refused || foreign || !current) && (
        <span className="dl-warn script-pick-why" role="note">
          {current?.refused ? `${value}: refused: ${current.refused}. Its syncs fail.`
            : foreign ? `${value} runs ${current.group}, not ${tool}: its syncs fail. Pick one that runs ${tool}.`
              : `${value}: no such script now. Its syncs fail.`}
        </span>
      )}
      {others.length > 0 && (
        <span className="dim script-pick-note">
          {others.length === 1 ? "1 script runs" : `${others.length} scripts run`} {otherTools.join(", ")}, not shown:
          {" "}a sync of this source runs in {tool}&rsquo;s lock group, with its pause between syncs.
        </span>
      )}
      {value && (
        <span className="dim">
          A sync runs this script instead, with the source&rsquo;s target and folder; the choices above are not
          used, the schedule is. See <Link to={scriptHref(value)} className="text-link">Scripts</Link>.
        </span>
      )}
    </fieldset>
  );
}

/* Why a source's script would fail its next sync (``script_warning``):
   refused, gone, or running another tool; with the reason (the chmod fix
   in it) and a link to its entry on the Scripts page. */
const SCRIPT_STATES = { refused: "refused", missing: "not found", other_tool: "runs another tool" };
export function ScriptWarning({ source: s, name = null }) {
  const w = s.script_warning;
  if (!w) return null;
  const id = s.options.script;
  return (
    <span className="source-script-warn" role="note">
      <Icon name="warn" size={12} />
      <span>
        <b>{name && `${name}: `}script {id}: {SCRIPT_STATES[w.state] || w.state}</b>
        {" "}<span className="source-script-why">({w.reason})</span>. Its syncs fail.{" "}
        <Link to={scriptHref(id)} className="text-link">See Scripts</Link>
      </span>
    </span>
  );
}

/* Edit what a source downloads. ``s.session``: the session its sync
   would use, for the login hint. Last N stays open until a first sync
   with it has worked. */
export function SourceOptionsDialog({ source: s, onClose, onSaved }) {
  const toast = useToast();
  const [form, setForm] = useState(() => formOf(s.options, s.choices));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const firstSync = !s.last_sync_at || s.options.first_posts != null;
  const problem = formError(form, s.choices);
  const focus = useRef(null);
  // Esc, Cancel or a click outside ask before changed choices go, as
  // every other form does since #125/#132 (#149). Compared as they would
  // be saved, so ticking a box off and on again is no change.
  const [asking, setAsking] = useState(false);
  const changed = JSON.stringify(optionsOf(form, s.choices, firstSync))
    !== JSON.stringify(optionsOf(formOf(s.options, s.choices), s.choices, firstSync));
  const cancel = () => (changed ? setAsking(true) : onClose());
  // A link in it ("See Scripts", "Settings → Sync"), Back or Forward ask
  // the same before the app leaves the page with them (#153).
  const unsaved = useUnsaved(changed);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await updateSource(s.id, optionsOf(form, s.choices, firstSync));
      if (!r?.ok) { setError(r?.error || "Could not save."); return; }
      toast(`${sourceName(s)}: options saved.`);
      onSaved?.(r.source);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <ConfirmDialog
        open
        title={`What ${sourceName(s)} downloads`}
        confirmLabel="Save"
        busy={busy}
        error={error || problem}
        confirmDisabled={!!problem || needsLogin(form, s.choices, s.session).length > 0}
        onConfirm={save}
        onCancel={cancel}
        initialFocus={focus}
      >
        <div ref={focus} tabIndex={-1}>
          <SourceOptions tool={s.tool} platform={s.platform} target={s.target} choices={s.choices} session={s.session} form={form}
                         onChange={setForm} firstSync={firstSync} />
        </div>
      </ConfirmDialog>
      <DiscardDialog open={asking || unsaved.asking}
                     onDiscard={unsaved.asking ? () => { unsaved.discard(); onClose(); } : onClose}
                     onKeep={unsaved.asking ? unsaved.keep : () => setAsking(false)}>
        The changed choices for {sourceName(s)} are not saved.
      </DiscardDialog>
    </>
  );
}

/* Remove, confirmed: the folder, its files and posts stay. */
export function RemoveSourceDialog({ source, onRemove, onClose }) {
  const toast = useToast();
  const [busy,  setBusy]  = useState(false);
  const [error, setError] = useState(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await onRemove(source);
      if (!r?.ok) { setError(r?.error || "Could not remove."); return; }
      toast(`${sourceName(source)} is no longer a source.`);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog
      open={!!source}
      title={source ? `Remove ${sourceName(source)}?` : ""}
      confirmLabel="Remove source"
      danger
      busy={busy}
      error={error}
      onConfirm={run}
      onCancel={onClose}
    >
      FeedVault stops syncing it. Its folder, files and posts stay as they are.
    </ConfirmDialog>
  );
}

// A bare name or @name is an Instagram profile; anything else is a link.
const HANDLE = /^@?[A-Za-z0-9._]{1,30}$/;
function isHandle(text) {
  return HANDLE.test(text) && (text.startsWith("@") || !text.includes("."));
}

/* What a pasted link would add, asked as the user types: the tool the
   routing table picks, the platform and the folder, or why it is refused. */
function useResolved(text) {
  // Kept with the text it answers, so a stale answer is never shown.
  const [resolved, setResolved] = useState({ text: null, r: null });
  useEffect(() => {
    if (!text) return undefined;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      resolveSource(text, { signal: ctl.signal }, isHandle(text) ? "instaloader" : undefined)
        .then(r => setResolved({ text, r }))
        .catch(err => { if (err.name !== "AbortError") setResolved({ text, r: { ok: false, error: err.message } }); });
    }, 250);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [text]);
  return resolved.text === text ? resolved.r : null;
}

/* Paste a profile link (the tool comes from Settings → Link routing), or an
   Instagram name. Before saving it shows the tool, platform and folder,
   and what the source can download. onDirty(bool) hears whether something
   is typed and not added yet (false again when the form goes). */
export function AddSource({ person = null, onAdded, onDirty }) {
  const toast = useToast();
  const [target, setTarget] = useState("");
  const [forms,  setForms]  = useState({});      // the options picked, by tool and platform
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState(null);
  const text = target.trim();
  const resolved = useResolved(text);
  const kind = resolved?.ok ? `${resolved.tool}:${resolved.platform}:${resolved.choices.content.join(",")}` : null;
  const form = kind && (forms[kind] || formOf(null, resolved.choices));
  const problem = form && formError(form, resolved.choices);
  const login = form ? needsLogin(form, resolved.choices, resolved.session) : [];

  useEffect(() => {
    onDirty?.(!!text);
    return () => onDirty?.(false);
  }, [text, onDirty]);

  async function add(e) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      const body = { target: text, options: optionsOf(form, resolved.choices) };
      if (isHandle(text)) body.tool = "instaloader";
      if (person != null) body.person = person;
      const r = await createSource(body);
      if (!r?.ok) { setError(r?.error || "Could not add the source."); return; }
      toast(`${sourceName(r.source)} added. Sync it to download its posts.`);
      setTarget("");
      setForms({});
      onAdded?.(r.source);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="source-add" onSubmit={add}>
      <input
        type="text"
        className="page-filter"
        placeholder="Profile link: x.com/name, tiktok.com/@name, @instagram_name…"
        aria-label="Profile link, or an Instagram name"
        maxLength={500}
        value={target}
        onChange={e => { setTarget(e.target.value); setError(null); }}
      />
      <button type="submit" className="btn-secondary"
              disabled={busy || !form || resolved.source != null || !!problem || login.length > 0}>
        <Icon name="plus" size={13} />Add source
      </button>
      {resolved?.ok && (
        <div className="source-resolved" role="status">
          <ToolBadge tool={resolved.tool} />
          <span className="chip platform-chip" title={platformLabel(resolved.platform)}>{platformShort(resolved.platform)}</span>
          <span className="source-resolved-target">{resolved.target.replace(/^https:\/\//, "")}</span>
          <span className="dim">into</span>
          <code className="source-folder" title={resolved.folder}>{resolved.folder}</code>
          {resolved.source != null && <span className="is-err">already a source</span>}
        </div>
      )}
      {form && resolved.source == null && (
        <SourceOptions tool={resolved.tool} platform={resolved.platform} target={resolved.target} choices={resolved.choices}
                       session={resolved.session} form={form}
                       onChange={f => { setForms(fs => ({ ...fs, [kind]: f })); setError(null); }} />
      )}
      {(error || problem || resolved?.ok === false) && (
        <div className="msg err source-msg" role="alert">{error || problem || resolved.error}</div>
      )}
    </form>
  );
}

/* "Sync all": the button, then n of m with the one running or waiting, a
   link to the Jobs page and Stop (cancels what has not run yet and the
   running one). */
export function SyncAllBar({ count, syncAll }) {
  const { batch, start, clear, busy } = syncAll;
  const { started } = useJobs();
  const toast = useToast();
  const [stopping, setStopping] = useState(false);

  async function stop() {
    setStopping(true);
    try {
      // The queued ones first, so none starts while the running one stops.
      const jobs = [...batch.jobs].sort((a, b) => (a.state === "running") - (b.state === "running"));
      for (const j of jobs) await cancelJob(j.id);
      started(jobs[0]);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setStopping(false);
    }
  }

  if (batch && !batch.done) {
    const c = batch.current;
    const n = Math.min(batch.ended + 1, batch.total);
    return (
      <div className="sync-all" role="status">
        <Icon name="refresh" size={14} className={c?.state === "running" ? "spin" : undefined} />
        <span>
          Syncing <b>{n}</b> of <b>{batch.total}</b>
          {c && <> · {c.label.replace(/^Sync /, "")} {c.state === "running" ? "running" : c.waits_until ? `starts ${fmtIn(c.waits_until)}` : "queued"}</>}
          {batch.added > 0 && ` · ${fmtInt(batch.added)} new posts so far`}
        </span>
        <div className="page-head-spacer" />
        <Link to="/jobs" className="btn-ghost">Jobs</Link>
        <button type="button" className="btn-danger-soft" disabled={stopping} onClick={stop}>
          <Icon name="close" size={13} />Stop
        </button>
      </div>
    );
  }
  // Cancelled ones never synced: counted apart (#126).
  const synced = batch ? batch.total - (batch.cancelled ?? 0) : 0;
  return (
    <div className="sync-all">
      {batch?.done && (
        <span>
          Synced {fmtInt(synced)} source{synced === 1 ? "" : "s"}: {fmtInt(batch.added)} new post{batch.added === 1 ? "" : "s"}
          {batch.failed > 0 && <>, <span className="is-err">{batch.failed} failed</span></>}
          {batch.cancelled > 0 && `, ${fmtInt(batch.cancelled)} cancelled`}.
          {" "}<button type="button" className="btn-link" onClick={clear}>Hide</button>
        </span>
      )}
      <div className="page-head-spacer" />
      <button type="button" className="btn-secondary" disabled={busy || !count} onClick={start}
              title="Sync every source, one after another, with a pause between them">
        <Icon name="refresh" size={13} />Sync all{count ? ` (${fmtInt(count)})` : ""}
      </button>
    </div>
  );
}
