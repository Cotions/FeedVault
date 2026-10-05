// Desktop, 1440x900: every page loads clean, the Feed shows posts, and
// Review decides from the keyboard.
import { test, expect, PAGES, openPage, reviewLeft, UNDO_COVER_404 } from "./fixtures.js";

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
  const session = page.locator(".review-session");
  const open = page.locator(".review-open");          // the current post's own page: one per post
  const left = await reviewLeft(page);
  expect(left).toBeGreaterThan(2);
  await expect(session).toHaveText("0 kept · 0 trashed");
  const first = await open.getAttribute("href");

  await page.keyboard.press("k");
  await expect(session).toHaveText("1 kept · 0 trashed");
  await expect(page.locator(".review-left")).toHaveText(String(left - 1));
  await expect(open).not.toHaveAttribute("href", first);
  const second = await open.getAttribute("href");

  await page.keyboard.press("d");
  await expect(session).toHaveText("1 kept · 1 trashed");
  await expect(page.locator(".review-left")).toHaveText(String(left - 2));
  await expect(open).not.toHaveAttribute("href", second);

  pageErrors.allow(UNDO_COVER_404);                 // #90
  // Undo brings the trashed post back, as the current one.
  await page.keyboard.press("z");
  await expect(session).toHaveText("1 kept · 0 trashed");
  await expect(page.locator(".review-left")).toHaveText(String(left - 1));
  await expect(open).toHaveAttribute("href", second);
});
