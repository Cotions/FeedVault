import { useCallback, useEffect, useRef, useState } from "react";
import { useBlocker } from "react-router-dom";

/* Typed edits are never dropped without asking. While ``dirty``, a move to
   another page in the app (a link, Back, Forward) waits for the user's
   answer in a DiscardDialog, and closing or reloading the tab gets the
   browser's own warning. Nothing is asked once the edits are saved,
   discarded or never made.

   Returns { asking, discard, keep, leave }: ``asking`` while a move waits;
   discard() goes on with it, keep() stays; leave(fn) runs fn without asking
   (after a delete the page goes on its own: nothing is left to keep).
   One per page: the router holds one blocker at a time. */
export function useUnsaved(dirty) {
  const leaving = useRef(false);
  const blocker = useBlocker(useCallback(
    ({ currentLocation: from, nextLocation: to }) =>
      dirty && !leaving.current && (from.pathname !== to.pathname || from.search !== to.search),
    [dirty],
  ));

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = e => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // Saved or put back while the question was up: nothing left to ask about.
  const blocked = blocker.state === "blocked";
  useEffect(() => { if (blocked && !dirty) blocker.proceed(); }, [blocked, dirty, blocker]);

  const leave = useCallback(fn => { leaving.current = true; fn(); }, []);
  return {
    asking: blocked && dirty,
    discard: () => blocker.proceed?.(),
    keep: () => blocker.reset?.(),
    leave,
  };
}

/* One of a list's items open for editing at a time (the Links page's, a
   person's links). Opening another while the open one has unsaved edits
   asks first, the same question as leaving the page: { asking, discard,
   keep } for a DiscardDialog. The form reports its edits through onDirty. */
export function useOneEdit() {
  const [editing, setEditing] = useState(null);       // the id being edited
  const [dirty,   setDirty]   = useState(false);
  const [next,    setNext]    = useState(null);       // { id } waiting on the answer
  return {
    editing,
    dirty,
    onDirty: setDirty,
    open: id => { if (id === editing) return; if (dirty) setNext({ id }); else setEditing(id); },
    close: () => setEditing(null),
    asking: next != null,
    discard: () => { setEditing(next.id); setNext(null); },
    keep: () => setNext(null),
  };
}
