// Long-session check: a page left open with a video playing on loop, as a
// user leaves Review (or a post) running for an hour.
//
//     cd frontend && npm run build && npm run perf:long -- [--minutes 5] [--every 15] [--page review|post]
//                                    [--reduced-motion] [--json out.json]
//
// Starts the browser tests' throwaway instance (harness.js: a tmp dir of its
// own, the test guard, a free port that is never 3380 or 3389, nothing
// reaching another server), swaps its demo videos (placeholders) for a real
// 2 s WebM recorded in the browser itself (canvas + MediaRecorder: no ffmpeg,
// no download), opens the page with that video looping, and every --every
// seconds, after a forced garbage collection, samples:
//
// - the JS heap in use, DOM nodes, event listeners and <video> elements;
// - the main thread's work since the last sample (script, style, layout,
//   task time, as % of wall time) and the frames painted (rAF ticks/s) with
//   the slowest one;
// - the backend's resident memory, open files and threads (Linux, /proc).
//
// --page review: Review on the videos (autoplay, loop); --page post: a video
// post's page, its video played on loop as a user would. --reduced-motion
// runs it with the backdrop's rain stopped, to see what that costs.
//
// Prints a table and the growth from the first sample after warm-up to the
// last; exits 1 when the heap, the nodes or the listeners grow past what a
// page that holds still may (--max-heap-mb, default 8; nodes and listeners
// 10 %) or the backend's open files grow by more than 20, so it can gate a
// change. Stops the backend and deletes the tmp dir however it ends.
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { offsite, startInstance, stopInstance } from "./harness.js";

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : dflt;
};
const MINUTES = Number(opt("--minutes", "5"));
const EVERY_S = Number(opt("--every", "15"));
const PAGE = opt("--page", "review");
const MAX_HEAP_MB = Number(opt("--max-heap-mb", "8"));
const JSON_OUT = opt("--json", null);
// The backdrop's rain stops under prefers-reduced-motion: what it costs.
const REDUCED = args.includes("--reduced-motion");
const WARMUP_S = 30;
if (!new Set(["review", "post"]).has(PAGE)) throw new Error(`longsession: --page review or post, not ${PAGE}`);

// A 2 s VP8 clip, recorded from a canvas in the browser: Playwright's
// Chromium plays WebM, not the demo's H.264 (and the e2e demo has no ffmpeg).
async function recordClip(browser) {
  const page = await browser.newPage();
  try {
    const b64 = await page.evaluate(async () => {
      const c = document.createElement("canvas");
      c.width = 320; c.height = 568;
      const g = c.getContext("2d");
      const rec = new MediaRecorder(c.captureStream(30), { mimeType: "video/webm;codecs=vp8" });
      const parts = [];
      rec.ondataavailable = e => parts.push(e.data);
      let n = 0;
      const draw = setInterval(() => {
        g.fillStyle = `hsl(${(n += 7) % 360} 60% 40%)`;
        g.fillRect(0, 0, c.width, c.height);
        g.fillStyle = "#fff";
        g.font = "28px sans-serif";
        g.fillText(`frame ${n / 7}`, 40, 280);
      }, 33);
      rec.start();
      await new Promise(r => setTimeout(r, 2000));
      const done = new Promise(r => { rec.onstop = r; });
      rec.stop();
      await done;
      clearInterval(draw);
      const buf = await new Blob(parts, { type: "video/webm" }).arrayBuffer();
      let s = "";
      const u8 = new Uint8Array(buf);
      for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000));
      return btoa(s);
    });
    return Buffer.from(b64, "base64");
  } finally {
    await page.close();
  }
}

function videoFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...videoFiles(p));
    else if (/\.(mp4|webm|mov|m4v)$/i.test(e.name)) out.push(p);
  }
  return out;
}

async function ask(base, url) {
  const r = await fetch(base + url, { headers: { "X-FeedVault": "1" }, signal: AbortSignal.timeout(60_000) });
  if (!r.ok) throw new Error(`longsession: GET ${url}: HTTP ${r.status}`);
  return r.json();
}

