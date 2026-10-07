import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { FOCUSABLE, restoreFocus } from "../lib/layout";

/* Modal confirmation. Traps Tab inside the dialog, Esc or a backdrop click
   cancels, focus starts on Cancel (so a stray Enter never destroys anything)
   and returns to whatever had it before, without scrolling. While `busy`, both buttons are
   disabled and Esc does nothing: the request is already on its way.

   Props: open, title, children (body), confirmLabel, danger, busy, error,
          onConfirm, onCancel, initialFocus (a ref to focus instead of Cancel,
          for a dialog whose body is a form), confirmDisabled */
export default function ConfirmDialog({
  open, title, children, confirmLabel = "Confirm", cancelLabel = "Cancel",
  danger = false, busy = false, error = null, onConfirm, onCancel, initialFocus, confirmDisabled = false,
}) {
  const boxRef    = useRef(null);
  const cancelRef = useRef(null);
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

  if (!open) return null;

  function onKeyDown(e) {
    if (e.key === "Escape") {
      e.stopPropagation();
      e.preventDefault();
      if (!busy) onCancel();
      return;
    }
    if (e.key !== "Tab") return;
    const items = [...boxRef.current.querySelectorAll(FOCUSABLE)];
    if (items.length === 0) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || !boxRef.current.contains(document.activeElement))) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

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
          <button ref={cancelRef} type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button type="button" className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm} disabled={busy || confirmDisabled}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
