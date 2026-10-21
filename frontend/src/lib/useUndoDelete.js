import { useCallback } from "react";
import { restorePosts } from "./api";
import { useToast } from "./toast";
import { useJobs } from "./jobs";
import { useScan } from "./scan";
import { TRASH_LINK, forgetDeletion, isLatestDeletion, laterDeletionText, noteDeletion, restoredToast } from "./restore.js";

/* The Undo of a delete's toast (Feed, a post's page, Storage): puts back
   what went, each post's latest deletion, and says what came back
   (lib/restore.js). Built only from what App provides (its toast, jobs poll
   and refresh), never from the page's state: the toast outlives the page
   (a post's page goes back to the Feed right after its delete), and the
   page showing when Undo is pressed reads the index again.

   undoOf(posts, what) → the toast's { label, onClick }. Call it as the
   delete succeeds: ``posts`` are the summaries of what went (id, platform,
   post_id, author), ``what`` "post" (deleted whole) or "item" (an item of
   each, the post stays). */
export function useUndoDelete() {
  const toast = useToast();
  const { started } = useJobs();
  const { refresh } = useScan();
  return useCallback((posts, what = "post") => {
    const ids = posts.map(p => p.id);
    const token = noteDeletion(ids);
    async function undo() {
      if (!isLatestDeletion(ids, token)) { toast(laterDeletionText(posts), "err", TRASH_LINK); return; }
      try {
        const r = await restorePosts(ids, { from: "undo" });
        if (r?.posts?.length) {
          forgetDeletion(r.posts, token);
          started();   // the new counts (jobs poll) follow now
          refresh();
        }
        const t = restoredToast(r, posts, what);
        // No "Open post" on that post's own page (an item put back).
        toast(t.text, t.kind, t.link?.to === window.location.pathname ? undefined : t.link);
      } catch (e) {
        toast(e.message, "err");
      }
    }
    return { label: "Undo", onClick: undo };
  }, [toast, started, refresh]);
}
