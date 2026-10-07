// Checks for lib/jobs.js: how an ended job reads in the history's Exit
// column and which toast style it gets (#139). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { endTone, jobExit, scriptJobToReopen } from "../src/lib/jobs.js";

test("Scripts opens again the log of the newest script run still going (#149)", () => {
  assert.equal(scriptJobToReopen(null), null);
  assert.equal(scriptJobToReopen([]), null);
  const jobs = [
    { id: 9, kind: "script-sync", state: "running" },     // a source's sync: not this page's
    { id: 8, kind: "script", state: "done" },
    { id: 7, kind: "script", state: "running" },
    { id: 6, kind: "script", state: "queued" },
    { id: 5, kind: "instaloader-profile", state: "running" },
  ];
  assert.equal(scriptJobToReopen(jobs).id, 7);
  assert.equal(scriptJobToReopen(jobs.slice(3)).id, 6);
  assert.equal(scriptJobToReopen([{ id: 3, kind: "script", state: "cancelled" }]), null);
});

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
