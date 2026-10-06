// Checks for lib/theme.js: the palette derivation, the contrast pick of the
// text on fills and glow, and what survives a malformed localStorage.
// Run with `npm test` (node's own test runner, no extra dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  PRESETS, PHOSPHOR, DEFAULT_THEME_ID, SURFACE_2, RING_ALPHA, contrast, palette, themeColors, getCustomThemes,
  getActiveTheme, saveCustomThemes,
} from "../src/lib/theme.js";

const HEX = /^#[0-9a-f]{6}$/;
const RGB = /^\d{1,3}, \d{1,3}, \d{1,3}$/;
const hexRgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const near = (a, b, tol) => hexRgb(a).every((v, i) => Math.abs(v - hexRgb(b)[i]) <= tol);
// Button text is judged against the middle of the hover→fill gradient.
const mid = p => hexRgb(p["--accent"]).map((v, i) => Math.round((v + hexRgb(p["--accent-hover"])[i]) / 2));

// A Map-backed stand-in for the browser's localStorage.
function storage(entries = {}) {
  const m = new Map(Object.entries(entries));
  globalThis.localStorage = {
    getItem: k => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
  };
  return m;
}

test("every preset derives a complete, well-formed palette", () => {
  for (const t of PRESETS) {
    const p = palette(t);
    assert.deepEqual(Object.keys(p).sort(), Object.keys(PHOSPHOR).sort(), t.id);
    for (const [k, v] of Object.entries(p)) assert.match(v, k.endsWith("-rgb") ? RGB : HEX, `${t.id} ${k}`);
  }
});

test("Phosphor's derivation lands close to index.css's hand-tuned green", () => {
  const p = palette(PRESETS[0]);
  for (const k of ["--accent", "--accent-hover", "--glow", "--accent-text"]) {
    assert.ok(near(p[k], PHOSPHOR[k], 12), `${k}: ${p[k]} vs ${PHOSPHOR[k]}`);
  }
});

test("text on fills and on glow is the more readable of dark ink and near-white", () => {
  const light = "#f5f7fb";
  for (const base of [...PRESETS.map(t => t.base), "#1e3a8a", "#7f1d1d", "#ffff00", "#808080", "#000000", "#ffffff"]) {
    const p = palette({ base });
    const on = p["--on-accent"], onGlow = p["--on-glow"];
    // Dark ink is only picked when it beats near-white.
    assert.ok(contrast(on, mid(p)) >= contrast(light, mid(p)) - 1e-9, `${base}: on-accent ${on}`);
    assert.ok(contrast(onGlow, p["--glow"]) >= contrast(light, p["--glow"]) - 1e-9, `${base}: on-glow ${onGlow}`);
    assert.ok(contrast(onGlow, p["--glow"]) >= 3, `${base}: on-glow ${onGlow} on ${p["--glow"]}`);
  }
  // Every preset's button text is readable (WCAG AA for large/bold text).
  for (const t of PRESETS) assert.ok(contrast(palette(t)["--on-accent"], mid(palette(t))) >= 3, t.id);
});

test("a dark fill gets light text, a light fill dark text", () => {
  assert.equal(palette({ base: "#000000", fill: "#101820" })["--on-accent"], "#f5f7fb");
  assert.notEqual(palette({ base: "#000000", fill: "#f0f0f0" })["--on-accent"], "#f5f7fb");
});

test("pinned roles win, invalid pins and bases are ignored", () => {
  const p = palette({ base: "#60a5fa", fill: "#123456", buttonText: "#abcdef", glow: "#00ff00", glowText: "#111111", highlight: "#fedcba" });
  assert.equal(p["--accent"], "#123456");
  assert.equal(p["--on-accent"], "#abcdef");
  assert.equal(p["--glow"], "#00ff00");
  assert.equal(p["--glow-rgb"], "0, 255, 0");
  assert.equal(p["--on-glow"], "#111111");
  assert.equal(p["--accent-text"], "#fedcba");
  const q = palette({ base: "#60a5fa", fill: "red", glow: "url(x)", buttonText: "#12345" });
  assert.deepEqual(q, palette({ base: "#60a5fa" }));
  assert.deepEqual(palette({ base: "javascript:1" }), palette(PRESETS[0]));
});

test("PHOSPHOR matches index.css's :root, so Phosphor previews and copies paint what the page does", () => {
  const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
  const root = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
  for (const [k, v] of Object.entries(PHOSPHOR)) {
    const m = root.match(new RegExp(`\\s${k}:\\s*([^;]+);`));
    assert.ok(m, `${k} missing from :root`);
    assert.equal(m[1].trim(), v, k);
  }
});

