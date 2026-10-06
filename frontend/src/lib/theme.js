// App-wide colour themes. A theme recolours every accent variable in index.css
// (:root); the dark base palette never changes. Stored per browser, like the
// other UI preferences.
//
// A theme is { id, name, base, fill?, glow?, highlight?, buttonText?, glowText? }.
// `base` alone derives a full palette; each optional field pins that one role to
// an exact colour instead of the derived one.
const ACTIVE_KEY = "fv:theme";
const CUSTOM_KEY = "fv:themes";

export const DEFAULT_THEME_ID = "phosphor";

export const PRESETS = [
  { id: "phosphor", name: "Phosphor", base: "#4ade80" },
  { id: "ocean",    name: "Ocean",    base: "#60a5fa" },
  { id: "aqua",     name: "Aqua",     base: "#22d3ee" },
  { id: "iris",     name: "Iris",     base: "#a78bfa" },
  { id: "rose",     name: "Rose",     base: "#f472b6" },
  { id: "crimson",  name: "Crimson",  base: "#f87171" },
  { id: "ember",    name: "Ember",    base: "#fb923c" },
  { id: "amber",    name: "Amber",    base: "#fbbf24" },
];

// Roles a custom theme can pin, in the order the editor lists them.
export const ROLES = [
  { key: "fill",       label: "Button fill",    hint: "Primary buttons, active states, bars" },
  { key: "buttonText", label: "Button text",    hint: "Text and icons on button fill" },
  { key: "glow",       label: "Glow",           hint: "Icons, rings, focus, status dot" },
  { key: "glowText",   label: "Text on glow",   hint: "Play button on thumbnails, selection tick" },
  { key: "highlight",  label: "Highlight text", hint: "Badges, counts, labels" },
];

const HEX = /^#[0-9a-f]{6}$/i;
export const isHex = v => typeof v === "string" && HEX.test(v);

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [n >> 16 & 255, n >> 8 & 255, n & 255];
}

function rgbToHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l * 100];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s * 100, l * 100];
}

function hslToRgb(h, s, l) {
  s /= 100; l /= 100;
  const k = n => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = n => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1))));
  return [f(0), f(8), f(4)];
}

const rgbHex = rgb => "#" + rgb.map(v => v.toString(16).padStart(2, "0")).join("");

