// The e2e harness (e2e/harness.js) refuses to run its throwaway instance on
// the live app's or testapp.sh's port, or with a config in the user's real
// FeedVault folders.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import {
  checkSafe, findPython, freePort, openRun, ownerAlive, pickPort, pidsIn, portBusy, protectedDirs,
  readOwner, startInstance, startTime, stopInstance, sweepStale,
} from "../e2e/harness.js";

const HOME = "/home/someone";
const ENV = {};                                   // no XDG overrides
const ok = { port: 45123, configPath: "/tmp/feedvault-e2e-abc/vault/config.json", root: "/tmp/feedvault-e2e-abc" };

test("ports 3380 and 3389 are refused", () => {
  for (const port of [3380, 3389]) {
    assert.throws(() => checkSafe({ ...ok, port }, ENV, HOME), /refusing port/);
  }
});

test("the live app's and testapp.sh's moved ports are refused too", () => {
  const env = { FEEDVAULT_PORT: "4100", FEEDVAULT_TEST_PORT: "04200" };
  assert.throws(() => checkSafe({ ...ok, port: 4100 }, env, HOME), /refusing port 4100 \(FEEDVAULT_PORT/);
  assert.throws(() => checkSafe({ ...ok, port: 4200 }, env, HOME), /refusing port 4200 \(FEEDVAULT_TEST_PORT/);
  checkSafe({ ...ok, port: 4300 }, env, HOME);
});

test("a free port that is a reserved one is passed over", async () => {
  const picks = [4100, 3380, 4200];
  const port = await pickPort({ FEEDVAULT_PORT: "4100" }, async () => picks.shift());
  assert.equal(port, 4200);
});

test("a bad port is refused", () => {
  for (const port of [0, -1, 70000, 1.5, NaN, undefined]) {
    assert.throws(() => checkSafe({ ...ok, port }, ENV, HOME), /bad port/);
  }
});

test("a config under ~/.config/feedvault is refused", () => {
  for (const configPath of [
    `${HOME}/.config/feedvault/config.json`,
    `${HOME}/.config/feedvault/config.test.json`,
    `${HOME}/.config/feedvault/sub/../config.json`,
  ]) {
    assert.throws(() => checkSafe({ ...ok, configPath }, ENV, HOME), /refusing/);
  }
});

test("a config under XDG_CONFIG_HOME/feedvault is refused", () => {
  const env = { XDG_CONFIG_HOME: "/elsewhere/cfg" };
  assert.throws(() => checkSafe({ ...ok, configPath: "/elsewhere/cfg/feedvault/config.json" }, env, HOME), /refusing/);
  // ~/.config/feedvault stays refused with XDG_CONFIG_HOME set.
  assert.throws(() => checkSafe({ ...ok, configPath: `${HOME}/.config/feedvault/config.json` }, env, HOME), /refusing/);
});

test("the demo folder, and a root that holds a protected folder, are refused", () => {
  assert.throws(() => checkSafe({ ...ok, root: `${HOME}/.cache/feedvault-demo` }, ENV, HOME), /refusing/);
  assert.throws(() => checkSafe({ ...ok, root: HOME }, ENV, HOME), /refusing/);
  assert.throws(() => checkSafe({ ...ok, root: `${HOME}/.config` }, ENV, HOME), /refusing/);
});

test("a tmp dir on a free port passes", async () => {
  assert.doesNotThrow(() => checkSafe(ok, ENV, HOME));
  assert.doesNotThrow(() => checkSafe({ ...ok, configPath: `${HOME}/.config/feedvault-other/config.json` }, ENV, HOME));
  const port = await freePort();
  assert.ok(port > 0 && port !== 3380 && port !== 3389);
});

test("the protected folders", () => {
  assert.deepEqual(protectedDirs(ENV, HOME), [
    path.join(HOME, ".config", "feedvault"),
    path.join(HOME, ".cache", "feedvault-demo"),
  ]);
});

test("startInstance refuses FEEDVAULT_E2E_PORT=3380 or 3389 before making anything", async () => {
  const before = process.env.FEEDVAULT_E2E_PORT;
  try {
    for (const port of ["3380", "3389"]) {
      process.env.FEEDVAULT_E2E_PORT = port;
      await assert.rejects(startInstance(), /refusing port/);
    }
  } finally {
    if (before === undefined) delete process.env.FEEDVAULT_E2E_PORT;
    else process.env.FEEDVAULT_E2E_PORT = before;
  }
});

test("a FEEDVAULT_E2E_PORT that is not a port is refused, not ignored", async () => {
  for (const v of ["0", "34o00", "-1", "99999"]) {
    await assert.rejects(pickPort({ FEEDVAULT_E2E_PORT: v }), /bad port/);
  }
  assert.equal(await pickPort({ FEEDVAULT_E2E_PORT: "45123" }), 45123);
});

test("a port something listens on is busy", async () => {
  const srv = net.createServer().listen(0, "127.0.0.1");
  await new Promise(r => srv.once("listening", r));
  try {
    assert.equal(await portBusy(srv.address().port), true);
  } finally {
    srv.close();
  }
});

test("a moved demo folder (FEEDVAULT_DEMO_DIR) is refused", () => {
  assert.throws(() => checkSafe({ ...ok, root: "/srv/fv-demo/x" }, { FEEDVAULT_DEMO_DIR: "/srv/fv-demo" }, HOME), /refusing/);
});

// An interrupted run (#110), with a stub backend: a Python that sleeps, in
// the run's tmp dir with its guard. Each test works in a tmp folder of its
// own (never the real /tmp/feedvault-e2e-*) and stops what it started by PID.
const LINUX = { skip: process.platform !== "linux" && "Linux's /proc and PR_SET_PDEATHSIG" };
const HARNESS = new URL("../e2e/harness.js", import.meta.url).href;
const STUB = ["-c", "import time; time.sleep(120)"];

function testTmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "fv-harness-test-"));
}

