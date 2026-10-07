// Desktop, 1440x900: saved links, on the Links page and on a person's
// page. Addresses are invented (.example); FeedVault never opens them, and
// neither does the test: it checks the anchors, it does not follow them.
import { test, expect, openPage, idle, stressData } from "./fixtures.js";
import { STRESS_LINKS } from "./stress.js";

const H = { "X-FeedVault": "1" };

// The anchor of a saved link opens a new tab with no opener and no referrer.
async function expectSafeAnchor(row, url) {
  const a = row.locator("a.link-row-title");
  await expect(a).toHaveAttribute("href", url);
  await expect(a).toHaveAttribute("target", "_blank");
  const rel = (await a.getAttribute("rel")).split(/\s+/);
  expect(rel).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));
}

test("Links page: add, edit and delete a link", async ({ page }) => {
  const url = `https://e2e.example/links-page-${Date.now()}`;
  await openPage(page, { name: "Links", path: "/links" });
  const form = page.locator(".links-add .link-form");
  await form.getByLabel("Address").fill(`  ${url}  `);
  await form.getByLabel("Title").fill("E2E link");
  await form.getByRole("button", { name: "Add link" }).click();
  const row = page.locator(".link-row", { hasText: "E2E link" });
  await expect(row).toHaveCount(1);
  await expect(row.locator(".link-site")).toHaveText("e2e.example");
  await expect(row.locator(".link-kind")).toHaveText("Other");
  await expectSafeAnchor(row, url);
  await expect(form.getByLabel("Address")).toHaveValue("");

  await row.getByRole("button", { name: "Edit E2E link" }).click();
  const edit = page.locator(".link-row.is-editing");
  await edit.getByLabel("Title").fill("E2E link, edited");
  await edit.getByRole("button", { name: "Save" }).click();
  const edited = page.locator(".link-row", { hasText: "E2E link, edited" });
  await expect(edited).toHaveCount(1);
  await expectSafeAnchor(edited, url);

  await edited.getByRole("button", { name: "Delete E2E link, edited" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(url);
  await dialog.getByRole("button", { name: "Delete link" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".link-row", { hasText: "E2E link" })).toHaveCount(0);
  await idle(page);
});

test("a person's Links: add, reorder, edit and delete", async ({ page }) => {
  const stamp = Date.now();
  const made = await page.request.post("/api/people", { headers: H, data: { name: `E2E links ${stamp}` } });
  const pid = (await made.json()).person.id;
  try {
    await page.goto(`/people/${pid}`);
    const section = page.locator(".person-links-section");
    await expect(section.locator(".empty")).toBeVisible();
    const form = section.locator(".person-link-add .link-form");
    for (const [path, title] of [["one", "First"], ["two", "Second"]]) {
      await form.getByLabel("Address").fill(`https://e2e-${stamp}.example/${path}`);
      await form.getByLabel("Title").fill(title);
      await form.getByRole("button", { name: "Add link" }).click();
      await expect(section.locator(".link-row", { hasText: title })).toHaveCount(1);
    }
    await form.getByLabel("Address").fill(`https://www.patreon.com/e2e${stamp}`);
    await form.getByRole("button", { name: "Add link" }).click();
    // Socials first, then the others in the order added.
    const titles = section.locator(".link-row .link-row-title");
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "First", "Second"]);
    await expect(section.locator(".link-group-title")).toHaveText(["Socials", "Other", "Add a link"]);

    await section.getByRole("button", { name: "Move Second up" }).click();
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "Second", "First"]);
    await page.reload();
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "Second", "First"]);

    await section.getByRole("button", { name: "Edit First" }).click();
    const edit = section.locator(".link-row.is-editing");
    await edit.getByLabel("Title").fill("First, edited");
    await edit.getByRole("button", { name: "Save" }).click();
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "Second", "First, edited"]);
    await expectSafeAnchor(section.locator(".link-row", { hasText: "First, edited" }), `https://e2e-${stamp}.example/one`);

    await section.getByRole("button", { name: "Delete Second" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete link" }).click();
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "First, edited"]);
    await idle(page);
  } finally {
    // The person's links stay, tied to no one, once the person is gone: both cleared.
    const { links } = await (await page.request.get(`/api/links?person=${pid}`, { headers: H })).json();
    for (const l of links) await page.request.delete(`/api/links/${l.id}`, { headers: H });
    await page.request.delete(`/api/people/${pid}`, { headers: H });
  }
});

