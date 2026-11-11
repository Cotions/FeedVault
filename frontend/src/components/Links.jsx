import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { getRecentLinkPeople, openLink } from "../lib/api";
import { plural, safeUrl } from "../lib/fmt";
import { personPath } from "../lib/people";
import {
  KIND_LABEL, MAX_NOTES, MAX_TITLE, MAX_URL, copyUrl, downloadState, linkLabel, linkName, namesText, shortUrl, withScheme,
} from "../lib/links";
import { focusLost, restoreFocus, trapTab } from "../lib/layout";
import { choiceLabel, isPlainClick, openChoices, sameChoice } from "../lib/openIn";
import { useOpenLinks } from "../lib/openLinks";
import { useToast } from "../lib/toast";
import ActionMenu from "./ActionMenu";
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
   leaving it).

   ``download`` (a new link's form; true or false, what it starts as): a
   "Download" checkbox, and the link goes as { download: true } when it is
   ticked: a link to download with another app, kept apart from the
   person's other links. It stays as set from one add to the next (several
   downloads in a row) and is not an edit: nothing is asked about it. */
export function LinkForm({
  link = null, people = null, onSubmit, onCancel, onDirty, busy = false, submitLabel = "Add link", idPrefix = "link",
  folded = false, download = null,
}) {
  const [url,    setUrl]    = useState(link?.url || "");
  const [title,  setTitle]  = useState(link?.title || "");
  const [notes,  setNotes]  = useState(link?.notes || "");
  const had = (link?.people || []).map(p => p.id);
  const [chosen, setChosen] = useState(had);         // the people's ids, in the order picked
  const [toGet,  setToGet]  = useState(!!download);  // the Download checkbox
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
    setUrl(""); setTitle(""); setNotes(""); setChosen([]); setError(null); setToGet(!!download);
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
    const body = { url: sent, title, notes, ...(people ? { people: chosen } : {}), ...(download != null && toGet ? { download: true } : {}) };
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
        {download != null && <DownloadCheck checked={toGet} onChange={setToGet} />}
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

/* The "Download" checkbox of every form that adds links (a link's form,
   the quick-add, Import many): ticked, what is added is a download, to do.
   FeedVault never downloads it: another app does. */
export function DownloadCheck({ checked, onChange, disabled = false }) {
  return (
    <label className="link-dl-check" title="A link to download with another app: it goes to Downloads, to do. FeedVault never downloads it.">
      <input type="checkbox" checked={checked} disabled={disabled} aria-label="Download" onChange={e => onChange(e.target.checked)} />
      <span>Download</span>
      <span className="link-dl-check-hint dim">to download with another app</span>
    </label>
  );
}

/* A row's Copy (a link, a profile on a person's page): its address on the
   clipboard, and a toast that says so, or why not (lib/links.js copyUrl).
   ``label`` names the row in the button's name. */
export function CopyAddress({ url, label }) {
  const toast = useToast();
  return (
    <button type="button" className="del-btn link-copy" onClick={() => copyUrl({ url }, toast)}
            aria-label={`Copy the address of ${label}`} title="Copy the address">
      <Icon name="copy" size={14} />
    </button>
  );
}

/* One link, a card of its list's grid (several to a line). Its first line:
   its title (else its site's label, else its address) opening the site in
   a new tab, and its buttons at the right. Under it, each line only when
   it has something: the address when the title is not it (so the site is
   read once, #236), short (lib/links.js shortUrl; the whole one is its
   ``title``, and what Copy copies), its notes (two lines at most), and a
   foot with its kind, its download mark and its people (``showPerson``;
   on a person's page ``here``, their id, leaves them out: the others it
   is shared with). Only an http(s) address is ever a link (safeUrl);
   anything else is text.

   The buttons, always in sight: Copy (its address on the clipboard, a
   toast says so), at most one button, and one "⋯" menu with everything
   else.
   The one button: ``assign`` (the Links page's Unsorted) puts "Assign…"
   on a link of no one; otherwise a download link gets its done toggle
   (``onDone``): a check that marks it downloaded, lit once it is (a click
   then takes that back).
   The menu, each entry only where it applies: Open (a new tab here), the
   other browsers and profiles ("Open in", below), Edit (``onEdit``),
   Assign… (``onAssign``, a link of no one) or Also link to… (``onAlso``,
   one that has people), Move to Downloads or Not a download
   (``onDownload(link, on)``), Mark downloaded or Mark to download
   (``onDone(link, on)``), Move up and Move down (``onUp``, ``onDown``:
   { ok, run }, a person's page), Remove from this creator (``onRemove``,
   a shared link, taken from them only) and Delete (``onDelete``).
   Without any of them (a search's results) the row is read only: Copy,
   and the menu only when there is another browser to open it in.

   A download is its own category (lib/links.js downloadState), never a
   social or other link: it shows no kind (``showKind``: nor does a
   person's page, whose groups say it). Downloaded, the row is dimmed and
   says "Downloaded"; still to do, it says "To download" only where
   downloads are mixed with other links (``mixed``: Unsorted, a search).
   FeedVault never downloads it: the states are the user's notes for
   another app.

   On the Links page: ``select`` ({ checked, onClick(event) }, in select
   mode) puts a checkbox first (shift-click for a range), and ``children``
   (the open assign picker) go at the card's foot.

   Where it opens (docs/API.md "Opening a link elsewhere"): with a default
   set in Settings (Open links in), a plain left click on the title (Enter
   too) asks FeedVault to start that browser on it (POST /api/open, the
   saved default) in place of following it; a middle click, Ctrl/Cmd- or
   Shift-click stay the browser's own (a tab or a window here). The menu's
   "Open in" group (when another browser is installed) offers each
   browser × profile, normal or private, for this once. A refusal (the
   browser gone, a profile renamed) is a toast with "Open here". */
export function LinkRow({
  link: l, showPerson = true, showKind = true, mixed = false, here = null, busy = false, flash = false, index, select,
  assign = false, onAssign, onAlso, onDownload, onDone, onEdit, onDelete, onRemove, onUp, onDown, children,
}) {
  const people = l.people || [];
  const others = people.filter(p => p.id !== here);
  const href = safeUrl(l.url);
  const shown = linkLabel(l);
  // The buttons' names: the title or the address, one per link (two links of
  // a site share its label).
  const label = linkName(l);
  const named = !!(l.title || l.label);           // shown is not the address: the address goes under it
  const state = downloadState(l);
  const { browsers, default: dflt } = useOpenLinks();
  const toast = useToast();

  // choice: { browser, profile, private }, or null for the saved default.
  async function openElsewhere(choice) {
    let r;
    try {
      r = await openLink({ url: l.url, ...(choice || {}) });
    } catch (e) {
      r = { ok: false, error: e.message };
    }
    if (r?.ok) return;
    toast(r?.error || "Could not open the link.", "err",
          { label: "Open here", onClick: () => { window.open(href, "_blank", "noopener,noreferrer"); } });
  }
  function onTitleClick(e) {
    if (!dflt || !isPlainClick(e)) return;
    e.preventDefault();
    openElsewhere(null);
  }
  const mark = c => (sameChoice(c, dflt) ? " (default)" : "");
  const openIn = href && browsers?.length ? [
    { heading: "Open in" },
    ...openChoices(browsers).map(c => ({
      label: `${choiceLabel(c, browsers)}${mark(c)}`, onSelect: () => openElsewhere(c),
      title: c.private ? "In a private window" : undefined,
    })),
    { label: "Set the default…", to: "/settings#open-links", icon: "settings" },
  ] : [];
  const assignHere = assign && !!onAssign && people.length === 0;
  const doneHere = !assignHere && !!onDone && state !== "none";
  // The card's foot, there only when one of these is.
  const kindHere = showKind && state === "none";
  const todoHere = state === "todo" && mixed;
  const peopleHere = showPerson && others.length > 0;
  const foot = kindHere || state === "done" || todoHere || peopleHere;
  const acts = [
    onEdit && { label: "Edit", icon: "pencil", disabled: busy, onSelect: () => onEdit(l) },
    onAssign && people.length === 0 && { label: "Assign…", icon: "users", disabled: busy, onSelect: () => onAssign(l), title: "Give it to a person" },
    onAlso && people.length > 0 && {
      label: "Also link to…", icon: "userPlus", disabled: busy, onSelect: () => onAlso(l),
      title: "To one more person: a shared account, theirs and their other people's",
    },
    onDownload && (state === "none"
      ? { label: "Move to Downloads", icon: "download", disabled: busy, onSelect: () => onDownload(l, true),
          title: "A link to download with another app: FeedVault only keeps the note" }
      : { label: "Not a download", icon: "link", disabled: busy, onSelect: () => onDownload(l, false),
          title: "Back among the other links" }),
    onDone && state === "todo" && { label: "Mark downloaded", icon: "check", disabled: busy, onSelect: () => onDone(l, true) },
    onDone && state === "done" && { label: "Mark to download", icon: "undo", disabled: busy, onSelect: () => onDone(l, false) },
    "separator",
    onUp && { label: "Move up", icon: "arrowUp", disabled: busy || !onUp.ok, onSelect: onUp.run },
    onDown && { label: "Move down", icon: "arrowDown", disabled: busy || !onDown.ok, onSelect: onDown.run },
    "separator",
    onRemove && others.length > 0 && {
      label: "Remove from this creator", icon: "close", disabled: busy, onSelect: () => onRemove(l),
      title: `Not theirs any more: it stays ${namesText(others.map(p => p.name))}'s`,
    },
    onDelete && { label: "Delete", icon: "trash", danger: true, disabled: busy, onSelect: () => onDelete(l) },
  ];
  // Read only and no other browser: Open alone would say what the title does.
  const menu = openIn.length || acts.some(a => a && a !== "separator") ? [
    href && { label: "Open", href, icon: "external", title: "A new tab here" },
    "separator",
    ...openIn,
    "separator",
    ...acts,
  ] : null;
  return (
    <li className={`link-row${flash ? " is-flash" : ""}${select?.checked ? " is-selected" : ""}${children ? " is-assigning" : ""}${state === "done" ? " is-downloaded" : ""}`}
        data-link-id={l.id} data-index={index}>
      {select ? (
        <label className="link-check" title="Select (shift-click: a range)">
          <input type="checkbox" checked={select.checked} onChange={() => {}} onClick={select.onClick}
                 aria-label={`Select ${label}`} />
        </label>
      ) : <span className="link-row-icon" aria-hidden="true"><Icon name={state === "none" ? "link" : "download"} size={15} /></span>}
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="link-row-title" onClick={onTitleClick}
           title={`${named ? `${shown}\n${l.url}` : l.url}${dflt ? `\nOpens in ${choiceLabel(dflt, browsers)} (Ctrl-click: a tab here)` : ""}`}>
          {shown}
        </a>
      ) : <span className="link-row-title" title={named ? `${shown}\n${l.url}` : l.url}>{shown}</span>}
      <span className="link-row-actions">
        <CopyAddress url={l.url} label={label} />
        {assignHere && (
          <button type="button" className="btn-secondary link-assign" disabled={busy} aria-expanded={!!children}
                  onClick={() => onAssign(l)} aria-label={`Assign ${label} to a person`} title="Give it to a person">
            <Icon name="users" size={13} />Assign…
          </button>
        )}
        {doneHere && (
          <button type="button" className={`del-btn link-done${state === "done" ? " is-on" : ""}`} disabled={busy}
                  aria-pressed={state === "done"} onClick={() => onDone(l, state !== "done")}
                  aria-label={state === "done" ? `Mark ${label} to download` : `Mark ${label} downloaded`}
                  title={state === "done" ? "Downloaded — undo" : "Mark downloaded"}>
            <Icon name="check" size={14} />
          </button>
        )}
        {menu && <ActionMenu className="link-menu" label={`More for ${label}`} items={menu} />}
      </span>
      {named && <span className="link-url creator-sub" title={l.url}>{shortUrl(l.url)}</span>}
      {l.notes && <span className="link-notes" title={l.notes}>{l.notes}</span>}
      {foot && (
        <span className="link-row-foot">
          {kindHere && (
            <span className={`chip link-kind is-${l.kind}`} title={l.kind === "social" ? "A social or creator platform" : "Any other site"}>
              {KIND_LABEL[l.kind] || l.kind}
            </span>
          )}
          {state === "done" && (
            <span className="chip link-dl-mark is-done" title="Downloaded, with another app">
              <Icon name="check" size={11} />Downloaded
            </span>
          )}
          {todoHere && (
            <span className="chip link-dl-mark" title="To download, with another app: FeedVault never downloads it">
              <Icon name="download" size={11} />To download
            </span>
          )}
          {peopleHere && (
            <span className="link-people" aria-label={here != null ? "Also theirs" : "Its people"}>
              {others.map(p => (
                <Link key={p.id} to={personPath(p.id)} className="chip person-chip link-person"
                      title={here != null ? `Also ${p.name}'s` : `Person: ${p.name}`}>
                  <Icon name="users" size={11} /><span className="chip-text">{p.name}</span>
                </Link>
              ))}
            </span>
          )}
        </span>
      )}
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
        {/* A card is too narrow for all of it: there, Cancel beside it says the last part. */}
        <span className="quick-add-hint dim">
          <kbd>↑</kbd><kbd>↓</kbd> choose · <kbd>Enter</kbd> assign<span className="link-assign-esc"> · <kbd>Esc</kbd> cancel</span>
        </span>
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
