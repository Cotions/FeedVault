// Desktop, 1440x900: Open links in (Settings), and a link row opening its
// link in another browser (POST /api/open) or here. No browser is ever
// started: fakeOpenLinks answers POST /api/open in the page, and the test
// instance refuses every launch besides (FEEDVAULT_NO_LAUNCH=1). No link
// is followed either: a tab opened here would be one to another origin,
// which fixtures.js aborts and fails the test on.
import { test, expect, idle, fakeOpenLinks, openLinkMenu } from "./fixtures.js";

const H = { "X-FeedVault": "1" };

async function setDefault(request, value) {
  const r = await request.post("/api/config", { headers: H, data: { open_links: value } });
  expect((await r.json()).ok).toBe(true);
}

async function addLink(request, title) {
  const url = `https://open-${Date.now()}-${Math.random().toString(36).slice(2, 7)}.example/x`;
  const r = await request.post("/api/links", { headers: H, data: { url, title } });
  return { ...(await r.json()).link, url };
}

async function deleteLink(request, id) {
  await request.post("/api/links/delete", { headers: H, data: { ids: [id] } });
}

// The row of a link on the Links page, found by searching for its title.
async function rowOf(page, link) {
  await page.goto(`/links?q=${encodeURIComponent(link.title)}`);
  const row = page.locator(`.link-row[data-link-id="${link.id}"]`);
  await expect(row).toBeVisible();
  await idle(page);
  return row;
}

test("Settings: Open links in saves a browser, a profile and a private window; This browser clears it", async ({ page, request }) => {
  await fakeOpenLinks(page);
  try {
    await page.goto("/settings#open-links");
    const card = page.locator("#open-links");
    await expect(card).toBeVisible();
    const browser = card.getByLabel("Browser", { exact: true });
    await expect(browser).toHaveValue("");
    await expect(browser.locator("option")).toHaveText(["This browser (a new tab here)", "Firefox", "Chromium (Flatpak)"]);
    await expect(card.getByLabel("Profile", { exact: true })).toHaveCount(0);

    await browser.selectOption("firefox");
    await expect(card.getByLabel("Profile", { exact: true })).toBeVisible();
    await expect(card.getByLabel("Profile", { exact: true }).locator("option")).toHaveText(["The browser picks", "default", "work"]);
    await card.getByLabel("Profile", { exact: true }).selectOption("work");
    await expect(card.getByLabel("Profile", { exact: true })).toHaveValue("work");
    await card.getByLabel("Private window").check();
    await expect(card.getByLabel("Private window")).toBeChecked();
    await idle(page);
    let saved = await (await request.get("/api/config", { headers: H })).json();
    expect(saved.open_links).toEqual({ browser: "firefox", profile: "work", private: true });

    await page.reload();
    await expect(card.getByLabel("Browser", { exact: true })).toHaveValue("firefox");
    await expect(card.getByLabel("Profile", { exact: true })).toHaveValue("work");
    await expect(card.getByLabel("Private window")).toBeChecked();

    await card.getByLabel("Browser", { exact: true }).selectOption("");
    await expect(card.getByLabel("Profile", { exact: true })).toHaveCount(0);
    await idle(page);
    saved = await (await request.get("/api/config", { headers: H })).json();
    expect(saved.open_links).toBeNull();
  } finally {
    await setDefault(request, null);
  }
});

test("a default set: a click on a link asks FeedVault to open it, and no tab opens here", async ({ page, request }) => {
  const sent = await fakeOpenLinks(page);
  const link = await addLink(request, "Opens elsewhere");
  await setDefault(request, { browser: "firefox", profile: "work", private: false });
  try {
    const row = await rowOf(page, link);
    const title = row.locator("a.link-row-title");
    await expect(title).toHaveAttribute("href", link.url);
    await expect(title).toHaveAttribute("title", /Opens in Firefox · work/);
    let tabs = 0;
    page.context().on("page", () => { tabs += 1; });
    await title.click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]).toEqual({ url: link.url });             // the saved default's browser, the backend's to read
    await title.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => sent.length).toBe(2);
    await page.waitForTimeout(200);
    expect(tabs).toBe(0);
    expect(page.url()).toContain("/links");
  } finally {
    await setDefault(request, null);
    await deleteLink(request, link.id);
  }
});

