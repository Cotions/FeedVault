// Checks for lib/fmt.js's counts as the page heads print them. Run with
// `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { distinctTail, fmtClock, fmtDay, fmtIn, plural } from "../src/lib/fmt.js";

// #139: a job waiting out the pause reads the same on Jobs as on Creators.
test("how long until a time: seconds under a minute, else minutes rounded up", () => {
  const now = 1_000_000_000_000;
  const at = s => now / 1000 + s;
  assert.equal(fmtIn(at(13), now), "in 13 s");
  assert.equal(fmtIn(at(0), now), "in 0 s");
  assert.equal(fmtIn(at(-5), now), "in 0 s");
  assert.equal(fmtIn(at(59.4), now), "in 59 s");
  assert.equal(fmtIn(at(60), now), "in 1 min");
  assert.equal(fmtIn(at(61), now), "in 2 min");
});

test("one is singular, any other count plural", () => {
  assert.equal(plural(1, "account"), "1 account");
  assert.equal(plural(0, "account"), "0 accounts");
  assert.equal(plural(2, "post"), "2 posts");
});

test("an irregular plural: the Creators head's people", () => {
  assert.equal(plural(1, "person", "people"), "1 person");
  assert.equal(plural(3, "person", "people"), "3 people");
});

// #126: two copies in folders of one name, under different parents.
test("a folder label shows the parents it takes to tell paths apart", () => {
  const a = "/vault/instagram/someone", b = "/vault/old/instagram/someone", c = "/vault/x/other";
  assert.equal(distinctTail(a, [a, c]), "someone");
  assert.equal(distinctTail(a, [a, b]), "vault/instagram/someone");
  assert.equal(distinctTail(b, [a, b]), "old/instagram/someone");
  assert.equal(distinctTail("/m/one/same", ["/m/one/same", "/m/two/same"]), "one/same");
  assert.equal(distinctTail("/m/one/same/", ["/m/one/same", "/m/one/same"]), "same");
  assert.equal(distinctTail("/", ["/"]), "/");
});

// The Trash page's History groups its rows by day, local time.
test("a day reads Today, Yesterday, then its date; a time reads as the clock", () => {
  const at = (y, m, d, h = 12, min = 0) => new Date(y, m, d, h, min).getTime() / 1000;
  const now = new Date(2026, 9, 21, 0, 30).getTime();   // just after midnight
  assert.equal(fmtDay(at(2026, 9, 21, 0, 10), now), "Today");
  assert.equal(fmtDay(at(2026, 9, 20, 23, 59), now), "Yesterday");
  assert.equal(fmtDay(at(2026, 9, 19), now), "Mon Oct 19");
  assert.equal(fmtDay(at(2025, 11, 31), now), "Wed Dec 31, 2025");
  assert.equal(fmtClock(at(2026, 9, 21, 9, 5)), "09:05");
  assert.equal(fmtClock(at(2026, 9, 21, 23, 59)), "23:59");
});