async function until(what, ok, ms = 10_000) {
  const end = Date.now() + ms;
  while (!ok()) {
    if (Date.now() > end) throw new Error(`timed out: ${what}`);
    await new Promise(r => setTimeout(r, 50));
  }
}

function stopPid(pid) {
  try { process.kill(pid, "SIGKILL"); } catch { /* gone already */ }
}

const cmdline = pid => {
  try { return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8"); } catch { return ""; }
};

// A Node process that opens a run in ``tmp`` and starts the stub backend
// through the harness, prints {root, pid}, then runs ``then`` (JS).
function runner(tmp, then) {
  const code = `
    import { findPython, openRun, launchBackend } from ${JSON.stringify(HARNESS)};
    const python = findPython();
    const inst = openRun({ python, tmp: ${JSON.stringify(tmp)} });
    launchBackend(inst, python, ${JSON.stringify(STUB)});
    console.log(JSON.stringify({ root: inst.root, pid: inst.pid }));
    ${then}`;
  const child = spawn(process.execPath, ["--input-type=module", "-e", code], { stdio: ["ignore", "pipe", "pipe"] });
  child.stderr.resume();
  const closed = new Promise(r => child.once("close", r));
  const started = new Promise((resolve, reject) => {
    let out = "";
    child.stdout.on("data", d => {
      out += d;
      if (out.includes("\n")) resolve(JSON.parse(out.split("\n")[0]));
    });
    closed.then(() => reject(new Error("the runner exited before starting the stub")));
  });
  return { child, started, closed };
}

test("a SIGKILLed runner's backend dies with it, and the next start deletes its tmp dir", LINUX, async () => {
  const tmp = testTmp();
  const { child, started, closed } = runner(tmp, "setInterval(() => {}, 1000);");
  let stub;
  try {
    const { root, pid } = await started;
    stub = pid;
    await until("the stub runs (past die-with-runner.py)", () => cmdline(stub).startsWith(`${findPython()}\0-c\0`));
    assert.deepEqual(pidsIn(root, readOwner(root).guard), [stub]);
    child.kill("SIGKILL");
    await closed;
    await until("the stub exits", () => startTime(stub) === null);
    assert.ok(fs.existsSync(root), "nothing ran on the runner's way out");
    assert.equal(ownerAlive(readOwner(root)), false);

    const inst = openRun({ python: findPython(), tmp });   // the next start
    assert.equal(fs.existsSync(root), false);
    assert.ok(fs.existsSync(inst.root));
    await stopInstance(inst);
    assert.deepEqual(fs.readdirSync(tmp), []);
  } finally {
    stopPid(child.pid);
    if (stub) stopPid(stub);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("a setup that exits or throws midway stops the backend and deletes its tmp dir", LINUX, async () => {
  for (const then of ["process.exit(130);", "throw new Error('setup failed');"]) {
    const tmp = testTmp();
    const { child, started, closed } = runner(tmp, then);
    let stub;
    try {
      const { root, pid } = await started;
      stub = pid;
      await closed;
      assert.equal(startTime(stub), null, `${then}: the stub is stopped`);
      assert.equal(fs.existsSync(root), false, `${then}: the tmp dir is deleted`);
      assert.deepEqual(fs.readdirSync(tmp), []);
    } finally {
      stopPid(child.pid);
      if (stub) stopPid(stub);
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }
});

// A process sleeping in ``cwd``, with FEEDVAULT_TEST_GUARD=``guard`` (none: unset).
function sleeper(cwd, guard) {
  const env = { ...process.env };
  delete env.FEEDVAULT_TEST_GUARD;
  if (guard !== undefined) env.FEEDVAULT_TEST_GUARD = guard;
  return spawn(process.execPath, ["-e", "setTimeout(() => {}, 120000)"], { cwd, env, stdio: "ignore" });
}

const guardFor = dir => JSON.stringify({ roots: [fs.realpathSync(dir)] });
const sleeping = procs => () => procs.every(p => cmdline(p.pid).includes("setTimeout"));

test("pidsIn finds only the processes in the dir that carry the run's guard", LINUX, async () => {
  const tmp = testTmp();
  const guard = guardFor(tmp);
  // The run's; no guard (a shell cd'd there); another run's guard; the guard, elsewhere.
  const procs = [sleeper(tmp, guard), sleeper(tmp), sleeper(tmp, guardFor(os.tmpdir())), sleeper(os.tmpdir(), guard)];
  try {
    await until("the sleepers start", sleeping(procs));
    assert.deepEqual(pidsIn(tmp, guard), [procs[0].pid]);
    assert.deepEqual(pidsIn(tmp), []);
    assert.deepEqual(pidsIn(tmp, ""), []);
  } finally {
    for (const p of procs) stopPid(p.pid);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("the sweep leaves a live runner's dir and an unowned one, and kills only a dead run's own", LINUX, async () => {
  const tmp = testTmp();
  const boot = fs.readFileSync("/proc/sys/kernel/random/boot_id", "utf8").trim();
  const make = (name, owner) => {
    const dir = path.join(tmp, `feedvault-e2e-${name}`);
    fs.mkdirSync(dir);
    const guard = guardFor(dir);
    if (owner) fs.writeFileSync(path.join(dir, "owner.pid"), JSON.stringify({ ...owner, boot, guard }));
    return { dir, guard };
  };
  const live = make("live", { pid: process.pid, start: startTime(process.pid) });
  const dead = make("dead", { pid: process.pid, start: "1" });   // this PID, but not this process
  const none = make("none", null);
  const procs = {
    live: sleeper(live.dir, live.guard),
    dead: sleeper(dead.dir, dead.guard),
    shell: sleeper(dead.dir),                                     // not the run's: left running
    none: sleeper(none.dir, none.guard),
  };
  try {
    await until("the sleepers start", sleeping(Object.values(procs)));
    const logs = [];
    assert.deepEqual(sweepStale({ tmp, log: m => logs.push(m) }), [dead.dir]);
    assert.equal(fs.existsSync(dead.dir), false);
    await until("the dead run's process exits", () => startTime(procs.dead.pid) === null);
    for (const k of ["live", "shell", "none"]) assert.notEqual(startTime(procs[k].pid), null, `${k} is left running`);
    assert.ok(fs.existsSync(live.dir) && fs.existsSync(none.dir));
    assert.ok(logs.some(m => m.includes("no owner.pid")));
  } finally {
    for (const p of Object.values(procs)) stopPid(p.pid);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
