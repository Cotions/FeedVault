import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import {
  acceptRename, createPerson, createSource, cancelJob, dismissRename, getPeople, getScripts, giveSourceTool, resolveSource,
  setSourcePerson, updateSource,
} from "../lib/api";
import { useApi } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { useJobs } from "../lib/jobs";
import { DOWNLOADS, ERRORS, SETUP_ERRORS, downloadWanted, sourceName } from "../lib/sources";
import { healthBadge, lastGood, loginText } from "../lib/health";
import { fmtAgo, fmtFullDate, fmtIn, fmtInt, platformLabel, safeUrl } from "../lib/fmt";
import {
  FIRST_POSTS_MAX, MEDIA, PRESET_CUSTOM, PRESET_ONLY, SCHEDULES, customBody, downloadOn, firstPostsEach, formError, formOf,
  kindEffect, kindLabel, mediaEffect, metadataEffect, metadataLabel, needsLogin, optionsSummary, presetBody, presetName,
  presetValue, resolvedPreset, restoreBody, sameOptions, scheduleShort, scheduleText, scriptHref, toggleKind, today,
} from "../lib/sourceOptions";
import Icon from "./Icon";
import ActionMenu from "./ActionMenu";
import PlatformIcon from "./PlatformIcon";
import BrowserPick from "./BrowserPick";
import { cookiesSession } from "../lib/browserProfiles";
import ConfirmDialog from "./ConfirmDialog";
import DiscardDialog from "./DiscardDialog";
import { useUnsaved } from "../lib/unsaved";
import { focusLost, restoreFocus, trapTab } from "../lib/layout";
import PersonPicker from "./PersonPicker";

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

/* What a source follows (docs/API.md "Presets"), picked in one select:
   Default, each named preset of its tool and platform, "Custom…" (its
   own options, in a dialog) and, apart, "Profile only". ``presets``:
   [{ id, name }]; ``custom``: whether Custom… is offered. */
