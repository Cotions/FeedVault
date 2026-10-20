// Desktop, 1280x800: the ⋯ menus (components/ActionMenu.jsx) that hold a
// card's or a row's secondary actions. Opened and closed with the mouse and
// the keyboard (the ARIA menu button pattern), focus given back to the ⋯
// button, the menu in the window and on top of every card (the top layer),
// turned upward near the bottom of the window; the Muted and Not synced
// markers that show what its switches are set to; the person page's
// Rename, from its menu, giving focus back to it; a source's creator, set
// from its menu and from the person page. People and a source are made for
// the tests and deleted after; every switch is turned off again.
import {
  test, expect, openPage, idle, menuItem, openMenu, checkLayout, dismissToasts, formatFindings, settle,
} from "./fixtures.js";

const H = { "X-FeedVault": "1" };

test.use({ viewport: { width: 1280, height: 800 } });

// An account linked to nobody, with an id (it has Review, Make creator, Mute, Don't sync).
async function looseAccount(request) {
  const authors = await (await request.get("/api/authors", { headers: H })).json();
  const a = authors.find(x => !x.person && x.id != null && x.handle);
  expect(a, "an account linked to nobody").toBeTruthy();
  return a;
}

// The menu's box in the window, and what a click at its corners and its
// centre reaches: the menu itself, over whatever is under it.
const placement = menu => menu.evaluate(el => {
  const r = el.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const pts = [[r.left + 4, r.top + 4], [r.right - 4, r.top + 4], [r.left + 4, r.bottom - 4], [r.right - 4, r.bottom - 4],
    [r.left + r.width / 2, r.top + r.height / 2]];
  return {
    inside: r.left >= 0 && r.top >= 0 && r.right <= vw && r.bottom <= innerHeight,
    onTop: pts.every(([x, y]) => el.contains(document.elementFromPoint(x, y))),
    box: r.toJSON(),
  };
});

const focusedText = page => page.evaluate(() => document.activeElement?.textContent.trim());

test("an account card's ⋯ menu: mouse, keyboard, focus, in the window and on top", async ({ page, request }) => {
  const a = await looseAccount(request);
  const label = `More for @${a.handle}`;
  await openPage(page, { name: "Creators", path: "/creators" });
  await page.getByLabel("Filter creators").fill(a.handle);
  const button = page.getByRole("button", { name: label, exact: true }).first();
  const card = page.locator(".creator-card").filter({ has: button }).first();
  await expect(button).toHaveAttribute("aria-haspopup", "menu");
  await expect(button).toHaveAttribute("aria-expanded", "false");
  // Sync is the one quick action in sight (when it has a source); the rest is in the menu.
  await expect(card.getByRole("link", { name: /^Review / })).toHaveCount(0);
  await expect(card.getByRole("button", { name: /^Mute / })).toHaveCount(0);
  await idle(page);

  // A click opens it, focus on its first item; in the window, on top.
  await button.click();
  const menu = page.getByRole("menu", { name: label, exact: true });
  await expect(menu).toBeVisible();
  await expect(button).toHaveAttribute("aria-expanded", "true");
  const items = menu.getByRole("menuitem");
  await expect(items.first()).toHaveText("Review");
  await expect(items.first()).toBeFocused();
  await expect(menu.getByRole("menuitem", { name: "Make creator" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Mute", exact: true })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Don't sync" })).toBeVisible();
  const at = await placement(menu);
  expect(at.inside, `in the window: ${JSON.stringify(at.box)}`).toBe(true);
  expect(at.onTop, "on top where it shows").toBe(true);
  // Under the button, its right edge on the button's.
  const b = await button.boundingBox();
  expect(at.box.top).toBeGreaterThanOrEqual(b.y + b.height);
  expect(Math.abs(at.box.right - (b.x + b.width))).toBeLessThanOrEqual(1);

  // Arrow keys move and wrap, Home and End, a letter jumps.
  const n = await items.count();
  await page.keyboard.press("ArrowDown");
  await expect(items.nth(1)).toBeFocused();
  await page.keyboard.press("End");
  await expect(items.nth(n - 1)).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(items.first()).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await expect(items.nth(n - 1)).toBeFocused();
  await page.keyboard.press("Home");
  await expect(items.first()).toBeFocused();
  await page.keyboard.press("m");
  expect(await focusedText(page)).toBe("Make creator");

  // Escape closes it onto the button.
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(button).toBeFocused();
  await expect(button).toHaveAttribute("aria-expanded", "false");

  // Enter opens it on the first item, Arrow Up on the last.
  await page.keyboard.press("Enter");
  await expect(items.first()).toBeFocused();
  await page.keyboard.press("Escape");
  await page.keyboard.press("ArrowUp");
  await expect(items.last()).toBeFocused();

  // Tab closes it and moves on from the button: Shift+Tab to the stop
  // before it, in the card; Tab past it (the filtered card is the page's
  // last stop, so the browser's own UI is next).
  await page.keyboard.press("Shift+Tab");
  await expect(menu).toHaveCount(0);
  const back = await page.evaluate(() => ({
    inCard: !!document.activeElement.closest(".creator-card"),
    onButton: document.activeElement.classList.contains("action-menu-btn"),
  }));
  expect(back, "Shift+Tab lands on the card's stop before ⋯").toEqual({ inCard: true, onButton: false });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(items.first()).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(menu).toHaveCount(0);
  await expect(button).not.toBeFocused();

  // A click outside closes it; a second click on the button too.
  await button.click();
  await expect(menu).toBeVisible();
  await page.locator("h2.page-title").click();
  await expect(menu).toHaveCount(0);
  await button.click();
  await expect(menu).toBeVisible();
  await button.click();
  await expect(menu).toHaveCount(0);
  await idle(page);
});

