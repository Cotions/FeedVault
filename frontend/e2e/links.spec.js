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

/* ── Quick-add (#165 A): Alt+L, the nav's + Link, a dropped URL ── */

// The clipboard as the quick-add reads it: this text, or a refusal (as a
// browser that was not given the permission). No real clipboard is touched.
async function stubClipboard(page, text) {
  await page.addInitScript(t => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { readText: () => (t == null ? Promise.reject(new DOMException("denied", "NotAllowedError")) : Promise.resolve(t)) },
    });
  }, text);
}

// A drag from outside the page carrying ``url``, over the page; then
// dropped on the drop overlay, once it shows.
async function dragUrlIn(page, url) {
  await page.evaluate(u => {
    const dt = new DataTransfer();
    dt.setData("text/uri-list", u);
    dt.setData("text/plain", u);
    window.__fvDrop = dt;
    const main = document.querySelector("main");
    for (const type of ["dragenter", "dragover"]) {
      main.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
    }
  }, url);
}
async function dropOnOverlay(page) {
  await page.evaluate(() => {
    const el = document.querySelector(".drop-overlay");
    el.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: window.__fvDrop }));
  });
}

async function linkByUrl(request, url) {
  const { links } = await (await request.get(`/api/links?q=${encodeURIComponent(new URL(url).host)}`, { headers: H })).json();
  return links.find(l => l.url === url) || null;
}

async function cleanUp(request, host, pid) {
  const { links } = await (await request.get(`/api/links?q=${encodeURIComponent(host)}`, { headers: H })).json();
  for (const l of links) await request.delete(`/api/links/${l.id}`, { headers: H });
  if (pid != null) await request.delete(`/api/people/${pid}`, { headers: H });
}