test("a pinned fill or glow carries its own hue into the shades and ink derived from it", () => {
  const hue = hex => {
    const [r, g, b] = hexRgb(hex).map(v => v / 255), max = Math.max(r, g, b), d = max - Math.min(r, g, b);
    return d === 0 ? 0 : (max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60;
  };
  const p = palette({ base: "#4ade80", fill: "#3b82f6", glow: "#f87171" });
  assert.ok(Math.abs(hue(p["--accent-light"]) - hue("#3b82f6")) < 6, p["--accent-light"]);
  assert.ok(Math.abs(hue(p["--on-glow"]) - hue("#f87171")) < 6 || p["--on-glow"] === "#f5f7fb", p["--on-glow"]);
});

test("malformed localStorage falls back to Phosphor", () => {
  for (const bad of ["{", "null", "42", '"x"', "{}", '[null, 1, "a", {"id": 3}]', '[{"id": "ocean", "base": "#000000"}]']) {
    storage({ "fv:themes": bad, "fv:theme": "c-1" });
    assert.deepEqual(getCustomThemes(), [], bad);
    assert.equal(getActiveTheme().id, DEFAULT_THEME_ID, bad);
  }
  storage({ "fv:theme": "nope" });
  assert.equal(getActiveTheme().id, DEFAULT_THEME_ID);
  globalThis.localStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(getActiveTheme().id, DEFAULT_THEME_ID);
  saveCustomThemes([{ id: "c-1", name: "x", base: "#000000" }]);
});

test("stored custom themes keep only known fields with valid colours", () => {
  storage({
    "fv:theme": "c-1",
    "fv:themes": JSON.stringify([
      { id: "c-1", name: { evil: 1 }, base: "#a78bfa", fill: "#123456", glow: "red; background: url(x)", extra: "<b>" },
      { id: "c-1", name: "duplicate", base: "#000000" },
      { id: "c-2", name: "  Mine  ", base: "#22D3EE" },
    ]),
  });
  assert.deepEqual(getCustomThemes(), [
    { id: "c-1", name: "Custom", base: "#a78bfa", fill: "#123456" },
    { id: "c-2", name: "Mine", base: "#22D3EE" },
  ]);
  assert.equal(getActiveTheme().id, "c-1");
});

// index.css's :root, as name -> value.
function rootVars() {
  const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
  const root = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
  return Object.fromEntries([...root.matchAll(/\s(--[\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]));
}
const mixHex = (top, under, a) => hexRgb(top).map((v, i) => Math.round(v * a + hexRgb(under)[i] * (1 - a)));

test("every preset reads at WCAG AA on the dark base (what the e2e themes check measures)", () => {
  const css = rootVars();
  assert.equal(css["--surface-2"], SURFACE_2, "theme.js's SURFACE_2 is index.css's --surface-2");
  assert.match(css["--ring"], new RegExp(`rgba\\(var\\(--glow-rgb\\), ${RING_ALPHA}\\)`), "RING_ALPHA is --ring's");
  for (const t of PRESETS) {
    const p = themeColors(t);
    // A button's fill is its edge: 3:1 against the page and the lightest surface.
    for (const bg of [css["--bg"], SURFACE_2]) assert.ok(contrast(p["--accent"], bg) >= 3, `${t.id}: --accent ${p["--accent"]} on ${bg}`);
    // Its 13px bold text, at both ends of the hover→fill gradient: 4.5:1.
    for (const k of ["--accent", "--accent-hover"]) {
      assert.ok(contrast(p["--on-accent"], p[k]) >= 4.5, `${t.id}: --on-accent on ${k} ${p[k]}`);
    }
    // Links, hashtags and badges are --accent-hover or --accent-text: 4.5:1 on every surface.
    for (const k of ["--accent-hover", "--accent-text"]) {
      for (const bg of [css["--bg"], css["--surface"], SURFACE_2]) assert.ok(contrast(p[k], bg) >= 4.5, `${t.id}: ${k} on ${bg}`);
    }
    // The focus ring: --glow at --ring's alpha, 3:1 on the lightest surface.
    assert.ok(contrast(mixHex(p["--glow"], SURFACE_2, RING_ALPHA), SURFACE_2) >= 3, `${t.id}: ring ${p["--glow"]}`);
  }
});

test("index.css's own text and edges read at WCAG AA on every surface", () => {
  const css = rootVars();
  for (const bg of [css["--bg"], css["--surface"], css["--surface-2"]]) {
    for (const k of ["--text", "--text-dim", "--muted"]) assert.ok(contrast(css[k], bg) >= 4.5, `${k} ${css[k]} on ${bg}`);
    assert.ok(contrast(css["--edge"], bg) >= 3, `--edge ${css["--edge"]} on ${bg}`);
  }
  for (const k of ["--danger-fill", "--danger-fill-hover"]) assert.ok(contrast("#ffffff", css[k]) >= 4.5, `white on ${k} ${css[k]}`);
});

test("a pinned button or glow text keeps 4.5:1: the fill and glow are not lightened away from it", () => {
  for (const t of PRESETS) {
    const white = palette({ base: t.base, buttonText: "#ffffff" });
    assert.ok(contrast("#ffffff", white["--accent"]) >= 4.5, `${t.id}: white on the fill`);
    const glowInk = palette({ base: t.base, glowText: "#ffffff" });
    assert.equal(glowInk["--on-glow"], "#ffffff");
    assert.ok(contrast("#ffffff", glowInk["--glow"]) >= Math.min(4.5, contrast("#ffffff", palette({ base: t.base })["--glow"])), `${t.id}: white on the glow`);
  }
  // Iris with white text keeps main's fill, 9.6:1 (not lightened to 4.4:1).
  assert.ok(contrast("#ffffff", palette({ base: "#a78bfa", buttonText: "#ffffff" })["--accent"]) > 9);
});
