import ConfirmDialog from "./ConfirmDialog";

/* The one question before typed edits go, anywhere in the app: Keep editing
   (focused, so a stray Enter keeps them) or Discard. ``children`` says what
   would be lost. */
export default function DiscardDialog({ open, onDiscard, onKeep, children }) {
  return (
    <ConfirmDialog
      open={open}
      title="Discard unsaved changes?"
      confirmLabel="Discard"
      cancelLabel="Keep editing"
      danger
      onConfirm={onDiscard}
      onCancel={onKeep}
    >
      {children}
    </ConfirmDialog>
  );
}
