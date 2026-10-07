// A phone with touch (#159): swipes that page media, the Post page's stage
// on the first screen, 44px tap targets, Review's Fullscreen where the
// browser has none, and a phone on its side in the phone layout. Sizes and
// places are measured from boxes, never from pixels.
import { test, expect, openPage, idle, stressData } from "./fixtures.js";

const H = { "X-FeedVault": "1" };

// A one-finger drag from (x, y) by (dx, dy), as the touch screen sends it
// (Chromium's DevTools protocol: touch events, pointer events from them).
async function swipe(page, x, y, dx, dy, steps = 8) {
  const cdp = await page.context().newCDPSession(page);
  const at = (px, py) => [{ x: Math.round(px), y: Math.round(py), id: 1 }];
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: at(x, y) });
  for (let i = 1; i <= steps; i++) {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: at(x + dx * i / steps, y + dy * i / steps) });
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await cdp.detach();
}

// The middle of an element, from its box.
async function middle(loc) {
  const b = await loc.boundingBox();
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

// A post with several items, all of them pictures: no video's controls in the way.
async function picturesPost(request) {
  const r = await request.get("/api/posts?kind=carousel&limit=100", { headers: H });
  for (const p of (await r.json()).posts) {
    const full = await (await request.get(`/api/posts/${p.platform}/${encodeURIComponent(p.post_id)}`, { headers: H })).json();
    const media = full.media || [];
    if (media.length >= 3 && media.every(m => m.kind === "image" && !m.missing)) return { path: `/p/${p.platform}/${encodeURIComponent(p.post_id)}`, n: media.length };
  }
  throw new Error("no carousel of pictures in the demo");
}

async function openPost(page, path) {
  await page.goto(path);
  await expect(page.locator(".post-page-head")).toBeVisible();
  await expect(page.locator(".carousel-stage")).toBeVisible();
  await idle(page);
}

test("Post page: a sideways swipe pages the media, an up or down one does not", async ({ page, request }) => {
  const { path, n } = await picturesPost(request);
  await openPost(page, path);
  const count = page.locator(".carousel-count");
  const stage = page.locator(".carousel-stage");
  await expect(count).toHaveText(`1/${n}`);
  const c = await middle(stage);

  await swipe(page, c.x + 80, c.y, -160, 6);              // to the left: the next one
  await expect(count).toHaveText(`2/${n}`);
  await swipe(page, c.x - 80, c.y, 160, -6);              // to the right: back
  await expect(count).toHaveText(`1/${n}`);
  await swipe(page, c.x - 80, c.y, 160, 0);               // right from the first: the last
  await expect(count).toHaveText(`${n}/${n}`);
  await swipe(page, c.x + 80, c.y, -160, 0);
  await expect(count).toHaveText(`1/${n}`);

  // Too short, or more down than across: no paging.
  await swipe(page, c.x, c.y, -30, 0);
  await swipe(page, c.x, c.y - 100, -60, 200);
  await page.waitForTimeout(300);
  await expect(count).toHaveText(`1/${n}`);
});

test("Post page: the stage ends above the screen's bottom, under the page head", async ({ page }) => {
  const S = stressData();
  await openPost(page, `/p/${S.post.platform}/STRESSpost03`);    // a video
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  const head = await page.locator(".post-page-head").boundingBox();
  const stage = await page.locator(".carousel-stage").boundingBox();
  const height = page.viewportSize().height;
  expect(stage.y).toBeGreaterThanOrEqual(head.y + head.height);
  expect(stage.y + stage.height).toBeLessThanOrEqual(height);
  // Not a sliver: it takes most of what is left.
  expect(stage.height).toBeGreaterThan((height - stage.y) * 0.8);
  // The video fills the stage, its controls along its bottom.
  const video = await page.locator(".carousel-slide.is-active video, .carousel-slide.is-active .media-missing").boundingBox();
  expect(video.y + video.height).toBeLessThanOrEqual(height);
});

// Every control listed in #159, at least 44px each way (height only for
// those with a label: they are wider anyway).
async function sizes(page, sel) {
  return page.locator(sel).evaluateAll(els => els.filter(el => el.getBoundingClientRect().width > 0).map(el => {
    const r = el.getBoundingClientRect();
    return { what: (el.getAttribute("aria-label") || el.textContent).trim().slice(0, 40), w: r.width, h: r.height };
  }));
}
function atLeast44(list, { width = true } = {}) {
  expect(list.length).toBeGreaterThan(0);
  for (const b of list) {
    expect.soft(b.h, `${b.what}: height`).toBeGreaterThanOrEqual(44);
    if (width) expect.soft(b.w, `${b.what}: width`).toBeGreaterThanOrEqual(44);
  }
}

test("tap targets are 44px: header, Feed, select mode", async ({ page }) => {
  await openPage(page, { name: "Feed", path: "/" });
  atLeast44(await sizes(page, "header .nav-menu-btn"));
  atLeast44(await sizes(page, "input.header-search"), { width: false });
  // The header's height did not change: the page under it starts where it did.
  expect((await page.locator("header").boundingBox()).height).toBeCloseTo(53, 0);
  atLeast44(await sizes(page, ".page-head button, .feed-filters .select-toggle"), { width: false });

  await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().tap();
  atLeast44(await sizes(page, ".select-bar button"), { width: false });
  // The check looks as it did (24px); a tap in the 44px square around it
  // lands on it (each side's middle, and the corner the card's rounded
  // corner does not cut).
  const check = page.locator("article.post-card .select-check").first();
  const b = await check.boundingBox();
  expect(b.width).toBe(24);
  const hits = await check.evaluate(el => {
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    return [[cx - 21, cy], [cx + 21, cy], [cx, cy - 21], [cx, cy + 21], [cx + 21, cy + 21]]
      .map(([x, y]) => el.contains(document.elementFromPoint(x, y)));
  });
  expect(hits).toEqual([true, true, true, true, true]);
  await check.tap();
  await expect(check).toHaveAttribute("aria-checked", "true");
});

test("tap targets are 44px: the Post page and Review", async ({ page, request }) => {
  const { path } = await picturesPost(request);
  await openPost(page, path);
  atLeast44(await sizes(page, ".post-page-head button"), { width: false });
  atLeast44(await sizes(page, ".carousel-arrow, .carousel-dot"));

  await openPage(page, { name: "Review", path: "/review" });
  atLeast44(await sizes(page, ".review-actions button"), { width: false });
  atLeast44(await sizes(page, ".review-actions-row button"));
});

// Review: a swipe pages the post's items, and never decides.
test("Review: a sideways swipe pages the items, decides nothing", async ({ page }) => {
  await openPage(page, { name: "Review", path: "/review" });
  const count = page.locator(".review-stage .carousel-count");
  const skip = page.locator(".review-actions").getByRole("button", { name: /^Skip/ });
  for (let i = 0; i < 40 && !(await count.isVisible()); i++) {
    await skip.tap();
    await idle(page);
  }
  await expect(count).toHaveText(/^1\/\d+$/);
  const n = Number((await count.textContent()).split("/")[1]);
  const left = await page.locator(".review-left").textContent();
  const open = await page.locator(".review-open").getAttribute("href");
  const box = await page.locator(".review-stage").boundingBox();
  // Above the band of a video's controls.
  const x = box.x + box.width / 2, y = box.y + box.height * 0.35;

  await swipe(page, x + 60, y, -140, 4);
  await expect(count).toHaveText(`2/${n}`);
  await swipe(page, x - 60, y, 140, 0);
  await expect(count).toHaveText(`1/${n}`);
  await expect(page.locator(".review-session")).toHaveText("0 kept · 0 trashed");
  await expect(page.locator(".review-left")).toHaveText(left);
  await expect(page.locator(".review-open")).toHaveAttribute("href", open);
});

test.describe("an iPhone: no Fullscreen API on the page", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(Document.prototype, "fullscreenEnabled", { get: () => false, configurable: true });
      HTMLVideoElement.prototype.webkitEnterFullscreen = function () { window.__videoFullscreen = (window.__videoFullscreen || 0) + 1; };
    });
  });

  test("Review offers Fullscreen on a video only, and it is the video's own", async ({ page }) => {
    await openPage(page, { name: "Review", path: "/review" });
    const fs = page.locator(".review-actions").getByRole("button", { name: "Fullscreen" });
    const skip = page.locator(".review-actions").getByRole("button", { name: /^Skip/ });
    const video = page.locator(".review-stage > video.review-media");
    // Anything else loaded: a picture, a text post, a missing file.
    const picture = page.locator(".review-stage > :is(img.review-media:not(.is-placeholder), .review-text, .review-empty:not(.dim))");
    const seen = { video: false, picture: false };
    for (let i = 0; i < 40 && !(seen.video && seen.picture); i++) {
      await expect(video.or(picture)).toBeVisible();
      if (await video.isVisible()) {
        await expect(fs).toBeVisible();
        if (!seen.video) {
          await fs.tap();
          expect(await page.evaluate(() => window.__videoFullscreen)).toBe(1);
          expect(await page.evaluate(() => document.fullscreenElement)).toBe(null);
        }
        seen.video = true;
      } else {
        await expect(fs).toHaveCount(0);
        // The row's other buttons fill it: no hole where it was.
        const row = await sizes(page, ".review-actions-row button");
        const bar = await page.locator(".review-actions").boundingBox();
        const right = await page.locator(".review-actions-row button").last().boundingBox();
        expect(row.length).toBe(5);
        expect(bar.x + bar.width - (right.x + right.width)).toBeLessThan(16);
        seen.picture = true;
      }
      await skip.tap();
      await idle(page);
    }
    expect(seen).toEqual({ video: true, picture: true });
  });
});

