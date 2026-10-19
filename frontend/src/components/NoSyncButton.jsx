import { useState } from "react";
import { setNoSync } from "../lib/api";
import { plural } from "../lib/fmt";
import { useToast } from "../lib/toast";
import ConfirmDialog from "./ConfirmDialog";
import Icon from "./Icon";

/* The Don't sync switch of a person or an account (docs/API.md "Don't
   sync"): while on, FeedVault never downloads from it. No source can be
   added for it and one it has is kept but never runs; its posts stay in the
   library. Turning it on for one with sources asks first: the backend says
   how many would stop, and this dialog says what that means.

   `on`: its own switch. `inherited`: the name of the account's person
   when their switch is on, so it is not synced either way: shown on, and
   turned off on the person, not here (the button says so in its title and
   does nothing; aria-disabled, so the title still shows on hover).
   `compact`: icon only, for a Creators card. */
export default function NoSyncButton({ on, inherited = null, whom, name, onDone, compact = false }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState(null);        // { sources: n } while the dialog is open
  const [error, setError] = useState(null);

  async function send(confirm = false) {
    setBusy(true);
    setError(null);
    try {
      const r = await setNoSync(whom, !on, confirm);
      if (r?.confirm && !confirm) { setAsk({ sources: r.sources }); return; }
      if (!r?.ok) {
        if (confirm) setError(r?.error || "Could not change it.");
        else toast(r?.error || "Could not change it.", "err");
        return;
      }
      setAsk(null);
      toast(on ? `${name} syncs again.` : `${name} set to Don't sync: nothing is downloaded from it.`);
      onDone?.();
    } catch (err) {
      if (confirm) setError(err.message); else toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  const blocked = !!inherited;
  const shown = on || blocked;
  const title = blocked ? `Not synced: ${inherited} is set to Don't sync. Turn it off on ${inherited} first`
    : on ? `Sync ${name} again: Add source, Sync and schedules come back`
    : `Don't sync ${name}: no source, no Sync, no schedule. What is downloaded stays`;
  const label = blocked ? `${name} not synced: ${inherited} is set to Don't sync`
    : shown ? `Sync ${name} again` : `Don't sync ${name}`;
  const click = blocked ? undefined : () => send();
  const button = compact ? (
    <button type="button" className={`icon-btn creator-nosync${shown ? " is-on" : ""}`} onClick={click}
            disabled={busy} aria-disabled={blocked || undefined} title={title} aria-label={label} aria-pressed={shown}>
      <Icon name={shown ? "syncOff" : "refresh"} size={15} />
    </button>
  ) : (
    <button type="button" className={`btn-secondary select-toggle nosync-toggle${shown ? " is-on" : ""}`}
            onClick={click} disabled={busy} aria-disabled={blocked || undefined} title={title} aria-pressed={shown}>
      <Icon name={shown ? "syncOff" : "refresh"} size={14} /> {shown ? "Not synced" : "Don't sync"}
    </button>
  );
  return (
    <>
      {button}
      <ConfirmDialog
        open={!!ask}
        title={`Stop syncing ${name}?`}
        confirmLabel="Don't sync"
        busy={busy}
        error={error}
        onConfirm={() => send(true)}
        onCancel={() => { setAsk(null); setError(null); }}
      >
        <p>
          {plural(ask?.sources || 0, "source")} of {name} will stop syncing. {ask?.sources === 1 ? "It stays" : "They stay"} stored,
          options included, marked Not synced, and never {ask?.sources === 1 ? "runs" : "run"}: no Sync, no Sync all, no schedule,
          until you turn Don't sync off again.
        </p>
        <p>Nothing is deleted: what is downloaded stays in the library.</p>
      </ConfirmDialog>
    </>
  );
}
