import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { getConfig, saveConfig, browse, getTrash, emptyTrash, startJob, saveToolPaths, saveInstaloaderSettings, saveSettings, cleanInfoJsonCookies, getDownloaders, checkDownloaders } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useJobs, ENDED } from "../lib/jobs";
import { useToast } from "../lib/toast";
import { JobLog } from "./Jobs";
import { fmtAgo, fmtBytes, fmtFullDate, plural } from "../lib/fmt";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import AppearanceSettings from "../components/AppearanceSettings";

function sameList(a, b) {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

function RootsEditor({ saved, onSaved, msg, setMsg }) {
  const { start, running } = useScan();
  const [roots,    setRoots]    = useState(saved);
  const [typed,    setTyped]    = useState("");
  const [picking,  setPicking]  = useState(false);
  const [saving,   setSaving]   = useState(false);

  const dirty = !sameList(roots, saved);

  function add(path) {
    const p = (path || "").trim();
    if (!p) return;
    setRoots(r => (r.includes(p) ? r : [...r, p]));
    setMsg(null);
  }

  async function pick() {
    setPicking(true);
    setMsg(null);
    try {
      const r = await browse();
      if (r?.path) add(r.path);
    } catch (e) {
      setMsg({ ok: false, text: `Folder picker failed: ${e.message}. Type the path instead.` });
    } finally {
      setPicking(false);
    }
  }

  async function save() {
    setSaving(true);
    setMsg(null);
    try {
      const r = await saveConfig(roots);
      if (r?.ok) {
        setRoots(r.config?.media_roots ?? roots);
        onSaved();
        setMsg({ ok: true, text: "Saved. Rescan to index the folders." });
      } else {
        setMsg({ ok: false, text: r?.error || "Save failed." });
      }
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card">
      <div className="card-title">Media roots</div>
      <p className="page-lede">
        Folders FeedVault reads. Point it at wherever instaloader (or gallery-dl, yt-dlp) writes;
        subfolders are scanned too. Scanning never modifies them; only deleting from FeedVault moves files, into the trash below.
      </p>
      {roots.length === 0 ? (
        <div className="roots-empty">No media roots yet. Add one below.</div>
      ) : (
        <ul className="roots-list">
          {roots.map(r => (
            <li key={r} className="root-row">
              <Icon name="folder" size={15} className="root-icon" />
              <code className="root-path" title={r}>{r}</code>
              {!saved.includes(r) && <span className="chip chip-new">unsaved</span>}
              <button
                type="button"
                className="del-btn del-btn-danger"
                onClick={() => { setRoots(list => list.filter(x => x !== r)); setMsg(null); }}
                aria-label={`Remove ${r}`}
                title="Remove"
              >
                <Icon name="trash" size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <form
        className="folder-row"
        onSubmit={e => { e.preventDefault(); add(typed); setTyped(""); }}
      >
        <input
          type="text"
          placeholder="/path/to/downloads"
          aria-label="Folder path to add"
          value={typed}
          onChange={e => setTyped(e.target.value)}
        />
        <button type="submit" className="btn-secondary" disabled={!typed.trim()}>
          <Icon name="plus" size={14} />Add
        </button>
        <button type="button" className="btn-secondary" onClick={pick} disabled={picking}>
          <Icon name="folder" size={14} />{picking ? "Waiting for picker…" : "Browse…"}
        </button>
      </form>

      <div className="settings-actions">
        <button type="button" className="btn-primary" onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {dirty && (
          <button type="button" className="btn-ghost" onClick={() => { setRoots(saved); setMsg(null); }}>
            Discard changes
          </button>
        )}
        <div className="page-head-spacer" />
        <button type="button" className="btn-secondary" onClick={start} disabled={running}>
          <Icon name="refresh" size={14} className={running ? "spin" : ""} />
          {running ? "Scanning…" : "Rescan now"}
        </button>
      </div>
      {msg && <div className={`msg ${msg.ok ? "ok" : "err"}`} role={msg.ok ? "status" : "alert"}>{msg.text}</div>}
    </div>
  );
}

function LastScan() {
  const { status, running } = useScan();
  const last = status?.last;
  return (
    <div className="card">
      <div className="card-title">Last scan</div>
      {running && <div className="msg ok scan-running"><Icon name="refresh" size={13} className="spin" /> A scan is running…</div>}
      {!last ? (
        <div className="dim">{running ? "" : "No scan has run yet."}</div>
      ) : (
        <>
          <div className="kv-row">
            <span className="kv-key">finished</span>
            <span className="kv-val" title={fmtFullDate(last.finished_at)}>
              {last.finished_at ? fmtAgo(last.finished_at) : "—"}
              {last.started_at && last.finished_at ? <span className="dim"> · took {Math.max(0, last.finished_at - last.started_at)}s</span> : null}
            </span>
          </div>
          <div className="scan-counts">
            {[["added", last.added], ["updated", last.updated], ["missing", last.missing], ["unmatched", last.unmatched]].map(([k, v]) => (
              <div key={k} className="scan-count"><span className="mono">{v ?? 0}</span><span>{k}</span></div>
            ))}
          </div>
          {last.errors?.length > 0 && (
            <>
              <div className="card-title card-title-sub">{last.errors.length} error{last.errors.length === 1 ? "" : "s"}</div>
              <ul className="scan-errors-list">
                {last.errors.map((e, i) => (
                  <li key={i}><code title={e.path}>{e.path}</code><span>{e.error}</span></li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}

/* Deleted files wait in <root>/.feedvault-trash/ until this card empties them.
   Refetched on every mount, so it is current after deleting elsewhere. */
function TrashCard() {
  const { refreshKey } = useScan();
  const { data: trash, error, reload } = useApi(getTrash, refreshKey);
  const [confirm, setConfirm] = useState(false);
  const [busy,    setBusy]    = useState(false);
  const [dlgErr,  setDlgErr]  = useState(null);
  const [msg,     setMsg]     = useState(null);

  async function runEmpty() {
    setBusy(true);
    setDlgErr(null);
    try {
      const r = await emptyTrash();
      if (!r?.ok) { setDlgErr(r?.error || "Could not empty the trash."); return; }
      setConfirm(false);
      setMsg({ ok: true, text: `Deleted ${(r.files ?? 0).toLocaleString()} file${r.files === 1 ? "" : "s"} for good, freed ${fmtBytes(r.bytes ?? 0)}.` });
      reload();
    } catch (e) {
      setDlgErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const files = trash?.files ?? 0;
  return (
    <div className="card">
      <div className="card-title">Trash</div>
      <p className="page-lede">
        Deleting a post or item moves its files to a <code>.feedvault-trash</code> folder inside
        the media root it came from. They stay there until you empty the trash, or delete them
        one by one on the <Link to="/trash" className="text-link">Trash</Link> page.
      </p>
      {!trash ? (
        <div className="dim">{error ? `Could not read the trash: ${error.message}` : "Loading…"}</div>
      ) : (
        <>
          <div className="trash-total">
            <span className="mono trash-num">{files.toLocaleString()}</span>
            <span className="dim">file{files === 1 ? "" : "s"}</span>
            <span className="mono trash-num">{fmtBytes(trash.bytes ?? 0)}</span>
          </div>
          {trash.roots?.length > 0 && (
            <ul className="roots-list">
              {trash.roots.map(r => (
                <li key={r.path} className="root-row trash-row">
                  <Icon name="trash" size={15} className="root-icon" />
                  <span className="trash-root">
                    <code className="root-path" title={r.path}>{r.path}</code>
                    <span className="dim">{r.root}</span>
                  </span>
                  <span className="mono trash-row-count">{(r.files ?? 0).toLocaleString()} · {fmtBytes(r.bytes ?? 0)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <div className="settings-actions">
        <Link to="/trash" className="btn-secondary" title="See what is in the trash, restore or delete single posts">
          <Icon name="search" size={14} />Browse the trash
        </Link>
        <button type="button" className="btn-danger" onClick={() => { setDlgErr(null); setMsg(null); setConfirm(true); }} disabled={!files}>
          <Icon name="trash" size={14} />Empty trash…
        </button>
      </div>
      {msg && <div className={`msg ${msg.ok ? "ok" : "err"}`} role="status">{msg.text}</div>}
      <ConfirmDialog
        open={confirm}
        danger
        busy={busy}
        error={dlgErr}
        title="Permanently delete the trash?"
        confirmLabel="Delete forever"
        onConfirm={runEmpty}
        onCancel={() => setConfirm(false)}
      >
        <p>
          This <strong>permanently deletes {files.toLocaleString()} file{files === 1 ? "" : "s"} ({fmtBytes(trash?.bytes ?? 0)})</strong> from
          disk. They do not go to the system trash, and this cannot be undone.
        </p>
      </ConfirmDialog>
    </div>
  );
}

/* A command to run yourself (an install or an update FeedVault cannot do),
   with a copy button. Never run from here. */
function CopyCommand({ command }) {
  const toast = useToast();
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      toast("Copied.");
    } catch {
      toast("Could not copy; select the command instead.", "err");
    }
  }
  return (
    <span className="dl-command">
      <code>{command}</code>
      <button type="button" className="del-btn" onClick={copy} aria-label={`Copy ${command}`} title="Copy">
        <Icon name="copy" size={14} />
      </button>
    </span>
  );
}

// How a tool signs in, from its sync settings (docs/API.md "Downloaders").
function LoginStatus({ tool, login }) {
  if (!login) return <span className="dim">no login needed</span>;
  if (login.mode === "cookies") return <span>{login.browser}&rsquo;s cookies</span>;
  if (login.mode === "login") {
    return login.session_file
      ? <span>saved session of <b>{login.user}</b></span>
      : <span className="dl-warn">no saved session for <b>{login.user}</b>: run <CopyCommand command={`instaloader --login ${login.user}`} /></span>;
  }
  return <span className="dim">none (public only){tool === "ffmpeg" ? "" : " · set one in its sync card below"}</span>;
}

function JobStatus({ job, what }) {
  if (!job) return null;
  const running = !ENDED.has(job.state);
  return (
    <span className={`tool-status${job.state === "done" ? " is-ok" : running ? "" : " is-err"}`}
          title={job.ended_at ? `${fmtFullDate(job.ended_at)}${job.result?.line ? `\n${job.result.line}` : ""}` : undefined}>
      {running ? <><Icon name="refresh" size={12} className="spin" /> {job.state === "queued" ? `${what} queued (waits for its tool's other jobs)` : `${what}…`}</>
        : <>{what}: {job.message || job.state}{job.ended_at ? <span className="dim"> · {fmtAgo(job.ended_at)}</span> : null}</>}
    </span>
  );
}

/* One tool: found or not, how it is installed, its version against PyPI's,
   its login, the path to run it from, and the Test and Update jobs (read
   from the shared jobs poll, so they survive leaving the page). */
function DownloaderRow({ info, saved, test, update, onSaved, shownLog, showLog }) {
  const { started } = useJobs();
  const [path,   setPath]   = useState(saved || "");
  const [saving, setSaving] = useState(false);
  const [msg,    setMsg]    = useState(null);
  const dirty = path.trim() !== (saved || "");
  const tool = info.tool;
  const testing  = test && !ENDED.has(test.state);
  const updating = update && !ENDED.has(update.state);

  async function run(kind) {
    setMsg(null);
    try {
      const r = await startJob(kind, { tool });
      if (r?.ok) { started(r.job); showLog(r.job.id); }
      else setMsg({ ok: false, text: r?.error || "Could not start it." });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  }

  async function save() {
    setSaving(true);
    setMsg(null);
    try {
      const r = await saveToolPaths({ [tool]: path.trim() });
      if (r?.ok) onSaved();
      else setMsg({ ok: false, text: r?.error || "Save failed." });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setSaving(false);
    }
  }

  const latest = info.latest;
  const logJob = [test, update].find(j => j && j.id === shownLog);
  return (
    <li className="dl-row">
      <div className="dl-head">
        <code className="tool-name">{tool}</code>
        <span className={`chip dl-install dl-install-${info.install}`}>{info.install}</span>
        <span className={`tool-status${info.version ? " is-ok" : " is-err"}`}>
          {info.version || (info.found ? info.version_error || "no version" : "not found")}
        </span>
        {latest && (latest.version
          ? (info.outdated
            ? <span className="chip dl-outdated" title={`checked ${fmtAgo(latest.checked_at)}`}>update available: {latest.version}</span>
            : <span className="dim dl-latest" title={`checked ${fmtAgo(latest.checked_at)}`}>latest {latest.version}{info.outdated === false ? " · up to date" : ""}</span>)
          : <span className="dim dl-latest" title={latest.error}>could not check PyPI</span>)}
        <div className="page-head-spacer" />
        {tool !== "ffmpeg" && (
          <button type="button" className="btn-secondary" onClick={() => run("tool-test")} disabled={!info.found || testing}
                  title="Run it once on a public item with its sync's login, to see whether it works">
            <Icon name="play" size={13} />Test
          </button>
        )}
        {info.update.possible && (
          <button type="button" className="btn-secondary" onClick={() => run("tool-update")} disabled={updating}
                  title={info.update.command}>
            <Icon name="arrowUp" size={13} />Update
          </button>
        )}
      </div>
      <div className="kv-row">
        <span className="kv-key">path</span>
        <span className="kv-val">
          {info.path ? <code title={info.real_path ? `links to ${info.real_path}` : undefined}>{info.path}</code> : <span className="dim">—</span>}
          {info.real_path && <span className="dim"> → <code>{info.real_path}</code></span>}
        </span>
      </div>
      {tool !== "ffmpeg" && (
        <div className="kv-row">
          <span className="kv-key">login</span>
          <span className="kv-val"><LoginStatus tool={tool} login={info.login} /></span>
        </div>
      )}
      {!info.found && info.install_hint && (
        <div className="kv-row">
          <span className="kv-key">install</span>
          <span className="kv-val"><CopyCommand command={info.install_hint} /></span>
        </div>
      )}
      {info.found && !info.update.possible && (
        <div className="kv-row">
          <span className="kv-key">update</span>
          <span className="kv-val dl-update">
            <span className="dim">{info.update.reason}</span>
            {info.update.command && <CopyCommand command={info.update.command} />}
          </span>
        </div>
      )}
      {(test || update) && (
        <div className="dl-jobs">
          <JobStatus job={test} what="test" />
          <JobStatus job={update} what="update" />
          {[test, update].filter(Boolean).map(j => (
            <button key={j.id} type="button" className="btn-ghost" onClick={() => showLog(shownLog === j.id ? null : j.id)}>
              {shownLog === j.id ? "Hide log" : `${j.kind === "tool-test" ? "Test" : "Update"} log`}
            </button>
          ))}
        </div>
      )}
      {logJob && <JobLog key={logJob.id} jobId={logJob.id} />}
      <form className="tool-path" onSubmit={e => { e.preventDefault(); if (dirty) save(); }}>
        <input
          type="text"
          placeholder={`found on PATH; or the path to ${tool}`}
          aria-label={`Path to ${tool}`}
          value={path}
          onChange={e => { setPath(e.target.value); setMsg(null); }}
        />
        {dirty && <button type="submit" className="btn-secondary" disabled={saving}>{saving ? "Saving…" : "Save"}</button>}
      </form>
      {msg && <div className={`msg ${msg.ok ? "ok" : "err"} tool-msg`} role="alert">{msg.text}</div>}
    </li>
  );
}

/* Settings → Downloaders: what each tool is, and what to do about it. A
   failed sync links here (#downloaders). */
function DownloadersCard({ saved, onSaved }) {
  const { list } = useJobs();
  const location = useLocation();
  const { data, error, reload } = useApi(getDownloaders);
  const [busy,    setBusy]    = useState(null);    // "check" | "updates"
  const [msg,     setMsg]     = useState(null);
  const [shownLog, setShownLog] = useState(null);
  const ref = useRef(null);

  // The newest test and update of each tool (the list is newest first).
  const tests = {}, updates = {};
  for (const j of list?.jobs || []) {
    const into = j.kind === "tool-test" ? tests : j.kind === "tool-update" ? updates : null;
    if (into && !(j.params.tool in into)) into[j.params.tool] = j;
  }
  // An update that has ended: the backend found its tool again, show it.
  const updated = Object.values(updates).filter(j => ENDED.has(j.state)).map(j => j.id).join(",");
  const seen = useRef(updated);
  useEffect(() => {
    if (updated !== seen.current) { seen.current = updated; reload(); }
  }, [updated, reload]);

  const loaded = !!data;
  useEffect(() => {
    if (loaded && location.hash === "#downloaders") ref.current?.scrollIntoView({ block: "start" });
  }, [loaded, location.hash, location.key]);

  async function act(what, fn) {
    setBusy(what);
    setMsg(null);
    try {
      const r = await fn();
      if (r?.ok === false) setMsg({ ok: false, text: r.error || "Failed." });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(null);
      reload();
    }
  }

  return (
    <div className="card" id="downloaders" ref={ref}>
      <div className="card-title">Downloaders</div>
      <p className="page-lede">
        The tools FeedVault runs, and ffmpeg for video frames. Each is looked up on your PATH, or at the path
        set here (the file must be named after the tool). <b>Test</b> runs one on a public item with its sync&rsquo;s
        login; <b>Update</b> upgrades one installed with pipx or in a virtualenv. Their output shows below the tool and
        on the <Link to="/jobs" className="text-link">Jobs</Link> page.
      </p>
      <div className="settings-actions">
        <label className="dl-toggle" title="Asks pypi.org at most once a day per tool; nothing else is sent">
          <input type="checkbox" checked={!!data?.check_updates} disabled={!data || !!busy}
                 onChange={e => act("updates", () => saveSettings({ check_updates: e.target.checked }))} />
          <span>Check PyPI for new versions <span className="dim">(once a day)</span></span>
        </label>
        <div className="page-head-spacer" />
        {data?.checked_at && <span className="dim" title={fmtFullDate(data.checked_at)}>checked {fmtAgo(data.checked_at)}</span>}
        <button type="button" className="btn-secondary" onClick={() => act("check", checkDownloaders)} disabled={!!busy}>
          <Icon name="refresh" size={13} className={busy ? "spin" : ""} />{busy === "check" ? "Checking…" : "Check again"}
        </button>
      </div>
      {msg && <div className="msg err" role="alert">{msg.text}</div>}
      {!data && <div className="dim">{error ? `Could not check the tools: ${error.message}` : "Checking the tools…"}</div>}
      <ul className="dl-list">
        {(data?.tools || []).map(t => (
          <DownloaderRow key={`${t.tool}:${saved[t.tool] || ""}`} info={t} saved={saved[t.tool]}
                         test={tests[t.tool]} update={updates[t.tool]} shownLog={shownLog} showLog={setShownLog}
                         onSaved={() => { onSaved(); reload(); }} />
        ))}
      </ul>
    </div>
  );
}

const BROWSERS = ["firefox", "chrome", "chromium", "brave", "edge"];

/* How instaloader reaches Instagram when FeedVault syncs a source. FeedVault
   only passes a browser's name or a user name on; instaloader does the rest. */
function InstaloaderCard({ saved, onSaved }) {
  const [mode,    setMode]    = useState(saved.session?.mode || "none");
  const [browser, setBrowser] = useState(saved.session?.browser || "firefox");
  const [user,    setUser]    = useState(saved.session?.user || "");
  const [pause,   setPause]   = useState(String(saved.pause ?? 60));
  const [saving,  setSaving]  = useState(false);
  const [msg,     setMsg]     = useState(null);

  const session = mode === "cookies" ? { mode, browser } : mode === "login" ? { mode, user: user.trim() } : { mode };
  const dirty = JSON.stringify(session) !== JSON.stringify(saved.session || { mode: "none" })
    || pause.trim() !== String(saved.pause ?? 60);

  async function save(e) {
    e.preventDefault();
    const seconds = Number(pause);
    if (!/^\d+$/.test(pause.trim()) || seconds > 3600) {
      setMsg({ ok: false, text: "The pause is whole seconds, from 0 to 3600." });
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      const r = await saveInstaloaderSettings({ session, pause: seconds });
      if (r?.ok) { onSaved(); setMsg({ ok: true, text: "Saved. The next sync uses it." }); }
      else setMsg({ ok: false, text: r?.error || "Save failed." });
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card">
      <div className="card-title">Instagram sync (instaloader)</div>
      <p className="page-lede">
        How instaloader signs in when you click Sync on a source. FeedVault only passes on the browser's
        name or your user name: it never sees, reads or stores your cookies, password or session file.
      </p>
      <form className="insta-form" onSubmit={save}>
        <fieldset className="insta-modes">
          <legend className="insta-legend">Session</legend>
          <label className="insta-mode">
            <input type="radio" name="insta-mode" value="none" checked={mode === "none"} onChange={() => setMode("none")} />
            <span>
              <span><b>No login</b> (default)</span>
              <span className="creator-sub">Public profiles only. Instagram limits anonymous requests sooner, so expect &ldquo;rate limited&rdquo; on big runs.</span>
            </span>
          </label>
          <label className="insta-mode">
            <input type="radio" name="insta-mode" value="cookies" checked={mode === "cookies"} onChange={() => setMode("cookies")} />
            <span>
              <b>Use my browser&rsquo;s Instagram login</b>
              <span className="creator-sub">instaloader reads the cookies of the browser where you are logged in to Instagram (<code>--load-cookies</code>). Close that browser first if it fails.</span>
            </span>
          </label>
          {mode === "cookies" && (
            <select className="insta-input" aria-label="Browser" value={browser} onChange={e => setBrowser(e.target.value)}>
              {BROWSERS.map(b => <option key={b} value={b}>{b}</option>)}
            </select>
          )}
          <label className="insta-mode">
            <input type="radio" name="insta-mode" value="login" checked={mode === "login"} onChange={() => setMode("login")} />
            <span>
              <b>Use a saved instaloader session</b>
              <span className="creator-sub">Run <code>instaloader --login your_name</code> once in a terminal; instaloader keeps its own session file and syncs reuse it (<code>--login</code>). FeedVault never asks for a password: without that file, syncs fail with &ldquo;login needed&rdquo;.</span>
            </span>
          </label>
          {mode === "login" && (
            <input type="text" className="insta-input" placeholder="your Instagram user name" aria-label="Instagram user name"
                   maxLength={30} value={user} onChange={e => setUser(e.target.value)} />
          )}
        </fieldset>
        <label className="insta-pause">
          <span>Pause between two syncs</span>
          <input type="text" inputMode="numeric" className="insta-input" aria-label="Pause in seconds" value={pause}
                 onChange={e => { setPause(e.target.value); setMsg(null); }} />
          <span className="dim">seconds, so Instagram does not see profiles fetched back to back</span>
        </label>
        <div>
          <button type="submit" className="btn-primary" disabled={saving || !dirty || (mode === "login" && !user.trim())}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
        {msg && <div className={`msg ${msg.ok ? "ok" : "err"}`} role="alert">{msg.text}</div>}
      </form>
    </div>
  );
}

const COOKIE_TOOLS = {
  "gallery-dl": { title: "X, Reddit, Bluesky, pixiv sync (gallery-dl)", sites: "X, Reddit, Bluesky or pixiv" },
  "yt-dlp": { title: "YouTube and TikTok sync (yt-dlp)", sites: "YouTube or TikTok" },
};
const TOOL_PAUSE = 30;
const IGNORE_FLAG = { "gallery-dl": "--config-ignore", "yt-dlp": "--ignore-config" };
const PAUSE_TEXT = {
  "gallery-dl": "seconds, so X and the others do not see profiles fetched back to back",
  "yt-dlp": "seconds, so TikTok and YouTube do not see profiles fetched back to back",
};

/* How gallery-dl or yt-dlp reaches the sites when FeedVault syncs a source:
   anonymously, or with a browser's cookies, which the tool reads itself; and
   the pause between two of its syncs. yt-dlp's card also has the YouTube
   length limit. */
function CookiesCard({ tool, saved, maxSeconds, onSaved, msg, setMsg }) {
  const { title, sites } = COOKIE_TOOLS[tool];
  const [mode,    setMode]    = useState(saved.session?.mode || "none");
  const [browser, setBrowser] = useState(saved.session?.browser || "firefox");
  const [longest, setLongest] = useState(String(maxSeconds ?? 180));
  const [pause,   setPause]   = useState(String(saved.pause ?? TOOL_PAUSE));
  const [ignore,  setIgnore]  = useState(!!saved.ignore_config);
  const [saving,  setSaving]  = useState(false);

  const session = mode === "cookies" ? { mode, browser } : { mode };
  const youtube = tool === "yt-dlp";
  const dirty = JSON.stringify(session) !== JSON.stringify(saved.session || { mode: "none" })
    || pause.trim() !== String(saved.pause ?? TOOL_PAUSE)
    || ignore !== !!saved.ignore_config
    || (youtube && longest.trim() !== String(maxSeconds ?? 180));

  async function save(e) {
    e.preventDefault();
    if (!/^\d+$/.test(pause.trim()) || Number(pause) > 3600) {
      setMsg({ ok: false, text: "The pause is whole seconds, from 0 to 3600." });
      return;
    }
    const changes = { [tool]: { session, pause: Number(pause), ignore_config: ignore } };
    if (youtube) {
      const seconds = Number(longest);
      if (!/^\d+$/.test(longest.trim()) || seconds < 1 || seconds > 86400) {
        setMsg({ ok: false, text: "The length is whole seconds, from 1 to 86400." });
        return;
      }
      changes.youtube_max_seconds = seconds;
    }
    setSaving(true);
    setMsg(null);
    try {
      const r = await saveSettings(changes);
      if (r?.ok) { onSaved(); setMsg({ ok: true, text: "Saved. The next sync uses it." }); }
      else setMsg({ ok: false, text: r?.error || "Save failed." });
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card">
      <div className="card-title">{title}</div>
      <p className="page-lede">
        How {tool} signs in when you click Sync on a source. FeedVault only passes on the browser&rsquo;s
        name: it never sees, reads or stores your cookies or credentials.
      </p>
      <form className="insta-form" onSubmit={save}>
        <fieldset className="insta-modes">
          <legend className="insta-legend">Session</legend>
          <label className="insta-mode">
            <input type="radio" name={`${tool}-mode`} value="none" checked={mode === "none"} onChange={() => setMode("none")} />
            <span>
              <span><b>No login</b> (default)</span>
              <span className="creator-sub">Public profiles only. Some sites show less, or limit requests sooner, without a login.</span>
            </span>
          </label>
          <label className="insta-mode">
            <input type="radio" name={`${tool}-mode`} value="cookies" checked={mode === "cookies"} onChange={() => setMode("cookies")} />
            <span>
              <b>Use my browser&rsquo;s login</b>
              <span className="creator-sub">
                {tool} reads the cookies of the browser where you are logged in to {sites} (<code>--cookies-from-browser</code>).
                Close that browser first if it fails.
                {youtube && <> yt-dlp copies the cookies it used into each video&rsquo;s <code>.info.json</code>; FeedVault
                  removes them from the files right after each sync.</>}
              </span>
            </span>
          </label>
          {mode === "cookies" && (
            <select className="insta-input" aria-label="Browser" value={browser} onChange={e => setBrowser(e.target.value)}>
              {BROWSERS.map(b => <option key={b} value={b}>{b}</option>)}
            </select>
          )}
        </fieldset>
        <label className="insta-pause">
          <span>Pause between two syncs</span>
          <input type="text" inputMode="numeric" className="insta-input" aria-label={`${tool} pause in seconds`} value={pause}
                 onChange={e => { setPause(e.target.value); setMsg(null); }} />
          <span className="dim">{PAUSE_TEXT[tool]}</span>
        </label>
        <label className="insta-mode">
          <input type="checkbox" checked={ignore} onChange={e => { setIgnore(e.target.checked); setMsg(null); }} />
          <span>
            <b>Ignore my {tool} config</b>
            <span className="creator-sub">
              Syncs skip your own {tool} config files (<code>{IGNORE_FLAG[tool]}</code>). A config can change where files
              go or how they are named, turn off the metadata FeedVault reads, or run commands after each download. Off by
              default: logins and cookies kept there are skipped too.
            </span>
          </span>
        </label>
        {youtube && (
          <label className="insta-pause">
            <span>Longest YouTube video</span>
            <input type="text" inputMode="numeric" className="insta-input" aria-label="Longest YouTube video in seconds"
                   value={longest} onChange={e => { setLongest(e.target.value); setMsg(null); }} />
            <span className="dim">seconds; longer ones are not downloaded nor indexed (left to ChannelVault)</span>
          </label>
        )}
        <div>
          <button type="submit" className="btn-primary" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
        {msg && <div className={`msg ${msg.ok ? "ok" : "err"}`} role="alert">{msg.text}</div>}
      </form>
      {youtube && <InfoJsonCookies />}
    </div>
  );
}

/* Info JSONs written before FeedVault removed the cookies after each sync:
   counted first (a dry run), then cleaned once confirmed. */
function InfoJsonCookies() {
  const [found,   setFound]   = useState(null);   // the dry run's answer, while the dialog is open
  const [busy,    setBusy]    = useState(false);
  const [dlgErr,  setDlgErr]  = useState(null);
  const [msg,     setMsg]     = useState(null);

  async function check() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await cleanInfoJsonCookies(false);
      if (!r?.ok) setMsg({ ok: false, text: r?.error || "Could not check the info JSONs." });
      else if (!r.files) setMsg({ ok: true, text: `No yt-dlp info JSON holds cookies (${plural(r.checked, "info JSON")} checked).` });
      else { setDlgErr(null); setFound(r); }
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    setBusy(true);
    setDlgErr(null);
    try {
      const r = await cleanInfoJsonCookies(true);
      if (!r?.ok) { setDlgErr(r?.error || "Could not clean the info JSONs."); return; }
      setFound(null);
      const failed = r.failures ? ` ${plural(r.failures, "file")} could not be changed: ${r.failed.map(f => `${f.path} (${f.error})`).join(", ")}${r.failures > r.failed.length ? ", …" : ""}` : "";
      setMsg({ ok: !r.failures, text: `Removed the cookies from ${plural(r.files, "info JSON")}.${failed}` });
    } catch (err) {
      setDlgErr(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="info-cookies">
      <p className="page-lede">
        Videos synced with cookies before FeedVault did this still have them in their <code>.info.json</code>.
        This looks through every yt-dlp info JSON under your media roots and counts those first.
      </p>
      <div className="settings-actions">
        <button type="button" className="btn-secondary" onClick={check} disabled={busy}>
          <Icon name="search" size={14} />{busy && !found ? "Checking…" : "Remove cookies from existing yt-dlp info JSONs…"}
        </button>
      </div>
      {msg && <div className={`msg ${msg.ok ? "ok" : "err"}`} role="status">{msg.text}</div>}
      <ConfirmDialog
        open={!!found}
        busy={busy}
        error={dlgErr}
        title={`Remove the cookies from ${plural(found?.files ?? 0, "info JSON")}?`}
        confirmLabel="Remove cookies"
        onConfirm={run}
        onCancel={() => setFound(null)}
      >
        <p>
          {plural(found?.files ?? 0, "yt-dlp info JSON")} of the {plural(found?.checked ?? 0, "info JSON")} under your
          media roots hold cookies. Each is rewritten without its <code>cookies</code> and <code>Cookie</code> headers;
          everything else in it, and its date, stay the same.
        </p>
      </ConfirmDialog>
    </div>
  );
}

const ROUTE_TOOLS = ["gallery-dl", "yt-dlp", "instaloader"];

/* Which tool syncs a pasted link, by its host: a host matches itself and its
   subdomains, the longest entry wins. instaloader is for instagram.com only. */
function RoutesCard({ saved, onSaved, msg, setMsg }) {
  const [rows,   setRows]   = useState(() => Object.entries(saved).map(([host, tool]) => ({ host, tool })));
  const [saving, setSaving] = useState(false);

  const table = Object.fromEntries(rows.filter(r => r.host.trim()).map(r => [r.host.trim().toLowerCase(), r.tool]));
  const dirty = JSON.stringify(table) !== JSON.stringify(saved);

  function edit(i, change) {
    setRows(list => list.map((r, j) => (j === i ? { ...r, ...change } : r)));
    setMsg(null);
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    setMsg(null);
    try {
      const r = await saveSettings({ routes: table });
      if (r?.ok) { onSaved(); setMsg({ ok: true, text: "Saved. New links use it; existing sources keep their tool, but one whose site is no longer listed cannot sync." }); }
      else setMsg({ ok: false, text: r?.error || "Save failed." });
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card">
      <div className="card-title">Link routing</div>
      <p className="page-lede">
        Which downloader a pasted profile link goes to, by its site. A site covers its subdomains too
        (<code>m.youtube.com</code>); a link to a site not listed is refused.
      </p>
      <form className="insta-form" onSubmit={save}>
        <ul className="route-list">
          {rows.map((r, i) => (
            <li key={i} className="route-row">
              <input type="text" className="insta-input route-host" aria-label="Site" placeholder="example.com"
                     maxLength={253} value={r.host} onChange={e => edit(i, { host: e.target.value })} />
              <select className="insta-input route-tool" aria-label={`Tool for ${r.host || "this site"}`} value={r.tool}
                      onChange={e => edit(i, { tool: e.target.value })}>
                {ROUTE_TOOLS.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <button type="button" className="del-btn del-btn-danger" aria-label={`Remove ${r.host}`} title="Remove"
                      onClick={() => { setRows(list => list.filter((_, j) => j !== i)); setMsg(null); }}>
                <Icon name="trash" size={15} />
              </button>
            </li>
          ))}
        </ul>
        <div className="settings-actions">
          <button type="button" className="btn-secondary" onClick={() => setRows(list => [...list, { host: "", tool: "gallery-dl" }])}>
            <Icon name="plus" size={14} />Add a site
          </button>
          <button type="submit" className="btn-primary" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </button>
          {dirty && (
            <button type="button" className="btn-ghost"
                    onClick={() => { setRows(Object.entries(saved).map(([host, tool]) => ({ host, tool }))); setMsg(null); }}>
              Discard changes
            </button>
          )}
        </div>
        {msg && <div className={`msg ${msg.ok ? "ok" : "err"}`} role="alert">{msg.text}</div>}
      </form>
    </div>
  );
}

const TABS = [
  { id: "library",    label: "Library",    icon: "folder" },
  { id: "downloads",  label: "Downloads",  icon: "download" },
  { id: "sync",       label: "Sync",       icon: "refresh" },
  { id: "appearance", label: "Appearance", icon: "palette" },
  { id: "about",      label: "About",      icon: "info" },
];
// Cards linked to from elsewhere (/settings#downloaders), and their tab.
const ANCHORS = { downloaders: "downloads" };

function tabOf(hash) {
  const h = hash.replace(/^#/, "");
  return TABS.some(t => t.id === h) ? h : ANCHORS[h] || "library";
}

export default function Settings() {
  const { refreshKey } = useScan();
  const { data: config, error, reload } = useApi(getConfig, refreshKey);
  const { hash } = useLocation();
  const tab = tabOf(hash);
  // Lives here, not in the editor: a save remounts the editor (see key below)
  // and the confirmation must survive that.
  const [msg, setMsg] = useState(null);   // { ok, text }
  const [notes, setNotes] = useState({});  // the same, per settings card below
  const note = name => ({ msg: notes[name] || null, setMsg: m => setNotes(n => ({ ...n, [name]: m })) });

  return (
    <div className="settings-page">
      <div className="page-head page-head-bare"><h2 className="page-title">Settings</h2></div>
      <div className="settings-layout">
        <nav className="settings-tabs" aria-label="Settings sections">
          {TABS.map(t => (
            <Link key={t.id} to={{ hash: `#${t.id}` }} replace className={`settings-tab${tab === t.id ? " active" : ""}`}
                  aria-current={tab === t.id ? "page" : undefined}>
              <Icon name={t.icon} size={15} />{t.label}
            </Link>
          ))}
        </nav>
        <div className="settings-pane">
          {/* Every tab but Appearance stays mounted while hidden, so unsaved
              edits in a card survive a look at another tab. Leaving the theme
              editor puts the saved theme back, so that one unmounts. */}
          {tab === "appearance" && <AppearanceSettings />}
          {!config ? (
            tab !== "appearance" && <div className="card"><div className="empty">{error ? `Could not load settings: ${error.message}` : "Loading…"}</div></div>
          ) : (
            <>
              <div className="settings-group" hidden={tab !== "library"}>
                {/* Keyed on the saved list so a refresh from the backend resets the editor. */}
                <RootsEditor
                  key={(config.media_roots || []).join("\n")}
                  saved={config.media_roots || []}
                  onSaved={reload}
                  msg={msg}
                  setMsg={setMsg}
                />
                <TrashCard />
                <LastScan />
              </div>
              <div className="settings-group" hidden={tab !== "downloads"}>
                <DownloadersCard saved={config.tools || {}} onSaved={reload} />
                <RoutesCard key={JSON.stringify(config.routes)} saved={config.routes || {}} onSaved={reload} {...note("routes")} />
              </div>
              <div className="settings-group" hidden={tab !== "sync"}>
                <InstaloaderCard key={JSON.stringify(config.instaloader)} saved={config.instaloader || {}} onSaved={reload} />
                {Object.keys(COOKIE_TOOLS).map(t => (
                  <CookiesCard key={`${t}:${JSON.stringify(config[t])}:${t === "yt-dlp" ? config.youtube_max_seconds : ""}`} tool={t}
                               saved={config[t] || {}} maxSeconds={config.youtube_max_seconds} onSaved={reload} {...note(t)} />
                ))}
              </div>
              <div className="settings-group" hidden={tab !== "about"}>
                <div className="card">
                  <div className="card-title">About</div>
                  <div className="kv-row">
                    <span className="kv-key">data directory</span>
                    <code className="kv-val">{config.data_directory || "—"}</code>
                  </div>
                  <div className="kv-row">
                    <span className="kv-key">version</span>
                    <code className="kv-val">{config.version || "—"}</code>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
