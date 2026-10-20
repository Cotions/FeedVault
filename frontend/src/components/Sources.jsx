import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import {
  acceptRename, createPerson, createSource, cancelJob, dismissRename, getPeople, getScripts, resolveSource, setSourcePerson,
  updateSource,
} from "../lib/api";
import { useApi } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { useJobs } from "../lib/jobs";
import { ERRORS, SETUP_ERRORS, sourceName } from "../lib/sources";
import { healthBadge, lastGood, loginText } from "../lib/health";
import { fmtAgo, fmtFullDate, fmtIn, fmtInt, platformLabel, safeUrl } from "../lib/fmt";
import {
  FIRST_POSTS_MAX, MEDIA, SCHEDULES, defaultText, fieldOverridden, firstPostsEach, formError, formOf, kindEffect, kindLabel,
  FIELDS, changesOf, fieldOwn, fieldsOf, mediaEffect, metadataEffect, metadataLabel, needsLogin, resetField,
  optionsOf, optionsSummary, scheduleShort, scheduleText, scriptHref, toggleKind, today,
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

// Option keys (docs/API.md "What a source downloads") as the form names them.
const OPTION_NAMES = {
  full_history: "full history", session: "login", content: "content", media: "media", since: "not older than",
  first_posts: "last posts", script: "command", metadata: "metadata files", schedule: "schedule",
};

/* Whether a source follows its platform's download defaults, or sets
   some options itself ("custom", which ones in its title). */
export function InheritBadge({ source: s }) {
  const own = s.overrides || [];
  if (!own.length) return <span className="source-inherit dim" title="Every option follows Settings → Download defaults">defaults</span>;
  return (
    <span className="chip source-inherit is-set"
          title={`Set on this source: ${own.map(k => OPTION_NAMES[k] || k).join(", ")}. The rest follow Settings → Download defaults.`}>
      custom
    </span>
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

/* One source: its profile, what it downloads, tool, folder, schedule, last
   sync and Sync; its profile on the site, Options, its creator (link,
   move, unlink) and Remove in its ⋯ menu. ``onSaved``: after its options
   changed; ``onLinked``: after its person did (the people's cards move
   too), else onSaved. One set to Don't sync is marked so and keeps its
   creator and Remove only: no Options, no Sync, no schedule line (its
   schedule waits for the switch to go off). */
export function SourceRow({ source: s, job, onSync, onRemove, onSaved, onLinked = onSaved, flash = false }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [linking, setLinking] = useState(false);
  const url = safeUrl(s.url);
  const summary = optionsSummary(s);
  const off = !!s.no_sync;
  const along = s.account ? "; its account goes with it" : "";

  async function unlink() {
    try {
      const r = await setSourcePerson(s.id, null);
      if (!r?.ok) { toast(r?.error || "Could not unlink.", "err"); return; }
      toast(`${sourceName(s)} is no longer ${s.person.name}'s.`);
      onLinked?.(r.source);
    } catch (err) {
      toast(err.message, "err");
    }
  }

  return (
    <li className={`source-row${flash ? " is-flash" : ""}${off ? " is-nosync" : ""}`} data-source-id={s.id}>
      <PlatformIcon platform={s.platform} />
      <span className="source-id">
        <span className="creator-name" title={sourceName(s)}>
          {sourceName(s)}
          {s.person && !s.account && <span className="creator-sub"> · first sync not done yet</span>}
        </span>
        <span className="source-summary-line">
          {off && <NotSyncedChip />}
          {summary && <span className="source-summary" title={`What it downloads: ${summary}`}>{summary}</span>}
          <InheritBadge source={s} />
        </span>
        {!off && <ScriptWarning source={s} />}
        {!off && <ScheduleLine source={s} />}
        {!off && <SourceHealth source={s} job={job} onSaved={onSaved} />}
        <code className="source-folder" title={s.folder}>{s.folder}</code>
      </span>
      <ToolBadge tool={s.tool} />
      {!off && <SourceStatus source={s} job={job} />}
      {job && <Link to="/jobs" className="btn-ghost">Log</Link>}
      {!off && <SyncButton source={s} job={job} onSync={onSync} />}
      <ActionMenu label={`More for ${sourceName(s)}`} items={[
        url && { label: "Open profile", icon: "external", href: url, title: `Profile on ${platformLabel(s.platform)} (opens the site)` },
        !off && { label: "Options", icon: "settings", disabled: !!job, onSelect: () => setEditing(true),
                  title: job ? "Its sync is queued or running" : "What this source downloads" },
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
    </li>
  );
}

/* Beside a field's name: "default" when it is what the source inherits
   (Settings → Download defaults), else "set here", the default, and Reset.
   ``own``: whether it is set (the source's form says), else whether it
   differs from the default; ``onReset``: Reset, else the default's value. */
function Inherit({ f, form, defaults, onChange, platform, tool, setting, own = null, onReset = null }) {
  if (!defaults) return null;
  if (!(own ? own(f) : fieldOverridden(form, defaults, f))) return <span className="source-default dim">default</span>;
  return (
    <span className="source-default is-set">
      set here · default: {defaultText(f, defaults, platform, tool, setting)}
      <button type="button" className="btn-link" onClick={() => (onReset ? onReset(f) : onChange(resetField(form, defaults, f)))}
              title="Follow Settings → Download defaults for this">Reset</button>
    </span>
  );
}

/* What a source downloads, as far as its tool and platform let it choose:
   content kinds, images or videos, a date floor and the first sync. Each
   with what it does in one line. ``firstSync``: the source has not synced
   yet (last N posts is for its first sync only). ``defaults``: the form of
   what it inherits, to mark each field as default or set here (with a
   Reset); none on a new source's form, which starts from them.
   ``metadata``: {inherited, setting}, the modes for the Metadata files
   choice. ``editor``: a platform's download defaults (Settings), which
   have no full history (a one-off) and no "Default" of their own.
   ``own``(field) and ``onReset``(field): a source's form, which knows
   what the source sets (see Inherit); picking "Default" resets too. */
export function SourceOptions({ tool, platform, target, choices, session, form, onChange, firstSync = true,
                                defaults = null, metadata = null, editor = false, own = null, onReset = null }) {
  const id = useId();
  const each = firstPostsEach(tool, form.content, platform, target);
  const set = patch => onChange({ ...form, ...patch });
  const login = needsLogin(form, choices, session);
  const toggle = k => onChange(toggleKind(form, k));
  const mark = f => <Inherit f={f} form={form} defaults={defaults} onChange={onChange} platform={platform} tool={tool}
                             setting={metadata?.setting} own={own} onReset={onReset} />;
  const setting = metadata?.setting || "full";
  // The Metadata files choice: what it inherits first ("Default (…)"),
  // then what Settings → Sync says (null), then each mode.
  const modes = choices.metadata || [];
  const metaFirst = defaults ? defaults.metadata : "";
  const metaOptions = [
    [metaFirst, defaults ? `Default (${metadataLabel(defaults.metadata || setting)})` : `as in Settings → Sync (${metadataLabel(setting)})`],
    ...(metaFirst !== "" ? [["", `as in Settings → Sync (${metadataLabel(setting)})`]] : []),
    ...modes.filter(m => m !== metaFirst).map(m => [m, metadataLabel(m)]),
  ];
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
          <legend>Download {mark("content")}</legend>
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
          <legend>Media {mark("media")}</legend>
          {MEDIA.map(([v, label]) => (
            <label key={v} className="source-opt-line">
              <input type="radio" name={`${id}-media`} checked={form.media === v} onChange={() => set({ media: v })} />
              <b>{label}</b><span className="dim">{mediaEffect(tool, v)}</span>
            </label>
          ))}
        </fieldset>
      )}
      <fieldset {...unused}>
        <legend>Not older than {mark("since")}</legend>
        <label className="source-opt-line">
          <input type="date" min="1970-01-01" max={today()} value={form.since} aria-label="Not older than"
                 onChange={e => set({ since: e.target.value })} />
          <span className="dim">{form.since ? "posts from before this day are never downloaded" : "no limit"}</span>
          {form.since && <button type="button" className="btn-link" onClick={() => set({ since: "" })}>Clear</button>}
        </label>
      </fieldset>
      <fieldset {...unused}>
        <legend>{firstSync ? "First sync" : "Next sync"} {mark("first")}</legend>
        <label className="source-opt-line">
          <input type="radio" name={`${id}-first`} checked={form.first === "new"} onChange={() => set({ first: "new" })} />
          <b>new posts</b>
          <span className="dim">stops at the newest post FeedVault already has from this profile</span>
        </label>
        {!editor && (
          <label className="source-opt-line">
            <input type="radio" name={`${id}-first`} checked={form.first === "full"} onChange={() => set({ first: "full" })} />
            <b>full history</b><span className="dim">walks the whole profile, once</span>
          </label>
        )}
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
      <SessionPicker value={form.session} onChange={session => set({ session })} unused={unused} mark={mark("session")} />
      <ScriptPicker tool={tool} value={form.script} onChange={script => set({ script })} mark={mark("script")} />
      {modes.length > 1 && (
        <fieldset {...unused}>
          <legend>Metadata files {mark("metadata")}</legend>
          <label className="source-opt-line">
            <select className="sort-select" aria-label="Metadata files a sync writes" value={form.metadata}
                    onChange={e => (defaults && onReset && e.target.value === metaFirst
                      ? onReset("metadata") : set({ metadata: e.target.value }))}>
              {metaOptions.map(([v, label]) => <option key={v || "setting"} value={v}>{label}</option>)}
            </select>
            <span className="dim">{metadataEffect(tool, form.metadata || setting)}</span>
          </label>
          {metadata?.ffmpeg === false && (form.metadata || (defaults?.metadata ?? "") || setting) === "none" && <NoFfmpeg />}
        </fieldset>
      )}
      <fieldset className="source-opt">
        <legend>Schedule {mark("schedule")}</legend>
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

/* The login a sync of this source uses: the tool's, from Settings → Sync
   (null), or the source's own: none, or a browser's cookies, with the
   browser profile they come from. One made elsewhere (a saved instaloader
   session, through the API) stays as it is until another is picked. */
const SESSION_EFFECT = {
  "": "the tool\u2019s, set in Settings \u2192 Sync",
  none: "public posts only, whatever Settings says",
  cookies: "this source\u2019s own: the tool reads that browser\u2019s cookies itself",
  login: "this source\u2019s own saved instaloader session",
};
function SessionPicker({ value, onChange, unused, mark = null }) {
  const mode = value?.mode || "";
  const pick = m => onChange(m === "" ? null : m === "cookies" ? cookiesSession("firefox", "") : m === "none" ? { mode: m } : value);
  return (
    <fieldset {...unused}>
      <legend>Login {mark}</legend>
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
function ScriptPicker({ tool, value, onChange, mark = null }) {
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
      <legend>Command {mark}</legend>
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
  const [initial] = useState(() => formOf(s.options, s.choices));
  const [form, setForm] = useState(initial);
  // What it inherits, and what it sets itself: only a field changed here
  // is saved as set (even to its default: it then stays when the default
  // changes), only one reset as inherited; the others stay as they are.
  const defaults = formOf(s.defaults, s.choices);
  const [setBefore] = useState(() => fieldsOf(s.overrides));
  const [resets, setResets] = useState(() => new Set());
  const isOwn = f => fieldOwn(f, form, initial, setBefore, resets);
  const own = FIELDS.filter(isOwn);
  // A field changed again after its Reset is set again.
  const change = next => {
    const edited = FIELDS.filter(f => JSON.stringify(resetField({}, next, f)) !== JSON.stringify(resetField({}, form, f)));
    if (edited.some(f => resets.has(f))) setResets(new Set([...resets].filter(f => !edited.includes(f))));
    setForm(next);
  };
  const resetOne = f => {
    setForm(resetField(form, defaults, f));
    setResets(new Set([...resets, f]));
  };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Until a sync has worked (s.synced, sources.synced_ok): a failed first one leaves last N open.
  const firstSync = !s.synced || s.options.first_posts != null;
  const problem = formError(form, s.choices);
  const focus = useRef(null);
  // Esc, Cancel or a click outside ask before changed choices go, as
  // every other form does since #125/#132 (#149). Compared as they would
  // be saved, so ticking a box off and on again is no change.
  const [asking, setAsking] = useState(false);
  const changes = changesOf(form, initial, setBefore, resets, s.choices, firstSync);
  const changed = !!changes;
  const cancel = () => (changed ? setAsking(true) : onClose());
  // A link in it ("See Scripts", "Settings → Sync"), Back or Forward ask
  // the same before the app leaves the page with them (#153).
  const unsaved = useUnsaved(changed);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = changes ? await updateSource(s.id, changes.options, changes.reset, true) : { ok: true, source: s };
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
          <div className="source-inherits" role="status">
            <span className="dim">
              {own.length ? `${own.length} choice${own.length === 1 ? "" : "s"} set here; the rest follow`
                : "Every choice follows"}{" "}
              <Link to="/settings#download-defaults" className="text-link">Download defaults</Link>
              {" "}for {platformLabel(s.platform)} ({s.tool}).
            </span>
            <button type="button" className="btn-link" disabled={!own.length}
                    onClick={() => { setForm(defaults); setResets(new Set(FIELDS)); }}
                    title="Every choice back to its default; Save to keep it">Reset all to default</button>
          </div>
          <SourceOptions tool={s.tool} platform={s.platform} target={s.target} choices={s.choices} session={s.session} form={form}
                         onChange={change} firstSync={firstSync} defaults={defaults} metadata={s.metadata}
                         own={isOwn} onReset={resetOne} />
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
  // A new source starts from its platform's download defaults; what stays
  // equal to them is not stored, so it follows them later.
  // Its account is set to Don't sync: no Add, nothing to choose.
  const off = !!resolved?.ok && !!resolved.no_sync;
  const form = kind && !off && (forms[kind] || formOf(resolved.defaults, resolved.choices));
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
              disabled={busy || !form || off || resolved.source != null || !!problem || login.length > 0}>
        <Icon name="plus" size={13} />Add source
      </button>
      {resolved?.ok && (
        <div className="source-resolved" role="status">
          <ToolBadge tool={resolved.tool} />
          <PlatformIcon platform={resolved.platform} />
          <span className="source-resolved-target">{resolved.target.replace(/^https:\/\//, "")}</span>
          <span className="dim">into</span>
          <code className="source-folder" title={resolved.folder}>{resolved.folder}</code>
          {resolved.source != null && <span className="is-err">already a source</span>}
          {off && <NotSyncedChip title="Its account is set to Don't sync: FeedVault does not download from it" />}
        </div>
      )}
      {off && (
        <div className="msg source-msg" role="status">
          This account is set to Don&apos;t sync: no source can be added for it. Turn Don&apos;t sync off on its card or page first.
        </div>
      )}
      {form && resolved.source == null && (
        <SourceOptions tool={resolved.tool} platform={resolved.platform} target={resolved.target} choices={resolved.choices}
                       session={resolved.session} form={form} metadata={resolved.metadata}
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
