// Desktop: a person's rating (#219). Half a star from the person page's
// stars (mouse and keyboard), the Creators card's border and stars after
// it, sort by rating and the rating filter; then a band's color changed in
// Settings → Appearance → Rating colors, and the card's border follows.
// Each test puts every rating and the colors back as they were.
import { test, expect, idle, checkLayout, dismissToasts, formatFindings, stressData, settle } from "./fixtures.js";

const H = { "X-FeedVault": "1" };
const S = stressData();
const SIZES = [
  { label: "1024x768", width: 1024, height: 768 },
  { label: "1280x800", width: 1280, height: 800 },
  { label: "1440x900", width: 1440, height: 900 },
  { label: "1920x1080", width: 1920, height: 1080 },
];

async function checkSizes(page, testInfo, view) {
  await dismissToasts(page);
  const found = [];
  for (const s of SIZES) {
    await page.setViewportSize({ width: s.width, height: s.height });
    await settle(page);
    found.push(...await checkLayout(page, testInfo, { view, size: s.label }));
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await settle(page);
  await page.screenshot({ path: testInfo.outputPath(`${view.replace(/[^\w-]+/g, "_")}.png`), fullPage: false });
  expect(found, `layout findings:\n${formatFindings(found)}`).toEqual([]);
}

const rate = (request, id, rating) => request.post(`/api/people/${id}`, { headers: H, data: { rating } });
const person = async (request, id) => (await request.get(`/api/people/${id}`, { headers: H })).json();
// rgb(r, g, b) of #rrggbb, as getComputedStyle gives a color.
const rgb = hex => `rgb(${[1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)).join(", ")})`;

// The Creators page narrowed to one person by its filter box, and their card.
async function cardOf(page, name) {
  await page.getByRole("textbox", { name: "Filter creators" }).fill(name);
  const card = page.locator(".person-card").filter({ hasText: name });
  await expect(card).toHaveCount(1);
  return card;
}

test.afterEach(async ({ request }) => {
  for (const p of await (await request.get("/api/people", { headers: H })).json()) {
    if (p.rating != null) await rate(request, p.id, null);
  }
  await request.post("/api/config", { headers: H, data: { rating_colors: null } });
});

test("rate from the person page: half stars, keys, clear; the Creators card, sort and filter", async ({ page, request }, testInfo) => {
  const me = await person(request, S.person);
  expect(me.rating).toBeNull();
  await page.goto(`/people/${S.person}`);
  const stars = page.getByRole("slider", { name: "Not rated" });
  await expect(stars).toBeVisible();
  await idle(page);

  // The left half of the fourth star: three and a half.
  const box = await stars.boundingBox();
  const step = (box.width - 4) / 5;                          // 2px of padding each side
  await page.mouse.click(box.x + 2 + step * 3 + step * 0.25, box.y + box.height / 2);
  await expect(page.getByRole("slider", { name: "Rating: 3.5 of 5" })).toBeVisible();
  expect((await person(request, S.person)).rating).toBe(7);
  await idle(page);

  // Keys: → half a star up, Home to half a star, ← there stays (only
  // Delete clears), Delete clears.
  const slider = page.locator(".rating-input");
  await slider.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("slider", { name: "Rating: 4 of 5" })).toBeVisible();
  await idle(page);
  await page.keyboard.press("Home");
  await expect(page.getByRole("slider", { name: "Rating: 0.5 of 5" })).toBeVisible();
  await idle(page);
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowDown");
  await idle(page);
  await expect(page.getByRole("slider", { name: "Rating: 0.5 of 5" })).toBeVisible();
  expect((await person(request, S.person)).rating).toBe(1);
  await page.keyboard.press("Delete");
  await expect(page.getByRole("slider", { name: "Not rated" })).toBeVisible();
  expect((await person(request, S.person)).rating).toBeNull();
  await idle(page);
  // A click on the rating it has clears it.
  await page.mouse.click(box.x + 2 + step * 3 + step * 0.25, box.y + box.height / 2);
  await expect(page.getByRole("slider", { name: "Rating: 3.5 of 5" })).toBeVisible();
  await idle(page);
  await page.mouse.click(box.x + 2 + step * 3 + step * 0.25, box.y + box.height / 2);
  await expect(page.getByRole("slider", { name: "Not rated" })).toBeVisible();
  await idle(page);
  await page.mouse.click(box.x + 2 + step * 3 + step * 0.25, box.y + box.height / 2);
  await expect(page.getByRole("slider", { name: "Rating: 3.5 of 5" })).toBeVisible();
  await idle(page);
  await checkSizes(page, testInfo, "Person page, rated");

  // Creators: the card's border is the band of 3½ stars (the defaults'
  // yellow, from 3), and its stars say so.
  await page.goto("/creators");
  const card = await cardOf(page, me.name);
  await expect(card).toHaveClass(/\bis-rated\b/);
  await expect(card).not.toHaveClass(/\bis-top-band\b/);
  await expect(card).toHaveAttribute("data-rating", "7");
  await expect(card.getByRole("img", { name: "Rating: 3.5 of 5" })).toBeVisible();
  await expect(card).toHaveCSS("border-top-color", rgb("#e6c84a"));
  await idle(page);
  await checkSizes(page, testInfo, "Creators, a rated card");

  // Sort by rating: another person at 5 stars comes first, then this one,
  // the unrated after them (two people made for it, deleted after).
  const made = [];
  for (const name of ["E2E rated five", "E2E not rated"]) {
    made.push((await (await request.post("/api/people", { headers: H, data: { name } })).json()).person);
  }
  const other = made[0];
  try {
    await rate(request, other.id, 10);
    await page.getByRole("textbox", { name: "Filter creators" }).fill("");
    await page.reload();
    await page.getByRole("combobox", { name: "Sort people" }).selectOption("rating");
    const names = page.locator(".person-card .creator-name");
    await expect(names.nth(0)).toContainText(other.name);
    await expect(names.nth(1)).toContainText(me.name);
    await expect(page.locator(".person-card").nth(0)).toHaveClass(/\bis-top-band\b/);
    await expect(page.locator(".person-card").nth(2)).not.toHaveClass(/\bis-rated\b/);
    // Kept through a reload (this browser's choice), as the view is.
    await page.reload();
    await expect(page.getByRole("combobox", { name: "Sort people" })).toHaveValue("rating");
    await expect(names.nth(0)).toContainText(other.name);

    // The filter: rated 4 stars or more is the other one only; not rated, neither.
    await page.getByRole("combobox", { name: "Rating" }).selectOption("is:8");
    await expect(page.locator(".person-card")).toHaveCount(1);
    await expect(names.nth(0)).toContainText(other.name);
    await expect(page).toHaveURL(/[?&]rating=8\b/);
    await page.getByRole("combobox", { name: "Rating" }).selectOption("none");
    await expect(page).toHaveURL(/[?&]rating=(&|$)/);
    await expect(page.locator(".person-card").first()).toBeVisible();
    await expect(page.locator(".person-card.is-rated")).toHaveCount(0);
    await expect(page.locator(".person-card .stars")).toHaveCount(0);
    await page.getByRole("button", { name: "Clear filters" }).click();
    await page.getByRole("combobox", { name: "Sort people" }).selectOption("name");
    await idle(page);
  } finally {
    for (const p of made) await request.delete(`/api/people/${p.id}`, { headers: H });
  }
});

