// Desktop, 1440x900: the long list pages on a vault far larger than the
// demo (docs/TESTING.md "Performance"). Each page's API answer is the
// real one with thousands of invented rows added (page.route: nothing is
// written), and the tests count what is in the DOM rather than timing
// anything: a windowed list (lib/windowing.js) renders the rows near the
// viewport, and keeps search, keyboard focus, unsaved edits and the links
// to one row working.
import { test, expect, openPage, idle } from "./fixtures.js";

// The rows a page may render at once: the viewport's and the overscan's,
// far below the thousands listed.
const MAX_ROWS = 120;

const onPath = path => url => url.pathname === path;

// Every row of a list, those not rendered too, is reached by scrolling.
async function scrollToBottom(page) {
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(100);
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
}

// What has focus: never <body> after its row scrolled away.
const focused = page => page.evaluate(() => {
  const a = document.activeElement;
  return a && a !== document.body ? (a.getAttribute("aria-label") || a.textContent).trim() : "<body>";
});

test.describe("Unmatched, 3,000 files", () => {
  const N = 3000;
  const path = i => `loose files/IMG_${String(i).padStart(5, "0")}.png`;
  test.beforeEach(async ({ page }) => {
    await page.route(onPath("/api/unmatched"), route => route.fulfill({
      json: Array.from({ length: N }, (_, i) => ({
        path: path(i), size: 1000 + i, mtime: 1700000000 + i, dismissed: false,
        reason: `duplicate of loose files/ORIG_${String(i).padStart(5, "0")}.png`,
      })),
    }));
  });

  test("renders a window of rows; scrolling, search and Tab reach every one", async ({ page }) => {
    await openPage(page, { name: "Unmatched", path: "/unmatched" });
    const rows = page.locator(".data-table tbody tr:not(.win-gap)");
    await expect(page.locator(".page-head .page-count")).toHaveText(/^3,000 files · /);
    await expect(rows.first()).toContainText(path(0));
    expect(await rows.count()).toBeLessThan(MAX_ROWS);

    // The last row, by scrolling: still a window.
    await scrollToBottom(page);
    await expect(page.locator("td.path", { hasText: path(N - 1) })).toBeInViewport();
    expect(await rows.count()).toBeLessThan(MAX_ROWS);

    // The search reaches a row far from the viewport (the find bar would not).
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByLabel("Search unmatched files").fill("IMG_01234");
    await expect(rows).toHaveCount(1);
    await expect(rows).toContainText(path(1234));
    await expect(page.locator(".page-head .page-count")).toHaveText(/^1 of 3,000 files · /);
    await page.getByLabel("Search unmatched files").fill("");

    // Tab walks the rows' links past the first viewport, each one in view.
    const first = page.locator(".data-table tbody tr", { hasText: path(0) }).getByRole("link");
    await first.focus();
    for (let i = 0; i < 40; i++) await page.keyboard.press("Tab");
    const at40 = page.locator(".data-table tbody tr", { hasText: path(40) }).getByRole("link");
    await expect(at40).toBeFocused();
    await expect(at40).toBeInViewport();

    // Its row scrolled far away, the focused link stays (rendered, not <body>).
    await scrollToBottom(page);
    await expect(page.locator("td.path", { hasText: path(N - 1) })).toBeInViewport();
    await expect(at40).toBeFocused();
    expect(await focused(page)).toBe("compare in Duplicates");
    await idle(page);
  });
});

