import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBlocker } from "react-router-dom";
import { UnsavedContext, stopping } from "../lib/unsaved";

/* The router's one blocker, for every useUnsaved under it (lib/unsaved.js):
   a move is stopped when any of them would stop it, and the one opened
   last asks. Inside the data router (main.jsx). */
export default function UnsavedProvider({ children }) {
  const guards = useRef([]);                            // in the order they were opened
  const move = useRef(null);                            // { from, to } of the move waiting
  const [asker, setAsker] = useState(null);             // the guard whose dialog asks
  const [closed, setClosed] = useState(0);              // guards gone so far
  const blocker = useBlocker(useCallback(({ currentLocation: from, nextLocation: to }) => {
    const g = stopping(guards.current, from, to);
    if (!g) return false;
    move.current = { from, to };
    setAsker(g);
    return true;
  }, []));

  const add    = useCallback(g => { guards.current = [...guards.current, g]; }, []);
  const remove = useCallback(g => { guards.current = guards.current.filter(x => x !== g); setClosed(n => n + 1); }, []);
  // The asker's edits went (saved, put back, its dialog closed) while it
  // asked: another may still stop the move, or nothing is left to ask about.
  // Once answered, a move is not answered again: the blocker this render
  // saw may already be on its way.
  const answer = useCallback(go => {
    if (!move.current) return;
    move.current = null;
    if (go) blocker.proceed?.(); else blocker.reset?.();
  }, [blocker]);
  const recheck = useCallback(() => {
    const m = move.current;
    const g = m && stopping(guards.current, m.from, m.to);
    if (g) setAsker(g); else answer(true);
  }, [answer]);
  const blocked = blocker.state === "blocked";
  useEffect(() => {
    if (blocked && !guards.current.includes(asker)) recheck();
  }, [blocked, asker, closed, recheck]);

  const value = useMemo(() => ({ blocker, asker, add, remove, recheck, answer }), [blocker, asker, add, remove, recheck, answer]);
  return <UnsavedContext.Provider value={value}>{children}</UnsavedContext.Provider>;
}
