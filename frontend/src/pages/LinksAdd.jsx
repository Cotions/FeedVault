import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { externalUrl, sharedTitle } from "../lib/links";
import { personPath } from "../lib/people";
import Icon from "../components/Icon";
import { QuickAddForm, UNSORTED_PATH } from "../components/QuickAddLink";

// How long "Saved to X" shows before a popup closes itself.
const CLOSE_AFTER_MS = 900;

/* /links/add?url=&title= (#165 C): the quick-add form on a page of its
   own, without the app around it, for the bookmarklet's small window
   (lib/bookmarklet.js). The query only fills the form: an address only if
   it is one web address (externalUrl), the title as plain text, capped.
   Nothing is saved until Save. Opened as a popup (?popup=1, or by a
   script), it says where the link went and closes; otherwise it says so
   with links there and "Add another". */
export default function LinksAdd() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  // Read once: "Add another" empties the form, and leaves the window as it was.
  const [popup] = useState(() => params.get("popup") === "1" || !!window.opener);
  const [start, setStart] = useState(() => ({ url: externalUrl(params.get("url")) || "", title: sharedTitle(params.get("title")), n: 0 }));
  const [saved, setSaved] = useState(null);
  const [closeTried, setCloseTried] = useState(false);

  // The page that opened this one is never used: no handle to it is kept.
  useEffect(() => {
    if (window.opener) window.opener = null;
  }, []);

  useEffect(() => {
    const was = document.title;
    document.title = "Add a link · FeedVault";
    return () => { document.title = was; };
  }, []);

  // A popup: Esc closes it, as it closes the dialog.
  useEffect(() => {
    if (!popup) return undefined;
    function onKey(e) { if (e.key === "Escape" && !e.defaultPrevented) window.close(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [popup]);

  // A browser may keep a window open that a script did not open (the tab
  // itself, after a blocked popup): then the links stay, as on a page.
  useEffect(() => {
    if (!saved || !popup) return undefined;
    const t = setTimeout(() => { window.close(); setCloseTried(true); }, CLOSE_AFTER_MS);
    return () => clearTimeout(t);
  }, [saved, popup]);

  function another() {
    setSaved(null);
    setCloseTried(false);
    setStart(s => ({ url: "", title: "", n: s.n + 1 }));
  }

  const p = saved?.person;
  return (
    <div className="links-add-page">
      <div className="links-add-box">
        <div className="links-add-head">
          <span className="brand-mark"><Icon name="feed" size={15} /></span>
          <h2 className="modal-title">Add a link</h2>
        </div>
        {saved ? (
          <div className="links-add-done">
            <p className="msg ok" role="status">
              <Icon name="check" size={15} />
              <span>Saved to {p ? <Link to={personPath(p.id)}>{p.name}</Link> : <Link to={UNSORTED_PATH}>Unsorted</Link>}.
                {popup && !closeTried && " Closing…"}</span>
            </p>
            <div className="modal-actions">
              <Link to="/links" className="btn-secondary">All links</Link>
              <button type="button" className="btn-primary" onClick={another}>
                <Icon name="plus" size={14} />Add another
              </button>
            </div>
          </div>
        ) : (
          <QuickAddForm key={start.n} url={start.url} title={start.title} escape={popup}
                        onSaved={setSaved} onLeave={() => {}}
                        onCancel={() => (popup ? window.close() : navigate("/links"))} />
        )}
      </div>
    </div>
  );
}
