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

const BATCH_KEY = "feedvault.syncAll";

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

/* "Sync all": the jobs it queued, remembered in localStorage so the progress
   survives a reload, and their progress from the shared jobs poll.

   { batch: { total, ended, failed, added, current, done, jobs } | null,
     start(), clear(), busy }
   current: the job running now, else the next queued one; jobs: those not
   ended yet (to stop them). */
export function useSyncAll() {
  const { list, started } = useJobs();
  const toast = useToast();
  const [ids, setIds] = useState(() => {
    try {
      const v = JSON.parse(localStorage.getItem(BATCH_KEY));
      return Array.isArray(v) && v.every(Number.isInteger) ? v : null;
    } catch {
      return null;
    }
  });
  const [busy, setBusy] = useState(false);

  function remember(v) {
    setIds(v);
    if (v) localStorage.setItem(BATCH_KEY, JSON.stringify(v));
    else localStorage.removeItem(BATCH_KEY);
  }

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
      remember(r.jobs.map(j => j.id));
      started(r.jobs[0]);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(false);
    }
  }

  let batch = null;
  if (ids && list) {
    const jobs = ids.map(id => list.jobs.find(j => j.id === id)).filter(Boolean);
    const ended = jobs.filter(j => ENDED.has(j.state));
    const current = jobs.find(j => j.state === "running") || jobs.find(j => j.state === "queued") || null;
    // A job not listed is either long over (older than the history kept)
    // or newer than the last poll.
    const oldest = list.jobs.length ? Math.min(...list.jobs.map(j => j.id)) : 0;
    const missing = ids.filter(id => !jobs.some(j => j.id === id));
    const pruned = missing.filter(id => id < oldest).length;
    batch = {
      total: ids.length,
      ended: ended.length + pruned,
      failed: ended.filter(j => j.state === "failed").length,
      added: ended.reduce((n, j) => n + (j.result?.added || 0), 0),
      current,
      done: !current && missing.length === pruned,
      jobs: jobs.filter(j => !ENDED.has(j.state)),
    };
  }
  return { batch, start, clear: () => remember(null), busy };
}
