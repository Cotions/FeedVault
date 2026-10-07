// Desktop, 1440x900: "Not a duplicate" can be taken back (#122), from the
// Undo toast or the Dismissed list, and Unmatched points a dismissed copy
// at that list instead of a group Duplicates no longer shows. The group is
// the --stress vault's STRESSpost01 and its copy in a deep folder; every
// test leaves it listed, as it found it.
import { test, expect, openPage, idle } from "./fixtures.js";
import { STRESS_POST } from "./stress.js";

const H = { "X-FeedVault": "1" };

// The copies group of STRESSpost01, through the instance's own API.
async function stressGroup(page) {
  const r = await page.request.get("/api/duplicates?kind=copies&limit=500", { headers: H });
  const g = (await r.json()).groups.find(x => x.members.some(m => m.type === "post" && m.post_id === STRESS_POST.id));
  expect(g, "the --stress vault has a copy of STRESSpost01").toBeTruthy();
  return { id: g.id, copy: g.members.find(m => m.type === "copy").meta_path };
}

async function dismissedIds(page) {
  const r = await page.request.get("/api/duplicates/dismissed", { headers: H });
  return (await r.json()).dismissed.map(d => d.id);
}

// Whatever a failed test left dismissed is listed again.
async function cleanUp(page, id) {
  await page.request.delete("/api/duplicates/dismiss", { headers: H, data: { group: id } });
}

const groupOf = (page, copy) => page.locator("section.dup-group", { has: page.locator(`.dup-folder[title="${copy}"]`) });

test("Duplicates: Not a duplicate, then Undo from the toast", async ({ page }) => {
  const g = await stressGroup(page);
  try {
    await openPage(page, { name: "Duplicates", path: "/duplicates" });
    const group = groupOf(page, g.copy);
    await expect(group).toHaveCount(1);
    await expect(page.locator("#dismissed")).toHaveCount(0);

    await group.getByRole("button", { name: "Not a duplicate" }).click();
    await expect(group).toHaveCount(0);
    expect(await dismissedIds(page)).toEqual([g.id]);
    const toast = page.locator(".toast", { hasText: "Marked not a duplicate." });
    await toast.getByRole("button", { name: "Undo" }).click();
    await expect(toast).toHaveCount(0);
    await expect(page.locator(".toast", { hasText: "Restored" })).toBeVisible();
    await expect(group).toHaveCount(1);
    await expect(page.locator("#dismissed")).toHaveCount(0);
    expect(await dismissedIds(page)).toEqual([]);
    await idle(page);
  } finally {
    await cleanUp(page, g.id);
  }
});

test("Duplicates: Not a duplicate, then Restore from the Dismissed list", async ({ page }) => {
  const g = await stressGroup(page);
  try {
    await openPage(page, { name: "Duplicates", path: "/duplicates" });
    const group = groupOf(page, g.copy);
    await group.getByRole("button", { name: "Not a duplicate" }).click();
    await expect(group).toHaveCount(0);
    // The toast's Undo is not used here: close it, the list is the other way back.
    await page.locator(".toast", { hasText: "Marked not a duplicate." }).getByRole("button", { name: "Dismiss" }).click();

    const list = page.locator("details#dismissed");
    await expect(list.locator("summary")).toHaveText("Dismissed (1)");
    await expect(list).not.toHaveAttribute("open", "");               // compact until opened
    await list.locator("summary").click();
    const row = list.locator(".dup-dismissed-row");
    await expect(row).toHaveCount(1);
    await expect(row.locator(".dup-dismissed-who")).toHaveAttribute("title", new RegExp(g.copy.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    await row.getByRole("button", { name: /^Restore / }).click();
    await expect(group).toHaveCount(1);
    await expect(list).toHaveCount(0);
    expect(await dismissedIds(page)).toEqual([]);
    await idle(page);
  } finally {
    await cleanUp(page, g.id);
  }
});

// #126: two copies in folders of one name, under different parents: each
// card's label shows the parent that tells them apart. The folders are
// renamed in the answer only: the vault is as it was.
test("Duplicates: copies in folders of one name show their parents", async ({ page }) => {
  const g = await stressGroup(page);
  const folders = ["/vault/2023/instagram/same.name", "/vault/2024/instagram/same.name"];
  await page.route(url => url.pathname === "/api/duplicates" && url.searchParams.get("kind") === "copies", async route => {
    const res = await route.fetch();
    const body = await res.json();
    for (const x of body.groups) if (x.id === g.id) x.members.forEach((m, i) => { m.folder = folders[i % 2]; });
    await route.fulfill({ response: res, json: body });
  });
  await openPage(page, { name: "Duplicates", path: "/duplicates" });
  const labels = groupOf(page, g.copy).locator(".dup-member-foot .dup-folder");
  await expect(labels).toHaveText(["2023/instagram/same.name/", "2024/instagram/same.name/"]);
  // Whole, not cut by the card's ellipsis.
  for (const w of await labels.evaluateAll(els => els.map(e => e.scrollWidth - e.clientWidth))) expect(w).toBeLessThanOrEqual(0);
  await idle(page);
});

test("Unmatched: a dismissed copy links to the Dismissed list, not to compare", async ({ page }) => {
  const g = await stressGroup(page);
  try {
    await openPage(page, { name: "Unmatched", path: "/unmatched" });
    const row = page.locator("tr", { has: page.locator(`td.path[title="${g.copy}"]`) });
    await expect(row.getByRole("link", { name: "compare in Duplicates" })).toHaveAttribute("href", "/duplicates");

    const r = await page.request.post("/api/duplicates/dismiss", { headers: H, data: { group: g.id } });
    expect(await r.json()).toEqual({ ok: true });
    await page.reload();
    await expect(row.getByRole("link", { name: "compare in Duplicates" })).toHaveCount(0);
    await expect(row.locator("td.reason")).toContainText("marked not a duplicate");
    const link = row.getByRole("link", { name: "restore in Duplicates" });
    await expect(link).toHaveAttribute("href", "/duplicates#dismissed");

    await link.click();
    const list = page.locator("details#dismissed");
    await expect(list).toHaveAttribute("open", "");                    // opened for us
    await expect(list.locator("summary")).toBeInViewport();
    await list.getByRole("button", { name: /^Restore / }).click();
    await expect(groupOf(page, g.copy)).toHaveCount(1);

    await page.goto("/unmatched");
    await expect(row.getByRole("link", { name: "compare in Duplicates" })).toHaveAttribute("href", "/duplicates");
    await idle(page);
  } finally {
    await cleanUp(page, g.id);
  }
});

// QA pass 3: while hashing runs, the groups listed are pending and the few
// identical ones are further down; the summary said "Select leaves reposts
// out" with no repost anywhere. It says what is going on instead. The
// answer is changed in the browser only.
test("Duplicates: while files are hashed, the summary says so, not that reposts are left out", async ({ page }) => {
  await page.route(url => url.pathname === "/api/duplicates" && (url.searchParams.get("kind") || "copies") === "copies", async route => {
    const res = await route.fetch();
    const body = await res.json();
    for (const g of body.groups) { g.identical = null; g.pending = true; g.repost = false; }
    await route.fulfill({ response: res, json: { ...body, identical: 4, identical_frees: 0, pending: body.groups.length, reposts: 0 } });
  });
  await openPage(page, { name: "Duplicates", path: "/duplicates" });
  const summary = page.locator(".dup-summary");
  await expect(summary).toContainText("4 identical groups would free 0 B. Load more to reach them.");
  await expect(summary).toContainText("still being hashed: Select takes");
  await expect(summary).not.toContainText("repost");
  await idle(page);
});
