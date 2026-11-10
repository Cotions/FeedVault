// Checks for lib/sourceOptions.js: the form's checks, the options it sends,
// the preset select's values and what a pick saves, and the one-line
// summary. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PRESET_CUSTOM, PRESET_ONLY, customBody, formError, formOf, mediaEffect, metadataEffect, needsLogin, optionsOf, optionsSummary,
  presetBody, presetName, presetValue, resolvedPreset, restoreBody, sameOptions, scheduleShort, scheduleText, today, toggleKind,
} from "../src/lib/sourceOptions.js";

const IG = { content: ["posts", "reels", "stories", "highlights", "tagged"], content_default: ["posts"],
             login: ["stories", "highlights", "tagged"], media: true, since: true, first_posts: false };
const YT = { content: [], content_default: [], login: [], media: false, since: true, first_posts: true };
const NOW = new Date(2026, 9, 2, 12);

test("defaults from no options", () => {
  assert.deepEqual(formOf(null, IG), { content: ["posts"], media: "all", since: "", first: "new", count: "",
                                    schedule: "off", script: "", session: null, metadata: "" });
  assert.equal(formOf({ script: "my-insta" }, IG).script, "my-insta");
  assert.deepEqual(formOf({ first_posts: 50, full_history: false }, YT).first, "last");
  assert.deepEqual(formOf({ full_history: true }, YT).first, "new");
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
  // Never full history: a one-off, asked from the source's ⋯ menu.
  assert.deepEqual(optionsOf(form, IG), { media: "images", since: "2024-01-01", content: ["posts", "stories"],
                                           first_posts: null, schedule: "daily",
                                           script: null, session: null, metadata: null });
  assert.deepEqual(optionsOf({ ...form, first: "last", count: "20" }, YT),
                   { media: "all", since: "2024-01-01", first_posts: 20, schedule: "daily",
                     script: null, session: null, metadata: null });
  assert.equal("first_posts" in optionsOf(form, YT, false), false);
  assert.equal(optionsOf({ ...form, script: "builtin:yt-dlp-channel" }, YT).script, "builtin:yt-dlp-channel");
});

test("login kinds without a session", () => {
  const form = { ...formOf(null, IG), content: ["posts", "stories", "tagged"] };
  assert.deepEqual(needsLogin(form, IG, { mode: "none" }), ["stories", "tagged"]);
  assert.deepEqual(needsLogin(form, IG, { mode: "login", user: "x" }), []);
  // The source's own session, once picked, is the one that counts.
  const own = { browser: "firefox", mode: "cookies", profile: "test" };
  assert.deepEqual(needsLogin({ ...form, session: own }, IG, { mode: "none" }), []);
  assert.deepEqual(needsLogin({ ...form, session: { mode: "none" } }, IG, { mode: "login", user: "x" }), ["stories", "tagged"]);
});

test("a source's own session is kept and sent", () => {
  const own = { browser: "firefox", mode: "cookies", profile: "test" };
  assert.deepEqual(formOf({ session: own }, IG).session, own);
  assert.deepEqual(optionsOf(formOf({ session: own }, IG), IG).session, own);
  assert.equal(optionsOf({ ...formOf({ session: own }, IG), session: null }, IG).session, null);
});

