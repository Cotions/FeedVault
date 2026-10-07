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
