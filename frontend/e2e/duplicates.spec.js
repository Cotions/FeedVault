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

// #149: copies groups are settled once the byte hashes are; the pass then
// fingerprints pictures for a long time. The page reloads its groups when
// the pass goes on to its next phase, not only when it ends, so Trash selected
// is offered as soon as it can be. Status and pending are changed in the
// browser only.
test("Duplicates: a group hashed during a pass can be kept before the pass ends", async ({ page }) => {
  const g = await stressGroup(page);
  let phase = "full";
  const status = { running: true, paused: false, done: 3, total: 9, bytes: 0, hashed: 9, fingerprinted: 0, errors: [] };
  await page.route(url => url.pathname === "/api/duplicates/status", route => route.fulfill({ json: { ...status, phase } }));
  await page.route(url => url.pathname === "/api/duplicates" && (url.searchParams.get("kind") || "copies") === "copies", async route => {
    const res = await route.fetch();
    const body = await res.json();
    if (phase === "full") for (const x of body.groups) { x.identical = null; x.pending = true; }
    await route.fulfill({ response: res, json: body });
  });
  await openPage(page, { name: "Duplicates", path: "/duplicates" });
  const keep = groupOf(page, g.copy).getByRole("button", { name: /^Trash selected/ });
  await expect(keep).toBeDisabled();
  await expect(keep).toHaveAttribute("title", "Wait until every file is hashed");
  phase = "dhash";                                   // whole files done: fingerprints next, still running
  await expect(keep).toBeEnabled({ timeout: 8_000 });
  await expect(page.locator(".dup-status")).toContainText("Fingerprinting pictures");
  await idle(page);
});

// A similar group of four carousels where one picture each matches and
// every other item is found nowhere else: by default only the matching
// pictures go, the posts and their other items stay. Trashing a post whole
// says what it loses and asks first. The group and the resolve are made up
// in the browser: nothing is trashed.
function carousels() {
  const members = [0, 1, 2, 3].map(n => ({
    id: `instagram:SIM${n}`, type: "post", post_id: `instagram:SIM${n}`,
    post: { id: `instagram:SIM${n}`, platform: "instagram", shortcode: `SIM${n}`, author: { handle: `poster${n}` } },
    folder: `/vault/poster${n}`, meta_path: `/vault/poster${n}/p.json`, thumb_url: null,
    items: Array.from({ length: 6 + n }, (_, i) => ({ idx: i + 1, kind: "image", size: 1_000_000, path: `/vault/poster${n}/p_${i + 1}.jpg`,
      hash: null, width: 1080, height: 1350, url: null, thumb_url: null })),
    files: 6 + n, bytes: (6 + n) * 1_000_000, saved_at: 1727500000, posted_at: 1727400000 + n, match: 2, kept: false,
    paths: [], author: `poster${n}`,
  }));
  const differs = members.flatMap(m => m.items.filter(i => i.idx !== 2).map(i => ({ member: m.id, idx: i.idx, reason: "only here" })));
  return { id: "f".repeat(20), kind: "similar", key: "k", members, identical: false, pending: false, differs, distance: 2,
    suggested: members[0].id, frees: 0, bytes: 0, repost: true };
}

