// Desktop state checks, at 1280x800 and 1440x900: what the resting layout
// checks (layout.spec.js) cannot see.
//  - Focus: Tab (then Shift+Tab back) through every page in the nav, a
//    post page and a person's page. Each stop shows a ring (an outline or
//    box-shadow that differs from its unfocused look), not cut off by an
//    overflow ancestor, not under the sticky header or the selection bar,
//    and never on something hidden; and Tab never drops focus on <body>
//    before the page's last stop.
//  - Hover: one element of each kind in sight on those pages. Its box and
//    its neighbours' stay put (one with a hover lift moves instead, and
//    stays put under reduced motion), and whatever opens on hover stays in the
//    window, clear of the bars. The popovers that open on a click
//    (creator picker, tag suggestions, notifications, the selection bar's
//    dialogs) too, at rest and with their field low in the window.
//  - Scrolled: the Feed (and in select mode), a creator page, Storage and
//    Jobs, halfway and at the bottom: nothing a click can reach sits under
//    a bar, and what is in the open takes its clicks. Jumps (a #card link,
//    Shift+Tab, Review's next post) land below the header.
// The probes run in the page (fixtures.js, installProbes); STATE_ALLOW
// lists what they let through, with a reason each.
import fs from "node:fs";
import path from "node:path";
import {
  test, expect, PAGES, stressData, settle, frame, quiet, addProbes, STATE_ALLOW, formatStateFindings,
  fakeDecisions, underToasts, dismissToasts,
} from "./fixtures.js";

const SIZES = [
  { label: "1280x800", width: 1280, height: 800 },
  { label: "1440x900", width: 1440, height: 900 },
];
const MAX_STOPS = 40;        // Tab presses per page and size: the Feed's cards go on
const BACK_STOPS = 10;       // then Shift+Tab, which scrolls up into the header's way

// Read when a test runs: global-setup sets the stress data after the
// specs are loaded (and listing them needs none).
const creatorUrl = () => { const S = stressData(); return `/?platform=${S.post.platform}&author=${encodeURIComponent(S.author.id)}`; };
const postUrl = () => { const S = stressData(); return `/p/${S.post.platform}/${S.post.post_id}`; };
const personUrl = () => `/people/${stressData().person}`;

async function openUrl(page, url, ready) {
  await page.goto(url);
  await quiet(page, ready);
}
// A page in the nav: its link is the current one and its title shows.
const READY = { Review: ".review-left" };
async function open(page, name) {
  const p = PAGES.find(x => x.name === name);
  await page.goto(p.path);
  await expect(page.locator(`#main-nav a.side-link[href="${p.path}"]`)).toHaveAttribute("aria-current", "page");
  await quiet(page, READY[name] || `h2.page-title:text-is("${name}"), h2:text-is("${name}")`);
}

const VIEWS = [
  ...PAGES.map(p => ({ name: p.name, open: page => open(page, p.name) })),
  { name: "Post", open: page => openUrl(page, postUrl(), ".post-page-head") },
  // Accounts, the link-in-bio import, Sources, Links: with Mute loaded.
  { name: "Person", open: page => openUrl(page, personUrl(), ".person-new button") },
];

test.beforeEach(async ({ page }) => { await addProbes(page); });

async function report(testInfo, found) {
  if (found.length) {
    const file = testInfo.outputPath("findings.json");
    fs.writeFileSync(file, JSON.stringify(found, null, 1));
    await testInfo.attach("findings.json", { path: file, contentType: "application/json" });
  }
  expect(found.length, `state findings:\n${formatStateFindings(found)}`).toBe(0);
}

async function atSize(page, s) {
  await page.setViewportSize({ width: s.width, height: s.height });
  await settle(page);
}

const HOVERABLE = "a[href], button, summary, [role=button], .post-card, .creator-card, .collection-card, .big-file, "
  + ".tag-row, .theme-card, .stats-hero-cell, .data-table tbody tr, .channel-bar-row, .settings-tab";
const MAX_HOVER = 24;

