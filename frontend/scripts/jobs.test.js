// Checks for lib/jobs.js: how an ended job reads in the history's Exit
// column and which toast style it gets (#139). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { endTone, jobExit } from "../src/lib/jobs.js";

test("a cancelled job's signal is not shown as its exit code", () => {
  assert.deepEqual(jobExit({ state: "cancelled", exit_code: -15 }),
    { text: "—", title: "Cancelled: ended by signal 15 (exit code -15)" });
  assert.deepEqual(jobExit({ state: "cancelled", exit_code: -9 }),
    { text: "—", title: "Cancelled: ended by signal 9 (exit code -9)" });
  assert.deepEqual(jobExit({ state: "cancelled", exit_code: null }), { text: "—", title: undefined });
  // Cancelled while queued: it never ran, no code.
  assert.deepEqual(jobExit({ state: "cancelled" }), { text: "—", title: undefined });
});

test("other jobs show their code; a signal says so in the title", () => {
  assert.deepEqual(jobExit({ state: "done", exit_code: 0 }), { text: "0", title: undefined });
  assert.deepEqual(jobExit({ state: "failed", exit_code: 1 }), { text: "1", title: undefined });
  assert.deepEqual(jobExit({ state: "failed", exit_code: -11 }),
    { text: "-11", title: "ended by signal 11 (exit code -11)" });
  assert.deepEqual(jobExit({ state: "interrupted", exit_code: null }), { text: "—", title: undefined });
});

test("a job the user stopped gets the neutral toast, not the success one", () => {
  assert.equal(endTone({ state: "done" }), "ok");
  assert.equal(endTone({ state: "failed" }), "err");
  assert.equal(endTone({ state: "cancelled" }), "info");
  assert.equal(endTone({ state: "interrupted" }), "info");
});
