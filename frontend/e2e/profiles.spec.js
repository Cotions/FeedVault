// Desktop, 1440x900: profiles (docs/API.md "Profiles"). A second profile is
// made in Settings → Profiles, switched to and back from the header, then
// removed; its data directory is a new folder beside the instance's own,
// deleted with it. Whatever happens, the instance ends on the profile it
// started on, with that one only: the other specs share it.
import fs from "node:fs";
import { test, expect, idle } from "./fixtures.js";

const H = { "X-FeedVault": "1" };
const LONG = "A profile with a very long name, as long as a name may be";   // under the 60 allowed

const profilesOf = async request => (await request.get("/api/profiles", { headers: H })).json();

// A switch is refused while a scan runs (one starts after every switch).
async function scanDone(request) {
  await expect.poll(async () => (await (await request.get("/api/scan", { headers: H })).json()).running,
                    { timeout: 60_000, message: "the scan ends" }).toBe(false);
}

// Nothing in the header's right side touches another part of it, or leaves it.
async function headerFits(page, what) {
  const boxes = await page.evaluate(() => {
    const box = sel => {
      const r = document.querySelector(sel).getBoundingClientRect();
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
    };
    const name = document.querySelector(".profile-switch-name");
    return { search: box(".header-search-wrap"), right: box(".header-right"), btn: box(".profile-switch-btn"),
             header: box("header"), width: document.documentElement.clientWidth,
             cut: name.scrollWidth > name.clientWidth, title: document.querySelector(".profile-switch-btn").title };
  });
  expect(boxes.search.right, `${what}: the search field ends before the switcher`).toBeLessThanOrEqual(boxes.btn.left);
  expect(boxes.right.right, `${what}: the header's right side is in the window`).toBeLessThanOrEqual(boxes.width);
  expect(boxes.btn.left, `${what}: the switcher is inside the header's right side`).toBeGreaterThanOrEqual(boxes.right.left);
  expect(boxes.btn.bottom, `${what}: the switcher is inside the header`).toBeLessThanOrEqual(boxes.header.bottom);
  return boxes;
}

test("a second profile is made, switched to from the header and back, and removed", async ({ page, request }) => {
  test.setTimeout(180_000);
  const before = await profilesOf(request);
  expect(before.profiles, "the instance starts with one profile").toHaveLength(1);
  const home = before.profiles[0];
  const name = `E2E profile ${Date.now()}`;
  const data = `${home.data_directory.replace(/\/+$/, "")}-e2e-${Date.now()}`;
  try {
    await scanDone(request);
    // One profile: no switcher, the header as it always was.
    await page.goto("/settings#profiles");
    await expect(page.locator("#profiles-card .profile-row")).toHaveCount(1);
    await expect(page.locator(".profile-switch")).toHaveCount(0);

    await page.getByRole("textbox", { name: "Name", exact: true }).fill(name);
    await page.getByRole("textbox", { name: "Data directory" }).fill(data);
    await page.getByRole("button", { name: "Create profile" }).click();
    await expect(page.getByRole("status").filter({ hasText: `Created ${name}` })).toBeVisible();
    await expect(page.locator("#profiles-card .profile-row")).toHaveCount(2);
    // Made, not switched to: the header now shows the profile the page is in.
    const button = page.locator(".profile-switch-btn");
    await expect(button).toContainText(home.name);
    await idle(page);

    // Switch from the header: the app starts again at the feed, in an empty library.
    await button.click();
    const menu = page.getByRole("menu", { name: "Profiles" });
    await expect(menu.getByRole("menuitemradio")).toHaveCount(2);
    await expect(menu.getByRole("menuitemradio", { name: home.name })).toHaveAttribute("aria-checked", "true");
    await Promise.all([page.waitForURL(u => u.pathname === "/"), menu.getByRole("menuitemradio", { name }).click()]);
    await expect(button).toContainText(name);
    await expect(page.getByRole("heading", { name: "No posts yet" })).toBeVisible();
    const there = await profilesOf(request);
    expect(there.profiles.find(p => p.active).name).toBe(name);
    expect(there.profiles.find(p => p.active).posts).toBe(0);
    expect(there.profiles.find(p => !p.active).posts, "the first library is still counted").toBe(home.posts);
    expect(fs.existsSync(`${data}/feedvault.db`), "the new library has its own database").toBe(true);

    // A long name is cut in the header, never pushed into the search field.
    const made = there.active;
    await request.post(`/api/profiles/${made}`, { headers: H, data: { name: LONG } });
    for (const size of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 1024, height: 768 }]) {
      await page.setViewportSize(size);
      await page.reload();
      await expect(button).toContainText(LONG);
      const boxes = await headerFits(page, `${size.width} wide`);
      expect(boxes.cut, `${size.width} wide: the long name is cut`).toBe(true);
      expect(boxes.title).toBe(`Profile: ${LONG}`);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await idle(page);

    // Back to the first profile, its posts as they were.
    await scanDone(request);
    await button.click();
    await Promise.all([page.waitForURL(u => u.pathname === "/"), menu.getByRole("menuitemradio", { name: home.name }).click()]);
    await expect(button).toContainText(home.name);
    await expect(page.locator(".feed-empty")).toHaveCount(0);
    const back = await profilesOf(request);
    expect(back.active).toBe(home.id);
    expect(back.profiles.find(p => p.active).posts).toBe(home.posts);

    // Removed in Settings: forgotten, its files left where they are.
    await button.click();
    await menu.getByRole("menuitem", { name: "Manage profiles" }).click();
    await expect(page).toHaveURL(/\/settings#profiles$/);
    await page.getByRole("button", { name: `Remove ${LONG}` }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("Its files stay on disk");
    await expect(dialog).toContainText(data);
    await dialog.getByRole("button", { name: "Remove profile" }).click();
    await expect(page.locator("#profiles-card .profile-row")).toHaveCount(1);
    await expect(page.locator(".profile-switch")).toHaveCount(0);
    expect(fs.existsSync(`${data}/feedvault.db`), "its database is still on disk").toBe(true);
    await idle(page);
  } finally {
    // The first profile again, alone, however far the test got.
    for (let i = 0; i < 120; i++) {
      const r = await request.post(`/api/profiles/${home.id}/activate`, { headers: H });
      if (r.status() !== 409) break;
      await new Promise(done => setTimeout(done, 500));
    }
    for (const p of (await profilesOf(request)).profiles) {
      if (p.id !== home.id) await request.delete(`/api/profiles/${p.id}`, { headers: H });
    }
    await scanDone(request);
  }
});
