// Desktop: a person's Profiles (#235, components/PersonProfiles.jsx), the
// one list where their page had Accounts and Sources. A person is made of
// the demo's accounts: one with a source (and a new name to accept), one
// whose last sync failed, one given two sources, one with none; and a
// source of their own that has no account yet. Checked: one row per
// profile, with its preset, state, Copy, Sync and one ⋯ menu; the details
// folded until asked for; a failure said in a few words; Copy; Unlink,
// which takes the account and its source; "More ways to add", folded, with
// how many are suggested; the head's ⋯ menu, which holds Review … Trash.
// The person and the sources are made for each test and deleted after;
// nothing is synced.
import { test, expect, idle, menuItem, openMenu, openMoreWays, checkLayout, dismissToasts, formatFindings, settle } from "./fixtures.js";

const H = { "X-FeedVault": "1" };
const ONE = { platform: "instagram", id: "9004", handle: "quiet_kiln" };       // one source; its tool reported a new name
const FAILED = { platform: "instagram", id: "9001", handle: "mossy.trails" };  // one source; rate limited
const TWO = { platform: "twitter", id: "4100001", handle: "tram_spotter" };    // none in the demo: two are added
const BARE = { platform: "tiktok", id: "6900000000000000002", handle: "juni.vale" };  // no source; the demo suggests more of them
const key = a => `${a.platform}:${a.id}`;
const SIZES = [{ label: "1280x800", width: 1280, height: 800 }, { label: "1440x900", width: 1440, height: 900 }];

const sourcesNow = async request => (await (await request.get("/api/sources", { headers: H })).json()).sources;
const sourceOf = (sources, a) => sources.filter(s => s.account?.platform === a.platform && s.account.id === a.id);

/* The person, on their page: ``fn({ person, one, failed, two, loose })``
   (the sources by profile), then everything made is deleted, so the
   accounts are linked to nobody again. */
async function withPerson(page, request, fn) {
  const stamp = Date.now();
  const made = [];
  const add = async data => {
    const r = await (await request.post("/api/sources", { headers: H, data })).json();
    expect(r.ok, r.error).toBe(true);
    made.push(r.source.id);
    return r.source;
  };
  const created = await (await request.post("/api/people", {
    headers: H, data: { name: `E2E profiles ${stamp}`, accounts: [ONE, FAILED, TWO, BARE].map(({ platform, id }) => ({ platform, id })) },
  })).json();
  expect(created.ok, created.error).toBe(true);
  const person = created.person;
  try {
    const account = { platform: TWO.platform, id: TWO.id };
    // The second in a folder of its own: two sources never share one.
    const first = await add({ target: `https://x.com/${TWO.handle}`, account, person: person.id });
    const two = [first, await add({ target: `https://x.com/${TWO.handle}/media`, account, person: person.id, folder: `${first.folder}_media` })];
    const loose = await add({ target: `https://x.com/e2e_profiles_${stamp}`, person: person.id });
    const sources = await sourcesNow(request);
    const [one, ...more] = sourceOf(sources, ONE);
    const [failed] = sourceOf(sources, FAILED);
    expect(one && !more.length, "the demo's quiet_kiln has one source").toBe(true);
    expect(failed?.last_result?.state, "the demo's mossy.trails failed its last sync").toBe("failed");
    expect(sourceOf(sources, BARE), "the demo's juni.vale has no source").toEqual([]);
    await page.goto(`/people/${person.id}`);
    await expect(page.getByRole("heading", { level: 2, name: person.name })).toBeVisible();
    await expect(page.locator(".person-profile").first()).toBeVisible();
    await idle(page);
    await fn({ person, one, failed, two, loose });
  } finally {
    for (const id of made) await request.delete(`/api/sources/${id}`, { headers: H });
    await request.delete(`/api/people/${person.id}`, { headers: H });
  }
}

const profile = (page, a) => page.locator(`.person-profile[data-profile="${key(a)}"]`);
const itemsOf = async menu => (await menu.getByRole("menuitem").allTextContents()).map(t => t.trim());

