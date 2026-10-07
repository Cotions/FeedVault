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
// - the API calls the pages make: each one "uncached" (right after a
//   write, which drops the backend's memo caches, as a sync or a review
//   decision does) and "cached" (the same call again), the median of
//   --runs;
// - the heavy pages in Chromium: from navigation to the last API response
//   and the last DOM change after it (interactive), the median of 3;
// - the long list pages (Storage, Unmatched, Links, Creators): the rows in
//   the DOM of those listed, the DOM's size, interactive, and one thing a
//   user does there (the slowest input's event handlers, its time to the next
//   paint, and the time until the page is quiet again), the median of 3.
//   --lists-only measures those alone.
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
// Only the long list pages (Storage, Unmatched, Links, Creators): no API
// table and no other page, for a quicker look at those.
const LISTS_ONLY = args.includes("--lists-only");
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

// Waits until no /api/ response and no DOM change came for 1 s; returns
// the page time of the last of them.
async function quiet(page) {
  await page.waitForFunction(() => {
    const p = window.__perf, now = performance.now();
    return p.api > 0 && now - Math.max(p.api, p.mutation) > 1000;
  }, null, { timeout: 120_000, polling: 100 });
  return page.evaluate(() => Math.max(window.__perf.api, window.__perf.mutation));
}

// The long list pages: how many of the page's rows are in the DOM, the
// DOM's size, the time to interactive, and one thing a user does there:
// the slowest input's handlers (React's render included) and its time to the
// next paint (Event Timing, what INP reads; headless Chromium adds 40-60 ms
// to any input), and the time until the page is quiet again. ``total(api)``
// is how many rows the page lists, from the API.
const LISTS = [
  {
    name: "Storage", url: "/storage", rows: ".storage-table tbody tr:not(.win-gap)",
    total: api => api.storage.by_author.length,
    action: ["sort by posts, twice", async page => {
      const b = page.getByRole("button", { name: "Posts", exact: true });
      await b.click();
      await b.click();
    }],
  },
  {
    name: "Unmatched", url: "/unmatched", rows: ".data-table tbody tr:not(.win-gap)",
    total: api => api.unmatched.length,
    action: ["type \"img_001\" in its search", async page => {
      const box = page.getByLabel("Search unmatched files");
      if (!(await box.count())) return false;   // the page had none before the list pass
      await box.pressSequentially("img_001");
      return true;
    }],
  },
  {
    name: "Links", url: "/links", rows: ".link-list > li:not(.win-gap)",
    total: api => api.links.links.length,
    action: ["type \"link 1\" in its search", async page => { await page.getByLabel("Search links").pressSequentially("link 1"); }],
  },
  {
    name: "Creators", url: "/creators", rows: ".creator-card",
    total: api => api.people.length + api.authors.filter(a => !a.person).length,
    action: ["type \"ma\" in its filter, then clear it", async page => {
      const box = page.getByLabel("Filter creators");
      await box.pressSequentially("ma");
      await box.press("Backspace");
      await box.press("Backspace");
    }],
  },
];

async function measureList(page, base, list, touch) {
  await page.goto("about:blank");
  await touch();
  await page.goto(base + list.url, { waitUntil: "load" });
  const interactive = await quiet(page);
  const counted = await page.evaluate(sel => ({
    rows: document.querySelectorAll(sel).length,
    nodes: document.getElementsByTagName("*").length,
  }), list.rows);
  const [label, act] = list.action;
  await page.evaluate(() => { window.__perf.events = []; window.__perf.t0 = performance.now(); });
  if ((await act(page)) === false) return { interactive, ...counted, action: null, input: null, handlers: null, settle: null };
  // Some interactions fetch, some only render: either way, quiet again.
  await page.waitForTimeout(300);
  await page.waitForFunction(() => performance.now() - Math.max(window.__perf.mutation, window.__perf.api) > 1000,
                             null, { timeout: 120_000, polling: 100 });
  const after = await page.evaluate(() => ({
    input: Math.max(0, ...window.__perf.events.map(e => e.d)),
    handlers: Math.max(0, ...window.__perf.events.map(e => e.p)),
    settle: Math.max(window.__perf.mutation, window.__perf.api) - window.__perf.t0,
  }));
  return { interactive, ...counted, action: label, ...after };
}

