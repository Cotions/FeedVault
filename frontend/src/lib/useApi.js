import { useCallback, useEffect, useState } from "react";

/* Load one endpoint and reload it whenever `key` changes (pass the app's
   refresh counter so a finished scan refreshes the page). `fn` must be stable:
   a module-level api function, or one wrapped in useCallback. Previous data
   stays on screen during a reload, so a rescan never blanks the page. */
export function useApi(fn, key) {
  const [state, setState] = useState({ data: null, error: null, done: false });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    fn().then(
      data  => { if (alive) setState({ data, error: null, done: true }); },
      error => { if (alive) setState(s => ({ data: s.data, error, done: true })); },
    );
    return () => { alive = false; };
  }, [fn, key, tick]);

  const reload = useCallback(() => setTick(t => t + 1), []);
  return { ...state, loading: !state.done, reload };
}
