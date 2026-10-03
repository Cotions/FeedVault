import { useState } from "react";
import { Link } from "react-router-dom";
import { authorFeedPath, fmtInt, platformLabel, platformShort } from "../lib/fmt";
import { accountKey, personPath, REASONS } from "../lib/people";
import Icon from "./Icon";

const SUGGESTIONS_SHOWN = 4;

// "Link?" cards: why these accounts look like one person, link or dismiss.
export default function Suggestions({ data, busy, onLink, onDismiss, title = "Link?" }) {
  const [all, setAll] = useState(false);
  const list = data?.suggestions || [];
  if (!list.length) return null;
  const shown = all ? list : list.slice(0, SUGGESTIONS_SHOWN);
  return (
    <section className="suggestions" aria-label="Link suggestions">
      <div className="suggestions-head">
        <h3 className="card-title">{title}</h3>
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
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => onDismiss(s)}
                      title="Not the same person: never suggest these together again">Not them</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