test("a ⋯ menu near the bottom of the window turns upward", async ({ page }) => {
  await openPage(page, { name: "Creators", path: "/creators" });
  const button = page.locator(".creator-card:not(.person-card) .action-menu-btn").nth(3);
  await button.scrollIntoViewIfNeeded();
  await button.evaluate(el => window.scrollBy(0, el.getBoundingClientRect().bottom - innerHeight + 12));
  const b = await button.boundingBox();
  expect(b.y + b.height, "the button is low in the window").toBeGreaterThan(800 - 60);
  await button.click();
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  const at = await placement(menu);
  expect(at.inside, `in the window: ${JSON.stringify(at.box)}`).toBe(true);
  expect(at.onTop, "on top where it shows").toBe(true);
  expect(at.box.bottom, "above the button").toBeLessThanOrEqual(b.y);
  await page.keyboard.press("Escape");
  await expect(button).toBeFocused();
  await idle(page);
});

test("Muted and Not synced markers, on the cards and the person page, nothing in the normal state", async ({ page, request }) => {
  const a = await looseAccount(request);
  const ref = { platform: a.platform, id: a.id };
  const stamp = Date.now();
  const name = `E2E markers ${stamp}`;
  const person = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person;
  try {
    // A person set to both (a person with no source: nothing to confirm).
    await request.post("/api/new/mute", { headers: H, data: { person: person.id, muted: true } });
    await request.post("/api/no-sync", { headers: H, data: { person: person.id, no_sync: true, confirm: true } });

    // An account card: nothing, then Muted from its menu, then nothing again.
    await openPage(page, { name: "Creators", path: "/creators" });
    await page.getByLabel("Filter creators").fill(a.handle);
    const label = `More for @${a.handle}`;
    const card = page.locator(".creator-card").filter({ has: page.getByRole("button", { name: label, exact: true }) }).first();
    await expect(card.locator(".state-line")).toHaveCount(0);
    await idle(page);
    await (await menuItem(page, card, label, "Mute")).click();
    await expect(card.locator(".state-chip")).toHaveText("Muted");
    await expect(page.getByRole("button", { name: label, exact: true }).first()).toBeFocused();
    const unmute = await menuItem(page, card, label, "Unmute");
    await unmute.click();
    await expect(card.locator(".state-line")).toHaveCount(0);

    // The person set to both: their card, in both views, and their page.
    await page.getByLabel("Filter creators").fill(name);
    const pcard = page.locator(".person-card", { hasText: name });
    for (const view of ["Portraits", "Compact"]) {
      await page.getByRole("group", { name: "Show people as" }).getByRole("button", { name: view }).click();
      await expect(pcard.locator(".nosync-chip")).toBeVisible();
      await expect(pcard.locator(".state-chip")).toHaveText("Muted");
      const menu = await openMenu(page, pcard, `More for ${name}`);
      await expect(menu.getByRole("menuitem", { name: "Unmute" })).toBeVisible();
      await expect(menu.getByRole("menuitem", { name: "Sync again" })).toBeVisible();
      await page.keyboard.press("Escape");
    }
    await page.getByRole("group", { name: "Show people as" }).getByRole("button", { name: "Portraits" }).click();
    await page.goto(`/people/${person.id}`);
    await expect(page.locator(".person-new .nosync-chip")).toBeVisible();
    await expect(page.locator(".person-new .state-chip")).toHaveText("Muted");
    await idle(page);
  } finally {
    await request.post("/api/new/mute", { headers: H, data: { account: ref, muted: false } });
    await request.post("/api/new/mute", { headers: H, data: { person: person.id, muted: false } });
    await request.post("/api/no-sync", { headers: H, data: { person: person.id, no_sync: false } });
    await request.delete(`/api/people/${person.id}`, { headers: H });
  }
});

