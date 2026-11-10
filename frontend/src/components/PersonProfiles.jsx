import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { dismissSuggestion, linkAccounts, setSourcePerson, syncPerson } from "../lib/api";
import { useToast } from "../lib/toast";
import { authorFeedPath, fmtAgo, fmtBytes, fmtFullDate, fmtInt, fmtShortDate, platformLabel, plural, safeUrl } from "../lib/fmt";
import { accountKey, accountRef } from "../lib/people";
import { personProfiles, profileMatches, syncs } from "../lib/personProfiles";
import { optionsSummary, presetName } from "../lib/sourceOptions";
import { sourceName } from "../lib/sources";
import ActionMenu from "./ActionMenu";
import BioImport from "./BioImport";
import CreatorPicker from "./CreatorPicker";
import Icon from "./Icon";
import { CopyAddress } from "./Links";
import { useNoSync } from "./NoSync";
import PlatformIcon from "./PlatformIcon";
import {
  AddSource, NotSyncedChip, ProfileOnlyChip, RemoveSourceDialog, RenamePrompt, ScheduleLine, ScriptWarning, SourceHealth,
  SourcePersonDialog, SourcePreset, SourceStatus, SyncButton, ToolBadge, useSourceActions,
} from "./Sources";
import Suggestions from "./Suggestions";

/* What a source shows on a profile's line: the preset it follows, then
   where its sync stands, in one compact state: its badge and a few words
   when it failed (the sentence is the title, and in the details), else
   when it last synced, then when it syncs next. One chip in their place
   for one set to Don't sync, and for a profile only (its preset is in its
   ⋯ menu, as on a creator's card). ``act``: its useSourceActions. */
function SourceCells({ source: s, job, act }) {
  if (act.off) return <span className="person-profile-mark"><NotSyncedChip /></span>;
  if (act.none || act.only) return <span className="person-profile-mark">{act.only && <ProfileOnlyChip source={s} />}</span>;
  return (
    <>
      <SourcePreset source={s} job={job} act={act} />
      <span className="person-profile-state">
        <SourceStatus source={s} job={job} compact hint prompted />
        {!job && <ScheduleLine source={s} compact />}
      </span>
    </>
  );
}

// Its sync's log while it runs, and Sync.
function SourceSync({ source: s, job, act, onSync }) {
  return (
    <>
      {job && <Link to="/jobs" className="btn-ghost">Log</Link>}
      {!act.quiet && <SyncButton source={s} job={job} onSync={onSync} />}
    </>
  );
}

/* What must be read without opening the details: a profile that still
   needs its folder, a script that would fail the next sync, and a new
   name its tool reported, asked once (#235). */
function SourceNotes({ source: s, job, act, onSaved }) {
  const warn = !act.quiet && !!s.script_warning;
  const rename = !act.quiet && !!s.health?.rename;
  if (!s.needs_tool && !warn && !rename) return null;
  return (
    <span className="person-profile-notes">
      {s.needs_tool && <span className="source-note dim">No downloader yet: pick its folder in ⋯ → Download options to download it.</span>}
      {warn && <ScriptWarning source={s} />}
      {rename && <RenamePrompt source={s} job={job} onSaved={onSaved} />}
    </span>
  );
}

/* A source in a profile's details: its tool and folder, what its preset
   downloads, its schedule in full, and how its account is doing (the last
   good sync, the login, the output line, the last sync's whole message). */
function SourceDetails({ source: s, job, titled }) {
  const r = s.last_result;
  const none = s.tool === "none";
  const quiet = !!s.no_sync || !!s.profile_only;
  const summary = optionsSummary(s);
  return (
    <div className="person-profile-detail" data-source-id={s.id}>
      {titled && <span className="person-profile-detail-title">{sourceName(s)}</span>}
      <span className="person-profile-detail-line">
        {!none && <ToolBadge tool={s.tool} />}
        {s.folder ? <code className="source-folder" title={s.folder}>{s.folder}</code>
          : <span className="dim">{none ? "No downloader takes this site" : "No folder yet"}</span>}
      </span>
      {!none && <span className="person-profile-detail-line dim">{presetName(s)}{summary && !s.profile_only ? `: ${summary}` : ""}</span>}
      {!quiet && <ScheduleLine source={s} />}
      {!quiet && r?.message && !job && (
        <span className={`person-profile-detail-msg${r.state === "failed" ? " is-err" : ""}`}>
          {r.message}{s.last_sync_at ? ` · ${fmtAgo(s.last_sync_at)}` : ""}
        </span>
      )}
      {!quiet && <SourceHealth source={s} job={job} prompt={false} />}
    </div>
  );
}