export function PresetSelect({ value, presets, onChange, label, title, disabled = false, custom = true, selectRef = null }) {
  return (
    <select ref={selectRef} className="sort-select preset-select" aria-label={label} title={title} value={value}
            disabled={disabled} onChange={e => onChange(e.target.value)}>
      <option value="">Default</option>
      {(presets || []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
      {custom && <option value={PRESET_CUSTOM}>Custom…</option>}
      <option disabled>──────────</option>
      <option value={PRESET_ONLY}>Profile only (never download)</option>
    </select>
  );
}

/* "Not synced": its account or person is set to Don't sync (docs/API.md
   "Don't sync"), so the source is kept but never runs. */
export function NotSyncedChip({ title = "Set to Don't sync: kept, never synced. Turn it off from the ⋯ menu of its card or page" }) {
  return (
    <span className="chip nosync-chip" title={title}>
      <Icon name="syncOff" size={11} />Not synced
    </span>
  );
}

/* "Profile only" (#218): shown as theirs, never downloaded: its Download
   option says never, its platform is in Settings → Profile only, or no
   downloader takes its site (tool "none"). ``source``: says which. */
export function ProfileOnlyChip({ source: s = null, title = null }) {
  const why = title || (s?.tool === "none" ? "No downloader takes this site: shown as theirs, never synced"
    : s?.options?.download === "off" ? "Set to Profile only: shown as theirs, never synced"
      : "Its platform is profile only (Settings → Sync → Profile only): shown as theirs, never synced");
  return (
    <span className="chip nosync-chip profile-only-chip" title={why}>
      <Icon name="eye" size={11} />Profile only
    </span>
  );
}

/* One source: its profile, the preset it follows (a select: picking one
   saves at once, "Custom…" opens its own options, "Profile only" stops
   its downloads), tool, folder, schedule, last sync and Sync; its profile
   on the site, its custom options, a full-history sync, its creator
   (link, move, unlink) and Remove in its ⋯ menu. ``onSaved``: after what
   it follows changed; ``onLinked``: after its person did (the people's
   cards move too), else onSaved. One set to Don't sync is marked so and
   keeps its creator and Remove only: no preset, no Sync, no schedule line
   (its schedule waits for the switch to go off). One that is profile only
   (#218) is dimmed, with no Sync and no sync state: its select says so,
   and picking a preset downloads it again. A profile no downloader takes
   (tool "none") has the chip instead, and its folder in its ⋯ menu. */
export function SourceRow({ source: s, job, onSync, onRemove, onSaved, onLinked = onSaved, flash = false }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [linking, setLinking] = useState(false);
  const [history, setHistory] = useState(false);  // "Sync full history…" is asking
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const url = safeUrl(s.url);
  const name = sourceName(s);
  const summary = optionsSummary(s);
  const off = !!s.no_sync;
  const only = !!s.profile_only;
  const none = s.tool === "none";
  const quiet = off || only;                   // never synced: no sync state, no Sync
  const along = s.account ? "; its account goes with it" : "";

  async function unlink() {
    try {
      const r = await setSourcePerson(s.id, null);
      if (!r?.ok) { toast(r?.error || "Could not unlink.", "err"); return; }
      toast(`${name} is no longer ${s.person.name}'s.`);
      onLinked?.(r.source);
    } catch (err) {
      toast(err.message, "err");
    }
  }

  // Back as it was before a pick, from its toast.
  async function undo() {
    try {
      const r = await updateSource(s.id, restoreBody(s));
      if (!r?.ok) { toast(r?.error || "Could not undo.", "err"); return; }
      toast(`${name}: ${presetName(r.source)} again.`);
      onSaved?.(r.source);
    } catch (err) {
      toast(err.message, "err");
    }
  }

  // A pick saves at once; Custom… opens its options instead.
  async function follow(value) {
    if (value === PRESET_CUSTOM) { setEditing(true); return; }
    setBusy(true);
    try {
      const r = await updateSource(s.id, presetBody(s, value));
      if (!r?.ok) { toast(r?.error || "Could not save.", "err"); return; }
      toast(value === PRESET_ONLY ? `${name} is profile only: shown as theirs, never downloaded.`
        : `${name} follows ${presetName(r.source)}.`, "ok", { label: "Undo", onClick: undo });
      onSaved?.(r.source);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function fullHistory() {
    setBusy(true);
    setError(null);
    try {
      const r = await updateSource(s.id, { options: { full_history: true } });
      if (!r?.ok) { setError(r?.error || "Could not save."); return; }
      setHistory(false);
      onSaved?.(r.source);
      onSync(r.source);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={`source-row${flash ? " is-flash" : ""}${off ? " is-nosync" : ""}${only ? " is-profile-only" : ""}`}
        data-source-id={s.id}>
      <PlatformIcon platform={s.platform} />
      <span className="source-id">
        <span className="creator-name" title={name}>
          {name}
          {s.person && !s.account && <span className="creator-sub"> · first sync not done yet</span>}
        </span>
        <span className="source-summary-line">
          {off && <NotSyncedChip />}
          {none && only && <ProfileOnlyChip source={s} />}
          {!off && !none && (
            <PresetSelect value={presetValue(s)} presets={s.presets} onChange={follow} disabled={!!job || busy}
                          label={`Preset of ${name}`}
                          title={job ? "Its sync is queued or running"
                            : only ? "Profile only: shown as theirs, never downloaded. Pick a preset to download it"
                              : `${presetName(s)}: ${summary || "new posts"}`} />
          )}
        </span>
        {s.needs_tool && (
          <span className="source-note dim">No downloader yet: pick its folder in ⋯ → Download options to download it.</span>
        )}
        {!quiet && <ScriptWarning source={s} />}
        {!quiet && <ScheduleLine source={s} />}
        {!quiet && <SourceHealth source={s} job={job} onSaved={onSaved} />}
        {s.folder && <code className="source-folder" title={s.folder}>{s.folder}</code>}
      </span>
      {!none && <ToolBadge tool={s.tool} />}
      {!quiet && <SourceStatus source={s} job={job} />}
      {job && <Link to="/jobs" className="btn-ghost">Log</Link>}
      {!quiet && <SyncButton source={s} job={job} onSync={onSync} />}
      <ActionMenu label={`More for ${name}`} items={[
        url && { label: "Open profile", icon: "external", href: url, title: `Profile on ${platformLabel(s.platform)} (opens the site)` },
        !off && none && { label: "Download options…", icon: "settings", onSelect: () => setEditing(true),
                          title: "Whether this profile is downloaded" },
        !off && !none && s.custom && { label: "Edit custom…", icon: "settings", disabled: !!job, onSelect: () => setEditing(true),
                                       title: job ? "Its sync is queued or running" : "Its own options" },
        !quiet && !none && { label: "Sync full history…", icon: "refresh", disabled: !!job,
                             onSelect: () => { setError(null); setHistory(true); },
                             title: job ? "Its sync is queued or running" : "Walk the whole profile once, not only its new posts" },
        { label: s.person ? "Move to creator…" : "Link to creator…", icon: "users", onSelect: () => setLinking(true),
          title: s.person ? `Show it with someone other than ${s.person.name}${along}` : `Show it with a person, one there is or a new one${along}` },
        s.person && { label: "Unlink from creator", icon: "close", onSelect: unlink,
                      title: `Back to the sources linked to nobody${s.account ? `; its account leaves ${s.person.name} too` : ""}. No post or file is touched` },
        "separator",
        { label: "Remove", icon: "trash", danger: true, disabled: !!job, onSelect: () => onRemove(s),
          title: job ? "Cancel its sync first" : "Forget this source; files and posts stay" },
      ]} />
      {editing && !off && <SourceOptionsDialog source={s} onClose={() => setEditing(false)} onSaved={onSaved} />}
      {linking && <SourcePersonDialog source={s} onClose={() => setLinking(false)} onLinked={onLinked} />}
      <ConfirmDialog open={history} title={`Sync ${name}'s full history?`} confirmLabel="Sync full history" busy={busy}
                     error={error} onConfirm={fullHistory} onCancel={() => setHistory(false)}>
        It walks the whole profile once: every post still there, not only the new ones. That takes longer, and a
        site is more likely to slow it down. Later syncs fetch new posts only.
      </ConfirmDialog>
    </li>
  );
}

/* A set of download options, as far as the tool and platform let one
   choose: content kinds, images or videos, a date floor, the first sync,
   login, command, metadata files and schedule. Each with what it does in
   one line. It is a preset's form (Settings → Presets) and a source's own
   (Custom…). ``firstSync``: "only the last N posts" is offered (a preset,
   or a source that has not synced yet). ``metadata``: { setting, ffmpeg },
   for the Metadata files choice. */
export function SourceOptions({ tool, platform, target, choices, session, form, onChange, firstSync = true, metadata = null }) {
  const id = useId();
  const each = firstPostsEach(tool, form.content, platform, target);
  const set = patch => onChange({ ...form, ...patch });
  const login = needsLogin(form, choices, session);
  const toggle = k => onChange(toggleKind(form, k));
  const setting = metadata?.setting || "full";
  const modes = choices.metadata || [];
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
      {firstSync && choices.first_posts && (
        <fieldset {...unused}>
          <legend>First sync</legend>
          <label className="source-opt-line">
            <input type="radio" name={`${id}-first`} checked={form.first === "new"} onChange={() => set({ first: "new" })} />
            <b>new posts</b>
            <span className="dim">stops at the newest post FeedVault already has from the profile</span>
          </label>
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
        </fieldset>
      )}
      <SessionPicker value={form.session} onChange={session => set({ session })} unused={unused} />
      <ScriptPicker tool={tool} value={form.script} onChange={script => set({ script })} />
      {modes.length > 1 && (
        <fieldset {...unused}>
          <legend>Metadata files</legend>
          <label className="source-opt-line">
            <select className="sort-select" aria-label="Metadata files a sync writes" value={form.metadata}
                    onChange={e => set({ metadata: e.target.value })}>
              <option value="">as in Settings → Sync ({metadataLabel(setting)})</option>
              {modes.map(m => <option key={m} value={m}>{metadataLabel(m)}</option>)}
            </select>
            <span className="dim">{metadataEffect(tool, form.metadata || setting)}</span>
          </label>
          {metadata?.ffmpeg === false && (form.metadata || setting) === "none" && <NoFfmpeg />}
        </fieldset>
      )}
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

/* yt-dlp's "none" embeds the metadata with ffmpeg: with none found its
   syncs write the files (full) instead, and say so in their log. */
export function NoFfmpeg() {
  return (
    <span className="dl-warn" role="note">
      ffmpeg not found: yt-dlp writes the metadata files (full) instead until FeedVault finds it, see{" "}
      <Link to="/settings#downloaders" className="text-link">Settings → Downloaders</Link>.
    </span>
  );
}

/* The login a sync uses: the tool's, from Settings → Sync (null), or the
   preset's or source's own: none, or a browser's cookies, with the
   browser profile they come from. One made elsewhere (a saved instaloader
   session, through the API) stays as it is until another is picked. */
const SESSION_EFFECT = {
  "": "the tool\u2019s, set in Settings \u2192 Sync",
  none: "public posts only, whatever Settings says",
  cookies: "its own: the tool reads that browser\u2019s cookies itself",
  login: "its own saved instaloader session",
};
function SessionPicker({ value, onChange, unused }) {
  const mode = value?.mode || "";
  const pick = m => onChange(m === "" ? null : m === "cookies" ? cookiesSession("firefox", "") : m === "none" ? { mode: m } : value);
  return (
    <fieldset {...unused}>
      <legend>Login</legend>
      <label className="source-opt-line">
        <select className="sort-select" aria-label="Login a sync uses" value={mode} onChange={e => pick(e.target.value)}>
          <option value="">as in Settings</option>
          <option value="none">no login</option>
          <option value="cookies">my browser&rsquo;s login</option>
          {mode === "login" && <option value="login">instaloader session of {value.user}</option>}
        </select>
        <span className="dim">{SESSION_EFFECT[mode]}</span>
      </label>
      {mode === "cookies" && (
        <BrowserPick className="sort-select" browser={value.browser} profile={value.profile || ""} disabled={!!unused.disabled}
                     onChange={p => onChange(cookiesSession(p.browser, p.profile))} />
      )}
    </fieldset>
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
          {" "}a sync runs in {tool}&rsquo;s lock group, with its pause between syncs.
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

/* Whether a source downloads (#218, docs/API.md "Profile only"): as its
   platform says (Settings → Profile only), always, or never. ``platformOnly``:
   its platform is listed there. */
function DownloadPicker({ value, onChange, platform, platformOnly }) {
  const id = useId();
  const follow = platformOnly ? "profile only now (Settings → Sync → Profile only)" : "downloads now";
  return (
    <fieldset className="source-opt">
      <legend>Download this profile</legend>
      {DOWNLOADS.map(([v, label, effect]) => (
        <label key={v} className="source-opt-line">
          <input type="radio" name={`${id}-download`} checked={value === v} onChange={() => onChange(v)} />
          <b>{label}</b>
          <span className="dim">{v === "platform" ? `${platformLabel(platform)}: ${follow}` : effect}</span>
        </label>
      ))}
    </fieldset>
  );
}

/* The folder a profile with no downloader (tool "none") downloads into
   once Download is on: the tool and default folder the routing table
   gives its link (GET /api/sources/resolve), or why none takes it. */
function useToolFor(s, wanted) {
  const [answer, setAnswer] = useState(null);
  useEffect(() => {
    if (!wanted || s.tool !== "none") return undefined;
    const ctl = new AbortController();
    resolveSource(s.url, { signal: ctl.signal })
      .then(setAnswer)
      .catch(err => { if (err.name !== "AbortError") setAnswer({ ok: false, error: err.message }); });
    return () => ctl.abort();
  }, [s.tool, s.url, wanted]);
  return wanted && s.tool === "none" ? answer : null;
}

/* A source's dialog: its own options (Custom…), or, for a profile no
   downloader takes (tool "none"), whether it downloads. */
export function SourceOptionsDialog({ source, onClose, onSaved }) {
  const Dialog = source.tool === "none" ? ProfileDownloadDialog : CustomDialog;
  return <Dialog source={source} onClose={onClose} onSaved={onSaved} />;
}

/* What a source follows, from a card's ⋯ menu (a card has no room for
   its select): the same select, saved with Save; "Custom…" goes on to
   its own options. */
export function SourcePresetDialog({ source: s, onClose, onSaved }) {
  const toast = useToast();
  const [value, setValue] = useState(() => presetValue(s));
  const [custom, setCustom] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const select = useRef(null);
  const summary = optionsSummary(s);

  async function save() {
    if (value === PRESET_CUSTOM) { setCustom(true); return; }
    if (value === presetValue(s)) { onClose(); return; }
    setBusy(true);
    setError(null);
    try {
      const r = await updateSource(s.id, presetBody(s, value));
      if (!r?.ok) { setError(r?.error || "Could not save."); return; }
      toast(value === PRESET_ONLY ? `${sourceName(s)} is profile only: shown as theirs, never downloaded.`
        : `${sourceName(s)} follows ${presetName(r.source)}.`);
      onSaved?.(r.source);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (custom) return <SourceOptionsDialog source={s} onClose={onClose} onSaved={onSaved} />;
  return (
    <ConfirmDialog open title={`Preset of ${sourceName(s)}`} confirmLabel={value === PRESET_CUSTOM ? "Edit custom…" : "Save"}
                   busy={busy} error={error} onConfirm={save} onCancel={onClose} initialFocus={select}>
      <div className="source-preset-pick">
        <PresetSelect value={value} presets={s.presets} onChange={v => { setValue(v); setError(null); }}
                      label={`Preset of ${sourceName(s)}`} selectRef={select} />
        <span className="dim">
          Now {presetName(s)}{summary ? `: ${summary}` : ""}. Presets are made in{" "}
          <Link to="/settings#presets" className="text-link">Settings → Presets</Link>.
        </span>
      </div>
    </ConfirmDialog>
  );
}

/* A source's own options (docs/API.md "Presets", Custom): a plain form of
   what it downloads now; saved, every choice is its own and it follows no
   preset. Opened from "Custom…" in its select, or "Edit custom…" in its ⋯
   menu once it is. Saving one that was profile only downloads it again,
   as picking a preset does. ``s.session``: the session its sync would
   use, for the login hint. Last N stays open until a first sync with it
   has worked. */
function CustomDialog({ source: s, onClose, onSaved }) {
  const toast = useToast();
  // Until a sync has worked (s.synced, sources.synced_ok): a failed first one leaves last N open.
  const firstSync = !s.synced || s.options.first_posts != null;
  const [initial] = useState(() => formOf(s.options, s.choices));
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const problem = formError(form, s.choices);
  const focus = useRef(null);
  // Esc, Cancel or a click outside ask before changed choices go, as
  // every other form does since #125/#132 (#149). Compared as they would
  // be saved, so ticking a box off and on again is no change.
  const [asking, setAsking] = useState(false);
  const changed = !sameOptions(form, initial, s.choices, firstSync);
  const cancel = () => (changed ? setAsking(true) : onClose());
  // A link in it ("See Scripts", "Settings → Sync"), Back or Forward ask
  // the same before the app leaves the page with them (#153).
  const unsaved = useUnsaved(changed);
  const followed = s.preset?.name || "Default";

  async function save() {
    // Custom already, and nothing to change.
    if (s.custom && !changed && !s.profile_only) { onClose(); return; }
    setBusy(true);
    setError(null);
    try {
      const r = await updateSource(s.id, customBody(s, form, firstSync));
      if (!r?.ok) { setError(r?.error || "Could not save."); return; }
      toast(`${sourceName(s)}: custom options saved.`);
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
        title={`Custom options for ${sourceName(s)}`}
        confirmLabel="Save"
        busy={busy}
        error={error || problem}
        confirmDisabled={!!problem || needsLogin(form, s.choices, s.session).length > 0}
        onConfirm={save}
        onCancel={cancel}
        initialFocus={focus}
      >
        <div ref={focus} tabIndex={-1}>
          <p className="source-custom-note dim">
            {s.custom ? "Its own options: no preset changes them."
              : `As ${followed} has them now. Saved, they are its own: a change to ${followed} no longer reaches it.`}
            {" "}Pick a preset on its row to follow one again.
          </p>
          <SourceOptions tool={s.tool} platform={s.platform} target={s.target} choices={s.choices} session={s.session} form={form}
                         onChange={setForm} firstSync={firstSync} metadata={s.metadata} />
        </div>
      </ConfirmDialog>
      <DiscardDialog open={asking || unsaved.asking}
                     onDiscard={unsaved.asking ? () => { unsaved.discard(); onClose(); } : onClose}
                     onKeep={unsaved.asking ? unsaved.keep : () => setAsking(false)}>
        The changed options for {sourceName(s)} are not saved.
      </DiscardDialog>
    </>
  );
}

/* Whether a profile with no downloader (tool "none") downloads: on, it
   asks for the folder, and the routing table gives the tool. */
function ProfileDownloadDialog({ source: s, onClose, onSaved }) {
  const toast = useToast();
  const [download, setDownload] = useState(s.options.download || "platform");
  const wanted = downloadWanted(download, s.platform_profile_only);
  const given = useToolFor(s, wanted);
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const focus = useRef(null);
  const [asking, setAsking] = useState(false);
  const downloadChanged = download !== (s.options.download || "platform");
  const changed = downloadChanged || !!folder.trim();
  const blocked = wanted && !given?.ok;          // given a tool and a folder on Save
  const cancel = () => (changed ? setAsking(true) : onClose());
  const unsaved = useUnsaved(changed);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = wanted ? await giveSourceTool(s.id, { download }, folder.trim() || null)
        : downloadChanged ? await updateSource(s.id, { options: { download }, pin: true }) : { ok: true, source: s };
      if (!r?.ok) { setError(r?.error || "Could not save."); return; }
      toast(wanted ? `${sourceName(s)} now downloads with ${r.source.tool}. Sync it to get its posts.`
        : `${sourceName(s)}: saved.`);
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
        title={`Download ${sourceName(s)}?`}
        confirmLabel="Save"
        busy={busy}
        error={error}
        confirmDisabled={blocked}
        onConfirm={save}
        onCancel={cancel}
        initialFocus={focus}
      >
        <div ref={focus} tabIndex={-1}>
          <DownloadPicker value={download} onChange={setDownload} platform={s.platform} platformOnly={!!s.platform_profile_only} />
          {wanted && (
            <fieldset className="source-opt source-give-tool">
              <legend>Folder</legend>
              {!given ? <span className="dim">Looking up its downloader…</span>
                : !given.ok ? (
                  <div className="msg err source-msg" role="alert">
                    {given.error}{" "}
                    <Link to="/settings#downloads" className="text-link">Open Link routing</Link>
                  </div>
                ) : (
                  <label className="source-opt-line">
                    <ToolBadge tool={given.tool} />
                    <input type="text" className="page-filter source-give-folder" aria-label="Folder it downloads into"
                           placeholder={given.folder} value={folder} maxLength={4096} onChange={e => setFolder(e.target.value)} />
                    <span className="dim">empty: {given.folder}</span>
                  </label>
                )}
            </fieldset>
          )}
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

/* Who a source shows with: a person there is (PersonPicker), or a new one
   by name (its handle to start with). Escape or a click beside it closes
   it, Tab stays inside, focus goes back where it was. The backend takes
   its account along (docs/API.md "Sources").

   Props: source, onClose, onLinked(source) */
export function SourcePersonDialog({ source: s, onClose, onLinked }) {
  const toast = useToast();
  const boxRef = useRef(null);
  const titleId = useId();
  const nameId = useId();
  const { data: people } = useApi(getPeople, "");
  const [active, setActive] = useState(null);
  const [name,   setName]   = useState(s.tool === "instaloader" ? s.target : "");
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState(null);
  const others = (people || []).filter(p => p.id !== s.person?.id);
  useEffect(() => {
    const prev = document.activeElement;
    return () => restoreFocus(prev);
  }, []);
  useEffect(() => {
    function onKey(e) {
      if (!focusLost()) return;
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      else trapTab(e, boxRef.current);
    }
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  // To a person: one there is, or (``make``, a name) a new one.
  async function link(person, make = null) {
    setBusy(true);
    setError(null);
    try {
      if (make) {
        const r = await createPerson(make);
        if (!r?.ok) { setError(r?.error || "Could not create the person."); return; }
        person = r.person;
      }
      const r = await setSourcePerson(s.id, person.id);
      if (!r?.ok) { setError(r?.error || "Could not link."); return; }
      toast(`${sourceName(s)} is now ${person.name}'s.`);
      onLinked?.(r.source);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div className="modal-overlay"
         onKeyDown={e => {
           if (e.key === "Escape" && !e.nativeEvent.isComposing) { e.stopPropagation(); e.preventDefault(); onClose(); return; }
           trapTab(e, boxRef.current);
         }}
         onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={boxRef} className="modal assign-modal" role="dialog" aria-modal="true"
           aria-labelledby={titleId} aria-busy={busy || undefined}>
        <h2 id={titleId} className="modal-title">
          <Icon name="users" size={16} />{s.person ? `Move ${sourceName(s)} to…` : `Link ${sourceName(s)} to…`}
        </h2>
        {s.person && <p className="creator-sub">Now {s.person.name}&apos;s{s.account ? "; its account moves with it" : ""}.</p>}
        <div className="link-assign-pick">
          <PersonPicker people={people ? others : null} recent={[]} active={active} onActive={setActive} label="Creator"
                        none={false} disabled={busy} autoFocus
                        onPick={o => { if (!busy && o?.person) link(o.person); }} />
        </div>
        <form className="source-person-new" onSubmit={e => { e.preventDefault(); if (name.trim()) link(null, name.trim()); }}>
          <label htmlFor={nameId} className="creator-sub">Or a new creator</label>
          <input id={nameId} type="text" maxLength={64} value={name} disabled={busy} placeholder="Name"
                 onChange={e => setName(e.target.value)} />
          <button type="submit" className="btn-secondary" disabled={busy || !name.trim()}>
            <Icon name="plus" size={14} />Create and link
          </button>
        </form>
        {error && <div className="msg err" role="alert">{error}</div>}
        <div className="link-assign-foot">
          <span className="quick-add-hint dim"><kbd>↑</kbd><kbd>↓</kbd> choose · <kbd>Enter</kbd> link · <kbd>Esc</kbd> cancel</span>
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>,
    document.body,
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
      {source?.profile_only ? "It is no longer shown as theirs." : "FeedVault stops syncing it."}{" "}
      {source?.folder ? "Its folder, files and posts stay as they are." : "Nothing on disk is touched."}
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
   Instagram name. Before saving, one line says what it is (platform,
   profile, folder) and one select what it follows: Default to start with,
   a named preset, "Custom…" (added on Default, then its options open), or
   "Profile only" (#218: shown as theirs, never downloaded), picked to
   start with on a platform Settings lists as profile only, and the only
   way for a site no downloader takes (tool "none", "Add profile").
   onDirty(bool) hears whether something is typed and not added yet (false
   again when the form goes). */
export function AddSource({ person = null, onAdded, onDirty }) {
  const toast = useToast();
  const [target, setTarget] = useState("");
  const [pick,   setPick]   = useState(null);    // the select, as picked (null: as its platform says)
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState(null);
  const [custom, setCustom] = useState(null);    // the source just added with Custom…: its options are open
  const text = target.trim();
  const resolved = useResolved(text);
  // No downloader takes the link's site: it can be added as a profile only.
  const profile = resolved?.ok === false ? resolved.profile || null : null;
  const value = resolved?.ok ? pick ?? (resolved.profile_only ? PRESET_ONLY : "") : "";
  const profileOnly = !!profile || (!!resolved?.ok && value === PRESET_ONLY);
  // Its account is set to Don't sync: no Add, nothing to choose (a profile only can be).
  const off = !!resolved?.ok && !!resolved.no_sync && !profileOnly;
  const there = resolved?.ok && resolved.source != null;
  // The kinds the preset picked downloads that need a login its sync lacks.
  const picked = resolved?.ok && !profileOnly ? resolvedPreset(resolved, value) : null;
  const login = picked ? needsLogin(formOf(picked.options, resolved.choices), resolved.choices, picked.session) : [];
  const summary = picked ? optionsSummary({ ...resolved, options: picked.options }) : "";

  useEffect(() => {
    onDirty?.(!!text);
    return () => onDirty?.(false);
  }, [text, onDirty]);

  async function add(e) {
    e.preventDefault();
    if (!resolved?.ok && !profile) return;
    setBusy(true);
    setError(null);
    try {
      // Profile only: download off, or (its platform listed) left to follow it.
      const body = { target: text };
      if (profile) body.tool = "none";
      else if (profileOnly) { if (!resolved.profile_only) body.options = { download: "off" }; }
      else {
        if (value && value !== PRESET_CUSTOM) body.preset = value;
        if (resolved.profile_only) body.options = { download: downloadOn(true) };
      }
      if (isHandle(text)) body.tool = "instaloader";
      if (person != null) body.person = person;
      const r = await createSource(body);
      if (!r?.ok) { setError(r?.error || "Could not add the source."); return; }
      toast(r.source.profile_only ? `${sourceName(r.source)} added as a profile only: shown as theirs, never downloaded.`
        : `${sourceName(r.source)} added. Sync it to download its posts.`);
      setTarget("");
      setPick(null);
      onAdded?.(r.source);
      if (value === PRESET_CUSTOM) setCustom(r.source);
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
        onChange={e => { setTarget(e.target.value); setError(null); setPick(null); }}
      />
      <button type="submit" className="btn-secondary"
              disabled={busy || (profile ? false : !resolved?.ok || there || off || login.length > 0)}>
        <Icon name="plus" size={13} />{profileOnly ? "Add profile" : "Add source"}
      </button>
      {resolved?.ok && (
        <div className="source-resolved" role="status" title={profileOnly ? undefined : `Synced with ${resolved.tool}`}>
          <PlatformIcon platform={resolved.platform} />
          <span className="source-resolved-target">{resolved.target.replace(/^https:\/\//, "")}</span>
          {!profileOnly && <><span className="dim">into</span>
            <code className="source-folder" title={resolved.folder}>{resolved.folder}</code></>}
          {there && <span className="is-err">already a source</span>}
          {off && <NotSyncedChip title="Its account is set to Don't sync: FeedVault does not download from it" />}
          {!there && !off && (
            <PresetSelect value={value} presets={resolved.presets} onChange={v => { setPick(v); setError(null); }}
                          label="Preset" disabled={busy}
                          title={profileOnly ? "Shown as theirs, never downloaded; pick a preset on its row to download it later"
                            : value === PRESET_CUSTOM ? "Added on Default, then its own options open"
                              : `${value ? "This preset" : "Default"}: ${summary || "new posts"}`} />
          )}
        </div>
      )}
      {profile && (
        <div className="source-resolved" role="status">
          <PlatformIcon platform={profile.platform} />
          <span className="source-resolved-target">{profile.target.replace(/^https:\/\//, "")}</span>
          <ProfileOnlyChip title="No downloader takes this site (Settings → Link routing): it can be shown as theirs, never downloaded" />
        </div>
      )}
      {off && (
        <div className="msg source-msg" role="status">
          This account is set to Don&apos;t sync: no source can be added for it. Turn Don&apos;t sync off on its card or page first.
        </div>
      )}
      {login.length > 0 && !there && !off && (
        <div className="msg err source-msg" role="alert">
          {login.map(k => kindLabel(resolved.platform, k)).join(", ")} need a logged-in session: set one in{" "}
          <Link to="/settings#sync" className="text-link">Settings → Sync</Link>, or pick another preset.
        </div>
      )}
      {(error || (resolved?.ok === false && !profile)) && (
        <div className="msg err source-msg" role="alert">{error || resolved.error}</div>
      )}
      {custom && <SourceOptionsDialog source={custom} onClose={() => setCustom(null)} onSaved={onAdded} />}
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
