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

/* ── State checks (states.spec.js) ──────────────────────────── */

// Findings the state checks let through, each with its reason: a rule
// (focus-ring, focus-hidden, covered, hover-moved, popover) and the
// selector its element matches.
export const STATE_ALLOW = [];

// In the page, before its own scripts (page.addInitScript): window.__fv,
// the probes states.spec.js calls between key presses
// and mouse moves. Self-contained: it is serialized into the page.
function installProbes() {
  const CLIPS = new Set(["hidden", "clip", "auto", "scroll"]);
  const INTERACTIVE = "a[href], button, input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex='-1']), "
    + "[role=button], [role=link], [role=tab], [role=checkbox], [role=switch], [role=menuitem], [role=option]";
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
  const textOf = el => (el.getAttribute("aria-label") || el.textContent || el.value || el.placeholder || "").replace(/\s+/g, " ").trim().slice(0, 50);
  const boxOf = r => ({ x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.right - r.left), h: Math.round(r.bottom - r.top) });
  const shown = el => el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && !el.closest("[inert], [aria-hidden=true]");
  const allowed = (allow, rule, el) => allow.some(x => x.rule === rule && el.matches(x.el));
  const describe = el => ({ sel: cssPath(el), text: textOf(el), box: boxOf(el.getBoundingClientRect()) });

  // The part of the window an element's overflow ancestors let show.
  function clipOf(el) {
    const de = document.documentElement;
    const box = { left: 0, top: 0, right: de.clientWidth, bottom: innerHeight };
    let by = null;
    const pos = getComputedStyle(el).position;
    if (pos === "fixed") return { box, by };
    const cb = pos === "absolute" ? el.offsetParent : null;
    for (let a = el.parentElement; a && a !== de && a !== document.body; a = a.parentElement) {
      if (cb && !a.contains(cb)) continue;
      const cs = getComputedStyle(a);
      const cx = CLIPS.has(cs.overflowX), cy = CLIPS.has(cs.overflowY);
      if (cx || cy) {
        const r = a.getBoundingClientRect();
        const l = r.left + a.clientLeft, t = r.top + a.clientTop;
        const next = { ...box };
        if (cx) { next.left = Math.max(box.left, l); next.right = Math.min(box.right, l + a.clientWidth); }
        if (cy) { next.top = Math.max(box.top, t); next.bottom = Math.min(box.bottom, t + a.clientHeight); }
        if (next.left > box.left || next.top > box.top || next.right < box.right || next.bottom < box.bottom) by = by || a;
        Object.assign(box, next);
      }
      if (cs.position === "fixed") break;
    }
    return { box, by };
  }

  // The bars pinned over the page: the sticky header, and the selection
  // bar at the bottom in select mode.
  const bars = () => [...document.querySelectorAll("body > #root > header, header, .select-bar")]
    .filter((el, i, all) => all.indexOf(el) === i && shown(el))
    .map(el => ({ el, r: el.getBoundingClientRect() }));
  const overlap = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
    * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));

  // Whether a click at the element's centre reaches it (or what it wraps:
  // a label's field), and if not, what does.
  function hit(el, r = el.getBoundingClientRect()) {
    const x = Math.min(Math.max(r.left + r.width / 2, 0), document.documentElement.clientWidth - 1);
    const y = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
    const top = document.elementFromPoint(x, y);
    const ok = !!top && (top === el || el.contains(top) || (top.tagName === "LABEL" && top.contains(el))
      || (top.control && top.control === el));
    return { ok, top, x, y };
  }

  // The outline and box-shadow of each element a focus ring may sit on: the
  // element, its next sibling (a styled box after a hidden input) and three
  // ancestors (a card that shows the ring of a link inside it).
  const RING = ["outlineStyle", "outlineWidth", "outlineColor", "outlineOffset", "boxShadow"];
  const ringOf = el => RING.map(k => getComputedStyle(el)[k]).join("|");
  function ringCarriers(el) {
    const list = [el];
    if (el.nextElementSibling) list.push(el.nextElementSibling);
    for (let a = el.parentElement; a && a !== document.body && list.length < 5; a = a.parentElement) list.push(a);
    return list;
  }
  // How far a ring reaches out of its element: an outline's width and
  // offset, or the spread of an outer box-shadow (its blur is a glow, not
  // the edge). Inset shadows stay inside.
  function ringReach(el) {
    const cs = getComputedStyle(el);
    let reach = 0;
    if (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) {
      reach = Math.max(reach, parseFloat(cs.outlineWidth) + parseFloat(cs.outlineOffset || "0"));
    }
    if (cs.boxShadow && cs.boxShadow !== "none") {
      for (const s of cs.boxShadow.split(/,(?![^(]*\))/)) {
        if (/\binset\b/.test(s)) continue;
        const n = (s.replace(/rgba?\([^)]*\)/g, "").match(/-?[\d.]+px/g) || []).map(parseFloat);
        const [dx = 0, dy = 0, , spread = 0] = n;
        if (spread > 0) reach = Math.max(reach, spread + Math.max(Math.abs(dx), Math.abs(dy)));
      }
    }
    return reach;
  }

  window.__fv = {
    // The element that has focus, checked: its ring shows (an outline or a
    // box-shadow that differs from its unfocused look), is not cut off by
    // an overflow ancestor, not under a pinned bar, and it is not hidden.
    focusStop(allow) {
      const el = document.activeElement;
      if (!el || el === document.body || el === document.documentElement) return null;
      const carriers = ringCarriers(el);
      // No transition while it is measured: a style read right after blur
      // would still give the focused value, where the transition starts.
      const kept = carriers.map(c => c.style.getPropertyValue("transition"));
      carriers.forEach(c => c.style.setProperty("transition", "none", "important"));
      const on = carriers.map(ringOf);
      el.blur();
      const off = carriers.map(ringOf);
      el.focus({ preventScroll: true });
      carriers.forEach((c, i) => (kept[i] ? c.style.setProperty("transition", kept[i]) : c.style.removeProperty("transition")));
      const carrier = carriers.find((c, i) => on[i] !== off[i]);
      const out = { ...describe(el), scrollY, problems: [] };
      const flag = (rule, detail) => { if (!allowed(allow, rule, el)) out.problems.push({ rule, detail }); };
      const visible = c => shown(c) && c.getBoundingClientRect().width > 1 && c.getBoundingClientRect().height > 1;
      if (!visible(el) && !(carrier && carrier !== el && visible(carrier))) {
        flag("focus-hidden", "focus went to an element that does not show");
        return out;
      }
      if (!carrier) { flag("focus-ring", "no outline or box-shadow changes on focus"); return out; }
      const r = carrier.getBoundingClientRect();
      const reach = ringReach(carrier);
      const ring = { left: r.left - reach, top: r.top - reach, right: r.right + reach, bottom: r.bottom + reach };
      out.ring = boxOf(ring);
      const { box, by } = clipOf(carrier);
      const cut = Math.max(box.left - ring.left, box.top - ring.top, ring.right - box.right, ring.bottom - box.bottom);
      if (cut > 0.5) {
        flag("focus-clipped", `ring cut by ${Math.round(cut * 10) / 10}px` + (by ? ` (overflow of ${cssPath(by)})` : " (window edge)"));
      }
      for (const b of bars()) {
        if (b.el.contains(el) || el.contains(b.el)) continue;
        const pinned = (() => { for (let a = el; a; a = a.parentElement) { const p = getComputedStyle(a).position; if (p === "fixed") return true; } return false; })();
        if (!pinned && overlap(ring, b.r) > 1) flag("focus-covered", `ring under ${cssPath(b.el)}`);
      }
      const h = hit(el, r);
      if (!h.ok && h.top && !carrier.contains(h.top) && !h.top.contains(el)) {
        const under = bars().find(b => b.el.contains(h.top));
        if (!under) flag("focus-covered", `centre covered by ${cssPath(h.top)}`);
      }
      return out;
    },

    // Interactive elements in the window, checked with the hit test: one
    // whose centre is under a pinned bar must not be what a click there
    // reaches, and one in the open must be.
    covered(allow) {
      const out = [];
      const vw = document.documentElement.clientWidth, vh = innerHeight;
      const pinned = bars();
      for (const el of document.querySelectorAll(INTERACTIVE)) {
        if (!shown(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        if (cx < 0 || cy < 0 || cx >= vw || cy >= vh) continue;
        const { box } = clipOf(el);
        if (cx < box.left || cx > box.right || cy < box.top || cy > box.bottom) continue;   // scrolled out of its box
        const own = pinned.find(b => b.el.contains(el));
        const under = pinned.find(b => !b.el.contains(el) && cx >= b.r.left && cx <= b.r.right && cy >= b.r.top && cy <= b.r.bottom);
        const h = hit(el, r);
        if (under && !own) {
          if (h.ok && !allowed(allow, "covered", el)) out.push({ rule: "covered", detail: `under ${cssPath(under.el)} yet a click there reaches it`, a: describe(el) });
        } else if (!h.ok && h.top && !allowed(allow, "covered", el)) {
          out.push({ rule: "covered", detail: `a click on its centre reaches ${cssPath(h.top)} "${textOf(h.top)}"`, a: describe(el) });
        }
      }
      return out;
    },

    // Elements a mouse can hover, one of each kind, in the window and
    // clear of the bars: marked data-fv-hover="<n>", their count returned.
    markHoverTargets(selector, max) {
      const kinds = new Set();
      let n = 0;
      for (const el of document.querySelectorAll(selector)) {
        if (n >= max) break;
        if (!shown(el) || el.disabled) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2 || r.top < 0 || r.bottom > innerHeight || r.left < 0 || r.right > document.documentElement.clientWidth) continue;
        if (!hit(el, r).ok || bars().some(b => !b.el.contains(el) && overlap(r, b.r) > 0)) continue;
        const kind = `${el.tagName}.${[...el.classList].filter(c => !/^is-|active/.test(c)).sort().join(".")}<${el.parentElement?.className || ""}`;
        if (kinds.has(kind)) continue;
        kinds.add(kind);
        el.dataset.fvHover = String(n++);
      }
      return n;
    },

    // The boxes hover must not move: the element, its parent and their
    // siblings. And what floats (absolute or fixed, shown): a popover.
    hoverBoxes(n, allow) {
      const el = document.querySelector(`[data-fv-hover="${n}"]`);
      if (!el) return null;
      const set = new Set([el, el.parentElement, ...(el.parentElement?.children || []), ...(el.parentElement?.parentElement?.children || [])]);
      const boxes = [...set].filter(Boolean).map(e => ({ sel: cssPath(e), self: e === el, box: e.getBoundingClientRect().toJSON() }));
      // Within 300px of the element: what its hover may have opened (not a
      // card the Feed loaded meanwhile, far down the page).
      const near = (r, q) => r.right > q.left - 300 && r.left < q.right + 300 && r.bottom > q.top - 300 && r.top < q.bottom + 300;
      const self = el.getBoundingClientRect();
      const floats = [...document.querySelectorAll("body *")].filter(e => {
        const p = getComputedStyle(e).position;
        if (p !== "absolute" && p !== "fixed") return false;
        const r = e.getBoundingClientRect();
        return shown(e) && r.width > 4 && r.height > 4 && near(r, self) && !e.closest("header, .cyber-bg");
      });
      for (const e of floats) e.dataset.fvFloat ??= String(window.__fvFloats = (window.__fvFloats || 0) + 1);
      return { ...describe(el), boxes, floats: floats.map(e => e.dataset.fvFloat), allowMove: allowed(allow, "hover-moved", el) };
    },

    // A popover (or any box): inside the window, clear of the pinned bars,
    // and on top where it shows (a click on its corners reaches it).
    popover(selector) {
      const el = document.querySelector(selector);
      if (!el || !shown(el)) return [{ rule: "popover", detail: `nothing shows for ${selector}` }];
      const out = [];
      const r = el.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      if (r.left < -0.5 || r.top < -0.5 || r.right > vw + 0.5 || r.bottom > innerHeight + 0.5) {
        out.push({ rule: "popover", detail: `out of the ${vw}x${innerHeight} window`, a: describe(el) });
      }
      for (const b of bars()) {
        if (b.el.contains(el)) continue;
        if (overlap(r, b.r) > 1) {
          // Over the bar is fine (a modal above it); under it is not.
          const pts = [[r.left + 3, Math.max(r.top, b.r.top) + 3], [r.right - 3, Math.max(r.top, b.r.top) + 3]];
          const hidden = pts.some(([x, y]) => { const t = document.elementFromPoint(x, y); return t && !el.contains(t) && b.el.contains(t); });
          if (hidden) out.push({ rule: "popover", detail: `under ${cssPath(b.el)}`, a: describe(el) });
        }
      }
      return out;
    },

    bars: () => bars().map(b => ({ kind: b.el.matches("header") ? "header" : "select-bar", sel: cssPath(b.el), ...boxOf(b.r) })),
  };
}