/* What a profile's row keeps for whoever asks (its ⋯ menu's Details): the
   account's id, the other names and folders it is known by, the handles
   it had, and each source's tool, folder and health. */
function ProfileDetails({ id, account: a, sources, jobOf }) {
  const former = (a?.handles || []).filter(h => h.handle !== a.handle);
  const names = (a?.names || []).map(n => n.name).filter(n => n !== a.name);
  return (
    <div className="person-profile-details" id={id}>
      {a && (
        <span className="creator-sub person-profile-detail-line">
          id {a.id}
          {names.length > 0 && ` · also named ${names.join(", ")}`}
          {a.aliases?.length > 0 && ` · folder ${a.aliases.join(", ")}`}
        </span>
      )}
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
      {sources.map(s => <SourceDetails key={s.id} source={s} job={jobOf(s)} titled={sources.length > 1} />)}
    </div>
  );
}

// "@handle" and its name, or a source's own name; then one quiet line.
function ProfileId({ account: a, source: s }) {
  if (!a) {
    const name = sourceName(s);
    return (
      <span className="person-profile-id">
        <span className="creator-name" title={name}>{name}</span>
        <span className="creator-sub">
          {platformLabel(s.platform)}
          {s.profile_only ? " · shown as theirs, never downloaded" : !s.account && " · first sync not done yet"}
        </span>
      </span>
    );
  }
  const handle = `@${a.handle || a.id}`;
  const named = a.name && a.name !== a.handle;
  return (
    <span className="person-profile-id">
      <span className="creator-name" title={named ? `${handle} · ${a.name}` : handle}>
        {handle}
        {named && <span className="creator-sub"> · {a.name}</span>}
      </span>
      <span className="creator-sub">
        {platformLabel(a.platform)} · {a.count ? `${plural(a.count, "post")} · ${fmtBytes(a.bytes)}` : "no posts indexed"}
        {a.newest && <> · <span title={fmtFullDate(a.newest)}>newest {fmtAgo(a.newest)}</span></>}
      </span>
    </span>
  );
}

// The ⋯ menu's switch for the details, and whether they are open.
function useDetails() {
  const id = useId();
  const [open, setOpen] = useState(false);
  const item = { label: open ? "Hide details" : "Details", icon: "info", onSelect: () => setOpen(o => !o),
                 title: "Its id, other names, folder, tool and the last sync's output" };
  return { id, open, item };
}

/* A profile with one source, the usual one: the account (when it has one
   yet) and its source on one line. Copy, Sync and one ⋯ menu; the rest of
   what the two rows had is in the menu or in the details. */
