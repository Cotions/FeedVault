import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getAuthors, getConfig, getPeopleAttributes, getPerson, getSuggestions, getNew, markSeen, updatePerson, deletePerson, linkAccounts, dismissSuggestion, syncPerson,
  createLink, updateLink, deleteLink, addLinkPerson, removeLinkPerson, orderPersonLinks, setSourcePerson, getPostsSummary } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useJobs } from "../lib/jobs";
import { useToast } from "../lib/toast";
import { authorFeedPath, fmtAgo, fmtBytes, fmtFullDate, fmtInt, fmtShortDate, platformLabel, plural, safeUrl } from "../lib/fmt";
import { accountKey, accountRef, cleanGender, sameLabels } from "../lib/people";
import Icon from "../components/Icon";
import PlatformIcon from "../components/PlatformIcon";
import ConfirmDialog from "../components/ConfirmDialog";
import DiscardDialog from "../components/DiscardDialog";
import CreatorPicker from "../components/CreatorPicker";
import Suggestions from "../components/Suggestions";
import BioImport from "../components/BioImport";
import ActionMenu from "../components/ActionMenu";
import StateChips from "../components/StateChips";
import { useNoSync } from "../components/NoSync";
import { useMute } from "../lib/useMute";
import { AddSource, ProfileOnlyChip, RemoveSourceDialog, SourceOptionsDialog, SourceRow } from "../components/Sources";
import { sourceName, useSources } from "../lib/sources";
import PageHeader from "../components/PageHeader";
import PersonAbout from "../components/PersonAbout";
import PersonAvatar from "../components/PersonAvatar";
import AvatarDialog from "../components/AvatarDialog";
import { LinkForm, LinkRow } from "../components/Links";
import { copyUrls, linkLabel, namesText } from "../lib/links";
import { useOneEdit, useUnsaved } from "../lib/unsaved";
import { accountMatches, linkMatches, sourceMatches, useSearchName } from "../lib/search";

/* A profile only source of theirs (#218) with no account of its own yet
   (none indexed, or a site no downloader takes): shown with the accounts,
   its platform, handle and profile link, marked "Profile only". Its ⋯ menu
   opens the profile, says whether it downloads (Options), or removes it. */
function ProfileRow({ source: s, onSaved, onRemove }) {
  const [editing, setEditing] = useState(false);
  const url = safeUrl(s.url);
  const name = sourceName(s);
  return (
    <li className="person-account" data-source-id={s.id}>
      <PlatformIcon platform={s.platform} />
      <span className="person-account-id">
        <span className="creator-name" title={name}>{name}</span>
        <span className="state-line"><ProfileOnlyChip source={s} /></span>
        <span className="creator-sub">{platformLabel(s.platform)} · profile only: shown as theirs, never downloaded</span>
      </span>
      <ActionMenu label={`More for ${name}`} items={[
        url && { label: "Open profile", icon: "external", href: url, title: `Profile on ${platformLabel(s.platform)} (opens the site)` },
        { label: "Download options…", icon: "settings", onSelect: () => setEditing(true), title: "Whether this profile is downloaded" },
        "separator",
        { label: "Remove", icon: "trash", danger: true, onSelect: () => onRemove(s), title: "No longer shown as theirs" },
      ]} />
      {editing && <SourceOptionsDialog source={s} onClose={() => setEditing(false)} onSaved={onSaved} />}
    </li>
  );
}

/* One account of theirs: its profile on the site, its posts, Don't sync
   and Unlink in its ⋯ menu. personOff: the person's name when they are set
   to Don't sync, so this account is not synced whatever its own switch
   says (turned off on the person, not here). */
