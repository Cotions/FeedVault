import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { getRecentLinkPeople } from "../lib/api";
import { plural, safeUrl } from "../lib/fmt";
import { personPath } from "../lib/people";
import { KIND_LABEL, MAX_NOTES, MAX_TITLE, MAX_URL, linkLabel, withScheme } from "../lib/links";
import { focusLost, restoreFocus, trapTab } from "../lib/layout";
import Icon from "./Icon";
import CreatorPicker from "./CreatorPicker";
import PersonPicker from "./PersonPicker";

/* A link's fields: URL, title, notes, and its person when ``people`` is
   given (the person page presets its own and leaves the picker out).
   onSubmit({ url, title, notes, person? }) resolves to an error message, or
   null when it went through (a new link's form then empties; the caller
   closes an edit). onDirty(bool) hears whether the fields differ from the
   link's (false again when the form goes). */
export function LinkForm({
  link = null, people = null, onSubmit, onCancel, onDirty, busy = false, submitLabel = "Add link", idPrefix = "link",
}) {
  const [url,    setUrl]    = useState(link?.url || "");
  const [title,  setTitle]  = useState(link?.title || "");
  const [notes,  setNotes]  = useState(link?.notes || "");
  const [person, setPerson] = useState(link?.person?.id ?? null);
  const [error,  setError]  = useState(null);
  const dirty = url !== (link?.url || "") || title !== (link?.title || "") || notes !== (link?.notes || "")
    || (!!people && person !== (link?.person?.id ?? null));

  useEffect(() => {
    onDirty?.(dirty);
    return () => onDirty?.(false);
  }, [dirty, onDirty]);

  async function submit(e) {
    e.preventDefault();
    if (!url.trim()) return;
    setError(null);
    // "example.org/x" goes as https://example.org/x, and the field shows what went.
    const sent = withScheme(url);
    if (sent !== url) setUrl(sent);
    const body = { url: sent, title, notes, ...(people ? { person } : {}) };
    const problem = await onSubmit(body);
    if (problem) { setError(problem); return; }
    if (!link) { setUrl(""); setTitle(""); setNotes(""); setPerson(null); }
  }

  return (
    <form className={`link-form${link ? " is-edit" : ""}`} onSubmit={submit}
          onKeyDown={e => { if (e.key === "Escape" && onCancel) { e.stopPropagation(); onCancel(); } }}>
      <label className="filter link-form-url">
        <span>Address</span>
        <input type="text" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://…"
               id={`${idPrefix}-url`} maxLength={MAX_URL} value={url} autoFocus={!!link}
               onChange={e => { setUrl(e.target.value); setError(null); }} />
      </label>
      <label className="filter link-form-title">
        <span>Title</span>
        <input type="text" className="link-input-text" placeholder="Optional" maxLength={MAX_TITLE} value={title}
               onChange={e => setTitle(e.target.value)} />
      </label>
      {people && (
        <div className="filter link-form-person">
          <span>Person</span>
          <CreatorPicker
            people={people}
            accounts={[]}
            value={person != null ? { person } : null}
            label="Person"
            allLabel="No one"
            placeholder="Search people…"
            onChange={v => setPerson(v?.person ? v.person.id : null)}
          />
        </div>
      )}
      <label className="filter link-form-notes">
        <span>Notes</span>
        <textarea className="person-notes" rows={1} maxLength={MAX_NOTES} placeholder="Optional" value={notes}
                  onChange={e => setNotes(e.target.value)} />
      </label>
      <div className="link-form-actions">
        <button type="submit" className="btn-primary" disabled={busy || !url.trim()}>
          {!link && <Icon name="plus" size={14} />}{submitLabel}
        </button>
        {onCancel && <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>}
      </div>
      {error && <div className="msg err link-form-msg" role="alert">{error}</div>}
    </form>
  );
}

/* One link: its title (or address) opening the site in a new tab, the
   site and kind, its person (``showPerson``), notes, and the row's actions.
   Only an http(s) address is ever a link (safeUrl); anything else is text.
   On the Links page: ``select`` ({ checked, onClick(event) }) puts a
   checkbox first (shift-click for a range), ``onAssign`` an "Assign…" on a
   link of no one, and ``children`` (the open assign picker) go under it. */
