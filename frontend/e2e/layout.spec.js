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
import { STRESS_PERSON } from "./stress.js";

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
  { name: "Links, Unsorted", open: page => openUrl(page, "/links?person=none", pg => pg.locator(".link-assign").first()) },
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
    // The demo's people (the stress person's long name among them) under
    // "Recent" and "Everyone else"; the clipboard is not read (refused).
    name: "Quick-add a link",
    open: async page => {
      await page.addInitScript(() => Object.defineProperty(navigator, "clipboard",
        { configurable: true, value: { readText: () => Promise.reject(new Error("refused")) } }));
      await openPage(page, { name: "Links", path: "/links" });
      await page.locator("body").press("Alt+l");
      await expect(page.locator(".person-pick-option").nth(1)).toBeVisible();
      await idle(page);
    },
    scope: ".modal-overlay",
  },
  {
    // #165 B: a row's picker, open inside the row, under the demo's Unsorted links.
    name: "Links › Assign an Unsorted link",
    open: async page => {
      await openUrl(page, "/links?person=none", pg => pg.locator(".link-assign").first());
      await page.locator(".link-assign").first().click();
      await expect(page.locator(".link-row.is-assigning .person-pick-option").first()).toBeVisible();
      await idle(page);
    },
    scope: ".link-list",
  },
  {
    name: "Links › Selection bar",
    open: async page => {
      await openUrl(page, "/links?person=none", pg => pg.locator(".link-check").first());
      await page.locator(".link-check input").first().click();
      await page.locator(".link-check input").nth(2).click({ modifiers: ["Shift"] });
      await expect(page.locator(".select-bar .select-count")).toHaveText("3 selected");
    },
    scope: ".select-bar",
  },
  {
    name: "Links › Assign the selected links",
    open: async page => {
      await openUrl(page, "/links?person=none", pg => pg.locator(".link-check").first());
      await page.locator(".link-check input").first().click();
      await page.locator(".select-bar").getByRole("button", { name: /^Assign 1 to/ }).click();
      await expect(page.locator(".assign-modal .person-pick-option").first()).toBeVisible();
      await idle(page);
    },
    scope: ".modal-overlay",
  },
  {
    // A URL dragged in from another window, not dropped yet.
    name: "Drop a URL overlay",
    open: async page => {
      await openPage(page, { name: "Collections", path: "/collections" });
      await page.evaluate(() => {
        const dt = new DataTransfer();
        dt.setData("text/uri-list", "https://example.org/");
        document.querySelector("main").dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt }));
        // Kept up while it is measured: no dragover follows to keep it.
        setInterval(() => document.querySelector("main")
          .dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt })), 100);
      });
    },
    scope: ".drop-overlay",
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
        return { x: tb.x, y: tb.y, bottom: hb.bottom, right: hb.right, next: next?.top ?? null, card: card?.top ?? null,
          tall: document.documentElement.scrollHeight - innerHeight };
      });
      const at = `${v.name} at ${s.label}`;
      if (!m) { problems.push(`${at}: no .page-head with an h2 in <main>`); continue; }
      const f = first[s.label] ??= { ...m, name: v.name };
      if (Math.abs(m.x - f.x) > 1 || Math.abs(m.y - f.y) > 1) {
        problems.push(`${at}: title at (${m.x}, ${m.y}), ${f.name} has it at (${f.x}, ${f.y})`);
      }
      // The head's rule as long on every page: Settings' ended 88px short (#149).
      if (Math.abs(m.right - f.right) > 1) problems.push(`${at}: the head ends at x=${m.right}, ${f.name}'s at x=${f.right}`);
      if (m.next != null && m.next < m.bottom - 0.5) problems.push(`${at}: the head (bottom ${m.bottom}) overlaps what follows it (top ${m.next})`);
      // Review fits the window with its head: the media never sets its height.
      if (v.name.startsWith("Review") && m.tall > 1) problems.push(`${at}: Review is ${m.tall}px taller than the window`);
      if (m.card != null && m.card < m.bottom - 0.5) problems.push(`${at}: the head (bottom ${m.bottom}) overlaps the first card (top ${m.card})`);
    }
  }
  expect(problems, problems.join("\n")).toEqual([]);
});

