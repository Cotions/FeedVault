import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/* useSearchParams, but an update given as a function starts from the
   address as it is now, not as the page last rendered it. The router puts
   a move in the address at once and renders it in a transition: a filter
   changed in between (Clear filters, then Kind straight after) started
   from the filters before the clear and put them back (#172). */
export function useLiveSearchParams() {
  const [params, setParams] = useSearchParams();
  const set = useCallback((next, opts) => setParams(
    typeof next === "function" ? next(new URLSearchParams(window.location.search)) : next, opts,
  ), [setParams]);
  return [params, set];
}