async function checkSizes(page, testInfo, view) {
  await dismissToasts(page);
  const found = [];
  for (const s of SIZES) {
    await page.setViewportSize({ width: s.width, height: s.height });
    await settle(page);
    found.push(...await checkLayout(page, testInfo, { view, size: s.label, scope: ".person-profiles" }));
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await settle(page);
  expect(found, `layout findings:\n${formatFindings(found)}`).toEqual([]);
}

test("Profiles: one row per profile, an account with its source, its sources, alone, and a source alone", async ({ page, request }, testInfo) => {
  await withPerson(page, request, async ({ one, failed, two, loose }) => {
    const list = page.getByRole("region", { name: "Profiles", exact: true });
    await expect(list.locator(".person-section-head .card-title")).toHaveText("Profiles 5");
    await expect(list.locator(".person-profile")).toHaveCount(5);
    // One list: no Accounts, no Sources, none of their rows.
    await expect(page.getByRole("region", { name: /^(Accounts|Sources)$/ })).toHaveCount(0);
    await expect(page.locator(".person-account, .source-row")).toHaveCount(0);
    await expect(list.getByRole("button", { name: "Sync all 5" })).toBeVisible();

    // An account and its one source: one row. Who, one quiet line, the
    // preset, the state, Copy, Sync and a ⋯ menu.
    const merged = profile(page, ONE);
    await expect(merged).toHaveAttribute("data-source-id", String(one.id));
    await expect(merged.locator(".creator-name")).toContainText(`@${ONE.handle}`);
    await expect(merged.locator(".person-profile-id > .creator-sub")).toHaveText(/^Instagram · \d+ posts? · .+ · newest /);
    await expect(merged.getByRole("combobox", { name: `Preset of @${ONE.handle}` })).toBeVisible();
    await expect(merged.getByRole("button", { name: `Copy the address of @${ONE.handle}` })).toBeVisible();
    await expect(merged.getByRole("button", { name: `Sync @${ONE.handle}` })).toBeVisible();
    await expect(merged.getByRole("button")).toHaveCount(5);       // Copy, Sync, ⋯, and the new name's Accept and Dismiss
    // Its tool reported a new name: asked once, with no "renamed" badge beside it (#235).
    await expect(merged.locator(".source-rename")).toHaveCount(1);
    await expect(merged.locator(".source-rename")).toContainText("Now called");
    await expect(merged.locator(".source-badge")).toHaveCount(0);
    // Folded: its id, tool and folder are in the details.
    await expect(merged.locator(".person-profile-details, .tool-chip, .source-folder")).toHaveCount(0);
    let menu = await openMenu(page, merged, `More for @${ONE.handle}`);
    expect(await itemsOf(menu)).toEqual(["Open profile", "Show feed", "Details", "Sync full history…", "Don't sync",
      "Move to creator…", "Unlink", "Remove source"]);
    await expect(menu.getByRole("separator")).toHaveCount(3);
    await menu.getByRole("menuitem", { name: "Details" }).click();
    const details = merged.locator(".person-profile-details");
    await expect(details).toContainText(`id ${ONE.id}`);
    await expect(details.locator(".tool-chip")).toHaveText("instaloader");
    await expect(details.locator(".source-folder")).toHaveText(one.folder);
    await (await menuItem(page, merged, `More for @${ONE.handle}`, "Hide details")).click();
    await expect(details).toHaveCount(0);

    // A failed sync: its badge and a few words on the line, the sentence
    // in the title and, in full, in the details.
    const bad = profile(page, FAILED);
    await expect(bad.locator(".source-badge")).toHaveText("rate limited");
    await expect(bad.locator(".source-message")).toHaveText("try again later");
    await expect(bad.locator(".source-status")).toHaveAttribute("title", new RegExp(`^${failed.last_result.message.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
    await (await menuItem(page, bad, `More for @${FAILED.handle}`, "Details")).click();
    await expect(bad.locator(".person-profile-detail-msg")).toContainText(failed.last_result.message);

    // An account with two sources: its own line, then one line per source.
    const several = profile(page, TWO);
    await expect(several).not.toHaveAttribute("data-source-id", /./);
    const lines = several.getByRole("list", { name: `Sources of @${TWO.handle}` }).locator(".person-profile-source");
    await expect(lines).toHaveCount(2);
    for (const [k, s] of two.entries()) {
      await expect(lines.nth(k)).toHaveAttribute("data-source-id", String(s.id));
      await expect(lines.nth(k).getByRole("combobox", { name: /^Preset of / })).toHaveValue("");
      await expect(lines.nth(k).getByRole("button", { name: /^Sync / })).toBeVisible();
    }
    await expect(several.getByRole("button", { name: /^Sync / })).toHaveCount(2);
    await expect(several.getByRole("button", { name: /^Copy the address of / })).toHaveCount(1);
    menu = await openMenu(page, several.locator(".person-profile-line").first(), `More for @${TWO.handle}`);
    expect(await itemsOf(menu)).toEqual(["Open profile", "Show feed", "Details", "Don't sync", "Move to creator…", "Unlink"]);
    await page.keyboard.press("Escape");
    menu = await openMenu(page, lines.nth(1), `More for x.com/${TWO.handle}/media`);
    expect(await itemsOf(menu)).toEqual(["Open profile", "Sync full history…", "Remove source"]);
    await page.keyboard.press("Escape");

    // An account with no source: the same row, with no preset and no Sync.
    const bare = profile(page, BARE);
    await expect(bare.locator(".creator-name")).toContainText(`@${BARE.handle}`);
    await expect(bare.getByRole("combobox")).toHaveCount(0);
    await expect(bare.getByRole("button", { name: /^Sync / })).toHaveCount(0);
    menu = await openMenu(page, bare, `More for @${BARE.handle}`);
    expect(await itemsOf(menu)).toEqual(["Open profile", "Show feed", "Details", "Don't sync", "Unlink"]);
    await page.keyboard.press("Escape");

    // A source with no account yet: a row of its own.
    const alone = page.locator(`.person-profile[data-source-id="${loose.id}"]`);
    await expect(alone).toHaveAttribute("data-profile", `source:${loose.id}`);
    await expect(alone.locator(".creator-name")).toHaveText(loose.target.replace("https://", ""));
    await expect(alone.locator(".person-profile-id > .creator-sub")).toHaveText("X · first sync not done yet");
    await expect(alone.getByRole("combobox", { name: /^Preset of / })).toHaveValue("");
    await expect(alone.getByRole("button", { name: /^Sync / })).toBeVisible();
    menu = await openMenu(page, alone, /^More for /);
    expect(await itemsOf(menu)).toEqual(["Open profile", "Details", "Sync full history…", "Move to creator…", "Unlink", "Remove source"]);
    await page.keyboard.press("Escape");

    await idle(page);
    await checkSizes(page, testInfo, "person, Profiles");
    await page.screenshot({ path: testInfo.outputPath("profiles.png"), fullPage: false });
  });
});

test("Profiles: a row's Copy copies the profile's address, as a link's does", async ({ page, request }) => {
  // The clipboard as Copy writes it: kept in the page, never the real one.
  await page.addInitScript(() => {
    window.__fvCopied = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: t => { window.__fvCopied.push(t); return Promise.resolve(); } },
    });
  });
  await withPerson(page, request, async ({ loose }) => {
    await profile(page, ONE).getByRole("button", { name: `Copy the address of @${ONE.handle}` }).click();
    await expect(page.locator(".toast", { hasText: /^Copied$/ })).toBeVisible();
    await dismissToasts(page);
    // One with no account: its source's address.
    await page.locator(`.person-profile[data-source-id="${loose.id}"]`).getByRole("button", { name: /^Copy the address of / }).click();
    await expect(page.locator(".toast", { hasText: /^Copied$/ })).toBeVisible();
    expect(await page.evaluate(() => window.__fvCopied)).toEqual([`https://www.instagram.com/${ONE.handle}/`, loose.url]);
  });
});

test("Profiles: Unlink takes the account and its sources from them", async ({ page, request }) => {
  await withPerson(page, request, async ({ person, one, two, loose }) => {
    const theirs = async () => (await (await request.get(`/api/people/${person.id}`, { headers: H })).json()).accounts.map(key);
    const personOf = async id => (await sourcesNow(request)).find(s => s.id === id).person;

    // One with no account: the source.
    const alone = page.locator(`.person-profile[data-source-id="${loose.id}"]`);
    await (await menuItem(page, alone, /^More for /, "Unlink")).click();
    await expect(page.locator(".toast", { hasText: `is no longer ${person.name}'s.` })).toBeVisible();
    await expect(alone).toHaveCount(0);
    expect(await personOf(loose.id)).toBeNull();
    await dismissToasts(page);

    // An account and its source, in one go.
    await (await menuItem(page, profile(page, ONE), `More for @${ONE.handle}`, "Unlink")).click();
    await expect(page.locator(".toast", { hasText: `@${ONE.handle} unlinked.` })).toBeVisible();
    await expect(profile(page, ONE)).toHaveCount(0);
    expect(await theirs()).not.toContain(key(ONE));
    expect(await personOf(one.id)).toBeNull();
    await dismissToasts(page);

    // An account with two sources added from this page, theirs on their own: all three.
    await (await menuItem(page, profile(page, TWO).locator(".person-profile-line").first(), `More for @${TWO.handle}`, "Unlink")).click();
    await expect(page.locator(".toast", { hasText: `@${TWO.handle} unlinked.` })).toBeVisible();
    await expect(profile(page, TWO)).toHaveCount(0);
    expect(await theirs()).toEqual([key(FAILED), key(BARE)]);
    for (const s of two) expect(await personOf(s.id)).toBeNull();
    await expect(page.getByRole("region", { name: "Profiles", exact: true }).locator(".person-section-head .card-title")).toHaveText("Profiles 2");
    await idle(page);
  });
});

test("Profiles: one Add profile box; the other ways fold behind More ways to add, which counts the suggestions", async ({ page, request }) => {
  await withPerson(page, request, async ({ person }) => {
    const { suggestions } = await (await request.get("/api/people/suggestions", { headers: H })).json();
    const n = suggestions.filter(s => s.person?.id === person.id).length;
    expect(n, "the demo suggests more accounts of juni.vale's person").toBeGreaterThan(0);
    const box = page.locator(".person-profile-add");
    await expect(box.locator(".person-profile-add-title")).toHaveText("Add profile");
    await expect(box.getByRole("textbox", { name: "Profile link, or an Instagram name" })).toBeVisible();
    await expect(box.getByRole("button", { name: "Add profile" })).toHaveText("Add");

    // Folded: one quiet line, which says what waits behind it.
    const toggle = box.getByRole("button", { name: /^More ways to add/ });
    await expect(toggle).toHaveText(`More ways to add · ${n} suggested`);
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    const picker = page.getByRole("combobox", { name: "Add an account" });
    const also = page.locator(".person-profile-more .suggestions");
    const bio = page.getByRole("button", { name: "Import from link-in-bio…" });
    for (const hidden of [picker, also, bio]) await expect(hidden).toBeHidden();

    const fold = await openMoreWays(page);
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    for (const shown of [picker, also, bio]) await expect(shown).toBeVisible();
    await expect(fold).toContainText("An account FeedVault has indexed, or a source linked to nobody");
    await expect(also).toContainText("Also them?");
    await expect(also.getByRole("button", { name: /Link$/ })).toHaveCount(n);

    // Folded again by the same line.
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    for (const hidden of [picker, also, bio]) await expect(hidden).toBeHidden();
    await idle(page);
  });
});

test("a person's head: Feed is the one button, Review … Trash are in the ⋯ menu", async ({ page, request }) => {
  await withPerson(page, request, async ({ person }) => {
    const nav = page.locator(".person-hero .person-links");
    await expect(nav.getByRole("link")).toHaveCount(1);
    await expect(nav.getByRole("link", { name: "Feed" })).toHaveAttribute("href", `/?person=${person.id}`);
    const menu = await openMenu(page, page.locator(".page-head"), `More for ${person.name}`);
    expect(await itemsOf(menu)).toEqual(["Review", "Re-review", "Storage", "Stats", "Trash", "Rename", "Mute", "Don't sync", "Delete person"]);
    await expect(menu.getByRole("separator")).toHaveCount(2);
    for (const [name, href] of [
      ["Review", `/review?person=${person.id}`], ["Re-review", `/review?person=${person.id}&again=1`],
      ["Storage", `/storage?person=${person.id}`], ["Stats", `/stats?person=${person.id}`], ["Trash", `/trash?person=${person.id}`],
    ]) {
      const item = menu.getByRole("menuitem", { name, exact: true });
      await expect(item).toHaveAttribute("href", href);
      await expect(item.locator("svg")).toHaveCount(1);
    }
    await menu.getByRole("menuitem", { name: "Storage", exact: true }).click();
    await expect(page).toHaveURL(`/storage?person=${person.id}`);
    await expect(page.getByRole("heading", { level: 2, name: "Storage", exact: true })).toBeVisible();
    await idle(page);
  });
});
