// Desktop, 1440x900: download presets. Settings → Sync → Presets (a
// platform's Default, a named preset made, edited and deleted, the unused
// platforms folded away), and the sources that follow them: the select on
// a source's row (Default, a preset, Custom…, Profile only), its Custom
// dialog, which opens at its top (#235), and "Sync full history…". Two X
// sources are added to the throwaway instance and removed after, with the
// presets; nothing is synced.
import { test, expect, idle, checkLayout, formatFindings, menuItem } from "./fixtures.js";

const H = { "X-FeedVault": "1" };
const NAME = "E2E videos weekly";

const twitter = async request => (await (await request.get("/api/config", { headers: H })).json())
  .source_defaults.find(b => b.tool === "gallery-dl" && b.platform === "twitter");
const source = async (request, s) => (await (await request.get(`/api/sources/${s.id}`, { headers: H })).json());

async function withSources(request, run) {
  const add = async (target, options) => (await (await request.post("/api/sources", {
    headers: H, data: { target, ...(options ? { options } : {}) } })).json()).source;
  const plain = await add("https://x.com/e2e_defaults_plain");
  const own = await add("https://x.com/e2e_defaults_own", { media: "videos" });
  try {
    await run({ plain, own });
  } finally {
    for (const s of [plain, own]) if (s) await request.delete(`/api/sources/${s.id}`, { headers: H });
    await request.post("/api/config", { headers: H, data: { source_defaults: { "gallery-dl": { twitter: null } } } });
    for (const p of (await twitter(request)).presets) await request.delete(`/api/presets/${p.id}`, { headers: H });
  }
}

