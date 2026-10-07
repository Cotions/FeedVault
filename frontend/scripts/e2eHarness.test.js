// The e2e harness (e2e/harness.js) refuses to run its throwaway instance on
// the live app's or testapp.sh's port, or with a config in the user's real
// FeedVault folders.
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import net from "node:net";
import { checkSafe, freePort, pickPort, portBusy, protectedDirs, startInstance } from "../e2e/harness.js";

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
