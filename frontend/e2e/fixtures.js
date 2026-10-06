// The test every spec uses: Playwright's, failing any test whose page logs
// a console error, throws, or gets a failed /api answer (4xx, 5xx, or no
// answer; a request aborted by leaving the page aside). A test may let
// one known error through with pageErrors.allow(pattern), each pointing at
// its issue.
import { test as base, expect } from "@playwright/test";

export const test = base.extend({
  pageErrors: [async ({ page }, provide) => {
    const errors = [];
    page.on("console", msg => {
      if (msg.type() !== "error") return;
      const where = msg.location()?.url;
      errors.push(`console: ${msg.text()}${where ? ` (${where})` : ""}`);
    });
    page.on("pageerror", err => errors.push(`page error: ${err.message}`));
    page.on("response", r => {
      if (new URL(r.url()).pathname.startsWith("/api/") && r.status() >= 400) {
        errors.push(`api: ${r.request().method()} ${r.url()} → ${r.status()}`);
      }
    });
    page.on("requestfailed", r => {
      const why = r.failure()?.errorText || "";
      if (new URL(r.url()).pathname.startsWith("/api/") && !why.includes("ERR_ABORTED")) {
        errors.push(`api: ${r.method()} ${r.url()} failed: ${why}`);
      }
    });
    const allowed = [];
    await provide({ allow: re => allowed.push(re) });
    expect(errors.filter(e => !allowed.some(re => re.test(e))), "console errors and failed /api requests").toEqual([]);
  }, { auto: true }],
});

export { expect };

// Every page in the nav.
export const PAGES = [
  { name: "Feed", path: "/" },
  { name: "Review", path: "/review" },
  { name: "Creators", path: "/creators" },
  { name: "Tags", path: "/tags" },
  { name: "Collections", path: "/collections" },
  { name: "Stats", path: "/stats" },
  { name: "Storage", path: "/storage" },
  { name: "Trash", path: "/trash" },
  { name: "Unmatched", path: "/unmatched" },
  { name: "Duplicates", path: "/duplicates" },
  { name: "Jobs", path: "/jobs" },
  { name: "Scripts", path: "/scripts" },
  { name: "Settings", path: "/settings" },
];

// Opens a page and waits until it has loaded: its link in the nav is the
// current one (hidden in the closed drawer on a phone), its title (Review:
// its count) shows, and no request is left.
export async function openPage(page, { name, path }) {
  await page.goto(path);
  await expect(page.locator(`#main-nav a.side-link[href="${path}"]`)).toHaveAttribute("aria-current", "page");
  if (name === "Review") await expect(page.locator(".review-left")).toHaveText(/^\d[\d,]*$/);
  else await expect(page.getByRole("heading", { level: 2, name, exact: true })).toBeVisible();
  await page.waitForLoadState("networkidle");
}

// Review's count of posts left, once known.
export async function reviewLeft(page) {
  const left = page.locator(".review-left");
  await expect(left).toHaveText(/^\d[\d,]*$/);
  return Number((await left.textContent()).replace(/,/g, ""));
}

// Review: keep, trash, undo, by the actions given (keys or taps), checked by
// the session's counts, the count left and which post is current.
export async function keepTrashUndo(page, { keep, trash, undo }) {
  const session = page.locator(".review-session");
  const leftNow = page.locator(".review-left");
  const open = page.locator(".review-open");          // the current post's own page: one per post
  const left = await reviewLeft(page);
  expect(left).toBeGreaterThan(2);
  await expect(session).toHaveText("0 kept · 0 trashed");
  const first = await open.getAttribute("href");

  await keep();
  await expect(session).toHaveText("1 kept · 0 trashed");
  await expect(leftNow).toHaveText(String(left - 1));
  await expect(open).not.toHaveAttribute("href", first);
  const second = await open.getAttribute("href");

  await trash();
  await expect(session).toHaveText("1 kept · 1 trashed");
  await expect(leftNow).toHaveText(String(left - 2));
  await expect(open).not.toHaveAttribute("href", second);

  // Undo brings the trashed post back, as the current one, without asking
  // for its old cover (#90): any console error fails the test.
  await undo();
  await expect(session).toHaveText("1 kept · 0 trashed");
  await expect(leftNow).toHaveText(String(left - 1));
  await expect(open).toHaveAttribute("href", second);
}

/* ── Layout checks (layout.spec.js) ─────────────────────────── */

// What make_demo.py --stress and stress.js made: { post, author,
// collection, person } (global-setup.js).
export function stressData() {
  return JSON.parse(process.env.FEEDVAULT_E2E_STRESS || "null");
}

