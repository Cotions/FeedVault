// Checks for lib/people.js: a person's gender, country and labels, as the
// person page edits them and Creators filters by them. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  accountChips, ACCOUNTS_SHOWN, attributeChoices, attributeFiltersOn, attributesMatch, cleanGender, countryFlag, countryName, countryOptions, filtersOn,
  mergeSet, mergeStopping, NO_FILTERS, notSynced, personText, sameLabels, syncFilter, syncMatch, withLabel,
} from "../src/lib/people.js";

const person = (over = {}) => ({ name: "Someone", gender: "", country: "", labels: [], accounts: [], ...over });

test("a country's flag is its two regional indicator letters", () => {
  assert.equal(countryFlag("FR"), "\u{1F1EB}\u{1F1F7}");
  assert.equal(countryFlag("JP"), "\u{1F1EF}\u{1F1F5}");
  for (const bad of ["", null, undefined, "fr", "FRA", "F1"]) assert.equal(countryFlag(bad), "");
});

test("a country's name comes from the browser, its code when it has none", () => {
  assert.equal(countryName(""), "");
  assert.notEqual(countryName("FR"), "");
  assert.equal(typeof countryName("ZZ"), "string");
});

test("a gender is stored with its spaces collapsed, a preset in its own case", () => {
  assert.equal(cleanGender("  transGENDER "), "Transgender");
  assert.equal(cleanGender("female"), "Female");
  assert.equal(cleanGender("Two   spirit"), "Two spirit");
  assert.equal(cleanGender(""), "");
});

test("a label is added once, whatever its case; retyped in another case is a change", () => {
  assert.deepEqual(withLabel(["Travel"], "travel"), ["Travel"]);
  assert.deepEqual(withLabel(["Travel"], "food"), ["Travel", "food"]);
  assert.equal(sameLabels(["a", "b"], ["b", "a"]), true);
  assert.equal(sameLabels(["a"], ["A"]), false);
  assert.equal(sameLabels([], []), true);
});

test("the filters: any, not set, or a value (labels and genders without regard to case)", () => {
  const p = person({ gender: "Female", country: "FR", labels: ["Travel", "food"] });
  const none = person();
  assert.equal(filtersOn(NO_FILTERS), false);
  assert.equal(attributesMatch(p, NO_FILTERS), true);
  assert.equal(attributesMatch(p, { ...NO_FILTERS, gender: "female" }), true);
  assert.equal(attributesMatch(p, { ...NO_FILTERS, gender: "Male" }), false);
  assert.equal(attributesMatch(none, { ...NO_FILTERS, gender: "" }), true);
  assert.equal(attributesMatch(p, { ...NO_FILTERS, gender: "" }), false);
  assert.equal(attributesMatch(p, { ...NO_FILTERS, country: "FR", label: "travel" }), true);
  assert.equal(attributesMatch(p, { ...NO_FILTERS, country: "JP" }), false);
  assert.equal(attributesMatch(none, { ...NO_FILTERS, label: "" }), true);
  assert.equal(attributesMatch(p, { ...NO_FILTERS, label: "" }), false);
  assert.equal(attributesMatch(p, { ...NO_FILTERS, label: "cosplay" }), false);
  assert.equal(filtersOn({ ...NO_FILTERS, label: "" }), true);
});

test("the sync filter: set to Don't sync, the person, the account, or each account of a person", () => {
  const off = person({ no_sync: true });
  const eachOff = person({ accounts: [{ no_sync: true }, { no_sync: true }] });
  const someOff = person({ accounts: [{ no_sync: true }, { no_sync: false }] });
  const synced = person({ no_sync: false });
  const account = { platform: "instagram", id: "1", no_sync: true };
  assert.equal(filtersOn({ ...NO_FILTERS, sync: "off" }), true);
  assert.equal(attributeFiltersOn({ ...NO_FILTERS, sync: "off" }), false);
  for (const p of [off, eachOff]) {
    assert.equal(notSynced(p), true);
    assert.equal(attributesMatch(p, { ...NO_FILTERS, sync: "off" }), true);
    assert.equal(attributesMatch(p, { ...NO_FILTERS, sync: "on" }), false);
  }
  for (const p of [someOff, synced, person()]) {
    assert.equal(notSynced(p), false);
    assert.equal(attributesMatch(p, { ...NO_FILTERS, sync: "on" }), true);
    assert.equal(attributesMatch(p, { ...NO_FILTERS, sync: "off" }), false);
  }
  // An account linked to nobody: its own switch.
  assert.equal(syncMatch(account, "off"), true);
  assert.equal(syncMatch({ ...account, no_sync: false }, "off"), false);
  assert.equal(syncMatch(account, null), true);
  // From the address: only on or off.
  assert.equal(syncFilter("off"), "off");
  assert.equal(syncFilter("on"), "on");
  assert.equal(syncFilter("<script>"), null);
  assert.equal(syncFilter(null), null);
});

