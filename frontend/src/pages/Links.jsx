import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getLinks, getPeople, updateLink, deleteLink, assignLinks, addLinkPerson, removeLinkPerson, deleteLinks,
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
import { useFiltersOpen } from "../lib/useFiltersOpen";
import { useQuickAdd } from "../lib/quickAdd";
import ActionMenu from "../components/ActionMenu";
import ConfirmDialog from "../components/ConfirmDialog";
import DiscardDialog from "../components/DiscardDialog";
import CreatorPicker from "../components/CreatorPicker";
import FiltersToggle from "../components/FiltersToggle";
import PageHeader from "../components/PageHeader";
import SelectionBar from "../components/SelectionBar";
import Icon from "../components/Icon";
import Bookmarklet from "../components/Bookmarklet";
import LinkImport from "../components/LinkImport";
import { AssignDialog, AssignPicker, LinkForm, LinkRow } from "../components/Links";
import { DOWNLOAD_VIEWS, KIND_LABEL, copyUrls, inLinksView, linkLabel, linkName, peopleKey } from "../lib/links";
import { useOneEdit, useUnsaved } from "../lib/unsaved";
import { useHeaderSearch } from "../lib/search";
import { useWindow } from "../lib/windowing";
import { Gap } from "../components/WinGap";

/* Every saved link: a creator's Linktree, Patreon or site, an interview, an
   article, tied to people (a band's account: the band's and each member's)
   or to no one. Nothing is fetched: a link is its text, opened in a new tab
   when followed. Three tabs, held in the address with the filters (a
   reload or Back keeps them):

   - All links: every link that is not a download.
   - Unsorted (?person=none, #165 B): the queue of links tied to no one,
     downloads included (they say "To download"). Each row's Assign… opens
     the person picker in place, and a pick sends the link to that person
     (it leaves the queue, with an Undo).
   - Downloads (?download=todo, done or all; the tab counts what is to
     do): the links to download with another app, whoever's. FeedVault
     never downloads from Links: "to download" and "downloaded" are the
     user's notes. Two switches narrow it: To download | Downloaded | All,
     and Anyone | With creator | No creator (?person=any or none). Each
     row's check marks it downloaded: in To download it leaves the list,
     with an Undo.

   A row is its title, Copy and one ⋯ menu (LinkRow), with the tab's one
   button (Assign…, or the done check). A change that takes a link out of
   the list shown (assigned from Unsorted, moved to Downloads from All
   links, marked downloaded in To download) removes its row at once, and a
   toast offers Undo.

   The page's head: "+ Add link" opens the app's quick-add dialog (the
   header's "+ Link": one form to add a link, its Download checkbox ticked
   from the Downloads tab), Select turns on the rows' checkboxes
   (shift-click: a range) and the selection bar (copy their addresses,
   assign them all to one person in place of any other, also link them to
   one, move them to Downloads or mark them, delete them), and More holds
   Import many… and the bookmarklet.

   Kind (social or other, read from the address), Site and Person (any of
   a link's people) fold behind Filters, which counts the ones in force
   (?kind=, ?site=, ?person= an id); the header's search box (?q=,
   lib/search.js) searches the address, title and notes. Sort (?sort=,
   none: newest first) stays in sight: oldest first, or by site (A to Z,
   the newest first within each, as the server orders them) under a heading
   per site with its count. The headings are rows of the rendered sequence
   (``rows``), so the windowed list measures and skips them as any row; the
   selection and its shift ranges count links only (``at``, a link's place
   in ``links``). It is a way to read the list, not a filter: Clear filters
   keeps it, and the tab. */
