// Performance measurement on a large invented vault (docs/TESTING.md):
//
//     cd frontend && npm run build && node e2e/perf.js [--posts 20000] [--runs 5] [--json out.json]
//
// Starts the browser tests' throwaway instance (harness.js: a tmp dir of
// its own, the test guard, a free port that is never 3380 or 3389) on
// make_demo.py --large N, then measures:
//
// - the index: built from nothing (make_demo.py's own scan, cold) and a
//   rescan of it with nothing changed (POST /api/scan, warm);
// - the hashing worker: its first pass over every file, a few calls the
//   pages make timed while it runs and once it is idle, and the pass the
//   warm rescan starts;
// - the API calls the pages make: each one "uncached" (right after a
//   write, which drops the backend's memo caches, as a sync or a review
//   decision does) and "cached" (the same call again), the median of
//   --runs;
// - the heavy pages in Chromium: from navigation to the last API response
//   and the last DOM change after it (interactive), the median of 3.
//
// Prints a Markdown table; stops the backend by its PID and deletes the
// tmp dir however it ends. Nothing here contacts another server.
import { chromium } from "@playwright/test";
import fs from "node:fs";
import { startInstance, stopInstance } from "./harness.js";

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : dflt;
};
const POSTS = Number(opt("--posts", "20000"));
const RUNS = Number(opt("--runs", "5"));
const JSON_OUT = opt("--json", null);
const PAGE_RUNS = 3;

const median = xs => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const ms = x => (x == null ? "" : `${Math.round(x)}`);

