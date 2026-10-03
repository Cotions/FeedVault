import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getNotifications, readNotifications } from "../lib/api";
import { fmtAgo, fmtInt } from "../lib/fmt";
import { personPath } from "../lib/people";
import Icon from "./Icon";

/* Where an entry leads: the posts its sync brought, or the source that
   failed (its person's page, else Creators). */
function notificationPath(e) {
  if (e.kind === "new") return `/?notification=${e.id}`;
  return e.person_id ? personPath(e.person_id) : "/creators";
}

/* The sidebar's bell: the unread count, and the list of syncs that brought
   new posts or failed (docs/API.md "Notifications"). Opening it marks
   what it shows read. An entry's text is tool output and handles: React
   renders it as text, never HTML. */
export default function Notifications({ unread, latest, onRead }) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const root = useRef(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    getNotifications().then(r => {
      if (!alive) return;
      setList(r);
      setError(null);
      const newest = r.entries[0]?.id;
      if (r.unread && newest) readNotifications(newest).then(onRead, () => {});
    }, e => { if (alive) setError(e); });
    return () => { alive = false; };
  }, [open, latest, onRead]);

  useEffect(() => {
    if (!open) return;
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    function onDown(e) { if (root.current && !root.current.contains(e.target)) setOpen(false); }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div className="side-row side-bell" ref={root}>
      <button
        type="button"
        className={`side-link${open ? " active" : ""}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(o => !o)}
        title="Syncs that brought new posts or failed"
      >
        <Icon name="bell" />Notifications
        {unread > 0 && <span className="side-badge" aria-label={`${unread} unread`}>{fmtInt(unread)}</span>}
      </button>
      {open && (
        <div className="notif-panel" role="dialog" aria-label="Notifications">
          <div className="notif-head">
            <span className="card-title">Notifications</span>
            <button type="button" className="btn-ghost notif-close" onClick={() => setOpen(false)} aria-label="Close">
              <Icon name="close" size={13} />
            </button>
          </div>
          {error ? <p className="notif-empty">Could not load the list: {error.message}</p>
            : !list ? <p className="notif-empty">Loading…</p>
            : !list.entries.length ? <p className="notif-empty">Nothing yet. A sync that brings new posts or fails leaves an entry here.</p>
            : (
              <ul className="notif-list">
                {list.entries.map(e => (
                  <li key={e.id}>
                    <Link to={notificationPath(e)} className={`notif-entry notif-${e.kind}${e.read ? "" : " is-unread"}`}
                          onClick={() => setOpen(false)}>
                      <Icon name={e.kind === "new" ? "download" : "warn"} size={14} />
                      <span className="notif-text">{e.text}</span>
                      <span className="notif-when" title={new Date(e.at * 1000).toLocaleString()}>
                        {fmtAgo(e.at)}{e.scheduled ? " · scheduled" : ""}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
        </div>
      )}
    </div>
  );
}
