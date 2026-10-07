import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { getScript, getScripts, runScript } from "../lib/api";
import { useApi } from "../lib/useApi";
import { ENDED, scriptJobToReopen, useJobs } from "../lib/jobs";
import { useToast } from "../lib/toast";
import { fmtAgo, fmtBytes } from "../lib/fmt";
import { scriptAnchor } from "../lib/sourceOptions";
import { useKeepFocus } from "../lib/layout";
import Icon from "../components/Icon";
import { CancelJobDialog, JobLog } from "./Jobs";
import PageHeader from "../components/PageHeader";

// What a script is given (docs/API.md "Scripts").
const NEEDS = {
  target: "a target: a profile name, a shortcode or a link, by its program",
  url: "a link",
  none: "nothing",
};

/* Copy a text to the clipboard. Writes nothing anywhere else. */
function CopyButton({ text, what }) {
  const toast = useToast();
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      toast(`Copied the ${what}.`);
    } catch {
      toast(`Could not copy; select the ${what} instead.`, "err");
    }
  }
  return (
    <button type="button" className="btn-ghost" onClick={copy} title={`Copy the ${what}`}>
      <Icon name="copy" size={13} />Copy {what}
    </button>
  );
}

/* The file to create for a template: its path and its text, to copy into
   a text editor. Nothing is written from here. */
function TemplateFile({ path, content, shell }) {
  return (
    <div className="script-template">
      <div className="script-template-head">
        <span>Create this file{shell && <>, then <code>chmod +x</code> it</>}:</span>
        <code className="script-path">{path}</code>
        <CopyButton text={path} what="path" />
        <CopyButton text={content} what="content" />
      </div>
      <pre className="script-content">{content}</pre>
    </div>
  );
}

/* A script's text, read-only, read again each time it is opened. */
function ScriptContent({ id }) {
  const [state, setState] = useState({ content: null, error: null });
  useEffect(() => {
    let alive = true;
    getScript(id).then(s => alive && setState({ content: s.content, error: null }),
                       e => alive && setState({ content: null, error: e.message }));
    return () => { alive = false; };
  }, [id]);
  if (state.error) return <div className="msg err" role="alert">{state.error}</div>;
  if (state.content == null) return <div className="dim">Loading…</div>;
  return <pre className="script-content" aria-label="Content, read-only">{state.content}</pre>;
}

// What a Target field asks for: what its tool takes, else what it becomes
// (#149: a shell script's read "text").
function targetHint(script) {
  if (script.tool === "instaloader") return "profile name or shortcode";
  if (script.tool) return "https://…";
  return script.kind === "shell" ? "passed as FV_TARGET" : "passed as {target}";
}

/* Run a script with its inputs; the job's log shows below. */
function RunForm({ script, onStarted }) {
  const [form,  setForm]  = useState({ target: "", url: "", folder: "" });
  const [busy,  setBusy]  = useState(false);
  const [error, setError] = useState(null);
  const set = patch => setForm(f => ({ ...f, ...patch }));
  // Run… opens the form on its first field, ready to type (QA pass 3).
  const formRef = useRef(null);
  useEffect(() => { formRef.current?.querySelector("input")?.focus(); }, []);

  async function run(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const inputs = {};
      if (script.needs === "target") inputs.target = form.target.trim();
      if (script.needs === "url") inputs.url = form.url.trim();
      if (form.folder.trim()) inputs.folder = form.folder.trim();
      const r = await runScript(script.id, inputs);
      if (!r?.ok) { setError(r?.error || "Could not start the script."); return; }
      onStarted(r.job);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="script-run" onSubmit={run} ref={formRef}>
      {script.needs === "target" && (
        <label className="script-field">
          <span>Target</span>
          <input type="text" value={form.target} onChange={e => set({ target: e.target.value })} required
                 placeholder={targetHint(script)} />
        </label>
      )}
      {script.needs === "url" && (
        <label className="script-field">
          <span>Link</span>
          <input type="text" value={form.url} onChange={e => set({ url: e.target.value })} required placeholder="https://…" />
        </label>
      )}
      <label className="script-field">
        <span>Folder <span className="dim">(optional)</span></span>
        <input type="text" value={form.folder} onChange={e => set({ folder: e.target.value })}
               placeholder="{root}: inside a media root, else the first one" />
      </label>
      <button type="submit" className="btn-primary" disabled={busy}>
        <Icon name="play" size={13} />{busy ? "Starting…" : "Run"}
      </button>
      {error && <div className="msg err" role="alert">{error}</div>}
    </form>
  );
}

/* One script. A refused one is never run; its text, when it could be read
   (``readable``), can still be viewed, read-only: it is how to find what
   to fix. ``lit``: the entry a source's warning linked to. */
