// Review's queue: every post fetched in the session, in server order, with
// a local status (keep/trash) and the current position. The model is
// described in pages/Review.jsx; no React here, so `npm test` checks it.

export const PAGE = 50;

export function nextUndecided(queue, status, from) {
  for (let i = from + 1; i < queue.length; i++) if (!status[queue[i].id]) return i;
  return -1;
}
export function prevUndecided(queue, status, from) {
  for (let i = Math.min(from, queue.length) - 1; i >= 0; i--) if (!status[queue[i].id]) return i;
  return -1;
}

// Where the next page starts in the server's list, and how many of the posts
// held on it this session has decided already. A normal session reads the
// `review=unreviewed` list, which a decision leaves: the posts held on it are
// the undecided ones, none decided. A re-review (`again`) reads every post in
// the scope, which a keep stays on and only a trash leaves: the posts kept
// this session are held on it, decided, not left in this pass. In both, a
// post tagged out of a tag scope (or the untagged one) has left it too:
// `outOfScope`, a test on a held post.
export function listPlace(queue, status, { again = false, outOfScope = () => false } = {}) {
  let offset = 0, decided = 0;
  for (const p of queue) {
    const st = status[p.id];
    if (st === "trash" || (st && !again) || outOfScope(p)) continue;
    offset++;
    if (st) decided++;
  }
  return { offset, decided };
}

export const initial = { queue: [], status: {}, pos: 0, left: null, exhausted: false, error: null };

export function reducer(s, a) {
  switch (a.type) {
    case "loaded": {
      const have = new Set(s.queue.map(p => p.id));
      const fresh = a.posts.filter(p => !have.has(p.id));
      return {
        ...s,
        queue: fresh.length ? [...s.queue, ...fresh] : s.queue,
        left: a.total,
        // No new post in a page means we have everything (or the list shifted
        // under us; stopping is safer than refetching the same page forever).
        exhausted: fresh.length === 0 || a.posts.length < PAGE,
        error: null,
      };
    }
    case "error":
      return { ...s, error: a.error, exhausted: true };
    case "decide": {
      const status = { ...s.status, [a.id]: a.decision };
      let pos = s.pos;
      if (s.queue[pos]?.id === a.id) {
        const n = nextUndecided(s.queue, status, pos);
        pos = n === -1 ? s.queue.length : n;
      }
      return { ...s, status, pos, left: s.left == null ? null : Math.max(0, s.left - 1) };
    }
    case "undecide": {
      const status = { ...s.status };
      delete status[a.id];
      return { ...s, status, pos: a.index, left: s.left == null ? null : s.left + 1 };
    }
    case "restored": {
      // A restore re-indexes the post and gives its media new ids: the cover
      // fetched with the queue points at an id that is gone (#90). Without
      // it the stage says "Loading…" until the post's full data is back.
      const i = s.queue.findIndex(p => p.id === a.id);
      if (i === -1 || !s.queue[i].cover) return s;
      const queue = [...s.queue];
      queue[i] = { ...queue[i], cover: null };
      return { ...s, queue };
    }
    case "goto":
      return { ...s, pos: a.index };
    case "next": {
      const n = nextUndecided(s.queue, s.status, s.pos);
      return { ...s, pos: n === -1 ? s.queue.length : n };
    }
    case "prev": {
      const p = prevUndecided(s.queue, s.status, s.pos);
      return p === -1 ? s : { ...s, pos: p };
    }
    default:
      return s;
  }
}
