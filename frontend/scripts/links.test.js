// Checks for lib/links.js: the address a link form sends (#126). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { withScheme } from "../src/lib/links.js";

test("an address with no scheme gets https://", () => {
  assert.equal(withScheme("example.org/x"), "https://example.org/x");
  assert.equal(withScheme("  www.patreon.com/someone "), "https://www.patreon.com/someone");
  assert.equal(withScheme("example.org"), "https://example.org");
  assert.equal(withScheme("example.org:8080/x"), "https://example.org:8080/x");
  assert.equal(withScheme("localhost:8080"), "https://localhost:8080");
  assert.equal(withScheme("//example.org/x"), "https://example.org/x");
  assert.equal(withScheme("example.org/a?b=c:d"), "https://example.org/a?b=c:d");
});

test("a typed scheme stays, for the server to take or refuse", () => {
  assert.equal(withScheme("http://example.org"), "http://example.org");
  assert.equal(withScheme("HTTPS://Example.org/x"), "HTTPS://Example.org/x");
  assert.equal(withScheme("javascript:alert(1)"), "javascript:alert(1)");
  assert.equal(withScheme("mailto:someone@example.org"), "mailto:someone@example.org");
  assert.equal(withScheme("ftp://example.org"), "ftp://example.org");
});

test("text that names no host stays as typed", () => {
  assert.equal(withScheme("hello"), "hello");
  assert.equal(withScheme("not an address.org"), "not an address.org");
  assert.equal(withScheme(""), "");
});
