// Contrast in every preset theme (lib/theme.js PRESETS), at 1440x900: the
// theme is in localStorage before the page loads, as a returning user's
// would be. On the Feed, Review, a post page and Settings, WCAG AA from
// computed colours (fixtures.js, the contrast probe): text 4.5:1 (3:1 when
// large), placeholders 4.5:1, the edge of fields and filled buttons 3:1,
// and the focus ring 3:1 at the Feed's first Tab stops. Disabled controls
// (WCAG exempts them) are only listed, in contrast-info.json, when under
// 3:1. CONTRAST_ALLOW lets findings through, each with its reason. The
// four pages of a theme load side by side, in tabs of their own.
import fs from "node:fs";
import { test, expect, stressData, addProbes, frame, quiet } from "./fixtures.js";
import { PRESETS } from "../src/lib/theme.js";

const S = stressData();
const RING_STOPS = 12;

// Findings let through: a rule and the selector its element matches.
export const CONTRAST_ALLOW = [];

// Loaded: the view's own content shows, no /api request left.
async function openUrl(page, url, ready) {
  await page.goto(url);
  await quiet(page, ready);
}

// ``rings``: the focus ring is the theme's, the same on every page: read
// on the Feed only, at its first Tab stops (the search field, the nav).
const VIEWS = [
  { name: "Feed", open: page => openUrl(page, "/", "article.post-card"), rings: true },
  { name: "Review", open: page => openUrl(page, "/review", ".review-open") },
  { name: "Post", open: page => openUrl(page, `/p/${S.post.platform}/${S.post.post_id}`, ".post-page-head") },
  { name: "Settings", open: page => openUrl(page, "/settings", ".settings-tab[aria-current=page]") },
];

for (const theme of PRESETS) {
  test(theme.name, async ({ page, context }, testInfo) => {
    await context.addInitScript(id => localStorage.setItem("fv:theme", id), theme.id);
    const found = [], info = [];
    // A tab per view (the test's own for the first, whose console the
    // fixture watches; the others' errors fail the test here).
    const tabs = [page, ...await Promise.all(VIEWS.slice(1).map(() => context.newPage()))];
    for (const [i, tab] of tabs.entries()) {
      await addProbes(tab);
      if (i) {
        tab.on("pageerror", e => found.push({ theme: theme.name, view: VIEWS[i].name, rule: "page-error", detail: e.message, a: { sel: "", text: "" } }));
        tab.on("console", m => { if (m.type() === "error") found.push({ theme: theme.name, view: VIEWS[i].name, rule: "console-error", detail: m.text(), a: { sel: "", text: "" } }); });
      }
    }
    await Promise.all(VIEWS.map((v, i) => v.open(tabs[i])));
    for (const [i, v] of VIEWS.entries()) {
      const tab = tabs[i];
      await tab.bringToFront();
      const label = x => ({ theme: theme.name, view: v.name, ...x });
      const res = await tab.evaluate(allow => window.__fv.contrast(allow), CONTRAST_ALLOW);
      found.push(...res.out.map(label));
      info.push(...res.info.map(label));
      if (!v.rings) continue;
      await tab.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
      for (let k = 0; k < RING_STOPS; k++) {
        await tab.keyboard.press("Tab");
        await frame(tab);                                    // the ring's transition ended
        const r = await tab.evaluate(allow => window.__fv.contrast(allow, { ring: true, ringOnly: true }), CONTRAST_ALLOW);
        found.push(...r.out.filter(x => x.rule === "focus-ring").map(label));
      }
    }
    await Promise.all(tabs.slice(1).map(t => t.close()));
    // One line per element and rule.
    const seen = new Set();
    const uniq = list => list.filter(f => { const k = `${f.view}|${f.rule}|${f.a.sel}|${f.detail}`; return !seen.has(k) && seen.add(k); });
    const list = uniq(found), notes = uniq(info);
    if (notes.length) {
      const file = testInfo.outputPath("contrast-info.json");
      fs.writeFileSync(file, JSON.stringify(notes, null, 1));
      await testInfo.attach("contrast-info.json", { path: file, contentType: "application/json" });
    }
    if (list.length) {
      const file = testInfo.outputPath("findings.json");
      fs.writeFileSync(file, JSON.stringify(list, null, 1));
      await testInfo.attach("findings.json", { path: file, contentType: "application/json" });
    }
    expect(list.length, `contrast findings:\n${list.map(f => `  ${f.theme} › ${f.view}: ${f.rule} ${f.detail}\n      ${f.a.sel} "${f.a.text}"`).join("\n")}`).toBe(0);
  });
}
