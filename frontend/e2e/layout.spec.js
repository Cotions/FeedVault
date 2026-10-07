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
import { test, expect, PAGES, openPage, stressData, stillPage, checkLayout, formatFindings, settle, idle, fakeBioImport } from "./fixtures.js";

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
  await idle(page);
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
    // The suggestions hang over the panel's buttons, cut off by no scrolling
    // box (Review's info panel scrolls): the last one the list shows without
    // scrolling is what a click there hits.
    name: "Review › Tag suggestions",
    open: async page => {
      await openPage(page, { name: "Review", path: "/review" });
      await page.keyboard.press("t");
      await page.getByRole("combobox", { name: "Tag this post…" }).fill("a");
      const list = page.locator(".tag-suggest");
      await expect(list.locator("li").first()).toBeVisible();
      expect(await list.evaluate(ul => {
        const bottom = ul.getBoundingClientRect().bottom;
        const li = [...ul.children].filter(x => x.getBoundingClientRect().bottom <= bottom).pop();
        const r = li.getBoundingClientRect();
        return li.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2));
      }), "the last suggestion in sight is on top, not cut off").toBe(true);
    },
    scope: ".tag-suggest",
  },
  {
    // Faked (fixtures.js): nothing is fetched. A long handle and a long name in the list.
    name: "Person › Link-in-bio import results",
    open: async page => {
      await fakeBioImport(page);
      await openUrl(page, `/people/${S.person}`, pg => pg.locator("h2.page-title"));
      await page.getByRole("textbox", { name: "Link-in-bio page" }).fill("https://linktr.ee/somebody");
      await page.locator(".bio-import").getByRole("button", { name: "Import" }).click();
      await expect(page.locator(".bio-import-row").first()).toBeVisible();
      await idle(page);
    },
    scope: ".bio-import",
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
      await d.open(page);
      await stillPage(page);
      await expect(page.locator(d.scope).first()).toBeVisible();
      await checkSizes(page, testInfo, d.name, { scope: d.scope });
    });
  }
});

// One page shell (#93): every page opens on the same head, above its cards.
// The title (h2) starts at the same x and y on each page, to 1px, and the
// head ends above whatever follows it and above the first card. Review,
// head included, fits the window.
const SHELL_SIZES = [SIZES[1], SIZES[2]];   // 1280x800, 1440x900
const SHELL_VIEWS = [
  ...VIEWS.filter(v => !v.name.startsWith("Settings ›")),
  { name: "Tag", open: page => openUrl(page, `/?tag=${encodeURIComponent("ceramics")}`, pg => pg.locator("article.post-card").first()) },
];

test("every page's title is in the same place", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "layout-zoom", "the zoomed project checks the resting layout only");
  test.setTimeout(90_000);
  const problems = [];
  const first = {};                                   // per size: the first page's title
  // Each page opens once and is measured at both sizes.
  for (const v of SHELL_VIEWS) {
    await v.open(page);
    await stillPage(page);
    for (const s of SHELL_SIZES) {
      await page.setViewportSize({ width: s.width, height: s.height });
      await settle(page);
      const m = await page.evaluate(() => {
        window.scrollTo(0, 0);
        const head = document.querySelector("main .page-head");
        const h2 = head?.querySelector("h2");
        if (!head || !h2) return null;
        const hb = head.getBoundingClientRect(), tb = h2.getBoundingClientRect();
        const next = head.nextElementSibling?.getBoundingClientRect();
        const card = [...document.querySelectorAll("main .card")].find(c => c.getBoundingClientRect().height > 0)?.getBoundingClientRect();
        return { x: tb.x, y: tb.y, bottom: hb.bottom, next: next?.top ?? null, card: card?.top ?? null,
          tall: document.documentElement.scrollHeight - innerHeight };
      });
      const at = `${v.name} at ${s.label}`;
      if (!m) { problems.push(`${at}: no .page-head with an h2 in <main>`); continue; }
      const f = first[s.label] ??= { ...m, name: v.name };
      if (Math.abs(m.x - f.x) > 1 || Math.abs(m.y - f.y) > 1) {
        problems.push(`${at}: title at (${m.x}, ${m.y}), ${f.name} has it at (${f.x}, ${f.y})`);
      }
      if (m.next != null && m.next < m.bottom - 0.5) problems.push(`${at}: the head (bottom ${m.bottom}) overlaps what follows it (top ${m.next})`);
      // Review fits the window with its head: the media never sets its height.
      if (v.name.startsWith("Review") && m.tall > 1) problems.push(`${at}: Review is ${m.tall}px taller than the window`);
      if (m.card != null && m.card < m.bottom - 0.5) problems.push(`${at}: the head (bottom ${m.bottom}) overlaps the first card (top ${m.card})`);
    }
  }
  expect(problems, problems.join("\n")).toEqual([]);
});
