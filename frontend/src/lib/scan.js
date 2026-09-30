import { createContext, useContext } from "react";

/* Scan status shared by the sidebar button, Settings and every page that
   refreshes when a scan finishes. Provided by App.

   { status, running, start(), refreshKey }
   - status:     last GET /api/scan answer ({ running, last }) or null
   - refreshKey: bumps each time a scan finishes or the backend comes back */
export const ScanContext = createContext({
  status: null,
  running: false,
  start: async () => ({ ok: false }),
  refreshKey: 0,
});

export function useScan() {
  return useContext(ScanContext);
}