// Editing the stress link with notes over several lines: the field opens
// with all of them in view (it grows with its text), not one line of them.
// Escape closes it unchanged.
test("editing a link shows its whole notes", async ({ page }) => {
  const stress = STRESS_LINKS.find(l => l.notes);
  await openPage(page, { name: "Links", path: "/links" });
  const row = page.locator(".link-row", { has: page.locator(`a[href="${stress.url}"]`) });
  await row.getByRole("button", { name: /^Edit / }).click();
  const notes = page.locator(".link-row.is-editing").getByLabel("Notes");
  await expect(notes).toHaveValue(stress.notes);
  const m = await notes.evaluate(t => ({ client: t.clientHeight, scroll: t.scrollHeight }));
  expect(m.scroll, "the notes need no scrolling").toBeLessThanOrEqual(m.client + 1);
  expect(m.client, "taller than one line").toBeGreaterThan(60);
  await notes.press("Escape");
  await expect(page.locator(".link-row.is-editing")).toHaveCount(0);
  await idle(page);
});

// A person's page, by Tab: out of Add an account (a picker whose open list
// of every account scrolls) on to the link-in-bio import's link to Settings
// (the import is off in this instance), not to <body>: the open list was a
// Tab stop (Chromium's focusable scrollers) that went as the field lost focus.
test("Tab goes on from a person's Add an account picker", async ({ page }) => {
  await page.goto(`/people/${stressData().person}`);
  await expect(page.locator(".person-new button")).toBeVisible();
  await idle(page);
  const picker = page.getByRole("combobox", { name: "Add an account" });
  await picker.focus();
  await expect(page.locator(".person-add .picker-list")).toBeVisible();
  expect(await page.locator(".person-add .picker-list").evaluate(ul => ul.scrollHeight > ul.clientHeight), "the open list scrolls").toBe(true);
  await page.keyboard.press("Tab");
  await expect(page.locator(".bio-import").getByRole("link", { name: "Turn it on in Settings" })).toBeFocused();
  await idle(page);
});

