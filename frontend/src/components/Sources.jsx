import { useState } from "react";
import { Link } from "react-router-dom";
import { createSource, cancelJob } from "../lib/api";
import { useToast } from "../lib/toast";
import { useJobs } from "../lib/jobs";
import { ERRORS } from "../lib/sources";
import { fmtAgo, fmtFullDate, fmtInt, platformLabel, platformShort, safeUrl } from "../lib/fmt";
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
  return (
    <span className="source-status" title={title}>
      {failed && <span className="chip job-state-failed source-badge">{ERRORS[r.error] || "failed"}</span>}
      {(r.state === "cancelled" || r.state === "interrupted") && <span className="chip job-state-interrupted source-badge">{r.state}</span>}
      {!compact && r.state === "done" && <span>{r.message}</span>}
      {!compact && failed && <span className="source-message">{r.message}</span>}
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
      title={job ? "Its sync is queued or running; see Jobs" : `Download @${s.target}'s new posts`}
      aria-label={`Sync @${s.target}`}
    >
      <Icon name="refresh" size={small ? 15 : 13} className={job?.state === "running" ? "spin" : undefined} />
      {!small && label}
    </button>
  );
}

/* One source: its profile, folder, last sync, Sync and Remove. */
export function SourceRow({ source: s, job, onSync, onRemove }) {
  const url = safeUrl(s.url);
  return (
    <li className="source-row">
      <span className="chip platform-chip" title={platformLabel(s.platform)}>{platformShort(s.platform)}</span>
      <span className="source-id">
        <span className="creator-name">
          @{s.target}
          {s.person && !s.account && <span className="creator-sub"> · first sync not done yet</span>}
        </span>
        <code className="source-folder" title={s.folder}>{s.folder}</code>
      </span>
      <SourceStatus source={s} job={job} />
      {url && (
        <a href={url} className="icon-btn" target="_blank" rel="noreferrer noopener"
           title="Profile on Instagram (opens the site)" aria-label={`@${s.target} on Instagram`}>
          <Icon name="external" size={15} />
        </a>
      )}
      {job && <Link to="/jobs" className="btn-ghost">Log</Link>}
      <SyncButton source={s} job={job} onSync={onSync} />
      <button type="button" className="btn-ghost" disabled={!!job} onClick={() => onRemove(s)}
              title={job ? "Cancel its sync first" : "Forget this source; files and posts stay"}>
        Remove
      </button>
    </li>
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
      toast(`@${source.target} is no longer a source.`);
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
      title={source ? `Remove @${source.target}?` : ""}
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

/* Paste a profile URL or a handle (Instagram, through instaloader). */
export function AddSource({ person = null, onAdded }) {
  const toast = useToast();
  const [target, setTarget] = useState("");
  const [full,   setFull]   = useState(false);
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState(null);

  async function add(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { tool: "instaloader", target: target.trim(), options: { full_history: full } };
      if (person != null) body.person = person;
      const r = await createSource(body);
      if (!r?.ok) { setError(r?.error || "Could not add the source."); return; }
      toast(`@${r.source.target} added. Sync it to download its posts.`);
      setTarget("");
      setFull(false);
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
        placeholder="instagram.com/name or @name"
        aria-label="Instagram profile URL or handle"
        maxLength={200}
        value={target}
        onChange={e => { setTarget(e.target.value); setError(null); }}
      />
      <label className="source-check" title="Without it, the first sync starts after the newest post FeedVault already has">
        <input type="checkbox" checked={full} onChange={e => setFull(e.target.checked)} />
        Full history
      </label>
      <button type="submit" className="btn-secondary" disabled={busy || !target.trim()}>
        <Icon name="plus" size={13} />Add source
      </button>
      {error && <div className="msg err source-msg" role="alert">{error}</div>}
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
