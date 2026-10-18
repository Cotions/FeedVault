// Desktop, 1440x900: Settings → Sync → Download defaults, and a source that
// follows them (its row's mark, its Options' "default" / "set here" and
// Reset). Two X sources are added to the throwaway instance and removed
// after, with the defaults; nothing is synced.
import { test, expect, idle, checkLayout, formatFindings } from "./fixtures.js";

const H = { "X-FeedVault": "1" };

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
  }
}

test("download defaults: set once for X, followed by the sources that do not set it", async ({ page, request }, testInfo) => {
  await withSources(request, async ({ plain, own }) => {
    expect(plain.overrides).toEqual([]);
    expect(own.overrides).toEqual(["media"]);
    await page.goto("/settings#download-defaults");
    const card = page.locator("#download-defaults");
    await expect(card).toBeVisible();
    const block = card.locator('details.defaults-block[data-defaults="gallery-dl:twitter"]');
    await expect(block).toHaveCount(1);
    await expect(block.locator("summary")).toContainText("X");
    await block.locator("summary").click();
    await idle(page);
    const found = await checkLayout(page, testInfo, { view: "download defaults, X open", size: "1440x900", scope: "#download-defaults" });
    expect(found, formatFindings(found)).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("defaults.png"), fullPage: false });

    // The X default: images only, saved for the platform.
    await block.getByRole("radio", { name: /^images only/i }).check();
    await block.getByRole("button", { name: "Save" }).click();
    const twitter = async () => (await (await request.get("/api/config", { headers: H })).json())
      .source_defaults.find(b => b.tool === "gallery-dl" && b.platform === "twitter");
    await expect.poll(async () => (await twitter()).defaults.media).toBe("images");
    // The one that sets its own keeps it; the plain one follows.
    const media = async s => (await (await request.get(`/api/sources/${s.id}`, { headers: H })).json()).options.media;
    expect(await media(plain)).toBe("images");
    expect(await media(own)).toBe("videos");

    // Saved, it stays open. Apply to all sources: asks first, with how many.
    await expect(block).toHaveAttribute("open", "");
    await expect(block.getByRole("radio", { name: /^images only/i })).toBeChecked();
    const apply = block.locator(".defaults-own li", { hasText: "Media" }).getByRole("button", { name: /Apply to all sources/ });
    await apply.click();
    const n = (await twitter()).overrides.media;
    expect(n).toBeGreaterThan(0);
    const ask = page.getByRole("alertdialog", { name: `Apply the default media to ${n} source${n === 1 ? "" : "s"}?` });
    await expect(ask).toBeVisible();
    await ask.getByRole("button", { name: "Cancel" }).click();
    await expect(ask).toHaveCount(0);
    expect(await media(own)).toBe("videos");

    // The source list: "defaults" or "custom".
    await page.goto("/creators");
    const plainRow = page.locator(`.source-row[data-source-id="${plain.id}"]`);
    const ownRow = page.locator(`.source-row[data-source-id="${own.id}"]`);
    await expect(plainRow.locator(".source-inherit")).toHaveText("defaults");
    await expect(ownRow.locator(".source-inherit")).toHaveText("custom");

    // Its Options: "set here" with the default and Reset; Reset all to default.
    await ownRow.getByRole("button", { name: "Options" }).click();
    const dialog = page.getByRole("alertdialog", { name: /downloads$/ });
    await expect(dialog).toBeVisible();
    const mediaSet = dialog.locator("fieldset", { has: page.locator("legend", { hasText: "Media" }) });
    await expect(mediaSet.locator(".source-default")).toContainText("set here · default: images only");
    await expect(dialog.locator("fieldset", { has: page.locator("legend", { hasText: "Schedule" }) }).locator(".source-default"))
      .toHaveText("default");
    await idle(page);
    const inDialog = await checkLayout(page, testInfo, { view: "source options, custom", size: "1440x900", scope: ".modal" });
    expect(inDialog, formatFindings(inDialog)).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("options.png"), fullPage: false });
    await dialog.getByRole("button", { name: "Reset all to default" }).click();
    await expect(mediaSet.locator(".source-default")).toHaveText("default");
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(async () => (await (await request.get(`/api/sources/${own.id}`, { headers: H })).json()).overrides)
      .toEqual([]);
    await expect(ownRow.locator(".source-inherit")).toHaveText("defaults");
  });
});
