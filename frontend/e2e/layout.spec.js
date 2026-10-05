// Desktop layout checks: every page in the nav, a post, a creator, a person
// and a collection page, each Settings tab and the dialogs reachable without
// a real tool, at 1024x768, 1280x800, 1440x900 and 1920x1080 (project
// "layout"), and at 1440x900 zoomed to 125% (project "layout-zoom": a
// 1152x720 CSS viewport at a device pixel ratio of 1.25, which is what the
// browser's own zoom gives a page). The rules (fixtures.js's
// findLayoutProblems): no overlap, no clipped text without a title, no
// control off screen, no sideways page scroll, no button or link under
// 24x24 px. The demo has the stress cases of make_demo.py --stress and
// stress.js: a 60-character creator, 15 tags, long names and paths.
import fs from "node:fs";
import { test, expect, PAGES, openPage, stressData, stillPage, checkLayout, formatFindings, settle } from "./fixtures.js";

const SIZES = [
  { label: "1024x768", width: 1024, height: 768 },
  { label: "1280x800", width: 1280, height: 800 },
  { label: "1440x900", width: 1440, height: 900 },
  { label: "1920x1080", width: 1920, height: 1080 },
];
const ZOOMED = [{ label: "1440x900@125%", width: 1152, height: 720 }];
const sizesFor = testInfo => (testInfo.project.name === "layout-zoom" ? ZOOMED : SIZES);

// FEEDVAULT_E2E_SHOTS=<dir>: a screenshot of each view at 1440x900, no
// outline, for before / after comparisons (a page in full, a dialog as seen).
const SHOTS = process.env.FEEDVAULT_E2E_SHOTS;

// Checks the view at every size of the project; fails once, with every finding.
async function checkSizes(page, testInfo, view, { scope = null } = {}) {
  const found = [];
  for (const s of sizesFor(testInfo)) {
    await page.setViewportSize({ width: s.width, height: s.height });
    await settle(page);
    if (SHOTS && s.label === "1440x900") {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `${SHOTS}/${view.replace(/[^\w-]+/g, "_")}.png`, fullPage: !scope });
    }
    found.push(...await checkLayout(page, testInfo, { view, size: s.label, scope }));
  }
  if (found.length) {
    const file = testInfo.outputPath("findings.json");
    fs.writeFileSync(file, JSON.stringify(found, null, 1));
    await testInfo.attach("findings.json", { path: file, contentType: "application/json" });
  }
  expect(found.length, `layout findings:\n${formatFindings(found)}`).toBe(0);
}

// A page that is not in the nav: loaded once ``ready`` shows and no request is left.
async function openUrl(page, url, ready) {
  await page.goto(url);
  await expect(ready(page)).toBeVisible();
  await page.waitForLoadState("networkidle");
}

const S = stressData();

const VIEWS = [
  ...PAGES.map(p => ({ name: p.name, open: page => openPage(page, p) })),
  { name: "Review, stress post", open: page => openUrl(page, `/review?platform=${S.post.platform}&author=${encodeURIComponent(S.author.id)}`,
    pg => pg.locator(".review-open")) },
  { name: "Post", open: page => openUrl(page, `/p/${S.post.platform}/${S.post.post_id}`, pg => pg.locator(".post-page-head")) },
  { name: "Creator", open: page => openUrl(page, `/?platform=${S.post.platform}&author=${encodeURIComponent(S.author.id)}`,
    pg => pg.locator("article.post-card").first()) },
  { name: "Person", open: page => openUrl(page, `/people/${S.person}`, pg => pg.locator("h2.page-title")) },
  { name: "Collection", open: page => openUrl(page, `/collections/${S.collection}`, pg => pg.locator("h2.page-title")) },
  ...["downloads", "sync", "appearance", "about"].map(tab => ({
    name: `Settings › ${tab}`,
    open: page => openUrl(page, `/settings#${tab}`, pg => pg.locator(`.settings-tab[aria-current="page"][href$="#${tab}"]`)),
  })),
];

test.describe("pages", () => {
  for (const v of VIEWS) {
    test(v.name, async ({ page }, testInfo) => {
      await v.open(page);
      await stillPage(page);
      await checkSizes(page, testInfo, v.name);
    });
  }
});

const DIALOGS = [
  {
    name: "Post › Delete post (confirm)",
    open: async page => {
      await openUrl(page, `/p/${S.post.platform}/${S.post.post_id}`, pg => pg.locator(".post-page-head"));
      await page.getByRole("button", { name: "Delete post" }).click();
    },
    scope: ".modal-overlay",
  },
  {
    name: "Post › Add to collection",
    open: async page => {
      await openUrl(page, `/p/${S.post.platform}/${S.post.post_id}`, pg => pg.locator(".post-page-head"));
      await page.getByRole("button", { name: /^Add to collection/ }).click();
      await expect(page.locator(".collection-pick-list")).toBeVisible();
    },
    scope: ".modal-overlay",
  },
  {
    name: "Feed › Tag selected posts",
    open: async page => {
      await openUrl(page, `/?platform=${S.post.platform}&author=${encodeURIComponent(S.author.id)}`, pg => pg.locator("article.post-card").first());
      await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().click();
      await page.locator("article.post-card").first().click();
      await page.getByRole("button", { name: /^Tag…/ }).click();
    },
    scope: ".modal-overlay",
  },
  {
    name: "Review › Keyboard shortcuts",
    open: async page => {
      await openPage(page, { name: "Review", path: "/review" });
      await page.keyboard.press("?");
    },
    scope: ".review-help",
  },
  {
    name: "Notifications panel",
    open: async page => {
      await openPage(page, PAGES[0]);
      await page.locator(".side-bell > button").click();
    },
    scope: ".notif-panel",
  },
  {
    name: "Feed › Creator picker open",
    open: async page => {
      await openPage(page, PAGES[0]);
      await page.locator(".feed-filters .picker-input").first().click();
      await expect(page.locator(".picker-list").first()).toBeVisible();
    },
    scope: ".picker",
  },
];

test.describe("dialogs", () => {
  for (const d of DIALOGS) {
    test(d.name, async ({ page }, testInfo) => {
      await stillPage(page);
      await d.open(page);
      await stillPage(page);
      await expect(page.locator(d.scope).first()).toBeVisible();
      await checkSizes(page, testInfo, d.name, { scope: d.scope });
    });
  }
});
