// Desktop, 1440x900: every page loads clean, the Feed shows posts, and
// Review decides from the keyboard.
import { test, expect, PAGES, openPage, keepTrashUndo } from "./fixtures.js";

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

test("Review: K keeps, D trashes, Z undoes", async ({ page, pageErrors }) => {
  await openPage(page, { name: "Review", path: "/review" });
  await keepTrashUndo(page, pageErrors, {
    keep: () => page.keyboard.press("k"),
    trash: () => page.keyboard.press("d"),
    undo: () => page.keyboard.press("z"),
  });
});
