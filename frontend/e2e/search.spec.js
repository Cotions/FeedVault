// Desktop, 1440x900: the header's search box searches the page it is on
// (lib/search.js). On Creators it stays there and lists the creators,
// accounts and links that match, in that order; on a person's page it
// narrows their accounts, sources and links; elsewhere it opens the Feed.
import { test, expect, openPage, idle, openLinkMenu } from "./fixtures.js";

const H = { "X-FeedVault": "1" };

// An account of the demo linked to no one, and a person and a link of the
// test's own around its handle: one search finds all three.
async function setUp(request, stamp) {
  const authors = await (await request.get("/api/authors", { headers: H })).json();
  const a = authors.find(x => !x.person && x.id != null && x.handle && x.handle.length >= 4);
  expect(a, "the demo has an account linked to no one").toBeTruthy();
  const made = await (await request.post("/api/people", {
    headers: H, data: { name: `Search ${a.handle} ${stamp}` },
  })).json();
  expect(made.ok).toBe(true);
  const link = await (await request.post("/api/links", {
    headers: H, data: { url: `https://e2e-search-${stamp}.example/${a.handle}`, title: `Search ${a.handle} ${stamp}`, person: made.person.id },
  })).json();
  expect(link.ok).toBe(true);
  return { account: a, person: made.person, link: link.link };
}

async function cleanUp(request, { person, link }) {
  await request.delete(`/api/links/${link.id}`, { headers: H });
  await request.delete(`/api/people/${person.id}`, { headers: H });
}

test("Creators: the header search stays on the page, creators, accounts and links in that order", async ({ page, request }) => {
  const stamp = Date.now();
  const made = await setUp(request, stamp);
  try {
    await openPage(page, { name: "Creators", path: "/creators" });
    const box = page.locator("input.header-search");
    await expect(box).toHaveAttribute("placeholder", "Search creators, accounts, links…");
    await expect(box).toHaveAccessibleName("Search creators, accounts and links");

    // "/" jumps to it, as on every page.
    await page.keyboard.press("/");
    await expect(box).toBeFocused();
    await page.keyboard.type(made.account.handle);
    await expect(page).toHaveURL(`/creators?q=${encodeURIComponent(made.account.handle)}`);
    const results = page.locator(".creator-search");
    await expect(results).toBeVisible();
    const heads = results.locator(".creator-search-section > h3");
    await expect(heads).toHaveCount(3);
    await expect(heads.nth(0)).toHaveText(/^Creators/);
    await expect(heads.nth(1)).toHaveText(/^Accounts/);
    await expect(heads.nth(2)).toHaveText(/^Links/);
    const section = name => results.getByRole("region", { name, exact: true });
    await expect(section("Creators").getByRole("link", { name: new RegExp(`Search ${made.account.handle} ${stamp}`) })).toBeVisible();
    await expect(section("Accounts").getByRole("link", { name: new RegExp(`@${made.account.handle}`) }).first()).toBeVisible();
    // The link opens its site; its person is one click away.
    const row = section("Links").locator(".link-row", { has: page.locator(`a[href="${made.link.url}"]`) });
    await expect(row.locator("a.link-row-title")).toHaveAttribute("href", made.link.url);
    await expect(row.locator("a.link-person")).toHaveAttribute("href", `/people/${made.person.id}`);
    // Read only: its Copy, and no menu (no other browser to open it in here).
    await expect(row.getByRole("button")).toHaveCount(1);
    await expect(row.getByRole("button", { name: /^Copy the address of / })).toBeVisible();
    // Not the page's own parts while it searches.
    await expect(page.getByRole("region", { name: "Sources" })).toBeHidden();
    await expect(page.locator(".creator-filters")).toHaveCount(0);

    // A reload keeps it; one word only the person and link have narrows it.
    await page.reload();
    await expect(box).toHaveValue(made.account.handle);
    await box.fill(`${made.account.handle} ${stamp}`);
    await expect(page).toHaveURL(`/creators?${new URLSearchParams({ q: `${made.account.handle} ${stamp}` })}`);
    await expect(heads).toHaveCount(2);
    await expect(heads.nth(0)).toHaveText(/^Creators/);
    await expect(heads.nth(1)).toHaveText(/^Links/);

    // Nothing at all: one line says so.
    await box.fill(`zz-nothing-${stamp}`);
    await expect(page.locator(".creator-search-empty")).toContainText("Nothing matches");

    // A creator clicked through, then Back: the search is there again.
    await box.fill(`${made.account.handle} ${stamp}`);
    await section("Creators").getByRole("link").first().click();
    await expect(page).toHaveURL(`/people/${made.person.id}`);
    await expect(box).toHaveValue("");
    await page.goBack();
    await expect(box).toHaveValue(`${made.account.handle} ${stamp}`);
    await expect(heads).toHaveCount(2);

    // Esc drops the search: the page as it was.
    await box.focus();
    await page.keyboard.press("Escape");
    await expect(box).toHaveValue("");
    await expect(page).toHaveURL("/creators");
    await expect(results).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Sources" })).toBeVisible();
    await idle(page);
  } finally {
    await cleanUp(request, made);
  }
});