// The backend's resident memory, open files and threads (Linux: /proc).
function backend(pid) {
  try {
    const status = fs.readFileSync(`/proc/${pid}/status`, "utf8");
    const num = k => Number(new RegExp(`^${k}:\\s+(\\d+)`, "m").exec(status)?.[1]);
    return { rssMB: num("VmRSS") / 1024, threads: num("Threads"), fds: fs.readdirSync(`/proc/${pid}/fd`).length };
  } catch {
    return { rssMB: NaN, threads: NaN, fds: NaN };
  }
}

async function sample(page, cdp, pid) {
  await cdp.send("HeapProfiler.collectGarbage");
  const { metrics } = await cdp.send("Performance.getMetrics");
  const m = Object.fromEntries(metrics.map(x => [x.name, x.value]));
  const inPage = await page.evaluate(() => {
    const f = window.__long;
    const out = { frames: f.frames, worst: f.worst, videos: document.querySelectorAll("video").length,
                  playing: [...document.querySelectorAll("video")].filter(v => !v.paused).length };
    f.frames = 0; f.worst = 0;
    return out;
  });
  return {
    t: Date.now(),
    heapMB: m.JSHeapUsedSize / 1048576,
    nodes: m.Nodes, listeners: m.JSEventListeners, documents: m.Documents,
    script: m.ScriptDuration, style: m.RecalcStyleDuration, layout: m.LayoutDuration, task: m.TaskDuration,
    layouts: m.LayoutCount, styles: m.RecalcStyleCount,
    ...inPage,
    ...backend(pid),
  };
}

