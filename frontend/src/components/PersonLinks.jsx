import { useEffect, useState } from "react";
import {
  addLinkPerson, createLink, deleteLink, getPeople, orderPersonLinks, removeLinkPerson, updateLink,
} from "../lib/api";
import { fmtInt } from "../lib/fmt";
import { useJobs } from "../lib/jobs";
import { copyUrls, linkLabel, namesText, splitDownloads } from "../lib/links";
import { linkMatches } from "../lib/search";
import { useToast } from "../lib/toast";
import { useOneEdit } from "../lib/unsaved";
import ConfirmDialog from "./ConfirmDialog";
import DiscardDialog from "./DiscardDialog";
import Icon from "./Icon";
import { AssignDialog, LinkForm, LinkRow } from "./Links";

/* A person's saved links, in two sections of their page.

   Links: their socials, then the others, each group in the person's order
   (the row menu's Move up and Move down move a link within its group).
   Never a download. One folded "+ Add link…" adds one with the person
   preset; its Download checkbox adds it to Downloads instead.

   Downloads, right after: the links of theirs to download with another
   app (FeedVault never downloads from a link: the two states are the
   user's notes), behind a To download | Downloaded switch with their
   counts. Each row's check marks it downloaded (or takes that back), with
   an Undo; Copy URLs copies the ones still to do, to paste into the other
   app. The section is there once they have a download.

   Every row: Copy, and one ⋯ menu (LinkRow) to edit in place, also link
   it to someone else, move it between Links and Downloads, or delete it.
   A link they share with others (a band's account, #215) shows the
   others; Remove from this creator takes it from this person only, Delete
   from everyone.

   ``query`` (the header's search): both sections show the links it
   matches only, not moved up or down (their places are among all of
   them), nor added to; the downloads matched are listed whatever their
   state. onDirty(bool): whether the link being edited has unsaved
   changes; onAddDirty(bool): whether the new link's form has something
   typed. */
