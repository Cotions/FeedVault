// Desktop, 1440x900: an account's platform is its logo (PlatformIcon.jsx),
// not the "IG" / "X" / "TT" text: on a person's page, in the Sources list,
// on a post card and in Review's byline. The logo is an image named after the platform, for
// screen readers and the tooltip, and sits before the name, on its line.
// A platform with no logo keeps its text chip.
import { test, expect, openPage, stressData, idle, fakeBioImport, BIO_ACCOUNTS } from "./fixtures.js";
import { platformLabel } from "../src/lib/fmt.js";

const logo = (scope, platform) => scope.getByRole("img", { name: platformLabel(platform), exact: true });

// The logo is drawn (a square box), left of the name and level with it:
// their middles within 2px. ``level``: what it is centred on when the name
// is the first of several lines (a row centres it on them all).
async function expectBeforeName(icon, name, level = name) {
  await expect(icon).toBeVisible();
  const [i, n, l] = [await icon.boundingBox(), await name.boundingBox(), await level.boundingBox()];
  expect(i.width).toBeGreaterThanOrEqual(12);
  expect(i.height).toBe(i.width);
  expect(i.x + i.width).toBeLessThanOrEqual(n.x);
  expect(Math.abs((i.y + i.height / 2) - (l.y + l.height / 2))).toBeLessThanOrEqual(2);
}

test("Person: each account row has its platform's logo, named", async ({ page }) => {
  await fakeBioImport(page);
  // One more account than the fixture's, on a platform with no logo.
  const accounts = [...BIO_ACCOUNTS, { platform: "patreon", handle: "fake.patron", url: "https://patreon.com/fake.patron",
    tool: "gallery-dl", status: "new", profile_url: "https://www.patreon.com/fake.patron", account: null, person: null, source: null }];
  await page.route("**/api/people/*/bio-import", route =>
    route.fulfill({ json: { ok: true, url: "https://linktr.ee/somebody", accounts, other: 0 } }));
  await page.goto(`/people/${stressData().person}`);
  await expect(page.locator("h2.page-title")).toBeVisible();
  await idle(page);

  const own = page.locator(".person-accounts:not(.bio-import-list) > .person-account").first();
  await expectBeforeName(logo(own, stressData().post.platform), own.locator(".creator-name"), own);
  await expect(own.locator("svg.platform-icon > title")).toHaveText(platformLabel(stressData().post.platform));
  await expect(own.locator(".platform-chip")).toHaveCount(0);

  const box = page.locator(".bio-import");
  await box.getByRole("textbox", { name: "Link-in-bio page" }).fill("linktr.ee/somebody");
  await box.getByRole("button", { name: "Import" }).click();
  await expect(box.locator(".bio-import-row")).toHaveCount(accounts.length);
  for (const [k, a] of accounts.entries()) {
    const row = box.locator(".bio-import-row").nth(k);
    if (a.platform === "patreon") {
      await expect(row.getByRole("img")).toHaveCount(0);
      await expect(row.locator(".platform-chip")).toHaveText("PA");
      await expect(row.locator(".platform-chip")).toHaveAttribute("title", "Patreon");
    } else {
      await expectBeforeName(logo(row, a.platform), row.locator(".creator-name"), row);
    }
  }
});

test("Sources and People: a logo before each name, TikTok's in the text colour", async ({ page }) => {
  await openPage(page, { name: "Creators", path: "/creators" });
  const rows = page.locator(".sources-panel .source-row");
  await expect(rows.first()).toBeVisible();
  const { sources } = await (await page.request.get("/api/sources", { headers: { "X-FeedVault": "1" } })).json();
  const seen = new Set();
  for (const s of sources) {
    const row = page.locator(`.sources-panel .source-row[data-source-id="${s.id}"]`);
    if (!await row.count()) continue;                  // a person's source: on their card
    await expectBeforeName(logo(row, s.platform), row.locator(".creator-name"), row);
    await expect(row.locator(".platform-chip")).toHaveCount(0);
    seen.add(s.platform);
  }
  expect([...seen]).toContain("tiktok");

  // A black mark: the text's colour, never black on the dark surface.
  const text = await page.evaluate(() => getComputedStyle(document.body).color);
  const tiktok = logo(rows, "tiktok").first();
  expect(await tiktok.evaluate(el => getComputedStyle(el).color)).toBe(text);
  expect(await tiktok.evaluate(el => getComputedStyle(el.querySelector("path:last-of-type")).fill)).toBe(text);

  // A person's card: the logo and @name in one chip.
  const chip = page.locator(".person-card .platform-chip").first();
  await expect(chip).toHaveText(/^@/);
  await expectBeforeName(chip.getByRole("img"), chip.locator(".chip-text"));
});

test("Feed: a card's author follows the platform's logo", async ({ page }) => {
  await openPage(page, { name: "Feed", path: "/" });
  const card = page.locator("article.post-card").first();
  await expect(card).toBeVisible();
  await expectBeforeName(card.locator(".post-meta").getByRole("img").first(), card.locator(".post-author"));
});

test("Review: the byline's handle follows the platform's logo, the chips after it", async ({ page }) => {
  await openPage(page, { name: "Review", path: "/review" });
  const byline = page.locator(".review-byline");
  const handle = byline.locator(".post-byline-handle");
  await expectBeforeName(byline.getByRole("img"), handle);
  const [h, chip] = [await handle.boundingBox(), await byline.locator(".chip").first().boundingBox()];
  expect(h.x + h.width).toBeLessThanOrEqual(chip.x);});
