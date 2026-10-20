import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { bioImport, createSource, linkAccounts } from "../lib/api";
import { platformLabel, plural, safeUrl } from "../lib/fmt";
import { importedParts } from "../lib/links";
import { personPath } from "../lib/people";
import Icon from "./Icon";
import PlatformIcon from "./PlatformIcon";

const STATUS = {
  linked: "Linked to them",
  source: "Has a source linked to nobody",
  indexed: "Downloaded, linked to nobody",
  new: "Not downloaded yet",
};

/* A person's page: the accounts their link-in-bio page (linktr.ee and the
   like) lists, fetched by the backend on an explicit click, each added
   with the usual calls. The import also keeps the page's address and the
   links it shows in their Links: the backend saves them, the counts are
   said here, and onImported lets the page show them. Everything in the
   answer comes from someone else's page: shown as text only, and the one
   link is the profile address FeedVault builds itself (profile_url). Off
   until Settings turns it on.

   Folded behind a small button until wanted: opened, focus goes to its
   field (to the Settings link while it is off). Cancel closes it and
   forgets what it held; Escape closes it too, unless an address is typed
   and not imported yet. It stays open after an import, its results in it. */
export default function BioImport({ person, enabled, onAdded, onImported }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(null);       // "fetch", or the key of the one being added
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);   // { url, accounts, links: { saved, already, … } }
  const [imported, setImported] = useState(null); // the address as sent, once its import went through
  const [added, setAdded] = useState({});       // key → true
  const boxRef = useRef(null);
  const toggleRef = useRef(null);
  const focusTo = useRef(null);                 // after the next open or close: "box" or "toggle"
  const typed = !!url.trim() && url.trim() !== imported;

  useEffect(() => {
    const to = focusTo.current;
    focusTo.current = null;
    if (to === "toggle") toggleRef.current?.focus();
    else if (to === "box") boxRef.current?.querySelector('input:not(:disabled), a[href], button:not(:disabled)')?.focus();
  }, [open]);

  function show() { focusTo.current = "box"; setOpen(true); }
  function close() {
    setUrl(""); setError(null); setResult(null); setImported(null); setAdded({});
    focusTo.current = "toggle";
    setOpen(false);
  }

  async function load(e) {
    e.preventDefault();
    if (!enabled || !url.trim()) return;
    setBusy("fetch");
    setError(null);
    setResult(null);
    setAdded({});
    try {
      const sent = url.trim();
      const r = await bioImport(person.id, sent);
      if (!r?.ok) { setError(r?.error || "Could not import that page."); return; }
      setResult(r);
      setImported(sent);
      onImported?.(r);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  }

  async function add(a, key) {
    setBusy(key);
    setError(null);
    try {
      const r = a.status === "indexed"
        ? await linkAccounts(person.id, { add: [{ platform: a.account.platform, id: a.account.id }] })
        : await createSource({ target: a.url, person: person.id });
      if (!r?.ok) { setError(r?.error || "Could not add it."); return; }
      setAdded(m => ({ ...m, [key]: true }));
      onAdded?.(a);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  }

  if (!open) {
    return (
      <div className="bio-import-fold">
        <button ref={toggleRef} type="button" className="btn-secondary bio-import-open" onClick={show}
                title="Fetch their linktr.ee (or the like) page: the accounts it lists, and its links for their Links">
          <Icon name="download" size={14} />Import from link-in-bio…
        </button>
      </div>
    );
  }

  return (
    <div className="bio-import" ref={boxRef}
         onKeyDown={e => {
           if (e.key !== "Escape" || e.nativeEvent.isComposing || typed || busy) return;
           e.stopPropagation();
           close();
         }}>
      <h4 className="bio-import-title">Import accounts from a link-in-bio page</h4>
      {!enabled && (
        <p className="creator-sub bio-import-off">
          Off: FeedVault fetches no page unless you allow it.{" "}
          <Link to="/settings#bio-import" className="text-link">Turn it on in Settings</Link>
        </p>
      )}
      <form className="bio-import-form" onSubmit={load}>
        <input
          type="text"
          className="page-filter"
          aria-label="Link-in-bio page"
          placeholder="linktr.ee/name, beacons.ai/name…"
          value={url}
          maxLength={2000}
          disabled={!enabled}
          onChange={e => setUrl(e.target.value)}
        />
        <button type="submit" className="btn-secondary" disabled={!enabled || !!busy || !url.trim()}
                title={enabled ? "Fetches this one page now, lists the accounts it links to, and saves its links to their Links" : "Turn it on in Settings first"}>
          <Icon name="download" size={14} className={busy === "fetch" ? "spin" : ""} />
          {busy === "fetch" ? "Fetching…" : "Import"}
        </button>
        <button type="button" className="btn-ghost" onClick={close} disabled={!!busy}>
          {result ? "Close" : "Cancel"}
        </button>
      </form>
      {error && <div className="msg err" role="alert">{error}</div>}
      {result && (
        <div className="bio-import-result" aria-live="polite">
          <p className="creator-sub">
            {result.accounts.length ? `${plural(result.accounts.length, "account")} found` : "No account found"}
          </p>
          <p className="creator-sub bio-import-kept">{importedParts(result.links).join(" · ")}</p>
          {result.accounts.length > 0 && (
            <ul className="person-accounts bio-import-list">
              {result.accounts.map(a => {
                const key = `${a.platform}:${a.handle.toLowerCase()}`;
                const profile = safeUrl(a.profile_url, ["https:"]);
                const done = added[key];
                return (
                  <li key={key} className="person-account bio-import-row">
                    <PlatformIcon platform={a.platform} />
                    <span className="person-account-id">
                      <span className="creator-name" title={`@${a.handle}`}>@{a.handle}</span>
                      <span className="creator-sub">
                        {platformLabel(a.platform)} ·{" "}
                        {done ? "Added" : a.status === "other"
                          ? <>Linked to <Link to={personPath(a.person.id)} className="text-link">{a.person.name}</Link></>
                          : STATUS[a.status]}
                      </span>
                    </span>
                    {profile && (
                      <a href={profile} className="icon-btn" target="_blank" rel="noreferrer noopener"
                         title={`Profile on ${platformLabel(a.platform)} (opens the site)`}
                         aria-label={`@${a.handle} on ${platformLabel(a.platform)}`}>
                        <Icon name="external" size={15} />
                      </a>
                    )}
                    {(a.status === "indexed" || a.status === "new") && (
                      <button type="button" className="btn-primary" disabled={!!busy || done}
                              aria-label={`Add @${a.handle} on ${platformLabel(a.platform)}`}
                              title={a.status === "indexed" ? "Link this account to them" : "Add it as a source of theirs (nothing is downloaded until you sync)"}
                              onClick={() => add(a, key)}>
                        {done ? <><Icon name="check" size={14} /> Added</> : busy === key ? "Adding…" : "Add"}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