async function call(base, method, url, body) {
  const t = performance.now();
  const r = await fetch(base + url, {
    method,
    headers: { "X-FeedVault": "1", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(300_000),
  });
  const text = await r.text();
  const took = performance.now() - t;
  if (!r.ok) throw new Error(`perf: ${method} ${url}: HTTP ${r.status} ${text.slice(0, 200)}`);
  return { took, bytes: text.length, json: JSON.parse(text) };
}

async function until(fn, what, limitMs = 30 * 60_000) {
  const end = Date.now() + limitMs;
  while (Date.now() < end) {
    if (await fn()) return;
    await new Promise(r => setTimeout(r, 50));
  }
  throw new Error(`perf: ${what} did not finish`);
}

// Calls a page makes, timed one after another while the hashing worker runs
// and again once it is idle (median and slowest).
const DURING = ["/api/posts?limit=60&offset=0", "/api/stats", "/api/sources", "/api/duplicates?kind=content&offset=0&limit=50"];

async function hashingPass(base) {
  const busy = Object.fromEntries(DURING.map(u => [u, []]));
  const end = Date.now() + 30 * 60_000;
  let started = null, last = null;
  while (Date.now() < end) {
    const scan = (await call(base, "GET", "/api/scan")).json;
    last = (await call(base, "GET", "/api/duplicates/status")).json;
    if (last.running && started == null) started = performance.now();
    if (!last.running && !scan.running && last.finished_at) break;
    if (last.running) {
      for (const u of DURING) busy[u].push((await call(base, "GET", u)).took);
    }
    await new Promise(r => setTimeout(r, 50));
  }
  const pass_s = started == null ? 0 : (performance.now() - started) / 1000;
  const idle = Object.fromEntries(DURING.map(u => [u, []]));
  for (let i = 0; i < 20; i++) {
    for (const u of DURING) idle[u].push((await call(base, "GET", u)).took);
  }
  const sum = xs => (xs.length ? { median_ms: median(xs), max_ms: Math.max(...xs), n: xs.length } : null);
  return {
    cold_s: pass_s, hashed: last.hashed, fingerprinted: last.fingerprinted,
    calls: DURING.map(u => ({ url: u, during: sum(busy[u]), idle: sum(idle[u]) })),
  };
}

async function main() {
  const out = { posts: POSTS, index: {}, api: [], pages: [] };
  const lines = [];
  const inst = await startInstance({
    large: POSTS, readyMs: 30 * 60_000,
    log: msg => { console.log(msg); lines.push(msg); },
  });
  try {
    const base = inst.url;
    const built = /Index built in ([\d.]+) s/.exec(lines.join("\n"));
    out.index.cold_s = built ? Number(built[1]) : null;
    // The first start's own scan, then its hashing pass of every file, with
    // the dashboard's calls timed while it runs.
    out.hashing = await hashingPass(base);
    const t = performance.now();
    await call(base, "POST", "/api/scan");
    await until(async () => !(await call(base, "GET", "/api/scan")).json.running, "rescan");
    out.index.warm_s = (performance.now() - t) / 1000;
    // The pass a rescan that changed nothing starts.
    const h = performance.now();
    await until(async () => !(await call(base, "GET", "/api/duplicates/status")).json.running, "hashing");
    out.hashing.warm_s = (performance.now() - h) / 1000;

    const stats = (await call(base, "GET", "/api/stats")).json;
    out.vault = { posts: stats.posts, media: stats.media, unmatched: stats.unmatched };
    const people = (await call(base, "GET", "/api/people")).json;
    const person = people.reduce((a, p) => (p.count > a.count ? p : a), people[0]);   // the busiest
    const authors = (await call(base, "GET", "/api/authors")).json;
    const top = authors[0];
    const collections = (await call(base, "GET", "/api/collections")).json;
    const coll = collections.reduce((a, c) => (c.count > (a?.count || 0) ? c : a), null) || collections[0];
    const first = (await call(base, "GET", "/api/posts?limit=1")).json.posts[0];
    out.vault.people = people.length;
    out.vault.accounts = authors.length;

    const P = "limit=60&offset=0";
    const urls = [
      `/api/posts?${P}`,
      `/api/posts/summary`,
      `/api/posts?${P}&platform=instagram&kind=video`,
      `/api/posts?${P}&review=unreviewed`,
      `/api/posts?review=unreviewed&sort=posted&offset=0&limit=50`,
      `/api/posts?${P}&q=moss`,
      `/api/posts/summary?q=moss`,
      `/api/posts?${P}&q=tag:favourites`,
      `/api/posts?${P}&untagged=1`,
      `/api/posts?${P}&new=1`,
      `/api/posts?${P}&sort=saved&order=asc`,
      `/api/posts?limit=60&offset=15000`,
      `/api/posts?${P}&author=${encodeURIComponent(top.id)}&platform=${top.platform}`,
      `/api/posts?${P}&person=${person.id}`,
      `/api/posts/summary?person=${person.id}`,
      `/api/posts/${first.platform}/${encodeURIComponent(first.post_id)}`,
      `/api/new`,
      `/api/notifications`,
      `/api/people`,
      `/api/people/${person.id}`,
      `/api/people/suggestions`,
      `/api/authors`,
      `/api/stats`,
      `/api/stats?person=${person.id}`,
      `/api/storage`,
      `/api/storage?person=${person.id}`,
      `/api/duplicates?kind=copies&offset=0&limit=50`,
      `/api/duplicates?kind=content&offset=0&limit=50`,
      `/api/duplicates?kind=similar&offset=0&limit=50`,
      `/api/duplicates/status`,
      `/api/trash`,
      `/api/trash/items?offset=0&limit=60`,
      `/api/tags`,
      `/api/collections`,
      `/api/collections/${coll.id}?offset=0&limit=60`,
      `/api/links`,
      `/api/unmatched`,
      `/api/jobs`,
      `/api/sources`,
      `/api/scan`,
      `/api/config`,
    ];
    // A write that changes nothing but drops the memo caches (db._memo):
    // the post's decision set to what it is.
    const kept = (await call(base, "GET", `/api/posts?limit=1&review=kept`)).json.posts[0];
    const touch = () => call(base, "POST", "/api/review", { posts: [kept.id], decision: "keep" });
    for (const url of urls) {
      const cold = [], warm = [];
      let bytes = 0;
      for (let i = 0; i < RUNS; i++) {
        await touch();
        const a = await call(base, "GET", url);
        const b = await call(base, "GET", url);
        cold.push(a.took);
        warm.push(b.took);
        bytes = b.bytes;
      }
      const row = { url, uncached_ms: median(cold), cached_ms: median(warm), bytes };
      out.api.push(row);
      console.log(`${ms(row.uncached_ms).padStart(6)} ${ms(row.cached_ms).padStart(6)} ms  ${url}`);
    }

    const pages = [
      ["/", "Feed"],
      ["/?q=moss", "Feed, search"],
      ["/review", "Review"],
      ["/creators", "Creators"],
      [`/people/${person.id}`, "Person"],
      ["/storage", "Storage"],
      ["/stats", "Stats"],
      ["/duplicates", "Duplicates"],
      ["/trash", "Trash"],
      ["/tags", "Tags"],
      ["/collections", "Collections"],
      [`/collections/${coll.id}`, "Collection"],
      ["/links", "Links"],
      ["/unmatched", "Unmatched"],
      ["/jobs", "Jobs"],
    ];
    const browser = await chromium.launch();
    try {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      // The last DOM change and the last /api/ response, page time.
      await context.addInitScript(() => {
        window.__perf = { mutation: 0, api: 0, nodes: 0 };
        new MutationObserver(() => { window.__perf.mutation = performance.now(); })
          .observe(document, { childList: true, subtree: true, characterData: true, attributes: true });
        const f = window.fetch;
        window.fetch = (...a) => f(...a).then(r => {
          if (String(a[0]).includes("/api/")) {
            r.clone().text().then(() => { window.__perf.api = performance.now(); }, () => {});
          }
          return r;
        });
      });
      const page = await context.newPage();
      for (const [url, name] of pages) {
        const times = [];
        let nodes = 0;
        for (let i = 0; i < PAGE_RUNS; i++) {
          await page.goto("about:blank");
          await touch();                       // what a page meets after any change
          await page.goto(base + url, { waitUntil: "load" });
          // Quiet: no /api/ response and no DOM change for 1 s.
          await page.waitForFunction(() => {
            const p = window.__perf, now = performance.now();
            return p.api > 0 && now - Math.max(p.api, p.mutation) > 1000;
          }, null, { timeout: 120_000, polling: 100 });
          const p = await page.evaluate(() => ({ ...window.__perf, nodes: document.getElementsByTagName("*").length }));
          times.push(Math.max(p.api, p.mutation));
          nodes = p.nodes;
        }
        const row = { url, name, interactive_ms: median(times), dom_nodes: nodes };
        out.pages.push(row);
        console.log(`${ms(row.interactive_ms).padStart(6)} ms ${String(nodes).padStart(6)} nodes  ${name} ${url}`);
      }
    } finally {
      await browser.close();
    }
  } finally {
    await stopInstance(inst);
    console.log(`perf: backend pid ${inst.pid} stopped, ${inst.root} deleted`);
  }

  console.log(`\nVault: ${JSON.stringify(out.vault)}`);
  console.log(`Index: built in ${out.index.cold_s} s (cold), rescan ${out.index.warm_s?.toFixed(2)} s (warm)`);
  const hp = out.hashing;
  console.log(`Hashing: first pass ${hp.cold_s.toFixed(2)} s (${hp.hashed} hashed, ${hp.fingerprinted} fingerprinted), `
    + `after the rescan ${hp.warm_s?.toFixed(2)} s\n`);
  console.log("| API call while hashing | median ms | max ms | idle median ms | idle max ms |\n|---|---:|---:|---:|---:|");
  for (const r of hp.calls) {
    console.log(`| \`${r.url}\` | ${ms(r.during?.median_ms)} | ${ms(r.during?.max_ms)} | ${ms(r.idle.median_ms)} | ${ms(r.idle.max_ms)} |`);
  }
  console.log("");
  console.log("| API call | uncached ms | cached ms | KiB |\n|---|---:|---:|---:|");
  for (const r of out.api) console.log(`| \`${r.url}\` | ${ms(r.uncached_ms)} | ${ms(r.cached_ms)} | ${Math.round(r.bytes / 1024)} |`);
  console.log("\n| Page | interactive ms | DOM nodes |\n|---|---:|---:|");
  for (const r of out.pages) console.log(`| ${r.name} \`${r.url}\` | ${ms(r.interactive_ms)} | ${r.dom_nodes} |`);
  if (JSON_OUT) fs.writeFileSync(JSON_OUT, JSON.stringify(out, null, 2));
}

main().catch(e => { console.error(e); process.exit(1); });
