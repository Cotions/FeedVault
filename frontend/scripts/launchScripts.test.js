// run.sh and testapp.sh, read as text. Neither is started but with --help
// (which prints and exits before anything else); run.sh's port block runs on
// its own in bash, with no backend, Vite or npm behind it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../..", import.meta.url));
const runSh = readFileSync(`${repo}/run.sh`, "utf8");

// The lines from "# --- port ---" to "# --- port end ---", with run.sh's die().
function portBlock() {
  const m = runSh.match(/^# --- port -+\n[\s\S]*?^# --- port end -+$/m);
  assert.ok(m, "run.sh has a port block");
  const die = runSh.match(/^die\(\) .*$/m);
  assert.ok(die, "run.sh has die()");
  return `set -euo pipefail\n${die[0]}\n${m[0]}\nprintf '%s %s' "$PORT" "$FEEDVAULT_PORT"\n`;
}

function runPortBlock(port) {
  const env = { PATH: process.env.PATH };
  if (port !== undefined) env.FEEDVAULT_PORT = port;
  return spawnSync("bash", ["-c", portBlock()], { env, encoding: "utf8" });
}

test("run.sh exports the port it uses, 3380 by default (#100)", () => {
  for (const [given, used] of [[undefined, "3380"], ["", "3380"], ["4000", "4000"], ["65535", "65535"], ["03389", "3389"]]) {
    const got = runPortBlock(given);
    assert.equal(got.status, 0, `${given}: ${got.stderr}`);
    assert.equal(got.stdout, `${used} ${used}`, String(given));
  }
});

test("run.sh stops on an invalid FEEDVAULT_PORT instead of using 3380", () => {
  for (const bad of ["abc", "0", "65536", "99999", "-1", "4000x", "1e3", "123456", "4000);import os"]) {
    const got = runPortBlock(bad);
    assert.notEqual(got.status, 0, bad);
    assert.equal(got.stdout, "", bad);
    assert.match(got.stderr, /FEEDVAULT_PORT must be a port number from 1 to 65535/, bad);
  }
});

test("run.sh checks and exports the port before dev mode starts Vite", () => {
  const block = runSh.indexOf("# --- port end");
  const dev = runSh.indexOf('if [ "$MODE" = dev ]');
  assert.ok(block > 0 && dev > block, "the port block comes before dev mode");
  assert.ok(runSh.indexOf("python3 -c \"import socket") > block, "the busy-port check uses the checked port");
  const devBranch = runSh.slice(dev, runSh.indexOf("\nfi\n", dev));
  assert.match(devBranch, /proxies \/api and \/media to :\$PORT/);
  assert.doesNotMatch(devBranch, /3380/);
});

// --help prints the comment block under the #! line, and only that.
for (const script of ["run.sh", "testapp.sh"]) {
  test(`${script} --help prints its header and nothing else`, () => {
    const text = readFileSync(`${repo}/${script}`, "utf8").split("\n");
    const header = [];
    for (const line of text.slice(1)) {
      if (!line.startsWith("#")) break;
      header.push(line.replace(/^# ?/, ""));
    }
    const home = mkdtempSync(`${tmpdir()}/feedvault-help-`);
    try {
      // No terminal: FEEDVAULT_NO_TERMINAL keeps run.sh from opening one.
      const got = spawnSync("bash", [`${repo}/${script}`, "--help"], {
        env: { PATH: process.env.PATH, HOME: home, TERM: "dumb", FEEDVAULT_NO_TERMINAL: "1" },
        encoding: "utf8", timeout: 10000,
      });
      assert.equal(got.status, 0, got.stderr);
      assert.equal(got.stdout, header.join("\n") + "\n");
      assert.doesNotMatch(got.stdout, /set -euo|ROOT=/);
    } finally {
      rmSync(home, { recursive: true, force: true });
    }
  });
}
