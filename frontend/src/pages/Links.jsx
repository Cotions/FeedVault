import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getLinks, getPeople, createLink, updateLink, deleteLink, assignLinks, addLinkPerson, removeLinkPerson, deleteLinks,
  markLinksDownload,
} from "../lib/api";
import { useApi } from "../lib/useApi";
import { useLiveSearchParams } from "../lib/useLiveSearchParams";
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
import LinkImport from "../components/LinkImport";
import { AssignDialog, AssignPicker, LinkForm, LinkRow } from "../components/Links";
import { KIND_LABEL, copyUrls, linkLabel, linkName, peopleKey } from "../lib/links";
import { useOneEdit, useUnsaved } from "../lib/unsaved";
import { useHeaderSearch } from "../lib/search";
import { useWindow } from "../lib/windowing";
import { Gap } from "../components/WinGap";

/* Every saved link: a creator's Linktree, Patreon or site, an interview, an
   article, tied to people (a band's account: the band's and each member's)
   or to no one. Add at the top; filter by kind (social or other, read from
   the address), site and person (any of a link's people), or search the
   address, title and notes (the header's search box, lib/search.js).
   Nothing is fetched: a link is its text, opened
   in a new tab when followed. The filters live in the address (?kind=,
   ?site=, ?person= an id or "none", ?q=), so a reload or Back keeps them.

   The Unsorted tab (?person=none, #165 B) is the queue of links tied to no
   one: each row's Assign… opens the person picker in place, and a pick
   sends the link to that person (it leaves the queue, with an Undo). Any
   row can be checked (shift-click: a range) for the selection bar: assign
   them all (to one person, in place of any other), also link them to one
   (their people kept), mark them to download or clear their mark, copy
   their addresses, or delete them.

   Sorting Unsorted out, a link is something to download (with another
   app: FeedVault never downloads from Links), or an artist's, or both. The
   download button on each row marks it "to download" (and clears the mark),
   apart from its people: a link marked in Unsorted stays there until it is
   assigned, and assigning keeps the mark. The To download tab (?download=1)
   lists the marked links, whoever's; a mark cleared there takes the link
   out of it at once, with an Undo, as an assign does from Unsorted.

   Sort (?sort=, none: newest first) orders any tab oldest first, or by
   site (A to Z, the newest first within each, as the server orders them)
   under a heading per site with its count. The headings are rows of the
   rendered sequence (``rows``), so the windowed list measures and skips
   them as any row; the selection and its shift ranges count links only
   (``at``, a link's place in ``links``). It is a way to read the list, not
   a filter: Clear filters keeps it. */
