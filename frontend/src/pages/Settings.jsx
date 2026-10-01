import { useState } from "react";
import { Link } from "react-router-dom";
import { getConfig, saveConfig, browse, getTrash, emptyTrash } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { fmtAgo, fmtBytes, fmtFullDate } from "../lib/fmt";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";

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

export default function Settings() {
  const { refreshKey } = useScan();
  const { data: config, error, reload } = useApi(getConfig, refreshKey);
  // Lives here, not in the editor: a save remounts the editor (see key below)
  // and the confirmation must survive that.
  const [msg, setMsg] = useState(null);   // { ok, text }

  return (
    <div className="settings-page">
      <div className="page-head page-head-bare"><h2 className="page-title">Settings</h2></div>
      {!config ? (
        <div className="card"><div className="empty">{error ? `Could not load settings: ${error.message}` : "Loading…"}</div></div>
      ) : (
        <>
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
        </>
      )}
    </div>
  );
}
