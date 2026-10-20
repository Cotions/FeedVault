import Icon from "./Icon";
import { NotSyncedChip } from "./Sources";

/* "Muted": a person, or an account linked to nobody, makes no
   notification and stays out of the new count (lib/useMute.js). */
export function MutedChip({ title }) {
  return (
    <span className="chip state-chip" title={title}>
      <Icon name="bellOff" size={11} />Muted
    </span>
  );
}

/* What a card, a row or a person's page is set to, now that Mute and
   Don't sync are items of its ⋯ menu: a "Muted" and a "Not synced" chip,
   each only while on (nothing in the normal state). ``name`` goes in their
   titles; ``noSyncTitle`` replaces the Not synced one's. */
export default function StateChips({ name, muted = false, noSync = false, noSyncTitle = null, className = "" }) {
  if (!muted && !noSync) return null;
  return (
    <span className={`state-line${className ? ` ${className}` : ""}`}>
      {noSync && <NotSyncedChip title={noSyncTitle || `${name} is set to Don't sync: nothing is downloaded from it. Sync again from its ⋯ menu`} />}
      {muted && <MutedChip title={`${name} is muted: no notification or toast, not in the global new count. Unmute from its ⋯ menu`} />}
    </span>
  );
}