// Tab, then Shift+Tab, through the page: the findings of each stop, once
// per element and rule.
async function walkFocus(page, view, size) {
  const found = [], seen = new Set();
  let first = null, last = null, lost = null;
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  const step = async key => {
    await page.keyboard.press(key);
    // ``after``: this stop comes after the previous one in the document.
    const stop = await page.evaluate(allow => {
      const prev = document.querySelector("[data-fv-last-stop]");
      const out = window.__fv.focusStop(allow);
      if (!out) return null;
      const el = document.activeElement;
      const after = !prev || !!(prev.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
      prev?.removeAttribute("data-fv-last-stop");
      el.setAttribute("data-fv-last-stop", "");
      return { ...out, after };
    }, STATE_ALLOW);
    // Focus on <body>: past the page's last stop, or dropped by the one
    // before (a stop that went away as it lost focus). The next Tab tells
    // which: round to the top of the page, or on further down.
    if (!stop) { if (key === "Tab") lost = last; return true; }
    if (key === "Tab" && lost && stop.after) {
      found.push({ view, size, state: key, rule: "focus-lost", detail: `Tab from ${lost.sel} "${lost.text}" left focus on <body>`, a: stop });
    }
    lost = null;
    last = stop;
    const id = `${stop.sel}|${stop.text}|${stop.box.x},${stop.box.y + stop.scrollY}`;
    if (key === "Tab" && id === first) return false;             // round the page and back
    first ??= id;
    for (const p of stop.problems) {
      const k = `${p.rule}|${stop.sel}|${stop.text}`;
      if (seen.has(k)) continue;
      seen.add(k);
      found.push({ view, size, state: key, ...p, a: stop });
    }
    return true;
  };
  for (let i = 0; i < MAX_STOPS && await step("Tab"); i++);
  for (let i = 0; i < BACK_STOPS; i++) await step("Shift+Tab");
  await page.evaluate(() => document.activeElement?.blur());
  return found;
}

// The mouse on one element of each kind in sight, in turn. An element a
// style sheet gives a hover transform (a lift) must move on hover, its box
// or its computed transform, and must not under reduced motion (#94).
// ``lifts``: when given, counts the lifts checked.
async function walkHover(page, view, size, { reduced = false, lifts = null } = {}) {
  const found = [];
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelectorAll("[data-fv-hover]").forEach(e => delete e.dataset.fvHover); });
  await page.mouse.move(1, size.height - 1);                      // the sidebar's empty foot
  const n = await page.evaluate(([sel, max]) => window.__fv.markHoverTargets(sel, max), [HOVERABLE, MAX_HOVER]);
  for (let i = 0; i < n; i++) {
    const before = await page.evaluate(([k, allow]) => window.__fv.hoverBoxes(k, allow), [i, STATE_ALLOW]);
    if (!before) continue;
    const self = before.boxes.find(b => b.self).box;
    await page.mouse.move(self.x + self.width / 2, self.y + self.height / 2);
    await frame(page);
    const after = await page.evaluate(([k, allow]) => window.__fv.hoverBoxes(k, allow), [i, STATE_ALLOW]);
    if (!after) continue;
    const diff = (x, y) => Math.max(Math.abs(x.x - y.x), Math.abs(x.y - y.y), Math.abs(x.width - y.width), Math.abs(x.height - y.height));
    if (before.lifts) {
      if (lifts) lifts.count++;
      const moved = diff(self, after.boxes.find(b => b.self).box) > 0.5 || before.transform !== after.transform;
      if (moved === reduced) {
        found.push({ view, size: size.label, state: reduced ? "hover, reduced motion" : "hover", rule: reduced ? "hover-lift-reduced" : "hover-lift-dead",
          detail: reduced ? `it moves on hover under reduced motion (transform ${after.transform})`
            : `it declares a hover transform but does not move (transform ${after.transform})`, a: before });
      }
    }
    for (let b = 0; b < before.boxes.length && b < after.boxes.length; b++) {
      const d = diff(before.boxes[b].box, after.boxes[b].box);
      if (d > 0.5 && !(before.boxes[b].self && (before.allowMove || before.lifts))) {
        found.push({ view, size: size.label, state: "hover", rule: "hover-moved",
          detail: `${before.boxes[b].self ? "the element" : `its neighbour ${before.boxes[b].sel}`} moved ${Math.round(d * 10) / 10}px`, a: before });
        break;
      }
    }
    // Opened on hover: in the window and clear of the bars.
    for (const f of after.floats.filter(f => !before.floats.includes(f))) {
      const probs = await page.evaluate(q => window.__fv.popover(q), `[data-fv-float="${f}"]`);
      found.push(...probs.map(p => ({ view, size: size.label, state: `hover over "${before.text}"`, ...p })));
    }
  }
  await page.mouse.move(1, size.height - 1);
  return found;
}