export default function Links() {
  const { refreshKey } = useScan();
  const { unsortedLinks, downloadLinks, refresh } = useJobs();
  const toast = useToast();
  const search = useHeaderSearch();
  const [params, setParams] = useLiveSearchParams();
  const kind   = ["social", "other"].includes(params.get("kind")) ? params.get("kind") : "";
  const site   = params.get("site") || "";
  const rawPerson = params.get("person") || "";
  const person = rawPerson === "none" ? "none" : /^\d+$/.test(rawPerson) ? Number(rawPerson) : null;   // null: anyone
  const q      = (params.get("q") || "").trim();      // the header's search, once typing pauses
  const downloadView = params.get("download") === "1";  // the To download tab
  const sort   = SORTS.includes(params.get("sort")) ? params.get("sort") : "newest";
  const unsortedView = person === "none";
  const [removing, setRemoving] = useState(null);     // the link to delete
  const [flash,    setFlash]    = useState(null);     // the id of a link to point at (a URL saved already)
  const [busy,     setBusy]     = useState(false);
  const [dlgError, setDlgError] = useState(null);
  const [assigning, setAssigning] = useState(null);   // the id of the link whose picker is open
  const [bulk,      setBulk]      = useState(null);   // "assign", "also" or "delete": the selection's dialog
  const edit = useOneEdit();                          // the link being edited, and whether it has changes
  const [adding, setAdding] = useState(false);        // the new link's form has something typed
  // A filter change may hide the link being edited, so with unsaved edits it
  // asks first, as leaving the page does. Clearing every filter (the sort
  // may stay: it hides nothing) cannot hide it: that goes through. No filter
  // touches the new link's form.
  const unsaved = useUnsaved(edit.dirty || adding,
    (from, to) => from.pathname === to.pathname && (sortOnly(to.search) || !edit.dirty));

  // The filters in the address, replacing the entry: Back leaves the page.
  const setFilters = useCallback(changes => setParams(prev => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(changes)) {
      if (v == null || v === "") next.delete(k); else next.set(k, String(v));
    }
    return next;
  }, { replace: true }), [setParams]);

  // Every filter off, the tab back to All links; the sort is kept.
  function clearFilters() {
    setParams(prev => new URLSearchParams(prev.get("sort") ? { sort: prev.get("sort") } : {}), { replace: true });
  }
  // Kept editing instead of searching: the box shows the search in force.
  function keepEditing() {
    unsaved.keep();
    search.reset();
  }

  // Each answer says which order it was read in (``sorted``): until the one
  // for a new sort comes, the list shown is the last one, in its own order,
  // and gets no site headings it was not sorted for.
  const load = useCallback(
    () => getLinks({ kind, site, person, q, download: downloadView, sort: sort === "newest" ? "" : sort })
      .then(r => ({ ...r, sorted: sort })),
    [kind, site, person, q, downloadView, sort]);
  const { data, error, loading, reload } = useApi(load, refreshKey);
  const { data: people } = useApi(getPeople, refreshKey);

  // Links just given to someone from Unsorted, or whose mark was cleared in
  // To download, leave the tab at once, before the list is read again
  // (adjusting state during render, not in an effect).
  const [gone, setGone] = useState(() => new Set());
  const [goneOf, setGoneOf] = useState(data);
  if (goneOf !== data) {
    setGoneOf(data);
    if (gone.size) setGone(new Set());
  }
  const leaving = unsortedView || downloadView;
  const links = useMemo(() => (data?.links || []).filter(l => !(leaving && gone.has(l.id))), [data, gone, leaving]);
  // What the list renders, in order: each link ({ link, at: its place in
  // ``links`` }) and, sorted by site, a heading before each site's links
  // ({ site, count }). Indexes into ``rows`` are the window's (data-index);
  // ``at`` is the selection's.
  const bySite = data?.sorted === "site";
  const rows = useMemo(() => siteRows(links, bySite), [links, bySite]);
  const rowOf = useMemo(() => new Map(rows.flatMap((r, i) => (r.link ? [[r.link.id, i]] : []))), [rows]);
  // Only the rows near the viewport are rendered once there are many
  // (lib/windowing.js); the one being edited always is: its form holds the
  // unsaved edits. So is the one being assigned. The browser's find bar
  // sees the rendered rows only: the search box searches them all.
  const editingAt = edit.editing == null ? -1 : rowOf.get(edit.editing) ?? -1;
  const assigningAt = assigning == null ? -1 : rowOf.get(assigning) ?? -1;
  const { listRef, focusProps, render, scrollTo } = useWindow(rows.length, { estimate: 66, pins: [editingAt, assigningAt] });

  // A new filter shows other links: a selection of links out of sight is
  // dropped. A new sort shows the same ones: it is kept.
  const sel = useSelection(links, {
    resetKey: `${kind}|${site}|${person}|${q}|${downloadView}`,
    escapeBlocked: !!removing || !!bulk || assigning != null || edit.editing != null,
  });

  // The link a 409 named: in view, and lit for a moment.
  useEffect(() => {
    if (flash == null || !data) return undefined;
    const at = rowOf.get(flash);
    if (at != null) scrollTo(at);
    const t = setTimeout(() => setFlash(null), 2400);
    return () => clearTimeout(t);
  }, [flash, data, rowOf, scrollTo]);

  // After an assign or a mark, focus goes on, after the render that shows
  // the change. A link that left the tab (``leaves``): to the same button
  // (``button``) of the next link, the one that took its place (``index``,
  // in ``links``), else the one before, else the tab. One that stayed, once
  // ``ready(link)`` (the list read again shows the change): to ``button``
  // in its own row, its title for an assign.
  const refocus = useRef(null);
  const tabsRef = useRef(null);
  useEffect(() => {
    const f = refocus.current, list = listRef.current;
    if (!f) return;
    const now = links.find(l => l.id === f.id);
    if (f.leaves ? now : !f.ready(now)) return;
    refocus.current = null;
    const at = l => (l ? list?.querySelector(`[data-link-id="${l.id}"] ${f.button}`) : null);
    restoreFocus(f.leaves
      ? at(links[f.index]) || at(links[f.index - 1]) || tabsRef.current?.querySelector("[aria-pressed=true]")
      : at(now));
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

  // The toast's Undo: each link back to the people it had before (or to no
  // one), but only if it is still ``now``'s alone, the one just given; a
  // link changed or deleted since stays as it is. ``before``: [[id, people]].
  async function undoAssign(before, now) {
    const groups = new Map();
    for (const [id, prev] of before) {
      const key = peopleKey(prev);
      groups.set(key, { people: prev.map(p => p.id), ids: [...(groups.get(key)?.ids || []), id] });
    }
    let skipped = 0;
    try {
      for (const { people: prev, ids } of groups.values()) {
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
      refocus.current = unsortedView
        ? { id: l.id, index: links.findIndex(x => x.id === l.id), leaves: true, button: ".link-assign" }
        : { id: l.id, button: ".link-row-title", ready: x => !!x?.people?.length };
      setAssigning(null);
      if (unsortedView) setGone(g => new Set(g).add(l.id));
      sel.drop([l.id]);
      toast(`${linkLabel(l)}: assigned to ${p.name}`, "ok", { label: "Undo", onClick: () => undoAssign([[l.id, []]], p.id) });
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
      // Undo gives each back to whom it had (no one, or other people).
      const before = chosen.filter(l => peopleKey(l.people) !== String(p.id)).map(l => [l.id, l.people || []]);
      toast(`${plural(ids.length, "link")} assigned to ${p.name}`, "ok",
        before.length ? { label: "Undo", onClick: () => undoAssign(before, p.id) } : { to: personPath(p.id), label: "Show" });
      changed();
    } catch (err) {
      setDlgError(err.message);
    } finally {
      setBusy(false);
    }
  }

  // The selection, also linked to ``p`` (all or none): their people keep
  // them. Undo takes them back from ``p``, only those that were not theirs.
  async function alsoSelected(p) {
    if (busy) return;
    const ids = sel.selectedItems.map(l => l.id);
    setBusy(true);
    setDlgError(null);
    try {
      const r = await addLinkPerson(ids, p.id);
      if (!r?.ok) {
        if (r?.missing) { setDlgError("Some of these links were deleted meanwhile: the list is read again. Nothing was linked."); reload(); }
        else setDlgError(r?.error || "Could not link them.");
        return;
      }
      setBulk(null);
      sel.exit();
      if (unsortedView) setGone(g => new Set([...g, ...r.added]));
      const undo = async () => {
        try {
          const back = await removeLinkPerson(r.added, p.id);
          if (!back?.ok) toast(back?.error || "Could not undo.", "err");
        } catch (err) {
          toast(err.message, "err");
        }
        changed();
      };
      toast(r.added.length ? `${plural(r.added.length, "link")} also linked to ${p.name}` : `Already ${p.name}'s`, "ok",
        r.added.length ? { label: "Undo", onClick: undo } : { to: personPath(p.id), label: "Show" });
      changed();
    } catch (err) {
      setDlgError(err.message);
    } finally {
      setBusy(false);
    }
  }

  // The toast's Undo of a mark or a clear: ``ids`` (those whose mark
  // changed) back to marked (``on``) or not; one deleted since is passed over.
  async function undoMark(ids, on) {
    try {
      const r = await markLinksDownload(ids, on, true);
      if (!r?.ok) toast(r?.error || "Could not undo.", "err");
    } catch (err) {
      toast(err.message, "err");
    }
    changed();
  }

  // A row's download button: marked to download, or the mark cleared. In
  // To download, a link whose mark is cleared leaves it, with an Undo;
  // elsewhere it stays, the button now saying the other.
  async function toggleDownload(l) {
    if (busy) return;
    const on = l.download_at == null;
    setBusy(true);
    try {
      const r = await updateLink(l.id, { download: on });
      if (!r?.ok) { toast(r?.error || "Could not change the mark.", "err"); return; }
      const leaves = downloadView && !on;
      refocus.current = { id: l.id, index: links.findIndex(x => x.id === l.id), leaves, button: ".link-dl",
                          ready: x => !!x && (x.download_at != null) === on };
      if (leaves) {
        setGone(g => new Set(g).add(l.id));
        sel.drop([l.id]);
        toast(`${linkLabel(l)}: download mark cleared`, "ok", { label: "Undo", onClick: () => undoMark([l.id], true) });
      }
      changed();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  // The selection marked to download (``on``) or its marks cleared, in one
  // call (all or none). Links that stay in the tab stay selected (to assign
  // them next); in To download, those cleared leave it, and the selection.
  async function markSelected(on) {
    if (busy) return;
    const ids = sel.selectedItems.map(l => l.id);
    setBusy(true);
    try {
      const r = await markLinksDownload(ids, on);
      if (!r?.ok) {
        if (r?.missing) {
          toast("Some of these links were deleted meanwhile: the list is read again. No mark was changed.", "err");
          reload();
        } else toast(r?.error || "Could not change the marks.", "err");
        return;
      }
      if (downloadView && !on) {
        setGone(g => new Set([...g, ...r.changed]));
        sel.drop(r.changed);
      }
      const n = r.changed.length;
      toast(!n ? (on ? "Marked to download already" : "None of them was marked")
        : on ? `${plural(n, "link")} marked to download` : `${plural(n, "link")}: download mark cleared`,
      "ok", n ? { label: "Undo", onClick: () => undoMark(r.changed, !on) } : undefined);
      changed();
    } catch (err) {
      toast(err.message, "err");
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
  const filtered = !!(kind || site || person != null || q || downloadView);
  const narrowed = !!(kind || site || typeof person === "number" || q);   // more than the tab
  const count = sel.count;
  const anyMarked = sel.selectedItems.some(l => l.download_at != null);
  const anyUnmarked = sel.selectedItems.some(l => l.download_at == null);
  return (
    <>
      <PageHeader
        title="Links"
        sub={!data ? "…" : filtered ? `${fmtInt(links.length)} of ${fmtInt(total)}` : fmtInt(total)}
      />
      <div className="card links-add">
        <h3 className="card-title">Add a link</h3>
        <p className="page-lede">
          Any address: a Linktree, a Patreon, an article. FeedVault keeps the text and never opens it.
        </p>
        <LinkForm people={people || []} busy={busy} onSubmit={add} idPrefix="links-add" onDirty={setAdding} />
        <Bookmarklet />
        <LinkImport onImported={changed} />
      </div>
      <div className="card">
        <div className="links-tabs" role="group" aria-label="Show" ref={tabsRef}>
          <button type="button" className={`links-tab${person == null && !downloadView ? " is-on" : ""}`}
                  aria-pressed={person == null && !downloadView} onClick={() => setFilters({ person: null, download: null })}>
            All links
          </button>
          <button type="button" className={`links-tab${unsortedView && !downloadView ? " is-on" : ""}`}
                  aria-pressed={unsortedView && !downloadView} onClick={() => setFilters({ person: "none", download: null })}
                  title="The links tied to no one yet, to give to a person">
            Unsorted
            {unsortedLinks > 0 && <span className="links-tab-count">{fmtInt(unsortedLinks)}</span>}
          </button>
          <button type="button" className={`links-tab${downloadView ? " is-on" : ""}`} aria-pressed={downloadView}
                  onClick={() => setFilters({ download: 1, person: null })}
                  title="The links marked to download, with another app: FeedVault only keeps the mark">
            To download
            {downloadLinks > 0 && <span className="links-tab-count">{fmtInt(downloadLinks)}</span>}
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
          {/* Named by its span only (not a wrapping label): "By site" in a
              label's text would also answer to the Site filter's name. */}
          <div className="filter">
            <span id="links-sort-label">Sort</span>
            <select className="sort-select" aria-labelledby="links-sort-label" value={sort}
                    onChange={e => setFilters({ sort: e.target.value === "newest" ? null : e.target.value })}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="site">By site</option>
            </select>
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
            {downloadView && !narrowed ? "Nothing to download: no link is marked."
              : unsortedView && !narrowed ? "Nothing to sort: every link has its person."
              : filtered ? "No link matches." : "No links yet. Add one above."}
          </div>
        ) : (
          <ul className="link-list" ref={listRef} {...focusProps}>
            {render(i => {
              const { link: l, at, site: head, count: n, first } = rows[i];
              if (!l) {
                return (
                  <li key={`site:${first}`} className="link-site-head" data-index={i}>
                    <h4 className="link-site-name">{head}</h4>
                    <span className="link-site-count">{plural(n, "link")}</span>
                  </li>
                );
              }
              return edit.editing === l.id ? (
                <li key={l.id} className="link-row is-editing" data-index={i}>
                  <LinkForm link={l} people={people || []} busy={busy} submitLabel="Save"
                            idPrefix={`link-${l.id}`} onSubmit={body => save(l.id, body)} onCancel={edit.close}
                            onDirty={edit.onDirty} />
                </li>
              ) : (
                <LinkRow key={l.id} index={i} link={l} busy={busy} flash={flash === l.id}
                         select={{ checked: sel.isSelected(l.id), onClick: e => { sel.enter(); sel.toggle(at, e.shiftKey); } }}
                         onAssign={x => setAssigning(a => (a === x.id ? null : x.id))}
                         onDownload={toggleDownload}
                         onEdit={x => { setAssigning(null); edit.open(x.id); }}
                         onDelete={x => { setDlgError(null); setRemoving(x); }}>
                  {assigning === l.id && (
                    <AssignPicker people={people} label={`Assign ${linkName(l)} to`} busy={busy}
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
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => { setDlgError(null); setBulk("assign"); }}
                    title="To one person, in place of any other">
              <Icon name="users" size={14} />Assign {fmtInt(count)} to…
            </button>
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => { setDlgError(null); setBulk("also"); }}
                    title="To one more person: a shared account, theirs and their other people's">
              <Icon name="plus" size={14} />Also link {fmtInt(count)} to…
            </button>
            <button type="button" className="btn-secondary" disabled={busy || !anyUnmarked} onClick={() => markSelected(true)}
                    title="To download, with another app: FeedVault only keeps the mark">
              <Icon name="download" size={14} />Mark {fmtInt(count)} to download
            </button>
            <button type="button" className="btn-secondary" disabled={busy || !anyMarked} onClick={() => markSelected(false)}
                    title="Downloaded: their mark goes, their people stay">
              Clear mark
            </button>
            <button type="button" className="btn-danger" disabled={busy} onClick={() => { setDlgError(null); setBulk("delete"); }}>
              <Icon name="trash" size={14} />Delete {fmtInt(count)}…
            </button>
          </SelectionBar>
        )}

        {(bulk === "assign" || bulk === "also") && (
          <AssignDialog count={count} people={people} busy={busy} error={dlgError} also={bulk === "also"}
                        onPick={bulk === "also" ? alsoSelected : assignSelected} onClose={() => { if (!busy) setBulk(null); }} />
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
          <p className="link-confirm">{removing?.title || removing?.label ? <>{removing.title || removing.label}<br /></> : null}<code>{removing?.url}</code></p>
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

// The orders of ?sort= (links.SORTS), the first the default.
const SORTS = ["newest", "oldest", "site"];

// An address whose only parameter, if any, is the sort: no filter in it.
function sortOnly(search) {
  return [...new URLSearchParams(search).keys()].every(k => k === "sort");
}

/* The rendered sequence of the list: each link as { link, at } (``at``:
   its place in ``links``), and, ``bySite``, a { site, count, first } heading
   before each run of one site's links (the server sorted them by site),
   ``first`` the id of the run's first link: its key, one of its own even
   if a site came in two runs. */
function siteRows(links, bySite) {
  if (!bySite) return links.map((link, at) => ({ link, at }));
  const counts = new Map();
  for (const l of links) counts.set(l.site, (counts.get(l.site) || 0) + 1);
  const out = [];
  links.forEach((link, at) => {
    if (at === 0 || links[at - 1].site !== link.site) out.push({ site: link.site, count: counts.get(link.site), first: link.id });
    out.push({ link, at });
  });
  return out;
}