test("quick-add: Alt+L prefills the clipboard's address, Enter saves to a recent person", async ({ page, request }) => {
  const stamp = Date.now();
  const host = `e2e-quick-${stamp}.example`;
  const name = `E2E recent ${stamp}`;
  const pid = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person.id;
  try {
    // Given a link just now: the most recent person, first under "No person".
    await request.post("/api/links", { headers: H, data: { url: `https://${host}/older`, person: pid } });
    await stubClipboard(page, `  https://${host}/from-clipboard \n`);
    await openPage(page, { name: "Links", path: "/links" });
    await page.locator("body").press("Alt+l");
    const dialog = page.getByRole("dialog", { name: "Add a link" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Address")).toHaveValue(`https://${host}/from-clipboard`);
    const picker = dialog.getByRole("combobox", { name: "Person" });
    await expect(picker).toBeFocused();
    const options = dialog.getByRole("option");
    await expect(options.first()).toHaveText("No person (Unsorted)");
    await expect(options.nth(1)).toHaveText(name);
    await expect(dialog.locator(".person-pick-head").first()).toHaveText("Recent");
    await expect(options.first()).toHaveAttribute("aria-selected", "true");

    await picker.press("ArrowDown");
    await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
    await expect(dialog.getByRole("button", { name: `Save to ${name}` })).toBeVisible();
    await dialog.getByLabel("Title").fill("From the clipboard");
    await picker.press("Enter");
    await expect(dialog).toBeHidden();
    const toast = page.locator(".toast", { hasText: `Link saved to ${name}` });
    await expect(toast).toBeVisible();
    await expect(toast.getByRole("link", { name: "Show" })).toHaveAttribute("href", `/people/${pid}`);
    const saved = await linkByUrl(request, `https://${host}/from-clipboard`);
    expect(saved.person).toEqual({ id: pid, name });
    expect(saved.title).toBe("From the clipboard");
    // The Links page lists it without a reload.
    await expect(page.locator(".link-row", { hasText: "From the clipboard" })).toHaveCount(1);

    // Typing searches everyone; Alt+L again (from a button) keeps what was typed; Escape closes.
    await page.locator("body").press("Alt+l");
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Title").fill("typed, kept");
    await dialog.getByRole("combobox", { name: "Person" }).fill(`recent ${stamp}`);
    await expect(options).toHaveText([name, "No person (Unsorted)"]);
    await dialog.getByRole("button", { name: "Cancel" }).focus();
    await page.keyboard.press("Alt+l");
    await expect(dialog.getByLabel("Title")).toHaveValue("typed, kept");
    await expect(dialog.getByRole("combobox", { name: "Person" })).toHaveValue(`recent ${stamp}`);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await idle(page);
  } finally {
    await cleanUp(request, host, pid);
  }
});

test("quick-add: the nav's + Link saves a typed address to no one; no clipboard is fine", async ({ page, request }) => {
  const host = `e2e-quick-untied-${Date.now()}.example`;
  try {
    await stubClipboard(page, null);                 // the permission refused: nothing shows, nothing fails
    await openPage(page, { name: "Feed", path: "/" });
    await page.locator("#main-nav").getByRole("button", { name: "Add a link" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a link" });
    const address = dialog.getByLabel("Address");
    await expect(address).toBeFocused();
    await expect(address).toHaveValue("");
    await address.fill(`${host}/typed`);
    await expect(dialog.getByRole("button", { name: "Save to Unsorted" })).toBeVisible();
    await address.press("Enter");
    await expect(dialog).toBeHidden();
    const toast = page.locator(".toast", { hasText: "Link saved to Unsorted" });
    await expect(toast.getByRole("link", { name: "Show" })).toHaveAttribute("href", "/links?person=none");
    const saved = await linkByUrl(request, `https://${host}/typed`);
    expect(saved.person).toBeNull();
    await idle(page);
  } finally {
    await cleanUp(request, host);
  }
});

test("quick-add: Alt+L while typing types, it does not open", async ({ page }) => {
  await stubClipboard(page, null);
  await openPage(page, { name: "Links", path: "/links" });
  await page.getByRole("textbox", { name: "Search posts" }).press("Alt+l");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await idle(page);
});

test("quick-add: a dropped URL opens it; one saved already says whose it is", async ({ page, request, pageErrors }) => {
  const stamp = Date.now();
  const host = `e2e-quick-drop-${stamp}.example`;
  const name = `E2E dropped ${stamp}`;
  const pid = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person.id;
  // The server's answer to the address saved already: a 409, which the test is about.
  pageErrors.allow(/^api: POST \S+\/api\/links → 409$/);
  pageErrors.allow(/^console: .*status of 409 .*\/api\/links\)$/);
  try {
    await openPage(page, { name: "Collections", path: "/collections" });
    await dragUrlIn(page, `https://${host}/dropped`);
    await expect(page.locator(".drop-overlay")).toBeVisible();
    await expect(page.locator(".drop-overlay")).toHaveText("Drop to add the link");
    await dropOnOverlay(page);
    await expect(page.locator(".drop-overlay")).toHaveCount(0);
    const dialog = page.getByRole("dialog", { name: "Add a link" });
    await expect(dialog.getByLabel("Address")).toHaveValue(`https://${host}/dropped`);
    const picker = dialog.getByRole("combobox", { name: "Person" });
    await expect(picker).toBeFocused();
    await picker.fill(name);
    await picker.press("Enter");                      // the one match, highlighted
    await expect(page.locator(".toast", { hasText: `Link saved to ${name}` })).toBeVisible();

    // The same address again: not saved twice, and the message leads to its person.
    await dragUrlIn(page, `https://${host}/dropped`);
    await dropOnOverlay(page);
    await expect(dialog.getByLabel("Address")).toHaveValue(`https://${host}/dropped`);
    await dialog.getByRole("combobox", { name: "Person" }).press("Enter");    // to no one
    const alert = dialog.locator(".quick-add-taken");
    await expect(alert).toHaveText(`Already saved, tied to ${name}.`);
    await alert.getByRole("link", { name }).click();
    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(`/people/${pid}`);
    await idle(page);
  } finally {
    await cleanUp(request, host, pid);
  }
});

test("quick-add: a drag that starts in the page is left to the page", async ({ page }) => {
  await openPage(page, { name: "Collections", path: "/collections" });
  // As a collection card's reorder, or an image dragged: dragstart comes first.
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.setData("text/uri-list", "https://inside.example/");
    const main = document.querySelector("main");
    main.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: dt }));
    main.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt }));
  });
  await expect(page.locator(".drop-overlay")).toHaveCount(0);
  await page.evaluate(() => {
    const main = document.querySelector("main");
    main.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: new DataTransfer() }));
    main.dispatchEvent(new DragEvent("dragend", { bubbles: true }));
  });
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // Its dragend never came (the dragged node went away): a drag from
  // outside, once the page's drag events have stopped, is taken again.
  await page.evaluate(() => document.querySelector("main")
    .dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: new DataTransfer() })));
  await page.waitForTimeout(800);
  await dragUrlIn(page, "https://outside.example/");
  await expect(page.locator(".drop-overlay")).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new DragEvent("dragleave", { relatedTarget: null })));
  await expect(page.locator(".drop-overlay")).toHaveCount(0);
  await idle(page);
});

