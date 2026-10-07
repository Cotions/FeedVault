import { useCallback, useEffect, useState } from "react";
import { getLinks, getPeople, createLink, updateLink, deleteLink } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { fmtInt } from "../lib/fmt";
import ConfirmDialog from "../components/ConfirmDialog";
import DiscardDialog from "../components/DiscardDialog";
import CreatorPicker from "../components/CreatorPicker";
import PageHeader from "../components/PageHeader";
import { LinkForm, LinkRow } from "../components/Links";
import { KIND_LABEL } from "../lib/links";
import { useOneEdit, useUnsaved } from "../lib/unsaved";

/* Every saved link: a creator's Linktree, Patreon or site, an interview, an
   article, tied to a person or to no one. Add at the top; filter by kind
   (social or other, read from the address), site and person, or search the
   address, title and notes. Nothing is fetched: a link is its text, opened
   in a new tab when followed. */
export default function Links() {
  const { refreshKey } = useScan();
  const toast = useToast();
  const [kind,     setKind]     = useState("");
  const [site,     setSite]     = useState("");
  const [person,   setPerson]   = useState(null);     // a person id, "none", or null for anyone
  const [query,    setQuery]    = useState("");
  const [q,        setQ]        = useState("");       // the query, once typing pauses
  const [removing, setRemoving] = useState(null);     // the link to delete
  const [flash,    setFlash]    = useState(null);     // the id of a link to point at (a URL saved already)
  const [busy,     setBusy]     = useState(false);
  const [dlgError, setDlgError] = useState(null);
  const edit = useOneEdit();                          // the link being edited, and whether it has changes
  const unsaved = useUnsaved(edit.dirty);

  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);

  const load = useCallback(() => getLinks({ kind, site, person, q }), [kind, site, person, q]);
  const { data, error, loading, reload } = useApi(load, refreshKey);
  const { data: people } = useApi(getPeople, refreshKey);

  // The link a 409 named: in view, and lit for a moment.
  useEffect(() => {
    if (flash == null || !data) return undefined;
    document.querySelector(`[data-link-id="${flash}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    const t = setTimeout(() => setFlash(null), 2400);
    return () => clearTimeout(t);
  }, [flash, data]);

  function showTaken(id) {
    setKind(""); setSite(""); setPerson(null); setQuery(""); setQ("");
    setFlash(id);
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
      reload();
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
        if (r?.id != null) return "That address is saved already, as another link.";
        return r?.error || "Could not save the link.";
      }
      edit.close();
      reload();
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
      setRemoving(null);
      reload();
    } catch (err) {
      setDlgError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const links = data?.links || [];
  const sites = data?.sites || [];
  const total = sites.reduce((n, s) => n + s.count, 0);
  const filtered = !!(kind || site || person != null || q);
  return (
    <>
      <PageHeader
        title="Links"
        sub={data ? fmtInt(total) : "…"}
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
        <LinkForm people={people || []} busy={busy} onSubmit={add} idPrefix="links-add" />
      </div>
      <div className="card">
        <div className="feed-filters links-filters" role="group" aria-label="Filters">
          <label className="filter">
            <span>Kind</span>
            <select className="sort-select" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="">All</option>
              <option value="social">{KIND_LABEL.social}</option>
              <option value="other">{KIND_LABEL.other}</option>
            </select>
          </label>
          <label className="filter">
            <span>Site</span>
            <select className="sort-select" value={site} onChange={e => setSite(e.target.value)}>
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
              allLabel={person === "none" ? "No person" : "Anyone"}
              placeholder="Search people…"
              onChange={v => setPerson(v?.person ? v.person.id : null)}
            />
          </div>
          <button type="button" className={`btn-secondary links-none${person === "none" ? " is-on" : ""}`}
                  aria-pressed={person === "none"} onClick={() => setPerson(person === "none" ? null : "none")}
                  title="Only the links tied to no one">
            No person
          </button>
          {filtered && (
            <button type="button" className="btn-ghost filter-clear"
                    onClick={() => { setKind(""); setSite(""); setPerson(null); setQuery(""); setQ(""); }}>
              Clear filters
            </button>
          )}
        </div>
        {error && !data ? (
          <div className="empty">Could not load links: {error.message}</div>
        ) : loading && !data ? (
          <div className="empty">Loading…</div>
        ) : links.length === 0 ? (
          <div className="empty">{filtered ? "No link matches." : "No links yet. Add one above."}</div>
        ) : (
          <ul className="link-list">
            {links.map(l => edit.editing === l.id ? (
              <li key={l.id} className="link-row is-editing">
                <LinkForm link={l} people={people || []} busy={busy} submitLabel="Save"
                          idPrefix={`link-${l.id}`} onSubmit={body => save(l.id, body)} onCancel={edit.close}
                          onDirty={edit.onDirty} />
              </li>
            ) : (
              <LinkRow key={l.id} link={l} busy={busy} flash={flash === l.id}
                       onEdit={x => edit.open(x.id)} onDelete={x => { setDlgError(null); setRemoving(x); }} />
            ))}
          </ul>
        )}

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
                       onKeep={edit.asking ? edit.keep : unsaved.keep}>
          <p>The changes to the link you are editing are not saved.</p>
        </DiscardDialog>
      </div>
    </>
  );
}
