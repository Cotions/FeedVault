import { useState } from "react";
import { browse, createProfile, renameProfile, deleteProfile } from "../lib/api";
import { useProfiles, switchProfile, profileCount } from "../lib/profiles";
import ConfirmDialog from "./ConfirmDialog";
import Icon from "./Icon";

const EMPTY = { name: "", data: "", roots: [], typed: "" };

/* Settings → Profiles. A profile is a library of its own: its data
   directory (database, thumbnails, tags, people, sources) and its media
   roots. Removing one only forgets it: nothing on disk is deleted, and a
   profile made again on the same data directory has it all back. */
export default function ProfileSettings({ onDirty }) {
  const { profiles, reload } = useProfiles();
  const [form,     setForm]     = useState(EMPTY);
  const [picking,  setPicking]  = useState(null);    // "data" | "root" while the folder picker is open
  const [creating, setCreating] = useState(false);
  const [editing,  setEditing]  = useState(null);    // { id, name } while a name is edited
  const [removing, setRemoving] = useState(null);    // the profile asked about
  const [busy,     setBusy]     = useState(false);
  const [dlgErr,   setDlgErr]   = useState(null);
  const [msg,      setMsg]      = useState(null);    // { ok, text }, under the list
  const [formMsg,  setFormMsg]  = useState(null);    // the same, under the form

  const dirty = !!(form.name.trim() || form.data.trim() || form.roots.length || form.typed.trim());
  function edit(changes) {
    const next = { ...form, ...changes };
    setForm(next);
    onDirty?.("New profile", !!(next.name.trim() || next.data.trim() || next.roots.length || next.typed.trim()));
  }

  function addRoot(path) {
    const p = (path || "").trim();
    if (p) edit({ roots: form.roots.includes(p) ? form.roots : [...form.roots, p], typed: "" });
  }

  async function pick(what) {
    setPicking(what);
    setFormMsg(null);
    try {
      const r = await browse();
      if (r?.path && what === "data") edit({ data: r.path });
      else if (r?.path) addRoot(r.path);
      else if (r?.error) setFormMsg({ ok: false, text: `Folder picker failed: ${r.error}. Type the path instead.` });
    } catch (e) {
      setFormMsg({ ok: false, text: `Folder picker failed: ${e.message}. Type the path instead.` });
    } finally {
      setPicking(null);
    }
  }

  async function create(e) {
    e.preventDefault();
    setCreating(true);
    setFormMsg(null);
    try {
      const typed = form.typed.trim();
      const roots = typed && !form.roots.includes(typed) ? [...form.roots, typed] : form.roots;
      const r = await createProfile({ name: form.name.trim(), data_directory: form.data.trim(), media_roots: roots });
      if (!r?.ok) { setFormMsg({ ok: false, text: r?.error || "Could not create the profile." }); return; }
      setForm(EMPTY);
      onDirty?.("New profile", false);
      reload();
      setFormMsg({ ok: true, text: `Created ${r.profile.name}. Switch to it to scan its folders.` });
    } catch (err) {
      setFormMsg({ ok: false, text: err.message });
    } finally {
      setCreating(false);
    }
  }

  async function rename(e) {
    e.preventDefault();
    setMsg(null);
    try {
      const r = await renameProfile(editing.id, editing.name.trim());
      if (!r?.ok) { setMsg({ ok: false, text: r?.error || "Rename failed." }); return; }
      setEditing(null);
      reload();
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    }
  }

  async function remove() {
    setBusy(true);
    setDlgErr(null);
    try {
      const r = await deleteProfile(removing.id);
      if (!r?.ok) { setDlgErr(r?.error || "Could not remove the profile."); return; }
      setMsg({ ok: true, text: `Removed ${removing.name}. Its files are still on disk.` });
      setRemoving(null);
      reload();
    } catch (err) {
      setDlgErr(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function activate(p) {
    setBusy(true);
    setMsg(null);
    try {
      await switchProfile(p.id);
    } catch (err) {
      setMsg({ ok: false, text: err.message });
      setBusy(false);
    }
  }

  return (
    <>
      <div className="card" id="profiles-card">
        <div className="card-title">Profiles</div>
        <p className="page-lede">
          Each profile is a library of its own: its media roots, and a data directory holding its database,
          thumbnails, tags, people, collections, links and sources. One is active at a time; switching reloads
          FeedVault in the other. Downloaders and their settings are shared. Scheduled syncs, and the
          userscript&rsquo;s Save, only reach the active profile.
        </p>
        <ul className="roots-list profile-list">
          {profiles.map(p => (
            <li key={p.id} className={`root-row profile-row${p.active ? " is-active" : ""}`}>
              <Icon name="vault" size={16} className="root-icon" />
              <div className="profile-row-main">
                {editing?.id === p.id ? (
                  <form className="folder-row" onSubmit={rename}>
                    <input type="text" autoFocus aria-label={`New name of ${p.name}`} maxLength={60} value={editing.name}
                           onChange={e => setEditing({ ...editing, name: e.target.value })}
                           onKeyDown={e => { if (e.key === "Escape") { e.stopPropagation(); setEditing(null); } }} />
                    <button type="submit" className="btn-primary" disabled={!editing.name.trim()}>Save</button>
                    <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
                  </form>
                ) : (
                  <div className="profile-row-head">
                    <b className="profile-row-name" title={p.name}>{p.name}</b>
                    {p.active && <span className="chip chip-new">active</span>}
                    <span className="dim profile-row-count">{profileCount(p)}</span>
                  </div>
                )}
                <code className="profile-row-path" title="Data directory">{p.data_directory}</code>
                {p.media_roots.length === 0
                  ? <span className="dim profile-row-path">No media roots yet</span>
                  : p.media_roots.map(r => <code key={r} className="profile-row-path dim" title="Media root">{r}</code>)}
              </div>
              {editing?.id !== p.id && (
                <div className="profile-row-actions">
                  {!p.active && (
                    <button type="button" className="btn-secondary" onClick={() => activate(p)} disabled={busy}>Switch</button>
                  )}
                  <button type="button" className="del-btn" onClick={() => { setEditing({ id: p.id, name: p.name }); setMsg(null); }}
                          aria-label={`Rename ${p.name}`} title="Rename">
                    <Icon name="pencil" size={15} />
                  </button>
                  <button type="button" className="del-btn del-btn-danger" disabled={p.active}
                          onClick={() => { setDlgErr(null); setMsg(null); setRemoving(p); }}
                          aria-label={`Remove ${p.name}`}
                          title={p.active ? "Switch to another profile before removing this one" : "Remove (its files stay on disk)"}>
                    <Icon name="trash" size={15} />
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
        {msg && <div className={`msg ${msg.ok ? "ok" : "err"}`} role={msg.ok ? "status" : "alert"}>{msg.text}</div>}
      </div>

      <form className="card" onSubmit={create}>
        <div className="card-title">New profile</div>
        <p className="page-lede">
          Pick an empty data directory to start a new library, or the one a removed profile used to have it back.
          Media roots can also be set later, in Library, once you have switched to it; a folder can only be in one profile.
        </p>
        <div className="profile-form">
          <label className="profile-field">
            <span>Name</span>
            <input type="text" maxLength={60} placeholder="Work, Art references…" value={form.name}
                   onChange={e => edit({ name: e.target.value })} />
          </label>
          <label className="profile-field">
            <span>Data directory</span>
            <span className="folder-row">
              <input type="text" placeholder="/path/to/its/data" value={form.data} onChange={e => edit({ data: e.target.value })} />
              <button type="button" className="btn-secondary" onClick={() => pick("data")} disabled={picking != null}>
                <Icon name="folder" size={14} />{picking === "data" ? "Waiting for picker…" : "Browse…"}
              </button>
            </span>
          </label>
          <div className="profile-field">
            <span id="profile-roots-label">Media roots</span>
            {form.roots.length > 0 && (
              <ul className="roots-list">
                {form.roots.map(r => (
                  <li key={r} className="root-row">
                    <Icon name="folder" size={15} className="root-icon" />
                    <code className="root-path" title={r}>{r}</code>
                    <button type="button" className="del-btn del-btn-danger" aria-label={`Remove ${r}`} title="Remove"
                            onClick={() => edit({ roots: form.roots.filter(x => x !== r) })}>
                      <Icon name="trash" size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <span className="folder-row">
              <input type="text" placeholder="/path/to/downloads" aria-labelledby="profile-roots-label" value={form.typed}
                     onChange={e => edit({ typed: e.target.value })}
                     onKeyDown={e => { if (e.key === "Enter" && form.typed.trim()) { e.preventDefault(); addRoot(form.typed); } }} />
              <button type="button" className="btn-secondary" onClick={() => addRoot(form.typed)} disabled={!form.typed.trim()}>
                <Icon name="plus" size={14} />Add
              </button>
              <button type="button" className="btn-secondary" onClick={() => pick("root")} disabled={picking != null}>
                <Icon name="folder" size={14} />{picking === "root" ? "Waiting for picker…" : "Browse…"}
              </button>
            </span>
          </div>
        </div>
        <div className="settings-actions">
          <button type="submit" className="btn-primary" disabled={creating || !form.name.trim() || !form.data.trim()}>
            {creating ? "Creating…" : "Create profile"}
          </button>
          {dirty && (
            <button type="button" className="btn-ghost" onClick={() => { setForm(EMPTY); onDirty?.("New profile", false); setFormMsg(null); }}>
              Discard
            </button>
          )}
        </div>
        {formMsg && <div className={`msg ${formMsg.ok ? "ok" : "err"}`} role={formMsg.ok ? "status" : "alert"}>{formMsg.text}</div>}
      </form>

      <ConfirmDialog
        open={removing != null}
        danger
        busy={busy}
        error={dlgErr}
        title={`Remove the profile “${removing?.name}”?`}
        confirmLabel="Remove profile"
        onConfirm={remove}
        onCancel={() => setRemoving(null)}
      >
        <p>
          FeedVault forgets this profile. <strong>Its files stay on disk</strong>, untouched: its media, and its
          data directory <code>{removing?.data_directory}</code> with its database, tags and people.
          A new profile on that data directory brings it all back.
        </p>
      </ConfirmDialog>
    </>
  );
}