export default function Links() {
  const { refreshKey } = useScan();
  const { unsortedLinks, downloadLinks, refresh } = useJobs();
  const toast = useToast();
  const search = useHeaderSearch();
  const openQuickAdd = useQuickAdd();
  const [params, setParams] = useLiveSearchParams();
  // The Downloads tab and its state; ?download=1 is the old address of To download.
  const rawDownload = params.get("download") === "1" ? "todo" : params.get("download");
  const download = DOWNLOAD_VIEWS.includes(rawDownload) ? rawDownload : null;
  const downloadsView = download != null;
  const kind   = !downloadsView && ["social", "other"].includes(params.get("kind")) ? params.get("kind") : "";
  const site   = params.get("site") || "";
  const rawPerson = params.get("person") || "";
  // null: anyone. "any" (with a creator) is the Downloads tab's alone.
  const person = rawPerson === "none" ? "none" : rawPerson === "any" && downloadsView ? "any"
    : /^\d+$/.test(rawPerson) ? Number(rawPerson) : null;
  const q      = (params.get("q") || "").trim();      // the header's search, once typing pauses
  const sort   = SORTS.includes(params.get("sort")) ? params.get("sort") : "newest";
  const unsortedView = person === "none" && !downloadsView;
  // What the list holds (lib/links.js inLinksView): a link a change takes out of it leaves at once.
  const view = useMemo(() => ({ download: downloadsView ? download : unsortedView ? null : "none", person }),
    [download, downloadsView, unsortedView, person]);
  const [removing, setRemoving] = useState(null);     // the link to delete
  const [flash,    setFlash]    = useState(null);     // the id of a link to point at (a URL saved already)
  const [busy,     setBusy]     = useState(false);
  const [dlgError, setDlgError] = useState(null);
  const [assigning, setAssigning] = useState(null);   // the id of the link whose picker is open
  const [bulk,      setBulk]      = useState(null);   // "assign", "also" or "delete": the selection's dialog
  const [alsoFor,   setAlsoFor]   = useState(null);   // the link of a row's "Also link to…"
  const [importing, setImporting] = useState(false);  // Import many… is open
  const [bookmarklet, setBookmarklet] = useState(false);
  const [filtersOpen, setFiltersOpen] = useFiltersOpen();
  const edit = useOneEdit();                          // the link being edited, and whether it has changes
  // A filter or tab change may hide the link being edited, so with unsaved
  // edits it asks first, as leaving the page does. Clearing the filters
  // (the tab and the sort stay) cannot hide it: that goes through.
  const unsaved = useUnsaved(edit.dirty,
    (from, to) => from.pathname === to.pathname && (!edit.dirty || (tabOf(from.search) === tabOf(to.search) && tabOnly(to.search))));

  // The filters in the address, replacing the entry: Back leaves the page.
  const setFilters = useCallback(changes => setParams(prev => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(changes)) {
      if (v == null || v === "") next.delete(k); else next.set(k, String(v));
    }
    return next;
  }, { replace: true }), [setParams]);

  // Every filter off and the search dropped; the tab and the sort are kept.
  function clearFilters() {
    setParams(prev => {
      const next = new URLSearchParams();
      for (const k of ["download", "sort"]) if (prev.get(k)) next.set(k, prev.get(k));
      if (["none", "any"].includes(prev.get("person"))) next.set("person", prev.get("person"));
      return next;
    }, { replace: true });
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
    () => getLinks({ kind, site, person, q, download: view.download, sort: sort === "newest" ? "" : sort })
      .then(r => ({ ...r, sorted: sort })),
    [kind, site, person, q, view.download, sort]);
  const { data, error, loading, reload } = useApi(load, refreshKey);
  const { data: people } = useApi(getPeople, refreshKey);

  // Links a change just took out of the list shown leave it at once, before
  // the list is read again (adjusting state during render, not in an effect).
  const [gone, setGone] = useState(() => new Set());
  const [goneOf, setGoneOf] = useState(data);
  if (goneOf !== data) {
    setGoneOf(data);
    if (gone.size) setGone(new Set());
  }
  const links = useMemo(() => (data?.links || []).filter(l => !gone.has(l.id)), [data, gone]);
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
  const { listRef, focusProps, render, scrollTo } = useWindow(rows.length, { estimate: 58, pins: [editingAt, assigningAt] });

  // A new filter shows other links: a selection of links out of sight is
  // dropped. A new sort shows the same ones: it is kept.
  const sel = useSelection(links, {
    resetKey: `${kind}|${site}|${person}|${q}|${download}`,
    escapeBlocked: !!removing || !!bulk || !!alsoFor || importing || assigning != null || edit.editing != null,
  });

  // The link a 409 named: in view, and lit for a moment.
  useEffect(() => {
    if (flash == null || !data) return undefined;
    const at = rowOf.get(flash);
    if (at != null) scrollTo(at);
    const t = setTimeout(() => setFlash(null), 2400);
    return () => clearTimeout(t);
  }, [flash, data, rowOf, scrollTo]);

  // After a row's change, focus goes on, after the render that shows it. A
  // link that left the list (``leaves``): to the same button (``button``)
  // of the next link, the one that took its place (``index``, in
  // ``links``), else the one before, else the tab; a row without that
  // button gets its menu's. One that stayed, once ``ready(link)`` (the list
  // read again shows the change): to ``button`` in its own row.
  const refocus = useRef(null);
  const tabsRef = useRef(null);
  useEffect(() => {
    const f = refocus.current, list = listRef.current;
    if (!f) return;
    const now = links.find(l => l.id === f.id);
    if (f.leaves ? now : !f.ready(now)) return;
    refocus.current = null;
    const row = l => (l ? list?.querySelector(`[data-link-id="${l.id}"]`) : null);
    const at = l => row(l)?.querySelector(f.button) || row(l)?.querySelector(MENU_BUTTON);
    restoreFocus(f.leaves
      ? at(links[f.index]) || at(links[f.index - 1]) || tabsRef.current?.querySelector("[aria-pressed=true]")
      : at(now));
  }, [links, listRef]);
  // ``l`` is leaving the list: its row goes now, and focus moves on from the button pressed.
  function leave(l, button) {
    refocus.current = { id: l.id, index: links.findIndex(x => x.id === l.id), leaves: true, button };
    setGone(g => new Set(g).add(l.id));
    sel.drop([l.id]);
  }

  // A change to the links: read them again, and the nav's Unsorted count.
  function changed() {
    reload();
    refresh();
  }

  async function save(id, body) {
    setBusy(true);
    try {
      const r = await updateLink(id, body);
      if (!r?.ok) {
        // The form stays open, with the edits and this message in view. The
        // link that has the address is lit when this tab lists it (its
        // filters dropped: both links are in what is left).
        if (r?.id == null) return r?.error || "Could not save the link.";
        if (!r.link || !inLinksView(r.link, { ...view, person: typeof person === "number" ? null : person })) {
          return `That address is saved already, as another link${r.link?.download_at != null ? " (in Downloads)" : r.link ? " (in All links)" : ""}.`;
        }
        if (kind || site || typeof person === "number" || q) clearFilters();
        setFlash(r.id);
        return "That address is saved already, as another link: it is lit in the list.";
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
      setAssigning(null);
      if (!inLinksView(r.link, view)) leave(l, ".link-assign");
      else refocus.current = { id: l.id, button: ".link-row-title", ready: x => !!x?.people?.length };
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
      setGone(g => new Set([...g, ...outOfView(r.links)]));
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

  // ``chosen`` (the selection, or one row's link), also linked to ``p``
  // (all or none): their people keep them. Undo takes them back from ``p``,
  // only those that were not theirs.
  async function alsoLink(chosen, p) {
    if (busy) return;
    const ids = chosen.map(l => l.id);
    setBusy(true);
    setDlgError(null);
    try {
      const r = await addLinkPerson(ids, p.id);
      if (!r?.ok) {
        if (r?.missing) { setDlgError("Some of these links were deleted meanwhile: the list is read again. Nothing was linked."); reload(); }
        else setDlgError(r?.error || "Could not link them.");
        return;
      }
      if (alsoFor) setAlsoFor(null);
      else { setBulk(null); sel.exit(); }
      setGone(g => new Set([...g, ...outOfView(r.links)]));
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
  // The ids of the links (as the server gives them back, changed) this list no longer holds.
  const outOfView = changedLinks => (changedLinks || []).filter(l => !inLinksView(l, view)).map(l => l.id);

  // The toast's Undo of a download change: each group of ``ids`` back to
  // the two states it had (``before``: { download, downloaded }); a link
  // deleted since is passed over. ``groups``: [[before, ids]].
  async function undoMarks(groups) {
    try {
      for (const [before, ids] of groups) {
        const r = await markLinksDownload(ids, before, true);
        if (!r?.ok) { toast(r?.error || "Could not undo.", "err"); break; }
      }
    } catch (err) {
      toast(err.message, "err");
    }
    changed();
  }

  // A row's download change (``marks``: { download } from its menu, or
  // { downloaded } from its check or its menu). A link it takes out of the
  // list leaves at once, with an Undo; so does a move between Downloads
  // and the other links wherever it shows (its state went with it). One
  // that stays marked downloaded, or to download again, says so itself.
  async function mark(l, marks, said) {
    if (busy) return;
    const button = pressedButton();
    setBusy(true);
    try {
      const r = await updateLink(l.id, marks);
      if (!r?.ok) { toast(r?.error || "Could not change the link.", "err"); return; }
      const leaves = !inLinksView(r.link, view);
      if (leaves) leave(l, button);
      if (leaves || marks.download != null) {
        toast(`${linkLabel(l)}: ${said}`, "ok", { label: "Undo", onClick: () => undoMarks([[marksOf(l), [l.id]]]) });
      }
      changed();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }
  const setDownload = (l, on) => mark(l, { download: on }, on ? "moved to Downloads" : "not a download any more");
  const setDone = (l, on) => mark(l, { downloaded: on }, on ? "downloaded" : "to download again");

  // The selection's download change (``what``, a key of BULK_MARKS), in one
  // call (all or none). Links that stay in the list stay selected (to
  // assign them next); those it takes out leave it, and the selection.
  async function markSelected(what) {
    if (busy) return;
    const { marks, done, none } = BULK_MARKS[what];
    const chosen = sel.selectedItems;
    setBusy(true);
    try {
      const r = await markLinksDownload(chosen.map(l => l.id), marks);
      if (!r?.ok) {
        if (r?.missing) {
          toast("Some of these links were deleted meanwhile: the list is read again. Nothing was changed.", "err");
          reload();
        } else toast(r?.error || "Could not change the links.", "err");
        return;
      }
      const out = outOfView(r.links);
      setGone(g => new Set([...g, ...out]));
      sel.drop(out);
      // Undo: those changed, grouped by the states they had.
      const groups = new Map();
      const did = new Set(r.changed);
      for (const l of chosen) {
        if (!did.has(l.id)) continue;
        const before = marksOf(l), key = `${before.download}|${before.downloaded}`;
        groups.set(key, [before, [...(groups.get(key)?.[1] || []), l.id]]);
      }
      const n = r.changed.length;
      toast(n ? `${plural(n, "link")} ${done}` : none, "ok", n ? { label: "Undo", onClick: () => undoMarks([...groups.values()]) } : undefined);
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
  // Counted as on the Feed: what Filters holds. The tab, its switches and the sort are not filters.
  const activeFilters = [kind, site, typeof person === "number"].filter(Boolean).length;
  const narrowed = activeFilters > 0 || !!q;
  const count = sel.count;
  const states = sel.selectedItems.map(l => marksOf(l));
  // The selection's download changes: the tab's own first, as a button; the rest in the bar's ⋯.
  const can = {
    move: states.some(s => !s.download),
    done: states.some(s => !s.downloaded),
    todo: states.some(s => s.downloaded),
    clear: states.some(s => s.download),
  };
  const mainMark = !downloadsView ? "move" : download === "done" ? "todo" : "done";
  const tab = (on, to) => ({ className: `links-tab${on ? " is-on" : ""}`, "aria-pressed": on, onClick: () => setFilters(to) });
  return (
    <>
      <PageHeader
        title="Links"
        sub={!data ? "…" : links.length === total ? fmtInt(total) : `${fmtInt(links.length)} of ${fmtInt(total)}`}
        actions={<>
          <button type="button" className="btn-primary links-add-open" onClick={() => openQuickAdd("", { download: downloadsView })}
                  title={downloadsView ? "Save a web address as a download (Alt+L)" : "Save a web address (Alt+L)"}>
            <Icon name="plus" size={14} />Add link
          </button>
          {(links.length > 0 || sel.active) && (
            <button type="button" className={`btn-secondary select-toggle${sel.active ? " is-on" : ""}`}
                    onClick={() => (sel.active ? sel.exit() : sel.enter())} aria-pressed={sel.active}
                    title={sel.active ? "Leave select mode (Esc)" : "Select links to copy, assign, mark or delete"}>
              <Icon name={sel.active ? "close" : "check"} size={14} />
              {sel.active ? "Done" : "Select"}
            </button>
          )}
          <ActionMenu className="links-more" label="More" items={[
            { label: "Import many…", icon: "download", onSelect: () => setImporting(true),
              title: "Open tabs or exported bookmarks, all at once" },
            { label: bookmarklet ? "Hide the bookmarklet" : "Bookmarklet…", icon: "bookmark", onSelect: () => setBookmarklet(b => !b),
              title: "A bookmark that saves the page you are on" },
          ]} />
        </>}
      />
      <div className="card">
        {bookmarklet && <Bookmarklet onClose={() => setBookmarklet(false)} />}
        <div className="links-tabs" role="group" aria-label="Show" ref={tabsRef}>
          <button type="button" {...tab(!downloadsView && !unsortedView, { person: typeof person === "number" ? person : null, download: null })}
                  title="Every link that is not a download">
            All links
          </button>
          <button type="button" {...tab(unsortedView, { person: "none", download: null })}
                  title="The links tied to no one yet, to give to a person">
            Unsorted
            {unsortedLinks > 0 && <span className="links-tab-count">{fmtInt(unsortedLinks)}</span>}
          </button>
          <button type="button" {...tab(downloadsView, { download: "todo", person: typeof person === "number" ? person : null, kind: null })}
                  title="The links to download with another app: FeedVault only keeps the note">
            Downloads
            {downloadLinks > 0 && <span className="links-tab-count">{fmtInt(downloadLinks)}</span>}
          </button>
        </div>
        {downloadsView && (
          <div className="links-switches">
            <div className="links-tabs is-small" role="group" aria-label="Downloads shown">
              <button type="button" {...tab(download === "todo", { download: "todo" })}>To download</button>
              <button type="button" {...tab(download === "done", { download: "done" })}>Downloaded</button>
              <button type="button" {...tab(download === "all", { download: "all" })}>All</button>
            </div>
            <div className="links-tabs is-small" role="group" aria-label="Whose">
              <button type="button" {...tab(person == null, { person: null })}>Anyone</button>
              <button type="button" {...tab(person === "any", { person: "any" })}>With creator</button>
              <button type="button" {...tab(person === "none", { person: "none" })}>No creator</button>
            </div>
          </div>
        )}
        <div className={`feed-filters feed-fold links-filters${filtersOpen ? " is-open" : ""}`} role="group" aria-label="Filters">
          <FiltersToggle open={filtersOpen} onToggle={() => setFiltersOpen(v => !v)} active={activeFilters} controls="links-scope" />
          <div className="feed-scope" id="links-scope">
            {!downloadsView && (
              <label className="filter">
                <span>Kind</span>
                <select className="sort-select" value={kind} onChange={e => setFilters({ kind: e.target.value })}>
                  <option value="">All</option>
                  <option value="social">{KIND_LABEL.social}</option>
                  <option value="other">{KIND_LABEL.other}</option>
                </select>
              </label>
            )}
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
                allLabel={person === "none" ? "No person" : person === "any" ? "Anyone's" : "Anyone"}
                placeholder="Search people…"
                onChange={v => setFilters({ person: v?.person ? v.person.id : null })}
              />
            </div>
          </div>
          {/* Named by its span only (not a wrapping label): "By site" in a
              label's text would also answer to the Site filter's name. */}
          <div className="filter filter-sort">
            <span id="links-sort-label">Sort</span>
            <select className="sort-select" aria-labelledby="links-sort-label" value={sort}
                    onChange={e => setFilters({ sort: e.target.value === "newest" ? null : e.target.value })}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="site">By site</option>
            </select>
          </div>
          {narrowed && (
            <button type="button" className="btn-ghost filter-clear" onClick={clearFilters}>
              <Icon name="close" size={12} /> Clear filters
            </button>
          )}
        </div>
        {error && !data ? (
          <div className="empty">Could not load links: {error.message}</div>
        ) : loading && !data ? (
          <div className="empty">Loading…</div>
        ) : links.length === 0 ? (
          <div className="empty">
            {narrowed ? "No link matches."
              : downloadsView ? (person != null ? "No download here."
                : download === "todo" ? "Nothing to download." : download === "done" ? "Nothing downloaded yet."
                : "No download yet. Add one with Add link, its Download box ticked.")
              : unsortedView ? "Nothing to sort: every link has its person."
              : "No links yet. Add one with Add link."}
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
                         mixed={unsortedView} assign={unsortedView}
                         select={sel.active ? { checked: sel.isSelected(l.id), onClick: e => sel.toggle(at, e.shiftKey) } : undefined}
                         onAssign={x => setAssigning(a => (a === x.id ? null : x.id))}
                         onAlso={x => { setDlgError(null); setAlsoFor(x); }}
                         onDownload={setDownload} onDone={setDone}
                         onEdit={x => { setAssigning(null); edit.open(x.id); }}
                         onDelete={x => { setDlgError(null); setRemoving(x); }}>
                  {assigning === l.id && (
                    <AssignPicker people={people} label={`Assign ${linkName(l)} to`} busy={busy}
                                  onPick={p => assignOne(l, p)}
                                  onCancel={() => {
                                    setAssigning(null);
                                    const row = listRef.current?.querySelector(`[data-link-id="${l.id}"]`);
                                    restoreFocus(row?.querySelector(".link-assign") || row?.querySelector(MENU_BUTTON));
                                  }} />
                  )}
                </LinkRow>
              );
            }, (height, key) => <Gap key={key} as="li" height={height} />)}
          </ul>
        )}
        {sel.active && (
          <SelectionBar selection={sel} loaded={links.length}>
            <button type="button" className="btn-secondary" disabled={busy || !count} onClick={() => copyUrls(sel.selectedItems, toast)}>
              <Icon name="copy" size={14} />Copy URLs
            </button>
            <button type="button" className="btn-secondary" disabled={busy || !count} onClick={() => { setDlgError(null); setBulk("assign"); }}
                    title="To one person, in place of any other">
              <Icon name="users" size={14} />Assign to…
            </button>
            <button type="button" className="btn-secondary" disabled={busy || !can[mainMark]} onClick={() => markSelected(mainMark)}
                    title={BULK_MARKS[mainMark].title}>
              <Icon name={BULK_MARKS[mainMark].icon} size={14} />{BULK_MARKS[mainMark].label}
            </button>
            <ActionMenu className="select-more" label="More for the selection" items={[
              { label: "Also link to…", icon: "userPlus", disabled: busy || !count,
                onSelect: () => { setDlgError(null); setBulk("also"); },
                title: "To one more person: a shared account, theirs and their other people's" },
              "separator",
              ...Object.keys(BULK_MARKS).filter(k => k !== mainMark).map(k => ({
                label: BULK_MARKS[k].label, icon: BULK_MARKS[k].icon, title: BULK_MARKS[k].title,
                disabled: busy || !can[k], onSelect: () => markSelected(k),
              })),
            ]} />
            <button type="button" className="btn-danger" disabled={busy || !count} onClick={() => { setDlgError(null); setBulk("delete"); }}>
              <Icon name="trash" size={14} />Delete…
            </button>
          </SelectionBar>
        )}

        {(bulk === "assign" || bulk === "also" || alsoFor) && (
          <AssignDialog count={alsoFor ? 1 : count} people={people} busy={busy} error={dlgError} also={!!alsoFor || bulk === "also"}
                        onPick={p => (alsoFor ? alsoLink([alsoFor], p) : bulk === "also" ? alsoLink(sel.selectedItems, p) : assignSelected(p))}
                        onClose={() => { if (!busy) { setBulk(null); setAlsoFor(null); } }} />
        )}
        {importing && <LinkImport download={downloadsView} onClose={() => setImporting(false)} onImported={changed} />}
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
          <p>The changes to the link you are editing are not saved.</p>
        </DiscardDialog>
      </div>
    </>
  );
}

// A row's ⋯ button: where focus goes when the button pressed is not in the next row.
const MENU_BUTTON = ".link-menu .action-menu-btn";

// The row button a change was asked from (focus is on it, or back on the menu's): its selector.
function pressedButton() {
  return document.activeElement?.closest?.(".link-done") ? ".link-done" : MENU_BUTTON;
}

// A link's two download states, as updateLink and markLinksDownload take them: what an Undo puts back.
const marksOf = l => ({ download: l.download_at != null, downloaded: l.downloaded_at != null });

/* The selection's download changes: what is sent (``marks``), the button's
   or the menu entry's name, and the toast's words (``done`` after the
   count; ``none`` when it changed nothing). */
const BULK_MARKS = {
  move: { marks: { download: true }, label: "Move to Downloads", icon: "download", done: "moved to Downloads", none: "Already in Downloads",
          title: "Links to download with another app: FeedVault only keeps the note" },
  done: { marks: { downloaded: true }, label: "Mark downloaded", icon: "check", done: "marked downloaded", none: "Already downloaded",
          title: "Downloaded, with another app; one that is not a download becomes one" },
  todo: { marks: { downloaded: false }, label: "Mark to download", icon: "undo", done: "marked to download", none: "None of them was downloaded",
          title: "Downloaded ones are to download again" },
  clear: { marks: { download: false }, label: "Not a download", icon: "link", done: "moved out of Downloads", none: "None of them was a download",
           title: "Back among the other links: their people stay" },
};

// The tab an address shows: its Downloads state, and whether it is narrowed to no one or to someone's.
function tabOf(search) {
  const p = new URLSearchParams(search);
  const person = p.get("person");
  return `${p.get("download") === "1" ? "todo" : p.get("download") || ""}|${["none", "any"].includes(person) ? person : ""}`;
}

// An address with its tab and sort at most: no filter and no search in it.
function tabOnly(search) {
  const p = new URLSearchParams(search);
  return [...p.keys()].every(k => k === "sort" || k === "download" || (k === "person" && ["none", "any"].includes(p.get(k))));
}

// The orders of ?sort= (links.SORTS), the first the default.
const SORTS = ["newest", "oldest", "site"];

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
