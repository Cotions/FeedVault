// Checks for lib/openIn.js: which click a set default takes over, and the
// "Open in…" menu's choices and names. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { choiceLabel, isPlainClick, openChoices, sameChoice } from "../src/lib/openIn.js";

const BROWSERS = [
  { id: "firefox", name: "Firefox", flatpak: false, profiles: ["default", "work"] },
  { id: "edge", name: "Microsoft Edge", flatpak: true, profiles: [] },
];

test("only a plain left click is taken over", () => {
  const click = { button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false };
  assert.equal(isPlainClick(click), true);
  for (const k of ["ctrlKey", "metaKey", "shiftKey", "altKey"]) assert.equal(isPlainClick({ ...click, [k]: true }), false, k);
  assert.equal(isPlainClick({ ...click, button: 1 }), false);
});

test("each profile, normal then private; a browser with none once", () => {
  assert.deepEqual(openChoices(BROWSERS), [
    { browser: "firefox", profile: "default", private: false },
    { browser: "firefox", profile: "default", private: true },
    { browser: "firefox", profile: "work", private: false },
    { browser: "firefox", profile: "work", private: true },
    { browser: "edge", profile: null, private: false },
    { browser: "edge", profile: null, private: true },
  ]);
  assert.deepEqual(openChoices(null), []);
});

test("names", () => {
  assert.equal(choiceLabel(null, BROWSERS), "This browser");
  assert.equal(choiceLabel({ browser: "firefox", profile: "work", private: false }, BROWSERS), "Firefox · work");
  assert.equal(choiceLabel({ browser: "edge", profile: null, private: true }, BROWSERS), "Microsoft Edge · private");
  // One no longer installed: its id.
  assert.equal(choiceLabel({ browser: "brave", profile: "Default", private: false }, BROWSERS), "brave · Default");
});

test("the same choice", () => {
  assert.equal(sameChoice(null, null), true);
  assert.equal(sameChoice(null, { browser: "firefox" }), false);
  assert.equal(sameChoice({ browser: "edge", profile: null, private: false }, { browser: "edge", profile: "", private: false }), true);
  assert.equal(sameChoice({ browser: "edge", profile: null, private: true }, { browser: "edge", profile: null, private: false }), false);
});
