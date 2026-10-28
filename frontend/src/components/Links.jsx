import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { getRecentLinkPeople } from "../lib/api";
import { plural, safeUrl } from "../lib/fmt";
import { personPath } from "../lib/people";
import { KIND_LABEL, MAX_NOTES, MAX_TITLE, MAX_URL, linkLabel, linkName, namesText, withScheme } from "../lib/links";
import { focusLost, restoreFocus, trapTab } from "../lib/layout";
import Icon from "./Icon";
import CreatorPicker from "./CreatorPicker";
import DiscardDialog from "./DiscardDialog";
import PersonPicker from "./PersonPicker";

/* A link's fields: URL, title, notes, and its people when ``people`` is
   given (the person page presets its own and leaves the picker out): a
   chip for each, × takes one off, the picker adds another (a band's
   account is the band's and each member's, #215).
   onSubmit({ url, title, notes, people? }) resolves to an error message, or
   null when it went through (a new link's form then empties; the caller
   closes an edit). onDirty(bool) hears whether the fields differ from the
   link's (false again when the form goes).

   ``folded`` (a new link's form, a person's page): the form starts as a
   "+ Add link…" button and opens in its place, focus in Address. It stays
   open after an add, empty for the next one; Cancel or Escape closes it,
   asking first when something is typed (the question the page asks before
   leaving it). */