test.describe("focus and hover", () => {
  for (const v of VIEWS) {
    test(v.name, async ({ page }, testInfo) => {
      await v.open(page);
      const found = [];
      for (const s of SIZES) {
        await atSize(page, s);
        found.push(...await walkFocus(page, v.name, s.label));
        // Hover does not depend on the width: at the larger size only, for CI time.
        if (s === SIZES[SIZES.length - 1]) found.push(...await walkHover(page, v.name, s));
      }
      await report(testInfo, found);
    });
  }
});

// The collection card's lift (#94): the walk above checks every lift it
// meets; here, that there is one to check, and that reduced motion stops it.
test("a hover lift moves, and stays put under reduced motion", async ({ page }, testInfo) => {
  const found = [];
  for (const reduced of [false, true]) {
    await page.emulateMedia({ reducedMotion: reduced ? "reduce" : "no-preference" });
    await open(page, "Collections");
    await atSize(page, SIZES[SIZES.length - 1]);
    const lifts = { count: 0 };
    found.push(...await walkHover(page, "Collections", SIZES[SIZES.length - 1], { reduced, lifts }));
    expect(lifts.count, `hover lifts checked${reduced ? " under reduced motion" : ""}`).toBeGreaterThan(0);
  }
  await report(testInfo, found);
});

// Popovers opened by a click: in the window, clear of the bars, at rest and
// with their field scrolled low in the window (they must turn upward).
const POPOVERS = [
  {
    name: "Feed › creator picker",
    open: async page => {
      await open(page, "Feed");
      await page.locator(".feed-filters .picker-input").first().click();
    },
    pop: ".picker-list",
  },
  {
    name: "Review › tag suggestions",
    open: async page => {
      await open(page, "Review");
      await page.keyboard.press("t");
      await page.getByRole("combobox", { name: "Tag this post…" }).fill("a");
    },
    pop: ".tag-suggest",
  },
  {
    name: "Post › tag suggestions, field low in the window",
    open: async page => {
      // A post with few tags (the stress post has them all): the first in the Feed.
      await open(page, "Feed");
      await page.goto(await page.locator("article.post-card a.post-cover, article.post-card a.post-textbody").first().getAttribute("href"));
      const field = page.locator(".post-tags .tag-input input");
      await expect(field).toBeVisible();
      await field.evaluate(el => {
        // Room above the field for the list, none below: it has to turn up.
        const r = el.getBoundingClientRect();
        window.scrollBy(0, r.bottom - innerHeight + 8);
      });
      await field.click();
      await page.keyboard.press("ArrowDown");          // every tag the post has not
    },
    pop: ".tag-suggest",
  },
  {
    name: "Notifications",
    open: async page => {
      await open(page, "Feed");
      await page.locator(".side-bell > button").click();
    },
    pop: ".notif-panel",
  },
  {
    name: "Selection bar › Tag… and its suggestions",
    open: async page => {
      await openUrl(page, creatorUrl(), "article.post-card");
      await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().click();
      await page.locator("article.post-card").first().click();
      await page.getByRole("button", { name: /^Tag…/ }).click();
      await page.locator(".modal-overlay input").first().fill("a");
    },
    pop: ".tag-suggest",
    also: [".modal-overlay .modal"],
  },
  {
    name: "Selection bar › Collection…",
    open: async page => {
      await openUrl(page, creatorUrl(), "article.post-card");
      await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().click();
      await page.locator("article.post-card").first().click();
      await page.getByRole("button", { name: /^Collection…/ }).click();
    },
    pop: ".modal-overlay .modal",
  },
];

test.describe("popovers", () => {
  for (const p of POPOVERS) {
    test(p.name, async ({ page }, testInfo) => {
      const found = [];
      // The smaller window only (the tighter fit), for CI time.
      for (const s of SIZES.slice(0, 1)) {
        await page.setViewportSize({ width: s.width, height: s.height });
        await p.open(page);
        await expect(page.locator(p.pop).first()).toBeVisible();
        await settle(page);
        for (const sel of [p.pop, ...(p.also || [])]) {
          const probs = await page.evaluate(q => window.__fv.popover(q), sel);
          found.push(...probs.map(x => ({ view: p.name, size: s.label, ...x })));
        }
      }
      await report(testInfo, found);
    });
  }
});

