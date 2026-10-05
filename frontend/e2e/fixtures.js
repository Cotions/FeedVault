// The test every spec uses: Playwright's, failing any test whose page logs
// a console error, throws, or gets a failed /api answer (4xx, 5xx, or no
// answer; a request aborted by leaving the page aside). A test may let
// one known error through with pageErrors.allow(pattern), each pointing at
// its issue.
import { test as base, expect } from "@playwright/test";

export const test = base.extend({
  pageErrors: [async ({ page }, provide) => {
    const errors = [];
    page.on("console", msg => {
      if (msg.type() !== "error") return;
      const where = msg.location()?.url;
      errors.push(`console: ${msg.text()}${where ? ` (${where})` : ""}`);
    });
    page.on("pageerror", err => errors.push(`page error: ${err.message}`));
    page.on("response", r => {
      if (new URL(r.url()).pathname.startsWith("/api/") && r.status() >= 400) {
        errors.push(`api: ${r.request().method()} ${r.url()} → ${r.status()}`);
      }
    });
    page.on("requestfailed", r => {
      const why = r.failure()?.errorText || "";
      if (new URL(r.url()).pathname.startsWith("/api/") && !why.includes("ERR_ABORTED")) {
        errors.push(`api: ${r.method()} ${r.url()} failed: ${why}`);
      }
    });
    const allowed = [];
    await provide({ allow: re => allowed.push(re) });
    expect(errors.filter(e => !allowed.some(re => re.test(e))), "console errors and failed /api requests").toEqual([]);
  }, { auto: true }],
});

export { expect };

// Every page in the nav.
export const PAGES = [
  { name: "Feed", path: "/" },
  { name: "Review", path: "/review" },
  { name: "Creators", path: "/creators" },
  { name: "Tags", path: "/tags" },
  { name: "Collections", path: "/collections" },
  { name: "Stats", path: "/stats" },
  { name: "Storage", path: "/storage" },
  { name: "Trash", path: "/trash" },
  { name: "Unmatched", path: "/unmatched" },
  { name: "Duplicates", path: "/duplicates" },
  { name: "Jobs", path: "/jobs" },
  { name: "Scripts", path: "/scripts" },
  { name: "Settings", path: "/settings" },
];

// Opens a page and waits until it has loaded: its link in the nav is the
// current one (hidden in the closed drawer on a phone), its title (Review:
// its count) shows, and no request is left.
export async function openPage(page, { name, path }) {
  await page.goto(path);
  await expect(page.locator(`#main-nav a.side-link[href="${path}"]`)).toHaveAttribute("aria-current", "page");
  if (name === "Review") await expect(page.locator(".review-left")).toHaveText(/^\d[\d,]*$/);
  else await expect(page.getByRole("heading", { level: 2, name, exact: true })).toBeVisible();
  await page.waitForLoadState("networkidle");
}

// Review's count of posts left, once known.
export async function reviewLeft(page) {
  const left = page.locator(".review-left");
  await expect(left).toHaveText(/^\d[\d,]*$/);
  return Number((await left.textContent()).replace(/,/g, ""));
}

// Review: keep, trash, undo, by the actions given (keys or taps), checked by
// the session's counts, the count left and which post is current.
export async function keepTrashUndo(page, { keep, trash, undo }) {
  const session = page.locator(".review-session");
  const leftNow = page.locator(".review-left");
  const open = page.locator(".review-open");          // the current post's own page: one per post
  const left = await reviewLeft(page);
  expect(left).toBeGreaterThan(2);
  await expect(session).toHaveText("0 kept · 0 trashed");
  const first = await open.getAttribute("href");

  await keep();
  await expect(session).toHaveText("1 kept · 0 trashed");
  await expect(leftNow).toHaveText(String(left - 1));
  await expect(open).not.toHaveAttribute("href", first);
  const second = await open.getAttribute("href");

  await trash();
  await expect(session).toHaveText("1 kept · 1 trashed");
  await expect(leftNow).toHaveText(String(left - 2));
  await expect(open).not.toHaveAttribute("href", second);

  // Undo brings the trashed post back, as the current one, without asking
  // for its old cover (#90): any console error fails the test.
  await undo();
  await expect(session).toHaveText("1 kept · 0 trashed");
  await expect(leftNow).toHaveText(String(left - 1));
  await expect(open).toHaveAttribute("href", second);
}
