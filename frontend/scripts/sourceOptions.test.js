// Checks for lib/sourceOptions.js: the form's checks, the options it sends
// and the row's summary. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  formError, formOf, mediaEffect, needsLogin, optionsOf, optionsSummary, scheduleShort, scheduleText, today, toggleKind,
} from "../src/lib/sourceOptions.js";

const IG = { content: ["posts", "reels", "stories", "highlights", "tagged"], content_default: ["posts"],
             login: ["stories", "highlights", "tagged"], media: true, since: true, first_posts: false };
const YT = { content: [], content_default: [], login: [], media: false, since: true, first_posts: true };
const NOW = new Date(2026, 9, 2, 12);

test("defaults from no options", () => {
  assert.deepEqual(formOf(null, IG), { content: ["posts"], media: "all", since: "", first: "new", count: "",
                                    schedule: "off" });
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
  const form = { content: ["stories", "posts"], media: "images", since: "2024-01-01", first: "new", count: "",
                 schedule: "daily" };
  assert.deepEqual(optionsOf(form, IG), { media: "images", since: "2024-01-01", content: ["posts", "stories"],
                                           full_history: false, first_posts: null, schedule: "daily" });
  assert.deepEqual(optionsOf({ ...form, first: "last", count: "20" }, YT),
                   { media: "all", since: "2024-01-01", full_history: false, first_posts: 20, schedule: "daily" });
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
  // yt-dlp's --playlist-items is per tab on a channel's own page.
  for (const [target, each] of [["https://youtube.com/@somechannel", " of each tab"],
                                ["https://youtube.com/@somechannel/shorts", ""],
                                ["https://youtube.com/playlist?list=PL1", ""]]) {
    assert.equal(optionsSummary({ tool: "yt-dlp", platform: "youtube", target, options: { media: "all", first_posts: 5 } }),
                 `first sync: last 5 posts${each}`, target);
  }
});

test("media effects say what each tool does", () => {
  assert.match(mediaEffect("instaloader", "videos"), /carousels keep their videos/);
  assert.equal(mediaEffect("gallery-dl", "videos"), "images are skipped");
});

test("stories turned on make an off schedule daily", () => {
  const form = formOf(null, IG);
  assert.equal(toggleKind(form, "stories").schedule, "daily");
  assert.equal(toggleKind(form, "reels").schedule, "off");
  assert.equal(toggleKind({ ...form, schedule: "weekly" }, "stories").schedule, "weekly");
  const on = { ...toggleKind(form, "stories"), schedule: "off" };     // the user turned it off
  assert.equal(toggleKind(toggleKind(on, "reels"), "reels").schedule, "off");
  assert.deepEqual(toggleKind(on, "stories").content, ["posts"]);
});

test("schedule line", () => {
  const now = 1790000000 * 1000;
  const sch = { every: "daily", next_at: 1790000000 + 3 * 3600, paused: false, skipped: null, failures: 0 };
  const s = { schedule: sch, last_result: { state: "done" } };
  assert.equal(scheduleText({ schedule: { ...sch, every: "off", next_at: null } }, {}, now), "");
  assert.equal(scheduleText(s, {}, now), "daily · next sync in 3 h");
  assert.equal(scheduleText({ ...s, schedule: { ...sch, next_at: 0 } }, {}, now), "daily · due, starts soon");
  assert.equal(scheduleText({ schedule: { ...sch, failures: 2, next_at: 1790000000 + 600 },
                              last_result: { state: "failed", error: "rate_limited" } },
                            { rate_limited: "rate limited" }, now),
               "daily, last failed: rate limited · next try in 10 min");
  assert.equal(scheduleText({ ...s, schedule: { ...sch, paused: true } }, {}, now), "daily · all schedules paused");
  assert.equal(scheduleText({ ...s, schedule: { ...sch, skipped: "skipped: gallery-dl was not found" } }, {}, now),
               "daily · skipped: gallery-dl was not found");
  assert.equal(scheduleShort(s, now), "in 3 h");
  assert.equal(scheduleShort({ schedule: { ...sch, next_at: 0 } }, now), "due");
});
