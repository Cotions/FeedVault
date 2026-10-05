// Checks for lib/fmt.js's counts as the page heads print them. Run with
// `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { plural } from "../src/lib/fmt.js";

test("one is singular, any other count plural", () => {
  assert.equal(plural(1, "account"), "1 account");
  assert.equal(plural(0, "account"), "0 accounts");
  assert.equal(plural(2, "post"), "2 posts");
});

test("an irregular plural: the Creators head's people", () => {
  assert.equal(plural(1, "person", "people"), "1 person");
  assert.equal(plural(3, "person", "people"), "3 people");
});
