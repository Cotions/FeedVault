import { createContext, useContext } from "react";

/* Job status shared by the sidebar indicator, the Jobs page and the Tools
   card. Provided by App, which polls GET /api/jobs: every second or so while
   a job is queued or running, rarely otherwise.

   { list, running, active, newCount, newUntil, started(job) }
   - list:       last GET /api/jobs answer ({ running, queued, jobs, sync_all, new, new_until }) or null
   - active:     running + queued
   - newCount:   posts new since the last "Mark all seen" (list.new)
   - newUntil:   the newest one's first_seen (list.new_until), the mark
                 "Mark all seen" sends, so posts indexed since stay new
   - started(job): tell the poller a job was just started, so it polls now
                 and fast until the job ends (a toast says how it ended) */
export const JobsContext = createContext({
  list: null,
  running: 0,
  active: 0,
  newCount: 0,
  newUntil: null,
  started: () => {},
});

export function useJobs() {
  return useContext(JobsContext);
}

export const ENDED = new Set(["done", "failed", "cancelled", "interrupted"]);

// The userscript's Save button (POST /api/save): one Instagram post.
export const SAVE_KIND = "instaloader-post";

// A job's params as shown: a script's SHA-256 is for the backend's check only.
export const shownParams = job => Object.entries(job.params || {}).filter(([k]) => k !== "sha256").map(([, v]) => v);

// "1 s", "2 min 5 s", "1 h 4 min"; null while it has not started
export function jobDuration(job, now = Date.now() / 1000) {
  if (!job.started_at || (ENDED.has(job.state) && !job.ended_at)) return null;   // ended when FeedVault crashed: unknown
  const s = Math.max(0, Math.round((job.ended_at ?? now) - job.started_at));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
  return `${Math.floor(s / 3600)} h${Math.floor((s % 3600) / 60) ? ` ${Math.floor((s % 3600) / 60)} min` : ""}`;
}
