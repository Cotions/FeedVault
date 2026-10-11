import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getLinks, getPeople, createLink, updateLink, deleteLink, assignLinks, deleteLinks } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useJobs } from "../lib/jobs";
import { useToast } from "../lib/toast";
import { fmtInt, plural } from "../lib/fmt";
import { personPath } from "../lib/people";
import { restoreFocus } from "../lib/layout";
import { useSelection } from "../lib/useSelection";
import ConfirmDialog from "../components/ConfirmDialog";
import DiscardDialog from "../components/DiscardDialog";
import CreatorPicker from "../components/CreatorPicker";
import PageHeader from "../components/PageHeader";
import SelectionBar from "../components/SelectionBar";
import Icon from "../components/Icon";
import Bookmarklet from "../components/Bookmarklet";
import { AssignDialog, AssignPicker, LinkForm, LinkRow } from "../components/Links";
import { KIND_LABEL, copyUrls, linkLabel } from "../lib/links";
import { useOneEdit, useUnsaved } from "../lib/unsaved";
import { useWindow } from "../lib/windowing";
import { Gap } from "../components/WinGap";

/* Every saved link: a creator's Linktree, Patreon or site, an interview, an
   article, tied to a person or to no one. Add at the top; filter by kind
   (social or other, read from the address), site and person, or search the
   address, title and notes. Nothing is fetched: a link is its text, opened
   in a new tab when followed. The filters live in the address (?kind=,
   ?site=, ?person= an id or "none", ?q=), so a reload or Back keeps them.

   The Unsorted tab (?person=none, #165 B) is the queue of links tied to no
   one: each row's Assign… opens the person picker in place, and a pick
   sends the link to that person (it leaves the queue, with an Undo). Any
   row can be checked (shift-click: a range) for the selection bar: assign
   them all, copy their addresses, or delete them. */