test("presets: made in Settings, picked on a source's row; Custom opens at its top", async ({ page, request }, testInfo) => {
  await withSources(request, async ({ plain, own }) => {
    expect(plain).toMatchObject({ preset: null, custom: false, presets: [] });
    expect(own).toMatchObject({ preset: null, custom: true, overrides: ["media"] });

    // The card's old address still opens it.
    await page.goto("/settings#download-defaults");
    const card = page.locator("#presets");
    await expect(card).toBeVisible();
    await expect(card.locator(".card-title")).toHaveText("Presets");
    const group = card.locator('.preset-group[data-presets="gallery-dl:twitter"]');
    await expect(group.locator(".preset-group-head")).toContainText("X");
    // The demo has X sources of its own: counted with these two.
    const counts = await twitter(request);
    expect(counts.sources).toBeGreaterThanOrEqual(2);
    expect(counts.sources).toBe(counts.default_sources + counts.custom);
    const profiles = n => `${n} profile${n === 1 ? "" : "s"}`;
    await expect(group.locator(".preset-group-count")).toHaveText(`${profiles(counts.sources)} · ${counts.custom} custom`);
    await expect(group.locator('.preset-row[data-preset="default"] .preset-count')).toHaveText(profiles(counts.default_sources));

    // The platforms nobody uses wait behind one toggle (#237).
    const shown = await card.locator(".preset-group").count();
    const unused = card.getByRole("button", { name: /^Show unused \(\d+\)$/ });
    await unused.click();
    expect(await card.locator(".preset-group").count()).toBeGreaterThan(shown);
    await card.getByRole("button", { name: /^Hide unused/ }).click();
    await expect(card.locator(".preset-group")).toHaveCount(shown);

    // Default: images only, saved for the platform. The one that follows it
    // has it; the custom one keeps its own.
    await group.getByRole("button", { name: "Edit Default, X (gallery-dl)" }).click();
    const editor = card.locator(".preset-editor");
    await expect(editor).toHaveCount(1);
    await expect(editor.getByRole("radio", { name: /full history/i })).toHaveCount(0);
    await editor.getByRole("radio", { name: /^images only/i }).check();
    await editor.getByRole("button", { name: "Save" }).click();
    await expect(editor).toHaveCount(0);
    await expect.poll(async () => (await twitter(request)).defaults.media).toBe("images");
    expect((await source(request, plain)).options.media).toBe("images");
    expect((await source(request, own)).options.media).toBe("videos");
    await expect(group.locator('.preset-row[data-preset="default"] .preset-summary')).toContainText("images only");

    // A new preset starts as a copy of Default; one form is open at a time.
    await group.getByRole("button", { name: "New preset, X (gallery-dl)" }).click();
    await expect(editor.getByRole("radio", { name: /^images only/i })).toBeChecked();
    await expect(editor.getByRole("button", { name: "Save" })).toBeDisabled();
    await editor.getByRole("textbox", { name: "Preset name" }).fill(NAME);
    await editor.getByRole("radio", { name: /^videos only/i }).check();
    await editor.getByRole("radio", { name: /^weekly/i }).check();
    // Another row's Edit asks before the typed one goes.
    await group.getByRole("button", { name: "Edit Default, X (gallery-dl)" }).click();
    const discard = page.getByRole("alertdialog", { name: "Discard unsaved changes?" });
    await discard.getByRole("button", { name: "Keep editing" }).click();
    await expect(editor).toHaveCount(1);
    // The scripts note is said once, not per platform (#237).
    expect(await card.locator(".script-pick-note").count()).toBeLessThanOrEqual(1);
    await idle(page);
    const found = await checkLayout(page, testInfo, { view: "presets, a new one open", size: "1440x900", scope: "#presets" });
    expect(found, formatFindings(found)).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("presets.png"), fullPage: false });
    await editor.getByRole("button", { name: "Save" }).click();
    await expect(editor).toHaveCount(0);
    const made = (await twitter(request)).presets.find(p => p.name === NAME);
    expect(made).toMatchObject({ set: ["media", "schedule"], sources: 0 });
    const row = group.locator(`.preset-row[data-preset="${made.id}"]`);
    await expect(row.locator(".preset-summary")).toHaveText("timeline · videos only · weekly");
    await expect(row.locator(".preset-count")).toHaveText("0 profiles");

    // The source list: one select per row says what it follows.
    await page.goto("/creators");
    const plainRow = page.locator(`.source-row[data-source-id="${plain.id}"]`);
    const ownRow = page.locator(`.source-row[data-source-id="${own.id}"]`);
    const pick = r => r.getByRole("combobox", { name: /^Preset of / });
    await expect(pick(plainRow)).toHaveValue("");
    await expect(pick(ownRow)).toHaveValue("custom");
    await expect(plainRow.locator(".source-inherit, .source-summary")).toHaveCount(0);
    await expect(pick(plainRow).locator("option")).toHaveText(["Default", NAME, "Custom…", /^─+$/, "Profile only (never download)"]);

    // A pick saves at once, with an Undo.
    await pick(plainRow).selectOption({ label: NAME });
    await expect.poll(async () => (await source(request, plain)).preset?.id).toBe(made.id);
    expect((await source(request, plain)).options).toMatchObject({ media: "videos", schedule: "weekly" });
    await expect(pick(plainRow)).toHaveValue(made.id);
    await expect(pick(plainRow)).toHaveAttribute("title", `${NAME}: timeline · videos only · weekly`);
    await page.locator(".toast").getByRole("button", { name: "Undo" }).click();
    await expect.poll(async () => (await source(request, plain)).preset).toBe(null);
    await pick(plainRow).selectOption({ label: NAME });
    await expect.poll(async () => (await source(request, plain)).preset?.id).toBe(made.id);

    // Custom…: a plain form of what it has now, open at its top (#235):
    // no "set here" marks, no Download radios, no full history.
    await pick(plainRow).selectOption("custom");
    const dialog = page.getByRole("alertdialog", { name: /^Custom options for / });
    await expect(dialog).toBeVisible();
    expect(await dialog.evaluate(d => d.scrollTop)).toBe(0);
    await expect(dialog.locator(".modal-title")).toBeInViewport();
    await expect(dialog.getByRole("radio", { name: /^videos only/i })).toBeChecked();
    await expect(dialog.locator(".source-default, .source-inherits")).toHaveCount(0);
    await expect(dialog.getByRole("group", { name: "Download this profile" })).toHaveCount(0);
    await expect(dialog.getByRole("radio", { name: /full history/i })).toHaveCount(0);
    await idle(page);
    const inDialog = await checkLayout(page, testInfo, { view: "custom options", size: "1440x900", scope: ".modal" });
    expect(inDialog, formatFindings(inDialog)).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("custom.png"), fullPage: false });
    // Cancelled, it still follows its preset.
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(pick(plainRow)).toHaveValue(made.id);

    // One that is custom: "Edit custom…" in its ⋯ menu; saved, its own options.
    await (await menuItem(page, ownRow, /^More for /, "Edit custom…")).click();
    await expect(dialog.getByRole("radio", { name: /^videos only/i })).toBeChecked();
    await dialog.getByRole("radio", { name: /^daily/i }).check();
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(async () => (await source(request, own)).options.schedule).toBe("daily");
    expect(await source(request, own)).toMatchObject({ custom: true, preset: null });
    // Default picked: its own options go.
    await pick(ownRow).selectOption("");
    await expect.poll(async () => (await source(request, own)).custom).toBe(false);
    expect(await source(request, own)).toMatchObject({ overrides: [], options: { media: "images", schedule: "off" } });
    await expect(ownRow.getByRole("button", { name: /^More for / })).toBeVisible();

    // Profile only, and back: a preset downloads it again.
    await pick(ownRow).selectOption("only");
    await expect(ownRow).toHaveClass(/is-profile-only/);
    await expect(pick(ownRow)).toHaveValue("only");
    expect((await source(request, own)).options.download).toBe("off");
    await expect(ownRow.getByRole("button", { name: /^Sync / })).toHaveCount(0);
    await pick(ownRow).selectOption("");
    await expect(ownRow).not.toHaveClass(/is-profile-only/);
    expect((await source(request, own)).options.download).toBe("platform");

    // Full history is asked for from the ⋯ menu, and says what it does.
    await (await menuItem(page, ownRow, /^More for /, "Sync full history…")).click();
    const history = page.getByRole("alertdialog", { name: /full history\?$/ });
    await expect(history).toContainText("walks the whole profile once");
    await history.getByRole("button", { name: "Cancel" }).click();
    expect((await source(request, own)).options.full_history).toBe(false);

    // Deleted: it says who goes back to Default, and they do.
    await page.goto("/settings#presets");
    await expect(row.locator(".preset-count")).toHaveText("1 profile");
    await group.getByRole("button", { name: `Edit ${NAME}, X (gallery-dl)` }).click();
    await editor.getByRole("button", { name: "Delete…" }).click();
    const ask = page.getByRole("alertdialog", { name: `Delete the preset ${NAME}?` });
    await expect(ask).toContainText("1 profile of X (gallery-dl) follows it: it goes back to Default.");
    await ask.getByRole("button", { name: "Delete preset" }).click();
    await expect(row).toHaveCount(0);
    expect(await source(request, plain)).toMatchObject({ preset: null, custom: false, options: { media: "images" } });
  });
});
