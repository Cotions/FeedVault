// Desktop, 1440x900: the Trash page's History says what became of a
// deleted post and brings it back, and a restore on the trash's list shows
// at once. Each test deletes demo posts nobody else uses and leaves them
// restored, as it found them.
import { test, expect, openPage, idle, dismissToasts, stressData } from "./fixtures.js";

const H = { "X-FeedVault": "1" };
const href = p => `/p/${encodeURIComponent(p.platform)}/${encodeURIComponent(p.post_id)}`;
const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Plain pictures from the end of the feed: not the first posts the Feed and
// Review tests act on, nor the stress post and its copies.
async function spare(request, n) {
  const S = stressData();
  const { posts } = await (await request.get("/api/posts?limit=200", { headers: H })).json();
  const picked = posts.filter(p => p.kind === "image" && p.platform && p.post_id && p.author?.handle
                              && !/STRESS|SIM/.test(p.post_id) && p.post_id !== S?.post?.post_id).slice(-n);
  expect(picked, "the demo has spare pictures").toHaveLength(n);
  return picked;
}

async function trashPosts(request, posts) {
  const r = await (await request.post("/api/delete", { headers: H, data: { posts: posts.map(p => p.id), from: "feed" } })).json();
  expect(r.ok, JSON.stringify(r)).toBe(true);
}

// Whatever the test left in the trash goes back.
async function putBack(request, posts) {
  await request.post("/api/trash/restore", { headers: H, data: { posts: posts.map(p => p.id) } });
}

test("History: a deleted post shows as in the trash, comes back from there, and opens", async ({ page, request }) => {
  const [post] = await spare(request, 1);
  try {
    await page.goto(href(post));
    await expect(page.locator(".post-page-head")).toBeVisible();
    await page.getByRole("button", { name: "Delete post" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete post" }).click();
    await expect(page.getByRole("alertdialog")).toBeHidden();
    await idle(page);
    await dismissToasts(page);

    // From the Trash page's tab, kept in the URL.
    await openPage(page, { name: "Trash", path: "/trash" });
    await page.getByRole("tab", { name: "History" }).click();
    await expect(page).toHaveURL(/\/trash\?view=history$/);
    await expect(page.getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
    const day = page.locator(".trash-day").first();
    await expect(day.locator(".trash-day-head")).toHaveText("Today");
    // Newest first: the deletion just made.
    const row = page.locator(".trash-event").filter({ hasText: /^Deleted/ }).first();
    await expect(row.locator(".trash-event-title")).toHaveText("Deleted from its post page");
    await expect(row.locator(".trash-event-meta")).toContainText(`@${post.author.handle}`);
    await expect(row.locator(".trash-state")).toHaveText("In the trash");
    await expect(row.locator(".trash-event-thumb img")).toBeVisible();
    await idle(page);

    await row.getByRole("button", { name: `Restore @${post.author.handle}` }).click();
    await expect(row.locator(".trash-state")).toHaveText("Restored");
    const open = row.getByRole("link", { name: `Open @${post.author.handle}'s post` });
    await expect(open).toBeVisible();
    await expect(page.locator(".toast", { hasText: `Restored @${post.author.handle}'s post` }).getByRole("link", { name: "Open post" })).toBeVisible();
    // The restore's own event comes in on top.
    await expect(page.locator(".trash-event").first().locator(".trash-event-title")).toHaveText("Restored from the trash");
    await idle(page);

    await open.click();
    await expect(page).toHaveURL(new RegExp(`${esc(href(post))}$`));
    await expect(page.locator(".post-page-head")).toBeVisible();
    await idle(page);
  } finally {
    await putBack(request, [post]);
  }
});

test("Trash: a restore shows on its entry at once, the rest stays usable, and no list is loaded again", async ({ page, request }) => {
  const posts = await spare(request, 2);
  // Held until the test lets it go, to see the page while it runs.
  let release;
  const held = new Promise(resolve => { release = resolve; });
  try {
    await trashPosts(request, posts);
    await openPage(page, { name: "Trash", path: "/trash" });
    const count = page.locator(".page-head .page-count");
    const before = Number((await count.textContent()).match(/^[\d,]+/)[0].replace(/,/g, ""));
    // The newest deletions first: the two posts, in either order.
    const entries = page.locator(".trash-entry");
    const [a, b] = [entries.nth(0), entries.nth(1)];
    for (const e of [a, b]) {
      await expect(e.locator(".big-file-author")).toHaveText(new RegExp(`^@(${posts.map(p => esc(p.author.handle)).join("|")})$`));
      await expect(e.locator("img")).toBeVisible();
    }
    await idle(page);

    await page.route(url => url.pathname === "/api/trash/restore", async route => {
      await held;
      await route.continue();
    });
    let reloads = 0;
    page.on("request", r => { if (new URL(r.url()).pathname === "/api/trash/items") reloads += 1; });

    await a.getByRole("button", { name: /^Restore / }).click();
    await expect(a.locator(".trash-entry-state")).toHaveText("Restoring…");
    await expect(a.getByRole("button", { name: /^Restore / })).toBeDisabled();
    await expect(b.getByRole("button", { name: /^Restore / })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Select", exact: true })).toBeEnabled();

    release();
    await expect(a.locator(".trash-entry-state")).toHaveText("Restored");
    await expect(a.getByRole("link", { name: /^Open @.*'s post$/ })).toBeVisible();
    await expect(page.locator(".toast", { hasText: /^Restored @.*'s post/ }).getByRole("link", { name: "Open post" })).toBeVisible();
    // Counted out here, not by loading the list again.
    await expect(count).toHaveText(new RegExp(`^${(before - 1).toLocaleString("en-US")} entr`));
    await dismissToasts(page);

    // The other one, while the first stays listed as restored.
    await b.getByRole("button", { name: /^Restore / }).click();
    await expect(b.locator(".trash-entry-state")).toHaveText("Restored");
    await expect(a.locator(".trash-entry-state")).toHaveText("Restored");
    await expect(count).toHaveText(new RegExp(`^${(before - 2).toLocaleString("en-US")} entr`));
    await idle(page);
    expect(reloads, "GET /api/trash/items after the restores").toBe(0);
  } finally {
    release();
    await putBack(request, posts);
  }
});