// The person page and the Links page line up, with the stress person (no
// source, two links with a long title, address, notes and name), at
// 1280x800, 1440x900 and 1920x1080:
//  - Mute is on the line of Feed … Trash, at the right edge of the sections.
//  - Every section after the first is set apart by a rule, below the one
//    before it; the empty Sources says so in a line, not a 48px gap.
//  - The link-in-bio import is a box inside Accounts, as wide as the Add a
//    link box, and both have the same sub-heading style.
//  - Add a link's title field and its button end at the same x (a person's
//    page); on the Links page the button ends where the Person field does.
//  - A long person name on the Links page ends in an ellipsis inside its chip.
const LINE_SIZES = [SIZES[1], SIZES[2], SIZES[3]];

test("the person page and the Links page line up", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "layout-zoom", "measured at three desktop sizes");
  const problems = [];
  const near = (a, b, px = 1) => Math.abs(a - b) <= px;
  await openUrl(page, `/people/${S.person}`, pg => pg.locator(".person-new button"));
  await stillPage(page);
  for (const s of LINE_SIZES) {
    await page.setViewportSize({ width: s.width, height: s.height });
    await settle(page);
    const at = `person page at ${s.label}`;
    const m = await page.evaluate(() => {
      const box = e => e.getBoundingClientRect();
      const nav = box(document.querySelector(".person-links a"));
      const mute = box(document.querySelector(".person-new button"));
      const sections = [...document.querySelectorAll(".person-section")];
      const accounts = sections[0];
      const style = e => getComputedStyle(e);
      const bio = document.querySelector(".bio-import"), add = document.querySelector(".person-link-add");
      const sourcesEmpty = sections.find(x => x.querySelector(".card-title")?.textContent.startsWith("Sources"))?.querySelector(".empty");
      const title = box(add.querySelector(".link-form-title input")), button = box(add.querySelector(".link-form-actions .btn-primary"));
      return {
        nav: nav.top + nav.height / 2, mute: mute.top + mute.height / 2, muteRight: mute.right, sectionRight: box(accounts).right,
        rules: sections.slice(1).map((x, i) => ({ border: parseFloat(style(x).borderTopWidth), top: box(x).top, above: box(sections[i]).bottom })),
        bioInAccounts: accounts.contains(bio), bio: box(bio), add: box(add),
        dashed: [style(bio).borderTopStyle, style(add).borderTopStyle],
        heads: [...document.querySelectorAll(".bio-import-title, .link-group-title")].map(h => `${style(h).fontSize} ${style(h).fontWeight} ${style(h).color}`),
        empty: sourcesEmpty ? box(sourcesEmpty).height : null,
        titleRight: title.right, buttonRight: button.right,
      };
    });
    if (!near(m.nav, m.mute, 2)) problems.push(`${at}: Mute is centred at y=${m.mute}, the Feed … Trash line at y=${m.nav}`);
    if (!near(m.muteRight, m.sectionRight)) problems.push(`${at}: Mute ends at x=${m.muteRight}, the sections at x=${m.sectionRight}`);
    m.rules.forEach((r, i) => {
      if (r.border < 1) problems.push(`${at}: section ${i + 2} has no rule above it`);
      if (r.top < r.above - 0.5) problems.push(`${at}: section ${i + 2} (top ${r.top}) overlaps the one before it (bottom ${r.above})`);
    });
    if (!m.bioInAccounts) problems.push(`${at}: the link-in-bio import is not inside Accounts`);
    if (!near(m.bio.left, m.add.left) || !near(m.bio.right, m.add.right)) {
      problems.push(`${at}: the import box spans x=${m.bio.left}…${m.bio.right}, Add a link x=${m.add.left}…${m.add.right}`);
    }
    if (m.dashed.some(d => d !== "dashed")) problems.push(`${at}: the import and Add a link boxes are ${m.dashed.join(" and ")}, not dashed`);
    if (new Set(m.heads).size !== 1) problems.push(`${at}: the sub-headings differ: ${[...new Set(m.heads)].join(" | ")}`);
    if (m.empty == null) problems.push(`${at}: the stress person's Sources has no empty state`);
    else if (m.empty > 60) problems.push(`${at}: the empty Sources is ${m.empty}px tall`);
    if (!near(m.titleRight, m.buttonRight)) problems.push(`${at}: Add a link's title ends at x=${m.titleRight}, its button at x=${m.buttonRight}`);
  }

  await openPage(page, { name: "Links", path: "/links" });
  await stillPage(page);
  for (const s of LINE_SIZES) {
    await page.setViewportSize({ width: s.width, height: s.height });
    await settle(page);
    const at = `Links at ${s.label}`;
    const m = await page.evaluate(person => {
      const form = document.querySelector(".links-add .link-form");
      const chip = [...document.querySelectorAll(".link-person")].find(c => c.title === `Person: ${person}`);
      const text = chip?.querySelector(".chip-text");
      return {
        personRight: form.querySelector(".link-form-person .picker-input").getBoundingClientRect().right,
        buttonRight: form.querySelector(".link-form-actions .btn-primary").getBoundingClientRect().right,
        chip: chip ? chip.getBoundingClientRect().right : null,
        text: text ? { right: text.getBoundingClientRect().right, cut: text.scrollWidth > text.clientWidth, overflow: getComputedStyle(text).textOverflow } : null,
      };
    }, STRESS_PERSON);
    if (!near(m.personRight, m.buttonRight)) problems.push(`${at}: Add link ends at x=${m.buttonRight}, the Person field at x=${m.personRight}`);
    if (!m.text) problems.push(`${at}: no person chip with a .chip-text for ${STRESS_PERSON}`);
    else {
      if (!m.text.cut || m.text.overflow !== "ellipsis") problems.push(`${at}: the long person name is not cut with an ellipsis (${JSON.stringify(m.text)})`);
      if (m.text.right > m.chip + 0.5) problems.push(`${at}: the name runs past its chip (${m.text.right} > ${m.chip})`);
    }
  }
  expect(problems, problems.join("\n")).toEqual([]);
});

