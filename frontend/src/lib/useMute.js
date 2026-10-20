import { useState } from "react";
import { muteNew } from "./api";
import { useToast } from "./toast";

/* Mute a person, or an account linked to nobody: its syncs make no
   notification and no toast, and its new posts stay out of the global
   count (they still show on its own page). The item of its ⋯ menu
   (components/ActionMenu.jsx), Mute or Unmute by ``muted``; the card or
   page shows the state with a "Muted" chip (components/StateChips.jsx). */
export function useMute({ muted, whom, name, onDone }) {
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
  return {
    label: muted ? "Unmute" : "Mute",
    icon: muted ? "bell" : "bellOff",
    title: muted ? `Unmute ${name}: notifications and the new count again`
      : `Mute ${name}: no notification or toast, not in the global new count`,
    disabled: busy,
    onSelect: toggle,
  };
}