test("a refused open says why in a toast, with Open here", async ({ page, request, pageErrors }) => {
  // The answer it fakes: a 400, which the browser logs too.
  pageErrors.allow(/\/api\/open\b.*400|400.*\/api\/open\b/);
  const error = "Firefox was not found: none of firefox is on PATH, nor is the Flatpak org.mozilla.firefox installed";
  const sent = await fakeOpenLinks(page, { answer: { ok: false, error } });
  const link = await addLink(request, "Refused open");
  await setDefault(request, { browser: "firefox" });
  try {
    const row = await rowOf(page, link);
    await row.locator("a.link-row-title").click();
    const toast = page.locator(".toast", { hasText: error });
    await expect(toast).toBeVisible();
    await expect(toast.getByRole("button", { name: "Open here" })).toBeVisible();
    expect(sent).toHaveLength(1);
  } finally {
    await setDefault(request, null);
    await deleteLink(request, link.id);
  }
});

// The row's own actions, as its ⋯ menu lists them after the ways to open it (an untied link, in All links).
const ROW_ACTS = ["Edit", "Assign…", "Move to Downloads", "Delete"];

test("no default: a click is a plain link; the row menu's Open in opens it once elsewhere", async ({ page, request }) => {
  const sent = await fakeOpenLinks(page);
  const link = await addLink(request, "Menu open");
  try {
    const row = await rowOf(page, link);
    const menu = await openLinkMenu(page, row);
    await expect(menu.getByRole("menuitem")).toHaveText([
      "Open",
      "Firefox · default", "Firefox · default · private", "Firefox · work", "Firefox · work · private",
      "Chromium", "Chromium · private",
      "Set the default…",
      ...ROW_ACTS,
    ]);
    await expect(menu.locator(".action-menu-head")).toHaveText("Open in");
    // Open: a plain link to a new tab here (not clicked: it would leave the instance).
    const here = menu.getByRole("menuitem", { name: "Open", exact: true });
    await expect(here).toHaveAttribute("href", link.url);
    await expect(here).toHaveAttribute("target", "_blank");
    await expect(menu.getByRole("menuitem", { name: "Set the default…" })).toHaveAttribute("href", "/settings#open-links");

    await menu.getByRole("menuitem", { name: "Firefox · work · private" }).click();
    await expect(menu).toBeHidden();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]).toEqual({ url: link.url, browser: "firefox", profile: "work", private: true });

    await (await openLinkMenu(page, row)).getByRole("menuitem", { name: "Chromium · private" }).click();
    await expect.poll(() => sent.length).toBe(2);
    expect(sent[1]).toEqual({ url: link.url, browser: "chromium", profile: null, private: true });
    await idle(page);
  } finally {
    await deleteLink(request, link.id);
  }
});

test("a default set: the row menu marks it among the Open in choices", async ({ page, request }) => {
  await fakeOpenLinks(page);
  const link = await addLink(request, "Menu default");
  await setDefault(request, { browser: "firefox", profile: "work", private: false });
  try {
    const row = await rowOf(page, link);
    const menu = await openLinkMenu(page, row);
    await expect(menu.getByRole("menuitem", { name: /^Firefox · work/ })).toHaveText(["Firefox · work (default)", "Firefox · work · private"]);
    await expect(menu.getByRole("menuitem", { name: /\(default\)/ })).toHaveCount(1);
    await page.keyboard.press("Escape");
    await idle(page);
  } finally {
    await setDefault(request, null);
    await deleteLink(request, link.id);
  }
});

test("with no other browser installed, a row's menu has no Open in", async ({ page, request }) => {
  // The instance's own answer: its PATH is empty, so it finds none.
  await page.route("**/api/open", route => route.abort());
  const link = await addLink(request, "No menu");
  try {
    const row = await rowOf(page, link);
    const menu = await openLinkMenu(page, row);
    await expect(menu.getByRole("menuitem")).toHaveText(["Open", ...ROW_ACTS]);
    await expect(menu.locator(".action-menu-head")).toHaveCount(0);
    await page.keyboard.press("Escape");
  } finally {
    await deleteLink(request, link.id);
  }
});
