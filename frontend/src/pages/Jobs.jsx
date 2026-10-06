import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { cancelJob, getJobLog } from "../lib/api";
import { useJobs, ENDED, SAVE_KINDS, jobDuration, shownParams } from "../lib/jobs";
import { fmtAgo, fmtFullDate, fmtStamp } from "../lib/fmt";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import PageHeader from "../components/PageHeader";

const LOG_POLL_MS = 1000;
const LOG_KEPT    = 5000;          // lines kept on screen, as many as the backend keeps

/* One job's output: polled every second while the job is queued or running
   and the tab is visible, read once when it has ended. Mounted with
   key={job.id}, so a new job starts from an empty log. */
export function JobLog({ jobId }) {
  const [lines, setLines] = useState([]);
  const [gap,   setGap]   = useState(false);   // the backend dropped lines we never read
  const [error, setError] = useState(null);
  const boxRef   = useRef(null);
  const stickRef = useRef(true);              // follow the end unless the user scrolled up

  useEffect(() => {
    let alive = true, timer = null, busy = false, after = 0, ended = false;
    async function tick() {
      timer = null;
      busy = true;
      try {
        const r = await getJobLog(jobId, after);
        if (!alive) return;
        setError(null);
        if (r.first > after + 1) setGap(true);
        if (r.lines.length) {
          setLines(ls => [...ls, ...r.lines].slice(-LOG_KEPT));
          after = r.next;
        }
        ended = ENDED.has(r.state) && !r.more;
        if (r.more) { busy = false; schedule(0); return; }
      } catch (e) {
        if (!alive) return;
        setError(e.message);
      }
      busy = false;
      if (!ended) schedule(LOG_POLL_MS);
    }
    function schedule(ms) {
      if (alive && !document.hidden) timer = setTimeout(tick, ms);
    }
    function onVisibility() {
      if (!document.hidden && !timer && !busy && !ended) tick();
    }
    tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [jobId]);

  useLayoutEffect(() => {
    const box = boxRef.current;
    if (box && stickRef.current) box.scrollTop = box.scrollHeight;
  }, [lines]);

  function onScroll() {
    const box = boxRef.current;
    stickRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  }

  return (
    <>
      {error && <div className="msg err" role="alert">Could not read the log: {error}</div>}
      <pre ref={boxRef} className="job-log" onScroll={onScroll} tabIndex={0} aria-label="Job output">
        {gap && <span className="job-log-gap">… earlier lines dropped …{"\n"}</span>}
        {lines.length === 0 ? (
          <span className="dim">No output yet.</span>
        ) : lines.map(l => (
          <span key={l.n} className={l.text.startsWith("[feedvault]") ? "job-log-own" : undefined}>{l.text}{"\n"}</span>
        ))}
      </pre>
    </>
  );
}

function StateChip({ state }) {
  return <span className={`chip job-state job-state-${state}`}>{state}</span>;
}

function JobTitle({ job }) {
  // A source sync's label names its profile (its param is only an id); a
  // save's names its post.
  const params = job.params?.source || SAVE_KINDS.has(job.kind) ? [] : shownParams(job);
  return (
    <span className="job-title">
      <span className="dim mono">#{job.id}</span> {job.label}
      {params.length > 0 && <span className="job-params mono"> {params.join(" · ")}</span>}
    </span>
  );
}

export default function Jobs() {
  const { list } = useJobs();
  const [picked,   setPicked]   = useState(null);   // a job clicked in the history
  const [closed,   setClosed]   = useState(null);   // the log closed by the user, not shown again
  const [confirm,  setConfirm]  = useState(null);   // job to cancel
  const [busy,     setBusy]     = useState(false);
  const [dlgErr,   setDlgErr]   = useState(null);

  const jobs    = list?.jobs || [];
  const running = jobs.filter(j => j.state === "running");
  const queued  = jobs.filter(j => j.state === "queued").reverse();    // next to run first
  const history = jobs.filter(j => ENDED.has(j.state));

  // Without a pick, the log follows the newest running job, and keeps
  // showing it once it has ended.
  const autoId = running[0]?.id ?? null;
  const [lastAuto, setLastAuto] = useState(autoId);
  if (autoId !== null && autoId !== lastAuto) setLastAuto(autoId);
  const shownId = picked ?? lastAuto;
  const shown = shownId === closed ? null : jobs.find(j => j.id === shownId) || null;
  function pick(id) { setPicked(id); setClosed(null); }

  async function runCancel() {
    setBusy(true);
    setDlgErr(null);
    try {
      const r = await cancelJob(confirm.id);
      if (!r?.ok) { setDlgErr(r?.error || "Could not cancel the job."); return; }
      setConfirm(null);
    } catch (e) {
      setDlgErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  function activeRow(j) {
    return (
      <li key={j.id} className="job-row">
        <StateChip state={j.state} />
        {j.state === "running" && <Icon name="refresh" size={13} className="spin job-spin" />}
        <JobTitle job={j} />
        <code className="job-argv" title={j.argv.join(" ")}>{j.argv.join(" ")}</code>
        <span className="dim mono job-when" title={fmtFullDate(j.started_at ?? j.created_at)}>
          {j.state === "running" ? `started ${fmtAgo(j.started_at)}`
            : j.waits_until ? `pausing between downloads, starts at ${new Date(j.waits_until * 1000).toLocaleTimeString()}`
            : `queued ${fmtAgo(j.created_at)}`}
        </span>
        {j.id !== shown?.id && (
          <button type="button" className="btn-ghost" onClick={() => pick(j.id)}>Show log</button>
        )}
        <button type="button" className="btn-danger-soft job-cancel" onClick={() => { setDlgErr(null); setConfirm(j); }}>
          <Icon name="close" size={13} />Cancel…
        </button>
      </li>
    );
  }

  return (
    <div className="jobs-page">
      <PageHeader title="Jobs" sub={list ? `${list.running} running · ${list.queued} queued` : "…"} />
      <div className="card">
        <p className="page-lede">
          Downloads and other command-line tools FeedVault runs for you, one at a time per tool.
          When a download ends, its folder is indexed right away. Check your tools in Settings.
        </p>
        {!list ? (
          <div className="empty">Loading…</div>
        ) : running.length + queued.length === 0 ? (
          <div className="empty">Nothing is running.</div>
        ) : (
          <ul className="job-list">
            {running.map(activeRow)}
            {queued.map(activeRow)}
          </ul>
        )}
      </div>

      {shown && (
        <div className="card">
          <div className="job-log-head">
            <div className="card-title">Log</div>
            <JobTitle job={shown} />
            <StateChip state={shown.state} />
            <div className="page-head-spacer" />
            {ENDED.has(shown.state) && shown.message && (
              <span className={`job-message${shown.state === "failed" ? " is-err" : ""}`}>{shown.message}</span>
            )}
            <button type="button" className="del-btn" onClick={() => { setClosed(shown.id); setPicked(null); }} aria-label="Close the log" title="Close">
              <Icon name="close" size={14} />
            </button>
          </div>
          <JobLog key={shown.id} jobId={shown.id} />
        </div>
      )}

      <div className="card">
        <div className="card-title">History</div>
        {history.length === 0 ? (
          <div className="empty">{list ? "No job has run yet." : "Loading…"}</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table job-table card-table" role="table">
              <thead role="rowgroup">
                <tr role="row">
                  <th scope="col" role="columnheader">State</th>
                  <th scope="col" role="columnheader">Job</th>
                  <th scope="col" role="columnheader" className="num">Started</th>
                  <th scope="col" role="columnheader" className="num">Took</th>
                  <th scope="col" role="columnheader" className="num">Exit</th>
                  <th scope="col" role="columnheader">Result</th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {history.map((j, i) => (
                  <tr
                    key={j.id}
                    role="row"
                    className={j.id === shown?.id ? "is-selected" : undefined}
                    style={{ animationDelay: `${Math.min(i, 30) * 20}ms` }}
                    onClick={() => pick(j.id)}
                  >
                    <td role="cell" data-label="State"><StateChip state={j.state} /></td>
                    <td role="cell" className="cell-main" data-label="Job">
                      <button type="button" className="btn-link" onClick={e => { e.stopPropagation(); pick(j.id); }} title="Show its log">
                        <JobTitle job={j} />
                      </button>
                    </td>
                    <td role="cell" className="num" data-label="Started" title={fmtFullDate(j.started_at ?? j.created_at)}>{fmtStamp(j.started_at ?? j.created_at)}</td>
                    <td role="cell" className="num" data-label="Took">{jobDuration(j) ?? "—"}</td>
                    <td role="cell" className="num" data-label="Exit">{j.exit_code ?? "—"}</td>
                    <td role="cell" className={j.state === "failed" ? "job-message is-err" : "job-message"} data-label="Result">{j.message || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!confirm}
        danger
        busy={busy}
        error={dlgErr}
        title="Cancel this job?"
        confirmLabel="Cancel job"
        cancelLabel="Keep it"
        onConfirm={runCancel}
        onCancel={() => setConfirm(null)}
      >
        {confirm && (
          <p>
            <strong>{confirm.label}</strong> (#{confirm.id}) {confirm.state === "running"
              ? "is stopped: the tool and everything it started get SIGTERM, then SIGKILL after 10 seconds. Files it already wrote stay, so a later run can resume."
              : "leaves the queue without running."}
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}
