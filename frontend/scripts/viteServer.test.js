// The Vite dev server (#73): no CORS for other localhost pages, no other port.
// Only this Vite server is asked, for its own files: never /api, so nothing
// reaches the backend it proxies to.
import { test } from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

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
