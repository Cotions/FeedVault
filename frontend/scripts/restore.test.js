// Checks for lib/restore.js: what an Undo of a delete says, and which
// deletion it may put back. Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TRASH_LINK, forgetDeletion, isLatestDeletion, laterDeletionText, noteDeletion, restoreErrors,
         restoreNotes, restoredToast, whosePost } from "../src/lib/restore.js";

const post = (id, handle = "someone") => ({ id, platform: "instagram", post_id: id.split(":")[1], author: handle ? { id: "1", handle } : null });
const ok = (posts, files, more = {}) => ({ ok: true, posts, files, errors: [], moved: [], recreated: [], decision_unknown: [], ...more });

test("whosePost: the author's handle, or the post", () => {
  assert.equal(whosePost(post("instagram:A1")), "@someone's post");
  assert.equal(whosePost(post("instagram:A1", null)), "the post");
});

test("one post back: what came back, and Open post on it", () => {
  const p = post("instagram:A1");
  assert.deepEqual(restoredToast(ok([p.id], 3), [p]), {
    kind: "ok", text: "Restored @someone's post (3 files).", link: { to: "/p/instagram/A1", label: "Open post" },
  });
  assert.equal(restoredToast(ok([p.id], 1), [p], "item").text, "Restored the item to @someone's post (1 file).");
});

test("several posts back: counted, no link", () => {
  const ps = ["instagram:A1", "instagram:A2", "instagram:A3", "instagram:A4"].map(id => post(id));
  assert.deepEqual(restoredToast(ok(ps.map(p => p.id), 12), ps), { kind: "ok", text: "Restored 4 posts (12 files).", link: undefined });
});

test("some posts could not come back: how many, why, and the Trash page", () => {
  const ps = [post("instagram:A1"), post("instagram:A2"), post("instagram:A3")];
  const errors = [{ path: "/m/a/A2.jpg", error: "a file is in the way" }, { path: "/m/a/A3.jpg", error: "a file is in the way" }];
  const t = restoredToast(ok([ps[0].id], 2, { errors }), ps);
  assert.equal(t.kind, "err");
  assert.deepEqual(t.link, TRASH_LINK);
  assert.equal(t.text, "Restored @someone's post (2 files). 2 posts could not come back. "
    + "2 files could not be restored: a file is in the way (/m/a/A2.jpg). They stay in the trash.");
  // Gone from the trash (emptied since): no error lines, nothing came back for them.
  assert.equal(restoredToast(ok([ps[0].id], 2), ps.slice(0, 2)).text,
    "Restored @someone's post (2 files). 1 post could not come back: no longer in the trash.");
});

test("the post back but a file of it not: an error toast, never a plain success", () => {
  const p = post("instagram:A1");
  const t = restoredToast(ok([p.id], 2, { errors: [{ path: "/m/a/A1_2.jpg", error: "no longer in the trash" }] }), [p]);
  assert.equal(t.kind, "err");
  assert.equal(t.text, "Restored @someone's post (2 files). 1 file could not be restored: no longer in the trash (/m/a/A1_2.jpg). It stays in the trash.");
  assert.deepEqual(t.link, TRASH_LINK);
});

test("nothing came back: says so, and why", () => {
  const p = post("instagram:A1");
  assert.deepEqual(restoredToast(ok([], 0), [p]), {
    kind: "err", text: "Could not restore @someone's post: it is no longer in the trash.", link: TRASH_LINK,
  });
  assert.equal(restoredToast(ok([], 0), [p], "item").text, "Could not restore the item of @someone's post: it is no longer in the trash.");
  assert.equal(restoredToast({ ok: false, error: "the trash is busy" }, [p]).text, "Could not restore @someone's post: the trash is busy.");
  assert.equal(restoredToast(ok([], 0, { errors: [{ path: "/m/x.jpg", error: "a file is in the way" }] }), [p, post("instagram:A2")]).text,
    "Could not restore 2 posts: 1 file could not be restored: a file is in the way (/m/x.jpg). It stays in the trash.");
  assert.equal(restoredToast(null, [p]).kind, "err");
});

test("restoreNotes: folders moved or made again, and unknown decisions, as the Trash page says them", () => {
  assert.equal(restoreNotes(ok(["instagram:A1"], 1)), "");
  assert.equal(restoreNotes(ok([], 0, {
    moved: [{ post: "a", from: "old", to: "alice" }, { post: "b", from: "old", to: "alice" }],
    recreated: [{ post: "c", folder: "bob" }],
    decision_unknown: ["d", "e"],
  })), " Their folder was gone: put back in the account's folder now (alice). Their folder was gone and was made again (bob)."
    + " Earlier review decision not known for 2 posts (trashed by an older FeedVault).");
  // The notes follow the counts in the toast.
  const p = post("instagram:A1");
  assert.equal(restoredToast(ok([p.id], 1, { recreated: [{ post: p.id, folder: "someone" }] }), [p]).text,
    "Restored @someone's post (1 file). Their folder was gone and was made again (someone).");
});

test("restoreErrors: empty without errors", () => {
  assert.equal(restoreErrors(ok([], 0)), "");
  assert.equal(restoreErrors({ errors: [{ path: null, error: "not a file in this root's trash" }] }),
    "1 file could not be restored: not a file in this root's trash. It stays in the trash.");
});

test("an Undo puts back only the latest deletion of its posts", () => {
  // Two items deleted from one post: the first one's Undo would bring back the second.
  const first = noteDeletion(["instagram:Z1"]);
  const second = noteDeletion(["instagram:Z1"]);
  assert.equal(isLatestDeletion(["instagram:Z1"], first), false);
  assert.equal(isLatestDeletion(["instagram:Z1"], second), true);
  // Once the second is back, the first is the latest again.
  forgetDeletion(["instagram:Z1"], second);
  assert.equal(isLatestDeletion(["instagram:Z1"], first), true);
  forgetDeletion(["instagram:Z1"], first);
  // A bulk delete: stale as soon as any of its posts had a later one.
  const bulk = noteDeletion(["instagram:Z2", "instagram:Z3"]);
  assert.equal(isLatestDeletion(["instagram:Z2", "instagram:Z3"], bulk), true);
  const later = noteDeletion(["instagram:Z3"]);
  assert.equal(isLatestDeletion(["instagram:Z2", "instagram:Z3"], bulk), false);
  forgetDeletion(["instagram:Z3"], later);
  assert.equal(isLatestDeletion(["instagram:Z2", "instagram:Z3"], bulk), true);
  // A post never noted (deleted before this page was loaded) does not block it.
  assert.equal(isLatestDeletion(["instagram:Z9"], bulk), true);
});

test("laterDeletionText: one post by its author, several by count", () => {
  assert.match(laterDeletionText([post("instagram:A1")]), /^Not undone: something else was deleted from @someone's post since/);
  assert.match(laterDeletionText([post("instagram:A1"), post("instagram:A2")]), /Restore them from the Trash page\.$/);
});