async function makeLinks(request, urls) {
  const ids = [];
  for (const url of urls) {
    const r = await request.post("/api/links", { headers: H, data: { url, title: url.replace(/^https:\/\//, "") } });
    ids.push((await r.json()).link.id);
  }
  return ids;
}

// #126: with filters on, the count is "shown of all"; the filters are in
// the address, so a reload and Back keep them.
test("Links: the count follows the filters, which a reload and Back keep", async ({ page, request }) => {
  const host = `e2e-filters-${Date.now()}.example`;
  const ids = await makeLinks(request, [`https://${host}/a`, `https://${host}/b`]);
  try {
    await openPage(page, { name: "Links", path: "/links" });
    const count = page.locator(".page-head .page-count");
    await expect(count).toHaveText(/^[\d,]+$/);
    const total = await count.textContent();
    const filters = page.locator(".links-filters");
    const rows = page.locator(".link-list > li");

    await filters.getByLabel("Site").selectOption(host);
    await expect(page).toHaveURL(`/links?site=${host}`);
    await expect(rows).toHaveCount(2);
    await expect(count).toHaveText(`2 of ${total}`);

    await page.reload();
    await expect(filters.getByLabel("Site")).toHaveValue(host);
    await expect(rows).toHaveCount(2);
    await expect(count).toHaveText(`2 of ${total}`);

    const search = page.getByRole("textbox", { name: "Search links" });
    await search.fill(`${host}/b`);
    await expect(page).toHaveURL(`/links?site=${host}&q=${encodeURIComponent(`${host}/b`)}`);
    await expect(count).toHaveText(`1 of ${total}`);

    await page.locator('#main-nav a.side-link[href="/tags"]').click();
    await expect(page).toHaveURL(/\/tags$/);
    await page.goBack();
    await expect(search).toHaveValue(`${host}/b`);
    await expect(filters.getByLabel("Site")).toHaveValue(host);
    await expect(rows).toHaveCount(1);
    await expect(count).toHaveText(`1 of ${total}`);

    await filters.getByRole("button", { name: "Clear filters" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await expect(search).toHaveValue("");
    await expect(count).toHaveText(total);
    await idle(page);
  } finally {
    for (const id of ids) await request.delete(`/api/links/${id}`, { headers: H });
  }
});

// A filter change while a link has unsaved edits asks first (it may hide
// the link); Clear filters hides none, and goes on.
test("Links: a filter change asks before dropping a link's unsaved edits", async ({ page, request }) => {
  const host = `e2e-guard-${Date.now()}.example`;
  const ids = await makeLinks(request, [`https://${host}/a`]);
  try {
    await page.goto(`/links?q=${host}`);
    const rows = page.locator(".link-list > li");
    await expect(rows).toHaveCount(1);
    await idle(page);
    const dialog = page.getByRole("alertdialog");
    const kind = page.locator(".links-filters").getByLabel("Kind");
    const search = page.getByRole("textbox", { name: "Search links" });
    await page.getByRole("button", { name: `Edit ${host}/a` }).click();
    const editing = page.locator(".link-row.is-editing");
    await editing.getByLabel("Title").fill("typed, not saved");

    await kind.selectOption("social");
    await expect(dialog).toContainText("not saved");
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(`/links?q=${host}`);
    await expect(kind).toHaveValue("");
    await expect(editing.getByLabel("Title")).toHaveValue("typed, not saved");

    await search.fill(`${host}/zzz`);
    await expect(dialog).toContainText("not saved");
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(search).toHaveValue(host);
    await expect(page).toHaveURL(`/links?q=${host}`);
    await expect(editing.getByLabel("Title")).toHaveValue("typed, not saved");

    await page.locator(".links-filters").getByRole("button", { name: "Clear filters" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await expect(dialog).toHaveCount(0);
    await expect(editing.getByLabel("Title")).toHaveValue("typed, not saved");

    await kind.selectOption("social");
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL("/links?kind=social");
    await expect(page.locator(".link-row", { hasText: host })).toHaveCount(0);
    await idle(page);
  } finally {
    for (const id of ids) await request.delete(`/api/links/${id}`, { headers: H });
  }
});

// #126: an edit to an address saved already points at that link, as Add
// does: every filter off, the link lit and in view, the edit still open.
test("Links: an edit to a saved address lights the link that has it", async ({ page, request, pageErrors }) => {
  const host = `e2e-taken-${Date.now()}.example`;
  const [a, b] = await makeLinks(request, [`https://${host}/a`, `https://${host}/b`]);
  // The server's answer to the taken address: a 409, which the test is about.
  pageErrors.allow(new RegExp(`^api: POST \\S+/api/links/${b} → 409$`));
  pageErrors.allow(new RegExp(`^console: .*status of 409 .*/api/links/${b}\\)$`));
  try {
    await page.goto(`/links?q=${encodeURIComponent(`${host}/b`)}`);
    await expect(page.locator(".link-list > li")).toHaveCount(1);
    await idle(page);
    await page.getByRole("button", { name: `Edit ${host}/b` }).click();
    const editing = page.locator(".link-row.is-editing");
    await editing.getByLabel("Address").fill(`https://${host}/a`);
    await editing.getByRole("button", { name: "Save" }).click();
    await expect(editing.getByRole("alert")).toContainText("saved already");
    await expect(page).toHaveURL(/\/links$/);
    const lit = page.locator(`.link-row.is-flash[data-link-id="${a}"]`);
    await expect(lit).toHaveCount(1);
    await expect(lit).toBeInViewport();
    await expect(editing.getByLabel("Address")).toHaveValue(`https://${host}/a`);
    await idle(page);
  } finally {
    const { links } = await (await request.get(`/api/links?q=${host}`, { headers: H })).json();
    for (const l of links) await request.delete(`/api/links/${l.id}`, { headers: H });
  }
});

// #126: an address typed with no scheme is saved as https://, not refused.
test("Links: an address with no scheme is added as https://", async ({ page, request }) => {
  const host = `e2e-scheme-${Date.now()}.example`;
  try {
    await openPage(page, { name: "Links", path: "/links" });
    const form = page.locator(".links-add .link-form");
    await form.getByLabel("Address").fill(`${host}/x`);
    await form.getByRole("button", { name: "Add link" }).click();
    const row = page.locator(".link-row", { has: page.locator(`a[href="https://${host}/x"]`) });
    await expect(row).toHaveCount(1);
    await expect(form.getByRole("alert")).toHaveCount(0);
    await expect(form.getByLabel("Address")).toHaveValue("");
    await idle(page);
  } finally {
    const { links } = await (await request.get(`/api/links?q=${host}`, { headers: H })).json();
    for (const l of links) await request.delete(`/api/links/${l.id}`, { headers: H });
  }
});
