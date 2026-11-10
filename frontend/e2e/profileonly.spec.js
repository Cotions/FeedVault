// Desktop: profile-only sources (#218) on the stress person (its 60-character
// name), at 1024x768, 1280x800, 1440x900 and 1920x1080: a Spotify profile
// (no downloader takes it) and an X source set to Download: never show with
// the accounts, marked Profile only, never in Sources and never synced; Add
// source offers "Add profile" for an unrouted link and "Profile only" in
// its preset select for a routed one; the Spotify profile's Download
// options ask for a folder, the X one's Preset… downloads it again;
// Settings → Sync has the Profile only card. Sources are
// added to the throwaway instance and removed after; nothing is synced.
import { test, expect, idle, checkLayout, dismissToasts, formatFindings, stressData, settle, menuItem } from "./fixtures.js";

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

test("Profile only: shown with the accounts, never synced, Add profile, Download, Settings, no overlap", async ({ page, request, pageErrors }, testInfo) => {
  // The backend refuses a profile only source's sync: 409, asked for below.
  pageErrors.allow(/^api: POST \S+\/api\/sources\/\d+\/sync → 409$/);
  pageErrors.allow(/^console: .*status of 409 .*\/api\/sources\/\d+\/sync\)$/);
  const made = [];
  const add = async data => {
    const r = await (await request.post("/api/sources", { headers: H, data: { person: S.person, ...data } })).json();
    expect(r.ok, r.error).toBe(true);
    made.push(r.source.id);
    return r.source;
  };
  try {
    const spotify = await add({ target: "https://open.spotify.com/artist/e2eProfileOnly", tool: "none" });
    expect(spotify).toMatchObject({ tool: "none", folder: "", profile_only: true, needs_tool: false });
    const x = await add({ target: "https://x.com/e2e_profile_only", options: { download: "off" } });
    expect(x.profile_only).toBe(true);
    const sync = await request.post(`/api/sources/${x.id}/sync`, { headers: H });
    expect(sync.status()).toBe(409);
    expect((await sync.json()).profile_only).toBe(true);

    // The person page: both with the accounts, marked; neither in Sources.
    await page.goto(`/people/${S.person}`);
    const accounts = page.locator(".person-accounts").first();
    for (const s of [spotify, x]) {
      const row = accounts.locator(`.person-account[data-source-id="${s.id}"]`);
      await expect(row).toBeVisible();
      await expect(row.locator(".profile-only-chip")).toBeVisible();
      await expect(page.locator(`.source-row[data-source-id="${s.id}"]`)).toHaveCount(0);
    }
    await idle(page);
    await checkSizes(page, testInfo, "person, profile only accounts");

    // Its Options: Download only, for a profile no downloader takes.
    const row = accounts.locator(`.person-account[data-source-id="${spotify.id}"]`);
    await (await menuItem(page, row, /^More for /, "Download options…")).click();
    const dialog = page.getByRole("alertdialog", { name: /^Download / });
    await expect(dialog.getByRole("group", { name: "Download this profile" })).toBeVisible();
    await expect(dialog.getByRole("radio", { name: /Never/ })).toBeChecked();
    await dialog.getByRole("radio", { name: /Always/ }).check();
    await expect(dialog.locator(".source-give-tool")).toContainText("not in the routing table");
    await idle(page);
    await checkSizes(page, testInfo, "profile only, Download", ".modal-overlay");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    // Always was picked: Cancel asks before it goes.
    const discard = page.getByRole("alertdialog", { name: "Discard unsaved changes?" });
    await discard.getByRole("button", { name: "Discard" }).click();
    await expect(page.getByRole("alertdialog")).toHaveCount(0);

    // One a downloader takes: Preset… says Profile only; a preset downloads it again.
    const xRow = accounts.locator(`.person-account[data-source-id="${x.id}"]`);
    await (await menuItem(page, xRow, /^More for /, "Preset…")).click();
    const preset = page.getByRole("alertdialog", { name: /^Preset of / });
    const pick = preset.getByRole("combobox", { name: /^Preset of / });
    await expect(pick).toHaveValue("only");
    await expect(pick).toBeFocused();
    await idle(page);
    await checkSizes(page, testInfo, "profile only, Preset", ".modal-overlay");
    await pick.selectOption("");
    await preset.getByRole("button", { name: "Save" }).click();
    await expect(preset).toHaveCount(0);
    const back = (await (await request.get(`/api/sources/${x.id}`, { headers: H })).json());
    expect(back).toMatchObject({ profile_only: false, preset: null, custom: false, options: { download: "platform" } });
    // It downloads: a source of theirs now, with the select on its row.
    const sourceRow = page.locator(`.source-row[data-source-id="${x.id}"]`);
    await expect(sourceRow.getByRole("combobox", { name: /^Preset of / })).toHaveValue("");

    // Add source: an unrouted profile link is offered as a profile.
    const field = page.getByRole("textbox", { name: "Profile link, or an Instagram name" });
    await field.fill("https://www.twitch.tv/e2e_profile_only");
    await expect(page.getByRole("button", { name: "Add profile" })).toBeEnabled();
    await expect(page.locator(".source-add .profile-only-chip, .source-resolved .profile-only-chip").first()).toBeVisible();
    await idle(page);
    await checkSizes(page, testInfo, "Add profile");
    // A routed one: one resolved line and the preset select, no options
    // form; Profile only picked makes it "Add profile".
    await field.fill("https://x.com/e2e_profile_only_add");
    const line = page.locator(".source-add .source-resolved");
    await expect(line).toContainText("x.com/e2e_profile_only_add");
    await expect(line.locator(".source-folder")).toBeVisible();
    const choose = line.getByRole("combobox", { name: "Preset" });
    await expect(choose).toHaveValue("");
    await expect(page.locator(".source-add .source-options")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Add source" })).toBeEnabled();
    await choose.selectOption("only");
    await expect(line.locator(".source-folder")).toHaveCount(0);
    await idle(page);
    await checkSizes(page, testInfo, "Add source, a link resolved");
    await page.getByRole("button", { name: "Add profile" }).click();
    await expect(field).toHaveValue("");
    const added = (await (await request.get("/api/sources", { headers: H })).json()).sources
      .find(s => s.target === "https://x.com/e2e_profile_only_add");
    made.push(added.id);
    expect(added).toMatchObject({ profile_only: true, options: { download: "off" } });

    // Settings → Sync: a switch per platform.
    await page.goto("/settings#profile-only");
    const card = page.locator("#profile-only");
    await expect(card).toBeVisible();
    await expect(card.getByRole("checkbox", { name: /Spotify/ })).not.toBeChecked();
    await idle(page);
    await checkSizes(page, testInfo, "Settings, Profile only", "#profile-only");
  } finally {
    for (const id of made) await request.delete(`/api/sources/${id}`, { headers: H });
    await request.post("/api/config", { headers: H, data: { profile_only_platforms: [] } });
  }
});