function ScriptRow({ script: s, dir, onStarted, lit = false }) {
  const [open, setOpen] = useState(null);       // "view" | "run" | "copy"
  const toggle = what => setOpen(o => (o === what ? null : what));
  const name = s.builtin ? s.id.slice("builtin:".length) : s.id;
  const viewable = !s.refused || s.readable;
  return (
    <li id={scriptAnchor(s.id)} className={`dl-row script-row${s.refused ? " is-refused" : ""}${lit ? " is-flash" : ""}`}>
      <div className="dl-head">
        <span className="tool-name">{s.name || s.file}</span>
        <code className="dim">{s.id}</code>
        {s.kind && <span className="chip">{s.kind}</span>}
        {/* As typed: a program's path is case-sensitive (#139). */}
        {s.tool && <span className="chip tool-chip script-tool" title={`Runs ${s.tool}`}>{s.tool}</span>}
        {s.needs && <span className="chip" title={`Needs ${NEEDS[s.needs]}`}>needs {s.needs}</span>}
        {s.builtin && <span className="chip">built-in</span>}
        {s.refused && <span className="chip job-state-failed">refused</span>}
        <div className="page-head-spacer" />
        {viewable && (
          <button type="button" className="btn-ghost" onClick={() => toggle("view")}
                  title={s.refused ? "Its text, read-only: it is never run" : undefined}>
            {open === "view" ? "Hide" : "View"}
          </button>
        )}
        {!s.refused && <button type="button" className="btn-ghost" onClick={() => toggle("run")}>Run…</button>}
        {s.builtin && <button type="button" className="btn-ghost" onClick={() => toggle("copy")}>Copy template</button>}
      </div>
      {s.description && <span className="dim">{s.description}</span>}
      {s.refused && <div className="msg err script-refused">{s.file} is refused: {s.refused}. It is never run.</div>}
      {s.path && (
        <span className="dim mono script-meta">
          {s.path}{s.size != null && ` · ${fmtBytes(s.size)}`}{s.mtime && ` · changed ${fmtAgo(s.mtime)}`}
        </span>
      )}
      {s.argv && open !== "view" && <code className="job-argv" title={s.argv.join(" ")}>{s.argv.join(" ")}</code>}
      {open === "view" && <ScriptContent id={s.id} />}
      {open === "run" && <RunForm script={s} onStarted={job => { setOpen(null); onStarted(job); }} />}
      {open === "copy" && <CopyTemplate id={s.id} path={`${dir}/${name}.json`} />}
    </li>
  );
}

function CopyTemplate({ id, path }) {
  const [content, setContent] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { getScript(id).then(s => setContent(s.content), e => setError(e.message)); }, [id]);
  if (error) return <div className="msg err" role="alert">{error}</div>;
  return content == null ? <div className="dim">Loading…</div> : <TemplateFile path={path} content={content} />;
}

