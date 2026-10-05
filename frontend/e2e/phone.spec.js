// Phone, 375x812 with touch: no page scrolls sideways, the menu drawer,
// Review's bar, the Feed's folded filters, and Storage's cards.
import { test, expect, PAGES, openPage, reviewLeft, UNDO_COVER_404 } from "./fixtures.js";

test.describe("no page scrolls sideways", () => {
  for (const p of PAGES) {
    test(p.name, async ({ page }) => {
      await openPage(page, p);
      const { scrollWidth, innerWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
      expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
    });
  }
});

test("the menu drawer opens, takes focus, closes on Escape and on a link", async ({ page }) => {
  await openPage(page, PAGES[0]);
  const menu = page.locator(".nav-menu-btn");
  const nav = page.locator("#main-nav");
  const focusInNav = () => page.evaluate(() => document.getElementById("main-nav").contains(document.activeElement));

  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await menu.tap();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(nav).toHaveClass(/\bis-open\b/);
  await expect(nav.getByRole("link", { name: "Feed", exact: true })).toBeInViewport();
  await expect.poll(focusInNav).toBe(true);

  await page.keyboard.press("Escape");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(nav).not.toHaveClass(/\bis-open\b/);
  await expect(menu).toBeFocused();

  await menu.tap();
  await expect(nav).toHaveClass(/\bis-open\b/);
  await nav.getByRole("link", { name: "Storage", exact: true }).tap();
  await expect(page).toHaveURL(/\/storage$/);
  await expect(nav).not.toHaveClass(/\bis-open\b/);
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("heading", { level: 2, name: "Storage", exact: true })).toBeVisible();
});

test("Review: Keep is on screen without a scroll; keep, trash and undo by tap", async ({ page, pageErrors }) => {
  await openPage(page, { name: "Review", path: "/review" });
  const bar = page.locator(".review-actions");
  const keep = bar.getByRole("button", { name: /^Keep/ });
  const trash = bar.getByRole("button", { name: /^Trash post/ });
  const undo = bar.getByRole("button", { name: /^Undo/ });
  const session = page.locator(".review-session");
  const open = page.locator(".review-open");

  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await expect(keep).toBeInViewport({ ratio: 1 });

  const left = await reviewLeft(page);
  expect(left).toBeGreaterThan(2);
  const first = await open.getAttribute("href");
  await keep.tap();
  await expect(session).toHaveText("1 kept · 0 trashed");
  await expect(page.locator(".review-left")).toHaveText(String(left - 1));
  await expect(open).not.toHaveAttribute("href", first);
  const second = await open.getAttribute("href");

  await trash.tap();
  await expect(session).toHaveText("1 kept · 1 trashed");
  await expect(page.locator(".review-left")).toHaveText(String(left - 2));

  pageErrors.allow(UNDO_COVER_404);                 // #90
  await undo.tap();
  await expect(session).toHaveText("1 kept · 0 trashed");
  await expect(page.locator(".review-left")).toHaveText(String(left - 1));
  await expect(open).toHaveAttribute("href", second);
});

test("the Feed's filters fold behind their toggle, and count what is set", async ({ page }) => {
  await openPage(page, PAGES[0]);
  const toggle = page.locator(".feed-filters .filters-toggle");
  const scope = page.locator("#feed-scope");
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(scope).toBeHidden();
  await expect(toggle.locator(".chip")).toHaveCount(0);

  // Folded, the first post starts high on the screen.
  const post = page.locator("article.post-card").first();
  await expect(post).toBeVisible();
  expect((await post.boundingBox()).y).toBeLessThan(300);

  await toggle.tap();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(scope).toBeVisible();

  await scope.locator("label.filter", { hasText: "Kind" }).locator("select").selectOption("video");
  await expect(page).toHaveURL(/[?&]kind=video\b/);
  await expect(toggle.locator(".chip")).toHaveText("1");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(scope).toBeVisible();
});

test("Storage › Per creator: one card per row, none scrolls sideways", async ({ page }) => {
  await openPage(page, { name: "Storage", path: "/storage" });
  const rows = page.locator(".storage-table tbody tr");
  await expect(rows.first()).toBeVisible();
  const cards = await rows.evaluateAll(trs => trs.map(tr => ({
    display: getComputedStyle(tr).display,
    label: getComputedStyle(tr.querySelector("td:not(.cell-main)"), "::before").content,
    scrollWidth: tr.scrollWidth,
    clientWidth: tr.clientWidth,
    cells: [...tr.querySelectorAll("td")].map(td => ({ scrollWidth: td.scrollWidth, clientWidth: td.clientWidth })),
  })));
  expect(cards.length).toBeGreaterThan(1);
  for (const c of cards) {
    expect(c.display).not.toBe("table-row");
    expect(c.label).not.toBe("none");               // its label / value pairs
    expect(c.scrollWidth).toBeLessThanOrEqual(c.clientWidth);
    for (const cell of c.cells) expect(cell.scrollWidth).toBeLessThanOrEqual(cell.clientWidth);
  }
  const wrap = await page.locator(".storage-table").evaluate(t => {
    const w = t.closest(".table-wrap");
    return { scrollWidth: w.scrollWidth, clientWidth: w.clientWidth };
  });
  expect(wrap.scrollWidth).toBeLessThanOrEqual(wrap.clientWidth);
});
