import { useRef, useState } from "react";
import { importManyLinks } from "../lib/api";
import { fmtBytes, fmtInt } from "../lib/fmt";
import { useToast } from "../lib/toast";
import ConfirmDialog from "./ConfirmDialog";
import Icon from "./Icon";
import { UNSORTED_PATH } from "./QuickAddLink";

// As the backend's linkimport.MAX_TEXT: characters read from a paste or a file.
const MAX_IMPORT = 10 * 1024 * 1024;

// "42 saved to Unsorted, 3 already there, 1 skipped.": the counts that are not 0.
function importSummary({ saved = 0, already = 0, skipped = 0, over = 0 }) {
  if (!saved && !already && !skipped && !over) return "No link found in that.";
  const parts = [saved ? `${fmtInt(saved)} saved to Unsorted` : "Nothing new"];
  if (already) parts.push(`${fmtInt(already)} already there`);
  if (skipped) parts.push(`${fmtInt(skipped)} skipped`);
  if (over) parts.push(`${fmtInt(over)} over the limit of one import`);
  return parts.join(", ") + ".";
}

/* The Links page's "Import many…": addresses pasted in a box, or a file
   (a browser's bookmarks exported to HTML, Firefox's JSON backup, a .txt
   list), all saved to Unsorted at once (POST /api/links/import-many:
   insert-only, a link saved already is left as it is; nothing is fetched).
   A chosen file is imported instead of the box. Once done the dialog
   closes, a toast says what came of it and offers the Unsorted tab, and
   onImported() lets the page read its links again.

   Props: onImported */
export default function LinkImport({ onImported }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);       // the File chosen, imported in place of the box
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const boxRef = useRef(null);
  const fileRef = useRef(null);

  function close() {
    if (!busy) reset();
  }

  function reset() {
    setOpen(false);
    setText("");
    setFile(null);
    setError(null);
  }

  function pick(e) {
    const f = e.target.files?.[0];
    e.target.value = "";                         // the same file again is a change too
    if (!f) return;
    setError(f.size > MAX_IMPORT ? `${f.name} is ${fmtBytes(f.size)}: at most ${fmtBytes(MAX_IMPORT)} is read.` : null);
    setFile(f.size > MAX_IMPORT ? null : f);
  }

  async function run() {
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      const sent = file ? await file.text() : text;
      if (!sent.trim()) { setError("Paste some addresses, or choose a file, first."); return; }
      if (sent.length > MAX_IMPORT) { setError(`That is more than ${fmtBytes(MAX_IMPORT)} of text: import it in parts.`); return; }
      const r = await importManyLinks(sent);
      if (!r?.ok) { setError(r?.error || "Could not import the links."); return; }
      reset();
      toast(importSummary(r), r.saved ? "ok" : "info", r.saved ? { to: UNSORTED_PATH, label: "Show Unsorted" } : undefined);
      onImported?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="links-import">
      <span className="links-bookmarklet-label">Many at once</span>
      <button type="button" className="btn-secondary links-import-open" onClick={() => setOpen(true)}>
        <Icon name="download" size={14} />Import many…
      </button>
      <span className="dim">Open tabs or exported bookmarks, all into Unsorted.</span>
      <ConfirmDialog
        open={open}
        title="Import links to Unsorted"
        confirmLabel={file ? "Import the file" : "Import"}
        busy={busy}
        error={error}
        initialFocus={boxRef}
        confirmDisabled={!file && !text.trim()}
        onConfirm={run}
        onCancel={close}
      >
        <div className="links-import-body">
          <p>
            <strong className="links-import-term">Open tabs:</strong> paste their addresses here, one per line or
            any way at all. Or bookmark all tabs into a folder and export your bookmarks.
          </p>
          <p>
            <strong className="links-import-term">Favorites:</strong> export your bookmarks to HTML (Firefox:
            Manage bookmarks → Export Bookmarks to HTML; Chrome, Edge: Bookmarks manager → Export bookmarks) and
            choose the file. Each keeps its title, the day you bookmarked it and its folder.
          </p>
          <textarea ref={boxRef} className="person-notes links-import-text" rows={7} spellCheck={false}
                    aria-label="Addresses to import" disabled={!!file} readOnly={busy}
                    placeholder={file ? "The file chosen is imported." : "https://…\nhttps://…\nA title - https://…"}
                    value={text} onChange={e => { setText(e.target.value); setError(null); }} />
          <div className="links-import-file">
            <input ref={fileRef} type="file" accept=".html,.htm,.txt,.json,text/html,text/plain,application/json"
                   hidden aria-label="Bookmarks file" onChange={pick} />
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => fileRef.current?.click()}>
              <Icon name="folder" size={14} />Choose file…
            </button>
            {file ? (
              <>
                <span className="links-import-name" title={file.name}>{file.name}</span>
                <span className="dim">{fmtBytes(file.size)}</span>
                <button type="button" className="btn-ghost" disabled={busy} onClick={() => setFile(null)}>Remove</button>
              </>
            ) : (
              <span className="dim">.html, .txt or Firefox .json</span>
            )}
          </div>
        </div>
      </ConfirmDialog>
    </div>
  );
}
