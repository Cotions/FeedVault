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
