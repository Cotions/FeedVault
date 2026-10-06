// How CI splits the browser tests (.github/workflows/ci.yml, job e2e-shard):
// one job per shard, each on a runner of its own with its own throwaway
// instance (global-setup.js), so no two shards share a vault, a port or a
// browser. A shard is a set of Playwright projects, balanced by their CI
// times; every project is in exactly one (scripts/e2eShards.test.js checks
// it against playwright.config.js, so a new project fails npm test until it
// has a shard). npm run e2e, locally, still runs every project in one go.
//
//   node e2e/shards.js <n>   prints shard n's --project arguments
import { pathToFileURL } from "node:url";

export const SHARDS = [
  ["layout"],
  ["states"],
  ["layout-zoom", "themes"],
  ["desktop", "phone"],
];

export function shardArgs(n) {
  const shard = /^\d+$/.test(String(n)) ? SHARDS[Number(n) - 1] : undefined;
  if (!shard) throw new Error(`e2e: no shard ${n} (1 to ${SHARDS.length})`);
  return shard.map(p => `--project=${p}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  console.log(shardArgs(process.argv[2]).join(" "));
}