// Findings the layout checks let through, each with its reason. A rule
// (overlap, clipped, offscreen, target) and the selectors its elements
// match: `a` and `b` for an overlap (either way round), `el` for the rest.
export const LAYOUT_ALLOW = [
  { rule: "overlap", a: "input.header-search", b: "kbd.search-kbd",
    reason: "the / shortcut hint sits inside the search field by design: the field's 44px right padding keeps typed text clear of it, and it takes no clicks" },
  { rule: "overlap", a: ".side-row > a.side-link", b: "a.side-new",
    reason: "the Feed link's \"N new\" badge is a link of its own laid on the Feed row's right end, past the label (index.css, .side-row)" },
];

// Animations and transitions end at once: boxes are measured where they
// end (not "none": cards and rows fade in from opacity 0, kept by the
// animation's fill).
export async function stillPage(page) {
  await page.addStyleTag({ content: "*, *::before, *::after { animation-duration: 0s !important; animation-delay: 0s !important; "
    + "animation-iteration-count: 1 !important; transition-duration: 0s !important; transition-delay: 0s !important; caret-color: transparent !important; }" });
}

// Two frames, and the fonts loaded: the layout after a resize or a click.
export async function settle(page) {
  await page.evaluate(() => document.fonts.ready.then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))));
}

