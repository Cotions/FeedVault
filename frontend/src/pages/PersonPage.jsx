import { useCallback, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getAuthors, getConfig, getPeopleAttributes, getPerson, getSuggestions, getNew, markSeen, updatePerson, deletePerson,
  getPostsSummary } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useJobs } from "../lib/jobs";
import { useToast } from "../lib/toast";
import { fmtAgo, fmtBytes, fmtInt, plural } from "../lib/fmt";
import { cleanGender, sameLabels } from "../lib/people";
import { personProfiles, profileMatches } from "../lib/personProfiles";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DiscardDialog from "../components/DiscardDialog";
import ActionMenu from "../components/ActionMenu";
import StateChips from "../components/StateChips";
import { useNoSync } from "../components/NoSync";
import { useMute } from "../lib/useMute";
import { useSources } from "../lib/sources";
import PageHeader from "../components/PageHeader";
import PersonAbout from "../components/PersonAbout";
import PersonAvatar from "../components/PersonAvatar";
import AvatarDialog from "../components/AvatarDialog";
import PersonLinks from "../components/PersonLinks";
import PersonProfiles from "../components/PersonProfiles";
import { useUnsaved } from "../lib/unsaved";
import { linkMatches, useSearchName } from "../lib/search";

/* One creator: who they are (portrait, About), their profiles (accounts
   and sources, one list: components/PersonProfiles.jsx), links, downloads
   and notes. Feed is the one button of the hero; the rest of the app
   scoped to them (Review, Storage, Stats, Trash) is in the head's ⋯ menu,
   over Rename, Mute, Don't sync and Delete person. */
export default function PersonPage() {
  const { id } = useParams();
  const { refreshKey } = useScan();
  const toast = useToast();
  const navigate = useNavigate();
  const load = useCallback(() => getPerson(id), [id]);
  const { data: p, error, reload } = useApi(load, refreshKey);
  // The header's search (lib/search.js), in ?q=: their profiles and links
  // that match, and how many of their posts, in place of the page.
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
  const [about,    setAbout]    = useState({});       // edited aliases, gender, country, labels (absent: untouched)
  const [confirm,  setConfirm]  = useState(false);
  const [picture,  setPicture]  = useState(false);    // the picture dialog is open
  const [dlgError, setDlgError] = useState(null);
  const [linkEdits, setLinkEdits] = useState(false);  // a link being edited has changes
  const [newLink,   setNewLink]   = useState(false);  // the new link's form has something typed
  const [newSource, setNewSource] = useState(false);  // the new profile's field has something typed
  const sources = useSources();
  // The head's ⋯ menu: the app scoped to them, Rename, Mute, Don't sync and
  // Delete person. The rename form gives focus back to its button when it closes.
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
    about.aliases != null && !sameLabels(about.aliases, p.aliases || []) && ["aliases", about.aliases],
  ].filter(Boolean));
  const aboutDirty = Object.keys(aboutChanges);
  // A search on the page (?q=) hides what is typed, never drops it: it goes through.
  const unsaved = useUnsaved(notesDirty || renameDirty || aboutDirty.length > 0 || linkEdits || newLink || newSource,
    (from, to) => from.pathname === to.pathname);

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

  const scoped = (path, more) => `${path}?${new URLSearchParams({ person: p.id, ...more })}`;
  const notesValue = notes ?? p.notes;
  const fresh = (newApi.data?.by_person || []).find(r => r.id === p.id);
  // What a search finds of theirs, to say when it is nothing at all.
  const theirs = (sources.data?.sources || []).filter(s => s.person?.id === p.id);
  const profilesFound = searching ? personProfiles(p.accounts, theirs).filter(x => profileMatches(x, q)).length : 0;
  const linksFound = searching ? (p.links || []).filter(l => linkMatches(l, q)).length : 0;
  const postsFound = searching && postsApi.data?.q === q.trim() ? postsApi.data.posts : null;
  const nothing = searching && !profilesFound && !linksFound && postsFound === 0;

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
            { label: "Review", icon: "review", to: scoped("/review"), title: "Their posts not reviewed yet" },
            { label: "Re-review", icon: "refresh", to: scoped("/review", { again: "1" }),
              title: "All their posts again, the kept ones too, which stay kept unless you trash them" },
            { label: "Storage", icon: "disk", to: scoped("/storage"), title: "What their posts take on disk" },
            { label: "Stats", icon: "chart", to: scoped("/stats") },
            { label: "Trash", icon: "trash", to: scoped("/trash"), title: "Their trashed posts" },
            "separator",
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
            {nothing && <div className="empty">Nothing else of theirs matches: no profile or link.</div>}
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

        <PersonProfiles
          person={p}
          query={q}
          sources={sources}
          authors={authors}
          suggestApi={suggestApi}
          bioImport={config?.bio_import === true}
          onChanged={reload}
          onNoSync={onNoSync}
          onDirty={setNewSource}
          onImported={() => { reload(); refresh(); }}   // their Links block, and the nav's counts
        />

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
              newLink && "the new link", newSource && "the new profile"].filter(Boolean).join(", ")}.
          </p>
        </DiscardDialog>

        {picture && <AvatarDialog person={p} onChanged={reload} onClose={() => setPicture(false)} />}

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
