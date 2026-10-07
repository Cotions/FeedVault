// Desktop, 1440x900: every page loads clean, the Feed shows posts, Review
// decides from the keyboard, and a person's link-in-bio import (faked).
import { test, expect, PAGES, openPage, keepTrashUndo, stressData, idle, fakeBioImport, BIO_ACCOUNTS } from "./fixtures.js";

test.describe("every page in the nav loads, no console error, no failed /api request", () => {
  for (const p of PAGES) {
    test(p.name, async ({ page }) => {
      await openPage(page, p);
    });
  }
});

test("the Feed shows posts", async ({ page }) => {
  await openPage(page, PAGES[0]);
  await expect(page.locator(".page-count").first()).toHaveText(/^[1-9][\d,]*$/);
  await expect(page.locator("article.post-card").first()).toBeVisible();
  expect(await page.locator("article.post-card").count()).toBeGreaterThan(1);
});

test("Review: K keeps, D trashes, Z undoes", async ({ page }) => {
  await openPage(page, { name: "Review", path: "/review" });
  await keepTrashUndo(page, {
    keep: () => page.keyboard.press("k"),
    trash: () => page.keyboard.press("d"),
    undo: () => page.keyboard.press("z"),
  });
});

async function openPerson(page) {
  await page.goto(`/people/${stressData().person}`);
  await expect(page.locator("h2.page-title")).toBeVisible();
  await idle(page);
  return page.locator(".bio-import");
}

test("Person: link-in-bio import is off until Settings allows it", async ({ page }) => {
  let imports = 0;
  await page.route("**/api/people/*/bio-import", route => { imports += 1; return route.abort(); });
  const box = await openPerson(page);
  await expect(box.getByRole("textbox", { name: "Link-in-bio page" })).toBeDisabled();
  await expect(box.getByRole("button", { name: "Import" })).toBeDisabled();
  const hint = box.getByRole("link", { name: "Turn it on in Settings" });
  await expect(hint).toHaveAttribute("href", "/settings#bio-import");
  await hint.click();
  const card = page.locator("#bio-import");
  await expect(card).toBeVisible();
  await expect(card.getByRole("checkbox", { name: "Import accounts from a link-in-bio page" })).not.toBeChecked();
  await expect(card).toContainText("fetches that one page");
  expect(imports).toBe(0);
});

test("Person: link-in-bio import lists the accounts and Add uses the usual calls", async ({ page }) => {
  const sent = await fakeBioImport(page);
  const box = await openPerson(page);
  const field = box.getByRole("textbox", { name: "Link-in-bio page" });
  await expect(field).toBeEnabled();
  await field.fill("linktr.ee/somebody");
  expect(sent.imports).toEqual([]);                    // typing fetches nothing
  await box.getByRole("button", { name: "Import" }).click();
  await expect(box.locator(".bio-import-row")).toHaveCount(BIO_ACCOUNTS.length);
  expect(sent.imports).toEqual([{ url: "linktr.ee/somebody" }]);
  await expect(box).toContainText("5 accounts found · 2 other links left out");

  const row = handle => box.locator(".bio-import-row", { hasText: `@${handle}` });
  await expect(row("fake.linked")).toContainText("Linked to them");
  await expect(row("fake.linked").getByRole("button")).toHaveCount(0);
  await expect(row("a-very-long-handle")).toContainText("Linked to Somebody Else With A Long Name");
  await expect(row("a-very-long-handle").getByRole("button")).toHaveCount(0);

  // Untrusted text stays text; the only outside links are FeedVault's https profile addresses.
  await expect(row("<img src=x onerror=alert(1)>")).toBeVisible();
  expect(await box.locator("img").count()).toBe(0);
  const hrefs = await box.locator("a").evaluateAll(as => as.map(a => a.getAttribute("href")));
  expect(hrefs.filter(h => !h.startsWith("/people/")).sort()).toEqual([
    "https://www.instagram.com/fake.linked/", "https://www.tiktok.com/@fake.new", "https://www.youtube.com/@x",
    "https://x.com/fake_indexed"]);

  await row("fake_indexed").getByRole("button", { name: /^Add/ }).click();
  await expect(row("fake_indexed")).toContainText("Added");
  expect(sent.links).toEqual([{ add: [{ platform: "twitter", id: "990001" }], remove: [] }]);
  await row("fake.new").getByRole("button", { name: /^Add/ }).click();
  await expect(row("fake.new")).toContainText("Added");
  expect(sent.sources).toEqual([{ target: "https://tiktok.com/@fake.new", person: stressData().person }]);
  expect(sent.imports).toHaveLength(1);                // one fetch per click, none after
});

