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

// QA pass 3: Back from a post opened far down the Feed came back at the
// top: the position was applied before the posts had loaded again.
test("Feed: Back from a post returns to where the Feed was scrolled", async ({ page }) => {
  await openPage(page, PAGES[0]);
  const cards = page.locator("a.post-cover");
  expect(await cards.count()).toBeGreaterThan(14);
  const card = cards.nth(14);
  const href = await card.getAttribute("href");
  await card.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  expect(before).toBeGreaterThan(1000);
  await card.click();
  await expect(page).toHaveURL(/\/p\//);
  await idle(page);
  await page.goBack();
  await expect(page.locator("article.post-card").first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before - 50);
  // Not to the pixel: the next page of posts comes in once the position is
  // back, the columns fill again and the browser's scroll anchoring may
  // follow a card of another column (60px further, in CI). Where the Feed
  // was is the card that was opened, on screen again.
  await idle(page);
  await expect(page.locator(`a.post-cover[href="${href}"]`)).toBeInViewport();
});

// #149: Back from a post opened from the keyboard left focus on <body>, so
// the next Tab started from the header. The card that was opened has it.
test("Feed: Back from a post focuses the card that was opened", async ({ page }) => {
  await openPage(page, PAGES[0]);
  const cards = page.locator("a.post-cover");
  expect(await cards.count()).toBeGreaterThan(14);
  const card = cards.nth(14);
  const href = await card.getAttribute("href");
  await card.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/p\//);
  await idle(page);
  await page.goBack();
  const again = page.locator(`a.post-cover[href="${href}"]`);
  await expect(again).toBeFocused();
  await expect(again).toBeInViewport();
  // The next Tab goes on from there, not from the header.
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => !!document.activeElement.closest(".masonry"))).toBe(true);
  await idle(page);
});

test("Review: K keeps, D trashes, Z undoes", async ({ page }) => {
  await openPage(page, { name: "Review", path: "/review" });
  await keepTrashUndo(page, {
    keep: () => page.keyboard.press("k"),
    trash: () => page.keyboard.press("d"),
    undo: () => page.keyboard.press("z"),
  });
});

