import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { acceptRename, createSource, cancelJob, dismissRename, getScripts, resolveSource, updateSource } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { useJobs } from "../lib/jobs";
import { ERRORS, SETUP_ERRORS, sourceName } from "../lib/sources";
import { healthBadge, lastGood, loginText } from "../lib/health";
import { fmtAgo, fmtFullDate, fmtInt, platformLabel, platformShort, safeUrl } from "../lib/fmt";
import {
  FIRST_POSTS_MAX, MEDIA, SCHEDULES, firstPostsEach, formError, formOf, kindEffect, kindLabel, mediaEffect, needsLogin,
  optionsOf, optionsSummary, scheduleShort, scheduleText, toggleKind, today,
} from "../lib/sourceOptions";
import Icon from "./Icon";
import ConfirmDialog from "./ConfirmDialog";

// "in 40 s", "in 2 min"
function fmtIn(ts) {
  const s = Math.max(0, Math.round(ts - Date.now() / 1000));
  return s < 60 ? `in ${s} s` : `in ${Math.ceil(s / 60)} min`;
}

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
        <Link to="/settings#downloaders" className="text-link source-setup">Settings → Downloaders</Link>
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
          <button type="button" className="btn-link" disabled={busy || !!job}
                  title={job ? "Wait for its sync to end" : `Sync @${rename.to} from now on; the folder and its files stay as they are`}
                  onClick={() => answer(true)}>Accept</button>
          <button type="button" className="btn-link" disabled={busy} onClick={() => answer(false)}>Dismiss</button>
        </span>
      )}
    </span>
  );
}

/* One source: its profile, what it downloads, tool, folder, schedule, last
   sync, Options, Sync and Remove. ``onSaved``: after its options changed. */
export function SourceRow({ source: s, job, onSync, onRemove, onSaved }) {
  const [editing, setEditing] = useState(false);
  const url = safeUrl(s.url);
  const summary = optionsSummary(s);
  return (
    <li className="source-row">
      <span className="chip platform-chip" title={platformLabel(s.platform)}>{platformShort(s.platform)}</span>
      <span className="source-id">
        <span className="creator-name" title={sourceName(s)}>
          {sourceName(s)}
          {s.person && !s.account && <span className="creator-sub"> · first sync not done yet</span>}
        </span>
        {summary && <span className="source-summary" title="What it downloads">{summary}</span>}
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
  return (
    <div className="source-options">
      {choices.content.length > 0 && (
        <fieldset className="source-opt">
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
        <fieldset className="source-opt">
          <legend>Media</legend>
          {MEDIA.map(([v, label]) => (
            <label key={v} className="source-opt-line">
              <input type="radio" name={`${id}-media`} checked={form.media === v} onChange={() => set({ media: v })} />
              <b>{label}</b><span className="dim">{mediaEffect(tool, v)}</span>
            </label>
          ))}
        </fieldset>
      )}
      <fieldset className="source-opt">
        <legend>Not older than</legend>
        <label className="source-opt-line">
          <input type="date" min="1970-01-01" max={today()} value={form.since} aria-label="Not older than"
                 onChange={e => set({ since: e.target.value })} />
          <span className="dim">{form.since ? "posts from before this day are never downloaded" : "no limit"}</span>
          {form.since && <button type="button" className="btn-link" onClick={() => set({ since: "" })}>Clear</button>}
        </label>
      </fieldset>
      <fieldset className="source-opt">
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

/* Which command a sync runs: the tool's own (the choices above), or a
   script from the Scripts page. A refused one shows why and cannot be
   picked; one gone from disk stays shown, so the sync's failure is not a
   surprise. */
function ScriptPicker({ tool, value, onChange }) {
  const { data, error } = useApi(getScripts);
  const scripts = data?.scripts || [];
  const current = scripts.find(sc => sc.id === value);
  return (
    <fieldset className="source-opt">
      <legend>Command</legend>
      <label className="source-opt-line">
        <select className="sort-select script-pick" value={value} onChange={e => onChange(e.target.value)}
                aria-label="Command a sync runs" disabled={!data && !value}>
          <option value="">{tool}&rsquo;s own command, with the choices above</option>
          {value && !current && <option value={value}>{value} (not found)</option>}
          {scripts.map(sc => (
            <option key={sc.id} value={sc.id} disabled={!!sc.refused}>
              {`${sc.id}${sc.name && sc.name !== sc.id ? ` · ${sc.name}` : ""}${sc.tool && sc.tool !== tool ? ` (${sc.tool})` : ""}`
                + (sc.refused ? ` · refused: ${sc.refused}` : "")}
            </option>
          ))}
        </select>
      </label>
      {error && <div className="msg err source-msg" role="alert">Could not list the scripts: {error.message}</div>}
      {value && (
        <span className="dim">
          {current?.refused ? <span className="dl-warn">refused: {current.refused}. Its syncs fail. </span>
            : !current && data ? <span className="dl-warn">no such script now: its syncs fail. </span> : null}
          A sync runs this script instead, with the source&rsquo;s target and folder; the choices above are not
          used, the schedule is. See <Link to="/scripts" className="text-link">Scripts</Link>.
        </span>
      )}
    </fieldset>
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
    <ConfirmDialog
      open
      title={`What ${sourceName(s)} downloads`}
      confirmLabel="Save"
      busy={busy}
      error={error || problem}
      confirmDisabled={!!problem || needsLogin(form, s.choices, s.session).length > 0}
      onConfirm={save}
      onCancel={onClose}
      initialFocus={focus}
    >
      <div ref={focus} tabIndex={-1}>
        <SourceOptions tool={s.tool} platform={s.platform} target={s.target} choices={s.choices} session={s.session} form={form}
                       onChange={setForm} firstSync={firstSync} />
      </div>
    </ConfirmDialog>
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
   and what the source can download. */
export function AddSource({ person = null, onAdded }) {
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
  return (
    <div className="sync-all">
      {batch?.done && (
        <span>
          Synced {fmtInt(batch.total)} source{batch.total === 1 ? "" : "s"}: {fmtInt(batch.added)} new post{batch.added === 1 ? "" : "s"}
          {batch.failed > 0 && <>, <span className="is-err">{batch.failed} failed</span></>}.
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