export function LinkForm({
  link = null, people = null, onSubmit, onCancel, onDirty, busy = false, submitLabel = "Add link", idPrefix = "link",
  folded = false,
}) {
  const [url,    setUrl]    = useState(link?.url || "");
  const [title,  setTitle]  = useState(link?.title || "");
  const [notes,  setNotes]  = useState(link?.notes || "");
  const had = (link?.people || []).map(p => p.id);
  const [chosen, setChosen] = useState(had);         // the people's ids, in the order picked
  const [error,  setError]  = useState(null);
  const [open,   setOpen]   = useState(!folded);
  const [asking, setAsking] = useState(false);        // Cancel with something typed: discard it?
  const urlRef = useRef(null);
  const openRef = useRef(null);
  const focusTo = useRef(null);                       // after the next render: "url" or "open"
  const dirty = url !== (link?.url || "") || title !== (link?.title || "") || notes !== (link?.notes || "")
    || (!!people && (chosen.length !== had.length || chosen.some(id => !had.includes(id))));

  useEffect(() => {
    onDirty?.(dirty);
    return () => onDirty?.(false);
  }, [dirty, onDirty]);

  useEffect(() => {
    const to = focusTo.current;
    focusTo.current = null;
    if (to === "url") urlRef.current?.focus();
    else if (to === "open") openRef.current?.focus();
  });

  function fold() {
    setUrl(""); setTitle(""); setNotes(""); setChosen([]); setError(null);
    focusTo.current = "open";
    setOpen(false);
  }
  const canCancel = folded || !!onCancel;
  function cancel() {
    if (!folded) onCancel();
    else if (dirty) setAsking(true);
    else fold();
  }

  async function submit(e) {
    e.preventDefault();
    if (!url.trim()) return;
    setError(null);
    // "example.org/x" goes as https://example.org/x, and the field shows what went.
    const sent = withScheme(url);
    if (sent !== url) setUrl(sent);
    const body = { url: sent, title, notes, ...(people ? { people: chosen } : {}) };
    const problem = await onSubmit(body);
    if (problem) { setError(problem); return; }
    if (!link) { setUrl(""); setTitle(""); setNotes(""); setChosen([]); }
    if (folded) focusTo.current = "url";              // the next one
  }

  if (!open) {
    return (
      <button ref={openRef} type="button" className="btn-secondary link-form-open"
              onClick={() => { focusTo.current = "url"; setOpen(true); }}>
        <Icon name="plus" size={14} />{submitLabel}…
      </button>
    );
  }

  return (
    <>
    <form className={`link-form${link ? " is-edit" : ""}`} onSubmit={submit}
          onKeyDown={e => { if (e.key === "Escape" && canCancel && !e.nativeEvent.isComposing) { e.stopPropagation(); cancel(); } }}>
      <label className="filter link-form-url">
        <span>Address</span>
        <input type="text" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://…"
               id={`${idPrefix}-url`} maxLength={MAX_URL} value={url} autoFocus={!!link} ref={urlRef}
               onChange={e => { setUrl(e.target.value); setError(null); }} />
      </label>
      <label className="filter link-form-title">
        <span>Title</span>
        <input type="text" className="link-input-text" placeholder="Optional" maxLength={MAX_TITLE} value={title}
               onChange={e => setTitle(e.target.value)} />
      </label>
      {people && (
        <div className="filter link-form-person">
          <span>{chosen.length > 1 ? "People" : "Person"}</span>
          {chosen.length > 0 && (
            <ul className="tag-chips link-form-people" aria-label="Its people">
              {chosen.map(id => {
                const name = people.find(p => p.id === id)?.name ?? `person ${id}`;
                return (
                  <li key={id} className="tag-chip label-chip">
                    <span className="tag-chip-name" title={name}>{name}</span>
                    <button type="button" className="tag-chip-x" onClick={() => setChosen(c => c.filter(x => x !== id))}
                            title={`Not ${name}'s`} aria-label={`Remove ${name}`}>
                      <Icon name="close" size={10} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <CreatorPicker
            people={people.filter(p => !chosen.includes(p.id))}
            accounts={[]}
            value={null}
            label={chosen.length ? "Add a person" : "Person"}
            allLabel={chosen.length ? "Add another…" : "No one"}
            placeholder="Search people…"
            onChange={v => { if (v?.person) setChosen(c => [...c, v.person.id]); }}
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
        {canCancel && <button type="button" className="btn-ghost" onClick={cancel}>Cancel</button>}
      </div>
      {error && <div className="msg err link-form-msg" role="alert">{error}</div>}
    </form>
    {/* Outside the form: its Escape does not reach the form's. */}
    {folded && (
      <DiscardDialog open={asking} onDiscard={() => { setAsking(false); fold(); }} onKeep={() => setAsking(false)}>
        <p>The new link is not saved.</p>
      </DiscardDialog>
    )}
    </>
  );
}

/* One link: its title (else its site's label, else its address) opening
   the site in a new tab, the address under it when the label is not it, the
   site and kind, its people (``showPerson``; on a person's page ``here``,
   their id, leaves them out: the others it is shared with), notes, and the
   row's actions; ``onRemove`` (a person's page, a shared link) takes it
   from them only.
   Only an http(s) address is ever a link (safeUrl); anything else is text.
   On the Links page: ``select`` ({ checked, onClick(event) }) puts a
   checkbox first (shift-click for a range), ``onAssign`` an "Assign…" on a
   link of no one, and ``children`` (the open assign picker) go under it.
   Without onEdit and onDelete (a search's results) it is read only. */
export function LinkRow({
  link: l, showPerson = true, here = null, busy = false, onEdit, onDelete, onRemove, onUp, onDown, flash = false, index, select,
  onAssign, children,
}) {
  const people = l.people || [];
  const others = people.filter(p => p.id !== here);
  const href = safeUrl(l.url);
  const shown = linkLabel(l);
  // The buttons' names: the title or the address, one per link (two links of
  // a site share its label).
  const label = linkName(l);
  const named = !!(l.title || l.label);           // shown is not the address: the address goes under it
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
            <a href={href} target="_blank" rel="noopener noreferrer" className="link-row-title" title={named ? `${shown}\n${l.url}` : l.url}>
              {shown}
            </a>
          ) : <span className="link-row-title">{shown}</span>}
          <span className={`chip link-kind is-${l.kind}`} title={l.kind === "social" ? "A social or creator platform" : "Any other site"}>
            {KIND_LABEL[l.kind] || l.kind}
          </span>
        </span>
        <span className="link-row-sub creator-sub">
          <span className="link-site">{l.site}</span>
          {named && <span className="link-url" title={l.url}>{l.url}</span>}
        </span>
        {l.notes && <span className="link-notes" title={l.notes}>{l.notes}</span>}
      </span>
      {showPerson && others.length > 0 && (
        <span className="link-people" aria-label={here != null ? "Also theirs" : "Its people"}>
          {others.map(p => (
            <Link key={p.id} to={personPath(p.id)} className="chip person-chip link-person"
                  title={here != null ? `Also ${p.name}'s` : `Person: ${p.name}`}>
              <Icon name="users" size={11} /><span className="chip-text">{p.name}</span>
            </Link>
          ))}
        </span>
      )}
      <span className="link-row-actions">
        {onAssign && people.length === 0 && (
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
        {onEdit && <button type="button" className="btn-ghost" disabled={busy} onClick={() => onEdit(l)} aria-label={`Edit ${label}`}>Edit</button>}
        {onRemove && others.length > 0 && (
          <button type="button" className="btn-ghost" disabled={busy} onClick={() => onRemove(l)} aria-label={`Remove ${label} from them`}
                  title={`Not theirs any more: it stays ${namesText(others.map(p => p.name))}'s`}>
            Remove
          </button>
        )}
        {onDelete && <button type="button" className="btn-ghost" disabled={busy} onClick={() => onDelete(l)} aria-label={`Delete ${label}`}>Delete</button>}
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
         onKeyDown={e => { if (e.key === "Escape" && !e.nativeEvent.isComposing) { e.stopPropagation(); e.preventDefault(); onCancel(); } }}>
      <PersonPicker people={people} recent={recent} active={active} onActive={setActive} label={label} none={false} autoFocus
                    onPick={o => { if (!busy && o?.person) onPick(o.person); }} />
      <div className="link-assign-foot">
        <span className="quick-add-hint dim"><kbd>↑</kbd><kbd>↓</kbd> choose · <kbd>Enter</kbd> assign · <kbd>Esc</kbd> cancel</span>
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

/* AssignPicker in a dialog, for the selection's "Assign N to…" (and,
   ``also``, its "Also link N to…": the person is added, the others kept):
   Escape or a click beside it closes it, Tab stays inside, focus goes back
   where it was.

   Props: count, people, onPick(person), onClose, busy, error, also */
export function AssignDialog({ count, people, onPick, onClose, busy = false, error = null, also = false }) {
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
           if (e.key === "Escape" && !e.nativeEvent.isComposing) { e.stopPropagation(); e.preventDefault(); onClose(); return; }
           trapTab(e, boxRef.current);
         }}
         onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={boxRef} className="modal assign-modal" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId} className="modal-title"><Icon name="users" size={16} />{also ? `Also link ${what} to…` : `Assign ${what} to…`}</h2>
        <AssignPicker people={people} label="Person" onPick={onPick} onCancel={onClose} busy={busy} />
        {error && <div className="msg err" role="alert">{error}</div>}
      </div>
    </div>,
    document.body,
  );
}
