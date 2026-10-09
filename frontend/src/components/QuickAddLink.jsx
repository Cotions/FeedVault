import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { createLink, getPeople, getRecentLinkPeople } from "../lib/api";
import { MAX_NOTES, MAX_TITLE, MAX_URL, externalUrl, withScheme } from "../lib/links";
import { NO_PERSON, personPath } from "../lib/people";
import { focusLost, restoreFocus, trapTab } from "../lib/layout";
import Icon from "./Icon";
import PersonPicker from "./PersonPicker";

export const UNSORTED_PATH = "/links?person=none";

/* Save a link in a few keys (#165): its address, an optional title and
   notes, and who it goes to (PersonPicker: no one, a recent person, or
   anyone found by name). Enter in the address, title or person field saves
   to the highlighted choice. A URL saved already says whose it is, with a
   link there, instead of failing silently.

   Props: url (to start with), clipboard (read the clipboard for an address
   when ``url`` is empty), onSaved(link), onLeave() (a link in the form was
   followed, or Cancel) */
export function QuickAddForm({ url: startUrl = "", clipboard = false, onSaved, onLeave }) {
  const [url,    setUrl]    = useState(startUrl);
  const [title,  setTitle]  = useState("");
  const [notes,  setNotes]  = useState("");
  const [active, setActive] = useState(NO_PERSON);
  const [people, setPeople] = useState(null);
  const [recent, setRecent] = useState(null);
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState(null);
  const [taken,  setTaken]  = useState(null);     // the link saved with this URL already
  const urlRef = useRef(null);
  const personRef = useRef(null);
  const typed = useRef(!!startUrl);               // the address field was given or typed into

  // With an address to start with, the next thing to say is who it is for.
  useEffect(() => {
    (startUrl ? personRef : urlRef).current?.focus();
    // Once, as the form opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let alive = true;
    getPeople().then(p => { if (alive) setPeople(p); }, () => { if (alive) setPeople([]); });
    getRecentLinkPeople(10).then(r => { if (alive) setRecent(r.people); }, () => { if (alive) setRecent([]); });
    return () => { alive = false; };
  }, []);

  // An address on the clipboard fills an empty field, unless typing got
  // there first. No permission, no address, no clipboard: nothing happens.
  useEffect(() => {
    if (!clipboard || startUrl || !navigator.clipboard?.readText) return undefined;
    let alive = true;
    navigator.clipboard.readText().then(text => {
      const found = externalUrl(text);
      if (!alive || !found || typed.current) return;
      typed.current = true;
      setUrl(found);
      if (document.activeElement === urlRef.current) personRef.current?.focus();
    }, () => {});
    return () => { alive = false; };
  }, [clipboard, startUrl]);

  async function save(option) {
    if (busy) return;
    if (!url.trim()) {
      setError("Paste or type the link's address first.");
      urlRef.current?.focus();
      return;
    }
    setError(null);
    setTaken(null);
    const sent = withScheme(url);
    if (sent !== url) setUrl(sent);
    setBusy(true);
    try {
      const r = await createLink({ url: sent, title, notes, person: option.person ? option.person.id : null });
      if (r?.ok) { onSaved(r.link); return; }
      if (r?.link) setTaken(r.link);
      else setError(r?.error || "Could not save the link.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const to = active.person ? active.person.name : "Unsorted";
  return (
    <form className="quick-add" onSubmit={e => { e.preventDefault(); save(active); }} aria-busy={busy}>
      <label className="filter quick-add-url">
        <span>Address</span>
        <input ref={urlRef} type="text" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://…"
               maxLength={MAX_URL} value={url}
               onChange={e => { typed.current = true; setUrl(e.target.value); setError(null); setTaken(null); }} />
      </label>
      <div className="quick-add-row">
        <label className="filter">
          <span>Title</span>
          <input type="text" className="link-input-text" placeholder="Optional" maxLength={MAX_TITLE} value={title}
                 onChange={e => setTitle(e.target.value)} />
        </label>
        <label className="filter">
          <span>Notes</span>
          <textarea className="person-notes" rows={1} maxLength={MAX_NOTES} placeholder="Optional" value={notes}
                    onChange={e => setNotes(e.target.value)} />
        </label>
      </div>
      <div className="filter quick-add-person">
        <span>Person</span>
        <PersonPicker people={people} recent={recent} active={active} onActive={setActive} onPick={o => { setActive(o); save(o); }}
                      inputRef={personRef} label="Person" />
        <div className="quick-add-hint dim"><kbd>↑</kbd><kbd>↓</kbd> choose · <kbd>Enter</kbd> save · <kbd>Esc</kbd> close</div>
      </div>
      {taken && (
        <div className="msg err quick-add-taken" role="alert">
          Already saved{taken.person ? <>, tied to <Link to={personPath(taken.person.id)} onClick={onLeave}>{taken.person.name}</Link></>
            : <>, in <Link to={UNSORTED_PATH} onClick={onLeave}>Unsorted</Link></>}.
        </div>
      )}
      {error && <div className="msg err" role="alert">{error}</div>}
      <div className="modal-actions quick-add-actions">
        <button type="button" className="btn-secondary" onClick={onLeave}>Cancel</button>
        <button type="submit" className="btn-primary quick-add-save" aria-disabled={busy || undefined} title={`Save to ${to}`}>
          <Icon name="plus" size={14} /><span className="quick-add-to">{busy ? "Saving…" : `Save to ${to}`}</span>
        </button>
      </div>
    </form>
  );
}

/* QuickAddForm in a dialog over any page: Escape or a click beside it
   closes it, Tab stays inside, and focus goes back where it was.

   Props: url, clipboard (as QuickAddForm), onSaved(link), onClose */
export default function QuickAddLink({ url, clipboard, onSaved, onClose }) {
  const boxRef = useRef(null);
  const titleId = useId();

  useEffect(() => {
    const prev = document.activeElement;
    return () => restoreFocus(prev);
  }, []);

  // Focus lost to <body> (a control that went away) never reaches the
  // overlay's own handler: Escape and Tab are heard on the document then.
  useEffect(() => {
    function onKey(e) {
      if (!focusLost()) return;
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      else trapTab(e, boxRef.current);
    }
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return createPortal(
    <div
      className="modal-overlay"
      onKeyDown={e => {
        if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); onClose(); return; }
        trapTab(e, boxRef.current);
      }}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div ref={boxRef} className="modal quick-add-modal" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId} className="modal-title"><Icon name="link" size={16} />Add a link</h2>
        <QuickAddForm url={url} clipboard={clipboard} onSaved={onSaved} onLeave={onClose} />
      </div>
    </div>,
    document.body,
  );
}
