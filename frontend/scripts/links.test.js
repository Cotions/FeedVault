// Checks for lib/links.js: the address a link form sends (#126). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { MAX_TITLE, droppedUrl, externalUrl, mayCarryUrl, sharedTitle, urlLines, withScheme } from "../src/lib/links.js";
import { NO_PERSON, personOptions } from "../src/lib/people.js";

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

test("an outside address is one http(s) URL, or nothing", () => {
  assert.equal(externalUrl("  https://www.patreon.com/someone \n"), "https://www.patreon.com/someone");
  assert.equal(externalUrl("http://example.org:8080/a?b=c#d"), "http://example.org:8080/a?b=c#d");
  for (const text of [null, 3, "", "example.org", "notes.txt", "www.example.org/x", "javascript:alert(1)",
    "ftp://example.org", "https://", "https://a b.example", "https://example.org/a b",
    "https://user:pw@example.org", "https://user@example.org", "https://example.org\\@evil.example",
    "https://example.org/\u0000", "https://a.example\nhttps://b.example", `https://example.org/${"x".repeat(2048)}`]) {
    assert.equal(externalUrl(text), null, String(text));
  }
});

test("the person picker: no person and the recent ones on top, typing searches everyone", () => {
  const people = [
    { id: 1, name: "Cleo", accounts: [] },
    { id: 2, name: "alice", accounts: [{ handle: "ali.ig" }] },
    { id: 3, name: "Bob", accounts: [] },
    { id: 4, name: "Dana", accounts: [] },
  ];
  const recent = [{ id: 4, name: "Dana" }, { id: 1, name: "Cleo" }];
  const keys = list => list.map(o => o.key);
  assert.deepEqual(keys(personOptions(people, recent, "")),
    ["none", "person:4", "person:1", "person:2", "person:3"]);
  assert.deepEqual(personOptions(people, recent, "").map(o => !!o.recent), [false, true, true, false, false]);
  assert.deepEqual(keys(personOptions(people, recent, "a")), ["person:4", "person:2", "none"]);   // Dana, alice
  assert.deepEqual(keys(personOptions(people, recent, "ali ig")), ["person:2", "none"]);        // by an account
  assert.deepEqual(keys(personOptions(people, recent, "zzz")), ["none"]);
  // The full list not loaded yet: the recent people by their name.
  assert.deepEqual(personOptions(null, recent, "").map(o => o.person?.name ?? null), [null, "Dana", "Cleo"]);
  assert.equal(personOptions(people, [], "")[0], NO_PERSON);

  // Assigning an Unsorted link: no "No person", typed or not.
  const none = { none: false };
  assert.deepEqual(keys(personOptions(people, recent, "", none)), ["person:4", "person:1", "person:2", "person:3"]);
  assert.deepEqual(keys(personOptions(people, recent, "a", none)), ["person:4", "person:2"]);
  assert.deepEqual(personOptions(people, recent, "zzz", none), []);
  assert.deepEqual(personOptions(null, [], "", none), []);
});

test("Copy URLs: one address per line, in the order given", () => {
  assert.equal(urlLines([{ url: "https://a.example" }, { url: "https://b.example/x?y=1" }]),
    "https://a.example\nhttps://b.example/x?y=1");
  assert.equal(urlLines([{ url: "https://a.example" }]), "https://a.example");
  assert.equal(urlLines([]), "");
  assert.equal(urlLines(null), "");
});

test("a drop gives its first link, else its text, if it is an address", () => {
  const drop = data => droppedUrl(type => data[type] ?? "");
  assert.equal(drop({ "text/uri-list": "# a comment\r\nhttps://a.example/x\r\nhttps://b.example" }), "https://a.example/x");
  assert.equal(drop({ "text/plain": " https://c.example " }), "https://c.example");
  assert.equal(drop({ "text/uri-list": "file:///etc/passwd", "text/plain": "https://d.example" }), "https://d.example");
  assert.equal(drop({ "text/plain": "just some words" }), null);
  assert.equal(drop({}), null);
  assert.equal(mayCarryUrl(["text/uri-list"]), true);
  assert.equal(mayCarryUrl(["text/plain", "text/html"]), true);
  assert.equal(mayCarryUrl(["Files"]), false);
  assert.equal(mayCarryUrl([]), false);
});

test("a shared page title: plain text on one line, cut at the cap", () => {
  assert.equal(sharedTitle("  A \n\t page\u0000 title  "), "A page title");
  assert.equal(sharedTitle("<b>bold</b> & co"), "<b>bold</b> & co");
  assert.equal(sharedTitle(null), "");
  assert.equal(sharedTitle(undefined), "");
  assert.equal(sharedTitle("x".repeat(MAX_TITLE + 50)), "x".repeat(MAX_TITLE));
  // An emoji across the cap is dropped whole, not cut in half.
  const cut = sharedTitle(`${"x".repeat(MAX_TITLE - 1)}😀 more`);
  assert.equal(cut, "x".repeat(MAX_TITLE - 1));
});
