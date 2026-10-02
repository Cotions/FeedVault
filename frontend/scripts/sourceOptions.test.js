// Checks for lib/sourceOptions.js: the form's checks, the options it sends
// and the row's summary. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { formError, formOf, mediaEffect, needsLogin, optionsOf, optionsSummary, today } from "../src/lib/sourceOptions.js";

const IG = { content: ["posts", "reels", "stories", "highlights", "tagged"], content_default: ["posts"],
             login: ["stories", "highlights", "tagged"], media: true, since: true, first_posts: false };
const YT = { content: [], content_default: [], login: [], media: false, since: true, first_posts: true };
const NOW = new Date(2026, 9, 2, 12);

test("defaults from no options", () => {
  assert.deepEqual(formOf(null, IG), { content: ["posts"], media: "all", since: "", first: "new", count: "" });
  assert.deepEqual(formOf({ first_posts: 50, full_history: false }, YT).first, "last");
});

test("the form refuses what the backend would", () => {
  const ok = formOf(null, IG);
  assert.equal(formError(ok, IG, NOW), null);
  assert.match(formError({ ...ok, content: [] }, IG, NOW), /at least one/);
  for (const since of ["2024-1-1", "yesterday", "2024-02-30", "2023-02-29", "1969-12-31", "2026-10-03"]) {
    assert.ok(formError({ ...ok, since }, IG, NOW), since);
  }
  assert.equal(formError({ ...ok, since: "2026-10-02" }, IG, NOW), null);
  for (const count of ["", "0", "10001", "1.5", "-3", "1e3", "5; rm"]) {
    assert.ok(formError({ ...ok, first: "last", count }, YT, NOW), count);
  }
  assert.equal(formError({ ...ok, first: "last", count: "10000" }, YT, NOW), null);
  assert.equal(today(NOW), "2026-10-02");
});

test("options sent", () => {
  const form = { content: ["stories", "posts"], media: "images", since: "2024-01-01", first: "new", count: "" };
  assert.deepEqual(optionsOf(form, IG), { media: "images", since: "2024-01-01", content: ["posts", "stories"],
                                           full_history: false, first_posts: null });
  assert.deepEqual(optionsOf({ ...form, first: "last", count: "20" }, YT),
                   { media: "all", since: "2024-01-01", full_history: false, first_posts: 20 });
  assert.equal("first_posts" in optionsOf(form, YT, false), false);
});

test("login kinds without a session", () => {
  const form = { ...formOf(null, IG), content: ["posts", "stories", "tagged"] };
  assert.deepEqual(needsLogin(form, IG, { mode: "none" }), ["stories", "tagged"]);
  assert.deepEqual(needsLogin(form, IG, { mode: "login", user: "x" }), []);
});

test("summary", () => {
  assert.equal(optionsSummary({ platform: "instagram", options: { content: ["posts", "reels"], media: "all",
                                                                   since: "2024-01-01" } }),
               "posts, reels · since 2024-01-01");
  assert.equal(optionsSummary({ platform: "twitter", options: { content: ["media", "with_replies"], media: "images",
                                                                 first_posts: 30 } }),
               "media, replies · images only · first sync: last 30 posts");
  // gallery-dl's --post-range is per kind.
  assert.equal(optionsSummary({ tool: "gallery-dl", platform: "twitter",
                                options: { content: ["media", "with_replies"], media: "all", first_posts: 30 } }),
               "media, replies · first sync: last 30 posts of each kind");
  assert.equal(optionsSummary({ tool: "gallery-dl", platform: "twitter",
                                options: { content: ["media"], media: "all", first_posts: 30 } }),
               "media · first sync: last 30 posts");
  assert.equal(optionsSummary({ platform: "youtube", options: { media: "all", full_history: false } }), "");
});

test("media effects say what each tool does", () => {
  assert.match(mediaEffect("instaloader", "videos"), /carousels keep their videos/);
  assert.equal(mediaEffect("gallery-dl", "videos"), "images are skipped");
});