const SCROLLED = [
  { name: "Feed", open: page => open(page, "Feed") },
  {
    name: "Feed, selecting",
    open: async page => {
      await open(page, "Feed");
      await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().click();
      await page.locator("article.post-card").first().click();
      await expect(page.locator(".select-bar")).toBeVisible();
    },
  },
  { name: "Creator", open: page => openUrl(page, creatorUrl(), "article.post-card") },
  { name: "Storage", open: page => open(page, "Storage") },
  { name: "Jobs › History", open: page => open(page, "Jobs") },
];

// Select mode pins its bar to the window's bottom: Tab must keep the
// focused card clear of it (the Feed's first cards, one size).
test("focus in select mode stays clear of the selection bar", async ({ page }, testInfo) => {
  const s = SIZES[0];
  await atSize(page, s);
  await SCROLLED.find(v => v.name === "Feed, selecting").open(page);
  const found = (await walkFocus(page, "Feed, selecting", s.label)).filter(f => f.state === "Tab");
  await report(testInfo, found);
});

test.describe("scrolled", () => {
  for (const v of SCROLLED) {
    test(v.name, async ({ page }, testInfo) => {
      await v.open(page);
      const found = [];
      for (const s of SIZES) {
        await atSize(page, s);
        for (const [state, to] of [["halfway", 0.5], ["bottom", 1]]) {
          await page.evaluate(f => window.scrollTo(0, (document.documentElement.scrollHeight - innerHeight) * f), to);
          await quiet(page, null);                             // the Feed loads more at its end
          await settle(page);
          const probs = await page.evaluate(allow => window.__fv.covered(allow), STATE_ALLOW);
          found.push(...probs.map(x => ({ view: v.name, size: s.label, state, ...x })));
        }
      }
      await report(testInfo, found);
    });
  }
});

test.describe("jumps land below the header", () => {
  test("a link to a Settings card", async ({ page }) => {
    for (const s of SIZES) {
      await page.setViewportSize({ width: s.width, height: s.height });
      await openUrl(page, "/settings#downloaders", "#downloaders");
      await settle(page);
      const [card, bars] = await Promise.all([
        page.locator("#downloaders").evaluate(el => el.getBoundingClientRect().top),
        page.evaluate(() => window.__fv.bars()),
      ]);
      expect(await page.evaluate(() => scrollY), `${s.label}: the page scrolled to the card`).toBeGreaterThan(0);
      const header = bars.find(b => b.kind === "header");
      expect(card, `${s.label}: #downloaders below the header`).toBeGreaterThanOrEqual(header.y + header.h);
    }
  });

  test("a Settings tab clicked low on a long tab", async ({ page }) => {
    for (const s of SIZES) {
      await page.setViewportSize({ width: s.width, height: s.height });
      await openUrl(page, "/settings#downloads", ".settings-tab[aria-current=page]");
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      await page.locator('.settings-tab[href$="#about"]').click();
      await settle(page);
      const top = await page.locator(".settings-layout > :not(.settings-tabs)").first().evaluate(el => el.getBoundingClientRect().top);
      const header = (await page.evaluate(() => window.__fv.bars())).find(b => b.kind === "header");
      expect(top, `${s.label}: the tab's first card starts below the header`).toBeGreaterThanOrEqual(header.y + header.h);
    }
  });

  test("Review's next post starts at the top of its panel", async ({ page }) => {
    for (const s of SIZES) {
      await page.setViewportSize({ width: s.width, height: s.height });
      await openUrl(page, `/review${creatorUrl().slice(1)}`, ".review-open");
      // A post whose details scroll (the stress post's 15 tags), scrolled down.
      const info = page.locator(".review-info");
      const first = await page.locator(".review-open").getAttribute("href");
      await info.evaluate(el => { el.scrollTop = el.scrollHeight; });
      expect(await info.evaluate(el => el.scrollTop), `${s.label}: the first post's details scroll`).toBeGreaterThan(0);
      await page.keyboard.press("l");
      await expect(page.locator(".review-open")).not.toHaveAttribute("href", first);
      await settle(page);
      expect(await info.evaluate(el => el.scrollTop), `${s.label}: the next post's details start at their top`).toBe(0);
      await page.keyboard.press("j");
      await expect(page.locator(".review-open")).toHaveAttribute("href", first);
      await settle(page);
      expect(await info.evaluate(el => el.scrollTop), `${s.label}: back to the first post, at its top`).toBe(0);
    }
  });
});

