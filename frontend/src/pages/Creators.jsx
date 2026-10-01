import { useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getAuthors, getPeople, getSuggestions, createPerson, mergePeople, linkAccounts, dismissSuggestion } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { useSelection } from "../lib/useSelection";
import { platformLabel, platformShort, authorFeedPath, fmtBytes, fmtInt } from "../lib/fmt";
import { accountKey, accountRef, accountText, matchedFormer, matches, personPath, personText, suggestName, REASONS } from "../lib/people";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import SelectionBar from "../components/SelectionBar";

const SUGGESTIONS_SHOWN = 4;

// Person names are unique; a default that is taken gets " 2", " 3"…
function freeName(name, people) {
  const taken = new Set(people.map(p => p.name.toLowerCase()));
  if (!taken.has(name.toLowerCase())) return name;
  for (let i = 2; ; i++) if (!taken.has(`${name} ${i}`.toLowerCase())) return `${name} ${i}`;
}

function PersonCard({ person: p, index, selectMode, selected, onToggle }) {
  const body = (
    <>
      <span className="avatar-letter" aria-hidden="true">{(p.name || "?").charAt(0).toUpperCase()}</span>
      <span className="creator-id">
        <span className="creator-name">{p.name}</span>
        <span className="person-chips">
          {p.accounts.length === 0 && <span className="creator-sub">No account linked</span>}
          {p.accounts.map(a => (
            <span key={accountKey(a)} className="chip platform-chip" title={platformLabel(a.platform)}>
              {platformShort(a.platform)} @{a.handle || a.id}
            </span>
          ))}
        </span>
      </span>
      <span className="person-stats">
        <span className="creator-count">{fmtInt(p.count)}</span>
        <span className="creator-sub">{fmtBytes(p.bytes)}</span>
      </span>
    </>
  );
  return (
    <div
      className={`creator-card person-card${selected ? " is-selected" : ""}`}
      style={{ animationDelay: `${Math.min(index, 30) * 25}ms` }}
    >
      {selectMode ? (
        <button type="button" className="creator-main" aria-pressed={selected} onClick={e => onToggle(e.shiftKey)}>
          {body}
        </button>
      ) : (
        <Link to={personPath(p.id)} className="creator-main" title={`Open ${p.name}`}>{body}</Link>
      )}
    </div>
  );
}

function AccountCard({ account: a, index, query, selectMode, selected, onToggle }) {
  const former = matchedFormer(a, query);
  const body = (
    <>
      <span className="avatar-letter" aria-hidden="true">{(a.handle || a.name || "?").charAt(0).toUpperCase()}</span>
      <span className="creator-id">
        <span className="creator-name">@{a.handle || a.id}</span>
        <span className="creator-sub">
          {a.name && a.name !== a.handle ? `${a.name} · ` : ""}{platformLabel(a.platform)}
          {former && ` · was @${former}`}
        </span>
      </span>
      <span className="creator-count">{a.count}</span>
    </>
  );
  const selectable = selectMode && a.id != null;
  return (
    <div
      className={`creator-card${selected ? " is-selected" : ""}`}
      style={{ animationDelay: `${Math.min(index, 30) * 25}ms` }}
    >
      {selectable ? (
        <button type="button" className="creator-main" aria-pressed={selected} onClick={e => onToggle(e.shiftKey)}>
          {body}
        </button>
      ) : (
        <Link to={authorFeedPath(a.platform, a)} className="creator-main" title={`Show posts by @${a.handle}`}>{body}</Link>
      )}
      {a.id != null && !selectMode && (
        <Link
          to={`/review?${new URLSearchParams({ platform: a.platform, author: a.id })}`}
          className="icon-btn creator-review"
          title={`Review @${a.handle}'s unreviewed posts`}
          aria-label={`Review @${a.handle}`}
        >
          <Icon name="review" size={15} />
        </Link>
      )}
    </div>
  );
}