// QA pass 3: real unmatched files differ in length, a long unbroken path
// here, a long reason there. The table's columns took their widths from
// the rows rendered, so each scroll re-laid them, every row's height moved,
// the window measured again, and a jump to the middle of the list ended in
// React's "Maximum update depth exceeded" (a blank page). Its columns now
// keep one width wherever the list is scrolled.
test.describe("Unmatched, 500 files in runs of different lengths", () => {
  // As a large vault lists them: copies with long paths and long reasons
  // first, then loose files with short ones, then long videos.
  const root = "/tmp/feedvault-vault-of-some-length/vault/media";
  const file = i => i < 160 ? {
    path: `${root}/gallery-dl/twitter/fog_meadow${i}/18000000000000${String(i).padStart(5, "0")}_1.jpg.json`,
    reason: `duplicate of twitter:18000000000000${String(i).padStart(5, "0")} (${root}/gallery-dl/twitter/bridge_glaze${i}/18000000000000${String(i).padStart(5, "0")}.json)`,
  } : i < 410 ? {
    path: `${root}/loose files/IMG_${String(i).padStart(5, "0")}.png`, reason: "no metadata file for this media",
  } : {
    path: `${root}/youtube/willow_meadow${i}/@willow_meadow${i}-20171008-L00000${i}.webm`,
    reason: "YouTube video longer than 3 min: left to ChannelVault",
  };
  test.beforeEach(async ({ page }) => {
    await page.route(onPath("/api/unmatched"), route => route.fulfill({
      json: Array.from({ length: 500 }, (_, i) => ({ ...file(i), size: 100 + i * 997, mtime: 1700000000 + i, dismissed: false })),
    }));
  });

  test("a jump to the middle renders rows, and the columns hold still", async ({ page }) => {
    await openPage(page, { name: "Unmatched", path: "/unmatched" });
    const widths = () => page.locator(".unmatched-table thead th").evaluateAll(ths => ths.map(t => Math.round(t.getBoundingClientRect().width)));
    const atTop = await widths();
    for (const at of [0.5, 1, 0.25, 0.75]) {
      await page.evaluate(f => window.scrollTo(0, (document.documentElement.scrollHeight - innerHeight) * f), at);
      await page.waitForTimeout(300);
      await expect(page.locator("#main-nav")).toBeVisible();     // not React's error page
      const shown = await page.locator(".unmatched-table tbody tr:not(.win-gap)").evaluateAll(rs =>
        rs.filter(r => { const b = r.getBoundingClientRect(); return b.bottom > 60 && b.top < innerHeight; }).length);
      expect(shown, `rows in view at ${at}`).toBeGreaterThan(0);
      expect(await widths(), `column widths at ${at}`).toEqual(atTop);
    }
    await idle(page);
  });
});

test.describe("Storage, 2,000 creators", () => {
  const N = 2000;
  test.beforeEach(async ({ page }) => {
    await page.route(url => url.pathname === "/api/storage", async route => {
      const res = await route.fetch();
      const body = await res.json();
      const like = body.by_author[0];
      const extra = Array.from({ length: N }, (_, i) => ({
        ...like, person: null, id: `big${i}`, handle: `big.creator.${i}`, name: `Big ${i}`, aliases: [],
        posts: 1 + (i % 50), media: 2, bytes: 10_000 + i, kept_bytes: 0, unreviewed_bytes: 10_000 + i,
      }));
      await route.fulfill({ response: res, json: { ...body, by_author: [...body.by_author, ...extra] } });
    });
  });

  test("renders a window of rows; sorting and the search cover them all", async ({ page }) => {
    await openPage(page, { name: "Storage", path: "/storage" });
    const rows = page.locator(".storage-table tbody tr:not(.win-gap)");
    await expect(page.locator(".card-title", { hasText: "Per creator" })).toHaveText(/Per creator · 2,0\d\d$/);
    expect(await rows.count()).toBeLessThan(MAX_ROWS);

    // Sorted by posts, ascending: the first row is one of the 1-post creators,
    // whichever row it was before.
    const posts = page.getByRole("button", { name: "Posts", exact: true });
    await posts.click();
    await posts.click();
    await expect(page.locator(".storage-table thead th[aria-sort=ascending]")).toHaveText(/Posts/);
    await expect(rows.first().locator("td[data-label=Posts]")).toHaveText("1");
    expect(await rows.count()).toBeLessThan(MAX_ROWS);

    await page.getByLabel("Search creators").fill("big.creator.1999");
    await expect(rows).toHaveCount(1);
    await expect(rows).toContainText("@big.creator.1999");
    await page.getByLabel("Search creators").fill("no such creator at all");
    await expect(page.locator(".storage-table .empty")).toHaveText("No creator matches.");
    await idle(page);
  });
});