async function main() {
  const inst = await startInstance({ log: msg => console.log(msg) });
  const browser = await chromium.launch();
  const rows = [];
  let failed = false;
  try {
    const base = inst.url;
    const clip = await recordClip(browser);
    const files = videoFiles(path.join(inst.root, "vault"));
    for (const f of files) fs.writeFileSync(f, clip);
    console.log(`longsession: ${files.length} demo videos replaced by a ${clip.length} B WebM clip`);

    let url;
    if (PAGE === "review") url = "/review?kind=video";
    else {
      const p = (await ask(base, "/api/posts?limit=1&kind=video")).posts[0];
      url = `/p/${p.platform}/${encodeURIComponent(p.post_id)}`;
    }

    const context = await browser.newContext({ viewport: { width: 1440, height: 900 },
                                                ...(REDUCED ? { reducedMotion: "reduce" } : {}) });
    await context.route(u => offsite(u.href, base), route => route.abort("blockedbyclient"));
    await context.addInitScript(() => {
      const f = window.__long = { frames: 0, worst: 0, last: 0 };
      const tick = now => {
        if (f.last) f.worst = Math.max(f.worst, now - f.last);
        f.last = now;
        f.frames++;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    const page = await context.newPage();
    page.on("pageerror", e => console.log(`page error: ${e.message}`));
    await page.goto(base + url, { waitUntil: "load" });
    await page.waitForSelector("video", { timeout: 30_000 });
    // The post page's video does not start by itself: play it, on loop, as a user would.
    await page.evaluate(() => {
      const v = [...document.querySelectorAll("video")].find(x => x.offsetParent);
      if (v) { v.loop = true; v.muted = true; v.play().catch(() => {}); }
    });
    await page.waitForTimeout(2000);
    const playing = await page.evaluate(() => [...document.querySelectorAll("video")].some(v => !v.paused && v.readyState >= 2));
    console.log(`longsession: ${base}${url}, video playing: ${playing}`);

    const cdp = await context.newCDPSession(page);
    await cdp.send("Performance.enable");
    await page.waitForTimeout(WARMUP_S * 1000);
    let prev = await sample(page, cdp, inst.pid);
    const first = prev;
    const end = Date.now() + MINUTES * 60_000;
    console.log("  min  heap MB  nodes  listeners  videos  fps  worst ms  main thread %  (script/style/layout)  backend MB/fds/threads");
    while (Date.now() < end) {
      await page.waitForTimeout(EVERY_S * 1000);
      const s = await sample(page, cdp, inst.pid);
      const wall = (s.t - prev.t) / 1000;
      const pct = k => (100 * (s[k] - prev[k])) / wall;
      const row = {
        min: (s.t - first.t) / 60_000, heapMB: s.heapMB, nodes: s.nodes, listeners: s.listeners,
        videos: s.videos, playing: s.playing, fps: s.frames / wall, worstMs: s.worst,
        taskPct: pct("task"), scriptPct: pct("script"), stylePct: pct("style"), layoutPct: pct("layout"),
        layoutsPerS: (s.layouts - prev.layouts) / wall, stylesPerS: (s.styles - prev.styles) / wall,
        backendMB: s.rssMB, backendFds: s.fds, backendThreads: s.threads,
      };
      rows.push(row);
      console.log(`${row.min.toFixed(1).padStart(5)}  ${row.heapMB.toFixed(2).padStart(7)}  ${String(row.nodes).padStart(5)}  `
        + `${String(row.listeners).padStart(9)}  ${`${row.playing}/${row.videos}`.padStart(6)}  ${row.fps.toFixed(0).padStart(3)}  `
        + `${row.worstMs.toFixed(0).padStart(8)}  ${row.taskPct.toFixed(1).padStart(13)}  `
        + `(${row.scriptPct.toFixed(1)}/${row.stylePct.toFixed(1)}/${row.layoutPct.toFixed(1)})`.padEnd(22)
        + `${row.backendMB.toFixed(1)}/${row.backendFds}/${row.backendThreads}`);
      prev = s;
    }
    const last = prev;
    const growth = {
      heapMB: last.heapMB - first.heapMB,
      nodes: last.nodes - first.nodes,
      listeners: last.listeners - first.listeners,
      backendMB: last.rssMB - first.rssMB,
      backendFds: last.fds - first.fds,
    };
    const avg = k => rows.reduce((a, r) => a + r[k], 0) / Math.max(1, rows.length);
    const summary = {
      page: url, minutes: MINUTES, playing,
      first: { heapMB: first.heapMB, nodes: first.nodes, listeners: first.listeners },
      last: { heapMB: last.heapMB, nodes: last.nodes, listeners: last.listeners },
      growth,
      avg: { fps: avg("fps"), taskPct: avg("taskPct"), scriptPct: avg("scriptPct"), stylePct: avg("stylePct"),
             layoutPct: avg("layoutPct"), layoutsPerS: avg("layoutsPerS"), stylesPerS: avg("stylesPerS") },
    };
    console.log(`\nGrowth over ${MINUTES} min: heap ${growth.heapMB >= 0 ? "+" : ""}${growth.heapMB.toFixed(2)} MB, `
      + `nodes ${growth.nodes >= 0 ? "+" : ""}${growth.nodes}, listeners ${growth.listeners >= 0 ? "+" : ""}${growth.listeners}; `
      + `backend ${growth.backendMB >= 0 ? "+" : ""}${growth.backendMB.toFixed(1)} MB, ${growth.backendFds >= 0 ? "+" : ""}${growth.backendFds} open files`);
    console.log(`Average: ${summary.avg.fps.toFixed(0)} fps, main thread busy ${summary.avg.taskPct.toFixed(1)} % `
      + `(script ${summary.avg.scriptPct.toFixed(1)}, style ${summary.avg.stylePct.toFixed(1)}, layout ${summary.avg.layoutPct.toFixed(1)}), `
      + `${summary.avg.stylesPerS.toFixed(1)} style recalcs/s, ${summary.avg.layoutsPerS.toFixed(1)} layouts/s`);
    if (!playing) { console.log("longsession: the video never played"); failed = true; }
    if (growth.heapMB > MAX_HEAP_MB) { console.log(`longsession: heap grew past ${MAX_HEAP_MB} MB`); failed = true; }
    if (growth.nodes > Math.max(50, first.nodes * 0.1)) { console.log("longsession: DOM nodes keep growing"); failed = true; }
    if (growth.listeners > Math.max(20, first.listeners * 0.1)) { console.log("longsession: listeners keep growing"); failed = true; }
    if (growth.backendFds > 20) { console.log("longsession: the backend keeps files open"); failed = true; }
    if (JSON_OUT) fs.writeFileSync(JSON_OUT, JSON.stringify({ summary, rows }, null, 2));
  } finally {
    await browser.close();
    await stopInstance(inst);
    console.log(`longsession: backend pid ${inst.pid} stopped, ${inst.root} deleted`);
  }
  if (failed) process.exit(1);
}

main().catch(e => { console.error(e); process.exit(1); });
