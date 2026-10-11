import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useProfiles, switchProfile } from "../lib/profiles";
import Icon from "./Icon";

/* The header's menu for jumping between profiles. Not there while there is
   only one, so a single library looks as it did before profiles. A long
   name is cut with an ellipsis (its whole text in the title): the button
   never grows into the search field. */
export default function ProfileSwitch() {
  const { profiles } = useProfiles();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(null);   // the id being switched to
  const [err,  setErr]  = useState(null);
  const wrap = useRef(null);
  const btn  = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e) { if (!wrap.current?.contains(e.target)) setOpen(false); }
    function onKey(e) {
      if (e.key !== "Escape") return;
      setOpen(false);
      btn.current?.focus();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  if (profiles.length < 2) return null;
  const active = profiles.find(p => p.active) || profiles[0];

  async function pick(p) {
    if (p.active) { setOpen(false); return; }
    setBusy(p.id);
    setErr(null);
    try {
      await switchProfile(p.id);
    } catch (e) {
      setErr(e.message);
      setBusy(null);
    }
  }

  return (
    <div className="profile-switch" ref={wrap}>
      <button type="button" ref={btn} className={`profile-switch-btn${open ? " is-open" : ""}`}
              onClick={() => { setOpen(o => !o); setErr(null); }}
              aria-haspopup="menu" aria-expanded={open}
              aria-label={`Profile: ${active.name}. Switch profile`} title={`Profile: ${active.name}`}>
        <Icon name="vault" size={14} />
        <span className="profile-switch-name">{active.name}</span>
        <span className="profile-switch-caret" aria-hidden="true" />
      </button>
      {open && (
        <div className="profile-menu" role="menu" aria-label="Profiles">
          {profiles.map(p => (
            <button type="button" key={p.id} role="menuitemradio" aria-checked={p.active}
                    className={`profile-option${p.active ? " is-on" : ""}`}
                    onClick={() => pick(p)} disabled={busy != null} title={p.name}>
              <span className="profile-option-mark">{p.active && <Icon name="check" size={12} />}</span>
              <span className="profile-option-name">{busy === p.id ? "Switching…" : p.name}</span>
              <span className="profile-option-n">{p.posts == null ? "?" : p.posts.toLocaleString("en-US")}</span>
            </button>
          ))}
          {err && <div className="profile-menu-err" role="alert">{err}</div>}
          <Link to="/settings#profiles" className="profile-menu-manage" role="menuitem" onClick={() => setOpen(false)}>
            <Icon name="settings" size={13} />Manage profiles
          </Link>
        </div>
      )}
    </div>
  );
}