test("a band's color changed in Settings: the Creators card's border follows", async ({ page, request }, testInfo) => {
  const me = await person(request, S.person);
  await rate(request, S.person, 9);                                    // 4½ stars: the green band, from 4
  await page.goto("/creators");
  let card = await cardOf(page, me.name);
  await expect(card).toHaveCSS("border-top-color", rgb("#5cb85c"));

  await page.goto("/settings#appearance");
  const settings = page.locator("#rating-colors");
  await expect(settings.getByRole("list", { name: "Rating bands" }).getByRole("listitem")).toHaveCount(5);
  await idle(page);
  await checkSizes(page, testInfo, "Settings, Rating colors");
  await settings.getByLabel("Color from 4 stars").fill("#123456");
  await settings.getByRole("button", { name: "Save" }).click();
  await expect(settings.getByRole("alert")).toContainText("Saved");
  const saved = (await (await request.get("/api/config", { headers: H })).json()).rating_colors;
  expect(saved.bands[3]).toEqual({ min: 8, color: "#123456" });

  await page.goto("/creators");
  card = await cardOf(page, me.name);
  await expect(card).toHaveCSS("border-top-color", rgb("#123456"));

  // Switched off: the plain border, the stars stay.
  await page.goto("/settings#appearance");
  await settings.getByRole("checkbox", { name: "Color the border of a rated person’s card" }).uncheck();
  await settings.getByRole("button", { name: "Save" }).click();
  await expect(settings.getByRole("alert")).toContainText("Saved");
  await page.goto("/creators");
  card = await cardOf(page, me.name);
  await expect(card).not.toHaveClass(/\bis-rated\b/);
  await expect(card.getByRole("img", { name: "Rating: 4.5 of 5" })).toBeVisible();

  // Reset to defaults, saved: the defaults again.
  await page.goto("/settings#appearance");
  await settings.getByRole("button", { name: "Reset to defaults" }).click();
  await settings.getByRole("button", { name: "Save" }).click();
  await expect(settings.getByRole("alert")).toContainText("Saved");
  await page.goto("/creators");
  card = await cardOf(page, me.name);
  await expect(card).toHaveCSS("border-top-color", rgb("#5cb85c"));
  await idle(page);
});
