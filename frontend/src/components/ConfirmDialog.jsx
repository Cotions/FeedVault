import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { focusLost, restoreFocus, trapTab } from "../lib/layout";

/* Modal confirmation. Traps Tab inside the dialog, Esc or a backdrop click
   cancels, focus starts on Cancel (so a stray Enter never destroys anything)
   and returns to whatever had it before, without scrolling. While `busy`, both buttons are
   off and Esc does nothing: the request is already on its way. They stay
   focusable (aria-disabled), so focus is not dropped on <body>; and should
   it get there anyway (a control in the body turned off or went away), Esc
   and Tab still work: they are heard on the document too (#136).

   Props: open, title, children (body), confirmLabel, danger, busy, error,
          onConfirm, onCancel, initialFocus (a ref to focus instead of Cancel,
          for a dialog whose body is a form), confirmDisabled */
export default function ConfirmDialog({
  open, title, children, confirmLabel = "Confirm", cancelLabel = "Cancel",
  danger = false, busy = false, error = null, onConfirm, onCancel, initialFocus, confirmDisabled = false,
}) {
  const boxRef    = useRef(null);
  const cancelRef = useRef(null);
  const keysRef   = useRef(null);
  const titleId   = useId();
  const bodyId    = useId();

  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement;
    (initialFocus?.current || cancelRef.current)?.focus();
    return () => restoreFocus(prev);
    // Once per opening; initialFocus is a ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function onKeyDown(e) {
    if (e.key === "Escape") {
      e.stopPropagation();
      e.preventDefault();
      if (!busy) onCancel();
      return;
    }
    trapTab(e, boxRef.current);
  }
  useEffect(() => { keysRef.current = onKeyDown; });

  // Focus on <body> never reaches the overlay's onKeyDown: Esc and Tab are
  // heard on the document then, first (capture), so a page's own Esc
  // (select mode's) does not act on the same key.
  useEffect(() => {
    if (!open) return;
    function onKey(e) {
      if ((e.key === "Escape" || e.key === "Tab") && focusLost() && boxRef.current) keysRef.current?.(e);
    }
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className="modal-overlay"
      onKeyDown={onKeyDown}
      onMouseDown={e => { if (e.target === e.currentTarget && !busy) onCancel(); }}
    >
      <div
        ref={boxRef}
        className={`modal confirm${danger ? " is-danger" : ""}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        aria-busy={busy}
      >
        <h2 id={titleId} className="modal-title">{title}</h2>
        <div id={bodyId} className="confirm-body">{children}</div>
        {error && <div className="msg err" role="alert">{error}</div>}
        <div className="modal-actions">
          <button ref={cancelRef} type="button" className="btn-secondary" aria-disabled={busy || undefined}
                  onClick={() => { if (!busy) onCancel(); }}>
            {cancelLabel}
          </button>
          <button type="button" className={danger ? "btn-danger" : "btn-primary"} aria-disabled={busy || undefined}
                  onClick={() => { if (!busy) onConfirm(); }} disabled={confirmDisabled}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