function SourceProfile({ profile, personOff, busy, job, jobOf, onSync, onSaved, onLinked, onRemove, onUnlink, onNoSync }) {
  const { account: a, sources: [s] } = profile;
  const act = useSourceActions({ source: s, job, onSync, onSaved, onLinked });
  const details = useDetails();
  const name = a ? `@${a.handle || a.id}` : act.name;
  const noSync = useNoSync({ on: !!a?.no_sync, inherited: personOff, whom: a && { account: accountRef(a) }, name, onDone: onNoSync });
  const url = safeUrl(a?.url) || safeUrl(s.url);
  const { items } = act;
  return (
    <li className={`person-profile${act.off ? " is-nosync" : ""}${act.only ? " is-profile-only" : ""}`}
        data-profile={profile.key} data-source-id={s.id}>
      <div className="person-profile-line">
        <PlatformIcon platform={a?.platform || s.platform} />
        <ProfileId account={a} source={s} />
        <SourceCells source={s} job={job} act={act} />
        <span className="person-profile-actions">
          {url && <CopyAddress url={url} label={name} />}
          <SourceSync source={s} job={job} act={act} onSync={onSync} />
          <ActionMenu className="link-menu" label={`More for ${name}`} items={[
            url && { label: "Open profile", icon: "external", href: url, title: `Profile on ${platformLabel(s.platform)} (opens the site)` },
            a && { label: "Show feed", icon: "feed", to: authorFeedPath(a.platform, a), title: "This profile's posts" },
            details.item,
            "separator",
            items.options, items.preset, items.custom, items.history, a && noSync.item,
            "separator",
            items.move,
            { label: "Unlink", icon: "close", disabled: busy, onSelect: () => onUnlink(profile),
              title: `${name} is no longer theirs${a ? ", account and source" : ""}; no post or file is touched` },
            "separator",
            { label: act.only && !a ? "Remove" : "Remove source", icon: "trash", danger: true, disabled: !!job,
              onSelect: () => onRemove(s),
              title: job ? "Cancel its sync first" : act.only ? "No longer shown as theirs" : "Stop syncing it; files and posts stay" },
          ]} />
        </span>
      </div>
      <SourceNotes source={s} job={job} act={act} onSaved={onSaved} />
      {details.open && <ProfileDetails id={details.id} account={a} sources={[s]} jobOf={jobOf} />}
      {act.dialogs}
      {a && noSync.dialog}
    </li>
  );
}

// One of an account's several sources, on a line of its own under it.
function SourceLine({ source: s, job, onSync, onSaved, onRemove }) {
  const act = useSourceActions({ source: s, job, onSync, onSaved });
  const { items } = act;
  return (
    <li className={`person-profile-source${act.off ? " is-nosync" : ""}${act.only ? " is-profile-only" : ""}`} data-source-id={s.id}>
      <div className="person-profile-line">
        <span className="person-profile-source-name creator-sub" title={act.name}>{act.name}</span>
        <SourceCells source={s} job={job} act={act} />
        <span className="person-profile-actions">
          <SourceSync source={s} job={job} act={act} onSync={onSync} />
          <ActionMenu className="link-menu" label={`More for ${act.name}`} items={[
            items.open,
            "separator",
            items.options, items.preset, items.custom, items.history,
            "separator",
            { label: "Remove source", icon: "trash", danger: true, disabled: !!job, onSelect: () => onRemove(s),
              title: job ? "Cancel its sync first" : "Stop syncing it; files and posts stay" },
          ]} />
        </span>
      </div>
      <SourceNotes source={s} job={job} act={act} onSaved={onSaved} />
      {act.dialogs}
    </li>
  );
}

/* An account with no source (indexed, never synced from here), or with
   several (two tabs of an X profile): its own line, then one line per
   source. Move takes every source of it along. */
function AccountProfile({ profile, personOff, busy, jobOf, onSync, onSaved, onLinked, onRemove, onUnlink, onNoSync }) {
  const { account: a, sources } = profile;
  const toast = useToast();
  const details = useDetails();
  const [moving, setMoving] = useState(false);
  const name = `@${a.handle || a.id}`;
  const noSync = useNoSync({ on: !!a.no_sync, inherited: personOff, whom: { account: accountRef(a) }, name, onDone: onNoSync });
  const url = safeUrl(a.url);
  const off = !!a.no_sync || (sources.length > 0 && sources.every(s => s.no_sync));

  // The first one took the account along (SourcePersonDialog): the others follow it.
  async function moved(first) {
    try {
      for (const s of sources.filter(x => x.id !== first.id)) {
        const r = await setSourcePerson(s.id, first.person.id);
        if (!r?.ok) { toast(r?.error || `Could not move ${sourceName(s)}.`, "err"); break; }
      }
    } catch (err) {
      toast(err.message, "err");
    }
    onLinked?.(first);
  }

  return (
    <li className={`person-profile${off ? " is-nosync" : ""}`} data-profile={profile.key}>
      <div className="person-profile-line">
        <PlatformIcon platform={a.platform} />
        <ProfileId account={a} />
        {off && sources.length === 0 && <span className="person-profile-mark"><NotSyncedChip /></span>}
        <span className="person-profile-actions">
          {url && <CopyAddress url={url} label={name} />}
          <ActionMenu className="link-menu" label={`More for ${name}`} items={[
            url && { label: "Open profile", icon: "external", href: url, title: `Profile on ${platformLabel(a.platform)} (opens the site)` },
            { label: "Show feed", icon: "feed", to: authorFeedPath(a.platform, a), title: "This profile's posts" },
            details.item,
            "separator",
            noSync.item,
            "separator",
            sources.length > 0 && { label: "Move to creator…", icon: "users", onSelect: () => setMoving(true),
                                    title: `Show it with someone else, its ${plural(sources.length, "source")} with it` },
            { label: "Unlink", icon: "close", disabled: busy, onSelect: () => onUnlink(profile),
              title: `${name} is no longer theirs${sources.length ? `, its ${plural(sources.length, "source")} with it` : ""}; no post or file is touched` },
          ]} />
        </span>
      </div>
      {sources.length > 0 && (
        <ul className="person-profile-sources" aria-label={`Sources of ${name}`}>
          {sources.map(s => <SourceLine key={s.id} source={s} job={jobOf(s)} onSync={onSync} onSaved={onSaved} onRemove={onRemove} />)}
        </ul>
      )}
      {details.open && <ProfileDetails id={details.id} account={a} sources={sources} jobOf={jobOf} />}
      {moving && <SourcePersonDialog source={sources[0]} onClose={() => setMoving(false)} onLinked={moved} />}
      {noSync.dialog}
    </li>
  );
}