// WCAG relative luminance and contrast ratio, on [r, g, b] or "#rrggbb".
function luminance(rgb) {
  const [r, g, b] = rgb.map(v => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(a, b) {
  const [hi, lo] = [a, b].map(c => luminance(typeof c === "string" ? hexToRgb(c) : c)).sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// Dark ink of the theme's own hue, or near-white: whichever reads better on bg.
export function inkFor(bg, hue) {
  const dark = hslToRgb(hue, 65, 5);
  const light = [245, 247, 251];
  return contrast(dark, bg) >= contrast(light, bg) ? dark : light;
}

// Shades copy the saturation and lightness the hand-tuned green sits at
// (fill 54%/40%, hover 52%/51%, ...), so every hue reads with the same weight
// on the dark base. Duller picks scale down; louder ones are capped at green's.
const GREEN_SAT = 69;
const MIN_INK = 4.5;
// index.css's lightest surface (--surface-2), and the focus ring's alpha
// (--ring): the base palette never changes with the theme.
export const SURFACE_2 = "#191d28";
export const RING_ALPHA = 0.9;
const mixRgb = (top, under, a) => top.map((v, i) => Math.round(v * a + under[i] * (1 - a)));
export function palette(theme) {
  const [h, s] = rgbToHsl(hexToRgb(isHex(theme.base) ? theme.base : PRESETS[0].base));
  const k = Math.min(s, GREEN_SAT) / GREEN_SAT;
  const at = (sat, l) => hslToRgb(h, sat * k, l);
  const pinned = key => isHex(theme[key]) ? hexToRgb(theme[key]) : null;

  // The glow draws focus rings and icons: 3:1 on the lightest surface it
  // sits on, at the ring's 0.9 alpha (Iris's was 2.9:1).
  let glowL = 58;
  while (!pinned("glow") && glowL < 90 && contrast(mixRgb(at(69, glowL), hexToRgb(SURFACE_2), RING_ALPHA), SURFACE_2) < 3) glowL++;
  const glow = pinned("glow") || at(69, glowL);
  // A pinned glow is read on its own hue: ink on it is tinted the same.
  const gh = pinned("glow") ? rgbToHsl(glow)[0] : h;
  const fill = pinned("fill");
  // The derived fill starts at green's 40% lightness and goes up until the
  // theme's dark ink reads on it at 4.5:1 (button text is 13px bold: WCAG
  // AA's normal size). That also keeps it 3:1 or more from the dark page:
  // Ember, Ocean and Rose sat at 3.9:1 under their ink, Iris and Crimson's
  // fills at 2.2:1 against the page.
  let fillL = 40;
  while (!fill && fillL < 90 && contrast(at(54, fillL), hslToRgb(h, 65, 5)) < MIN_INK) fillL++;
  const accent = fill || at(54, fillL);
  // A pinned fill keeps its own hue for the hover/bright shades.
  const [fh, fs, fl] = fill ? rgbToHsl(fill) : [h, 54 * k, fillL];
  const hover = hslToRgb(fh, fs, Math.min(fl + 11, 92));
  const highlight = pinned("highlight") || at(73, 79);
  // Button text sits on the hover→fill gradient: judge it against the middle.
  const mid = accent.map((v, i) => Math.round((v + hover[i]) / 2));

  return {
    "--accent": rgbHex(accent),
    "--accent-hover": rgbHex(hover),
    "--accent-bright": rgbHex(hslToRgb(fh, fs, Math.min(fl + 19, 94))),
    "--accent-light": rgbHex(fill ? hslToRgb(fh, Math.min(fs, 40), 14) : at(40, 14)),
    "--accent-text": rgbHex(highlight),
    "--on-accent": rgbHex(pinned("buttonText") || inkFor(mid, fh)),
    "--on-glow": rgbHex(pinned("glowText") || inkFor(glow, gh)),
    "--glow": rgbHex(glow),
    "--accent-rgb": accent.join(", "),
    "--glow-rgb": glow.join(", "),
  };
}

// index.css's own values for Phosphor, kept in step with :root.
export const PHOSPHOR = {
  "--accent": "#2f9e4f", "--accent-hover": "#41c46a", "--accent-bright": "#54d77c",
  "--accent-light": "#16331f", "--accent-text": "#a5f0bf", "--on-accent": "#04130a",
  "--on-glow": "#04130a", "--glow": "#4ade80", "--accent-rgb": "47, 158, 79", "--glow-rgb": "74, 222, 128",
};

// Every variable a theme sets, so Phosphor can drop them all again.
const VARS = Object.keys(PHOSPHOR);

// Copying Phosphor pins its exact roles: its look comes from index.css, not
// from the derivation.
export const PHOSPHOR_PINS = {
  fill: PHOSPHOR["--accent"], glow: PHOSPHOR["--glow"], highlight: PHOSPHOR["--accent-text"],
  buttonText: PHOSPHOR["--on-accent"], glowText: PHOSPHOR["--on-glow"],
};

// What a theme actually paints, for previews.
export function themeColors(theme) {
  return theme.id === DEFAULT_THEME_ID ? PHOSPHOR : palette(theme);
}

function readJson(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
// False when the browser refuses (private mode, quota).
function write(key, value) {
  try { localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value)); return true; } catch { return false; }
}

// A stored theme as the app can use it, or null: whatever is in localStorage
// was written by someone, so only known fields with valid colours survive.
function clean(t) {
  if (!t || typeof t !== "object" || typeof t.id !== "string" || !t.id || !isHex(t.base)) return null;
  if (PRESETS.some(p => p.id === t.id)) return null;
  const out = { id: t.id, name: (typeof t.name === "string" && t.name.trim().slice(0, 32)) || "Custom", base: t.base };
  for (const r of ROLES) if (isHex(t[r.key])) out[r.key] = t[r.key];
  return out;
}

export function getCustomThemes() {
  const list = readJson(CUSTOM_KEY, []);
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  return list.map(clean).filter(t => t && !seen.has(t.id) && seen.add(t.id));
}

export function saveCustomThemes(list) { return write(CUSTOM_KEY, list.map(clean).filter(Boolean)); }

export function allThemes() { return [...PRESETS, ...getCustomThemes()]; }

export function getActiveTheme() {
  let id = DEFAULT_THEME_ID;
  try { id = localStorage.getItem(ACTIVE_KEY) || id; } catch { /* storage blocked */ }
  return allThemes().find(t => t.id === id) || PRESETS[0];
}

export const getActiveId = () => getActiveTheme().id;

export function applyTheme(theme) {
  const style = document.documentElement.style;
  // Phosphor: drop the overrides so index.css's hand-tuned green is used as is.
  if (theme.id === DEFAULT_THEME_ID) {
    VARS.forEach(v => style.removeProperty(v));
    return;
  }
  Object.entries(palette(theme)).forEach(([k, v]) => style.setProperty(k, v));
}

export function setActiveTheme(theme) {
  applyTheme(theme);
  return write(ACTIVE_KEY, theme.id);
}