function AccountRow({ account: a, busy, onUnlink, personOff = null, onNoSync, profile = null }) {
  const url = safeUrl(a.url);
  const former = (a.handles || []).filter(h => h.handle !== a.handle);
  const names = (a.names || []).map(n => n.name).filter(n => n !== a.name);
  const handle = `@${a.handle || a.id}`;
  const named = a.name && a.name !== a.handle;
  const noSync = useNoSync({ on: !!a.no_sync, inherited: personOff, whom: { account: accountRef(a) }, name: handle, onDone: onNoSync });
  return (
    <li className="person-account">
      <PlatformIcon platform={a.platform} />
      <span className="person-account-id">
        <span className="creator-name" title={named ? `${handle} · ${a.name}` : handle}>
          {handle}
          {named && <span className="creator-sub"> · {a.name}</span>}
        </span>
        <StateChips name={handle} noSync={!!a.no_sync} />
        {profile && <span className="state-line"><ProfileOnlyChip source={profile} /></span>}
        <span className="creator-sub">
          {platformLabel(a.platform)} · id {a.id}
          {names.length > 0 && ` · also named ${names.join(", ")}`}
          {a.aliases?.length > 0 && ` · folder ${a.aliases.join(", ")}`}
        </span>
        {former.length > 0 && (
          <span className="creator-sub person-former">
            Former handles:{" "}
            {former.map((h, i) => (
              <span key={h.handle} title={h.first || h.last ? `seen ${fmtFullDate(h.first ?? h.last)} – ${fmtFullDate(h.last ?? h.first)}` : undefined}>
                {i > 0 && ", "}@{h.handle}{h.last != null && ` (until ${fmtShortDate(h.last)})`}
              </span>
            ))}
          </span>
        )}
      </span>
      <span className="person-account-stats creator-sub">
        {a.count ? `${fmtInt(a.count)} posts · ${fmtBytes(a.bytes)}` : "No posts indexed"}
        {a.newest && <> · <span title={fmtFullDate(a.newest)}>newest {fmtAgo(a.newest)}</span></>}
      </span>
      <ActionMenu label={`More for ${handle}`} items={[
        url && { label: "Open profile", icon: "external", href: url, title: `Profile on ${platformLabel(a.platform)} (opens the site)` },
        { label: "Show feed", icon: "feed", to: authorFeedPath(a.platform, a), title: "This account's posts" },
        noSync.item,
        "separator",
        { label: "Unlink", icon: "close", danger: true, disabled: busy, onSelect: () => onUnlink(a),
          title: `${handle} goes back to the accounts linked to nobody; no post or file is touched` },
      ]} />
      {noSync.dialog}
    </li>
  );
}

/* A person's saved links: socials first, then the others, each group in
   the person's order (up and down move a link within its group). Add one
   with the person preset; edit and delete in place. A link they share
   with others (a band's account, #215) shows the others; Remove takes it
   from this person only, Delete from everyone. onDirty(bool): whether
   the link being edited has unsaved changes; onAddDirty(bool): whether the
   new link's form has something typed. */
function PersonLinks({ person: p, query = "", onChanged, onDirty, onAddDirty }) {
  const toast = useToast();
  const { refresh } = useJobs();
  const [busy,     setBusy]     = useState(false);
  const edit = useOneEdit();                          // the link being edited, and whether it has changes
  const [removing, setRemoving] = useState(null);
  const [dlgError, setDlgError] = useState(null);
  const all = p.links || [];
  // While the header's search has something in it: the links it matches,
  // not moved up or down (their places are among all of them), nor added.
  const searching = !!query.trim();
  const links = searching ? all.filter(l => linkMatches(l, query)) : all;
  const groups = [["Socials", links.filter(l => l.kind === "social")], ["Other", links.filter(l => l.kind !== "social")]];
  useEffect(() => { onDirty(edit.dirty); }, [edit.dirty, onDirty]);
  const sharedWith = (removing?.people || []).filter(o => o.id !== p.id);

  async function call(fn, failed) {
    setBusy(true);
    try {
      const r = await fn();
      if (!r?.ok) return r?.id != null ? "That address is saved already, as another link (see Links)." : r?.error || failed;
      onChanged();
      refresh();                                      // the nav's Unsorted count
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

  return (
    <section className="person-section person-links-section" hidden={searching && !links.length}>
      <div className="person-section-head">
        <h3 className="card-title">Links <span className="page-count">{links.length}</span></h3>
        {links.length > 0 && (
          <button type="button" className="btn-secondary" onClick={() => copyUrls(links, toast)}
                  title="Their links' addresses, one per line, to paste into another app">
            <Icon name="copy" size={14} />Copy URLs
          </button>
        )}
      </div>
      {all.length === 0 && <div className="empty">No link saved for them yet: a Linktree, a Patreon, their site, an interview…</div>}
      {groups.map(([name, group]) => group.length > 0 && (
        <div key={name} className="person-link-group">
          <h4 className="link-group-title">{name}</h4>
          <ul className="link-list">
            {group.map((l, i) => edit.editing === l.id ? (
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
              <LinkRow key={l.id} link={l} here={p.id} busy={busy} onRemove={untie}
                       onUp={!searching && { ok: i > 0, run: () => move(group, i, -1) }}
                       onDown={!searching && { ok: i < group.length - 1, run: () => move(group, i, 1) }}
                       onEdit={x => edit.open(x.id)} onDelete={x => { setDlgError(null); setRemoving(x); }} />
            ))}
          </ul>
        </div>
      ))}
      <div className="person-link-add" hidden={searching}>
        <h4 className="link-group-title">Add a link</h4>
        <LinkForm busy={busy} idPrefix="person-link-add" onDirty={onAddDirty} folded
                  onSubmit={async body => {
                    const problem = await call(() => createLink({ ...body, person: p.id }), "Could not add the link.");
                    if (!problem) toast("Link added.");
                    return problem;
                  }} />
      </div>
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
          <p>It is {namesText(sharedWith.map(o => o.name))}&apos;s too: they lose it as well. Remove takes it from {p.name} only.</p>
        )}
      </ConfirmDialog>
      <DiscardDialog open={edit.asking} onDiscard={edit.discard} onKeep={edit.keep}>
        <p>The changes to the link you are editing are not saved.</p>
      </DiscardDialog>
    </section>
  );
}