// QA pass 4: Enter on a focused link (the author, a hashtag, "Open post
// page") kept the post and moved on, instead of following the link.
test("Review: Enter on a focused link follows it and decides nothing", async ({ page }) => {
  const decisions = [];
  page.on("request", r => { if (r.method() === "POST" && new URL(r.url()).pathname === "/api/review") decisions.push(r.postData()); });
  await openPage(page, { name: "Review", path: "/review" });
  await expect(page.locator(".review-session")).toHaveText("0 kept · 0 trashed");
  const open = page.locator(".review-open");
  const href = await open.getAttribute("href");
  await open.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`${href.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
  await idle(page);
  expect(decisions).toEqual([]);
});

// #159: the shortcuts dialog keeps Tab inside it, and gives focus back to
// its button when it closes, from the keyboard or the mouse.
test("Review: the shortcuts dialog holds focus and gives it back", async ({ page }) => {
  await openPage(page, { name: "Review", path: "/review" });
  const dialog = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  const button = page.locator(".review-actions").getByRole("button", { name: "Keyboard shortcuts" });
  const inDialog = () => page.evaluate(() => !!document.activeElement?.closest(".review-help"));

  await page.keyboard.press("?");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
  for (const key of ["Tab", "Tab", "Shift+Tab", "Shift+Tab", "Shift+Tab"]) {
    await page.keyboard.press(key);
    expect(await inDialog(), `after ${key}`).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(button).toBeFocused();

  // Focus on <body> (a click elsewhere) when it opens: Tab still lands in it.
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press("?");
  await expect(dialog).toBeVisible();
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press("Tab");
  expect(await inDialog()).toBe(true);
  await page.keyboard.press("Escape");
  await expect(button).toBeFocused();

  await button.click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(button).toBeFocused();
  await expect(page.locator(".review-session")).toHaveText("0 kept · 0 trashed");
});

// #159's touch sizes are for a touch screen: with a mouse at 1440x900 the
// header, the Post page's carousel and Review's buttons keep their sizes,
// the sidebar shows, a Fullscreen button too, and the stage is as tall.
test("a mouse keeps the desktop's sizes", async ({ page, request }) => {
  await openPage(page, PAGES[0]);
  expect(await page.evaluate(() => matchMedia("(pointer: fine)").matches)).toBe(true);
  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.locator("header .nav-menu-btn")).toBeHidden();
  expect((await page.locator("header").boundingBox()).height).toBeCloseTo(57, 0);
  expect((await page.locator("input.header-search").boundingBox()).height).toBeCloseTo(36, 0);

  const r = await request.get("/api/posts?kind=carousel&limit=1", { headers: H });
  const p = (await r.json()).posts[0];
  await page.goto(`/p/${p.platform}/${encodeURIComponent(p.post_id)}`);
  await expect(page.locator(".carousel-stage")).toBeVisible();
  await idle(page);
  expect((await page.locator(".post-page-head .btn-back").boundingBox()).height).toBeCloseTo(34, 0);
  expect((await page.locator(".carousel-stage").boundingBox()).height).toBeCloseTo(Math.min(900 * 0.78, 820), 0);
  const arrow = await page.locator(".carousel-arrow.next").boundingBox();
  expect([arrow.width, arrow.height].map(Math.round)).toEqual([40, 40]);
  const dot = await page.locator(".carousel-dot").first().boundingBox();
  expect([dot.width, dot.height].map(Math.round)).toEqual([22, 22]);

  await openPage(page, { name: "Review", path: "/review" });
  await expect(page.locator(".review-actions").getByRole("button", { name: "Fullscreen" })).toBeVisible();
  for (const b of await page.locator(".review-actions-row button").all()) {
    expect((await b.boundingBox()).height).toBeLessThan(40);
  }
  expect(await page.locator(".review-actions").evaluate(el => getComputedStyle(el).position)).not.toBe("fixed");
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
  await expect(box).toContainText("5 accounts found");
  await expect(box.locator(".bio-import-kept")).toHaveText("4 links saved to their Links · 2 links already there · 1 link dropped (not a web address Links keeps)");

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
    // Within a pixel: fonts differ on CI by a fraction of one (main moved it 10 px).
    const still = async when => {
      const now = await where();
      expect(Math.abs(now.head - before.head), `head height ${when}`).toBeLessThan(1);
      expect(Math.abs(now.grid - before.grid), `grid top ${when}`).toBeLessThan(1);
    };
    const rename = page.getByRole("button", { name: "Rename", exact: true });
    await rename.click();
    const field = page.getByRole("textbox", { name: "Collection name" });
    await expect(field).toBeFocused();
    await still("with the rename form open");
    // The form sits inside the head, above its bottom line.
    const box = await page.locator(".page-head .tag-rename").boundingBox();
    const head = await page.locator(".page-head").boundingBox();
    expect(box.y + box.height).toBeLessThanOrEqual(head.y + head.height);
    await page.keyboard.press("Escape");
    await expect(rename).toBeFocused();
    await still("after Esc");

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

// A cookies session names which browser profile its cookies come from: the
// select beside the browser lists what the browser has (faked here: the
// instance's HOME has no browser), saves it, and keeps a profile the
// browser no longer has, marked. Then the same on a source of its own.
test("Browser profile: picked in Settings and on a source, a missing one stays shown", async ({ page, request }) => {
  let names = ["cotions", "default", "test", "TRND"];
  await page.route(url => url.pathname === "/api/browsers/profiles", route => route.fulfill({
    json: { profiles: { firefox: names, chrome: ["Default", "Profile 1"], chromium: [], brave: [], edge: [] } } }));
  const before = await (await request.get("/api/config", { headers: H })).json();
  const made = await (await request.post("/api/sources", { headers: H, data: { target: `https://x.com/e2e_profile_${Date.now()}` } })).json();
  expect(made.ok, JSON.stringify(made)).toBe(true);
  const inside = async (el, outer, what) => {
    const a = await el.boundingBox();
    const b = await outer.boundingBox();
    expect(a.x, what).toBeGreaterThanOrEqual(b.x - 0.5);
    expect(a.x + a.width, what).toBeLessThanOrEqual(b.x + b.width + 0.5);
  };
  try {
    await page.goto("/settings#sync");
    const card = page.locator(".card", { hasText: "Instagram sync (instaloader)" });
    await card.getByRole("radio", { name: /Use my browser/ }).check();
    const browser = card.getByRole("combobox", { name: "Browser", exact: true });
    const profile = card.getByRole("combobox", { name: "Browser profile" });
    await expect(profile.locator("option")).toHaveText(["Default (the tool picks)", "cotions", "default", "test", "TRND"]);
    await expect(profile).toHaveValue("");
    // Side by side, neither over the other nor out of the card.
    const [b, p] = [await browser.boundingBox(), await profile.boundingBox()];
    expect(p.x, "the profile starts after the browser").toBeGreaterThanOrEqual(b.x + b.width);
    expect(Math.abs(p.y - b.y), "on the browser's line").toBeLessThan(2);
    await inside(profile, card, "the profile select stays in the card");
    // Another browser: its own profiles, back to Default.
    await profile.selectOption("test");
    await browser.selectOption("chrome");
    await expect(profile).toHaveValue("");
    await expect(profile.locator("option")).toHaveText(["Default (the tool picks)", "Default", "Profile 1"]);
    await browser.selectOption("firefox");
    await profile.selectOption("test");
    await card.getByRole("button", { name: "Save" }).click();
    // (The card's "Saved" line does not outlive the reload a save does: the setting is what is checked.)
    await expect.poll(async () => (await (await request.get("/api/config", { headers: H })).json()).instaloader.session)
      .toEqual({ mode: "cookies", browser: "firefox", profile: "test" });
    await expect(card.getByRole("button", { name: "Save" })).toBeDisabled();
    await expect(profile).toHaveValue("test");

    // Renamed in Firefox since: still the stored one, marked, with why.
    names = ["cotions", "default", "TRND"];
    await page.reload();
    await expect(profile).toHaveValue("test");
    await expect(profile.locator("option")).toHaveText(["Default (the tool picks)", "test · not found", "cotions", "default", "TRND"]);
    const note = card.locator(".browser-pick-note");
    await expect(note).toContainText("firefox has no profile named test now");
    await inside(note, card, "the note stays in the card");
    // Nothing changed since it was loaded: leaving does not ask.
    await page.locator(".settings-tabs").getByRole("link", { name: "Library" }).click();
    await expect(page).toHaveURL(/\/settings#library$/);
    await page.goto("/creators");
    await expect(page.getByRole("alertdialog")).toHaveCount(0);

    // A source's own: its Options, under Login.
    names = ["cotions", "default", "test", "TRND"];
    const row = page.locator(`.source-row[data-source-id="${made.source.id}"]`);
    await row.getByRole("button", { name: "Options" }).click();
    const dialog = page.getByRole("alertdialog", { name: /downloads$/ });
    const login = dialog.getByRole("combobox", { name: "Login a sync uses" });
    await expect(login).toHaveValue("");
    await expect(dialog.getByRole("combobox", { name: "Browser profile" })).toHaveCount(0);
    await login.selectOption("cookies");
    const own = dialog.getByRole("combobox", { name: "Browser profile" });
    await expect(own.locator("option")).toHaveText(["Default (the tool picks)", "cotions", "default", "test", "TRND"]);
    await inside(own, dialog, "the profile select stays in the dialog");
    await own.selectOption("TRND");
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toHaveCount(0);
    const { sources } = await (await request.get("/api/sources", { headers: H })).json();
    expect(sources.find(s => s.id === made.source.id).options.session)
      .toEqual({ mode: "cookies", browser: "firefox", profile: "TRND" });
    await row.getByRole("button", { name: "Options" }).click();
    await expect(login).toHaveValue("cookies");
    await expect(own).toHaveValue("TRND");
    await login.selectOption("");
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toHaveCount(0);
    const again = await (await request.get("/api/sources", { headers: H })).json();
    expect(again.sources.find(s => s.id === made.source.id).options.session).toBeNull();
    await idle(page);
  } finally {
    await request.delete(`/api/sources/${made.source.id}`, { headers: H });
    await request.post("/api/config", { headers: H, data: { instaloader: before.instaloader } });
  }
});

// instaloader reads a browser's cookies with its optional browser_cookie3
// module: the Downloaders card says so, with the command that adds it,
// only when the module is known missing and Instagram syncs use cookies
// (GET /api/downloaders faked here: the instance has no such install).
test("Downloaders: instaloader without browser_cookie3 is warned about, with the command", async ({ page }) => {
  const venv = "/home/someone/.local/share/pipx/venvs/instaloader-with-a-long-name-of-its-own";
  const command = `${venv}/bin/python -m pip install browser_cookie3`;
  let module = { found: false, command };
  let login = { mode: "cookies", browser: "firefox" };
  await page.route(url => url.pathname === "/api/downloaders", async route => {
    const json = await (await route.fetch()).json();
    json.tools = json.tools.map(t => (t.tool === "instaloader" ? { ...t, login, browser_cookie3: module } : t));
    return route.fulfill({ json });
  });
  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/settings#downloaders");
    const row = page.locator(".dl-row", { has: page.locator(".tool-name", { hasText: /^instaloader$/ }) });
    const warn = row.locator(".kv-row", { hasText: "browser_cookie3 is not installed" });
    await expect(warn).toContainText("instaloader cannot read firefox’s cookies without it");
    await expect(warn.locator("code")).toHaveText(command);
    // Nothing of it leaves the row, and the copy button is not under the command.
    const outer = await row.boundingBox();
    const code = await warn.locator("code").boundingBox();
    const copy = await warn.getByRole("button", { name: `Copy ${command}` }).boundingBox();
    for (const [what, b] of [["the command", code], ["its copy button", copy]]) {
      expect(b.x, `${width}: ${what} stays in the row`).toBeGreaterThanOrEqual(outer.x - 0.5);
      expect(b.x + b.width, `${width}: ${what} stays in the row`).toBeLessThanOrEqual(outer.x + outer.width + 0.5);
    }
    expect(copy.x, `${width}: the copy button is after the command`).toBeGreaterThanOrEqual(code.x + code.width - 0.5);
  }
  // Not known (a system install), or no cookies asked for: no warning.
  const warning = page.locator("#downloaders").getByText("browser_cookie3 is not installed");
  module = { found: null, command: "pipx install 'instaloader[browser_cookie3]'" };
  await page.reload();
  await expect(page.locator("#downloaders .dl-row").first()).toBeVisible();
  await expect(warning).toHaveCount(0);
  module = { found: false, command };
  login = { mode: "none" };
  await page.reload();
  await expect(page.locator("#downloaders .dl-row").first()).toBeVisible();
  await expect(warning).toHaveCount(0);
  await idle(page);
});