async function main() {
  const out = { posts: POSTS, index: {}, api: [], pages: [], lists: [] };
  const lines = [];
  const inst = await startInstance({
    large: POSTS, readyMs: 30 * 60_000,
    log: msg => { console.log(msg); lines.push(msg); },
  });
  try {
    const base = inst.url;
    const built = /Index built in ([\d.]+) s/.exec(lines.join("\n"));
    out.index.cold_s = built ? Number(built[1]) : null;
    // The first start's own scan and hashing pass, out of the way.
    await until(async () => !(await call(base, "GET", "/api/duplicates/status")).json.running, "hashing");
    const t = performance.now();
    await call(base, "POST", "/api/scan");
    await until(async () => !(await call(base, "GET", "/api/scan")).json.running, "rescan");
    out.index.warm_s = (performance.now() - t) / 1000;
    await until(async () => !(await call(base, "GET", "/api/duplicates/status")).json.running, "hashing");

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
    for (const url of LISTS_ONLY ? [] : urls) {
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
        window.__perf = { mutation: 0, api: 0, nodes: 0, events: [] };
        // Event Timing: each input's time to the next paint.
        try {
          new PerformanceObserver(l => { for (const e of l.getEntries()) window.__perf.events.push({ d: e.duration, p: e.processingEnd - e.processingStart }); })
            .observe({ type: "event", durationThreshold: 16, buffered: true });
        } catch { /* not supported: no input numbers */ }
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
      for (const [url, name] of LISTS_ONLY ? [] : pages) {
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
      const api = {
        storage: (await call(base, "GET", "/api/storage")).json,
        unmatched: (await call(base, "GET", "/api/unmatched")).json,
        links: (await call(base, "GET", "/api/links")).json,
        people, authors,
      };
      for (const list of LISTS) {
        const runs = [];
        for (let i = 0; i < PAGE_RUNS; i++) runs.push(await measureList(page, base, list, touch));
        const pick = k => (runs[0][k] == null ? null : median(runs.map(r => r[k])));
        const row = {
          name: list.name, url: list.url, rows: runs[0].rows, total: list.total(api), dom_nodes: runs[0].nodes,
          interactive_ms: pick("interactive"), action: runs[0].action, input_ms: pick("input"), handlers_ms: pick("handlers"), settled_ms: pick("settle"),
        };
        out.lists.push(row);
        console.log(`${ms(row.interactive_ms).padStart(6)} ms ${String(row.dom_nodes).padStart(6)} nodes `
                    + `${String(row.rows).padStart(5)} rows  ${row.name}: ${row.action || "-"} ${ms(row.handlers_ms)} / ${ms(row.input_ms)} / ${ms(row.settled_ms)} ms`);
      }
    } finally {
      await browser.close();
    }
  } finally {
    await stopInstance(inst);
    console.log(`perf: backend pid ${inst.pid} stopped, ${inst.root} deleted`);
  }

  console.log(`\nVault: ${JSON.stringify(out.vault)}`);
  console.log(`Index: built in ${out.index.cold_s} s (cold), rescan ${out.index.warm_s?.toFixed(2)} s (warm)\n`);
  console.log("| API call | uncached ms | cached ms | KiB |\n|---|---:|---:|---:|");
  for (const r of out.api) console.log(`| \`${r.url}\` | ${ms(r.uncached_ms)} | ${ms(r.cached_ms)} | ${Math.round(r.bytes / 1024)} |`);
  if (out.lists.length) {
    console.log("\n| List page | rows in the DOM | DOM nodes | interactive ms | interaction | slowest handlers ms | slowest input ms | settled ms |"
                + "\n|---|---:|---:|---:|---|---:|---:|---:|");
    for (const r of out.lists) {
      console.log(`| ${r.name} | ${r.rows} of ${r.total} | ${r.dom_nodes} | ${ms(r.interactive_ms)} | ${r.action || "none"} `
                  + `| ${ms(r.handlers_ms)} | ${ms(r.input_ms)} | ${ms(r.settled_ms)} |`);
    }
  }
  console.log("\n| Page | interactive ms | DOM nodes |\n|---|---:|---:|");
  for (const r of out.pages) console.log(`| ${r.name} \`${r.url}\` | ${ms(r.interactive_ms)} | ${r.dom_nodes} |`);
  if (JSON_OUT) fs.writeFileSync(JSON_OUT, JSON.stringify(out, null, 2));
}

main().catch(e => { console.error(e); process.exit(1); });