// A dialog the selection bar opens, closed with Cancel (Close) or Esc,
// gives focus back to its button and leaves the page where it was, and so
// does Tab along the bar (#119: the button sits in the room scroll-padding
// keeps for the bar, and each close scrolled the Feed down by that room).
test.describe("closing a selection bar dialog leaves the Feed where it was", () => {
  for (const name of ["Tag…", "Collection…", "Delete…"]) {
    test(name, async ({ page }) => {
      await atSize(page, SIZES[1]);
      await open(page, "Feed");
      await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().click();
      await page.locator("article.post-card").first().click();
      const button = page.locator(".select-bar").getByRole("button", { name, exact: true });
      await expect(button).toBeEnabled();
      await settle(page);
      const before = await page.evaluate(() => scrollY);
      // Clicked where it is, as a mouse does: Playwright's click() scrolls
      // its target into view first, and that is the very scroll measured.
      const box = await button.boundingBox();
      for (const close of ["Escape", "Cancel", "Escape"]) {
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        const dialog = page.locator(".modal-overlay .modal");
        await expect(dialog).toBeVisible();
        if (close === "Escape") await page.keyboard.press("Escape");
        else await dialog.getByRole("button", { name: /^(Cancel|Close)$/ }).click();
        await expect(dialog).toHaveCount(0);
        await expect(button).toBeFocused();
        await settle(page);
        expect(await page.evaluate(() => scrollY), `${name} closed with ${close}: the Feed did not scroll`).toBe(before);
      }
      // Nor does Tab along the bar, either way.
      for (const key of ["Shift+Tab", "Tab"]) {
        await page.keyboard.press(key);
        await settle(page);
        expect(await page.evaluate(() => scrollY), `${key} from ${name}: the Feed did not scroll`).toBe(before);
      }
      await expect(button).toBeFocused();
    });
  }
});

// Focus never falls onto <body> (#136): when the control that has it turns
// off (a busy flag) or goes away, it stays on it or moves somewhere that
// makes sense, so the next key still does something. Each test makes what
// it changes (collections, a job) and removes it after.
const H = { "X-FeedVault": "1" };
const onBody = page => page.evaluate(() => !document.activeElement || document.activeElement === document.body);

async function api(request, method, url, data) {
  const r = await request.fetch(url, { method, headers: H, ...(data ? { data } : {}) });
  expect(r.ok(), `${method} ${url}: ${r.status()}`).toBe(true);
  return r.json();
}

