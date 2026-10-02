import { useCallback, useMemo, useState } from "react";
import { getSources, syncSource, deleteSource, syncAllSources } from "./api";
import { useApi } from "./useApi";
import { useScan } from "./scan";
import { useJobs, ENDED } from "./jobs";
import { useToast } from "./toast";

// One sync kind per tool (docs/API.md "Jobs").
export const SYNC_KINDS = new Set(["instaloader-sync", "gallery-dl-sync", "yt-dlp-sync"]);

// How a source is named: @name for Instagram, else its link without https://.
export function sourceName(s) {
  return s.tool === "instaloader" ? `@${s.target}` : s.target.replace(/^https:\/\//, "");
}

// last_result.error → a short badge
export const ERRORS = {
  rate_limited: "rate limited",
  login_required: "login needed",
  private: "private",
  not_found: "not found",
  missing: "tool missing",
  generic: "failed",
};

// The "Sync all" summary the user hid, by batch (id and start time); the
// batch itself lives in the backend, in memory (docs/API.md "Sync all").
const HIDDEN_KEY = "feedvault.syncAllHidden";
localStorage.removeItem("feedvault.syncAll");   // job ids kept by older builds (#32)

/* Every source, kept fresh: reloaded after a scan and whenever a sync job
   starts, moves on or ends (a source shows its sync's state and, once it has
   ended, its result). Live job state comes from the shared jobs poll.

   { data, error, reload, jobOf(source), sync(source), remove(source) } */
export function useSources() {
  const { refreshKey } = useScan();
  const { list, started } = useJobs();
  const toast = useToast();
  const syncs = useMemo(() => (list?.jobs || []).filter(j => SYNC_KINDS.has(j.kind)), [list]);
  const live = syncs.filter(j => !ENDED.has(j.state)).map(j => `${j.id}:${j.state}`).join(",");
  const api = useApi(getSources, `${refreshKey}|${live}`);
  const { reload } = api;

  // The source's sync while it is queued or running, from the jobs poll.
  const jobOf = useCallback(
    s => syncs.find(j => j.params?.source === String(s.id) && !ENDED.has(j.state)) || null,
    [syncs]);

  const sync = useCallback(async s => {
    try {
      const r = await syncSource(s.id);
      if (!r?.ok) { toast(r?.error || "Could not start the sync.", "err"); return; }
      started(r.job);
      reload();
    } catch (err) {
      toast(err.message, "err");
    }
  }, [started, reload, toast]);

  const remove = useCallback(async s => {
    const r = await deleteSource(s.id);
    if (r?.ok) reload();
    return r;
  }, [reload]);

  return { ...api, jobOf, sync, remove };
}

// One "Sync all" (GET /api/jobs sync_all): what its summary is hidden and toasted by.
export const batchKey = b => `${b.id}:${b.started_at}`;

/* "Sync all": the backend's last batch (GET /api/jobs `sync_all`), from
   the shared jobs poll. Only live jobs count, so a restart or a replaced
   database can never leave a batch showing.

   { batch: { total, ended, failed, added, current, done, jobs } | null,
     start(), clear(), busy }
   current: the job running now, else the next queued one; jobs: those not
   ended yet (to stop them). clear() hides a finished batch's summary. */
export function useSyncAll() {
  const { list, started } = useJobs();
  const toast = useToast();
  const [hidden, setHidden] = useState(() => localStorage.getItem(HIDDEN_KEY));
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    try {
      const r = await syncAllSources();
      if (!r?.ok) { toast(r?.error || "Could not start the syncs.", "err"); return; }
      for (const e of r.errors || []) toast(`Source ${e.source}: ${e.error}`, "err");
      if (!r.jobs.length) {
        toast(r.skipped ? "Every source is already syncing." : "No source to sync.");
        return;
      }
      started(r.jobs[0]);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  const b = list?.sync_all;
  const key = b ? batchKey(b) : null;
  let batch = null;
  if (b && !(b.done && key === hidden)) {
    const active = new Set(b.active);
    batch = { ...b, jobs: list.jobs.filter(j => active.has(j.id)) };
  }
  function clear() {
    if (!key) return;
    setHidden(key);
    localStorage.setItem(HIDDEN_KEY, key);
  }
  return { batch, start, clear, busy };
}