// The sidebar fits short desktop windows: at 1280x800 and 1366x768 every
// entry shows without scrolling it, the last (Quit) whole and clear of the
// sidebar's bottom edge by its focus ring, and each entry stays a 24px target.
const SIDEBAR_SIZES = [SIZES[1], { label: "1366x768", width: 1366, height: 768 }];

test("the sidebar fits without scrolling", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "layout-zoom", "measured at the two short desktop sizes only");
  await openPage(page, PAGES[0]);
  const problems = [];
  for (const s of SIDEBAR_SIZES) {
    await page.setViewportSize({ width: s.width, height: s.height });
    await settle(page);
    const m = await page.evaluate(() => {
      const nav = document.querySelector("#main-nav");
      const box = nav.getBoundingClientRect();
      const items = [...nav.children].filter(e => e.getBoundingClientRect().height > 0);
      const last = items.at(-1).getBoundingClientRect();
      const small = [...nav.querySelectorAll(".side-link")].filter(e => e.getBoundingClientRect().height < 24)
        .map(e => e.textContent.trim());
      return { scroll: nav.scrollHeight, client: nav.clientHeight, lastBottom: last.bottom, navBottom: box.bottom, small,
               lastText: items.at(-1).textContent.trim() };
    });
    const at = `sidebar at ${s.label}`;
    if (m.scroll > m.client) problems.push(`${at}: content ${m.scroll}px in ${m.client}px (overflows by ${m.scroll - m.client})`);
    // 3px: room for the focus ring (box-shadow var(--ring)) below the last entry.
    if (m.lastBottom > m.navBottom - 3) problems.push(`${at}: "${m.lastText}" ends at ${m.lastBottom}, the sidebar at ${m.navBottom}`);
    if (m.small.length) problems.push(`${at}: entries under 24px: ${m.small.join(", ")}`);
  }
  expect(problems, problems.join("\n")).toEqual([]);
});