/* A person's profiles (#235), the one list of their page: an account of
   theirs and its source are one row (lib/personProfiles.js), where
   Accounts and Sources showed most of them twice. A row is who it is, one
   quiet line, the preset it follows, where its sync stands, Copy, Sync
   and one ⋯ menu; what it had beside is in the menu or its details.
   Under the list, the one way to add in sight (AddSource: a profile link
   or an Instagram name); the others (an indexed account, the "Also them?"
   suggestions, a link-in-bio page) fold behind "More ways to add", which
   says how many are suggested.

   Props: person; query (the header's search: the rows that match, the
   rest of the section hidden, never unmounted, so what is typed stays);
   sources (lib/sources.js useSources); authors (every account, for the
   picker); suggestApi (useApi of the suggestions); bioImport (Settings
   allows the fetch); onChanged (the person changed: reload them);
   onNoSync (a Don't sync switch moved); onDirty(bool) (a profile is typed
   and not added); onImported (a link-in-bio page gave them links). */
export default function PersonProfiles({ person: p, query = "", sources, authors, suggestApi, bioImport, onChanged, onNoSync, onDirty, onImported }) {
  const toast = useToast();
  const moreId = useId();
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState(null);     // the source to remove
  const [more, setMore] = useState(false);            // "More ways to add" is open
  const searching = !!query.trim();
  const all = sources.data?.sources || [];
  const theirs = all.filter(s => s.person?.id === p.id);
  const profiles = personProfiles(p.accounts, theirs);
  const shown = searching ? profiles.filter(x => profileMatches(x, query)) : profiles;
  const live = theirs.filter(syncs);
  const linked = new Set(p.accounts.map(accountKey));
  // Linked to nobody, with no account yet (never synced): one with an account comes along with it.
  const loose = all.filter(s => !s.person && !s.account);
  // Suggestions that would add accounts to this person.
  const suggested = (suggestApi.data?.suggestions || []).filter(s => s.person?.id === p.id);

  function changed() {
    onChanged();
    suggestApi.reload();
    sources.reload();                          // a source shows with its account's person
  }

  // A call that changes whose a profile is: its toast, then everything again.
  async function run(call, message, failure) {
    setBusy(true);
    try {
      const r = await call();
      if (!r?.ok) { toast(r?.error || failure, "err"); return; }
      toast(message);
      changed();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }
  const link = (body, message) => run(() => linkAccounts(p.id, body), message, "Could not change the profiles.");

  /* No longer theirs, the account and its sources: the account first (as
     the Accounts list's Unlink did), then each source, which may be theirs
     on its own (added from this page). One with no account: the source. */
  async function unlink({ account: a, sources: of }) {
    setBusy(true);
    try {
      if (a) {
        const r = await linkAccounts(p.id, { remove: [accountRef(a)] });
        if (!r?.ok) { toast(r?.error || "Could not unlink.", "err"); return; }
      }
      for (const s of of) {
        const r = await setSourcePerson(s.id, null);
        if (!r?.ok) { toast(r?.error || "Could not unlink.", "err"); return; }
      }
      toast(a ? `@${a.handle || a.id} unlinked.` : `${sourceName(of[0])} is no longer ${p.name}'s.`);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
      changed();
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

  const row = {
    personOff: p.no_sync ? p.name : null, busy, jobOf: sources.jobOf, onSync: sources.sync, onSaved: sources.reload,
    onLinked: changed, onRemove: setRemoving, onUnlink: unlink, onNoSync,
  };
  return (
    <section className="person-section person-profiles" aria-label="Profiles" hidden={searching && !shown.length}>
      <div className="person-section-head">
        <h3 className="card-title">Profiles <span className="page-count">
          {searching ? `${shown.length} of ${profiles.length}` : profiles.length}
        </span></h3>
        {!searching && live.length > 0 && (
          <button type="button" className="btn-primary" disabled={busy} onClick={syncAll}
                  title="Sync each profile of this person, one after another">
            <Icon name="refresh" size={14} /> Sync {live.length > 1 ? `all ${live.length}` : ""}
          </button>
        )}
      </div>
      {p.no_sync && !searching && (
        <p className="creator-sub nosync-note">
          Don&apos;t sync is on: nothing of theirs is downloaded and no profile can be added, until Sync again in the ⋯ menu at the top.
        </p>
      )}
      {profiles.length === 0 ? (
        <div className="empty">{sources.data ? "No profile yet. Add one below." : "Loading…"}</div>
      ) : (
        <ul className="person-profile-list">
          {shown.map(x => (x.sources.length === 1
            ? <SourceProfile key={x.key} profile={x} job={sources.jobOf(x.sources[0])} {...row} />
            : <AccountProfile key={x.key} profile={x} {...row} />))}
        </ul>
      )}
      <div className="person-profile-add" hidden={searching}>
        {!p.no_sync && (
          <>
            <h4 className="person-profile-add-title">Add profile</h4>
            <AddSource person={p.id} profile onAdded={sources.reload} onDirty={onDirty} />
          </>
        )}
        <button type="button" className="btn-link person-profile-more-toggle" aria-expanded={more} aria-controls={moreId}
                onClick={() => setMore(m => !m)}>
          <Icon name={more ? "chevDown" : "chevRight"} size={12} />
          More ways to add{suggested.length > 0 && ` · ${fmtInt(suggested.length)} suggested`}
        </button>
        {/* Hidden, not gone: what is typed in it stays while it is folded. */}
        <div className="person-profile-more" id={moreId} hidden={!more}>
          <div className="person-add">
            <CreatorPicker
              accounts={authors || []}
              sources={loose}
              exclude={linked}
              label="Add an account"
              allLabel="Add an account…"
              onChange={v => {
                if (v?.source) {
                  run(() => setSourcePerson(v.source.id, p.id), `${sourceName(v.source)} linked.`, "Could not link the source.");
                  return;
                }
                if (!v?.account) return;
                const a = v.account;
                const from = a.person && a.person.id !== p.id ? ` (moved from ${a.person.name})` : "";
                link({ add: [accountRef(a)] }, `@${a.handle || a.id} linked${from}.`);
              }}
            />
            <span className="creator-sub">An account FeedVault has indexed, or a source linked to nobody: adding it here takes it from anyone else.</span>
          </div>
          <Suggestions
            title="Also them?"
            data={{ suggestions: suggested }}
            busy={busy}
            onLink={s => {
              const add = s.accounts.filter(a => !a.person).map(accountRef);
              link({ add }, `${add.length} account${add.length === 1 ? "" : "s"} linked.`);
            }}
            onDismiss={dismiss}
          />
          <BioImport
            key={p.id}
            person={p}
            enabled={bioImport}
            onAdded={changed}
            onImported={onImported}
          />
        </div>
      </div>
      <RemoveSourceDialog source={removing} onRemove={sources.remove} onClose={() => setRemoving(null)} />
    </section>
  );
}
