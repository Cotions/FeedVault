// Desktop, 1440x900: what is typed stays with what it was typed for. A
// page about one person or collection opens clean when the app goes there
// from another (#120), and typed edits are never dropped without asking
// (#125). People, collections and links are made for each test and removed
// after it.
import { test, expect, idle } from "./fixtures.js";

const H = { "X-FeedVault": "1" };

// To ``path`` as Forward does, without loading the page again: a history
// entry the router can count (its idx) and the popstate it listens to.
async function forwardTo(page, path) {
  await page.evaluate(to => {
    const idx = (window.history.state?.idx ?? 0) + 1;
    window.history.pushState({ usr: null, key: `e2e${idx}`, idx }, "", to);
    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
  }, path);
}

async function makePeople(request, names) {
  const ids = [];
  for (const name of names) {
    const r = await request.post("/api/people", { headers: H, data: { name } });
    ids.push((await r.json()).person.id);
  }
  return ids;
}

const notesOf = async (request, id) => (await (await request.get(`/api/people/${id}`, { headers: H })).json()).notes;

test("a person's page keeps nothing of the previous person (#120)", async ({ page, request }) => {
  const stamp = Date.now();
  const [one, two] = await makePeople(request, [`E2E one ${stamp}`, `E2E two ${stamp}`]);
  try {
    await page.goto(`/people/${one}`);
    await expect(page.locator("h2.page-title")).toHaveText(`E2E one ${stamp}`);
    await idle(page);

    // Rename open, nothing changed yet: nothing to lose, so the app goes on.
    await page.getByRole("button", { name: "Rename" }).click();
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toHaveValue(`E2E one ${stamp}`);
    await forwardTo(page, `/people/${two}`);
    await expect(page.locator("h2.page-title")).toHaveText(`E2E two ${stamp}`);
    await expect(page.getByRole("textbox", { name: "Name", exact: true }), "the rename stays with person one").toHaveCount(0);
    await expect(page.locator('textarea[aria-label="Notes"]')).toHaveValue("");
    await idle(page);

    // Notes typed for person two, then person one again: asked first, and
    // once discarded, person one shows their own (empty) notes.
    await page.locator('textarea[aria-label="Notes"]').fill("notes typed for person TWO");
    await page.goBack();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("Discard");
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL(new RegExp(`/people/${one}$`));
    await expect(page.locator("h2.page-title")).toHaveText(`E2E one ${stamp}`);
    await expect(page.locator('textarea[aria-label="Notes"]')).toHaveValue("");
    await expect(page.getByRole("button", { name: "Save notes" })).toHaveCount(0);
    await idle(page);
    expect(await notesOf(request, one)).toBe("");
    expect(await notesOf(request, two)).toBe("");
  } finally {
    for (const id of [one, two]) await request.delete(`/api/people/${id}`, { headers: H });
  }
});

test("a collection's page keeps nothing of the previous collection (#120)", async ({ page, request }) => {
  const stamp = Date.now();
  const ids = [];
  for (const name of [`E2E coll one ${stamp}`, `E2E coll two ${stamp}`]) {
    const r = await request.post("/api/collections", { headers: H, data: { name } });
    ids.push((await r.json()).collection.id);
  }
  try {
    await page.goto(`/collections/${ids[0]}`);
    await expect(page.locator("h2.page-title")).toHaveText(`E2E coll one ${stamp}`);
    await idle(page);
    await page.getByRole("button", { name: "Rename" }).click();
    await expect(page.getByRole("textbox", { name: "Collection name" })).toHaveValue(`E2E coll one ${stamp}`);
    await forwardTo(page, `/collections/${ids[1]}`);
    await expect(page.locator("h2.page-title")).toHaveText(`E2E coll two ${stamp}`);
    await expect(page.getByRole("textbox", { name: "Collection name" }), "the rename stays with collection one").toHaveCount(0);
    await idle(page);
  } finally {
    for (const id of ids) await request.post(`/api/collections/${id}/delete`, { headers: H });
  }
});