// Installs the probes in every page the test opens from now on, and plays
// CSS animations and transitions at once (through the DevTools protocol: a
// stylesheet on every element would restyle the whole page at each focus
// change, many times slower): boxes are measured where they end.
const inFlight = new WeakMap();
export async function addProbes(page) {
  // /api requests in flight, for quiet() below.
  const n = { now: 0, last: Date.now() };
  inFlight.set(page, n);
  const api = r => new URL(r.url()).pathname.startsWith("/api/");
  page.on("request", r => { if (api(r)) { n.now++; n.last = Date.now(); } });
  for (const ev of ["requestfinished", "requestfailed"]) page.on(ev, r => { if (api(r)) { n.now--; n.last = Date.now(); } });
  await page.addInitScript(installProbes);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Animation.enable");
  await cdp.send("Animation.setPlaybackRate", { playbackRate: 100000 });
}

// Loaded: ``ready`` shows and no /api request has been in flight for
// ``ms`` (networkidle waits 500ms, many times over in these specs).
export async function quiet(page, ready, ms = 150) {
  if (ready) await expect(page.locator(ready).first()).toBeVisible();
  const n = inFlight.get(page);
  const end = Date.now() + 10_000;
  while (Date.now() < end && (n.now > 0 || Date.now() - n.last < ms)) await page.waitForTimeout(25);
  await settle(page);
}

// One frame: the styles of a new focus or hover applied.
export async function frame(page) {
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => r())));
}

export function formatStateFindings(list) {
  return list.map(f => `  ${f.view} @ ${f.size}${f.state ? ` (${f.state})` : ""}: ${f.rule} ${f.detail}`
    + (f.a ? `\n      ${f.a.sel} "${f.a.text}" ${fmtBox(f.a.box)}` : "")).join("\n");
}
