import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

/* Typed edits are never dropped without asking. While ``dirty``, a move to
   another page in the app (a link, Back, Forward) waits for the user's
   answer in a DiscardDialog, and closing or reloading the tab gets the
   browser's own warning. Nothing is asked once the edits are saved,
   discarded or never made.

   Returns { asking, discard, keep, leave }: ``asking`` while a move waits;
   discard() goes on with it, keep() stays; leave(fn) runs fn without asking
   (after a delete the page goes on its own: nothing is left to keep).
   ``passes(from, to)``, when given, lets a move that cannot lose the edits
   through without asking (the Links page clearing its filters: every link,
   the one being edited too, stays in the list).

   Any number at once: a page's and a dialog's on it (a source's Options,
   #153). The router holds one blocker, UnsavedProvider's; it stops a move
   when any of them would, and the one opened last (the dialog, on top)
   asks: one answer for all, as the move drops every one of them. */
export const UnsavedContext = createContext(null);

// The guard, opened last first, that would stop the move from ``from`` to ``to``.
export function stopping(guards, from, to) {
  if (from.pathname === to.pathname && from.search === to.search) return null;
  return [...guards].reverse().find(({ current: g }) => g.dirty && !g.leaving && !g.passes?.(from, to)) || null;
}

export function useUnsaved(dirty, passes = null) {
  const ctx = useContext(UnsavedContext);
  if (!ctx) throw new Error("useUnsaved needs an UnsavedProvider (main.jsx)");
  const guard = useRef({ dirty: false, leaving: false, passes: null });
  useEffect(() => { guard.current.dirty = dirty; guard.current.passes = passes; });
  const { add, remove, recheck, answer, blocker, asker } = ctx;
  useEffect(() => { add(guard); return () => remove(guard); }, [add, remove]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = e => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // Saved or put back while the question was up: nothing left to ask about here.
  const mine = blocker.state === "blocked" && asker === guard;
  useEffect(() => { if (mine && !dirty) recheck(); }, [mine, dirty, recheck]);

  const leave = useCallback(fn => { guard.current.leaving = true; fn(); }, []);
  return {
    asking: mine && dirty,
    discard: () => answer(true),
    keep: () => answer(false),
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