test("a person's page: the header search narrows their profiles and links", async ({ page, request }) => {
  const stamp = Date.now();
  const made = await setUp(request, stamp);
  const a = made.account;
  await request.post(`/api/people/${made.person.id}/accounts`, { headers: H, data: { add: [{ platform: a.platform, id: a.id }] } });
  // Two downloads of theirs, one downloaded, and one the search will not match.
  const extra = [];
  for (const [path, more] of [["set-one", { download: true }], ["set-two", { downloaded: true }], ["other", { download: true }]]) {
    const host = path === "other" ? `e2e-other-${stamp}.example` : `e2e-search-${stamp}.example`;
    const r = await (await request.post("/api/links", { headers: H, data: { url: `https://${host}/${path}`, person: made.person.id, download: true } })).json();
    if (more.downloaded) await request.post(`/api/links/${r.link.id}`, { headers: H, data: more });
    extra.push(r.link.id);
  }
  try {
    await page.goto(`/people/${made.person.id}`);
    const name = made.person.name;
    await expect(page.getByRole("heading", { level: 2, name })).toBeVisible();
    const box = page.locator("input.header-search");
    await expect(box).toHaveAttribute("placeholder", `Search ${name}'s profiles, links…`);
    await expect(box).toHaveAccessibleName(`Search ${name}'s profiles and links`);

    // Notes typed and not saved stay through a search.
    const notes = page.getByRole("textbox", { name: "Notes" });
    await notes.fill("typed, not saved");

    // Their link only: the profiles and notes step aside.
    await box.fill(`e2e-search-${stamp}`);
    await expect(page).toHaveURL(`/people/${made.person.id}?q=e2e-search-${stamp}`);
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    const links = page.locator(".person-links-section");
    await expect(links.locator(".link-row")).toHaveCount(1);
    // Not moved while searched: their places are among all of them.
    const menu = await openLinkMenu(page, links.locator(".link-row"));
    await expect(menu.getByRole("menuitem", { name: "Edit" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: /^Move (up|down)$/ })).toHaveCount(0);
    await menu.getByRole("menuitem", { name: "Edit" }).click();
    await links.locator(".link-row.is-editing").getByRole("button", { name: "Cancel" }).click();
    // Their Downloads too: the ones that match, to do and downloaded together, with no switch.
    const downloads = page.locator(".person-downloads-section");
    await expect(downloads.locator(".link-row")).toHaveCount(2);
    await expect(downloads.locator(".link-row.is-downloaded")).toHaveCount(1);
    await expect(downloads.getByRole("group", { name: "Show downloads" })).toHaveCount(0);
    const profiles = page.getByRole("region", { name: "Profiles", exact: true });
    await expect(profiles).toBeHidden();
    await expect(notes).toBeHidden();
    await expect(page.locator(".person-search")).toContainText(/\d+ posts? of theirs/);

    // Their profile, by its handle: a word every one of these has. The
    // ways to add one step aside while it searches.
    await box.fill(a.handle);
    await expect(profiles.locator(".person-profile")).toHaveCount(1);
    await expect(profiles.locator(".page-count")).toHaveText("1 of 1");
    await expect(profiles.locator(".person-profile-add")).toBeHidden();
    await expect(page.locator(".person-search").getByRole("link", { name: "Show in the Feed" })).toHaveAttribute(
      "href", `/?person=${made.person.id}&q=${encodeURIComponent(a.handle)}`);

    // Esc: the whole page again, the notes as typed.
    await box.focus();
    await page.keyboard.press("Escape");
    await expect(page).toHaveURL(`/people/${made.person.id}`);
    await expect(notes).toHaveValue("typed, not saved");
    await expect(page.locator(".person-search")).toHaveCount(0);
    await notes.fill("");
    await idle(page);
  } finally {
    for (const id of extra) await request.delete(`/api/links/${id}`, { headers: H });
    await cleanUp(request, made);
  }
});

test("elsewhere the header search still opens the Feed", async ({ page }) => {
  await openPage(page, { name: "Tags", path: "/tags" });
  const box = page.getByRole("textbox", { name: "Search posts" });
  await box.fill("a");
  await expect(page).toHaveURL("/?q=a");
  await idle(page);
});