// In the page: every finding of the layout rules, in the viewport's
// coordinates (scrolled to the top). ``scope``: a selector to check only
// what is inside it (an open dialog), or null for the whole page.
function findLayoutProblems({ scope, allow, overlapPx, minTarget }) {
  const de = document.documentElement;
  const vw = de.clientWidth;
  const root = scope ? document.querySelector(scope) : document.body;
  if (!root) return [{ rule: "scope", detail: `nothing matches ${scope}` }];
  const out = [];
  const INTERACTIVE = "a[href], button, input:not([type=hidden]), select, textarea, summary, [contenteditable=''], [contenteditable=true], "
    + "[role=button], [role=link], [role=tab], [role=checkbox], [role=switch], [role=menuitem], [role=option]";
  const TARGET = "a[href], button, [role=button], [role=link], [role=tab], summary";
  const CLIPS = new Set(["hidden", "clip", "auto", "scroll"]);

  const cssPath = el => {
    const parts = [];
    for (let e = el; e && e !== document.body && parts.length < 4; e = e.parentElement) {
      let p = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(`${p}#${e.id}`); break; }
      const cls = [...e.classList].filter(c => !/^is-/.test(c)).slice(0, 2);
      if (cls.length) p += "." + cls.join(".");
      parts.unshift(p);
    }
    return parts.join(" > ");
  };
  const textOf = el => (el.getAttribute("aria-label") || el.textContent || el.value || "").replace(/\s+/g, " ").trim().slice(0, 60);
  const boxOf = r => ({ x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.right - r.left), h: Math.round(r.bottom - r.top) });
  const allowed = (rule, a, b) => allow.some(x => x.rule === rule && (b
    ? (a.matches(x.a) && b.matches(x.b)) || (a.matches(x.b) && b.matches(x.a))
    : a.matches(x.el)));

  // The part of ``r`` its ancestors' overflow lets show (null if none).
  const clipCache = new Map();
  function clipOf(el) {
    if (clipCache.has(el)) return clipCache.get(el);
    let box = { left: -Infinity, top: -Infinity, right: Infinity, bottom: Infinity };
    const pos = getComputedStyle(el).position;
    if (pos !== "fixed") {
      // An absolute box is clipped by the ancestors from its containing block up.
      const cb = pos === "absolute" ? el.offsetParent : null;
      for (let a = el.parentElement; a && a !== de; a = a.parentElement) {
        if (cb && !a.contains(cb)) continue;
        const cs = getComputedStyle(a);
        if (CLIPS.has(cs.overflowX) || CLIPS.has(cs.overflowY)) {
          const r = a.getBoundingClientRect();
          const l = r.left + a.clientLeft, t = r.top + a.clientTop;
          if (CLIPS.has(cs.overflowX)) { box.left = Math.max(box.left, l); box.right = Math.min(box.right, l + a.clientWidth); }
          if (CLIPS.has(cs.overflowY)) { box.top = Math.max(box.top, t); box.bottom = Math.min(box.bottom, t + a.clientHeight); }
        }
        if (cs.position === "fixed") break;
      }
    }
    clipCache.set(el, box);
    return box;
  }
  // Text is clipped by its own element's overflow too (an ellipsis).
  function ownClip(el) {
    const cs = getComputedStyle(el);
    const box = { ...clipOf(el) };
    if (!CLIPS.has(cs.overflowX) && !CLIPS.has(cs.overflowY)) return box;
    const r = el.getBoundingClientRect();
    const l = r.left + el.clientLeft, t = r.top + el.clientTop;
    if (CLIPS.has(cs.overflowX)) { box.left = Math.max(box.left, l); box.right = Math.min(box.right, l + el.clientWidth); }
    if (CLIPS.has(cs.overflowY)) { box.top = Math.max(box.top, t); box.bottom = Math.min(box.bottom, t + el.clientHeight); }
    return box;
  }
  const cut = (r, c) => {
    const x = { left: Math.max(r.left, c.left), top: Math.max(r.top, c.top), right: Math.min(r.right, c.right), bottom: Math.min(r.bottom, c.bottom) };
    return x.right - x.left > 1 && x.bottom - x.top > 1 ? x : null;
  };
  const shown = el => el.checkVisibility({ opacityProperty: true, visibilityProperty: true })
    && !el.closest("[inert]");

  // Text: an element's own text nodes, line by line, each line trimmed to
  // its line-height (a font's glyph box is taller than a tight line).
  function textRects(el) {
    const rects = [];
    const lh = parseFloat(getComputedStyle(el).lineHeight);
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.data.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) {
        if (r.width < 1 || r.height < 1) continue;
        const inset = lh && r.height > lh ? (r.height - lh) / 2 : 0;
        rects.push({ left: r.left, right: r.right, top: r.top + inset, bottom: r.bottom - inset });
      }
    }
    return rects;
  }

  const items = [];
  for (const el of root.querySelectorAll("*")) {
    if (el.closest("svg") && el.tagName.toLowerCase() !== "svg") continue;
    const interactive = el.matches(INTERACTIVE);
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.data.trim());
    if (!interactive && !hasText) continue;
    if (!shown(el)) continue;
    const clip = interactive ? clipOf(el) : ownClip(el);
    const raw = interactive
      ? (getComputedStyle(el).display === "inline" ? [...el.getClientRects()] : [el.getBoundingClientRect()])
      : textRects(el);
    const rects = raw.map(r => cut(r, clip)).filter(Boolean);
    if (!rects.length) continue;
    items.push({
      el, interactive, rects,
      top: Math.min(...rects.map(r => r.top)), bottom: Math.max(...rects.map(r => r.bottom)),
    });
  }

  // Overlap: two of them, neither inside the other, crossing by more than overlapPx both ways.
  items.sort((a, b) => a.top - b.top);
  for (let i = 0; i < items.length; i++) {
    const A = items[i];
    for (let j = i + 1; j < items.length && items[j].top < A.bottom; j++) {
      const B = items[j];
      if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
      let worst = null;
      for (const ra of A.rects) for (const rb of B.rects) {
        const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (w > overlapPx && h > overlapPx && (!worst || w * h > worst.w * worst.h)) worst = { w, h, ra, rb };
      }
      if (!worst || allowed("overlap", A.el, B.el)) continue;
      out.push({
        rule: "overlap", detail: `${Math.round(worst.w)}x${Math.round(worst.h)}px`,
        a: { sel: cssPath(A.el), text: textOf(A.el), box: boxOf(worst.ra) },
        b: { sel: cssPath(B.el), text: textOf(B.el), box: boxOf(worst.rb) },
      });
    }
  }

  // Off screen: an interactive element partly left or right of the viewport.
  for (const it of items) {
    if (!it.interactive) continue;
    const l = Math.min(...it.rects.map(r => r.left)), r = Math.max(...it.rects.map(r => r.right));
    if ((l < -1 || r > vw + 1) && !allowed("offscreen", it.el)) {
      out.push({ rule: "offscreen", detail: `x ${Math.round(l)} to ${Math.round(r)}, viewport ${vw}`,
        a: { sel: cssPath(it.el), text: textOf(it.el), box: boxOf({ left: l, right: r, top: it.top, bottom: it.bottom }) } });
    }
  }

  // Targets: buttons and links at least minTarget square, but for one
  // inline in a sentence (its parent, laid out as text, has text of its own).
  for (const it of items) {
    if (!it.el.matches(TARGET)) continue;
    const r = it.el.getBoundingClientRect();
    if (r.width >= minTarget && r.height >= minTarget) continue;
    const parent = it.el.parentElement;
    const inSentence = getComputedStyle(it.el).display.startsWith("inline")
      && /^(block|inline|inline-block|list-item|table-cell)$/.test(getComputedStyle(parent).display)
      && [...parent.childNodes].some(n => n.nodeType === 3 && n.data.trim());
    if (inSentence || allowed("target", it.el)) continue;
    out.push({ rule: "target", detail: `${Math.round(r.width)}x${Math.round(r.height)}px`,
      a: { sel: cssPath(it.el), text: textOf(it.el), box: boxOf(r) } });
  }

  // Clipped text: an element that hides its overflow with text of its own
  // (not a nested scroller's) past its edge, and no title with the text.
  const norm = s => s.replace(/[\s@#…]+/g, " ").trim().toLowerCase();
  for (const el of [root, ...root.querySelectorAll("*")]) {
    const cs = getComputedStyle(el);
    const hx = cs.overflowX === "hidden" || cs.overflowX === "clip";
    const hy = cs.overflowY === "hidden" || cs.overflowY === "clip";
    if (!hx && !hy) continue;
    const overX = hx && el.scrollWidth > el.clientWidth + 1;
    const overY = hy && el.scrollHeight > el.clientHeight + 1;
    if ((!overX && !overY) || !shown(el)) continue;
    const r = el.getBoundingClientRect();
    if ((cs.position === "absolute" || cs.position === "fixed") && (r.width <= 2 || r.height <= 2)) continue;  // for screen readers only
    const l = r.left + el.clientLeft, t = r.top + el.clientTop;
    const inner = { left: l - 1, top: t - 1, right: l + el.clientWidth + 1, bottom: t + el.clientHeight + 1 };
    let past = null;
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n && !past; n = walk.nextNode()) {
      if (!/[\p{L}\p{N}]/u.test(n.data) || !shown(n.parentElement)) continue;   // " @", " · ": nothing to find in a title
      let own = true;
      for (let a = n.parentElement; a && a !== el; a = a.parentElement) {
        const s = getComputedStyle(a);
        if (s.overflowX !== "visible" || s.overflowY !== "visible" || s.position === "fixed" || s.position === "absolute") { own = false; break; }
      }
      if (!own) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const tr of range.getClientRects()) {
        if (tr.width < 1) continue;
        if ((overX && (tr.right > inner.right || tr.left < inner.left)) || (overY && (tr.bottom > inner.bottom || tr.top < inner.top))) { past = n; break; }
      }
    }
    if (!past) continue;
    // The title of the element or of one around it holds the cut text.
    const titled = el.closest("[title]");
    const title = titled ? norm(titled.getAttribute("title")) : "";
    if ((title && title.includes(norm(past.data))) || allowed("clipped", el)) continue;
    const ellipsis = cs.textOverflow === "ellipsis" || cs.webkitLineClamp !== "none" && cs.webkitLineClamp !== "";
    out.push({ rule: "clipped", detail: `${overX ? `width ${el.scrollWidth} > ${el.clientWidth}` : ""}${overX && overY ? ", " : ""}${overY ? `height ${el.scrollHeight} > ${el.clientHeight}` : ""}; ${ellipsis ? "ellipsis but no title with the text" : "no ellipsis, no title"}`,
      a: { sel: cssPath(el), text: textOf(el), box: boxOf(r) } });
  }

  // The page itself scrolls sideways.
  if (!scope && de.scrollWidth > de.clientWidth) {
    out.push({ rule: "hscroll", detail: `page scrollWidth ${de.scrollWidth} > ${de.clientWidth}` });
  }
  return out;
}

