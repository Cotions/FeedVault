import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getAuthors, getPerson, updatePerson, deletePerson, linkAccounts } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { authorFeedPath, fmtAgo, fmtBytes, fmtFullDate, fmtInt, platformLabel, platformShort, safeUrl } from "../lib/fmt";
import { accountKey, accountRef, formerHandles } from "../lib/people";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import CreatorPicker from "../components/CreatorPicker";
import { AddSource, RemoveSourceDialog, SourceRow } from "../components/Sources";
import { useSources } from "../lib/sources";

function AccountRow({ account: a, busy, onUnlink }) {
  const url = safeUrl(a.url);
  const former = formerHandles(a);
  const names = (a.names || []).map(n => n.name).filter(n => n !== a.name);
  return (
    <li className="person-account">
      <span className="chip platform-chip" title={platformLabel(a.platform)}>{platformShort(a.platform)}</span>
      <span className="person-account-id">
        <span className="creator-name">
          @{a.handle || a.id}
          {a.name && a.name !== a.handle && <span className="creator-sub"> · {a.name}</span>}
        </span>
        <span className="creator-sub">
          {platformLabel(a.platform)} · id {a.id}
          {former.length > 0 && ` · was ${former.map(h => `@${h}`).join(", ")}`}
          {names.length > 0 && ` · also named ${names.join(", ")}`}
          {a.aliases?.length > 0 && ` · folder ${a.aliases.join(", ")}`}
        </span>
      </span>
      <span className="person-account-stats creator-sub">
        {a.count ? `${fmtInt(a.count)} posts · ${fmtBytes(a.bytes)}` : "No posts indexed"}
        {a.newest && <> · <span title={fmtFullDate(a.newest)}>newest {fmtAgo(a.newest)}</span></>}
      </span>
      {url && (
        <a href={url} className="icon-btn" target="_blank" rel="noreferrer noopener"
           title={`Profile on ${platformLabel(a.platform)} (opens the site)`} aria-label={`@${a.handle || a.id} on ${platformLabel(a.platform)}`}>
          <Icon name="external" size={15} />
        </a>
      )}
      <Link to={authorFeedPath(a.platform, a)} className="icon-btn" title="This account's posts" aria-label={`Posts by @${a.handle || a.id}`}>
        <Icon name="feed" size={15} />
      </Link>
      <button type="button" className="btn-ghost" disabled={busy} onClick={() => onUnlink(a)}>Unlink</button>
    </li>
  );
}

