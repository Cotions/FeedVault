// Checks for lib/health.js: account health's badges and lines, and the
// Creators warning. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { healthBadge, lastGood, loginText, warnings } from "../src/lib/health.js";
import { scheduleShort, scheduleText } from "../src/lib/sourceOptions.js";

const NOW = Date.UTC(2026, 9, 2, 12);
const AT = NOW / 1000;

test("a badge for each state but ok", () => {
  assert.equal(healthBadge(null), null);
  assert.equal(healthBadge({ state: null }), null);
  assert.equal(healthBadge({ state: "ok" }), null);
  assert.deepEqual(healthBadge({ state: "not_found" }), { label: "not found", tone: "failed" });
  assert.deepEqual(healthBadge({ state: "login_required" }), { label: "login needed", tone: "failed" });
  assert.deepEqual(healthBadge({ state: "rate_limited" }), { label: "rate limited", tone: "interrupted" });
  assert.deepEqual(healthBadge({ state: "renamed" }), { label: "renamed", tone: "interrupted" });
  assert.deepEqual(healthBadge({ state: "private" }), { label: "private", tone: "failed" });
  assert.deepEqual(healthBadge({ state: "error" }), { label: "failed", tone: "failed" });
  assert.deepEqual(healthBadge({ state: "something new" }), { label: "failed", tone: "failed" });
});

test("last good sync", () => {
  assert.equal(lastGood({ result: null }, NOW), "");
  assert.equal(lastGood({ result: "failed", ok_at: AT - 3 * 3600 }, NOW), "last good sync 3h ago");
  assert.equal(lastGood({ result: "failed", ok_at: null }, NOW), "no sync has worked yet");
});

test("login state, from the last sync's output", () => {
  assert.equal(loginText({ login: null }), "");
  assert.equal(loginText({ login: { mode: "none", found: null, accepted: null } }), "");
  assert.equal(loginText({ login: { mode: "login", found: true, accepted: true } }), "saved login accepted by the last sync");
  assert.equal(loginText({ login: { mode: "cookies", found: false, accepted: false } }),
               "browser cookies not found by the last sync");
  assert.equal(loginText({ login: { mode: "cookies", found: true, accepted: false } }),
               "browser cookies refused by the last sync");
  assert.equal(loginText({ login: { mode: "cookies", found: true, accepted: null } }),
               "browser cookies found by the last sync");
  assert.equal(loginText({ login: { mode: "cookies", found: null, accepted: null } }), "");
});

test("the Creators warning says which source and why", () => {
  assert.deepEqual(warnings(undefined), []);
  assert.deepEqual(warnings([{ target: "a", health: { warning: null } }, { target: "b", health: null }]), []);
  assert.deepEqual(warnings([{ target: "a", health: { warning: "account not found" } },
                             { target: "b", health: { warning: "3 failed syncs in a row" } }], s => `@${s.target}`),
                   ["@a: account not found", "@b: 3 failed syncs in a row"]);
  // #138: a script its next sync would fail on.
  assert.deepEqual(warnings([{ target: "a", health: { warning: "account not found" }, options: { script: "greet" },
                               script_warning: { state: "refused", reason: "greet.sh is refused: not executable" } },
                             { target: "b", options: { script: null }, script_warning: null }]),
                   ["a: account not found", "a: script greet: greet.sh is refused: not executable"]);
});

test("a stopped schedule says why", () => {
  const s = { schedule: { every: "daily", next_at: null, paused: false, skipped: null, stopped: "paused: account not found",
                          failures: 2 }, last_result: { state: "failed", error: "not_found" } };
  assert.equal(scheduleText(s, {}, NOW), "daily · paused: account not found");
  assert.equal(scheduleShort(s, NOW), "stopped");
  assert.equal(scheduleText({ schedule: { ...s.schedule, every: "off", stopped: null } }, {}, NOW), "");
});
