// Checks for lib/search.js: which pages the header's search box searches,
// and what the Creators and person pages' searches find. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FEED_SCOPE, accountMatches, linkMatches, personMatches, scopeText, searchCreators, searchScope, sourceMatches,
} from "../src/lib/search.js";

test("the Feed, Creators, Links and a person's page search themselves; any other page sends it to the Feed", () => {
  assert.equal(searchScope("/"), FEED_SCOPE);
  assert.equal(searchScope("/creators").placeholder, "Search creators, accounts, links…");
  assert.equal(searchScope("/links").label, "Search links");
  assert.equal(searchScope("/people/12").person, true);
  for (const path of ["/tags", "/review", "/settings", "/people/12/more", "/creators/x", "/p/instagram/abc"]) {
    assert.equal(searchScope(path), null, path);
  }
});

test("the box's words: the Feed's for a page that sends it there, a person's name once known", () => {
  assert.equal(scopeText(null).label, "Search posts");
  const person = searchScope("/people/3");
  assert.equal(scopeText(person, null).placeholder, "Search their accounts, links…");
  assert.equal(scopeText(person, "Ada").placeholder, "Search Ada's accounts, links…");
  assert.equal(scopeText(person, "Ada").label, "Search Ada's accounts, sources and links");
  assert.equal(scopeText(searchScope("/creators"), "Ada"), searchScope("/creators"));
});

test("a person matches by name or label, every word somewhere", () => {
  const p = { name: "Ada Lovelace", labels: ["maths", "Engines"], accounts: [{ handle: "ada_l" }] };
  assert.ok(personMatches(p, "ada"));
  assert.ok(personMatches(p, "lovelace engines"));
  assert.ok(!personMatches(p, "ada poetry"));
  assert.ok(!personMatches(p, "ada_l"), "a handle is the Accounts section's");
});

test("an account matches by any handle, name or id; a source by target or address; a link by address, title or notes", () => {
  assert.ok(accountMatches({ handle: "new.name", handles: [{ handle: "old.name" }], id: "123" }, "old"));
  assert.ok(accountMatches({ handle: "x", name: "Display Name", id: "123" }, "display"));
  assert.ok(sourceMatches({ target: "somebody", tool: "instaloader" }, "some"));
  assert.ok(sourceMatches({ target: "https://x.com/somebody", url: "https://x.com/somebody", tool: "gallery-dl" }, "x.com"));
  const l = { url: "https://www.patreon.com/ada", title: "Patreon", notes: "Monthly sketches" };
  assert.ok(linkMatches(l, "patreon"));
  assert.ok(linkMatches(l, "monthly ada"));
  assert.ok(!linkMatches(l, "discord"));
  // No title: the label it shows (lib/links.js linkLabel); a title hides it.
  assert.ok(linkMatches({ url: "https://ko-fi.com/ada", title: "", label: "Ko-fi tips", notes: "" }, "tips"));
  assert.ok(!linkMatches({ url: "https://ko-fi.com/ada", title: "Support", label: "Ko-fi tips", notes: "" }, "tips"));
});

test("Creators: people and accounts, linked or not, in the order given; nothing for an empty search", () => {
  const people = [{ id: 1, name: "Moss Garden", labels: [] }, { id: 2, name: "Fern", labels: ["moss"] }, { id: 3, name: "Rock", labels: [] }];
  const accounts = [
    { platform: "instagram", id: "1", handle: "mossy", person: { id: 3, name: "Rock" } },
    { platform: "twitter", id: "2", handle: "lichen" },
    { platform: "tiktok", id: "3", handle: "moss.tok" },
  ];
  const found = searchCreators(people, accounts, " Moss ");
  assert.deepEqual(found.people.map(p => p.id), [1, 2]);
  assert.deepEqual(found.accounts.map(a => a.id), ["1", "3"]);
  assert.deepEqual(searchCreators(people, accounts, "  "), { people: [], accounts: [] });
  assert.deepEqual(searchCreators(null, null, "moss"), { people: [], accounts: [] });
});