test("what the filters offer: counted, most used first, and how many have none", () => {
  const c = attributeChoices([
    person({ gender: "Female", country: "FR", labels: ["Travel"] }),
    person({ gender: "female", labels: ["travel", "food"] }),
    person({ gender: "Agender", country: "FR" }),
    person(),
  ]);
  assert.deepEqual(c.genders, [{ value: "Female", count: 2 }, { value: "Agender", count: 1 }]);
  assert.deepEqual(c.countries, [{ value: "FR", count: 2 }]);
  assert.deepEqual(c.labels, [{ value: "Travel", count: 2 }, { value: "food", count: 1 }]);
  assert.deepEqual(c.none, { gender: 1, country: 2, label: 2 });
});

test("the text filter sees the gender, the country's name and the labels", () => {
  const text = personText(person({ gender: "Genderfluid", country: "FR", labels: ["Cosplay"] }));
  assert.ok(text.includes("genderfluid") && text.includes("cosplay"));
  assert.ok(text.includes(countryName("FR").toLowerCase()));
});

test("the country picker: an exact code first, then names that start with the text", () => {
  const codes = ["FR", "GF", "PF", "TF", "JP"];
  assert.deepEqual(countryOptions(codes, "jp").map(c => c.code), ["JP"]);
  const all = countryOptions(codes);
  assert.equal(all.length, 5);
  assert.deepEqual(all.map(c => c.name), [...all.map(c => c.name)].sort((a, b) => a.localeCompare(b)));
  assert.equal(countryOptions(codes, "zzzz").length, 0);
});

test("a merge's Don't sync warning holds for the set it was given for only", () => {
  const a = { platform: "instagram", id: "1" }, b = { platform: "x", id: "2" };
  const set = mergeSet([{ id: 3 }, { id: 7 }], [a]);
  assert.equal(set, mergeSet([{ id: 7 }, { id: 3 }], [a]));
  const merge = { name: "N", stopping: 2, stoppingFor: set };
  assert.equal(mergeStopping(merge, set), 2);
  // A person or an account added or dropped since: asked again.
  assert.equal(mergeStopping(merge, mergeSet([{ id: 3 }], [a])), 0);
  assert.equal(mergeStopping(merge, mergeSet([{ id: 3 }, { id: 7 }], [a, b])), 0);
  assert.equal(mergeStopping(merge, mergeSet([{ id: 3 }, { id: 9 }], [a])), 0);
  assert.equal(mergeStopping({ name: "N" }, set), 0);
  assert.equal(mergeStopping(null, set), 0);
});

test("a card's accounts: each its own chip while they fit, else one per platform and +n", () => {
  const acc = (platform, handle) => ({ platform, id: handle, handle });
  const handles = ({ chips, rest }) => [chips.map(c => c.map(a => a.handle)), rest.map(a => a.handle)];
  assert.equal(ACCOUNTS_SHOWN, 3);
  assert.deepEqual(handles(accountChips([])), [[], []]);
  // Three or fewer: one chip each, two on a platform too.
  const three = [acc("instagram", "a"), acc("instagram", "b"), acc("tiktok", "c")];
  assert.deepEqual(handles(accountChips(three)), [[["a"], ["b"], ["c"]], []]);
  // More: grouped by platform, in the order they first come.
  const six = [acc("instagram", "a"), acc("twitter", "t"), acc("instagram", "b"), acc("tiktok", "k"),
               acc("instagram", "c"), acc("instagram", "d")];
  assert.deepEqual(handles(accountChips(six)), [[["a", "b", "c", "d"], ["t"], ["k"]], []]);
  assert.deepEqual(handles(accountChips(six.filter(a => a.platform === "instagram"))), [[["a", "b", "c", "d"]], []]);
  // More platforms than chips: the first few, the accounts of the rest counted.
  const many = [...six, acc("youtube", "y"), acc("bluesky", "s1"), acc("bluesky", "s2")];
  assert.deepEqual(handles(accountChips(many)), [[["a", "b", "c", "d"], ["t"], ["k"]], ["y", "s1", "s2"]]);
  // Every account is in a chip or in the rest, once.
  const { chips, rest } = accountChips(many);
  assert.equal(chips.flat().length + rest.length, many.length);
  assert.deepEqual(handles(accountChips(many, 5)), [[["a", "b", "c", "d"], ["t"], ["k"], ["y"], ["s1", "s2"]], []]);
});
