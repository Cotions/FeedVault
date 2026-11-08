// Desktop, 1440x900: Links → Import many… (LinkImport.jsx), pasted
// addresses and an exported bookmarks file, all into Unsorted. Addresses
// are invented (.example); nothing is fetched, by FeedVault or the test.
import { test, expect, openPage, idle } from "./fixtures.js";

const H = { "X-FeedVault": "1" };

async function unsortedCount(request) {
  return (await (await request.get("/api/jobs", { headers: H })).json()).unsorted_links;
}

// The links on ``host``, deleted: the instance is shared with the other specs.
async function cleanUp(request, host) {
  const all = (await (await request.get("/api/links", { headers: H })).json()).links;
  const ids = all.filter(l => l.site === host).map(l => l.id);
  if (ids.length) await request.post("/api/links/delete", { headers: H, data: { ids } });
}

async function openImport(page) {
  await page.locator(".links-add").getByRole("button", { name: "Import many…" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Import links to Unsorted" });
  await expect(dialog).toBeVisible();
  // Nothing in it overflows the dialog, nor the dialog the window.
  const box = await dialog.boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(900);
  expect(await dialog.evaluate(d => d.scrollWidth <= d.clientWidth)).toBe(true);
  return dialog;
}

test("Import many: pasted addresses go to Unsorted", async ({ page, request }) => {
  const host = `e2e-paste-${Date.now()}.example`;
  await request.post("/api/links", { headers: H, data: { url: `https://${host}/old` } });
  try {
    const before = await unsortedCount(request);
    await openPage(page, { name: "Links", path: "/links" });
    const dialog = await openImport(page);
    await expect(dialog.getByRole("textbox", { name: "Addresses to import" })).toBeFocused();
    await expect(dialog.getByRole("button", { name: "Import", exact: true })).toBeDisabled();
    await dialog.getByRole("textbox", { name: "Addresses to import" }).fill(
      `First one - https://${host}/one\nhttps://${host}/two?utm_source=x https://${host}/old\n`);
    await dialog.getByRole("button", { name: "Import", exact: true }).click();
    await expect(dialog).toBeHidden();
    const toast = page.locator(".toast", { hasText: "2 saved to Unsorted, 1 already there." });
    await expect(toast).toBeVisible();
    await expect(page.locator("#main-nav a.side-unsorted")).toHaveText(String(before + 2));

    await toast.getByRole("link", { name: "Show Unsorted" }).click();
    await expect(page).toHaveURL(/\/links\?person=none$/);
    const rows = page.locator(".link-row", { hasText: host });
    await expect(rows).toHaveCount(3);
    await expect(page.locator(".link-row", { hasText: "First one" })).toHaveCount(1);
    await expect(page.locator(`.link-row a.link-row-title[href="https://${host}/two"]`)).toHaveCount(1);
    await idle(page);
  } finally {
    await cleanUp(request, host);
  }
});

test("Import many: an exported bookmarks file keeps titles and folders", async ({ page, request }) => {
  const host = `e2e-bookmarks-${Date.now()}.example`;
  const html = `<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1600000000" PERSONAL_TOOLBAR_FOLDER="true">Bookmarks bar</H3>
    <DL><p>
        <DT><H3 ADD_DATE="1600000000">Art</H3>
        <DL><p>
            <DT><A HREF="https://${host}/painter" ADD_DATE="1650000000">A painter &amp; friends</A>
        </DL><p>
        <DT><A HREF="https://${host}/shop" ADD_DATE="1660000000">Shop</A>
        <DT><A HREF="javascript:void(0)" ADD_DATE="1660000000">A bookmarklet</A>
    </DL><p>
</DL><p>
`;
  try {
    // Unsorted, searched for the test's host: bookmarks from 2022 are far
    // down a long list otherwise, out of the rendered rows.
    await page.goto(`/links?person=none&q=${host}`);
    await expect(page.getByRole("heading", { level: 2, name: "Links", exact: true })).toBeVisible();
    await expect(page.locator(".empty")).toHaveText("No link matches.");
    await idle(page);
    const dialog = await openImport(page);
    await dialog.locator('input[type="file"]').setInputFiles({
      name: "bookmarks.html", mimeType: "text/html", buffer: Buffer.from(html),
    });
    await expect(dialog.locator(".links-import-name")).toHaveText("bookmarks.html");
    await expect(dialog.getByRole("textbox", { name: "Addresses to import" })).toBeDisabled();
    await dialog.getByRole("button", { name: "Import the file" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.locator(".toast", { hasText: "2 saved to Unsorted, 1 skipped." })).toBeVisible();

    // In the Unsorted list already: newest bookmarked first.
    const titles = page.locator(".link-row", { hasText: host }).locator(".link-row-title");
    await expect(titles).toHaveText(["Shop", "A painter & friends"]);
    const links = (await (await request.get("/api/links?person=none", { headers: H })).json()).links
      .filter(l => l.site === host);
    expect(links.map(l => [l.title, l.created_at, l.notes])).toEqual([
      ["Shop", 1660000000, "Folder: Bookmarks bar"],
      ["A painter & friends", 1650000000, "Folder: Bookmarks bar / Art"],
    ]);
    await idle(page);
  } finally {
    await cleanUp(request, host);
  }
});
