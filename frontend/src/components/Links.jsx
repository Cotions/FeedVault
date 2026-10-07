import { useState } from "react";
import { Link } from "react-router-dom";
import { safeUrl } from "../lib/fmt";
import { personPath } from "../lib/people";
import { KIND_LABEL, MAX_NOTES, MAX_TITLE, MAX_URL } from "../lib/links";
import Icon from "./Icon";
import CreatorPicker from "./CreatorPicker";

/* A link's fields: URL, title, notes, and its person when ``people`` is
   given (the person page presets its own and leaves the picker out).
   onSubmit({ url, title, notes, person? }) resolves to an error message, or
   null when it went through (a new link's form then empties; the caller
   closes an edit). */
export function LinkForm({
  link = null, people = null, onSubmit, onCancel, busy = false, submitLabel = "Add link", idPrefix = "link",
}) {
  const [url,    setUrl]    = useState(link?.url || "");
  const [title,  setTitle]  = useState(link?.title || "");
  const [notes,  setNotes]  = useState(link?.notes || "");
  const [person, setPerson] = useState(link?.person?.id ?? null);
  const [error,  setError]  = useState(null);

  async function submit(e) {
    e.preventDefault();
    if (!url.trim()) return;
    setError(null);
    const body = { url: url.trim(), title, notes, ...(people ? { person } : {}) };
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
   Only an http(s) address is ever a link (safeUrl); anything else is text. */
export function LinkRow({ link: l, showPerson = true, busy = false, onEdit, onDelete, onUp, onDown, flash = false }) {
  const href = safeUrl(l.url);
  const label = l.title || l.url.replace(/^https?:\/\//, "");
  return (
    <li className={`link-row${flash ? " is-flash" : ""}`} data-link-id={l.id}>
      <span className="link-row-icon" aria-hidden="true"><Icon name="link" size={15} /></span>
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
    </li>
  );
}
