// Checks for lib/browserProfiles.js: the Browser profile select's lines and
// the session it saves. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { BROWSERS, cookiesSession, profileChoices, profileMissing } from "../src/lib/browserProfiles.js";

const NAMES = ["cotions", "default", "test", "TRND"];
const DEFAULT = ["", "Default (the tool picks)"];

test("the default, then the browser's profiles", () => {
  assert.deepEqual(profileChoices(NAMES, ""), [DEFAULT, ...NAMES.map(n => [n, n])]);
  assert.deepEqual(profileChoices(NAMES, "test"), [DEFAULT, ...NAMES.map(n => [n, n])]);
  assert.deepEqual(profileChoices([], ""), [DEFAULT]);
});

test("a stored profile the browser no longer has is shown as missing, not dropped", () => {
  assert.deepEqual(profileChoices(NAMES, "work"), [DEFAULT, ["work", "work · not found"], ...NAMES.map(n => [n, n])]);
  assert.equal(profileMissing(NAMES, "work"), true);
  assert.equal(profileMissing(NAMES, "test"), false);
  assert.equal(profileMissing(NAMES, ""), false);
  assert.equal(profileMissing([], "work"), true);
  // A name is matched as it is: Firefox tells "Test" from "test".
  assert.equal(profileMissing(NAMES, "Test"), true);
});

test("while the list loads, the stored profile stays as it is", () => {
  assert.deepEqual(profileChoices(null, "test"), [DEFAULT, ["test", "test"]]);
  assert.equal(profileMissing(null, "test"), false);
  assert.deepEqual(profileChoices(null, ""), [DEFAULT]);
});

test("the session saved: no profile key without one, keys as the API answers them", () => {
  assert.deepEqual(cookiesSession("firefox", ""), { mode: "cookies", browser: "firefox" });
  assert.equal("profile" in cookiesSession("firefox", ""), false);
  assert.equal(JSON.stringify(cookiesSession("firefox", "test")), '{"browser":"firefox","mode":"cookies","profile":"test"}');
  assert.equal(JSON.stringify(cookiesSession("chrome", "")), '{"browser":"chrome","mode":"cookies"}');
  assert.deepEqual(BROWSERS, ["firefox", "chrome", "chromium", "brave", "edge"]);
});