test("summary", () => {
  // A script runs instead of the tool's command: the other options are not its.
  assert.equal(optionsSummary({ platform: "instagram", options: { content: ["posts"], script: "my-insta" } }),
               "script my-insta");
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
  assert.equal(optionsSummary({ platform: "youtube", options: { media: "all", full_history: false, schedule: "off",
                                                                 session: null, metadata: null } }), "");
  // The kinds it does not set are the platform's own.
  assert.equal(optionsSummary({ platform: "instagram", choices: IG, options: { content: null, media: "images" } }),
               "posts · images only");
  assert.equal(optionsSummary({ platform: "youtube", choices: YT, options: { content: null } }), "");
  // A preset's line: its schedule, login and metadata files too; a script keeps its schedule.
  assert.equal(optionsSummary({ platform: "instagram", options: { content: ["posts"], schedule: "daily", metadata: "slim",
                                                                   session: { mode: "none" } } }),
               "posts · no login · metadata slim · daily");
  assert.equal(optionsSummary({ platform: "twitter", options: { session: { mode: "cookies", browser: "firefox" } } }),
               "firefox login");
  assert.equal(optionsSummary({ platform: "instagram", options: { script: "my-insta", schedule: "weekly" } }),
               "script my-insta · weekly");
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

test("what a source follows, and what a pick saves", () => {
  const src = { id: 1, tool: "instaloader", platform: "instagram", choices: IG, synced: true, preset: null, custom: false,
                profile_only: false, platform_profile_only: false,
                options: { content: ["posts"], media: "all", since: null, first_posts: null, schedule: "off", script: null,
                           session: null, metadata: null, download: "platform", full_history: false } };
  const named = { ...src, preset: { id: "ab12cd", name: "Images" } };
  const own = { ...src, custom: true, options: { ...src.options, media: "images" } };
  const only = { ...named, profile_only: true, options: { ...src.options, download: "off" } };
  assert.deepEqual([src, named, own, only].map(presetValue), ["", "ab12cd", PRESET_CUSTOM, PRESET_ONLY]);
  assert.deepEqual([src, named, own, only].map(presetName), ["Default", "Images", "Custom", "Profile only"]);
  // A preset: its id, or null for Default; nothing else is sent, so its Download stays.
  assert.deepEqual(presetBody(src, "ab12cd"), { preset: "ab12cd" });
  assert.deepEqual(presetBody(named, ""), { preset: null });
  // Custom… saves nothing by itself: its dialog does.
  assert.equal(presetBody(src, PRESET_CUSTOM), null);
  assert.deepEqual(presetBody(named, PRESET_ONLY), { options: { download: "off" } });
  // From profile only, a preset downloads again: "on" where its platform is listed as profile only.
  assert.deepEqual(presetBody(only, "ab12cd"), { preset: "ab12cd", options: { download: "platform" } });
  assert.deepEqual(presetBody({ ...only, platform_profile_only: true }, ""), { preset: null, options: { download: "on" } });
  // Custom: every option the form has, never full history; last N only before a first sync.
  const form = { ...formOf(src.options, IG), media: "videos" };
  assert.deepEqual(customBody(src, form, false), { custom: true, options: {
    content: ["posts"], media: "videos", since: null, schedule: "off", script: null, session: null, metadata: null } });
  assert.equal(customBody(src, form).options.first_posts, null);
  assert.equal(customBody(only, form, false).options.download, "platform");
  assert.equal("full_history" in customBody(src, form).options, false);
  // Undo: the preset it followed, or its own options, and its Download.
  assert.deepEqual(restoreBody(named), { preset: "ab12cd", options: { download: "platform" } });
  assert.deepEqual(restoreBody(only), { preset: "ab12cd", options: { download: "off" } });
  assert.deepEqual(restoreBody(own), { custom: true, options: { ...customBody(own, formOf(own.options, IG), false).options,
                                                                download: "platform" } });
});

test("a form compared as it would be saved", () => {
  const a = formOf({ content: ["posts", "reels"], media: "videos" }, IG);
  // The order of the kinds is not a change, nor a count typed under "new posts".
  assert.equal(sameOptions(a, { ...a, content: ["reels", "posts"] }, IG), true);
  assert.equal(sameOptions(a, { ...a, count: "20" }, YT), true);
  assert.equal(sameOptions(a, { ...a, first: "last", count: "20" }, YT), false);
  assert.equal(sameOptions(a, { ...a, first: "last", count: "20" }, YT, false), true);
  assert.equal(sameOptions(a, { ...a, schedule: "daily" }, IG), false);
});

test("a new source's preset: its options and the session they would use", () => {
  const resolved = { defaults: { content: ["posts"] }, session: { mode: "none" },
                     presets: [{ id: "ab12cd", name: "Stories", options: { content: ["posts", "stories"] },
                                 session: { mode: "none" } }] };
  assert.equal(resolvedPreset(resolved, "").options, resolved.defaults);
  assert.equal(resolvedPreset(resolved, PRESET_CUSTOM).options, resolved.defaults);
  const picked = resolvedPreset(resolved, "ab12cd");
  assert.deepEqual(needsLogin(formOf(picked.options, IG), IG, picked.session), ["stories"]);
  // The userscript's answer has no presets.
  assert.equal(resolvedPreset({ defaults: {} }, "ab12cd").session, undefined);
});

test("metadata modes say what none loses", () => {
  assert.match(metadataEffect("yt-dlp", "none"), /^no files beside the media.*ffprobe/);
  assert.match(metadataEffect("instaloader", "slim"), /slimmed/);
  assert.equal(formOf({ metadata: "none" }, IG).metadata, "none");
  assert.equal(optionsOf({ ...formOf(null, IG), metadata: "" }, IG).metadata, null);
});
