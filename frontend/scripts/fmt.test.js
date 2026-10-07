// Checks for lib/fmt.js's counts as the page heads print them. Run with
// `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { distinctTail, plural } from "../src/lib/fmt.js";

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