test.describe("focus stays off <body>", () => {
  test("the Feed's Collection… dialog: a picked collection keeps it, and Esc closes", async ({ page, request }) => {
    const name = `E2E focus pick ${Date.now()}`;
    const { collection } = await api(request, "POST", "/api/collections", { name });
    try {
      await atSize(page, SIZES[1]);
      await open(page, "Feed");
      await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().click();
      for (const n of [0, 1, 2]) await page.locator("article.post-card").nth(n).click();
      const button = page.locator(".select-bar").getByRole("button", { name: "Collection…", exact: true });
      const dialog = page.locator(".modal-overlay .modal");
      await button.click();
      const item = dialog.locator(".collection-pick-item", { hasText: name });
      await item.focus();
      await page.keyboard.press("Enter");
      await expect(page.locator(".toast", { hasText: `added to “${name}”` })).toBeVisible();
      await expect(item).toHaveAttribute("aria-pressed", "true");
      await expect(item, "the picked collection keeps focus").toBeFocused();
      await page.keyboard.press("Escape");
      await expect(dialog, "Esc closes the dialog after a pick").toHaveCount(0);
      await expect(button).toBeFocused();

      // Should focus get onto <body> some other way, Esc still closes it.
      await button.click();
      await expect(dialog).toBeVisible();
      await page.evaluate(() => document.activeElement?.blur());
      expect(await onBody(page)).toBe(true);
      await page.keyboard.press("Escape");
      await expect(dialog, "Esc with focus on <body> closes the dialog").toHaveCount(0);
      await expect(page.locator(".select-bar"), "and only the dialog: select mode stays").toBeVisible();
    } finally {
      await api(request, "POST", `/api/collections/${collection.id}/delete`);
    }
  });

  test("Move later twice in a row, inside a collection", async ({ page, request }) => {
    const { collection } = await api(request, "POST", "/api/collections", { name: `E2E focus order ${Date.now()}` });
    try {
      const ids = (await api(request, "GET", "/api/posts?limit=3")).posts.map(p => p.id);
      await api(request, "POST", `/api/collections/${collection.id}/add`, { posts: ids });
      await atSize(page, SIZES[1]);
      await openUrl(page, `/collections/${collection.id}`, ".collection-tile");
      const tiles = page.locator(".collection-tile");
      await expect(tiles).toHaveCount(3);
      const href = await tiles.first().locator(".collection-tile-media").getAttribute("href");
      const moved = tiles.filter({ has: page.locator(`.collection-tile-media[href="${href}"]`) });
      const later = moved.getByRole("button", { name: "Move later" });
      await later.focus();
      for (const at of [1, 2]) {
        const saved = page.waitForResponse(r => r.url().endsWith(`/api/collections/${collection.id}/order`));
        await page.keyboard.press("Enter");
        await saved;
        await expect(tiles.nth(at).locator(".collection-tile-media"), `moved to ${at}`).toHaveAttribute("href", href);
        await expect(later, `after move ${at}: on the moved post's Move later`).toBeFocused();
      }
      const { posts } = await api(request, "GET", `/api/collections/${collection.id}`);
      expect(posts.map(p => p.id)).toEqual([ids[1], ids[2], ids[0]]);
    } finally {
      await api(request, "POST", `/api/collections/${collection.id}/delete`);
    }
  });

  test("Move later twice in a row, on Collections", async ({ page, request }) => {
    const stamp = Date.now();
    const made = [];
    try {
      for (const n of ["A", "B", "C"]) made.push((await api(request, "POST", "/api/collections", { name: `E2E focus ${n} ${stamp}` })).collection);
      const order = async () => (await api(request, "GET", "/api/collections")).map(c => c.id);
      const before = await order();
      // The first of them, with the other two after it.
      const c = made.slice().sort((a, b) => before.indexOf(a.id) - before.indexOf(b.id))[0];
      const from = before.indexOf(c.id);
      expect(from + 2).toBeLessThan(before.length);
      await atSize(page, SIZES[1]);
      await open(page, "Collections");
      const later = page.getByRole("button", { name: `Move ${c.name} later`, exact: true });
      await later.focus();
      for (const step of [1, 2]) {
        const saved = page.waitForResponse(r => r.url().endsWith("/api/collections/reorder"));
        await page.keyboard.press("Enter");
        await saved;
        await expect.poll(async () => (await order()).indexOf(c.id), `move ${step}`).toBe(from + step);
        await expect(later, `after move ${step}: on its Move later`).toBeFocused();
      }
    } finally {
      for (const c of made) await api(request, "POST", `/api/collections/${c.id}/delete`);
    }
  });

  // A sync of the demo's night.tram, held at its start by the fake
  // instaloader's gate (it waits until that file exists, and it never
  // does) until it is cancelled: nothing is written.
  test("cancelling a running job", async ({ page, request }) => {
    const { data_directory } = await api(request, "GET", "/api/config");
    const vault = path.dirname(data_directory);
    const fake = path.join(vault, "fake_instaloader.json");
    const kept = fs.readFileSync(fake, "utf8");
    let jobId = null;
    try {
      fs.writeFileSync(fake, JSON.stringify({ ...JSON.parse(kept), gate: path.join(vault, "e2e-gate-never-made") }));
      await api(request, "POST", "/api/config", { instaloader: { pause: 0 } });
      const { sources } = await api(request, "GET", "/api/sources");
      const src = sources.find(s => s.target === "night.tram");
      expect(src, "the demo's night.tram source").toBeTruthy();
      const r = await api(request, "POST", `/api/sources/${src.id}/sync`, {});
      jobId = r.job.id;
      await expect.poll(async () => (await api(request, "GET", `/api/jobs/${jobId}`)).state, { timeout: 20_000 }).toBe("running");

      await atSize(page, SIZES[1]);
      await open(page, "Jobs");
      const row = page.locator(".job-row", { hasText: "night.tram" });
      await expect(row).toHaveCount(1);
      await row.getByRole("button", { name: "Cancel…" }).focus();
      await page.keyboard.press("Enter");
      const dialog = page.locator(".modal-overlay .modal");
      await dialog.getByRole("button", { name: "Cancel job" }).focus();
      await page.keyboard.press("Enter");
      await expect(dialog).toHaveCount(0);
      await expect(row).toHaveCount(0, { timeout: 20_000 });
      expect((await api(request, "GET", `/api/jobs/${jobId}`)).state).toBe("cancelled");
      await expect.poll(() => onBody(page), "focus after the cancelled job's row went").toBe(false);
      await expect(page.getByRole("region", { name: "Running and queued jobs" })).toBeFocused();
    } finally {
      fs.writeFileSync(fake, kept);
      if (jobId != null) {
        const j = await (await request.get(`/api/jobs/${jobId}`, { headers: H })).json();
        if (!["done", "failed", "cancelled", "interrupted"].includes(j.state)) {
          await request.post(`/api/jobs/${jobId}/cancel`, { headers: H });
        }
      }
    }
  });

  test("closing Notifications gives focus back to its button", async ({ page }) => {
    await atSize(page, SIZES[1]);
    await open(page, "Feed");
    const bell = page.locator(".side-bell > button");
    const panel = page.locator(".notif-panel");
    for (const key of ["Enter", "Escape"]) {
      await bell.click();
      await expect(panel).toBeVisible();
      await panel.getByRole("button", { name: "Close" }).focus();
      await page.keyboard.press(key);       // Enter on its Close, or Esc
      await expect(panel).toHaveCount(0);
      await expect(bell, `closed with ${key}`).toBeFocused();
    }
  });

  // #139: Use as cover goes once used, Remove takes its tile.
  test("Use as cover and Remove, inside a collection", async ({ page, request }) => {
    const { collection } = await api(request, "POST", "/api/collections", { name: `E2E focus cover ${Date.now()}` });
    try {
      const ids = (await api(request, "GET", "/api/posts?limit=3")).posts.map(p => p.id);
      await api(request, "POST", `/api/collections/${collection.id}/add`, { posts: ids });
      await atSize(page, SIZES[1]);
      await openUrl(page, `/collections/${collection.id}`, ".collection-tile");
      const tiles = page.locator(".collection-tile");
      await expect(tiles).toHaveCount(3);
      const second = tiles.nth(1);
      const href = await second.locator(".collection-tile-media").getAttribute("href");
      await second.getByRole("button", { name: "Use as cover" }).focus();
      const covered = page.waitForResponse(r => r.url().endsWith(`/api/collections/${collection.id}/cover`));
      await page.keyboard.press("Enter");
      await covered;
      await expect(second.locator(".collection-cover-badge")).toBeVisible();
      await expect(second.getByRole("button", { name: "Use as cover" })).toHaveCount(0);
      await expect(second.locator(".collection-tile-media"), "after Use as cover: on its tile's picture").toBeFocused();

      // Remove the first: focus goes to the next tile's Remove; the last, to the one before.
      const third = await tiles.nth(2).locator(".collection-tile-media").getAttribute("href");
      await tiles.first().getByRole("button", { name: "Remove from collection" }).focus();
      await page.keyboard.press("Enter");
      await expect(tiles).toHaveCount(2);
      const onTile = h => tiles.filter({ has: page.locator(`.collection-tile-media[href="${h}"]`) });
      await expect(onTile(href).getByRole("button", { name: "Remove from collection" })).toBeFocused();
      await onTile(third).getByRole("button", { name: "Remove from collection" }).focus();
      await page.keyboard.press("Enter");
      await expect(tiles).toHaveCount(1);
      await expect(onTile(href).getByRole("button", { name: "Remove from collection" })).toBeFocused();
      // The very last: the posts' card.
      await page.keyboard.press("Enter");
      await expect(tiles).toHaveCount(0);
      await expect.poll(() => onBody(page), "focus after the last tile went").toBe(false);
      await expect(page.getByRole("region", { name: "Posts in this collection" })).toBeFocused();
    } finally {
      await api(request, "POST", `/api/collections/${collection.id}/delete`);
    }
  });

  // #139: an entry's link goes with the panel; focus lands on the page it
  // opened. The entry is faked (a failed sync with no source: Creators).
  test("following a Notifications entry puts focus on the new page", async ({ page }) => {
    await page.route(url => url.pathname === "/api/notifications", route => route.fulfill({ json: {
      entries: [{ id: 99001, kind: "failed", text: "Sync of @e2e.gone failed", at: Date.now() / 1000 - 60, read: true, scheduled: false }],
      unread: 0,
    } }));
    await atSize(page, SIZES[1]);
    await open(page, "Feed");
    await page.locator(".side-bell > button").click();
    const entry = page.locator(".notif-panel .notif-entry");
    await entry.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/creators$/);
    await expect(page.locator(".notif-panel")).toHaveCount(0);
    await expect.poll(() => onBody(page), "focus after the entry's link went").toBe(false);
    await expect(page.locator("main")).toBeFocused();
    // The next Tab goes into the page, not back to the document's start.
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => document.querySelector("main").contains(document.activeElement))).toBe(true);
  });

  // #136, #139: in select mode, Space on a focused card's link toggles the
  // card, as Enter does, instead of scrolling the page.
  test("Space toggles a focused Feed card in select mode", async ({ page }) => {
    await atSize(page, SIZES[1]);
    await open(page, "Feed");
    await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().click();
    const card = page.locator("article.post-card").nth(1);
    const check = card.locator(".select-check");
    await expect(check).toHaveAttribute("aria-checked", "false");
    await card.locator("a").first().focus();
    const y = await page.evaluate(() => window.scrollY);
    await page.keyboard.press(" ");
    await expect(check).toHaveAttribute("aria-checked", "true");
    expect(await page.evaluate(() => window.scrollY), "the page did not scroll").toBe(y);
    await page.keyboard.press(" ");
    await expect(check).toHaveAttribute("aria-checked", "false");
    // Its checkbox, a button, toggles once per Space.
    await check.focus();
    await page.keyboard.press(" ");
    await expect(check).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("Escape");
  });
});

