// The Vite dev server (#73): no CORS for other localhost pages, no other port;
// its proxy follows FEEDVAULT_PORT (#100).
// Only this Vite server is asked, for its own files: never /api, so nothing
// reaches the backend it proxies to.
import { test } from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { createServer, loadConfigFromFile } from "vite";
import { backendPort } from "../vite.config.js";

const root = fileURLToPath(new URL("..", import.meta.url));

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once("error", reject);
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

function devServer(port) {
  return createServer({ root, logLevel: "silent", server: { port, host: "127.0.0.1" } });
}

test("a page on another localhost port gets no CORS answer", async () => {
  const port = await freePort();
  const server = await devServer(port);
  try {
    await server.listen();
    const origin = "http://localhost:4444";
    const got = await fetch(`http://127.0.0.1:${port}/@vite/client`, { headers: { Origin: origin } });
    assert.equal(got.status, 200);
    assert.equal(got.headers.get("access-control-allow-origin"), null);
    const preflight = await fetch(`http://127.0.0.1:${port}/@vite/client`, {
      method: "OPTIONS",
      headers: { Origin: origin, "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "x-feedvault" },
    });
    assert.equal(preflight.headers.get("access-control-allow-origin"), null);
    assert.equal(preflight.headers.get("access-control-allow-headers"), null);
  } finally {
    await server.close();
  }
});

test("a port in use stops the dev server instead of picking another", async () => {
  const port = await freePort();
  const taken = net.createServer();
  await new Promise(resolve => taken.listen(port, "127.0.0.1", resolve));
  const server = await devServer(port);
  try {
    await assert.rejects(server.listen(), /already in use/);
  } finally {
    await server.close();                     // on another port, or never listening: the test ends
    await new Promise(resolve => taken.close(resolve));
  }
});

// The proxy goes to the backend's port, FEEDVAULT_PORT (#100), never to 3380
// when another port is set. The config is only loaded: nothing listens or
// sends a request.
async function proxyTargets(port) {
  const saved = process.env.FEEDVAULT_PORT;
  if (port === undefined) delete process.env.FEEDVAULT_PORT;
  else process.env.FEEDVAULT_PORT = port;
  try {
    const loaded = await loadConfigFromFile({ command: "serve", mode: "development" }, undefined, root, "silent");
    return Object.values(loaded.config.server.proxy).map(p => p.target);
  } finally {
    if (saved === undefined) delete process.env.FEEDVAULT_PORT;
    else process.env.FEEDVAULT_PORT = saved;
  }
}

test("every proxy goes to FEEDVAULT_PORT when it is set", async () => {
  const targets = await proxyTargets("4000");
  assert.equal(targets.length, 4);
  for (const target of targets) assert.equal(target, "http://127.0.0.1:4000");
});

test("every proxy goes to 3380 when FEEDVAULT_PORT is unset", async () => {
  for (const target of await proxyTargets(undefined)) assert.equal(target, "http://127.0.0.1:3380");
});

test("an invalid FEEDVAULT_PORT stops the config instead of falling back to 3380", async () => {
  for (const bad of ["", "abc", "0", "65536", "99999", "-1", "4000x", " 4000", "4000.5", "1e3", "123456"]) {
    await assert.rejects(proxyTargets(bad), /FEEDVAULT_PORT must be a port number from 1 to 65535/, bad);
  }
});

test("backendPort accepts the whole range", () => {
  assert.equal(backendPort(undefined), 3380);
  assert.equal(backendPort("1"), 1);
  assert.equal(backendPort("65535"), 65535);
  assert.equal(backendPort("03389"), 3389);
});