test("Duplicates: trash just the matching pictures of a similar group, or a whole post after a warning", async ({ page }) => {
  const g = carousels();
  let sent = null;
  await page.route(url => url.pathname === "/api/duplicates" && url.searchParams.get("kind") === "similar", route =>
    route.fulfill({ json: { kind: "similar", threshold: 6, total: 1, reposts: 1, identical: 0, pending: 0, frees: 0,
      identical_frees: 0, dismissed: 0, groups: [g] } }));
  await page.route(url => url.pathname === "/api/duplicates/resolve", route => {
    sent = route.request().postDataJSON();
    return route.fulfill({ json: { ok: true, resolved: [g.id], skipped: [], posts: [], copies: [], media: [1, 2], files: 2,
      bytes: 2_000_000, errors: [] } });
  });
  await openPage(page, { name: "Duplicates", path: "/duplicates" });
  await page.getByRole("tab", { name: "Looks the same" }).click();
  const group = page.locator("section.dup-group");
  const cards = group.locator(".dup-member");
  await expect(cards).toHaveCount(4);
  await expect(cards.nth(0)).toHaveClass(/is-keep/);
  for (const n of [1, 2, 3]) await expect(cards.nth(n)).toHaveClass(/is-picture/);
  await expect(cards.nth(1).locator(".dup-badge-trash")).toHaveText("trash picture");
  const plan = group.locator(".dup-plan");
  await expect(plan).toHaveText("Trashes 3 pictures · 2.9 MB · loses nothing found nowhere else");
  const button = group.getByRole("button", { name: /^Trash selected/ });
  await expect(button).toHaveText("Trash selected · 2.9 MB");

  // the second post whole: 8 files, 7 of them found nowhere else
  await cards.nth(2).getByRole("button", { name: "Whole post" }).click();
  await expect(cards.nth(2)).toHaveClass(/is-post/);
  await expect(cards.nth(2).locator(".dup-badge-trash")).toHaveText("trash post · 8 files");
  await expect(plan).toHaveClass(/is-lossy/);
  await expect(plan).toHaveText("Trashes 2 pictures + 1 post · 9.5 MB · loses 7 items found nowhere else");
  await button.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("7 items found nowhere else");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  expect(sent).toBeNull();

  // every post whole: allowed, with a warning, and it asks first. Ticking the
  // kept one leaves no kept post for the pictures to match, so they read as
  // kept until ticked again, whole this time.
  await cards.nth(0).getByRole("checkbox").check();
  for (const n of [1, 3]) await cards.nth(n).getByRole("checkbox").check();
  for (const n of [0, 1, 2, 3]) await expect(cards.nth(n)).toHaveClass(/is-post/);
  await expect(plan).toHaveClass(/is-lossy/);
  await expect(plan).toHaveText("Every member is marked for the trash: the whole group goes · 4 posts · 28.6 MB");
  await expect(button).toBeEnabled();
  await expect(button).toHaveAttribute("title", /^Move 4 posts to the trash/);
  await button.click();
  await expect(dialog).toContainText("No member is kept: the whole group goes.");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  expect(sent).toBeNull();

  // Untick all: none ticked, nothing to send
  const tickAll = group.getByRole("button", { name: "Tick all", exact: true });
  const untickAll = group.getByRole("button", { name: "Untick all", exact: true });
  await expect(tickAll).toHaveCount(0);
  await untickAll.click();
  for (const n of [0, 1, 2, 3]) await expect(cards.nth(n).getByRole("checkbox")).not.toBeChecked();
  await expect(untickAll).toHaveCount(0);
  await expect(button).toBeDisabled();
  await expect(button).toHaveAttribute("title", "Tick the members to trash first");
  await expect(plan).toHaveText("Nothing marked for the trash: tick a member to trash it.");
  // Tick all: every post whole, then back to none
  await tickAll.click();
  for (const n of [0, 1, 2, 3]) await expect(cards.nth(n)).toHaveClass(/is-post/);
  await expect(plan).toHaveText("Every member is marked for the trash: the whole group goes · 4 posts · 28.6 MB");
  await untickAll.click();
  await expect(button).toBeDisabled();

  // two pictures: they go after the similar group's confirmation
  for (const n of [1, 3]) await cards.nth(n).getByRole("checkbox").check();
  await button.click();
  await page.getByRole("alertdialog").getByRole("button", { name: /^Move .* to the trash$/ }).click();
  await expect.poll(() => sent).not.toBeNull();
  expect(sent.groups).toEqual([{ group: g.id, trash: [{ member: "instagram:SIM1", items: [2] }, { member: "instagram:SIM3", items: [2] }] }]);
  expect(sent.threshold).toBe(6);
  await idle(page);
});
