// Every page loads everything from FeedVault itself (#148): the fonts once
// came from Google Fonts, which saw each page load and failed offline. The
// source HTML and CSS, the build (vite.config.js's selfHosted plugin fails
// it) and, once built, dist itself point at no other origin; and the
// browser tests abort and fail any request to another one (harness.js's
// offsite, used by e2e/fixtures.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import config, { externalRefs } from "../vite.config.js";
import { offsite } from "../e2e/harness.js";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const read = p => fs.readFileSync(path.join(ROOT, p), "utf8");
const cssUnder = dir => fs.readdirSync(path.join(ROOT, dir), { recursive: true })
  .filter(f => f.endsWith(".css")).map(f => path.join(dir, f));

test("externalRefs finds another origin in a link, a script, an @import or a url()", () => {
  const html = `<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href='//cdn.example/x.css' rel=stylesheet><script src=http://cdn.example/a.js></script>
    <style>@font-face { src: url(https://fonts.gstatic.com/a.woff2) }</style>
    <div style="background: url('http://img.example/b.png')"></div>`;
  assert.deepEqual(externalRefs(html, "html"), [
    "https://fonts.gstatic.com", "//cdn.example/x.css", "http://cdn.example/a.js",
    "https://fonts.gstatic.com/a.woff2", "http://img.example/b.png",
  ]);
  const css = `@import "https://fonts.googleapis.com/css2?family=X";
    @import url(//fonts.googleapis.com/y);
    a { background: url( "HTTPS://x.example/a.png" ) }`;
  assert.deepEqual(externalRefs(css, "css"), [
    "//fonts.googleapis.com/y", "HTTPS://x.example/a.png", "https://fonts.googleapis.com/css2?family=X",
  ]);
});

test("externalRefs lets the page's own files and data: URLs through", () => {
  const html = `<link rel="icon" href="/favicon.svg" /><script type="module" crossorigin src="/assets/index-a.js"></script>
    <link rel="stylesheet" href="./assets/index.css"><a href="https://example.com/">a link is not a load</a>`;
  assert.deepEqual(externalRefs(html, "html"), []);
  const css = `@font-face { src: url(/assets/a-1.woff2) format('woff2') }
    .x { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3C/svg%3E") }
    .y { background: url(data:image/png;base64,AAAA) } @import "./other.css";`;
  assert.deepEqual(externalRefs(css, "css"), []);
});

test("index.html and the UI's style sheets point at no other origin", () => {
  for (const file of ["index.html", ...cssUnder("src")]) {
    assert.deepEqual(externalRefs(read(file), file.endsWith(".html") ? "html" : "css"), [], file);
  }
});

test("the build runs the check (vite.config.js's selfHosted plugin)", () => {
  const names = config.plugins.flat(Infinity).map(p => p?.name);
  assert.ok(names.includes("feedvault-self-hosted"), names.join(", "));
});

test("the fonts are the packages' woff2 files, Latin and Latin Extended only, with fallbacks", () => {
  const css = read("src/fonts.css");
  const files = [...css.matchAll(/url\('([^']+)'\)/g)].map(m => m[1]);
  assert.equal(files.length, 8);
  for (const f of files) {
    assert.match(f, /^@fontsource(-variable)?\/[a-z-]+\/files\/[a-z-]+-latin(-ext)?-(opsz|500|700|800)-normal\.woff2$/, f);
    assert.ok(fs.existsSync(path.join(ROOT, "node_modules", f)), `${f} is not installed`);
  }
  assert.match(read("src/main.jsx"), /import '\.\/fonts\.css'/);
  // A font that fails to load leaves the system's.
  const index = read("src/index.css");
  assert.match(index, /--mono: 'JetBrains Mono', [^;]*monospace;/);
  assert.match(index, /--display: 'Bricolage Grotesque', [^;]*sans-serif;/);
});

test("dist, once built, points at no other origin and has the fonts and their licences", { skip: !fs.existsSync(path.join(ROOT, "dist", "index.html")) && "not built" }, () => {
  const assets = fs.readdirSync(path.join(ROOT, "dist", "assets"));
  assert.deepEqual(externalRefs(read("dist/index.html"), "html"), []);
  const css = assets.filter(f => f.endsWith(".css")).map(f => read(`dist/assets/${f}`)).join("\n");
  assert.deepEqual(externalRefs(css, "css"), []);
  for (const face of ["Bricolage Grotesque", "JetBrains Mono"]) assert.ok(css.includes(face), face);
  for (const f of css.matchAll(/url\((\/assets\/[^)]+\.woff2)\)/g)) assert.ok(assets.includes(f[1].slice(8)), f[1]);
  assert.ok(assets.includes("fonts-bricolage-grotesque.LICENSE.txt") && assets.includes("fonts-jetbrains-mono.LICENSE.txt"));
  assert.match(read("dist/assets/fonts-jetbrains-mono.LICENSE.txt"), /SIL OPEN FONT LICENSE/);
});

test("offsite: only the instance's own origin stays in the machine", () => {
  const own = "http://127.0.0.1:45123";
  for (const url of [`${own}/`, `${own}/api/stats?x=1`, `${own}/assets/a.woff2`, "ws://127.0.0.1:45123/x",
    "data:font/woff2;base64,AA", "blob:http://127.0.0.1:45123/1", "about:blank"]) {
    assert.equal(offsite(url, own), false, url);
  }
  for (const url of ["https://fonts.googleapis.com/css2?family=X", "https://fonts.gstatic.com/a.woff2",
    "http://127.0.0.1:3380/api/stats", "http://localhost:45123/", "https://127.0.0.1:45123/",
    "http://127.0.0.1:45124/", "wss://example.com/", "not a url"]) {
    assert.equal(offsite(url, own), true, url);
  }
});