test("a person's ⋯ menu: Rename gives focus back to it, Delete person still asks", async ({ page, request }) => {
  const stamp = Date.now();
  const name = `E2E menu ${stamp}`;
  const person = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person;
  try {
    await page.goto(`/people/${person.id}`);
    await expect(page.locator("h2.page-title")).toHaveText(name);
    await idle(page);
    const head = page.locator(".page-head");
    // "← Creators" and the ⋯ menu; no Rename, Delete person or Mute in sight.
    await expect(head.getByRole("button", { name: "Rename" })).toHaveCount(0);
    await expect(head.getByRole("button", { name: /Delete person/ })).toHaveCount(0);
    const more = page.getByRole("button", { name: `More for ${name}`, exact: true });
    const menu = await openMenu(page, page, `More for ${name}`);
    await expect(menu.getByRole("menuitem")).toHaveText(["Rename", "Mute", "Don't sync", "Delete person"]);
    await expect(menu.getByRole("menuitem", { name: "Delete person" })).toHaveClass(/is-danger/);
    const at = await placement(menu);
    expect(at.inside && at.onTop, `in the window, on top: ${JSON.stringify(at.box)}`).toBe(true);

    // Rename: the field takes focus; Escape and Cancel give it back to ⋯.
    await menu.getByRole("menuitem", { name: "Rename" }).click();
    const field = page.getByRole("textbox", { name: "Name", exact: true });
    await expect(field).toBeFocused();
    await expect(field).toHaveValue(name);
    await page.keyboard.press("Escape");
    await expect(field).toHaveCount(0);
    await expect(more).toBeFocused();
    // By the keyboard: Enter opens the menu, Enter on Rename.
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: "Rename" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(field).toBeFocused();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(more).toBeFocused();
    // Saved: the new name, focus on its ⋯ (named for it now).
    await (await menuItem(page, page, `More for ${name}`, "Rename")).click();
    await field.fill(`${name} renamed`);
    await field.press("Enter");
    await expect(page.locator("h2.page-title")).toHaveText(`${name} renamed`);
    await expect(page.getByRole("button", { name: `More for ${name} renamed`, exact: true })).toBeFocused();

    // Delete person asks first; Cancel gives focus back to ⋯.
    await (await menuItem(page, page, `More for ${name} renamed`, "Delete person")).click();
    const ask = page.getByRole("alertdialog");
    await expect(ask).toBeVisible();
    await ask.getByRole("button", { name: "Cancel" }).click();
    await expect(ask).toHaveCount(0);
    await expect(page.getByRole("button", { name: `More for ${name} renamed`, exact: true })).toBeFocused();
    await idle(page);
  } finally {
    await request.delete(`/api/people/${person.id}`, { headers: H });
  }
});