// "Link?" cards: why these accounts look like one person, link or dismiss.
function Suggestions({ data, busy, onLink, onDismiss }) {
  const [all, setAll] = useState(false);
  const list = data?.suggestions || [];
  if (!list.length) return null;
  const shown = all ? list : list.slice(0, SUGGESTIONS_SHOWN);
  return (
    <section className="suggestions" aria-label="Link suggestions">
      <div className="suggestions-head">
        <h3 className="card-title">Link?</h3>
        <span className="page-count">{fmtInt(list.length)} suggested</span>
        <div className="page-head-spacer" />
        {list.length > SUGGESTIONS_SHOWN && (
          <button type="button" className="btn-ghost" onClick={() => setAll(a => !a)}>
            {all ? "Show fewer" : `Show all ${fmtInt(list.length)}`}
          </button>
        )}
      </div>
      <ul className="suggestion-list">
        {shown.map(s => (
          <li key={s.id} className="suggestion">
            <div className="suggestion-accounts">
              {s.accounts.map(a => (
                <Link key={accountKey(a)} to={authorFeedPath(a.platform, a)} className="chip platform-chip" title={`${platformLabel(a.platform)} · ${a.count} posts`}>
                  {platformShort(a.platform)} @{a.handle || a.id}
                </Link>
              ))}
              {s.person && <span className="suggestion-into">into <Link to={personPath(s.person.id)} className="text-link">{s.person.name}</Link></span>}
            </div>
            <div className="suggestion-why">
              {s.reasons.map(r => (
                <span key={`${r.reason}:${r.detail}`} className="suggestion-reason">
                  <b>{REASONS[r.reason] || r.reason}</b> {r.detail}
                </span>
              ))}
            </div>
            <div className="suggestion-actions">
              <button type="button" className="btn-primary" disabled={busy} onClick={() => onLink(s)}>
                <Icon name="check" size={14} /> Link
              </button>
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => onDismiss(s)}>Dismiss</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Creators() {
  const { refreshKey } = useScan();
  const toast = useToast();
  const navigate = useNavigate();
  const [version, setVersion] = useState(0);                 // bumped after a link changes people
  const key = `${refreshKey}:${version}`;
  const authorsApi = useApi(getAuthors, key);
  const peopleApi  = useApi(getPeople, key);
  const suggestApi = useApi(getSuggestions, key);
  const [filter, setFilter] = useState("");
  const [busy,   setBusy]   = useState(false);
  const [merge,  setMerge]  = useState(null);                // { name, error } while the dialog is open
  const nameRef = useRef(null);

  const data = authorsApi.data;
  const peopleAll = useMemo(() => peopleApi.data || [], [peopleApi.data]);
  const people = useMemo(
    () => filter ? peopleAll.filter(p => matches(personText(p), filter)) : peopleAll,
    [peopleAll, filter]);
  const unlinked = useMemo(() => {
    const list = (data || []).filter(a => !a.person);
    return filter ? list.filter(a => matches(accountText(a), filter)) : list;
  }, [data, filter]);
  const total = (data || []).reduce((s, a) => s + (a.count || 0), 0);

  // One selection over both grids, in the order shown.
  const items = useMemo(() => [
    ...people.map(p => ({ id: `person:${p.id}`, person: p })),
    ...unlinked.map(a => ({ id: a.id != null ? `account:${accountKey(a)}` : null, account: a })),
  ], [people, unlinked]);
  const sel = useSelection(items, { resetKey: `${filter}|${key}`, escapeBlocked: !!merge });
  const index = id => items.findIndex(i => i.id === id);
  const chosen = sel.selectedItems;
  const chosenPeople = chosen.filter(i => i.person).map(i => i.person);
  const chosenAccounts = chosen.filter(i => i.account).map(i => i.account);
  const canMerge = chosen.length > 0 && !(chosenPeople.length === 1 && chosenAccounts.length === 0);

  function reload() { setVersion(v => v + 1); }

  function askMerge() {
    const name = chosenPeople[0]?.name || freeName(suggestName(chosenAccounts) || "New person", peopleAll);
    setMerge({ name, error: null });
  }

  async function runMerge() {
    const name = merge.name.trim();
    const accounts = chosenAccounts.map(accountRef);
    setBusy(true);
    try {
      const r = chosenPeople.length
        ? await mergePeople(chosenPeople.map(p => p.id), { name, accounts })
        : await createPerson(name, accounts);
      if (!r?.ok) { setMerge(m => ({ ...m, error: r?.error || "Could not merge." })); return; }
      setMerge(null);
      sel.exit();
      navigate(personPath(r.person.id));
    } catch (err) {
      setMerge(m => ({ ...m, error: err.message }));
    } finally {
      setBusy(false);
    }
  }

  async function linkSuggestion(s) {
    const add = s.accounts.filter(a => !a.person).map(accountRef);
    setBusy(true);
    try {
      const r = s.person
        ? await linkAccounts(s.person.id, { add })
        : await createPerson(freeName(suggestName(s.accounts), peopleAll), s.accounts.map(accountRef));
      if (!r?.ok) { toast(r?.error || "Could not link.", "err"); return; }
      const name = r.person?.name || s.person?.name;
      toast(`Linked ${s.accounts.length} accounts as ${name}.`);
      reload();
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
      reload();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  const error = authorsApi.error || peopleApi.error;
  const loading = !data || !peopleApi.data;
  const empty = !people.length && !unlinked.length;

  return (
    <div className="card">
      <div className="page-head">
        <h2 className="page-title">Creators</h2>
        <span className="page-count">
          {data && peopleApi.data
            ? `${fmtInt(peopleAll.length)} people · ${fmtInt(data.length)} accounts · ${fmtInt(total)} posts`
            : "…"}
        </span>
        <div className="page-head-spacer" />
        {(data?.length > 8 || peopleAll.length > 8) && (
          <input
            type="text"
            className="page-filter"
            placeholder="Name or any handle…"
            aria-label="Filter creators"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          />
        )}
        {data?.length > 1 && (
          <button
            type="button"
            className={`btn-secondary select-toggle${sel.active ? " is-on" : ""}`}
            aria-pressed={sel.active}
            onClick={() => (sel.active ? sel.exit() : sel.enter())}
            title={sel.active ? "Leave select mode (Esc)" : "Select people and accounts to merge"}
          >
            <Icon name={sel.active ? "close" : "check"} size={14} />
            {sel.active ? "Done" : "Select"}
          </button>
        )}
      </div>

      {!sel.active && !filter && (
        <Suggestions data={suggestApi.data} busy={busy} onLink={linkSuggestion} onDismiss={dismiss} />
      )}

      {error && !data ? (
        <div className="empty">Could not load creators: {error.message}</div>
      ) : loading ? (
        <div className="empty">Loading…</div>
      ) : empty ? (
        <div className="empty">{filter ? "No creator matches." : "No creators yet. They appear once posts are indexed."}</div>
      ) : (
        <>
          {people.length > 0 && (
            <>
              <h3 className="card-title creators-section">People <span className="page-count">{fmtInt(people.length)}</span></h3>
              <div className="creator-grid">
                {people.map((p, i) => (
                  <PersonCard
                    key={p.id}
                    person={p}
                    index={i}
                    selectMode={sel.active}
                    selected={sel.isSelected(`person:${p.id}`)}
                    onToggle={shift => sel.toggle(index(`person:${p.id}`), shift)}
                  />
                ))}
              </div>
            </>
          )}
          {unlinked.length > 0 && (
            <>
              {people.length > 0 && (
                <h3 className="card-title creators-section">Accounts <span className="page-count">{fmtInt(unlinked.length)} not linked</span></h3>
              )}
              <div className="creator-grid">
                {unlinked.map((a, i) => (
                  <AccountCard
                    key={`${a.platform}:${a.id ?? a.handle}`}
                    account={a}
                    index={i}
                    query={filter}
                    selectMode={sel.active}
                    selected={sel.isSelected(`account:${accountKey(a)}`)}
                    onToggle={shift => sel.toggle(index(`account:${accountKey(a)}`), shift)}
                  />
                ))}
              </div>
            </>
          )}
        </>
      )}

      {sel.active && (
        <SelectionBar selection={sel} loaded={items.filter(i => i.id).length}>
          <button type="button" className="btn-primary" disabled={!canMerge || busy} onClick={askMerge}
                  title={canMerge ? "" : "Select accounts, or two people"}>
            <Icon name="users" size={14} /> Merge into one person
          </button>
        </SelectionBar>
      )}

      <ConfirmDialog
        open={!!merge}
        title={chosenPeople.length > 1 ? "Merge people" : chosenPeople.length ? `Add to ${chosenPeople[0].name}` : "New person"}
        confirmLabel={chosenPeople.length ? "Merge" : "Create"}
        busy={busy}
        error={merge?.error}
        onConfirm={runMerge}
        onCancel={() => setMerge(null)}
        initialFocus={nameRef}
        confirmDisabled={!merge?.name.trim()}
      >
        <p>
          {chosenPeople.length > 1 && `${chosenPeople.length} people become one; their notes are kept. `}
          {chosenAccounts.length > 0 && `${chosenAccounts.length} account${chosenAccounts.length === 1 ? "" : "s"} get linked. `}
          Posts and files are not touched.
        </p>
        <label className="dialog-field">
          <span>Name</span>
          <input
            ref={nameRef}
            type="text"
            className="page-filter"
            maxLength={64}
            value={merge?.name || ""}
            onChange={e => setMerge(m => ({ ...m, name: e.target.value, error: null }))}
            onKeyDown={e => { if (e.key === "Enter" && merge?.name.trim() && !busy) runMerge(); }}
          />
        </label>
      </ConfirmDialog>
    </div>
  );
}