// #123, #124: a failed sync's notification, for a source with no person,
// opens Creators on that source, scrolled into view and lit for a moment.
// The demo's fake tools fail them: mossy.trails is rate limited (an
// account card), demo.hidden is a private TikTok synced with no cookies
// (a row of its own, whose message points to Settings → Sync).
const H = { "X-FeedVault": "1" };

async function failedSync(page, target) {
  const { sources } = await (await page.request.get("/api/sources", { headers: H })).json();
  const src = sources.find(s => s.target === target);
  expect(src, target).toBeTruthy();
  const r = await (await page.request.post(`/api/sources/${src.id}/sync`, { headers: H, data: {} })).json();
  expect(r.ok, JSON.stringify(r)).toBe(true);
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${r.job.id}`, { headers: H })).json()).state,
                    { timeout: 20_000 }).toBe("failed");
  return src.id;
}

test("a failed sync's notification lands on its source, highlighted", async ({ page }) => {
  // No pause between syncs of a tool: a retry runs at once too.
  const cfg = await page.request.post("/api/config", { headers: H, data: { instaloader: { pause: 0 }, "yt-dlp": { pause: 0 } } });
  expect(cfg.ok()).toBe(true);
  const cases = [
    { target: "mossy.trails", lit: ".creator-card.is-flash", name: "@mossy.trails" },
    { target: "https://tiktok.com/@demo.hidden", lit: ".source-row.is-flash", name: "demo.hidden",
      message: /^Private profile and no cookies in use: .*Settings → Sync$/ },
  ];
  for (const c of cases) c.id = await failedSync(page, c.target);

  for (const c of cases) {
    await openPage(page, PAGES[0]);
    await page.locator(".side-bell > button").click();
    const entry = page.locator(`.notif-panel .notif-entry[href="/creators?source=${c.id}"]`).first();
    await expect(entry).toBeVisible();
    await entry.click();
    await expect(page).toHaveURL(new RegExp(`/creators\\?source=${c.id}$`));
    const lit = page.locator(c.lit);
    await expect(lit).toHaveCount(1);
    await expect(lit).toContainText(c.name);
    await expect(lit).toBeInViewport();
    if (c.message) await expect(lit.locator(".source-message")).toHaveText(c.message);
    // Lit for a moment, then ?source= goes: the same entry can point at it again.
    await expect(page.locator(".is-flash")).toHaveCount(0, { timeout: 6_000 });
    await expect(page).toHaveURL(/\/creators$/);
    await idle(page);
  }
});

// #126: Enter in New person's Name creates the person, as Create does.
test("New person: Enter in Name creates them", async ({ page, request }) => {
  const name = `E2E enter ${Date.now()}`;
  let pid = null;
  try {
    await openPage(page, { name: "Creators", path: "/creators" });
    await page.getByRole("button", { name: "New person" }).click();
    const dialog = page.getByRole("alertdialog");
    const field = dialog.getByRole("textbox", { name: "Name" });
    await expect(field).toBeFocused();
    await field.fill(name);
    await field.press("Enter");
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/people\/\d+$/);
    pid = page.url().match(/\/people\/(\d+)$/)[1];
    await expect(page.locator("h2.page-title")).toHaveText(name);
    await idle(page);
  } finally {
    if (pid) await request.delete(`/api/people/${pid}`, { headers: H });
  }
});

// #126: a Sync all whose last jobs were cancelled does not count them as
// synced, in its toast and in the summary on Creators. The batch is faked.
test("Sync all's summary counts cancelled syncs apart", async ({ page }) => {
  let done = false;
  await page.route(url => url.pathname === "/api/jobs", async route => {
    const res = await route.fetch();
    const body = await res.json();
    const batch = { id: 990001, started_at: 1727500000, total: 7, ended: done ? 7 : 5, failed: 3, cancelled: done ? 2 : 0,
                    added: 0, profiles: 0, first: null, current: null, jobs: [990001], active: [], done };
    await route.fulfill({ response: res, json: { ...body, running: done ? body.running : 1, sync_all: batch } });
  });
  await openPage(page, { name: "Creators", path: "/creators" });
  await expect(page.locator(".sync-all")).toContainText("Syncing");
  done = true;
  await expect(page.locator(".toast", { hasText: "failed" })).toHaveText(/3 of 5 syncs failed, 2 cancelled/);
  await expect(page.locator(".sync-all > span")).toHaveText("Synced 5 sources: 0 new posts, 3 failed, 2 cancelled. Hide");
  await idle(page);
});

// #126: the new counts (the sidebar's, the Feed's chip) follow a delete at
// once, not with the next idle jobs poll 15 s later. The delete and the
// counts are faked: the demo's posts stay as they are.
test("deleting posts from the Feed updates the new counts at once", async ({ page }) => {
  let deleted = false;
  await page.route(url => url.pathname === "/api/jobs", async route => {
    const res = await route.fetch();
    await route.fulfill({ response: res, json: { ...(await res.json()), new: deleted ? 4 : 5 } });
  });
  await page.route(url => url.pathname === "/api/delete", async route => {
    const ids = route.request().postDataJSON().posts;
    deleted = true;
    await route.fulfill({ json: { ok: true, posts: ids, files: 1, bytes: 1024, errors: [] } });
  });
  await openPage(page, PAGES[0]);
  const sidebar = page.locator("#main-nav .side-new");
  const chip = page.getByRole("button", { name: /^New since last visit/ });
  await expect(sidebar).toHaveText("5 new");
  await expect(chip).toHaveText("New since last visit (5)");

  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.locator("article.post-card .select-check").first().click();
  await page.getByRole("button", { name: "Delete…" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete 1 post" }).click();
  await expect(page.getByRole("alertdialog")).toBeHidden();
  await expect(sidebar).toHaveText("4 new", { timeout: 3_000 });
  await expect(chip).toHaveText("New since last visit (4)", { timeout: 3_000 });
  await idle(page);
});

// #139: jobs that only wait (queued, pausing between downloads) show a
// still icon, not a spinner, and the pause reads "starts in 13 s" as on
// Creators. The waiting jobs are faked into GET /api/jobs: nothing runs.
test("jobs that only wait: a still icon, and the pause in seconds", async ({ page }) => {
  let running = false;
  await page.route(url => url.pathname === "/api/jobs", async route => {
    const res = await route.fetch();
    const real = await res.json();
    const now = Date.now() / 1000;
    const fake = (id, state, extra) => ({
      id, kind: "instaloader-profile", label: `Sync @e2e.wait${id}`, state, argv: ["instaloader", `e2e.wait${id}`],
      params: {}, created_at: now - 5, started_at: state === "running" ? now - 2 : null, ended_at: null,
      exit_code: null, message: null, result: null, ...extra,
    });
    const jobs = [fake(90002, "queued", { waits_until: now + 13 }), fake(90001, running ? "running" : "queued", {})];
    await route.fulfill({ response: res, json: { ...real, running: running ? 1 : 0, queued: running ? 1 : 2, jobs: [...jobs, ...real.jobs] } });
  });
  await openPage(page, PAGES.find(p => p.name === "Jobs"));
  const badge = page.locator('#main-nav a[href="/jobs"] .side-badge');
  await expect(badge).toHaveText("2");
  await expect(badge.locator(".spin")).toHaveCount(0);
  await expect(page.locator("header .nav-jobs-live .spin")).toHaveCount(0);
  const waiting = page.locator(".job-row", { hasText: "e2e.wait90002" }).locator(".job-when");
  await expect(waiting).toHaveText(/^pausing between downloads, starts in 1[0-3] s$/);
  await expect(waiting).toHaveAttribute("title", /^Starts at /);

  // One running: the spinner is back (on the Feed: Jobs would read the fake's log).
  running = true;
  await openPage(page, PAGES[0]);
  await expect(badge.locator(".spin")).toHaveCount(1);
  await expect(page.locator("header .nav-jobs-live .spin")).toHaveCount(1);
  await idle(page);
});

// #139: renaming a collection keeps its head's height, so the posts stay
// where they are; Esc gives focus back to Rename. The hints name the arrows.
test("renaming a collection keeps the page still", async ({ page, request }) => {
  const H = { "X-FeedVault": "1" };
  const { collection } = await (await request.post("/api/collections", { headers: H, data: { name: `E2E rename ${Date.now()}` } })).json();
  try {
    const ids = (await (await request.get("/api/posts?limit=2", { headers: H })).json()).posts.map(p => p.id);
    await request.post(`/api/collections/${collection.id}/add`, { headers: H, data: { posts: ids } });
    await page.goto(`/collections/${collection.id}`);
    await expect(page.locator(".collection-tile")).toHaveCount(2);
    await expect(page.locator(".collection-hint")).toHaveText("Drag a post onto another to move it there, or use its arrows.");
    await idle(page);
    const where = () => page.evaluate(() => ({
      head: document.querySelector(".page-head").getBoundingClientRect().height,
      grid: document.querySelector(".collection-posts").getBoundingClientRect().top,
    }));
    const before = await where();
    const rename = page.getByRole("button", { name: "Rename", exact: true });
    await rename.click();
    const field = page.getByRole("textbox", { name: "Collection name" });
    await expect(field).toBeFocused();
    expect(await where()).toEqual(before);
    // The form sits inside the head, above its bottom line.
    const box = await page.locator(".page-head .tag-rename").boundingBox();
    const head = await page.locator(".page-head").boundingBox();
    expect(box.y + box.height).toBeLessThanOrEqual(head.y + head.height);
    await page.keyboard.press("Escape");
    await expect(rename).toBeFocused();
    expect(await where()).toEqual(before);

    await page.goto("/collections");
    await expect(page.locator(".collection-hint")).toHaveText("Drag a collection onto another to move it there, or use its arrows.");
    await idle(page);
  } finally {
    await request.post(`/api/collections/${collection.id}/delete`, { headers: H });
  }
});

// #139: a source's rename suggestion keeps Accept and Dismiss together
// (the demo's quiet_kiln, now quiet.kiln.studio).
test("a rename suggestion's Accept and Dismiss stay on one line", async ({ page }) => {
  await page.goto("/creators");
  const box = page.locator(".source-rename", { hasText: "quiet.kiln.studio" }).first();
  await expect(box).toBeVisible();
  const accept = await box.getByRole("button", { name: "Accept" }).boundingBox();
  const dismiss = await box.getByRole("button", { name: "Dismiss" }).boundingBox();
  expect(Math.abs(accept.y - dismiss.y), "Dismiss on Accept's line").toBeLessThan(2);
  expect(dismiss.x).toBeGreaterThan(accept.x);
  const outer = await box.boundingBox();
  expect(dismiss.x + dismiss.width).toBeLessThanOrEqual(outer.x + outer.width + 0.5);
  await idle(page);
});