test("Links: opening Edit on another link asks before dropping the first one's edits (#125)", async ({ page, request }) => {
  const stamp = Date.now();
  const ids = [];
  for (const t of ["A", "B"]) {
    const r = await request.post("/api/links", { headers: H, data: { url: `https://e2e-${stamp}.example/${t}`, title: `Unsaved ${t} ${stamp}` } });
    ids.push((await r.json()).link.id);
  }
  try {
    await page.goto("/links");
    await page.getByRole("textbox", { name: "Search links" }).fill(`${stamp}`);
    await expect(page.locator(".link-list > li")).toHaveCount(2);
    await idle(page);
    const editing = page.locator(".link-row.is-editing");
    const dialog = page.getByRole("alertdialog");

    // Opened and left as it was: another Edit just moves on.
    await page.getByRole("button", { name: `Edit Unsaved A ${stamp}` }).click();
    await page.getByRole("button", { name: `Edit Unsaved B ${stamp}` }).click();
    await expect(dialog).toHaveCount(0);
    await expect(editing.getByLabel("Title")).toHaveValue(`Unsaved B ${stamp}`);
    await editing.getByRole("button", { name: "Cancel" }).click();

    // Changed: asked first. Keep editing keeps A open with the change.
    await page.getByRole("button", { name: `Edit Unsaved A ${stamp}` }).click();
    await editing.getByLabel("Title").fill(`Unsaved A ${stamp} edited`);
    await page.getByRole("button", { name: `Edit Unsaved B ${stamp}` }).click();
    await expect(dialog).toContainText("not saved");
    await expect(dialog.getByRole("button", { name: "Keep editing" })).toBeFocused();
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(editing).toHaveCount(1);
    await expect(editing.getByLabel("Title")).toHaveValue(`Unsaved A ${stamp} edited`);

    // Discard: B opens, A is as saved.
    await page.getByRole("button", { name: `Edit Unsaved B ${stamp}` }).click();
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(editing.getByLabel("Title")).toHaveValue(`Unsaved B ${stamp}`);
    await expect(page.locator(".link-row-title", { hasText: `Unsaved A ${stamp}` })).toHaveText(`Unsaved A ${stamp}`);

    // B changed, then away to another page: asked too; Keep editing stays.
    await editing.getByLabel("Notes").fill("a note not saved yet");
    await page.locator('#main-nav a.side-link[href="/tags"]').click();
    await expect(dialog).toContainText("not saved");
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await expect(editing.getByLabel("Notes")).toHaveValue("a note not saved yet");
    await idle(page);
  } finally {
    for (const id of ids) await request.delete(`/api/links/${id}`, { headers: H });
  }
});

test("a person's page asks before dropping unsaved notes (#125)", async ({ page, request }) => {
  const stamp = Date.now();
  const [pid] = await makePeople(request, [`E2E notes ${stamp}`]);
  try {
    await page.goto(`/people/${pid}`);
    await expect(page.locator("h2.page-title")).toHaveText(`E2E notes ${stamp}`);
    await idle(page);
    const notes = page.locator('textarea[aria-label="Notes"]');
    const dialog = page.getByRole("alertdialog");
    const linksNav = page.locator('#main-nav a.side-link[href="/links"]');

    await notes.fill("typed, not saved");
    await linksNav.click();
    await expect(dialog).toContainText(`Not saved for E2E notes ${stamp}: the notes.`);
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(new RegExp(`/people/${pid}$`));
    await expect(notes).toHaveValue("typed, not saved");

    // Closing the tab: the browser's own warning, while the notes are unsaved.
    const asked = page.waitForEvent("dialog");
    await page.close({ runBeforeUnload: true });
    const warning = await asked;
    expect(warning.type()).toBe("beforeunload");
    await warning.dismiss();
    await expect(notes).toHaveValue("typed, not saved");

    // Saved: nothing to ask, the sidebar goes straight on.
    await page.getByRole("button", { name: "Save notes" }).click();
    await expect(page.getByRole("button", { name: "Save notes" })).toHaveCount(0);
    expect(await notesOf(request, pid)).toBe("typed, not saved");
    await linksNav.click();
    await expect(page).toHaveURL(/\/links$/);
    await expect(dialog).toHaveCount(0);

    // Typed again, then Discard: the app goes on, the saved notes stay.
    await page.goBack();
    await expect(notes).toHaveValue("typed, not saved");
    await notes.fill("dropped");
    await linksNav.click();
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await idle(page);
    expect(await notesOf(request, pid)).toBe("typed, not saved");
  } finally {
    await request.delete(`/api/people/${pid}`, { headers: H });
  }
});
