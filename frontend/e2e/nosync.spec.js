// Desktop: Don't sync (#189) on the stress person (its 60-character name)
// and on an account linked to nobody, at 1024x768, 1280x800, 1440x900 and
// 1920x1080: the confirmation, the person page with its source kept and
// marked Not synced, and the Creators page filtered to Not synced, in both
// views. A source is added to the throwaway instance and removed after,
// and every switch is turned off again; nothing is synced.
import { test, expect, idle, checkLayout, dismissToasts, formatFindings, stressData, settle, menuItem, openMenu } from "./fixtures.js";

const H = { "X-FeedVault": "1" };
const S = stressData();
const SIZES = [
  { label: "1024x768", width: 1024, height: 768 },
  { label: "1280x800", width: 1280, height: 800 },
  { label: "1440x900", width: 1440, height: 900 },
  { label: "1920x1080", width: 1920, height: 1080 },
];

async function checkSizes(page, testInfo, view, scope = null) {
  await dismissToasts(page);
  const found = [];
  for (const s of SIZES) {
    await page.setViewportSize({ width: s.width, height: s.height });
    await settle(page);
    found.push(...await checkLayout(page, testInfo, { view, size: s.label, scope }));
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await settle(page);
  await page.screenshot({ path: testInfo.outputPath(`${view.replace(/[^\w-]+/g, "_")}.png`), fullPage: false });
  expect(found, `layout findings:\n${formatFindings(found)}`).toEqual([]);
}

test("Don't sync: asked first, the source kept and marked, the filter, no overlap", async ({ page, request, pageErrors }, testInfo) => {
  // Turned on for one with a source, the first answer is 409 "confirm": the dialog asks.
  pageErrors.allow(/^api: POST \S+\/api\/no-sync → 409$/);
  pageErrors.allow(/^console: .*status of 409 .*\/api\/no-sync\)$/);
  const person = await (await request.get(`/api/people/${S.person}`, { headers: H })).json();
  const authors = await (await request.get("/api/authors", { headers: H })).json();
  const loose = authors.find(a => !a.person && a.id != null);
  const src = (await (await request.post("/api/sources", {
    headers: H, data: { target: "https://x.com/e2e_nosync_kept", person: S.person } })).json()).source;
  expect(src?.no_sync).toBe(false);
  try {
    // The person page: the switch (in the head's ⋯ menu) asks first, with
    // how many sources stop.
    await page.goto(`/people/${S.person}`);
    await expect(page.locator(`.source-row[data-source-id="${src.id}"]`)).toBeVisible();
    await expect(page.locator(".person-new .nosync-chip"), "no marker while it syncs").toHaveCount(0);
    await idle(page);
    const head = `More for ${person.name}`;
    await (await menuItem(page, page, head, "Don't sync")).click();
    const ask = page.getByRole("alertdialog", { name: `Stop syncing ${person.name}?` });
    await expect(ask).toBeVisible();
    await expect(ask).toContainText("1 source");
    await expect(ask).toContainText("Nothing is deleted");
    await idle(page);
    await checkSizes(page, testInfo, "Don't sync, asked", ".modal-overlay");
    await ask.getByRole("button", { name: "Don't sync" }).click();
    await expect(ask).toHaveCount(0);
    // The dialog gives focus back to the ⋯ button it came from.
    await expect(page.getByRole("button", { name: head, exact: true })).toBeFocused();

    // Kept, marked, no Sync, no Options, no Add source.
    const row = page.locator(`.source-row[data-source-id="${src.id}"]`);
    await expect(row.locator(".nosync-chip")).toBeVisible();
    await expect(row.getByRole("button", { name: /^Sync / })).toHaveCount(0);
    const rowMenu = await openMenu(page, row, /^More for /);
    await expect(rowMenu.getByRole("menuitem", { name: "Options" })).toHaveCount(0);
    await expect(rowMenu.getByRole("menuitem", { name: "Remove" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(rowMenu).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "Profile link, or an Instagram name" })).toHaveCount(0);
    // The page says so beside the Feed … Trash line, and its ⋯ menu turns it off.
    await expect(page.locator(".person-new .nosync-chip")).toBeVisible();
    await expect(await menuItem(page, page, head, "Sync again")).toBeVisible();
    await page.keyboard.press("Escape");
    // An account of theirs: turned off on the person only, its item's title says who.
    const account = page.locator(".person-account").first();
    const own = await menuItem(page, account, /^More for @/, "Sync again");
    await expect(own).toHaveAttribute("aria-disabled", "true");
    expect(await own.getAttribute("title")).toContain(`${person.name} is set to Don't sync`);
    await own.click({ force: true });           // aria-disabled: Playwright would wait for it to be enabled
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    await expect(page.getByRole("menu"), "a click on an item that is off keeps the menu open").toBeVisible();
    await page.keyboard.press("Escape");
    expect((await (await request.get("/api/no-sync", { headers: H })).json()).accounts).toEqual([]);
    await idle(page);
    await checkSizes(page, testInfo, "person set to Don't sync");

    // The backend refuses it too.
    const sync = await request.post(`/api/sources/${src.id}/sync`, { headers: H });
    expect(sync.status()).toBe(409);
    expect((await sync.json()).no_sync).toBe(true);

    // An account linked to nobody, too (sent with confirm: it may have a source).
    if (loose) {
      const r = await request.post("/api/no-sync", { headers: H, data: { account: { platform: loose.platform, id: loose.id }, no_sync: true, confirm: true } });
      expect((await r.json()).ok).toBe(true);
    }

    // Creators, Not synced only: both views.
    await page.goto("/creators?sync=off");
    const card = page.locator(".person-card", { hasText: person.name });
    await expect(card.locator(".nosync-chip")).toBeVisible();
    if (loose) await expect(page.locator(".creator-card:not(.person-card) .nosync-chip").first()).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Sync" })).toHaveValue("off");
    await idle(page);
    await checkSizes(page, testInfo, "Creators, Not synced, portraits");
    await page.getByRole("button", { name: "Compact" }).click();
    await idle(page);
    await checkSizes(page, testInfo, "Creators, Not synced, compact");
    await page.getByRole("button", { name: "Portraits" }).click();

    // Turned off from its card's ⋯ menu: syncs again.
    await (await menuItem(page, card, `More for ${person.name}`, "Sync again")).click();
    await expect.poll(async () => (await (await request.get(`/api/sources/${src.id}`, { headers: H })).json()).no_sync)
      .toBe(false);
  } finally {
    await request.post("/api/no-sync", { headers: H, data: { person: S.person, no_sync: false } });
    if (loose) {
      await request.post("/api/no-sync", { headers: H, data: { account: { platform: loose.platform, id: loose.id }, no_sync: false } });
    }
    if (src) await request.delete(`/api/sources/${src.id}`, { headers: H });
  }
});