test.describe("a phone on its side, 812x375", () => {
  test.use({ viewport: { width: 812, height: 375 } });

  test("the phone layout: no sidebar, the menu button", async ({ page }) => {
    await openPage(page, { name: "Feed", path: "/" });
    expect(await page.evaluate(() => matchMedia("(pointer: coarse)").matches)).toBe(true);
    await expect(page.locator(".sidebar")).toBeHidden();
    await expect(page.locator("header .nav-menu-btn")).toBeVisible();
    const m = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
    expect(m.scrollWidth).toBeLessThanOrEqual(m.clientWidth);
  });

  test("Review: the decision bar is fixed on the first screen, one row", async ({ page }) => {
    await openPage(page, { name: "Review", path: "/review" });
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    const bar = page.locator(".review-actions");
    expect(await bar.evaluate(el => getComputedStyle(el).position)).toBe("fixed");
    const buttons = await bar.locator("button").evaluateAll(els => els.map(el => el.getBoundingClientRect())
      .filter(r => r.width > 0).map(r => ({ top: r.top, bottom: r.bottom })));
    expect(buttons.length).toBeGreaterThan(5);
    const height = page.viewportSize().height;
    for (const b of buttons) {
      expect(b.bottom).toBeLessThanOrEqual(height);
      expect(Math.abs(b.top - buttons[0].top)).toBeLessThan(2);    // one row
    }
    await expect(bar.getByRole("button", { name: /^Keep/ })).toBeInViewport({ ratio: 1 });
  });

  test("Post page: the stage ends above the screen's bottom", async ({ page }) => {
    const S = stressData();
    await openPost(page, `/p/${S.post.platform}/STRESSpost03`);
    const stage = await page.locator(".carousel-stage").boundingBox();
    expect(stage.y + stage.height).toBeLessThanOrEqual(page.viewportSize().height);
    expect(stage.height).toBeGreaterThanOrEqual(150);
  });
});
