// Putting back what a delete moved to the trash, from an Undo: the toasts
// of a delete's Undo (lib/useUndoDelete.js) and of Review's. POST
// /api/trash/restore with post ids puts back each post's most recent
// deletion and answers { ok, posts, files, errors, moved, recreated,
// decision_unknown } (docs/API.md "Trash contents"). No React here, so
// `npm test` checks it.
import { plural, postPath } from "./fmt.js";

export const TRASH_LINK = { to: "/trash", label: "Trash" };

// "@someone's post", or "the post" when the author has no handle.
export function whosePost(p) {
  const h = p?.author?.handle;
  return h ? `@${h}'s post` : "the post";
}

// What a restore says besides the counts: where posts went when their
// folder was gone, and posts whose earlier review decision is not known
// (trashed before decisions were kept with them). As the Trash page says it.
export function restoreNotes(r) {
  const notes = [];
  const folders = list => [...new Set(list)].join(", ");
  if (r?.moved?.length) {
    notes.push(`Their folder was gone: put back in the account's folder now (${folders(r.moved.map(m => m.to))}).`);
  }
  if (r?.recreated?.length) {
    notes.push(`Their folder was gone and was made again (${folders(r.recreated.map(m => m.folder))}).`);
  }
  const unknown = r?.decision_unknown?.length ?? 0;
  if (unknown) {
    notes.push(`Earlier review decision not known for ${plural(unknown, "post")} (trashed by an older FeedVault).`);
  }
  return notes.length ? ` ${notes.join(" ")}` : "";
}

// The files that could not come back, the first one named; "" when none.
export function restoreErrors(r) {
  const errs = r?.errors || [];
  if (!errs.length) return "";
  const first = errs[0];
  return `${plural(errs.length, "file")} could not be restored: ${first.error}${first.path ? ` (${first.path})` : ""}. ${
    errs.length === 1 ? "It stays" : "They stay"} in the trash.`;
}

// The toast after an Undo asked to put back ``posts`` (summaries: id,
// platform, post_id, author), deleted whole (``what`` "post") or one item
// each ("item"): what came back, and Open post when that is one post; what
// could not, and why, with the Trash page. → { text, kind, link }
export function restoredToast(r, posts, what = "post") {
  const back = new Set(r?.posts || []);
  const came = posts.filter(p => back.has(p.id));
  const errors = restoreErrors(r);
  if (!came.length) {
    const subject = posts.length > 1 ? plural(posts.length, "post")
      : what === "item" ? `the item of ${whosePost(posts[0])}` : whosePost(posts[0]);
    const why = r?.error ? `${r.error}.` : errors || `${posts.length > 1 ? "they are" : "it is"} no longer in the trash.`;
    return { text: `Could not restore ${subject}: ${why}`, kind: "err", link: TRASH_LINK };
  }
  const files = plural(r.files ?? 0, "file");
  let text = came.length > 1 ? `Restored ${plural(came.length, "post")} (${files}).`
    : what === "item" ? `Restored the item to ${whosePost(came[0])} (${files}).`
    : `Restored ${whosePost(came[0])} (${files}).`;
  const lost = posts.length - came.length;
  if (lost) text += ` ${plural(lost, "post")} could not come back${errors ? "." : ": no longer in the trash."}`;
  if (errors) text += ` ${errors}`;
  text += restoreNotes(r);
  if (lost || errors) return { text, kind: "err", link: TRASH_LINK };
  return { text, kind: "ok", link: came.length === 1 ? { to: postPath(came[0]), label: "Open post" } : undefined };
}

// Each post's deletions this session, oldest first, as tokens. A restore by
// post id puts back the post's latest deletion: an Undo whose deletion is no
// longer the latest of one of its posts (another item deleted from it since)
// would bring back the wrong thing, so it sends the user to the Trash page.
const deletions = new Map();
let lastToken = 0;

// A delete of ``ids`` just went through: its token, for its Undo.
export function noteDeletion(ids) {
  const token = ++lastToken;
  for (const id of ids) deletions.set(id, [...(deletions.get(id) || []), token]);
  return token;
}

// Whether ``token`` is still the latest deletion of every one of ``ids``.
export function isLatestDeletion(ids, token) {
  return ids.every(id => {
    const tokens = deletions.get(id);
    return !tokens || tokens[tokens.length - 1] === token;
  });
}

// Those of ``ids`` came back: the deletion before is the latest again.
export function forgetDeletion(ids, token) {
  for (const id of ids) {
    const tokens = (deletions.get(id) || []).filter(t => t !== token);
    if (tokens.length) deletions.set(id, tokens); else deletions.delete(id);
  }
}

// Why an Undo did not run: see noteDeletion.
export function laterDeletionText(posts) {
  return posts.length === 1
    ? `Not undone: something else was deleted from ${whosePost(posts[0])} since, and an undo would bring that back instead. Restore this one from the Trash page.`
    : "Not undone: something else was deleted from some of these posts since, and an undo would bring that back instead. Restore them from the Trash page.";
}
