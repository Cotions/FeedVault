// Desktop, 1440x900: the header's search box searches the page it is on
// (lib/search.js). On Creators it stays there and lists the creators,
// accounts and links that match, in that order; on a person's page it
// narrows their accounts, sources and links; elsewhere it opens the Feed.
import { test, expect, openPage, idle } from "./fixtures.js";

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
    const row = section("Links").locator(".link-row", { hasText: made.link.url });
    await expect(row.locator("a.link-row-title")).toHaveAttribute("href", made.link.url);
    await expect(row.locator("a.link-person")).toHaveAttribute("href", `/people/${made.person.id}`);
    await expect(row.getByRole("button")).toHaveCount(0);
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

test("a person's page: the header search narrows their accounts, sources and links", async ({ page, request }) => {
  const stamp = Date.now();
  const made = await setUp(request, stamp);
  const a = made.account;
  await request.post(`/api/people/${made.person.id}/accounts`, { headers: H, data: { add: [{ platform: a.platform, id: a.id }] } });
  try {
    await page.goto(`/people/${made.person.id}`);
    const name = made.person.name;
    await expect(page.getByRole("heading", { level: 2, name })).toBeVisible();
    const box = page.locator("input.header-search");
    await expect(box).toHaveAttribute("placeholder", `Search ${name}'s accounts, links…`);
    await expect(box).toHaveAccessibleName(`Search ${name}'s accounts, sources and links`);

    // Notes typed and not saved stay through a search.
    const notes = page.getByRole("textbox", { name: "Notes" });
    await notes.fill("typed, not saved");

    // Their link only: the accounts and notes step aside.
    await box.fill(`e2e-search-${stamp}`);
    await expect(page).toHaveURL(`/people/${made.person.id}?q=e2e-search-${stamp}`);
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    const links = page.locator(".person-links-section");
    await expect(links.locator(".link-row")).toHaveCount(1);
    await expect(links.getByRole("button", { name: /Move/ })).toHaveCount(0);
    await expect(page.locator(".person-accounts")).toBeHidden();
    await expect(notes).toBeHidden();
    await expect(page.locator(".person-search")).toContainText(/\d+ posts? of theirs/);

    // Their account, by its handle: a word every one of these has.
    await box.fill(a.handle);
    await expect(page.locator(".person-accounts .person-account")).toHaveCount(1);
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