const fmtBox = b => b ? `[${b.x},${b.y} ${b.w}x${b.h}]` : "";
export function formatFindings(list) {
  return list.map(f => `  ${f.view} @ ${f.size}: ${f.rule} ${f.detail}`
    + (f.a ? `\n      ${f.a.sel} "${f.a.text}" ${fmtBox(f.a.box)}` : "")
    + (f.b ? `\n      ${f.b.sel} "${f.b.text}" ${fmtBox(f.b.box)}` : "")).join("\n");
}

// Checks the page as it is now; on findings, a full-page screenshot with
// their boxes outlined (red: first element, blue: second) is attached to
// the test. Resolves to the findings, labelled with ``view`` and ``size``.
export async function checkLayout(page, testInfo, { view, size, scope = null }) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await settle(page);
  const found = await page.evaluate(findLayoutProblems, { scope, allow: LAYOUT_ALLOW, overlapPx: 2, minTarget: 24 });
  const list = found.map(f => ({ view, size, ...f }));
  if (list.length) {
    await page.evaluate(fs => {
      const layer = document.createElement("div");
      layer.id = "layout-findings";
      layer.style.cssText = "position:absolute;left:0;top:0;pointer-events:none;z-index:2147483647";
      for (const f of fs) {
        for (const [k, color] of [["a", "#e00"], ["b", "#06f"]]) {
          const b = f[k]?.box;
          if (!b) continue;
          const d = document.createElement("div");
          d.style.cssText = `position:absolute;left:${b.x + scrollX}px;top:${b.y + scrollY}px;width:${b.w}px;height:${b.h}px;outline:2px solid ${color};background:${color}22`;
          layer.append(d);
        }
      }
      document.body.append(layer);
    }, list);
    const file = testInfo.outputPath(`${view}-${size}.png`.replace(/[^\w.@-]+/g, "_"));
    await page.screenshot({ path: file, fullPage: true });
    await testInfo.attach(`${view} @ ${size}`, { path: file, contentType: "image/png" });
    await page.evaluate(() => document.getElementById("layout-findings")?.remove());
  }
  return list;
}