test("quick-add: text dragged into a text field is left to the field; plain words open nothing", async ({ page }) => {
  await openPage(page, { name: "Links", path: "/links" });
  const notes = page.locator(".links-add .link-form").getByLabel("Notes");
  // Over and onto the notes field: not taken (the browser types it there), no overlay, even for a link.
  const taken = await notes.evaluate(el => {
    const out = [];
    for (const [type, text] of [["text/plain", "some words"], ["text/uri-list", "https://a.example/"]]) {
      const dt = new DataTransfer();
      dt.setData(type, text);
      for (const ev of ["dragenter", "dragover", "drop"]) {
        out.push(!el.dispatchEvent(new DragEvent(ev, { bubbles: true, cancelable: true, dataTransfer: dt })));
      }
    }
    return out;
  });
  expect(taken, "no drag event on the field was cancelled").toEqual([false, false, false, false, false, false]);
  await expect(page.locator(".drop-overlay")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // Plain words dropped on the page: no overlay while dragged, no dialog, no error.
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.setData("text/plain", "just some words");
    const main = document.querySelector("main");
    for (const ev of ["dragenter", "dragover", "drop"]) main.dispatchEvent(new DragEvent(ev, { bubbles: true, cancelable: true, dataTransfer: dt }));
  });
  await expect(page.locator(".drop-overlay")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".toast")).toHaveCount(0);

  // A plain-text address dropped on the page opens the quick-add with it.
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.setData("text/plain", "https://plain.example/x");
    const main = document.querySelector("main");
    for (const ev of ["dragenter", "dragover", "drop"]) main.dispatchEvent(new DragEvent(ev, { bubbles: true, cancelable: true, dataTransfer: dt }));
  });
  await expect(page.getByRole("dialog", { name: "Add a link" }).getByLabel("Address")).toHaveValue("https://plain.example/x");
  await page.keyboard.press("Escape");
  await idle(page);
});

/* ── Unsorted (#165 B): the queue, assign, select, Copy URLs ── */

// The clipboard as Copy URLs writes it: kept in window.__fvCopied, never
// the real one. Reading it is refused (the quick-add then reads nothing).
async function stubClipboardWrites(page) {
  await page.addInitScript(() => {
    window.__fvCopied = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: t => { window.__fvCopied.push(t); return Promise.resolve(); },
        readText: () => Promise.reject(new DOMException("denied", "NotAllowedError")),
      },
    });
  });
}
const copied = page => page.evaluate(() => window.__fvCopied);

async function unsortedCount(request) {
  return (await (await request.get("/api/jobs", { headers: H })).json()).unsorted_links;
}