export default function PersonLinks({ person: p, query = "", onChanged, onDirty, onAddDirty }) {
  const toast = useToast();
  const { refresh } = useJobs();
  const [busy,     setBusy]     = useState(false);
  const edit = useOneEdit();                          // the link being edited, and whether it has changes
  const [removing, setRemoving] = useState(null);
  const [dlgError, setDlgError] = useState(null);
  const [view,     setView]     = useState("todo");   // the Downloads switch: "todo" or "done"
  const [also,     setAlso]     = useState(null);     // the link to also give to someone, and whom to choose from
  const all = p.links || [];
  const searching = !!query.trim();
  const found = searching ? all.filter(l => linkMatches(l, query)) : all;
  const { links, todo, done } = splitDownloads(found);
  const groups = [["Socials", links.filter(l => l.kind === "social")], ["Other", links.filter(l => l.kind !== "social")]];
  const downloads = searching ? [...todo, ...done] : view === "done" ? done : todo;
  const anyDownload = all.some(l => l.download_at != null);
  useEffect(() => { onDirty(edit.dirty); }, [edit.dirty, onDirty]);
  const sharedWith = (removing?.people || []).filter(o => o.id !== p.id);

  async function call(fn, failed) {
    setBusy(true);
    try {
      const r = await fn();
      if (!r?.ok) return r?.id != null ? "That address is saved already, as another link (see Links)." : r?.error || failed;
      onChanged();
      refresh();                                      // the nav's Unsorted and Downloads counts
      return null;
    } catch (err) {
      return err.message;
    } finally {
      setBusy(false);
    }
  }

  async function move(group, i, by) {
    const ids = group.map(l => l.id);
    [ids[i], ids[i + by]] = [ids[i + by], ids[i]];
    const problem = await call(() => orderPersonLinks(p.id, ids), "Could not reorder.");
    if (problem) toast(problem, "err");
  }

  async function remove() {
    setDlgError(null);
    const problem = await call(() => deleteLink(removing.id), "Could not delete.");
    if (problem) { setDlgError(problem); return; }
    toast("Link deleted.");
    setRemoving(null);
  }

  // Not theirs any more; the link stays its other people's. Undo gives it
  // back to them (at the end of their links).
  async function untie(l) {
    const problem = await call(() => removeLinkPerson([l.id], p.id), "Could not remove it.");
    if (problem) { toast(problem, "err"); return; }
    const undo = async () => {
      const back = await call(() => addLinkPerson([l.id], p.id), "Could not undo.");
      if (back) toast(back, "err");
    };
    toast(`${linkLabel(l)}: no longer ${p.name}'s`, "ok", { label: "Undo", onClick: undo });
  }

  // A link's download states changed to ``marks`` ({ download?, downloaded? }),
  // said in a toast whose Undo puts back the two it had.
  async function mark(l, marks, said) {
    const problem = await call(() => updateLink(l.id, marks), "Could not change the link.");
    if (problem) { toast(problem, "err"); return; }
    const before = { download: l.download_at != null, downloaded: l.downloaded_at != null };
    const undo = async () => {
      const back = await call(() => updateLink(l.id, before), "Could not undo.");
      if (back) toast(back, "err");
    };
    toast(`${linkLabel(l)}: ${said}`, "ok", { label: "Undo", onClick: undo });
  }
  const setDownload = (l, on) => mark(l, { download: on }, on ? "moved to Downloads" : "not a download any more");
  const setDone = (l, on) => mark(l, { downloaded: on }, on ? "downloaded" : "to download again");

  // Also link to…: the people it is not tied to yet, read when asked for.
  async function openAlso(l) {
    setDlgError(null);
    setAlso({ link: l, people: null });
    const mine = new Set((l.people || []).map(o => o.id));
    let people = [];
    try { people = (await getPeople()).filter(o => !mine.has(o.id)); } catch { /* an empty list: nothing to pick */ }
    setAlso(a => (a?.link.id === l.id ? { ...a, people } : a));
  }
  async function alsoTo(o) {
    const l = also.link;
    setDlgError(null);
    const problem = await call(() => addLinkPerson([l.id], o.id), "Could not link it.");
    if (problem) { setDlgError(problem); return; }
    setAlso(null);
    const undo = async () => {
      const back = await call(() => removeLinkPerson([l.id], o.id), "Could not undo.");
      if (back) toast(back, "err");
    };
    toast(`${linkLabel(l)}: also ${o.name}'s`, "ok", { label: "Undo", onClick: undo });
  }

  // A row, or the form in its place while the link is edited.
  function row(l, extra) {
    return edit.editing === l.id ? (
      <li key={l.id} className="link-row is-editing">
        <LinkForm link={l} busy={busy} submitLabel="Save" idPrefix={`person-link-${l.id}`}
                  onCancel={edit.close} onDirty={edit.onDirty}
                  onSubmit={async body => {
                    const problem = await call(() => updateLink(l.id, body), "Could not save the link.");
                    if (!problem) edit.close();
                    return problem;
                  }} />
      </li>
    ) : (
      <LinkRow key={l.id} link={l} here={p.id} busy={busy} showKind={false} onRemove={untie} onAlso={openAlso}
               onDownload={setDownload} onDone={setDone}
               onEdit={x => edit.open(x.id)} onDelete={x => { setDlgError(null); setRemoving(x); }} {...extra} />
    );
  }

  return (
    <>
      <section className="person-section person-links-section" hidden={searching && !links.length}>
        <div className="person-section-head">
          <h3 className="card-title">Links <span className="page-count">{links.length}</span></h3>
          {links.length > 0 && (
            <button type="button" className="btn-ghost person-links-copy" onClick={() => copyUrls(links, toast)}
                    title="Their links' addresses, one per line, to paste into another app">
              <Icon name="copy" size={14} />Copy URLs
            </button>
          )}
        </div>
        {links.length === 0 && !searching && (
          <div className="empty">No link saved for them yet: a Linktree, a Patreon, their site, an interview…</div>
        )}
        {groups.map(([name, group]) => group.length > 0 && (
          <div key={name} className="person-link-group">
            <h4 className="link-group-title">{name}</h4>
            <ul className="link-list">
              {group.map((l, i) => row(l, {
                onUp: !searching && { ok: i > 0, run: () => move(group, i, -1) },
                onDown: !searching && { ok: i < group.length - 1, run: () => move(group, i, 1) },
              }))}
            </ul>
          </div>
        ))}
        <div className="person-link-add" hidden={searching}>
          <LinkForm busy={busy} idPrefix="person-link-add" onDirty={onAddDirty} folded download={false}
                    onSubmit={async body => {
                      const problem = await call(() => createLink({ ...body, person: p.id }), "Could not add the link.");
                      if (!problem) {
                        toast(body.download ? "Link added to Downloads." : "Link added.");
                        if (body.download) setView("todo");
                      }
                      return problem;
                    }} />
        </div>
      </section>
      {anyDownload && (
        <section className="person-section person-downloads-section" hidden={searching && !downloads.length}>
          <div className="person-section-head">
            <h3 className="card-title">Downloads</h3>
            {!searching && (
              <div className="links-tabs is-small" role="group" aria-label="Show downloads">
                {[["todo", "To download", todo.length], ["done", "Downloaded", done.length]].map(([key, name, n]) => (
                  <button key={key} type="button" className={`links-tab${view === key ? " is-on" : ""}`} aria-pressed={view === key}
                          onClick={() => setView(key)}>
                    {name}<span className="links-tab-count">{fmtInt(n)}</span>
                  </button>
                ))}
              </div>
            )}
            {todo.length > 0 && (searching || view === "todo") && (
              <button type="button" className="btn-ghost person-links-copy" onClick={() => copyUrls(todo, toast)}
                      title="The addresses still to download, one per line, to paste into another app">
                <Icon name="copy" size={14} />Copy URLs
              </button>
            )}
          </div>
          {downloads.length === 0 ? (
            <div className="empty">{view === "done" ? "Nothing downloaded yet." : "Nothing left to download."}</div>
          ) : (
            <ul className="link-list">{downloads.map(l => row(l))}</ul>
          )}
        </section>
      )}
      {also && (
        <AssignDialog count={1} people={also.people} busy={busy} error={dlgError} also
                      onPick={alsoTo} onClose={() => { if (!busy) setAlso(null); }} />
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
        <p className="link-confirm">{removing?.title || removing?.label ? <>{removing.title || removing.label}<br /></> : null}<code>{removing?.url}</code></p>
        {sharedWith.length > 0 && (
          <p>It is {namesText(sharedWith.map(o => o.name))}&apos;s too: they lose it as well. Remove from this creator takes it from {p.name} only.</p>
        )}
      </ConfirmDialog>
      <DiscardDialog open={edit.asking} onDiscard={edit.discard} onKeep={edit.keep}>
        <p>The changes to the link you are editing are not saved.</p>
      </DiscardDialog>
    </>
  );
}