test.describe("Links, 2,000 links", () => {
  const N = 2000;
  let ids = [];
  test.beforeEach(async ({ page }) => {
    await page.route(url => url.pathname === "/api/links", async route => {
      if (route.request().method() === "POST") {
        // Saved already: the last link, far below the viewport.
        return route.fulfill({ json: { ok: false, error: "that link is saved already", id: ids[N - 1] } });
      }
      const res = await route.fetch();
      const body = await res.json();
      const like = body.links[0];
      const extra = Array.from({ length: N }, (_, i) => ({
        ...like, id: 900000 + i, url: `https://big-${i}.example/`, site: `big-${i}.example`, title: `Big link ${i}`,
        notes: i % 7 ? "" : `Notes of big link ${i}`, person: null,
      }));
      ids = extra.map(l => l.id);
      await route.fulfill({ response: res, json: { ...body, links: [...extra, ...body.links] } });
    });
  });

  test("renders a window; an edit in progress survives scrolling; a 409 brings its link", async ({ page }) => {
    await openPage(page, { name: "Links", path: "/links" });
    const rows = page.locator(".link-list > li:not(.win-gap)");
    await expect(rows.first()).toContainText("Big link 0");
    expect(await rows.count()).toBeLessThan(MAX_ROWS);

    // An edit with unsaved text, scrolled far away and back: still there.
    const row = page.locator(".link-row", { hasText: "Big link 3" }).first();
    await row.getByRole("button", { name: /^Edit/ }).click();
    const title = page.locator(".link-row.is-editing").getByLabel("Title");
    await title.fill("Edited, not saved");
    // Focus out of the list: the edit, not focus, is what keeps the row.
    await page.locator(".links-add .link-form").getByLabel("Title").focus();
    await scrollToBottom(page);
    await expect(page.locator(".link-row", { hasText: "Big link 1999" })).toBeInViewport();
    expect(await rows.count()).toBeLessThan(MAX_ROWS);
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(title).toHaveValue("Edited, not saved");
    await page.locator(".link-row.is-editing").getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator(".link-row.is-editing")).toHaveCount(0);

    // Adding a link saved already lights it: the last one, brought into view.
    const form = page.locator(".links-add .link-form");
    await form.getByLabel("Address").fill("https://big-1999.example/");
    await form.getByRole("button", { name: "Add link" }).click();
    const lit = page.locator(".link-row.is-flash");
    await expect(lit).toContainText("Big link 1999");
    await expect(lit).toBeInViewport();
    expect(await rows.count()).toBeLessThan(MAX_ROWS);
    await idle(page);
  });
});

test.describe("Creators, 1,500 more accounts", () => {
  const N = 1500;
  const SOURCE = 990077;
  const extra = Array.from({ length: N }, (_, i) => ({
    platform: "instagram", id: `many${i}`, handle: `many.acc.${i}`, name: `Many ${i}`, count: 1, person: null,
    aliases: [], handles: [], names: [],
  }));
  test.beforeEach(async ({ page }) => {
    await page.route(onPath("/api/authors"), async route => {
      const res = await route.fetch();
      await route.fulfill({ response: res, json: [...(await res.json()), ...extra] });
    });
    // One source more, of the last account: what ?source= points at.
    await page.route(onPath("/api/sources"), async route => {
      const res = await route.fetch();
      const body = await res.json();
      const like = body.sources.find(s => s.account && !s.person) || body.sources[0];
      const last = extra[N - 1];
      const src = { ...like, id: SOURCE, person: null, account: { platform: last.platform, id: last.id },
                    target: "many.acc.1499", health: {}, last_result: null, schedule: null };
      await route.fulfill({ response: res, json: { ...body, sources: [...body.sources, src] } });
    });
  });

  test("renders a window of cards; the filter and ?source= reach any of them", async ({ page }) => {
    await openPage(page, { name: "Creators", path: "/creators" });
    const cards = page.locator(".creator-card");
    await expect(page.locator(".page-head .page-count")).toContainText("accounts");
    expect(await cards.count()).toBeLessThan(MAX_ROWS);

    const filter = page.getByLabel("Filter creators");
    await filter.fill("many.acc.1234");
    await expect(cards).toHaveCount(1);
    await expect(cards).toContainText("@many.acc.1234");
    await filter.fill("");

    // A failed sync's notification links to its source: the last card,
    // brought into view and lit, though it was never rendered.
    await page.goto(`/creators?source=${SOURCE}`);
    const lit = page.locator(".creator-card.is-flash");
    await expect(lit).toContainText("@many.acc.1499");
    await expect(lit).toBeInViewport();
    expect(await cards.count()).toBeLessThan(MAX_ROWS);

    // Its Review link focused, then scrolled away from (the address drops
    // ?source=, and a new address starts at the top): focus stays on it.
    const review = lit.getByRole("link", { name: "Review @many.acc.1499" });
    await review.focus();
    await expect(page).toHaveURL(/\/creators$/, { timeout: 6_000 });
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await expect(review).not.toBeInViewport();
    await expect(page.getByRole("link", { name: "Review @many.acc.1499" })).toBeFocused();
    expect(await cards.count()).toBeLessThan(MAX_ROWS);
    await idle(page);
  });
});