// Accounts linked to nobody become creators from Creators: one card's Make
// creator (the page stays, for the next card), and "One creator each" on a
// selection. The people made are deleted after: their accounts are free again.
async function unlinkedAccounts(request) {
  const authors = await (await request.get("/api/authors", { headers: H })).json();
  return authors.filter(a => !a.person && a.id != null);
}
async function deletePeopleOf(request, accounts) {
  const people = await (await request.get("/api/people", { headers: H })).json();
  const keys = new Set(accounts.map(a => `${a.platform}:${a.id}`));
  for (const p of people) {
    if (p.accounts.some(a => keys.has(`${a.platform}:${a.id}`))) await request.delete(`/api/people/${p.id}`, { headers: H });
  }
}
const isAccount = a => u => u.platform === a.platform && u.id === a.id;

test("Creators: an account's Make creator makes it a person, and the page stays", async ({ page, request }) => {
  const [a] = await unlinkedAccounts(request);
  expect(a).toBeTruthy();
  try {
    await openPage(page, { name: "Creators", path: "/creators" });
    const make = page.getByRole("button", { name: `Make @${a.handle} a creator`, exact: true });
    const card = page.locator(".creator-card").filter({ has: make });
    await make.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText(`Make @${a.handle} a creator`);
    const field = dialog.getByRole("textbox", { name: "Name" });
    await expect(field).toBeFocused();
    await expect(field).not.toHaveValue("");
    const name = await field.inputValue();
    await dialog.getByRole("button", { name: "Create creator" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator(".toast", { hasText: `@${a.handle} is now a creator` })).toBeVisible();
    await expect(page).toHaveURL(/\/creators$/);
    // Under People now, out of the accounts not linked.
    await expect(card).toHaveCount(0);
    await expect(page.locator(".person-card", { hasText: name })).toHaveCount(1);
    expect((await unlinkedAccounts(request)).some(isAccount(a))).toBe(false);
    await idle(page);
  } finally {
    await deletePeopleOf(request, [a]);
  }
});

test("Creators: One creator each makes a person per selected account", async ({ page, request }) => {
  const chosen = (await unlinkedAccounts(request)).slice(0, 2);
  expect(chosen).toHaveLength(2);
  try {
    await openPage(page, { name: "Creators", path: "/creators" });
    await page.getByRole("button", { name: "Select", exact: true }).click();
    const bar = page.getByRole("toolbar", { name: "Selection" });
    const each = bar.getByRole("button", { name: "One creator each" });
    await expect(each).toBeDisabled();
    const cardOf = a => page.locator(".creator-card:not(.person-card)", { hasText: `@${a.handle}` })
      .locator("button.creator-main");
    await cardOf(chosen[0]).click();
    // One account: the merge button says what it does.
    await expect(bar.getByRole("button", { name: "Make creator" })).toBeEnabled();
    await expect(each).toBeEnabled();
    await cardOf(chosen[1]).click();
    await expect(bar.getByRole("button", { name: "Merge into one person" })).toBeEnabled();
    await each.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("Make 2 creators?");
    await dialog.getByRole("button", { name: "Create creators" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator(".toast", { hasText: "2 creators made." })).toBeVisible();
    await expect(bar).toHaveCount(0);
    const left = await unlinkedAccounts(request);
    const people = await (await request.get("/api/people", { headers: H })).json();
    for (const a of chosen) {
      expect(left.some(isAccount(a))).toBe(false);
      expect(people.filter(p => p.accounts.length === 1 && p.accounts.some(isAccount(a)))).toHaveLength(1);
    }
    await idle(page);
  } finally {
    await deletePeopleOf(request, chosen);
  }
});

// A person's About: a gender preset, then one typed (Enter saves), a
// country picked from the keyboard, a label (Enter on the empty field
// saves). Creators then filters by each, and its text box finds the label.
// The person is made for the test and deleted after.
test("Person: gender, country and a label, and Creators filters by them", async ({ page, request }) => {
  const name = `E2E about ${Date.now()}`;
  const label = `e2e-label-${Date.now()}`;
  const r = await (await request.post("/api/people", { headers: H, data: { name } })).json();
  const pid = r.person.id;
  const about = async () => (await (await request.get(`/api/people/${pid}`, { headers: H })).json());
  try {
    await page.goto(`/people/${pid}`);
    await expect(page.locator("h2.page-title")).toHaveText(name);
    await idle(page);
    const section = page.locator(".person-about");
    const save = section.getByRole("button", { name: "Save", exact: true });
    const presets = section.getByRole("group", { name: "Gender presets" });

    await presets.getByRole("button", { name: "Female" }).click();
    await expect(presets.getByRole("button", { name: "Female" })).toHaveAttribute("aria-pressed", "true");
    await save.click();
    await expect(save).toHaveCount(0);
    expect((await about()).gender).toBe("Female");

    const gender = section.getByLabel("Gender", { exact: true });
    await gender.fill("Genderfluid");
    await expect(presets.getByRole("button", { name: "Female" })).toHaveAttribute("aria-pressed", "false");
    await gender.press("Enter");
    await expect(save).toHaveCount(0);
    expect((await about()).gender).toBe("Genderfluid");

    const country = section.getByRole("combobox", { name: "Country" });
    await country.fill("fran");
    await expect(page.getByRole("option").first()).toContainText("France");
    await country.press("Enter");
    await expect(country).toHaveValue(/France/);
    const add = section.getByRole("combobox", { name: "Add a label…" });
    await add.fill(label);
    await add.press("Enter");
    await expect(section.locator(".label-chip", { hasText: label })).toBeVisible();
    await add.press("Enter");                                  // empty: saves the country and the label
    await expect(save).toHaveCount(0);
    const saved = await about();
    expect([saved.country, saved.labels]).toEqual(["FR", [label]]);
    await idle(page);

    // The label's chip opens Creators filtered by it.
    await section.locator(".label-chip a", { hasText: label }).click();
    await expect(page).toHaveURL(new RegExp(`/creators\\?label=${label}$`));
    const cards = page.locator(".person-card");
    await expect(cards).toHaveCount(1);
    await expect(cards.first()).toContainText(name);
    await expect(cards.first().locator(".person-flag")).toHaveAttribute("aria-label", "France");
    await expect(page.locator(".creator-card:not(.person-card)")).toHaveCount(0);   // accounts have no label
    const filters = page.getByRole("group", { name: "Filter people" });
    await filters.getByLabel("Gender").selectOption({ label: "Genderfluid (1)" });
    await filters.getByLabel("Country").selectOption({ label: "🇫🇷 France (1)" });
    await expect(cards).toHaveCount(1);
    await filters.getByLabel("Gender").selectOption("none");
    await expect(cards).toHaveCount(0);
    await expect(page.locator(".empty", { hasText: "No creator matches." })).toBeVisible();
    await filters.getByRole("button", { name: "Clear filters" }).click();
    await expect(page).toHaveURL(/\/creators$/);
    expect(await cards.count()).toBeGreaterThan(1);

    // The text box matches labels and countries too.
    const box = page.getByRole("textbox", { name: "Filter creators" });
    await box.fill(label);
    await expect(cards).toHaveCount(1);
    await box.fill(`france ${name}`);
    await expect(cards).toHaveCount(1);
    await idle(page);
  } finally {
    await request.delete(`/api/people/${pid}`, { headers: H });
  }
});

// A person's picture: one uploaded (a picture drawn in the page, framed
// by a drag), shown as a 2:3 portrait on their page and their Creators
// card (Portraits, the default), round in Compact, the view kept through
// a reload; then one of their posts' pictures; then Remove, back to the
// letter. The person, made for the test with an account linked to nobody,
// is deleted after (and their picture's files with them).
test("Person: upload a picture, pick one from a post, remove it", async ({ page, request }) => {
  const [a] = (await unlinkedAccounts(request)).filter(x => x.count > 0);
  expect(a).toBeTruthy();
  const name = `E2E picture ${Date.now()}`;
  const r = await (await request.post("/api/people", { headers: H, data: { name, accounts: [{ platform: a.platform, id: a.id }] } })).json();
  const pid = r.person.id;
  const person = async () => (await (await request.get(`/api/people/${pid}`, { headers: H })).json());
  const loaded = img => img.evaluate(el => el.complete && el.naturalWidth > 0);
  // A box 2:3 (to a pixel), at least ``min`` px wide.
  const portrait = async (el, min) => {
    const b = await el.boundingBox();
    expect(b.width).toBeGreaterThanOrEqual(min);
    expect(Math.abs(b.height - b.width * 1.5)).toBeLessThanOrEqual(1.5);
  };
  try {
    await page.goto(`/people/${pid}`);
    await expect(page.locator("h2.page-title")).toHaveText(name);
    await idle(page);
    const bar = page.locator(".person-hero");
    await expect(bar.locator(".avatar-letter")).toHaveAttribute("data-letter", "E");
    await portrait(bar.locator(".avatar-letter"), 160);              // the placeholder is a portrait too
    // Beside the person's links and About, under the title.
    const [pic, links] = [await bar.locator(".avatar-edit").boundingBox(), await bar.locator(".person-links").boundingBox()];
    expect(pic.x + pic.width).toBeLessThan(links.x);
    expect((await page.locator("h2.page-title").boundingBox()).y).toBeLessThan(pic.y);

    // Upload: a 600x400 picture, half red, half blue, made by a canvas.
    await page.getByRole("button", { name: `Add a picture of ${name}` }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText(`${name}'s picture`);
    const png = await page.evaluate(() => {
      const c = document.createElement("canvas");
      c.width = 600; c.height = 400;
      const g = c.getContext("2d");
      g.fillStyle = "#d01010"; g.fillRect(0, 0, 300, 400);
      g.fillStyle = "#1010d0"; g.fillRect(300, 0, 300, 400);
      return c.toDataURL("image/png").split(",")[1];
    });
    await dialog.getByLabel("Picture file").setInputFiles({ name: "me.png", mimeType: "image/png", buffer: Buffer.from(png, "base64") });
    const view = dialog.getByRole("group", { name: /^Crop/ });
    await expect(view.locator("img")).toBeVisible();
    const box = await view.boundingBox();
    expect([Math.round(box.width), Math.round(box.height)]).toEqual([200, 300]);     // the frame is 2:3
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2, { steps: 4 });   // towards the red half
    await page.mouse.up();
    await dialog.getByRole("button", { name: "Save picture" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator(".toast", { hasText: "Picture saved." })).toBeVisible();
    const uploaded = (await person()).avatar;
    expect(uploaded).toMatch(/^[0-9a-f]{20}$/);
    const big = bar.locator("img.avatar-img");
    await expect(big).toHaveAttribute("src", `/media/avatars/${uploaded}.webp`);
    await expect.poll(() => loaded(big)).toBe(true);
    await portrait(big, 160);
    expect(await big.evaluate(el => [el.naturalWidth, el.naturalHeight])).toEqual([267, 400]);  // 2:3, never enlarged
    await idle(page);

    // On their Creators card: a portrait in Portraits (the default), the
    // letter's round place in Compact; the view kept through a reload.
    const filtered = async () => {
      await page.getByRole("textbox", { name: "Filter creators" }).fill(name);
      await expect(page.locator(".person-card", { hasText: name })).toHaveCount(1);
    };
    await page.goto("/creators");
    await filtered();
    const card = page.locator(".person-card", { hasText: name });
    const views = page.getByRole("group", { name: "Show people as" });
    await expect(views.getByRole("button", { name: "Portraits" })).toHaveAttribute("aria-pressed", "true");
    const tall = card.locator("img.avatar-img");
    await expect(tall).toHaveAttribute("src", `/media/avatars/${uploaded}-300.webp`);
    await expect(tall).toHaveAttribute("loading", "lazy");
    await expect.poll(() => loaded(tall)).toBe(true);
    await portrait(tall, 150);
    // The card's buttons sit clear of its count.
    const count = await card.locator(".creator-count").boundingBox();
    for (const b of await card.locator(".portrait-actions .icon-btn").all()) {
      const r = await b.boundingBox();
      expect(r.x >= count.x + count.width || r.y >= count.y + count.height || r.y + r.height <= count.y).toBe(true);
    }
    await views.getByRole("button", { name: "Compact" }).click();
    const small = card.locator("img.avatar-img");
    await expect(small).toHaveAttribute("src", `/media/avatars/${uploaded}-96.webp`);
    await expect.poll(() => loaded(small)).toBe(true);
    const size = await small.boundingBox();
    expect([Math.round(size.width), Math.round(size.height)]).toEqual([36, 36]);
    await page.reload();
    await filtered();
    await expect(views.getByRole("button", { name: "Compact" })).toHaveAttribute("aria-pressed", "true");
    await expect(small).toHaveAttribute("src", `/media/avatars/${uploaded}-96.webp`);
    await views.getByRole("button", { name: "Portraits" }).click();
    await page.reload();
    await filtered();
    await expect(views.getByRole("button", { name: "Portraits" })).toHaveAttribute("aria-pressed", "true");
    await expect(tall).toHaveAttribute("src", `/media/avatars/${uploaded}-300.webp`);
    await idle(page);

    // One of their posts' pictures, cropped in the middle.
    await page.goto(`/people/${pid}`);
    await expect(page.locator("h2.page-title")).toHaveText(name);
    await page.getByRole("button", { name: `Change ${name}'s picture` }).click();
    await dialog.locator(".avatar-media").first().click();
    await expect(view.locator("img")).toBeVisible();
    await dialog.getByRole("button", { name: "Save picture" }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(async () => (await person()).avatar).not.toBe(uploaded);
    const picked = (await person()).avatar;
    expect(picked).toMatch(/^[0-9a-f]{20}$/);
    await expect(big).toHaveAttribute("src", `/media/avatars/${picked}.webp`);
    // The uploaded one is no one's now: gone.
    expect((await request.get(`/media/avatars/${uploaded}.webp`)).status()).toBe(404);
    await idle(page);

    // Remove: the letter again.
    await page.getByRole("button", { name: `Change ${name}'s picture` }).click();
    await dialog.getByRole("button", { name: "Remove picture" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator(".toast", { hasText: "Picture removed." })).toBeVisible();
    await expect(bar.locator(".avatar-letter")).toBeVisible();
    await expect(bar.locator("img.avatar-img")).toHaveCount(0);
    expect((await person()).avatar).toBeNull();
    await idle(page);
  } finally {
    await request.delete(`/api/people/${pid}`, { headers: H });
  }
});