export default function PersonPage() {
  const { id } = useParams();
  const { refreshKey } = useScan();
  const toast = useToast();
  const navigate = useNavigate();
  const load = useCallback(() => getPerson(id), [id]);
  const { data: p, error, reload } = useApi(load, refreshKey);
  const { data: authors } = useApi(getAuthors, refreshKey);
  const [busy,     setBusy]     = useState(false);
  const [editName, setEditName] = useState(null);     // the name being typed, null when not renaming
  const [notes,    setNotes]    = useState(null);     // edited notes, null when untouched
  const [confirm,  setConfirm]  = useState(false);
  const [dlgError, setDlgError] = useState(null);
  const [removing, setRemoving] = useState(null);     // the source to remove
  const sources = useSources();

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
      navigate("/creators");
    } catch (err) {
      setDlgError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!p) {
    return (
      <div className="card">
        <div className="empty">
          {error?.status === 404 ? <>No such person. <Link to="/creators" className="text-link">All creators</Link></>
            : error ? `Could not load: ${error.message}` : "Loading…"}
        </div>
      </div>
    );
  }

  const mine = (sources.data?.sources || []).filter(s => s.person?.id === p.id);
  const scoped = path => `${path}?${new URLSearchParams({ person: p.id })}`;
  const notesValue = notes ?? p.notes;

  return (
    <div className="card person-page">
      <div className="page-head">
        <Link to="/creators" className="icon-btn" title="All creators" aria-label="All creators"><Icon name="back" size={16} /></Link>
        <span className="avatar-letter" aria-hidden="true">{p.name.charAt(0).toUpperCase()}</span>
        {editName != null ? (
          <form className="person-rename" onSubmit={e => { e.preventDefault(); save({ name: editName }, () => setEditName(null)); }}>
            <input
              autoFocus
              type="text"
              className="page-filter"
              aria-label="Name"
              maxLength={64}
              value={editName}
              onChange={e => setEditName(e.target.value)}
              onKeyDown={e => { if (e.key === "Escape") setEditName(null); }}
            />
            <button type="submit" className="btn-primary" disabled={busy || !editName.trim()}>Save</button>
            <button type="button" className="btn-ghost" onClick={() => setEditName(null)}>Cancel</button>
          </form>
        ) : (
          <>
            <h2 className="page-title">{p.name}</h2>
            <button type="button" className="btn-ghost" onClick={() => setEditName(p.name)}>Rename</button>
          </>
        )}
        <span className="page-count">
          {fmtInt(p.count)} posts · {fmtBytes(p.bytes)}{p.newest ? ` · newest ${fmtAgo(p.newest)}` : ""}
        </span>
        <div className="page-head-spacer" />
        <button type="button" className="btn-ghost person-delete" onClick={() => { setDlgError(null); setConfirm(true); }}>
          <Icon name="trash" size={14} /> Delete person
        </button>
      </div>

      <nav className="person-links" aria-label={`${p.name} across the app`}>
        <Link to={scoped("/")} className="btn-secondary review-link"><Icon name="feed" size={14} /> Feed</Link>
        <Link to={scoped("/review")} className="btn-secondary review-link"><Icon name="review" size={14} /> Review</Link>
        <Link to={scoped("/storage")} className="btn-secondary review-link"><Icon name="disk" size={14} /> Storage</Link>
        <Link to={scoped("/trash")} className="btn-secondary review-link"><Icon name="trash" size={14} /> Trash</Link>
      </nav>

      <section className="person-section">
        <h3 className="card-title">Accounts <span className="page-count">{p.accounts.length}</span></h3>
        {p.accounts.length === 0 ? (
          <div className="empty">No account linked yet. Add one below.</div>
        ) : (
          <ul className="person-accounts">
            {p.accounts.map(a => (
              <AccountRow
                key={accountKey(a)}
                account={a}
                busy={busy}
                onUnlink={a => change({ remove: [accountRef(a)] }, `@${a.handle || a.id} unlinked.`)}
              />
            ))}
          </ul>
        )}
        <div className="person-add">
          <CreatorPicker
            accounts={authors || []}
            exclude={linked}
            label="Add an account"
            allLabel="Add an account…"
            onChange={v => {
              if (!v?.account) return;
              const a = v.account;
              const from = a.person && a.person.id !== p.id ? ` (moved from ${a.person.name})` : "";
              change({ add: [accountRef(a)] }, `@${a.handle || a.id} linked${from}.`);
            }}
          />
          <span className="creator-sub">An account belongs to one person: adding it here takes it from anyone else.</span>
        </div>
      </section>

      <section className="person-section">
        <h3 className="card-title">Sources <span className="page-count">{mine.length}</span></h3>
        {mine.length === 0 ? (
          <div className="empty">
            {sources.data ? "Nothing to sync yet. Add a profile below to download their new posts from here." : "Loading…"}
          </div>
        ) : (
          <ul className="source-list">
            {mine.map(s => (
              <SourceRow key={s.id} source={s} job={sources.jobOf(s)} onSync={sources.sync} onRemove={setRemoving} />
            ))}
          </ul>
        )}
        <AddSource person={p.id} onAdded={sources.reload} />
      </section>

      <section className="person-section">
        <h3 className="card-title">Notes</h3>
        <textarea
          className="person-notes"
          aria-label="Notes"
          rows={4}
          maxLength={5000}
          placeholder="Anything worth remembering about them."
          value={notesValue}
          onChange={e => setNotes(e.target.value)}
        />
        {notes != null && notes !== p.notes && (
          <div className="person-notes-actions">
            <button type="button" className="btn-primary" disabled={busy} onClick={() => save({ notes }, () => setNotes(null))}>Save notes</button>
            <button type="button" className="btn-ghost" disabled={busy} onClick={() => setNotes(null)}>Discard</button>
          </div>
        )}
      </section>

      <RemoveSourceDialog source={removing} onRemove={sources.remove} onClose={() => setRemoving(null)} />

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
        Their {p.accounts.length} account{p.accounts.length === 1 ? "" : "s"} go back to unlinked.
        No post or file is touched.
      </ConfirmDialog>
    </div>
  );
}