export default function Links() {
  const { refreshKey } = useScan();
  const { unsortedLinks, refresh } = useJobs();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const kind   = ["social", "other"].includes(params.get("kind")) ? params.get("kind") : "";
  const site   = params.get("site") || "";
  const rawPerson = params.get("person") || "";
  const person = rawPerson === "none" ? "none" : /^\d+$/.test(rawPerson) ? Number(rawPerson) : null;   // null: anyone
  const q      = (params.get("q") || "").trim();      // the search, once typing pauses
  const unsortedView = person === "none";
  const [query,    setQuery]    = useState(q);        // the search box, as typed
  const [removing, setRemoving] = useState(null);     // the link to delete
  const [flash,    setFlash]    = useState(null);     // the id of a link to point at (a URL saved already)
  const [busy,     setBusy]     = useState(false);
  const [dlgError, setDlgError] = useState(null);
  const [assigning, setAssigning] = useState(null);   // the id of the link whose picker is open
  const [bulk,      setBulk]      = useState(null);   // "assign" or "delete": the selection's dialog
  const edit = useOneEdit();                          // the link being edited, and whether it has changes
  const [adding, setAdding] = useState(false);        // the new link's form has something typed
  // A filter change may hide the link being edited, so with unsaved edits it
  // asks first, as leaving the page does. Clearing every filter cannot hide
  // it: that goes through. No filter touches the new link's form.
  const unsaved = useUnsaved(edit.dirty || adding,
    (from, to) => from.pathname === to.pathname && (!to.search || !edit.dirty));

  // The filters in the address, replacing the entry: Back leaves the page.
  const setFilters = useCallback(changes => setParams(prev => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(changes)) {
      if (v == null || v === "") next.delete(k); else next.set(k, String(v));
    }
    return next;
  }, { replace: true }), [setParams]);

  // The box follows the address when that changes otherwise (Back, Clear);
  // ``written`` is the last search it sent, so its own does not echo back.
  const written = useRef(q);
  useEffect(() => {
    if (q !== written.current) { written.current = q; setQuery(q); }
  }, [q]);
  useEffect(() => {
    const t = setTimeout(() => {
      if (query.trim() === written.current) return;
      written.current = query.trim();
      setFilters({ q: query.trim() });
    }, 250);
    return () => clearTimeout(t);
  }, [query, setFilters]);
  function clearFilters() {
    written.current = "";
    setQuery("");
    setParams(new URLSearchParams(), { replace: true });
  }
  // Kept editing instead of searching: the box shows the search in force.
  function keepEditing() {
    unsaved.keep();
    written.current = q;
    setQuery(q);
  }

  const load = useCallback(() => getLinks({ kind, site, person, q }), [kind, site, person, q]);
  const { data, error, loading, reload } = useApi(load, refreshKey);
  const { data: people } = useApi(getPeople, refreshKey);

  // Links just given to someone from Unsorted leave it at once, before the
  // list is read again (adjusting state during render, not in an effect).
  const [gone, setGone] = useState(() => new Set());
  const [goneOf, setGoneOf] = useState(data);
  if (goneOf !== data) {
    setGoneOf(data);
    if (gone.size) setGone(new Set());
  }
  const links = useMemo(() => (data?.links || []).filter(l => !(unsortedView && gone.has(l.id))), [data, gone, unsortedView]);
  // Only the rows near the viewport are rendered once there are many
  // (lib/windowing.js); the one being edited always is: its form holds the
  // unsaved edits. So is the one being assigned. The browser's find bar
  // sees the rendered rows only: the search box searches them all.
  const editingAt = edit.editing == null ? -1 : links.findIndex(l => l.id === edit.editing);
  const assigningAt = assigning == null ? -1 : links.findIndex(l => l.id === assigning);
  const { listRef, focusProps, render, scrollTo } = useWindow(links.length, { estimate: 66, pins: [editingAt, assigningAt] });

  // A new filter shows other links: a selection of links out of sight is dropped.
  const sel = useSelection(links, {
    resetKey: `${kind}|${site}|${person}|${q}`,
    escapeBlocked: !!removing || !!bulk || assigning != null || edit.editing != null,
  });

  // The link a 409 named: in view, and lit for a moment.
  useEffect(() => {
    if (flash == null || !data) return undefined;
    const at = links.findIndex(l => l.id === flash);
    if (at >= 0) scrollTo(at);
    const t = setTimeout(() => setFlash(null), 2400);
    return () => clearTimeout(t);
  }, [flash, data, links, scrollTo]);

  // After an assign, focus goes on: to the next link's Assign… in
  // Unsorted (the one that took the place of the link that left), else to
  // the link itself. After the render that shows the change.
  const refocus = useRef(null);
  const tabsRef = useRef(null);
  useEffect(() => {
    const f = refocus.current, list = listRef.current;
    if (!f) return;
    if (f.unsorted ? links.some(l => l.id === f.id) : links.find(l => l.id === f.id)?.person == null) return;
    refocus.current = null;
    const at = sel => list?.querySelector(sel);
    restoreFocus(f.unsorted
      ? at(`[data-index="${f.index}"] .link-assign`) || at(`[data-index="${f.index - 1}"] .link-assign`)
        || tabsRef.current?.querySelector("[aria-pressed=true]")
      : at(`[data-link-id="${f.id}"] .link-row-title`));
  }, [links, listRef]);

  // Every filter off, so the link is listed, lit and scrolled to.
  function showTaken(id) {
    if (params.toString()) clearFilters();
    setFlash(id);
  }

  // A change to the links: read them again, and the nav's Unsorted count.
  function changed() {
    reload();
    refresh();
  }

  async function add(body) {
    setBusy(true);
    try {
      const r = await createLink(body);
      if (!r?.ok) {
        if (r?.id != null) { showTaken(r.id); return "That link is saved already: it is lit in the list below."; }
        return r?.error || "Could not add the link.";
      }
      toast("Link added.");
      changed();
      return null;
    } catch (err) {
      return err.message;
    } finally {
      setBusy(false);
    }
  }

  async function save(id, body) {
    setBusy(true);
    try {
      const r = await updateLink(id, body);
      if (!r?.ok) {
        // The form stays open, with the edits and this message in view.
        if (r?.id != null) { showTaken(r.id); return "That address is saved already, as another link: it is lit in the list."; }
        return r?.error || "Could not save the link.";
      }
      edit.close();
      changed();
      return null;
    } catch (err) {
      return err.message;
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setDlgError(null);
    try {
      const r = await deleteLink(removing.id);
      if (!r?.ok) { setDlgError(r?.error || "Could not delete."); return; }
      toast("Link deleted.");
      sel.drop([removing.id]);
      setRemoving(null);
      changed();
    } catch (err) {
      setDlgError(err.message);
    } finally {
      setBusy(false);
    }
  }

  // The toast's Undo: each link back to the person it had before (or to no
  // one), but only if it is still with ``now``, the one just given; a link
  // moved or deleted since stays as it is.
  async function undoAssign(before, now) {
    const groups = new Map();
    for (const [id, prev] of before) groups.set(prev, [...(groups.get(prev) || []), id]);
    let skipped = 0;
    try {
      for (const [prev, ids] of groups) {
        const r = await assignLinks(ids, prev, now);
        if (!r?.ok) { toast(r?.error || "Could not undo.", "err"); break; }
        skipped += r.skipped.length;
      }
      if (skipped) toast(`${plural(skipped, "link")} changed since: left as ${skipped === 1 ? "it is" : "they are"}.`, "ok");
    } catch (err) {
      toast(err.message, "err");
    }
    changed();
  }

  // A link of no one, given to ``p`` from its row's picker.
  async function assignOne(l, p) {
    if (busy) return;
    setBusy(true);
    try {
      const r = await updateLink(l.id, { person: p.id });
      if (!r?.ok) { toast(r?.error || "Could not assign the link.", "err"); return; }
      refocus.current = { id: l.id, index: links.findIndex(x => x.id === l.id), unsorted: unsortedView };
      setAssigning(null);
      if (unsortedView) setGone(g => new Set(g).add(l.id));
      sel.drop([l.id]);
      toast(`${linkLabel(l)}: assigned to ${p.name}`, "ok", { label: "Undo", onClick: () => undoAssign([[l.id, null]], p.id) });
      changed();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  // The selection, given to ``p`` in one call (all or none).
  async function assignSelected(p) {
    if (busy) return;
    const chosen = sel.selectedItems;
    const ids = chosen.map(l => l.id);
    setBusy(true);
    setDlgError(null);
    try {
      const r = await assignLinks(ids, p.id);
      if (!r?.ok) {
        if (r?.missing) { setDlgError("Some of these links were deleted meanwhile: the list is read again. Nothing was assigned."); reload(); }
        else setDlgError(r?.error || "Could not assign the links.");
        return;
      }
      setBulk(null);
      sel.exit();
      if (unsortedView) setGone(g => new Set([...g, ...ids]));
      // Undo gives each back to whom it had (no one or someone else).
      const before = chosen.filter(l => l.person?.id !== p.id).map(l => [l.id, l.person?.id ?? null]);
      toast(`${plural(ids.length, "link")} assigned to ${p.name}`, "ok",
        before.length ? { label: "Undo", onClick: () => undoAssign(before, p.id) } : { to: personPath(p.id), label: "Show" });
      changed();
    } catch (err) {
      setDlgError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelected() {
    const ids = sel.selectedItems.map(l => l.id);
    setBusy(true);
    setDlgError(null);
    try {
      const r = await deleteLinks(ids);
      if (!r?.ok) { setDlgError(r?.error || "Could not delete."); return; }
      toast(`${plural(r.deleted, "link")} deleted.`);
      setBulk(null);
      sel.exit();
      changed();
    } catch (err) {
      setDlgError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const sites = data?.sites || [];
  const total = sites.reduce((n, s) => n + s.count, 0);
  const filtered = !!(kind || site || person != null || q);
  const narrowed = !!(kind || site || typeof person === "number" || q);   // more than the tab
  const count = sel.count;
  return (
    <>
      <PageHeader
        title="Links"
        sub={!data ? "…" : filtered ? `${fmtInt(links.length)} of ${fmtInt(total)}` : fmtInt(total)}
        actions={
          <input
            type="text"
            className="page-filter"
            placeholder="Search links…"
            aria-label="Search links"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        }
      />
      <div className="card links-add">
        <h3 className="card-title">Add a link</h3>
        <p className="page-lede">
          A creator's Linktree, Patreon, site or Discord invite, an interview, an article: anything with an
          address. FeedVault keeps the text only; it never opens the link itself.
        </p>
        <LinkForm people={people || []} busy={busy} onSubmit={add} idPrefix="links-add" onDirty={setAdding} />
        <Bookmarklet />
      </div>
      <div className="card">
        <div className="links-tabs" role="group" aria-label="Show" ref={tabsRef}>
          <button type="button" className={`links-tab${person == null ? " is-on" : ""}`} aria-pressed={person == null}
                  onClick={() => setFilters({ person: null })}>
            All links
          </button>
          <button type="button" className={`links-tab${unsortedView ? " is-on" : ""}`} aria-pressed={unsortedView}
                  onClick={() => setFilters({ person: "none" })} title="The links tied to no one yet, to give to a person">
            Unsorted
            {unsortedLinks > 0 && <span className="links-tab-count">{fmtInt(unsortedLinks)}</span>}
          </button>
        </div>
        <div className="feed-filters links-filters" role="group" aria-label="Filters">
          <label className="filter">
            <span>Kind</span>
            <select className="sort-select" value={kind} onChange={e => setFilters({ kind: e.target.value })}>
              <option value="">All</option>
              <option value="social">{KIND_LABEL.social}</option>
              <option value="other">{KIND_LABEL.other}</option>
            </select>
          </label>
          <label className="filter">
            <span>Site</span>
            <select className="sort-select" value={site} onChange={e => setFilters({ site: e.target.value })}>
              <option value="">All sites</option>
              {site && !sites.some(s => s.site === site) && <option value={site}>{site}</option>}
              {sites.map(s => <option key={s.site} value={s.site}>{s.site} ({s.count})</option>)}
            </select>
          </label>
          <div className="filter filter-author">
            <span>Person</span>
            <CreatorPicker
              people={people || []}
              accounts={[]}
              value={typeof person === "number" ? { person } : null}
              label="Person"
              allLabel={unsortedView ? "No person" : "Anyone"}
              placeholder="Search people…"
              onChange={v => setFilters({ person: v?.person ? v.person.id : null })}
            />
          </div>
          {filtered && (
            <button type="button" className="btn-ghost filter-clear"
                    onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>
        {error && !data ? (
          <div className="empty">Could not load links: {error.message}</div>
        ) : loading && !data ? (
          <div className="empty">Loading…</div>
        ) : links.length === 0 ? (
          <div className="empty">
            {unsortedView && !narrowed ? "Nothing to sort: every link has its person."
              : filtered ? "No link matches." : "No links yet. Add one above."}
          </div>
        ) : (
          <ul className="link-list" ref={listRef} {...focusProps}>
            {render(i => {
              const l = links[i];
              return edit.editing === l.id ? (
                <li key={l.id} className="link-row is-editing" data-index={i}>
                  <LinkForm link={l} people={people || []} busy={busy} submitLabel="Save"
                            idPrefix={`link-${l.id}`} onSubmit={body => save(l.id, body)} onCancel={edit.close}
                            onDirty={edit.onDirty} />
                </li>
              ) : (
                <LinkRow key={l.id} index={i} link={l} busy={busy} flash={flash === l.id}
                         select={{ checked: sel.isSelected(l.id), onClick: e => { sel.enter(); sel.toggle(i, e.shiftKey); } }}
                         onAssign={x => setAssigning(a => (a === x.id ? null : x.id))}
                         onEdit={x => { setAssigning(null); edit.open(x.id); }}
                         onDelete={x => { setDlgError(null); setRemoving(x); }}>
                  {assigning === l.id && (
                    <AssignPicker people={people} label={`Assign ${linkLabel(l)} to`} busy={busy}
                                  onPick={p => assignOne(l, p)}
                                  onCancel={() => {
                                    setAssigning(null);
                                    restoreFocus(listRef.current?.querySelector(`[data-link-id="${l.id}"] .link-assign`));
                                  }} />
                  )}
                </LinkRow>
              );
            }, (height, key) => <Gap key={key} as="li" height={height} />)}
          </ul>
        )}
        {count > 0 && (
          <SelectionBar selection={sel} loaded={links.length}>
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => copyUrls(sel.selectedItems, toast)}>
              <Icon name="copy" size={14} />Copy URLs
            </button>
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => { setDlgError(null); setBulk("assign"); }}>
              <Icon name="users" size={14} />Assign {fmtInt(count)} to…
            </button>
            <button type="button" className="btn-danger" disabled={busy} onClick={() => { setDlgError(null); setBulk("delete"); }}>
              <Icon name="trash" size={14} />Delete {fmtInt(count)}…
            </button>
          </SelectionBar>
        )}

        {bulk === "assign" && (
          <AssignDialog count={count} people={people} busy={busy} error={dlgError}
                        onPick={assignSelected} onClose={() => { if (!busy) setBulk(null); }} />
        )}
        <ConfirmDialog
          open={bulk === "delete"}
          title={`Delete ${plural(count, "link")}?`}
          confirmLabel={`Delete ${plural(count, "link")}`}
          danger
          busy={busy}
          error={dlgError}
          onConfirm={deleteSelected}
          onCancel={() => setBulk(null)}
        >
          <p>They are deleted for good: a link has no trash to come back from.</p>
        </ConfirmDialog>
        <ConfirmDialog
          open={!!removing}
          title="Delete this link?"
          confirmLabel="Delete link"
          danger
          busy={busy}
          error={dlgError}
          onConfirm={remove}
          onCancel={() => setRemoving(null)}
        >
          <p className="link-confirm">{removing?.title ? <>{removing.title}<br /></> : null}<code>{removing?.url}</code></p>
        </ConfirmDialog>
        <DiscardDialog open={edit.asking || unsaved.asking}
                       onDiscard={edit.asking ? edit.discard : unsaved.discard}
                       onKeep={edit.asking ? edit.keep : keepEditing}>
          <p>
            {edit.asking || !adding ? "The changes to the link you are editing are not saved."
              : edit.dirty ? "The new link and the changes to the link you are editing are not saved."
              : "The new link is not saved."}
          </p>
        </DiscardDialog>
      </div>
    </>
  );
}