export default function Scripts() {
  const { list, started } = useJobs();
  const { data, error, reload } = useApi(getScripts);
  // The job whose log shows; undefined until the job list is known.
  const [jobId, setJobId] = useState(undefined);
  // Back on the page (from Jobs, say) while a script run from here still
  // runs: its log opens again, from the job list, once (#149); the id was
  // the page's own state, gone on leaving it. Not scrolled to, nor
  // focused: the page opens where it was left.
  const [reopened, setReopened] = useState(null);
  if (jobId === undefined && list) {
    const again = scriptJobToReopen(list.jobs)?.id ?? null;
    setJobId(again);
    setReopened(again);
  }
  const [shellOpen, setShellOpen] = useState(false);
  const [confirm, setConfirm] = useState(null);      // the job to cancel

  // Files are edited elsewhere: read them again on coming back.
  useEffect(() => {
    window.addEventListener("focus", reload);
    return () => window.removeEventListener("focus", reload);
  }, [reload]);

  const job = jobId != null ? (list?.jobs || []).find(j => j.id === jobId) : null;
  // Run is clicked far down the page, on a template; the log opens near the
  // top. Bring it into view, else the click seems to do nothing.
  const logRef = useRef(null);
  // Cancel… goes once the job ends (or is cancelled), and the log's Close
  // takes the log away: focus on either goes to the log's card, or to the
  // card of files once the log is gone, never onto <body> (#139). So does
  // a Run… form's Run, which shuts the form as the log opens.
  const filesRef = useRef(null);
  const { onFocus, onBlur } = useKeepFocus(() => logRef.current || filesRef.current);
  const shownId = job?.id;
  // A run just started from a form: its log takes focus once it shows (the
  // form, and focus with it, went before the log came; QA pass 3).
  const focusLog = useRef(false);
  useEffect(() => {
    if (shownId == null || shownId === reopened) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (focusLog.current) {
      focusLog.current = false;
      logRef.current?.focus({ preventScroll: true });
    }
    logRef.current?.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
  }, [shownId, reopened]);
  const all = data?.scripts || [];
  const builtins = all.filter(s => s.builtin);
  const files = all.filter(s => !s.builtin);

  // A source's script warning links to its entry (#script-<id>): shown lit,
  // brought into view once it is listed.
  const { hash } = useLocation();
  // Anchors are [a-z0-9_.-] (scriptAnchor): no decoding needed, none to fail.
  const target = hash ? hash.slice(1) : null;
  const scrolled = useRef(null);
  useEffect(() => {
    if (!data || !target || scrolled.current === target) return;
    const el = document.getElementById(target);
    if (!el) return;
    scrolled.current = target;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
  }, [data, target]);
  const litOf = s => target === scriptAnchor(s.id);

  function onStarted(j) {
    focusLog.current = true;
    started(j);
    setJobId(j.id);
  }

  return (
    <div className="jobs-page" onFocus={onFocus} onBlur={onBlur}>
      <PageHeader
        title="Scripts"
        sub={data ? `${files.length} file${files.length === 1 ? "" : "s"}` : "…"}
        actions={<button type="button" className="btn-secondary" onClick={reload}><Icon name="refresh" size={13} />Read again</button>}
      />
      <div className="card" ref={filesRef} tabIndex={-1} role="region" aria-label="Your scripts">
        <p className="page-lede">
          Your own download commands (<code>.json</code>) and shell scripts (<code>.sh</code>), as files in
          {" "}<code className="script-path">{data?.dir || "…"}</code>. Create and edit them in a text editor:
          FeedVault only lists, shows and runs them, and never writes one. A source can sync with one instead of
          its tool&rsquo;s command (its Options). Anyone who can reach FeedVault&rsquo;s port can run them: keep it
          on 127.0.0.1.
        </p>
        {error && <div className="msg err" role="alert">{error.message}</div>}
        {data?.dir_refused && <div className="msg err" role="alert">The folder is refused: {data.dir_refused}. None of its files is run.</div>}
        {!data ? (
          <div className="empty">Loading…</div>
        ) : files.length === 0 ? (
          <div className="empty">No script on disk yet. Copy a template below, or start a shell script.</div>
        ) : (
          <ul className="dl-list">
            {files.map(s => <ScriptRow key={s.file} script={s} dir={data.dir} onStarted={onStarted} lit={litOf(s)} />)}
          </ul>
        )}
      </div>

      {job && (
        <div className="card" ref={logRef} tabIndex={-1} role="region" aria-label="Script log">
          <div className="job-log-head">
            <div className="card-title">Log</div>
            <span className="job-title"><span className="dim mono">#{job.id}</span> {job.label}</span>
            <span className={`chip job-state job-state-${job.state}`}>{job.state}</span>
            <div className="page-head-spacer" />
            {job.message && <span className={`job-message${job.state === "failed" ? " is-err" : ""}`}>{job.message}</span>}
            <Link to="/jobs" className="btn-ghost">Jobs</Link>
            {/* As on a Jobs row: a long script stops from here too (#139). */}
            {!ENDED.has(job.state) && (
              <button type="button" className="btn-danger-soft job-cancel" onClick={() => setConfirm(job)}>
                <Icon name="close" size={13} />Cancel…
              </button>
            )}
            <button type="button" className="del-btn" onClick={() => setJobId(null)} aria-label="Close the log" title="Close">
              <Icon name="close" size={14} />
            </button>
          </div>
          <JobLog key={job.id} jobId={job.id} />
        </div>
      )}

      <div className="card">
        <div className="card-title">Built-in templates</div>
        <p className="page-lede">
          Run one as it is, or copy it into a file to change it: its id is then the file&rsquo;s name.
        </p>
        <ul className="dl-list">
          {builtins.map(s => <ScriptRow key={s.id} script={s} dir={data?.dir} onStarted={onStarted} lit={litOf(s)} />)}
        </ul>
      </div>

      {data && (
        <div className="card">
          <div className="job-log-head">
            <div className="card-title">A shell script</div>
            <div className="page-head-spacer" />
            <button type="button" className="btn-ghost" onClick={() => setShellOpen(o => !o)}>
              {shellOpen ? "Hide" : "Copy template"}
            </button>
          </div>
          <p className="page-lede">
            Run as the file itself, its inputs only as environment variables (<code>FV_TARGET</code>,
            {" "}<code>FV_URL</code>, <code>FV_ROOT</code>, <code>FV_DATA_DIR</code>, <code>FV_ARCHIVE</code>), with
            a minimal environment otherwise.
          </p>
          {shellOpen && <TemplateFile path={`${data.dir}/my-script.sh`} content={data.shell_template} shell />}
        </div>
      )}

      <CancelJobDialog job={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}
