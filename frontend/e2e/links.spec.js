// Desktop, 1440x900: saved links, on the Links page and on a person's
// page. Addresses are invented (.example); FeedVault never opens them, and
// neither does the test: it checks the anchors, it does not follow them.
import { test, expect, openPage, idle } from "./fixtures.js";

const H = { "X-FeedVault": "1" };

// The anchor of a saved link opens a new tab with no opener and no referrer.
async function expectSafeAnchor(row, url) {
  const a = row.locator("a.link-row-title");
  await expect(a).toHaveAttribute("href", url);
  await expect(a).toHaveAttribute("target", "_blank");
  const rel = (await a.getAttribute("rel")).split(/\s+/);
  expect(rel).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));
}

test("Links page: add, edit and delete a link", async ({ page }) => {
  const url = `https://e2e.example/links-page-${Date.now()}`;
  await openPage(page, { name: "Links", path: "/links" });
  const form = page.locator(".links-add .link-form");
  await form.getByLabel("Address").fill(`  ${url}  `);
  await form.getByLabel("Title").fill("E2E link");
  await form.getByRole("button", { name: "Add link" }).click();
  const row = page.locator(".link-row", { hasText: "E2E link" });
  await expect(row).toHaveCount(1);
  await expect(row.locator(".link-site")).toHaveText("e2e.example");
  await expect(row.locator(".link-kind")).toHaveText("Other");
  await expectSafeAnchor(row, url);
  await expect(form.getByLabel("Address")).toHaveValue("");

  await row.getByRole("button", { name: "Edit E2E link" }).click();
  const edit = page.locator(".link-row.is-editing");
  await edit.getByLabel("Title").fill("E2E link, edited");
  await edit.getByRole("button", { name: "Save" }).click();
  const edited = page.locator(".link-row", { hasText: "E2E link, edited" });
  await expect(edited).toHaveCount(1);
  await expectSafeAnchor(edited, url);

  await edited.getByRole("button", { name: "Delete E2E link, edited" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(url);
  await dialog.getByRole("button", { name: "Delete link" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".link-row", { hasText: "E2E link" })).toHaveCount(0);
  await idle(page);
});

test("a person's Links: add, reorder, edit and delete", async ({ page }) => {
  const stamp = Date.now();
  const made = await page.request.post("/api/people", { headers: H, data: { name: `E2E links ${stamp}` } });
  const pid = (await made.json()).person.id;
  try {
    await page.goto(`/people/${pid}`);
    const section = page.locator(".person-links-section");
    await expect(section.locator(".empty")).toBeVisible();
    const form = section.locator(".person-link-add .link-form");
    for (const [path, title] of [["one", "First"], ["two", "Second"]]) {
      await form.getByLabel("Address").fill(`https://e2e-${stamp}.example/${path}`);
      await form.getByLabel("Title").fill(title);
      await form.getByRole("button", { name: "Add link" }).click();
      await expect(section.locator(".link-row", { hasText: title })).toHaveCount(1);
    }
    await form.getByLabel("Address").fill(`https://www.patreon.com/e2e${stamp}`);
    await form.getByRole("button", { name: "Add link" }).click();
    // Socials first, then the others in the order added.
    const titles = section.locator(".link-row .link-row-title");
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "First", "Second"]);
    await expect(section.locator(".link-group-title")).toHaveText(["Socials", "Other", "Add a link"]);

    await section.getByRole("button", { name: "Move Second up" }).click();
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "Second", "First"]);
    await page.reload();
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "Second", "First"]);

    await section.getByRole("button", { name: "Edit First" }).click();
    const edit = section.locator(".link-row.is-editing");
    await edit.getByLabel("Title").fill("First, edited");
    await edit.getByRole("button", { name: "Save" }).click();
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "Second", "First, edited"]);
    await expectSafeAnchor(section.locator(".link-row", { hasText: "First, edited" }), `https://e2e-${stamp}.example/one`);

    await section.getByRole("button", { name: "Delete Second" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete link" }).click();
    await expect(titles).toHaveText([`www.patreon.com/e2e${stamp}`, "First, edited"]);
    await idle(page);
  } finally {
    // The person's links stay, tied to no one, once the person is gone: both cleared.
    const { links } = await (await page.request.get(`/api/links?person=${pid}`, { headers: H })).json();
    for (const l of links) await page.request.delete(`/api/links/${l.id}`, { headers: H });
    await page.request.delete(`/api/people/${pid}`, { headers: H });
  }
});