test("a source's creator: Link to creator…, Move, Unlink, and the person page's Add an account", async ({ page, request }, testInfo) => {
  const stamp = Date.now();
  const handle = `e2e_link_${stamp}`;
  const name = `E2E link ${stamp}`;
  const person = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person;
  const src = (await (await request.post("/api/sources", {
    headers: H, data: { target: `https://x.com/${handle}` } })).json()).source;
  expect(src?.person).toBeNull();
  const made = [];
  const personOf = async () => (await (await request.get(`/api/sources/${src.id}`, { headers: H })).json()).person;
  const label = `More for x.com/${handle}`;
  try {
    // Creators: loose, linked from its ⋯ menu to a person there is.
    await openPage(page, { name: "Creators", path: "/creators" });
    const row = page.locator(`.source-row[data-source-id="${src.id}"]`);
    await expect(row).toBeVisible();
    await idle(page);
    const menu = await openMenu(page, row, label);
    await expect(menu.getByRole("menuitem", { name: "Unlink from creator" })).toHaveCount(0);
    await menu.getByRole("menuitem", { name: "Link to creator…" }).click();
    const dialog = page.getByRole("dialog", { name: `Link x.com/${handle} to…` });
    await expect(dialog).toBeVisible();
    const field = dialog.getByRole("combobox", { name: "Creator" });
    await expect(field).toBeFocused();
    await dismissToasts(page);
    await settle(page);
    const found = await checkLayout(page, testInfo, { view: "Link to creator", size: "1280x800", scope: ".modal-overlay" });
    await page.screenshot({ path: testInfo.outputPath("link-to-creator.png") });
    expect(found, `layout findings:\n${formatFindings(found)}`).toEqual([]);
    await field.fill(name);
    await page.keyboard.press("Enter");
    await expect(dialog).toHaveCount(0);
    await expect(row, "it leaves the loose sources").toHaveCount(0);
    expect((await personOf())?.id).toBe(person.id);

    // Their page lists it; Move to a new creator, made by name in the dialog.
    await page.goto(`/people/${person.id}`);
    const mine = page.locator(`.source-row[data-source-id="${src.id}"]`);
    await expect(mine).toBeVisible();
    await idle(page);
    await (await menuItem(page, mine, label, "Move to creator…")).click();
    const move = page.getByRole("dialog", { name: `Move x.com/${handle} to…` });
    await move.getByRole("textbox", { name: "Or a new creator" }).fill(`${name} new`);
    await move.getByRole("button", { name: "Create and link" }).click();
    await expect(move).toHaveCount(0);
    await expect(mine, "it leaves this person's page").toHaveCount(0);
    const moved = await personOf();
    expect(moved?.name).toBe(`${name} new`);
    made.push(moved.id);

    // Unlinked from there: loose again.
    await page.goto(`/people/${moved.id}`);
    const theirs = page.locator(`.source-row[data-source-id="${src.id}"]`);
    await expect(theirs).toBeVisible();
    await idle(page);
    await (await menuItem(page, theirs, label, "Unlink from creator")).click();
    await expect(theirs).toHaveCount(0);
    expect(await personOf()).toBeNull();

    // A person's Add an account lists a loose source that never synced, and links it.
    await page.goto(`/people/${person.id}`);
    const add = page.getByRole("combobox", { name: "Add an account" });
    await add.click();
    await add.fill(handle);
    await expect(page.getByRole("option", { name: new RegExp(handle) })).toContainText("not synced yet");
    await page.keyboard.press("Enter");
    await expect(page.locator(`.source-row[data-source-id="${src.id}"]`)).toBeVisible();
    expect((await personOf())?.id).toBe(person.id);
  } finally {
    await request.delete(`/api/sources/${src.id}`, { headers: H });
    for (const id of [person.id, ...made]) await request.delete(`/api/people/${id}`, { headers: H });
  }
});
