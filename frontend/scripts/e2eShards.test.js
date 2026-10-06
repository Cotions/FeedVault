// CI's e2e shards (e2e/shards.js) cover every Playwright project of
// playwright.config.js exactly once: none left out, none run twice.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import config from "../playwright.config.js";
import { SHARDS, shardArgs } from "../e2e/shards.js";

test("every project is in exactly one shard", () => {
  const projects = config.projects.map(p => p.name).sort();
  const sharded = SHARDS.flat().sort();
  assert.deepEqual(sharded, projects);
  assert.equal(new Set(sharded).size, sharded.length);
});

test("no shard is empty", () => {
  for (const s of SHARDS) assert.ok(s.length > 0);
});

test("shardArgs gives a shard's --project arguments, and refuses one that does not exist", () => {
  assert.deepEqual(shardArgs(1), SHARDS[0].map(p => `--project=${p}`));
  assert.deepEqual(shardArgs(String(SHARDS.length)), SHARDS.at(-1).map(p => `--project=${p}`));
  for (const n of [0, SHARDS.length + 1, "", "1 --grep x", "-1", undefined]) {
    assert.throws(() => shardArgs(n), /no shard/);
  }
});

test("node e2e/shards.js <n> prints them", () => {
  const out = execFileSync(process.execPath, ["e2e/shards.js", "2"], { encoding: "utf8" }).trim();
  assert.equal(out, shardArgs(2).join(" "));
});

test("CI's matrix runs every shard, and only those", () => {
  const ci = fs.readFileSync(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
  const job = ci.slice(ci.indexOf("\n  e2e-shard:"), ci.indexOf("\n  e2e:\n"));
  const n = SHARDS.length;
  assert.match(job, new RegExp(`\\n {8}shard: \\[${Array.from({ length: n }, (_, i) => i + 1).join(", ")}\\]\\n`));
  assert.match(job, new RegExp(`name: e2e shard \\$\\{\\{ matrix.shard \\}\\}/${n}\\n`));
});
