import { useState } from "react";
import { muteNew } from "../lib/api";
import { useToast } from "../lib/toast";
import Icon from "./Icon";

/* Mute a person, or an account linked to nobody: its syncs make no
   notification and no toast, and its new posts stay out of the global
   count (they still show on its own page). `compact`: icon only, for a
   Creators card. */
export default function MuteButton({ muted, whom, name, onDone, compact = false }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function toggle() {
    setBusy(true);
    try {
      const r = await muteNew(whom, !muted);
      if (!r?.ok) { toast(r?.error || "Could not change it.", "err"); return; }
      toast(muted ? `${name} unmuted.` : `${name} muted: no notification, not in the new count.`);
      onDone?.();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }
  const title = muted ? `Unmute ${name}: notifications and the new count again`
    : `Mute ${name}: no notification or toast, not in the global new count`;
  return compact ? (
    <button type="button" className={`icon-btn creator-mute${muted ? " is-muted" : ""}`} onClick={toggle} disabled={busy}
            title={title} aria-label={muted ? `Unmute ${name}` : `Mute ${name}`} aria-pressed={muted}>
      <Icon name={muted ? "bellOff" : "bell"} size={15} />
    </button>
  ) : (
    <button type="button" className={`btn-secondary select-toggle${muted ? " is-on" : ""}`} onClick={toggle} disabled={busy}
            title={title} aria-pressed={muted}>
      <Icon name={muted ? "bellOff" : "bell"} size={14} /> {muted ? "Muted" : "Mute"}
    </button>
  );
}