export default function PersonPage() {
  const { id } = useParams();
  const { refreshKey } = useScan();
  const toast = useToast();
  const navigate = useNavigate();
  const load = useCallback(() => getPerson(id), [id]);
  const { data: p, error, reload } = useApi(load, refreshKey);
  // The header's search (lib/search.js), in ?q=: their accounts, sources and
  // links that match, and how many of their posts, in place of the page.
  // What is typed elsewhere on it stays, hidden, until the search is cleared.
  const [params] = useSearchParams();
  const q = params.get("q") || "";
  const searching = !!q.trim();
  useSearchName(p?.name);
  // Their posts the Feed would show for it: counted, the Feed one click away.
  const postsLoad = useCallback(() => (searching
    ? getPostsSummary({ person: id, q: q.trim() }).then(r => ({ ...r, q: q.trim() }))
    : Promise.resolve(null)), [searching, id, q]);
  const postsApi = useApi(postsLoad, refreshKey);
  const { data: authors } = useApi(getAuthors, refreshKey);
  const suggestApi = useApi(getSuggestions, refreshKey);
  const { data: config } = useApi(getConfig, refreshKey);
  const attrApi = useApi(getPeopleAttributes, refreshKey);
  // Their new posts, again whenever the jobs poll's total moves.
  const { newCount, started, refresh } = useJobs();
  const newApi = useApi(getNew, `${refreshKey}:${newCount}`);
  const [busy,     setBusy]     = useState(false);
  const [editName, setEditName] = useState(null);     // the name being typed, null when not renaming
  const [notes,    setNotes]    = useState(null);     // edited notes, null when untouched
  const [about,    setAbout]    = useState({});       // edited gender, country, labels (absent: untouched)
  const [confirm,  setConfirm]  = useState(false);
  const [picture,  setPicture]  = useState(false);    // the picture dialog is open
  const [dlgError, setDlgError] = useState(null);
  const [removing, setRemoving] = useState(null);     // the source to remove
  const [linkEdits, setLinkEdits] = useState(false);  // a link being edited has changes
  const [newLink,   setNewLink]   = useState(false);  // the new link's form has something typed
  const [newSource, setNewSource] = useState(false);  // the new source's field has something typed
  const sources = useSources();
  // The head's ⋯ menu: Rename, Mute, Don't sync and Delete person. The
  // rename form gives focus back to its button when it closes.
  const moreRef = useRef(null);
  const muted = !!p && !!newApi.data?.muted?.people?.includes(p.id);
  const mute = useMute({ muted, whom: { person: p?.id }, name: p?.name, onDone: () => { newApi.reload(); started(); } });
  const onNoSync = () => { reload(); sources.reload(); suggestApi.reload(); };
  const noSync = useNoSync({ on: !!p?.no_sync, whom: { person: p?.id }, name: p?.name, onDone: onNoSync });
  function endRename() {
    setEditName(null);
    moreRef.current?.focus({ preventScroll: true });
  }

  // What was typed and not saved, asked about before the app leaves the page.
  const notesDirty  = !!p && notes != null && notes !== p.notes;
  const renameDirty = !!p && editName != null && editName !== p.name;
  // What of the About fields changed: the gender as the backend would
  // store it (spaces collapsed, a preset in its case), labels as typed.
  const aboutChanges = !p ? {} : Object.fromEntries([
    about.gender != null && cleanGender(about.gender) !== p.gender && ["gender", about.gender],
    about.country != null && about.country !== p.country && ["country", about.country],
    about.labels != null && !sameLabels(about.labels, p.labels) && ["labels", about.labels],
  ].filter(Boolean));
  const aboutDirty = Object.keys(aboutChanges);
  // A search on the page (?q=) hides what is typed, never drops it: it goes through.
  const unsaved = useUnsaved(notesDirty || renameDirty || aboutDirty.length > 0 || linkEdits || newLink || newSource,
    (from, to) => from.pathname === to.pathname);

  const linked = useMemo(() => new Set((p?.accounts || []).map(accountKey)), [p]);

  async function save(changes, done) {
    setBusy(true);
    try {
      const r = await updatePerson(p.id, changes);
      if (!r?.ok) { toast(r?.error || "Could not save.", "err"); return; }
      done?.();
      reload();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function change(body, message) {
    setBusy(true);
    try {
      const r = await linkAccounts(p.id, body);
      if (!r?.ok) { toast(r?.error || "Could not change the accounts.", "err"); return; }
      toast(message);
      reload();
      suggestApi.reload();
      sources.reload();                        // a source shows with its account's person
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  // A source linked to nobody, with no account yet (never synced): one
  // with an account comes along with it, from the accounts above.
  async function adoptSource(s) {
    setBusy(true);
    try {
      const r = await setSourcePerson(s.id, p.id);
      if (!r?.ok) { toast(r?.error || "Could not link the source.", "err"); return; }
      toast(`${sourceName(s)} linked.`);
      reload();
      sources.reload();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setDlgError(null);
    try {
      const r = await deletePerson(p.id);
      if (!r?.ok) { setDlgError(r?.error || "Could not delete."); return; }
      toast(`${p.name} deleted; ${r.unlinked} account${r.unlinked === 1 ? "" : "s"} unlinked.`);
      if (p.links?.length) started();                 // their links are Unsorted now: the nav's count
      unsaved.leave(() => navigate("/creators"));
    } catch (err) {
      setDlgError(err.message);
    } finally {
      setBusy(false);
    }
  }

  // Each source through the normal queue: one sync per platform at a time,
  // the tool's pause between two.
  async function syncAll() {
    setBusy(true);
    try {
      const r = await syncPerson(p.id);
      if (!r?.ok) { toast(r?.error || "Could not sync.", "err"); return; }
      const n = r.jobs.length;
      const parts = [n ? `${n} sync${n === 1 ? "" : "s"} queued` : "Nothing queued"];
      if (r.skipped) parts.push(`${r.skipped} already queued or running`);
      if (r.not_synced) parts.push(`${r.not_synced} set to Don't sync`);
      if (r.errors.length) parts.push(`${r.errors.length} refused: ${r.errors[0].error}`);
      toast(`${parts.join("; ")}.`, r.errors.length && !n ? "err" : undefined);
      sources.reload();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  // Up to the newest one counted: a post indexed since stays new.
  async function markMineSeen(fresh) {
    setBusy(true);
    try {
      const r = await markSeen(fresh.until, { person: p.id });
      if (!r?.ok) { toast(r?.error || "Could not mark them seen.", "err"); return; }
      toast(`${plural(fresh.count, "new post")} of ${p.name} marked seen.`);
      newApi.reload();
      started();                               // the sidebar's count, now
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function dismiss(s) {
    setBusy(true);
    try {
      const r = await dismissSuggestion(s.id);
      if (!r?.ok) { toast(r?.error || "Could not dismiss.", "err"); return; }
      suggestApi.reload();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  // Before the person loads, or when they cannot: the same head, with a
  // stand-in title, so nothing jumps when they come.
  const back = <Link to="/creators" className="btn-secondary btn-back"><Icon name="back" size={15} />Creators</Link>;
  if (!p) {
    return (
      <>
        <PageHeader title="Person" back={back} />
        <div className="card">
          <div className="empty">
            {error?.status === 404 ? <>No such person. <Link to="/creators" className="text-link">All creators</Link></>
              : error ? `Could not load: ${error.message}` : "Loading…"}
          </div>
        </div>
      </>
    );
  }

  // Profile only (#218): shown with the accounts, on the account it is of
  // or on a row of its own, never in Sources (nothing to sync).
  const theirs = (sources.data?.sources || []).filter(s => s.person?.id === p.id);
  const mine = theirs.filter(s => !s.profile_only);
  const profiles = theirs.filter(s => s.profile_only);
  const profileOf = new Map(profiles.filter(s => s.account).map(s => [accountKey(s.account), s]));
  const known = new Set(p.accounts.map(accountKey));
  const ownRows = profiles.filter(s => !s.account || !known.has(accountKey(s.account)));
  const profilesShown = searching ? ownRows.filter(s => sourceMatches(s, q)) : ownRows;
  const loose = (sources.data?.sources || []).filter(s => !s.person && !s.account);
  // Suggestions that would add accounts to this person.
  const suggested = (suggestApi.data?.suggestions || []).filter(s => s.person?.id === p.id);
  const scoped = (path, more) => `${path}?${new URLSearchParams({ person: p.id, ...more })}`;
  const notesValue = notes ?? p.notes;
  const fresh = (newApi.data?.by_person || []).find(r => r.id === p.id);
  // Set to Don't sync, they or one account: those sources are kept, never run.
  const live = mine.filter(s => !s.no_sync);
  const accounts = searching ? p.accounts.filter(a => accountMatches(a, q)) : p.accounts;
  const sourcesShown = searching ? mine.filter(s => sourceMatches(s, q)) : mine;
  const linksFound = searching ? (p.links || []).filter(l => linkMatches(l, q)).length : 0;
  const postsFound = searching && postsApi.data?.q === q.trim() ? postsApi.data.posts : null;
  const nothing = searching && !accounts.length && !profilesShown.length && !sourcesShown.length && !linksFound && postsFound === 0;

  return (
    <>
      <PageHeader
        title={p.name}
        heading={editName != null && (
          <form className="person-rename" onSubmit={e => { e.preventDefault(); save({ name: editName }, endRename); }}>
            <input
              autoFocus
              type="text"
              className="page-filter"
              aria-label="Name"
              maxLength={64}
              value={editName}
              onChange={e => setEditName(e.target.value)}
              onKeyDown={e => { if (e.key === "Escape") endRename(); }}
            />
            <button type="submit" className="btn-primary" disabled={busy || !editName.trim()}>Save</button>
            <button type="button" className="btn-ghost" onClick={endRename}>Cancel</button>
          </form>
        )}
        sub={`${fmtInt(p.count)} posts · ${fmtBytes(p.bytes)}${p.newest ? ` · newest ${fmtAgo(p.newest)}` : ""}`}
        back={back}
        actions={(
          <ActionMenu label={`More for ${p.name}`} buttonRef={moreRef} items={[
            editName == null && { label: "Rename", icon: "pencil", onSelect: () => setEditName(p.name) },
            newApi.data && mute,
            noSync.item,
            "separator",
            { label: "Delete person", icon: "trash", danger: true, onSelect: () => { setDlgError(null); setConfirm(true); },
              title: `Their ${plural(p.accounts.length, "account")} ${p.accounts.length === 1 ? "goes" : "go"} back to unlinked; no post or file is touched` },
          ]} />
        )}
      />
      <div className="card person-page">
        {/* Searching: how many of their posts match, first. */}
        {searching && (
          <section className="person-section person-search" aria-label={`Search results for ${q.trim()}`}>
            <h3 className="card-title">Posts</h3>
            <p className="creator-sub">
              {postsFound == null ? "Searching their posts…" : (
                <>
                  {plural(postsFound, "post")} of theirs {postsFound === 1 ? "matches" : "match"} “{q.trim()}”.{" "}
                  {postsFound > 0 && (
                    <Link to={scoped("/", { q: q.trim() })} className="text-link">Show in the Feed</Link>
                  )}
                </>
              )}
            </p>
            {nothing && <div className="empty">Nothing else of theirs matches: no account, source or link.</div>}
          </section>
        )}
        {/* Their portrait (2:3, as Stash shows a performer) beside who they
            are, at the top of the card: in the page's head it would move
            the title off where every page has it (#93). */}
        <div className="person-hero" hidden={searching}>
          <button type="button" className="avatar-edit is-portrait" onClick={() => setPicture(true)}
                  title={p.avatar ? "Change picture" : "Add a picture"}
                  aria-label={p.avatar ? `Change ${p.name}'s picture` : `Add a picture of ${p.name}`}>
            <PersonAvatar person={p} size="large" />
            <span className="avatar-edit-badge" aria-hidden="true"><Icon name="pencil" size={13} /></span>
          </button>
          <div className="person-hero-main">
            <div className="person-bar">
              <nav className="person-links" aria-label={`${p.name} across the app`}>
                <Link to={scoped("/")} className="btn-secondary review-link"><Icon name="feed" size={14} /> Feed</Link>
                {/* Re-review is an icon joined to Review: in words it pushes Mute off this line at 1280. */}
                <span className="link-pair">
                  <Link to={scoped("/review")} className="btn-secondary review-link"><Icon name="review" size={14} /> Review</Link>
                  <Link to={scoped("/review", { again: "1" })} className="btn-secondary review-link" aria-label="Re-review"
                        title="Re-review: all their posts again, the kept ones too, which stay kept unless you trash them">
                    <Icon name="refresh" size={14} />
                  </Link>
                </span>
                <Link to={scoped("/storage")} className="btn-secondary review-link"><Icon name="disk" size={14} /> Storage</Link>
                <Link to={scoped("/stats")} className="btn-secondary review-link"><Icon name="chart" size={14} /> Stats</Link>
                <Link to={scoped("/trash")} className="btn-secondary review-link"><Icon name="trash" size={14} /> Trash</Link>
              </nav>

              <div className="person-new">
                {fresh?.count > 0 && (
                  <>
                    <Link to={`/?${new URLSearchParams({ person: p.id, new: "1" })}`} className="side-badge side-new-inline"
                          title="Their posts indexed since you last marked them seen">
                      {fmtInt(fresh.count)} new
                    </Link>
                    <button type="button" className="btn-secondary" disabled={busy} onClick={() => markMineSeen(fresh)}
                            title="Their new posts stop being new; everyone else's stay">
                      <Icon name="check" size={14} /> Mark seen
                    </button>
                  </>
                )}
                {/* What the ⋯ menu's switches are set to, where Mute was. */}
                <StateChips name={p.name} muted={muted} noSync={!!p.no_sync}
                            noSyncTitle={`${p.name} is set to Don't sync: nothing of theirs is downloaded. Sync again from the ⋯ menu at the top`} />
              </div>
            </div>

            <PersonAbout
              person={p}
              attributes={attrApi.data}
              draft={about}
              onDraft={changes => setAbout(a => ({ ...a, ...changes }))}
              dirty={aboutDirty.length > 0}
              busy={busy}
              onSave={() => save(aboutChanges, () => { setAbout({}); attrApi.reload(); })}
              onDiscard={() => setAbout({})}
              onRate={rating => save({ rating })}
            />
          </div>
        </div>

        <section className="person-section" hidden={searching && !accounts.length && !profilesShown.length}>
          <h3 className="card-title">Accounts <span className="page-count">
            {searching ? `${accounts.length + profilesShown.length} of ${p.accounts.length + ownRows.length}` : p.accounts.length + ownRows.length}
          </span></h3>
          {p.accounts.length + ownRows.length === 0 ? (
            <div className="empty">No account linked yet. Add one below.</div>
          ) : (
            <ul className="person-accounts">
              {accounts.map(a => (
                <AccountRow
                  key={accountKey(a)}
                  account={a}
                  busy={busy}
                  personOff={p.no_sync ? p.name : null}
                  onNoSync={onNoSync}
                  onUnlink={a => change({ remove: [accountRef(a)] }, `@${a.handle || a.id} unlinked.`)}
                  profile={profileOf.get(accountKey(a)) || null}
                />
              ))}
              {profilesShown.map(s => <ProfileRow key={`source:${s.id}`} source={s} onSaved={sources.reload} onRemove={setRemoving} />)}
            </ul>
          )}
          <div hidden={searching}>
          <div className="person-add">
            <CreatorPicker
              accounts={authors || []}
              sources={loose}
              exclude={linked}
              label="Add an account"
              allLabel="Add an account…"
              onChange={v => {
                if (v?.source) { adoptSource(v.source); return; }
                if (!v?.account) return;
                const a = v.account;
                const from = a.person && a.person.id !== p.id ? ` (moved from ${a.person.name})` : "";
                change({ add: [accountRef(a)] }, `@${a.handle || a.id} linked${from}.`);
              }}
            />
            <span className="creator-sub">An account belongs to one person: adding it here takes it from anyone else. A source linked to nobody that never synced is listed too.</span>
          </div>
          <Suggestions
            title="Also them?"
            data={{ suggestions: suggested }}
            busy={busy}
            onLink={s => {
              const add = s.accounts.filter(a => !a.person).map(accountRef);
              change({ add }, `${add.length} account${add.length === 1 ? "" : "s"} linked.`);
            }}
            onDismiss={dismiss}
          />
          <BioImport
            key={p.id}
            person={p}
            enabled={config?.bio_import === true}
            onAdded={() => { reload(); suggestApi.reload(); sources.reload(); }}
            onImported={() => { reload(); refresh(); }}   // their Links block, and the nav's counts
          />
          </div>
        </section>

        <section className="person-section" hidden={searching && !sourcesShown.length}>
          <div className="person-section-head">
            <h3 className="card-title">Sources <span className="page-count">{searching ? `${sourcesShown.length} of ${mine.length}` : mine.length}</span></h3>
            {!searching && live.length > 0 && (
              <button type="button" className="btn-primary" disabled={busy} onClick={syncAll}
                      title="Sync each source of this person, one after another">
                <Icon name="refresh" size={14} /> Sync {live.length > 1 ? `all ${live.length}` : ""}
              </button>
            )}
          </div>
          {p.no_sync && !searching && (
            <p className="creator-sub nosync-note">
              Don&apos;t sync is on: FeedVault downloads nothing of theirs, no source can be added, and{" "}
              {mine.length ? "their sources stay listed but never run" : "none is suggested"}. What is downloaded stays.
              Turn it off with Sync again, in the ⋯ menu at the top of the page.
            </p>
          )}
          {mine.length === 0 ? (
            <div className="empty">
              {sources.data ? "Nothing to sync yet. Add a profile below to download their new posts from here." : "Loading…"}
            </div>
          ) : (
            <ul className="source-list">
              {sourcesShown.map(s => (
                <SourceRow key={s.id} source={s} job={sources.jobOf(s)} onSync={sources.sync} onRemove={setRemoving}
                           onSaved={sources.reload} onLinked={() => { reload(); sources.reload(); }} />
              ))}
            </ul>
          )}
          {!p.no_sync && <div hidden={searching}><AddSource person={p.id} onAdded={sources.reload} onDirty={setNewSource} /></div>}
        </section>

        <PersonLinks person={p} query={q} onChanged={reload} onDirty={setLinkEdits} onAddDirty={setNewLink} />

        <section className="person-section" hidden={searching}>
          <h3 className="card-title">Notes</h3>
          <textarea
            className="person-notes"
            aria-label="Notes"
            rows={4}
            maxLength={5000}
            placeholder="Anything worth remembering about them."
            value={notesValue}
            onChange={e => setNotes(e.target.value)}
            // The whole box in sight, ring included, when Tab lands on it at
            // the bottom of the window: the browser only brings its caret in.
            onFocus={e => e.target.scrollIntoView({ block: "nearest" })}
          />
          {notesDirty && (
            <div className="person-notes-actions">
              <button type="button" className="btn-primary" disabled={busy} onClick={() => save({ notes }, () => setNotes(null))}>Save notes</button>
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => setNotes(null)}>Discard</button>
            </div>
          )}
        </section>

        <DiscardDialog open={unsaved.asking} onDiscard={unsaved.discard} onKeep={unsaved.keep}>
          <p>
            Not saved for {p.name}:{" "}
            {[notesDirty && "the notes", renameDirty && "the new name",
              ...aboutDirty.map(k => `the ${k}`), linkEdits && "the changes to a link",
              newLink && "the new link", newSource && "the new source"].filter(Boolean).join(", ")}.
          </p>
        </DiscardDialog>

        {picture && <AvatarDialog person={p} onChanged={reload} onClose={() => setPicture(false)} />}

        <RemoveSourceDialog source={removing} onRemove={sources.remove} onClose={() => setRemoving(null)} />
        {noSync.dialog}

        <ConfirmDialog
          open={confirm}
          title={`Delete ${p.name}?`}
          confirmLabel="Delete person"
          danger
          busy={busy}
          error={dlgError}
          onConfirm={remove}
          onCancel={() => setConfirm(false)}
        >
          Their {plural(p.accounts.length, "account")} {p.accounts.length === 1 ? "goes" : "go"} back to unlinked.
          No post or file is touched.
        </ConfirmDialog>
      </div>
    </>
  );
}