test("Unsorted: the tab, and the nav's count that follows saves, deletes and assigns", async ({ page, request }) => {
  const stamp = Date.now();
  const host = `e2e-unsorted-${stamp}.example`;
  const name = `E2E sorter ${stamp}`;
  const pid = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person.id;
  await makeLinks(request, [`https://${host}/a`, `https://${host}/b`]);
  await request.post("/api/links", { headers: H, data: { url: `https://${host}/tied`, person: pid } });
  // The demo's own Unsorted links, given back to no one at the end.
  let parked = [];
  try {
    await stubClipboard(page, null);
    const before = await unsortedCount(request);
    await openPage(page, { name: "Links", path: "/links" });
    const badge = page.locator("#main-nav a.side-unsorted");
    await expect(badge).toHaveText(String(before));
    await expect(badge).toHaveAttribute("href", "/links?person=none");
    await expect(badge).toHaveAccessibleName(`Unsorted links: ${before}`);

    // One click to the queue, from the tab: only links of no one.
    const tab = page.locator(".links-tabs").getByRole("button", { name: /^Unsorted/ });
    await expect(tab).toHaveText(`Unsorted${before}`);
    await tab.click();
    await expect(page).toHaveURL("/links?person=none");
    await expect(tab).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("textbox", { name: "Search links" }).fill(host);
    const rows = page.locator(".link-list > li");
    await expect(rows).toHaveCount(2);
    await expect(rows.locator(".link-person")).toHaveCount(0);

    // A delete, a quick-add to no one: the count follows at once, not on the next poll.
    await page.getByRole("button", { name: `Delete ${host}/a` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete link" }).click();
    await expect(badge).toHaveText(String(before - 1));
    await page.locator("#main-nav").getByRole("button", { name: "Add a link" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a link" });
    await dialog.getByLabel("Address").fill(`https://${host}/quick`);
    await dialog.getByLabel("Address").press("Enter");
    await expect(dialog).toBeHidden();
    await expect(badge).toHaveText(String(before));
    await expect(rows).toHaveCount(2);

    // Nothing left to sort: no badge, and the queue says so.
    parked = (await (await request.get("/api/links?person=none", { headers: H })).json()).links.map(l => l.id);
    await request.post("/api/links/assign", { headers: H, data: { ids: parked, person: pid } });
    // (Changed from elsewhere: read on the next poll, or at once on a reload.)
    await page.goto("/links?person=none");
    await expect(page.locator(".card .empty")).toHaveText("Nothing to sort: every link has its person.");
    await expect(badge).toHaveCount(0);
    await expect(tab).toHaveText("Unsorted");
    await idle(page);
  } finally {
    if (parked.length) await request.post("/api/links/assign", { headers: H, data: { ids: parked, person: null } });
    await cleanUp(request, host, pid);
  }
});

test("Unsorted: Assign… gives a row's link to a recent person by keyboard, with Undo", async ({ page, request }) => {
  const stamp = Date.now();
  const host = `e2e-assign-${stamp}.example`;
  const name = `E2E assignee ${stamp}`;
  const pid = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person.id;
  try {
    await request.post("/api/links", { headers: H, data: { url: `https://${host}/theirs`, person: pid } });   // recent
    await makeLinks(request, [`https://${host}/one`, `https://${host}/two`]);
    await page.goto(`/links?person=none&q=${host}`);
    const rows = page.locator(".link-list > li");
    await expect(rows).toHaveCount(2);                      // newest first: two, one
    await idle(page);
    const badge = page.locator("#main-nav a.side-unsorted");
    const before = Number(await badge.textContent());

    await page.getByRole("button", { name: `Assign ${host}/two to a person` }).click();
    const picker = page.getByRole("combobox", { name: `Assign ${host}/two to` });
    await expect(picker).toBeFocused();
    const open = page.locator(".link-row.is-assigning");
    await expect(open.getByRole("option").first()).toHaveText(name);   // the most recent, and no "No person"
    await expect(open.locator(".person-pick-head").first()).toHaveText("Recent");
    await expect(open).not.toContainText("No person");
    await picker.press("Enter");
    await expect(rows).toHaveCount(1);
    const toast = page.locator(".toast", { hasText: `${host}/two: assigned to ${name}` });
    await expect(toast).toBeVisible();
    await expect(badge).toHaveText(String(before - 1));
    expect((await linkByUrl(request, `https://${host}/two`)).person).toEqual({ id: pid, name });
    // Focus went on to the next link's Assign…: Enter opens it, Escape closes it and gives focus back.
    const next = page.getByRole("button", { name: `Assign ${host}/one to a person` });
    await expect(next).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("combobox", { name: `Assign ${host}/one to` })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(open).toHaveCount(0);
    await expect(next).toBeFocused();

    // Undo: back with no one, in the queue again.
    await toast.getByRole("button", { name: "Undo" }).click();
    await expect(rows).toHaveCount(2);
    await expect(badge).toHaveText(String(before));
    expect((await linkByUrl(request, `https://${host}/two`)).person).toBeNull();
    await idle(page);
  } finally {
    await cleanUp(request, host, pid);
  }
});

test("Links: select with shift-click, copy the URLs, assign them all, delete", async ({ page, request }) => {
  const stamp = Date.now();
  const host = `e2e-select-${stamp}.example`;
  const name = `E2E bulk ${stamp}`;
  const pid = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person.id;
  const urls = ["a", "b", "c", "d"].map(x => `https://${host}/${x}`);
  try {
    await makeLinks(request, urls);
    await stubClipboardWrites(page);
    await page.goto(`/links?q=${host}`);
    const rows = page.locator(".link-list > li");
    await expect(rows).toHaveCount(4);                      // newest first: d, c, b, a
    await idle(page);
    const shown = [...urls].reverse();

    await rows.nth(0).getByRole("checkbox").click();
    await rows.nth(2).getByRole("checkbox").click({ modifiers: ["Shift"] });
    const bar = page.getByRole("toolbar", { name: "Selection" });
    await expect(bar.locator(".select-count")).toHaveText("3 selected");
    await expect(rows.nth(1).getByRole("checkbox")).toBeChecked();
    await expect(rows.nth(3).getByRole("checkbox")).not.toBeChecked();

    await bar.getByRole("button", { name: "Copy URLs" }).click();
    await expect(page.locator(".toast", { hasText: "Copied 3 links" })).toBeVisible();
    expect(await copied(page)).toEqual([shown.slice(0, 3).join("\n")]);

    await bar.getByRole("button", { name: "Assign 3 to…" }).click();
    const dialog = page.getByRole("dialog", { name: "Assign 3 links to…" });
    const picker = dialog.getByRole("combobox", { name: "Person" });
    await expect(picker).toBeFocused();
    await picker.fill(name);
    await picker.press("Enter");
    await expect(dialog).toBeHidden();
    await expect(page.locator(".toast", { hasText: `3 links assigned to ${name}` })).toBeVisible();
    await expect(bar).toHaveCount(0);
    await expect(rows.locator(".link-person")).toHaveCount(3);
    const { links } = await (await request.get(`/api/links?person=${pid}`, { headers: H })).json();
    expect(links.map(l => l.url)).toEqual(shown.slice(0, 3));   // in her order, as selected

    // The person's panel copies all of theirs, in its order.
    await page.goto(`/people/${pid}`);
    await page.locator(".person-links-section").getByRole("button", { name: "Copy URLs" }).click();
    await expect(page.locator(".toast", { hasText: "Copied 3 links" })).toBeVisible();
    expect((await copied(page)).at(-1)).toBe(shown.slice(0, 3).join("\n"));

    // Delete from the selection, through the confirm dialog.
    await page.goto(`/links?q=${host}`);
    await expect(rows).toHaveCount(4);
    await rows.nth(3).getByRole("checkbox").click();
    await bar.getByRole("button", { name: "Delete 1…" }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm).toContainText("Delete 1 link?");
    await confirm.getByRole("button", { name: "Delete 1 link" }).click();
    await expect(rows).toHaveCount(3);
    await expect(page.locator(".toast", { hasText: "1 link deleted." })).toBeVisible();
    expect(await linkByUrl(request, urls[0])).toBeNull();
    await idle(page);
  } finally {
    await cleanUp(request, host, pid);
  }
});

test("Links: a shift range holds after a row comes in; Undo gives each link back to whom it had", async ({ page, request }) => {
  const stamp = Date.now();
  const host = `e2e-undo-${stamp}.example`;
  const people = {};
  for (const who of ["P", "Q", "R"]) {
    people[who] = (await (await request.post("/api/people", { headers: H, data: { name: `E2E ${who} ${stamp}` } })).json()).person.id;
  }
  const url = x => `https://${host}/${x}`;
  try {
    await makeLinks(request, [url("a")]);
    await request.post("/api/links", { headers: H, data: { url: url("b"), title: `${host}/b`, person: people.Q } });
    await makeLinks(request, [url("c")]);
    await stubClipboard(page, null);
    await page.goto(`/links?q=${host}`);
    const rows = page.locator(".link-list > li");
    await expect(rows).toHaveCount(3);                      // newest first: c, b, a
    await idle(page);

    // The anchor is c; a quick-add then puts d on top. The shift range is still c to a.
    await rows.nth(0).getByRole("checkbox").click();
    await page.locator("#main-nav").getByRole("button", { name: "Add a link" }).click();
    const add = page.getByRole("dialog", { name: "Add a link" });
    await add.getByLabel("Address").fill(url("d"));
    await add.getByLabel("Address").press("Enter");
    await expect(add).toBeHidden();
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(1).getByRole("checkbox")).toBeChecked();   // c, moved down one
    await rows.nth(3).getByRole("checkbox").click({ modifiers: ["Shift"] });
    const bar = page.getByRole("toolbar", { name: "Selection" });
    await expect(bar.locator(".select-count")).toHaveText("3 selected");
    await expect(rows.nth(0).getByRole("checkbox")).not.toBeChecked();

    // c and a had no one, b had Q: all to P.
    await bar.getByRole("button", { name: "Assign 3 to…" }).click();
    const dialog = page.getByRole("dialog", { name: "Assign 3 links to…" });
    await dialog.getByRole("combobox", { name: "Person" }).fill(`E2E P ${stamp}`);
    await dialog.getByRole("combobox", { name: "Person" }).press("Enter");
    const toast = page.locator(".toast", { hasText: `3 links assigned to E2E P ${stamp}` });
    await expect(toast).toBeVisible();

    // a moves on to R meanwhile: Undo leaves it there, the others go back.
    await request.post(`/api/links/${(await linkByUrl(request, url("a"))).id}`, { headers: H, data: { person: people.R } });
    await toast.getByRole("button", { name: "Undo" }).click();
    await expect(page.locator(".toast", { hasText: "1 link changed since: left as it is." })).toBeVisible();
    expect((await linkByUrl(request, url("a"))).person?.id).toBe(people.R);
    expect((await linkByUrl(request, url("b"))).person?.id).toBe(people.Q);
    expect((await linkByUrl(request, url("c"))).person).toBeNull();
    await idle(page);
  } finally {
    await cleanUp(request, host, null);
    for (const pid of Object.values(people)) await request.delete(`/api/people/${pid}`, { headers: H });
  }
});

/* ── /links/add and the bookmarklet (#165 C) ── */

// /links/add with this query, loaded: the form alone, no app around it.
async function openAdd(page, query) {
  await page.goto(`/links/add?${new URLSearchParams(query)}`);
  await expect(page.getByRole("heading", { level: 2, name: "Add a link" })).toBeVisible();
  await expect(page.locator("#main-nav")).toHaveCount(0);
  await idle(page);
}

test("/links/add: prefilled from the query, Enter saves to a recent person; Add another", async ({ page, request }) => {
  const stamp = Date.now();
  const host = `e2e-add-page-${stamp}.example`;
  const url = `https://${host}/page?a=1&b=2#x`;
  const name = `E2E add page ${stamp}`;
  const pid = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person.id;
  try {
    await request.post("/api/links", { headers: H, data: { url: `https://${host}/older`, person: pid } });
    await openAdd(page, { url, title: "  A page\n\ttitle  " });
    await expect(page).toHaveTitle("Add a link · FeedVault");
    await expect(page.getByLabel("Address")).toHaveValue(url);
    await expect(page.getByLabel("Title")).toHaveValue("A page title");
    const picker = page.getByRole("combobox", { name: "Person" });
    await expect(picker).toBeFocused();
    const options = page.getByRole("option");
    await expect(options.first()).toHaveText("No person (Unsorted)");
    await expect(options.nth(1)).toHaveText(name);
    // Not a popup: Esc closes nothing, so the hint does not offer it.
    await expect(page.locator(".quick-add-hint")).not.toContainText("Esc");
    expect(await linkByUrl(request, url), "nothing saved before Save").toBeNull();

    await picker.press("ArrowDown");
    await picker.press("Enter");
    const done = page.getByRole("status");
    await expect(done).toHaveText(`Saved to ${name}.`);
    await expect(done.getByRole("link", { name })).toHaveAttribute("href", `/people/${pid}`);
    await expect(page.getByRole("link", { name: "All links" })).toHaveAttribute("href", "/links");
    const saved = await linkByUrl(request, url);
    expect(saved.person).toEqual({ id: pid, name });
    expect(saved.title).toBe("A page title");

    await page.getByRole("button", { name: "Add another" }).click();
    await expect(page.getByLabel("Address")).toHaveValue("");
    await expect(page.getByLabel("Address")).toBeFocused();
    await expect(page.getByLabel("Title")).toHaveValue("");
    // Cancel, on a page of its own, goes to Links.
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page).toHaveURL("/links");
    await expect(page.getByRole("heading", { level: 2, name: "Links", exact: true })).toBeVisible();
    await idle(page);
  } finally {
    await cleanUp(request, host, pid);
  }
});

test("/links/add: saved to no one; one saved already says whose it is", async ({ page, request, pageErrors }) => {
  const stamp = Date.now();
  const host = `e2e-add-untied-${stamp}.example`;
  const name = `E2E add taken ${stamp}`;
  const pid = (await (await request.post("/api/people", { headers: H, data: { name } })).json()).person.id;
  // The server's answer to the address saved already: a 409, which the test is about.
  pageErrors.allow(/^api: POST \S+\/api\/links → 409$/);
  pageErrors.allow(/^console: .*status of 409 .*\/api\/links\)$/);
  try {
    await openAdd(page, { url: `https://${host}/untied` });
    await expect(page.getByLabel("Title")).toHaveValue("");
    await page.getByRole("combobox", { name: "Person" }).press("Enter");
    const done = page.getByRole("status");
    await expect(done).toHaveText("Saved to Unsorted.");
    await expect(done.getByRole("link", { name: "Unsorted" })).toHaveAttribute("href", "/links?person=none");
    expect((await linkByUrl(request, `https://${host}/untied`)).person).toBeNull();

    await request.post("/api/links", { headers: H, data: { url: `https://${host}/taken`, person: pid } });
    await openAdd(page, { url: `https://${host}/taken`, title: "Again" });
    await page.getByRole("combobox", { name: "Person" }).press("Enter");
    const alert = page.locator(".quick-add-taken");
    await expect(alert).toHaveText(`Already saved, tied to ${name}.`);
    await expect(page.getByRole("status")).toHaveCount(0);
    await alert.getByRole("link", { name }).click();
    await expect(page).toHaveURL(`/people/${pid}`);
    await idle(page);
  } finally {
    await cleanUp(request, host, pid);
  }
});

test("/links/add: an address that is not one web address is not filled in", async ({ page }) => {
  for (const bad of ["javascript:alert(1)", "data:text/html,<b>x</b>", "https://user:pw@e2e.example/x",
    "e2e.example/no-scheme", "https://e2e.example/a b", "ftp://e2e.example/x"]) {
    await openAdd(page, { url: bad, title: "<b>Bold</b> & more" });
    await expect(page.getByLabel("Address"), bad).toHaveValue("");
    await expect(page.getByLabel("Address"), bad).toBeFocused();
    // The title is text: shown as typed, never as markup.
    await expect(page.getByLabel("Title")).toHaveValue("<b>Bold</b> & more");
    await expect(page.locator(".links-add-page b")).toHaveCount(0);
  }
  await openAdd(page, { url: "https://e2e.example/long", title: "x".repeat(400) });
  await expect(page.getByLabel("Title")).toHaveValue("x".repeat(300));
});

test("the bookmarklet opens /links/add in a popup that closes after the save; blocked, it opens in the tab", async ({ page, request }) => {
  const stamp = Date.now();
  await openPage(page, { name: "Links", path: "/links" });
  const mark = page.getByRole("link", { name: "Save to FeedVault" });
  const origin = new URL(page.url()).origin;
  await expect(mark).toHaveAttribute("href", /^javascript:/);
  const href = await mark.getAttribute("href");
  expect(decodeURIComponent(href.slice("javascript:".length))).toContain(JSON.stringify(`${origin}/links/add`));
  // A click here runs nothing: it says how it is used.
  await mark.click();
  await expect(page.locator(".toast", { hasText: "Drag it to your bookmarks bar" })).toBeVisible();
  expect(page.context().pages()).toHaveLength(1);

  // The bookmark clicked on a page of this instance (no other site is
  // reached): its script, as a browser runs it, on that page.
  const here = `${origin}/links?bookmarklet=${stamp}`;
  const run = h => page.evaluate(code => (0, eval)(code), decodeURIComponent(h.slice("javascript:".length)));
  try {
    await page.goto(here);
    await page.evaluate(() => { document.title = "Bookmarklet & 100% #test"; });
    const [popup] = await Promise.all([page.waitForEvent("popup"), run(href)]);
    await popup.waitForLoadState();
    const u = new URL(popup.url());
    expect([u.pathname, u.searchParams.get("popup"), u.searchParams.get("url"), u.searchParams.get("title")])
      .toEqual(["/links/add", "1", here, "Bookmarklet & 100% #test"]);
    await expect(popup.getByLabel("Address")).toHaveValue(here);
    await expect(popup.getByLabel("Title")).toHaveValue("Bookmarklet & 100% #test");
    await expect(popup.locator(".quick-add-hint")).toContainText("Esc close");
    const picker = popup.getByRole("combobox", { name: "Person" });
    await expect(picker).toBeFocused();
    const closed = popup.waitForEvent("close");
    await picker.press("Enter");
    await expect(popup.getByRole("status")).toHaveText("Saved to Unsorted. Closing…");
    await closed;
    const saved = await linkByUrl(request, here);
    expect(saved).toMatchObject({ title: "Bookmarklet & 100% #test", person: null });

    // No window (a popup blocker): the same page, in this tab, ends on links.
    await request.delete(`/api/links/${saved.id}`, { headers: H });
    await page.evaluate(() => { window.open = () => null; });
    await Promise.all([page.waitForURL(/\/links\/add\?/), run(href)]);
    expect(new URL(page.url()).searchParams.get("popup")).toBeNull();
    await expect(page.getByLabel("Address")).toHaveValue(here);
    await page.getByRole("combobox", { name: "Person" }).press("Enter");
    await expect(page.getByRole("status")).toHaveText("Saved to Unsorted.");
    await expect(page.getByRole("button", { name: "Add another" })).toBeVisible();
    await idle(page);
  } finally {
    const left = await linkByUrl(request, here);
    if (left) await request.delete(`/api/links/${left.id}`, { headers: H });
  }
});
