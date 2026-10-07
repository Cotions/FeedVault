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
    await expect(page).toHaveURL(new RegExp(`/links\\?q=${stamp}$`));
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

// #132: a tag's new name, and the add forms (a new link on the Links page
// and on a person's page, a new source on a person's page and on Creators).
// Each asks only once something is typed, and never after it went through.
const nav = (page, path) => page.locator(`#main-nav a.side-link[href="${path}"]`).click();
// On the page ``title`` and settled: the page left is gone, its question
// with it, before Back.
async function arrived(page, title) {
  await expect(page.locator("h2.page-title")).toHaveText(title);
  await idle(page);
}

async function removeLinks(request, stamp) {
  const { links } = await (await request.get(`/api/links?q=${stamp}`, { headers: H })).json();
  for (const l of links) await request.delete(`/api/links/${l.id}`, { headers: H });
}

async function removeSources(request, stamp) {
  const { sources } = await (await request.get("/api/sources", { headers: H })).json();
  for (const s of sources.filter(x => x.target.includes(`${stamp}`))) await request.delete(`/api/sources/${s.id}`, { headers: H });
}

test("Tags: another rename or leaving asks before dropping a typed name (#132)", async ({ page, request }) => {
  const stamp = Date.now();
  const [a, b] = [`e2e-unsaved-a-${stamp}`, `e2e-unsaved-b-${stamp}`];
  const { posts } = await (await request.get("/api/posts?limit=1", { headers: H })).json();
  await request.post("/api/tags/apply", { headers: H, data: { posts: [posts[0].id], add: [a, b] } });
  try {
    await page.goto("/tags");
    const filterBox = page.getByRole("textbox", { name: "Filter tags" });
    const narrow = async () => { if (await filterBox.count()) await filterBox.fill(`${stamp}`); };
    const row = name => page.locator(".tag-row", { hasText: name });
    const field = name => page.getByRole("textbox", { name: `New name for ${name}` });
    const dialog = page.getByRole("alertdialog");
    await expect(row(a)).toHaveCount(1);
    await narrow();
    await idle(page);

    // Opened and left as it was: another Rename, or leaving, just goes on.
    await row(a).getByRole("button", { name: "Rename" }).click();
    await row(b).getByRole("button", { name: "Rename" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(field(b)).toHaveValue(b);
    await nav(page, "/links");
    await expect(page).toHaveURL(/\/links$/);
    await expect(dialog).toHaveCount(0);
    await page.goBack();
    await expect(row(a)).toHaveCount(1);
    await narrow();

    // Typed: another Rename asks. Keep editing keeps the typed name.
    await row(a).getByRole("button", { name: "Rename" }).click();
    await field(a).fill(`${a}-typed`);
    await row(b).getByRole("button", { name: "Rename" }).click();
    await expect(dialog).toContainText(`The new name for “${a}” is not saved.`);
    await expect(dialog.getByRole("button", { name: "Keep editing" })).toBeFocused();
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(field(a)).toHaveValue(`${a}-typed`);
    await expect(field(b)).toHaveCount(0);

    // Discard: B's rename opens, A keeps its name.
    await row(b).getByRole("button", { name: "Rename" }).click();
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(field(b)).toHaveValue(b);
    await expect(field(a)).toHaveCount(0);
    await expect(row(a).locator(".tag-row-name")).toHaveText(a);

    // Typed, then away: asked too. Keep editing stays; Discard goes.
    await field(b).fill(`${b}-typed`);
    await nav(page, "/links");
    await expect(dialog).toContainText(`The new name for “${b}” is not saved.`);
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(/\/tags$/);
    await expect(field(b)).toHaveValue(`${b}-typed`);
    await nav(page, "/links");
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await arrived(page, "Links");
    await page.goBack();
    await expect(row(a)).toHaveCount(1);
    await narrow();

    // Renamed: nothing left to ask about.
    await row(a).getByRole("button", { name: "Rename" }).click();
    await field(a).fill(`${a}-renamed`);
    await field(a).press("Enter");
    await expect(page.locator(".tag-row-name", { hasText: `${a}-renamed` })).toHaveCount(1);
    await nav(page, "/links");
    await expect(page).toHaveURL(/\/links$/);
    await expect(dialog).toHaveCount(0);
    await idle(page);
  } finally {
    for (const name of [a, b, `${a}-renamed`]) await request.post("/api/tags/delete", { headers: H, data: { name } });
  }
});

test("Links: a half-filled new link is asked about before it goes (#132)", async ({ page, request }) => {
  const stamp = Date.now();
  try {
    await page.goto("/links");
    const url = page.locator("#links-add-url");
    const dialog = page.getByRole("alertdialog");
    await expect(url).toHaveValue("");
    await idle(page);

    // Untouched: the app goes on.
    await nav(page, "/tags");
    await expect(page).toHaveURL(/\/tags$/);
    await expect(dialog).toHaveCount(0);
    await page.goBack();

    // Typed: leaving asks; Keep editing keeps it. A filter leaves the form be.
    await url.fill(`https://e2e-${stamp}.example/new`);
    await nav(page, "/tags");
    await expect(dialog).toContainText("The new link is not saved.");
    await expect(dialog.getByRole("button", { name: "Keep editing" })).toBeFocused();
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await expect(url).toHaveValue(`https://e2e-${stamp}.example/new`);
    await page.locator(".links-filters select").first().selectOption("social");
    await expect(page).toHaveURL(/\/links\?kind=social$/);
    await expect(dialog).toHaveCount(0);
    await expect(url).toHaveValue(`https://e2e-${stamp}.example/new`);

    // Discard: the app goes on, and the form is empty on the way back.
    await nav(page, "/tags");
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL(/\/tags$/);
    await arrived(page, "Tags");
    await page.goBack();
    await expect(url).toHaveValue("");

    // Added: nothing left to ask about.
    await url.fill(`https://e2e-${stamp}.example/added`);
    await page.locator(".links-add").getByRole("button", { name: "Add link" }).click();
    await expect(url).toHaveValue("");
    await nav(page, "/tags");
    await expect(page).toHaveURL(/\/tags$/);
    await expect(dialog).toHaveCount(0);
    await idle(page);
  } finally {
    await removeLinks(request, stamp);
  }
});

test("a person's page asks before dropping a half-filled new link or source (#132)", async ({ page, request }) => {
  const stamp = Date.now();
  const [pid] = await makePeople(request, [`E2E adds ${stamp}`]);
  try {
    await page.goto(`/people/${pid}`);
    await expect(page.locator("h2.page-title")).toHaveText(`E2E adds ${stamp}`);
    const url = page.locator("#person-link-add-url");
    const source = page.getByRole("textbox", { name: "Profile link, or an Instagram name" });
    const dialog = page.getByRole("alertdialog");
    const back = async () => { await page.goBack(); await expect(page.locator("h2.page-title")).toHaveText(`E2E adds ${stamp}`); };
    await idle(page);

    // Untouched: the app goes on.
    await nav(page, "/links");
    await expect(page).toHaveURL(/\/links$/);
    await expect(dialog).toHaveCount(0);
    await back();

    // A new link typed: asked; Keep editing keeps it, Discard drops it.
    await url.fill(`https://e2e-${stamp}.example/person`);
    await nav(page, "/links");
    await expect(dialog).toContainText(`Not saved for E2E adds ${stamp}: the new link.`);
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(new RegExp(`/people/${pid}$`));
    await expect(url).toHaveValue(`https://e2e-${stamp}.example/person`);
    await nav(page, "/links");
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await arrived(page, "Links");
    await back();
    await expect(url).toHaveValue("");

    // A new source typed: the same.
    await source.fill(`https://x.com/e2e_person_${stamp}`);
    await nav(page, "/links");
    await expect(dialog).toContainText(`Not saved for E2E adds ${stamp}: the new source.`);
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(new RegExp(`/people/${pid}$`));
    await expect(source).toHaveValue(`https://x.com/e2e_person_${stamp}`);
    await nav(page, "/links");
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await arrived(page, "Links");
    await back();
    await expect(source).toHaveValue("");

    // Both added: nothing left to ask about.
    await url.fill(`https://e2e-${stamp}.example/person-added`);
    await page.locator(".person-link-add").getByRole("button", { name: "Add link" }).click();
    await expect(url).toHaveValue("");
    await expect(page.locator(".person-links-section .link-row")).toHaveCount(1);
    await source.fill(`https://x.com/e2e_person_${stamp}`);
    const add = page.getByRole("button", { name: "Add source" });
    await expect(add).toBeEnabled();
    await add.click();
    await expect(source).toHaveValue("");
    await nav(page, "/links");
    await expect(page).toHaveURL(/\/links$/);
    await expect(dialog).toHaveCount(0);
    await idle(page);
  } finally {
    await removeSources(request, stamp);
    await removeLinks(request, stamp);
    await request.delete(`/api/people/${pid}`, { headers: H });
  }
});

test("Creators: a half-typed new source is asked about before it goes (#132)", async ({ page, request }) => {
  const stamp = Date.now();
  try {
    await page.goto("/creators");
    const source = page.getByRole("textbox", { name: "Profile link, or an Instagram name" });
    const dialog = page.getByRole("alertdialog");
    await expect(source).toHaveValue("");
    await idle(page);

    // Untouched: the app goes on.
    await nav(page, "/links");
    await expect(page).toHaveURL(/\/links$/);
    await expect(dialog).toHaveCount(0);
    await page.goBack();

    // Typed: leaving asks; Keep editing keeps it, through a filter too.
    await source.fill(`https://x.com/e2e_creators_${stamp}`);
    await nav(page, "/links");
    await expect(dialog).toContainText("The new source is not added yet.");
    await expect(dialog.getByRole("button", { name: "Keep editing" })).toBeFocused();
    await dialog.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(/\/creators$/);
    await expect(source).toHaveValue(`https://x.com/e2e_creators_${stamp}`);
    const filter = page.getByRole("textbox", { name: "Filter creators" });
    await filter.fill(`nothing ${stamp}`);
    await expect(source).toBeHidden();
    await filter.fill("");
    await expect(source).toHaveValue(`https://x.com/e2e_creators_${stamp}`);

    // Discard: the app goes on, and the field is empty on the way back.
    await nav(page, "/links");
    await dialog.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL(/\/links$/);
    await arrived(page, "Links");
    await page.goBack();
    await expect(source).toHaveValue("");

    // Added: nothing left to ask about.
    await source.fill(`https://x.com/e2e_creators_${stamp}`);
    const add = page.getByRole("button", { name: "Add source" });
    await expect(add).toBeEnabled();
    await add.click();
    await expect(source).toHaveValue("");
    await nav(page, "/links");
    await expect(page).toHaveURL(/\/links$/);
    await expect(dialog).toHaveCount(0);
    await idle(page);
  } finally {
    await removeSources(request, stamp);
  }
});

// QA pass 3: Settings' forms (media roots, a tool's path, the sync and
// cookie settings, link routing) dropped their edits when the app went to
// another page. Leaving now asks; another tab of Settings keeps them
// without asking. Nothing is saved: the instance's config stays as it was.
test("Settings: leaving asks before dropping unsaved edits; its tabs keep them", async ({ page, request }) => {
  const before = await (await request.get("/api/config", { headers: H })).json();
  await page.goto("/settings#library");
  const dialog = page.getByRole("alertdialog");
  const root = page.getByRole("textbox", { name: "Folder path to add" });
  await expect(root).toBeVisible();
  await idle(page);

  // Untouched: the app goes on.
  await nav(page, "/links");
  await arrived(page, "Links");
  await expect(dialog).toHaveCount(0);
  await page.goBack();

  // A folder typed, not added: leaving asks, Keep editing keeps it.
  await root.fill("/tmp/feedvault-e2e-not-a-real-root");
  await nav(page, "/links");
  await expect(dialog).toContainText("Not saved: Media roots.");
  await expect(dialog.getByRole("button", { name: "Keep editing" })).toBeFocused();
  await dialog.getByRole("button", { name: "Keep editing" }).click();
  await expect(page).toHaveURL(/\/settings#library$/);
  await expect(root).toHaveValue("/tmp/feedvault-e2e-not-a-real-root");

  // Another tab is the same page: no question, and both edits stay.
  await page.locator(".settings-tabs").getByRole("link", { name: "Sync" }).click();
  await expect(page).toHaveURL(/\/settings#sync$/);
  await expect(dialog).toHaveCount(0);
  const pause = page.getByRole("textbox", { name: "Pause in seconds", exact: true });
  await pause.fill("61");
  await nav(page, "/links");
  await expect(dialog).toContainText("Not saved: Media roots, Instagram sync.");

  // Discard: the app goes on; nothing was saved.
  await dialog.getByRole("button", { name: "Discard" }).click();
  await arrived(page, "Links");
  const after = await (await request.get("/api/config", { headers: H })).json();
  expect(after.media_roots).toEqual(before.media_roots);
  expect(after.instaloader).toEqual(before.instaloader);

  // Put back by hand: nothing left to ask about.
  await page.goto("/settings#sync");
  await pause.fill("61");
  await pause.fill(String(before.instaloader?.pause ?? 60));
  await nav(page, "/links");
  await arrived(page, "Links");
  await expect(dialog).toHaveCount(0);
});
