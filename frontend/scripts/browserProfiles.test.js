// Checks for lib/browserProfiles.js: the Browser profile select's lines and
// the session it saves. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { BROWSERS, cookiesFix, cookiesSession, profileChoices, profileMissing } from "../src/lib/browserProfiles.js";

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

test("instaloader without browser_cookie3 is warned about only when known missing and cookies are used", () => {
  const command = "pipx inject instaloader browser_cookie3";
  const cookies = { mode: "cookies", browser: "firefox" };
  const tool = (found, login) => ({ tool: "instaloader", login, browser_cookie3: { found, command } });
  assert.equal(cookiesFix(tool(false, cookies)), command);
  assert.equal(cookiesFix(tool(true, cookies)), null);
  assert.equal(cookiesFix(tool(null, cookies)), null);                       // a system install: not known
  assert.equal(cookiesFix(tool(false, { mode: "none" })), null);
  assert.equal(cookiesFix(tool(false, { mode: "login", user: "me" })), null);
  assert.equal(cookiesFix({ tool: "yt-dlp", login: cookies, browser_cookie3: null }), null);
  assert.equal(cookiesFix({ tool: "instaloader", login: cookies }), null);   // an older backend
  assert.equal(cookiesFix(undefined), null);
});
