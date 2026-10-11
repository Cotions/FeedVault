import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { externalUrl, sharedTitle, urlProblem } from "../lib/links";
import { personPath } from "../lib/people";
import Icon from "../components/Icon";
import { QuickAddForm, UNSORTED_PATH } from "../components/QuickAddLink";

// How long "Saved to X" shows before a popup closes itself.
const CLOSE_AFTER_MS = 900;

/* /links/add#url=&title= (#165 C): the quick-add form on a page of its
   own, without the app around it, for the bookmarklet's small window
   (lib/bookmarklet.js). The fragment (never sent to the server, so never
   in its log) only fills the form: an address only if it is one web
   address (externalUrl; else a line says why not), the title as plain
   text, capped. An address given so is saved only once a person, or no
   one, is chosen: Enter alone, pressed as the window opens, saves nothing.
   Opened as a popup (?popup=1, or by a script), it says where the link
   went and closes; otherwise it says so with links there and "Add
   another". */
export default function LinksAdd() {
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const navigate = useNavigate();
  // Read once: "Add another" empties the form, and leaves the window as it was.
  const [popup] = useState(() => params.get("popup") === "1" || !!window.opener);
  const [start, setStart] = useState(() => {
    const given = new URLSearchParams(hash.slice(1));
    return { url: externalUrl(given.get("url")) || "", title: sharedTitle(given.get("title")),
             refused: urlProblem(given.get("url")), n: 0 };
  });
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

  // Something dropped on the page beside a text field does nothing: the
  // browser would open a dropped link here, in place of the form.
  useEffect(() => {
    const editable = t => t instanceof Element && (t.closest("input, textarea") || t.isContentEditable);
    function onDrag(e) {
      if (editable(e.target)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "none";
    }
    window.addEventListener("dragover", onDrag);
    window.addEventListener("drop", onDrag);
    return () => {
      window.removeEventListener("dragover", onDrag);
      window.removeEventListener("drop", onDrag);
    };
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
    setStart(s => ({ url: "", title: "", refused: null, n: s.n + 1 }));
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
          <>
            {start.refused && (
              <p className="msg err links-add-refused">The page's address was not filled in: {start.refused}.</p>
            )}
            <QuickAddForm key={start.n} url={start.url} title={start.title} escape={popup} confirm={!!start.url}
                          onSaved={setSaved} onLeave={() => {}}
                          onCancel={() => (popup ? window.close() : navigate("/links"))} />
          </>
        )}
      </div>
    </div>
  );
}
