// run.sh and testapp.sh, read as text. Neither is started but with --help
// (which prints and exits before anything else); run.sh's port block runs on
// its own in bash, with no backend, Vite or npm behind it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
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
  for (const [given, used] of [[undefined, "3380"], ["4000", "4000"], ["65535", "65535"], ["03389", "3389"]]) {
    const got = runPortBlock(given);
    assert.equal(got.status, 0, `${given}: ${got.stderr}`);
    assert.equal(got.stdout, `${used} ${used}`, String(given));
  }
});

test("run.sh stops on an invalid FEEDVAULT_PORT instead of using 3380", () => {
  for (const bad of ["", "abc", "0", "65536", "99999", "-1", "4000x", "1e3", "123456", "4000);import os"]) {
    const got = runPortBlock(bad);
    assert.notEqual(got.status, 0, bad);
    assert.equal(got.stdout, "", bad);
    assert.match(got.stderr, /FEEDVAULT_PORT must be a port number from 1 to 65535/, bad);
  }
});

test("run.sh checks and exports the port before dev mode starts Vite", () => {
  const block = runSh.indexOf("# --- port end");
  assert.ok(block > runSh.indexOf('exec "$VENV/bin/python" -m pytest'), "--test never reads the port");
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

test("testapp.sh checks FEEDVAULT_TEST_PORT before any use", () => {
  const text = readFileSync(`${repo}/testapp.sh`, "utf8");
  const check = text.indexOf("FEEDVAULT_TEST_PORT must be a port number from 1 to 65535");
  assert.ok(check > 0 && check < text.indexOf("python3 -c \"import socket"), "checked before the Python port check");
  assert.match(text, /^PORT="\$\{FEEDVAULT_TEST_PORT-3389\}"$/m);
});

// testapp.sh's port block, run on its own the same way.
function testappPort(env) {
  const sh = readFileSync(`${repo}/testapp.sh`, "utf8");
  const m = sh.match(/^# --- port -+\n[\s\S]*?^# --- port end -+$/m);
  assert.ok(m, "testapp.sh has a port block");
  const die = sh.match(/^die\(\) .*$/m)[0];
  const port = sh.match(/^PORT=.*$/m)[0];
  const script = `set -euo pipefail\n${die}\n${port}\n${m[0]}\nprintf '%s' "$PORT"\n`;
  return spawnSync("bash", ["-c", script], { env: { PATH: process.env.PATH, ...env }, encoding: "utf8" });
}

test("testapp.sh never takes the live app's port, moved or not", () => {
  for (const env of [{ FEEDVAULT_TEST_PORT: "3380" }, { FEEDVAULT_TEST_PORT: "03380" },
                     { FEEDVAULT_TEST_PORT: "4100", FEEDVAULT_PORT: "4100" },
                     { FEEDVAULT_TEST_PORT: "4100", FEEDVAULT_PORT: "04100" }]) {
    const got = testappPort(env);
    assert.notEqual(got.status, 0, JSON.stringify(env));
    assert.match(got.stderr, /must not be the live app's port/);
  }
  for (const [env, used] of [[{}, "3389"], [{ FEEDVAULT_TEST_PORT: "4100", FEEDVAULT_PORT: "4000" }, "4100"],
                             [{ FEEDVAULT_PORT: "junk" }, "3389"]]) {
    const got = testappPort(env);
    assert.equal(got.status, 0, got.stderr);
    assert.equal(got.stdout, used);
  }
});

// testapp.sh's test data blocks (#111), run on their own the same way, in a
// tmp dir with a fake HOME and a fake live config: never the real ones.
// "--- test data" only resolves and checks (it writes nothing); "--- test
// copy" resets and copies, and only runs where a regression could do no
// more than delete part of the tmp dir.
function testappData({ mode = "reset", testData, config, copy = true, cwd }) {
  const sh = readFileSync(`${repo}/testapp.sh`, "utf8");
  const block = name => {
    const m = sh.match(new RegExp(`^# --- ${name} -+\\n[\\s\\S]*?^# --- ${name} end -+$`, "m"));
    assert.ok(m, `testapp.sh has a ${name} block`);
    return m[0];
  };
  const line = re => { const m = sh.match(re); assert.ok(m, String(re)); return m[0]; };
  const script = ["set -euo pipefail", line(/^say\(\) .*$/m), line(/^die\(\) .*$/m), `MODE=${mode}`,
                  line(/^CONFIG_HOME=.*$/m), line(/^LIVE_CONFIG=.*$/m), block("test data"),
                  ...(copy ? [block("test copy")] : []), `printf '%s' "$TEST_DATA"`].join("\n");
  const t = config.root;
  writeFileSync(`${t}/home/.config/feedvault/config.json`, JSON.stringify(config.json));
  const env = { PATH: process.env.PATH, HOME: `${t}/home` };
  if (testData !== undefined) env.FEEDVAULT_TEST_DATA = testData;
  return spawnSync("bash", ["-c", script], { env, cwd: cwd || `${t}/cwd`, encoding: "utf8", timeout: 10000 });
}

function fakeLive() {
  const t = realpathSync(mkdtempSync(`${tmpdir()}/feedvault-testdata-`));
  for (const d of ["home/.config/feedvault", "cwd", "live/data", "media/creator"]) mkdirSync(`${t}/${d}`, { recursive: true });
  writeFileSync(`${t}/live/data/feedvault.db`, "");           // an empty SQLite database
  writeFileSync(`${t}/home/keep`, "mine");
  writeFileSync(`${t}/media/creator/keep`, "mine");
  symlinkSync(`${t}/live/data`, `${t}/link`);
  const json = { data_directory: `${t}/live/data/`, media_roots: [`${t}/media`, `${t}/gone/media`] };
  writeFileSync(`${t}/home/.config/feedvault/config.json`, JSON.stringify(json));
  return { root: t, json };
}

// Every file under dir, relative, sorted.
function tree(dir) {
  return readdirSync(dir, { recursive: true }).map(String).sort();
}

test("testapp.sh refuses a test data dir that is or holds HOME, the live data or a media folder (#111)", () => {
  const config = fakeLive();
  const t = config.root;
  try {
    const before = tree(t);
    for (const [dir, why] of [[`${t}/home`, /would hold/], [t, /overlaps|would hold/],
                              [`${t}/live/data`, /overlaps/], [`${t}/live/data/`, /overlaps/],
                              [`${t}/live/data/../data`, /overlaps/], [`${t}/link`, /overlaps/],
                              [`${t}/live/data/sub`, /overlaps/], [`${t}/live`, /overlaps/],
                              [`${t}/media`, /overlaps/], [`${t}/media/creator`, /overlaps/],
                              [`${t}/gone`, /overlaps/], [`${t}/gone/media/x`, /overlaps/]]) {
      for (const mode of ["reset", "start"]) {
        const got = testappData({ mode, testData: dir, config });
        assert.notEqual(got.status, 0, `${mode} ${dir}`);
        assert.match(got.stderr, why, `${mode} ${dir}`);
        assert.equal(got.stdout, "", `${mode} ${dir}`);
      }
    }
    for (const dir of ["rel", "./x", "~/x"]) {
      const got = testappData({ testData: dir, config });
      assert.notEqual(got.status, 0, dir);
      assert.match(got.stderr, /FEEDVAULT_TEST_DATA must be an absolute path/, dir);
    }
    // The checks alone for these: nothing after them runs even if they fail.
    for (const dir of ["/", "//", "/.", "/tmp/.."]) {
      const got = testappData({ testData: dir, config, copy: false });
      assert.notEqual(got.status, 0, dir);
      assert.match(got.stderr, /overlaps|would hold \//, dir);
    }
    assert.deepEqual(tree(t), before);
    assert.equal(readFileSync(`${t}/home/keep`, "utf8"), "mine");
    assert.equal(readFileSync(`${t}/media/creator/keep`, "utf8"), "mine");
  } finally {
    rmSync(t, { recursive: true, force: true });
  }
});

test("testapp.sh refuses an empty, null or relative data_directory (#111)", () => {
  const config = fakeLive();
  const t = config.root;
  try {
    for (const data of ["", null, "relative/data", "None", undefined, 3]) {
      const json = { ...config.json, data_directory: data };
      if (data === undefined) delete json.data_directory;
      const got = testappData({ config: { root: t, json }, copy: false });
      assert.notEqual(got.status, 0, String(data));
      assert.match(got.stderr, /data_directory in .* must be an absolute path/, String(data));
    }
    assert.deepEqual(readdirSync(`${t}/cwd`), []);
  } finally {
    rmSync(t, { recursive: true, force: true });
  }
});

test("testapp.sh resets and writes into only a folder it marked (#111)", () => {
  const config = fakeLive();
  const t = config.root;
  try {
    // A folder it did not make: refused, left as it is, with how to remove it.
    mkdirSync(`${t}/old`);
    writeFileSync(`${t}/old/feedvault.db`, "old copy");
    writeFileSync(`${t}/old/notes`, "mine");
    for (const mode of ["reset", "start"]) {
      const got = testappData({ mode, testData: `${t}/old`, config });
      assert.notEqual(got.status, 0, mode);
      assert.match(got.stderr, /no \.feedvault-test marker/, mode);
      assert.ok(got.stderr.includes(`rm -rf -- '${t}/old'`), got.stderr);
    }
    // A marker that is a symlink does not count.
    symlinkSync(`${t}/home/keep`, `${t}/old/.feedvault-test`);
    assert.notEqual(testappData({ testData: `${t}/old`, config }).status, 0);
    assert.deepEqual(tree(`${t}/old`), [".feedvault-test", "feedvault.db", "notes"]);
    assert.equal(readFileSync(`${t}/home/keep`, "utf8"), "mine");

    // The default, <data_directory>-test: made and marked, then reset.
    const made = `${t}/live/data-test`;
    let got = testappData({ mode: "start", config });
    assert.equal(got.status, 0, got.stderr);
    assert.equal(got.stdout.split("\n").pop(), made);
    assert.deepEqual(tree(made), [".feedvault-test", "feedvault.db"]);
    writeFileSync(`${made}/scratch`, "");
    got = testappData({ mode: "start", config });
    assert.equal(got.status, 0, got.stderr);
    assert.deepEqual(tree(made), [".feedvault-test", "feedvault.db", "scratch"]);
    got = testappData({ mode: "reset", config });
    assert.equal(got.status, 0, got.stderr);
    assert.match(got.stdout, /Removing the old copy/);
    assert.deepEqual(tree(made), [".feedvault-test", "feedvault.db"]);
    assert.equal(readFileSync(`${t}/live/data/feedvault.db`, "utf8"), "");
  } finally {
    rmSync(t, { recursive: true, force: true });
  }
});
