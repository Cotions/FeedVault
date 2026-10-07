// Checks for lib/notify.js: where a notifications entry leads. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { notificationPath } from "../src/lib/notify.js";

test("new posts open the Feed on that entry's posts", () => {
  assert.equal(notificationPath({ id: 42, kind: "new", source_id: 5, person_id: 3 }), "/?notification=42");
});

test("a failure of a person's source opens their page", () => {
  assert.equal(notificationPath({ id: 41, kind: "failed", source_id: 5, person_id: 3 }), "/people/3");
});

test("a failure of a source with no person opens Creators on that source (#123)", () => {
  assert.equal(notificationPath({ id: 40, kind: "failed", source_id: 6, person_id: null }), "/creators?source=6");
  assert.equal(notificationPath({ id: 39, kind: "failed", source_id: 0, person_id: null }), "/creators?source=0");
});

test("a failure whose source is unknown opens Creators", () => {
  assert.equal(notificationPath({ id: 38, kind: "failed", source_id: null, person_id: null }), "/creators");
  assert.equal(notificationPath({ id: 37, kind: "failed", person_id: null }), "/creators");
});
