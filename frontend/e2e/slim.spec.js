// Desktop, 1440x900: Settings → Sync, Instagram metadata. The count is the
// real job on the throwaway instance (it changes nothing); the rewrite is
// never started here: its request and its result are answered by the test,
// so the demo vault's files stay as every other spec expects them.
import { test, expect, idle } from "./fixtures.js";

const H = { "X-FeedVault": "1" };

async function openCard(page) {
  await page.goto("/settings#sync");
  const card = page.locator("#slim-metadata");
  await expect(card).toBeVisible();
  await idle(page);
  return card;
}

test("Settings: slimming new metadata is off until switched on", async ({ page, request }) => {
  const card = await openCard(page);
  const box = card.getByRole("checkbox", { name: /Slim new metadata after each sync and save/ });
  await expect(box).not.toBeChecked();
  const setting = async () => (await (await request.get("/api/config", { headers: H })).json()).slim_metadata;
  expect(await setting()).toBe(false);
  try {
    await box.click();                           // checked once the setting is saved and read back
    await expect.poll(setting).toBe(true);
    await expect(box).toBeChecked();
    await box.click();
    await expect.poll(setting).toBe(false);
    await expect(box).not.toBeChecked();
  } finally {
    await request.post("/api/config", { headers: H, data: { slim_metadata: false } });
  }
});

test("Settings: slimming the metadata on disk counts first, then asks, then says how it went", async ({ page }) => {
  const started = [];
  let ended = null;                              // the rewrite's job, once "started"
  await page.route(/\/api\/jobs(\?.*)?$/, async route => {
    const req = route.request();
    if (req.method() === "POST") {
      const body = req.postDataJSON();
      started.push(body);
      if (body.kind !== "slim-metadata" || body.params.mode !== "apply") return route.fallback();
      ended = {
        id: 999999, kind: "slim-metadata", label: "Slim instaloader metadata", params: { mode: "apply" },
        argv: ["feedvault", "slim-metadata", "apply"], cwd: null, group: "instaloader", state: "done",
        created_at: 1727500000, started_at: 1727500000, ended_at: 1727500002, exit_code: null, rescan: null,
        result: { mode: "apply", checked: 14, clean: 0, files: 12, bytes_before: 48211, bytes_after: 8112, failures: 3,
                  failed: [{ path: "/media/instagram/a-folder-with-a-very-long-name-that-goes-on-and-on/2024-06-01_12-00-00_UTC.json", error: "Permission denied" },
                           { path: "/media/instagram/b/2024-06-02_12-00-00_UTC.json", error: "it changed while it was being rewritten" }],
                  stopped: false },
        message: "12 files slimmed, 39.2 KB freed; 3 files failed and were left as they were", waits_until: null,
      };
      return route.fulfill({ json: { ok: true, job: { ...ended, state: "queued", ended_at: null, result: null, message: null } } });
    }
    if (!ended) return route.fallback();
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, jobs: [ended, ...body.jobs] } });
  });

  const card = await openCard(page);
  await card.getByRole("button", { name: "Slim existing metadata…" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".modal-title")).toHaveText(/^Slim \d+ metadata files\?$/);
  await expect(dialog).toContainText("This cannot be undone.");
  await expect(dialog).toContainText("For a post that is gone from Instagram, what is removed cannot be fetched again.");
  await expect(dialog).toContainText("That is measured, not estimated");
  await expect(dialog).toContainText(/take [\d.]+ (B|KB|MB) now and would take [\d.]+ (B|KB)/);
  expect(started).toEqual([{ kind: "slim-metadata", params: { mode: "preview" } }]);
  // Focus starts on Cancel, and Cancel starts nothing.
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  expect(started).toHaveLength(1);

  await card.getByRole("button", { name: "Slim existing metadata…" }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: /^Slim \d+ files$/ }).click();
  await expect(dialog).toHaveCount(0);
  expect(started.at(-1)).toEqual({ kind: "slim-metadata", params: { mode: "apply" } });
  // How it went: what was slimmed and freed, and each file left as it was.
  await expect(card.locator(".tool-status")).toContainText("12 files slimmed, 39.2 KB freed; 3 files failed and were left as they were");
  const failed = card.locator(".slim-failed li");
  await expect(failed).toHaveCount(3);
  await expect(failed.nth(0)).toContainText("Permission denied");
  await expect(failed.nth(2)).toContainText("and 1 other");
  // Nothing runs out of the card, however long a path is.
  const box = await card.boundingBox();
  for (const li of await failed.all()) {
    const b = await li.boundingBox();
    expect(b.x + b.width, "a failed file stays in the card").toBeLessThanOrEqual(box.x + box.width + 0.5);
  }
});
