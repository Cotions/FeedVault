import { useCallback, useMemo } from "react";
import { Link } from "react-router-dom";
import { getLinks } from "../lib/api";
import { useApi } from "../lib/useApi";
import { authorFeedPath, fmtInt, platformLabel, plural } from "../lib/fmt";
import { accountKey, personPath } from "../lib/people";
import { LINKS_Q_MAX, linkMatches, searchCreators } from "../lib/search";
import Icon from "./Icon";
import PlatformIcon from "./PlatformIcon";
import PersonAvatar from "./PersonAvatar";
import { LinkRow } from "./Links";

// Each section lists this many at most; typing more narrows it.
const SHOWN = 60;

/* The Creators page while the header's search has something in it
   (lib/search.js): what matches, in three sections, each one hidden when
   nothing in it matches. Creators (a person by name or label) open their
   page; Accounts (any handle, linked to someone or not) their posts in the
   Feed, with their person one click away; Links (address, title or label, notes)
   open in a new tab, their person one click away too.

   Props: query, people (/api/people), accounts (/api/authors), refreshKey. */
export default function CreatorSearch({ query, people, accounts, refreshKey }) {
  const found = useMemo(() => searchCreators(people, accounts, query), [people, accounts, query]);
  // /api/links matches the whole of `q`: it is given the longest word, and
  // every word is checked here, as for people and accounts.
  const word = query.trim().split(/\s+/).sort((a, b) => b.length - a.length)[0].slice(0, LINKS_Q_MAX);
  const loadLinks = useCallback(() => getLinks({ q: word }), [word]);
  const linksApi = useApi(loadLinks, refreshKey);
  const links = useMemo(() => (linksApi.data?.links || []).filter(l => linkMatches(l, query)), [linksApi.data, query]);
  const waiting = !people || !accounts || (!linksApi.data && linksApi.loading);
  const total = found.people.length + found.accounts.length + links.length;

  if (!total) {
    return (
      <div className="empty creator-search-empty" role="status">
        {waiting ? "Searching…" : <>Nothing matches “{query.trim()}”: no creator, account or link.</>}
      </div>
    );
  }
  return (
    <div className="creator-search" aria-label={`Search results for ${query.trim()}`} role="region">
      {found.people.length > 0 && (
        <Section title="Creators" count={found.people.length}>
          <div className="creator-grid">
            {found.people.slice(0, SHOWN).map(p => (
              <div key={p.id} className="creator-card person-card">
                <Link to={personPath(p.id)} className="creator-main" title={`Open ${p.name}`}>
                  <PersonAvatar person={p} />
                  <span className="creator-id">
                    <span className="creator-name">{p.name}</span>
                    <span className="creator-sub">
                      {[plural(p.accounts.length, "account"), ...(p.labels || [])].join(" · ")}
                    </span>
                  </span>
                  <span className="person-stats"><span className="creator-count">{fmtInt(p.count)}</span></span>
                </Link>
              </div>
            ))}
          </div>
        </Section>
      )}
      {found.accounts.length > 0 && (
        <Section title="Accounts" count={found.accounts.length}>
          <div className="creator-grid">
            {found.accounts.slice(0, SHOWN).map(a => {
              const name = `@${a.handle || a.id}`;
              return (
                <div key={a.id != null ? accountKey(a) : `${a.platform}:@${a.handle}`} className="creator-card">
                  <Link to={authorFeedPath(a.platform, a)} className="creator-main" title={`Show posts by ${name}`}>
                    <PlatformIcon platform={a.platform} />
                    <span className="creator-id">
                      <span className="creator-name">{name}</span>
                      <span className="creator-sub">
                        {a.name && a.name !== a.handle ? `${a.name} · ` : ""}{platformLabel(a.platform)}
                        {a.person ? "" : " · not linked"}
                      </span>
                    </span>
                    <span className="person-stats"><span className="creator-count">{fmtInt(a.count || 0)}</span></span>
                  </Link>
                  {a.person && (
                    <span className="card-actions">
                      <Link to={personPath(a.person.id)} className="chip person-chip" title={`Person: ${a.person.name}`}>
                        <Icon name="users" size={11} /><span className="chip-text">{a.person.name}</span>
                      </Link>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      )}
      {links.length > 0 && (
        <Section title="Links" count={links.length}>
          <ul className="link-list">
            {links.slice(0, SHOWN).map(l => <LinkRow key={l.id} link={l} />)}
          </ul>
        </Section>
      )}
    </div>
  );
}

function Section({ title, count, children }) {
  return (
    <section className="creator-search-section" aria-label={title}>
      <h3 className="card-title creators-section">
        {title} <span className="page-count">{fmtInt(count)}{count > SHOWN ? `, the first ${SHOWN}: type more to narrow` : ""}</span>
      </h3>
      {children}
    </section>
  );
}