// Toasts never cover the buttons pinned at the bottom: the selection
// bar's right after a bulk action, Review's after an undo (#121). The
// decisions are faked: nothing changes on the server.
const TOAST_SIZES = [
  { label: "1280x800", width: 1280, height: 800 },
  { label: "1440x900", width: 1440, height: 900 },
  { label: "1920x1080", width: 1920, height: 1080 },
];

test.describe("toasts stay clear of the bottom buttons", () => {
  test("Feed: the selection bar after Keep", async ({ page }) => {
    await fakeDecisions(page);
    await open(page, "Feed");
    await page.locator(".feed-filters .select-toggle", { hasText: /Select|Done/ }).last().click();
    await expect(page.locator(".select-bar")).toBeVisible();
    const keep = page.locator(".select-bar").getByRole("button", { name: "Keep", exact: true });
    for (const s of TOAST_SIZES) {
      await atSize(page, s);
      // Two toasts: the second keep comes while the first shows.
      for (const n of [1, 2]) {
        await page.locator("article.post-card").nth(n).click();
        await keep.click();
        await expect(page.locator(".toast")).toHaveCount(n);
      }
      await settle(page);
      expect(await underToasts(page, ".select-bar button"), `${s.label}: buttons under a toast`).toEqual([]);
      await dismissToasts(page);
    }
  });

  test("Review: its buttons after Undo", async ({ page }) => {
    await fakeDecisions(page);
    await open(page, "Review");
    const bar = page.locator(".review-actions");
    const undo = bar.getByRole("button", { name: /^Undo/ });
    for (const s of TOAST_SIZES) {
      await atSize(page, s);
      await bar.getByRole("button", { name: /^Keep/ }).click();
      await expect(page.locator(".review-session")).toHaveText(/^1 kept/);
      await undo.click();
      await expect(page.locator(".toast", { hasText: "Undone." })).toBeVisible();
      await settle(page);
      expect(await underToasts(page, ".review-actions button"), `${s.label}: buttons under a toast`).toEqual([]);
      await dismissToasts(page);
    }
  });
});