export function LinkRow({
  link: l, showPerson = true, busy = false, onEdit, onDelete, onUp, onDown, flash = false, index, select, onAssign, children,
}) {
  const href = safeUrl(l.url);
  const label = linkLabel(l);
  return (
    <li className={`link-row${flash ? " is-flash" : ""}${select?.checked ? " is-selected" : ""}${children ? " is-assigning" : ""}`}
        data-link-id={l.id} data-index={index}>
      {select ? (
        <label className="link-check" title="Select (shift-click: a range)">
          <input type="checkbox" checked={select.checked} onChange={() => {}} onClick={select.onClick}
                 aria-label={`Select ${label}`} />
        </label>
      ) : <span className="link-row-icon" aria-hidden="true"><Icon name="link" size={15} /></span>}
      <span className="link-row-main">
        <span className="link-row-head">
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer" className="link-row-title" title={l.title ? `${l.title}\n${l.url}` : l.url}>
              {label}
            </a>
          ) : <span className="link-row-title">{label}</span>}
          <span className={`chip link-kind is-${l.kind}`} title={l.kind === "social" ? "A social or creator platform" : "Any other site"}>
            {KIND_LABEL[l.kind] || l.kind}
          </span>
        </span>
        <span className="link-row-sub creator-sub">
          <span className="link-site">{l.site}</span>
          {l.title && <span className="link-url" title={l.url}>{l.url}</span>}
        </span>
        {l.notes && <span className="link-notes" title={l.notes}>{l.notes}</span>}
      </span>
      {showPerson && l.person && (
        <Link to={personPath(l.person.id)} className="chip person-chip link-person" title={`Person: ${l.person.name}`}>
          <Icon name="users" size={11} /><span className="chip-text">{l.person.name}</span>
        </Link>
      )}
      <span className="link-row-actions">
        {onAssign && !l.person && (
          <button type="button" className="btn-secondary link-assign" disabled={busy} aria-expanded={!!children}
                  onClick={() => onAssign(l)} aria-label={`Assign ${label} to a person`} title="Give it to a person">
            <Icon name="users" size={13} />Assign…
          </button>
        )}
        {onUp && (
          <button type="button" className="del-btn" disabled={busy || !onUp.ok} onClick={onUp.run}
                  title="Move up" aria-label={`Move ${label} up`}>
            <Icon name="arrowUp" size={14} />
          </button>
        )}
        {onDown && (
          <button type="button" className="del-btn" disabled={busy || !onDown.ok} onClick={onDown.run}
                  title="Move down" aria-label={`Move ${label} down`}>
            <Icon name="arrowDown" size={14} />
          </button>
        )}
        <button type="button" className="btn-ghost" disabled={busy} onClick={() => onEdit(l)} aria-label={`Edit ${label}`}>Edit</button>
        <button type="button" className="btn-ghost" disabled={busy} onClick={() => onDelete(l)} aria-label={`Delete ${label}`}>Delete</button>
      </span>
      {children}
    </li>
  );
}

/* Who to give links to (#165 B): PersonPicker with no "No person" (the
   links have none already), the recent people on top, read afresh each
   time it opens, so the one just used comes first. Focus starts in its
   search field; Escape cancels. A pick while ``busy`` is ignored.

   Props: people, label (the field's name), onPick(person), onCancel, busy */
export function AssignPicker({ people, label, onPick, onCancel, busy = false }) {
  const [recent, setRecent] = useState(null);
  const [active, setActive] = useState(null);
  useEffect(() => {
    let alive = true;
    getRecentLinkPeople(10).then(r => { if (alive) setRecent(r.people); }, () => { if (alive) setRecent([]); });
    return () => { alive = false; };
  }, []);
  return (
    <div className="link-assign-pick" aria-busy={busy || undefined}
         onKeyDown={e => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); onCancel(); } }}>
      <PersonPicker people={people} recent={recent} active={active} onActive={setActive} label={label} none={false} autoFocus
                    onPick={o => { if (!busy && o?.person) onPick(o.person); }} />
      <div className="link-assign-foot">
        <span className="quick-add-hint dim"><kbd>↑</kbd><kbd>↓</kbd> choose · <kbd>Enter</kbd> assign · <kbd>Esc</kbd> cancel</span>
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

/* AssignPicker in a dialog, for the selection's "Assign N to…": Escape or
   a click beside it closes it, Tab stays inside, focus goes back where it
   was.

   Props: count, people, onPick(person), onClose, busy, error */
export function AssignDialog({ count, people, onPick, onClose, busy = false, error = null }) {
  const boxRef = useRef(null);
  const titleId = useId();
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
  const what = plural(count, "link");
  return createPortal(
    <div className="modal-overlay"
         onKeyDown={e => {
           if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); onClose(); return; }
           trapTab(e, boxRef.current);
         }}
         onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={boxRef} className="modal assign-modal" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId} className="modal-title"><Icon name="users" size={16} />Assign {what} to…</h2>
        <AssignPicker people={people} label="Person" onPick={onPick} onCancel={onClose} busy={busy} />
        {error && <div className="msg err" role="alert">{error}</div>}
      </div>
    </div>,
    document.body,
  );
}
