import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getPosts, getPost, getAuthors, getTags, applyTags, deleteItems, setDecision, restorePosts } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { KINDS, excerpt, fmtBytes, fmtFullDate, fmtIso, platformLabel, authorFeedPath } from "../lib/fmt";
import { sameTag, tagsMatch, withTags } from "../lib/tags";
import RichText from "../components/RichText";
import TagChips from "../components/TagChips";
import TagInput from "../components/TagInput";
import CollectionDialog from "../components/CollectionDialog";
import Icon from "../components/Icon";

/* Review: one unreviewed post at a time, decided from the keyboard.

   Queue model. The server's `review=unreviewed` list shrinks as posts are
   decided, so plain offset paging would skip posts. The queue keeps every post
   fetched in this session, in server order, with a local status (keep/trash).
   The server's unreviewed list is then exactly "our undecided posts, in order,
   followed by the ones not fetched yet", so the next page starts at
   offset = number of undecided posts we hold. Ids already held are filtered
   out as a guard against a rescan shifting things. Tagging can take a post
   out of a tag scope (or the untagged one) on the server too, so those are
   not counted either. */

const PAGE = 50;
const TOP_UP_BELOW = 10;
const PRELOAD_AHEAD = 2;
const MUTE_KEY = "feedvault.review.muted";

function readMuted() {
  try { return localStorage.getItem(MUTE_KEY) !== "0"; } catch { return true; }
}

function nextUndecided(queue, status, from) {
  for (let i = from + 1; i < queue.length; i++) if (!status[queue[i].id]) return i;
  return -1;
}
function prevUndecided(queue, status, from) {
  for (let i = Math.min(from, queue.length) - 1; i >= 0; i--) if (!status[queue[i].id]) return i;
  return -1;
}

const initial = { queue: [], status: {}, pos: 0, left: null, exhausted: false, error: null };

function reducer(s, a) {
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

function errorText(r, fallback) {
  const errs = r?.errors || [];
  if (errs.length) {
    const first = errs[0];
    return `${errs.length} file${errs.length === 1 ? "" : "s"} could not be moved: ${first.error} (${first.path})`;
  }
  return r?.error || fallback;
}

const SHORTCUTS = [
  ["K / Enter", "Keep, next post"],
  ["D / Delete", "Trash the whole post, next post"],
  ["X", "Trash only this item (the post stays)"],
  ["← / →", "Previous / next item in the post"],
  ["↑ / J", "Previous post (no decision)"],
  ["↓ / L", "Next post (skip, no decision)"],
  ["Space", "Play / pause video"],
  ["M", "Mute on / off (remembered)"],
  ["T", "Tag this post (Enter applies, Esc closes)"],
  ["C", "Add this post to a collection"],
  ["Z / Ctrl+Z", "Undo the last decision"],
  ["F", "Fullscreen"],
  ["?", "This help"],
];

function Kbd({ children }) {
  return <kbd className="kbd">{children}</kbd>;
}

function ReviewSession({ scope, scopeControls }) {
  const toast = useToast();
  const [state, dispatch] = useReducer(reducer, initial);
  const { queue, status, pos, left, exhausted } = state;
  const [full, setFull] = useState({});           // id -> full post | { error }
  const [item, setItem] = useState(0);
  const [itemFor, setItemFor] = useState(null);
  const [undo, setUndo] = useState([]);
  const [session, setSession] = useState({ kept: 0, trashed: 0, items: 0 });
  const [busy, setBusy] = useState(false);
  const [help, setHelp] = useState(false);
  const [muted, setMuted] = useState(readMuted);
  const [tagging, setTagging] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [tagsOf, setTagsOf] = useState({});      // id -> tags changed this session
  const tagsApi = useApi(getTags, 0);
  const busyRef     = useRef(false);
  const fetchingRef = useRef(false);
  const requested   = useRef(new Set());
  const videoRef    = useRef(null);
  const rootRef     = useRef(null);

  const cur = pos < queue.length ? queue[pos] : null;
  const post = cur ? full[cur.id] : null;
  const media = post?.media || [];

  // A new current post starts on its first item.
  if ((cur?.id ?? null) !== itemFor) {
    setItemFor(cur?.id ?? null);
    setItem(0);
    setTagging(false);
  }
  const safeItem = Math.min(item, Math.max(0, media.length - 1));
  const m = media[safeItem];

  // Upcoming undecided posts, for full-post fetches and media preloading.
  const upcoming = useMemo(() => {
    const out = [];
    for (let i = pos + 1; i < queue.length && out.length < PRELOAD_AHEAD; i++) {
      if (!status[queue[i].id]) out.push(queue[i]);
    }
    return out;
  }, [queue, status, pos]);
  const undecidedAhead = useMemo(() => {
    let n = 0;
    for (let i = pos; i < queue.length; i++) if (!status[queue[i].id]) n++;
    return n;
  }, [queue, status, pos]);
  const undecidedHeld = useMemo(() => queue.filter(p => !status[p.id]).length, [queue, status]);
  const outOfScope = useMemo(
    () => queue.filter(p => !status[p.id] && tagsOf[p.id] && !tagsMatch(tagsOf[p.id], scope.tag, scope.untagged)).length,
    [queue, status, tagsOf, scope]);

  // Top up the queue when it runs low.
  useEffect(() => {
    if (exhausted || fetchingRef.current || undecidedAhead >= TOP_UP_BELOW) return;
    fetchingRef.current = true;
    getPosts({ ...scope, review: "unreviewed", sort: "posted", offset: undecidedHeld - outOfScope, limit: PAGE })
      .then(
        r => dispatch({ type: "loaded", posts: r.posts || [], total: r.total ?? 0 }),
        e => dispatch({ type: "error", error: e }),
      )
      .finally(() => { fetchingRef.current = false; });
  }, [exhausted, undecidedAhead, undecidedHeld, outOfScope, scope]);

  // Full posts (media lists) for the current one and the next few.
  const fetchFull = useCallback(p => {
    requested.current.add(p.id);
    getPost(p.platform, p.post_id).then(
      fp => setFull(f => ({ ...f, [p.id]: fp })),
      e  => setFull(f => ({ ...f, [p.id]: { error: e } })),
    );
  }, []);
  useEffect(() => {
    for (const p of [cur, ...upcoming]) {
      if (p && !requested.current.has(p.id)) fetchFull(p);
    }
  }, [cur, upcoming, fetchFull]);

  function refetchFull(p) {
    requested.current.delete(p.id);
    setFull(f => { const n = { ...f }; delete n[p.id]; return n; });
    fetchFull(p);
  }

  // Preload: the next item of this post, and the first item of the next posts.
  const preloadVideos = [];
  const preloadImages = [];
  const nextInPost = media[safeItem + 1];
  if (nextInPost && !nextInPost.missing) (nextInPost.kind === "video" ? preloadVideos : preloadImages).push(nextInPost.url);
  for (const p of upcoming) {
    const first = full[p.id]?.media?.[0];
    if (first && !first.missing) (first.kind === "video" ? preloadVideos : preloadImages).push(first.url);
  }
  const imgKey = preloadImages.join("|");
  useEffect(() => {
    for (const url of imgKey ? imgKey.split("|") : []) { const im = new Image(); im.src = url; }
  }, [imgKey]);

  useEffect(() => {
    try { localStorage.setItem(MUTE_KEY, muted ? "1" : "0"); } catch { /* private mode */ }
    if (videoRef.current) videoRef.current.muted = muted;
  }, [muted]);

  /* ── Actions ───────────────────────────────────────────── */

  const run = useCallback(async fn => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try { await fn(); }
    catch (e) { toast(e.message, "err"); }
    finally { busyRef.current = false; setBusy(false); }
  }, [toast]);

  const keep = () => cur && run(async () => {
    const r = await setDecision([cur.id], "keep");
    if (!r?.ok) { toast(r?.error || "Could not mark as kept.", "err"); return; }
    setUndo(u => [...u, { type: "keep", post: cur, index: pos }]);
    setSession(s => ({ ...s, kept: s.kept + 1 }));
    dispatch({ type: "decide", id: cur.id, decision: "keep" });
  });

  const trashPost = () => cur && run(async () => {
    const r = await deleteItems({ posts: [cur.id] });
    if (!r?.posts?.includes(cur.id)) { toast(errorText(r, "The post could not be trashed."), "err"); return; }
    if (r.errors?.length) toast(errorText(r, ""), "err");
    setUndo(u => [...u, { type: "trash", post: cur, index: pos }]);
    setSession(s => ({ ...s, trashed: s.trashed + 1 }));
    dispatch({ type: "decide", id: cur.id, decision: "trash" });
  });

  const trashItem = () => cur && m && run(async () => {
    const r = await deleteItems({ media: [m.id] });
    if (!r?.media?.includes(m.id)) { toast(errorText(r, "The item could not be trashed."), "err"); return; }
    if (r.errors?.length) toast(errorText(r, ""), "err");
    let postGone = false;
    try {
      const fp = await getPost(cur.platform, cur.post_id);
      setFull(f => ({ ...f, [cur.id]: fp }));
      setItem(i => Math.min(i, Math.max(0, (fp.media?.length || 1) - 1)));
    } catch (e) {
      if (e.status !== 404) throw e;
      postGone = true;
    }
    setUndo(u => [...u, { type: "item", post: cur, index: pos, itemIndex: safeItem, postGone }]);
    setSession(s => ({ ...s, items: s.items + 1, trashed: s.trashed + (postGone ? 1 : 0) }));
    if (postGone) {
      toast("That was the last item: the post is gone.");
      dispatch({ type: "decide", id: cur.id, decision: "trash" });
    }
  });

  const undoLast = () => undo.length && run(async () => {
    const e = undo[undo.length - 1];
    if (e.type === "keep") {
      const r = await setDecision([e.post.id], null);
      if (!r?.ok) { toast(r?.error || "Undo failed.", "err"); return; }
      setSession(s => ({ ...s, kept: s.kept - 1 }));
      dispatch({ type: "undecide", id: e.post.id, index: e.index });
    } else {
      const r = await restorePosts([e.post.id]);
      if (!r?.posts?.includes(e.post.id)) { toast(errorText(r, "Could not restore from the trash."), "err"); return; }
      if (r.errors?.length) toast(errorText(r, ""), "err");
      // Restoring re-indexes the post, so its media ids may have changed.
      refetchFull(e.post);
      if (e.type === "trash" || e.postGone) {
        setSession(s => ({ ...s, trashed: s.trashed - 1, items: s.items - (e.type === "item" ? 1 : 0) }));
        dispatch({ type: "undecide", id: e.post.id, index: e.index });
      } else {
        setSession(s => ({ ...s, items: s.items - 1 }));
        dispatch({ type: "goto", index: e.index });
      }
      if (e.type === "item") setItem(e.itemIndex);
    }
    setUndo(u => u.slice(0, -1));
    toast("Undone.");
  });

  const curTags = cur ? tagsOf[cur.id] ?? post?.tags ?? cur.tags ?? [] : [];
  // Not through run(): a tag typed while a keep or trash is on its way still
  // goes out, for the post it was typed on.
  const tagPost = async ({ add = [], remove = [] }) => {
    if (!cur) return;
    const id = cur.id, base = curTags;
    try {
      const r = await applyTags([id], { add, remove });
      if (!r?.ok) { toast(r?.error || "Could not change the tags.", "err"); return; }
      if (!r.posts?.includes(id)) { toast("This post is no longer in the index.", "err"); return; }
      const known = tagsApi.data || [];
      const names = add.map(a => known.find(t => sameTag(t.name, a))?.name || a);
      setTagsOf(t => ({ ...t, [id]: withTags(t[id] ?? base, names, remove) }));
      tagsApi.reload();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const stepItem = d => media.length > 1 && setItem(i => (Math.min(i, media.length - 1) + d + media.length) % media.length);

  function togglePlay() {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {}); else v.pause();
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else rootRef.current?.requestFullscreen?.().catch(() => {});
  }

  // Latest handlers for the one window listener.
  const openTags = () => cur && !post?.error && setTagging(true);
  const openCollections = () => cur && post && !post.error && setCollecting(true);
  // After a collection change: the post's list of collections, without a reload flash.
  const collectionsChanged = () => {
    const p = cur;
    getPost(p.platform, p.post_id).then(fp => setFull(f => ({ ...f, [p.id]: fp })), () => {});
  };
  const keys = { keep, trashPost, trashItem, undoLast, stepItem, togglePlay, toggleFullscreen, openTags,
                 openCollections, blocked: collecting };
  const keysRef = useRef(keys);
  useEffect(() => { keysRef.current = keys; });

  useEffect(() => {
    function onKey(e) {
      const t = e.target;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable) return;
      // A focused button handles its own Enter/Space.
      if (t.tagName === "BUTTON" && (e.key === "Enter" || e.key === " ")) return;
      const k = keysRef.current;
      if (k.blocked) return;                    // a dialog has the keyboard
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if ((e.ctrlKey || e.metaKey) && key === "z") { e.preventDefault(); k.undoLast(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      let handled = true;
      switch (key) {
        case "?":          setHelp(h => !h); break;
        case "Escape":     if (document.fullscreenElement) handled = false; else setHelp(false); break;
        case "k": case "Enter":      k.keep(); break;
        case "d": case "Delete":     k.trashPost(); break;
        case "x":          k.trashItem(); break;
        case "ArrowLeft":  k.stepItem(-1); break;
        case "ArrowRight": k.stepItem(1); break;
        case "ArrowUp": case "j":    dispatch({ type: "prev" }); break;
        case "ArrowDown": case "l":  dispatch({ type: "next" }); break;
        case " ":          k.togglePlay(); break;
        case "m":          setMuted(v => !v); break;
        case "z":          k.undoLast(); break;
        case "f":          k.toggleFullscreen(); break;
        case "t":          k.openTags(); break;
        case "c":          k.openCollections(); break;
        default:           handled = false;
      }
      if (handled) e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Mouse clicks on the action buttons must not leave focus there, or the next
  // Enter would press the button again instead of meaning "keep".
  function act(e, fn) { e.currentTarget.blur(); fn(); }

  /* ── Render ────────────────────────────────────────────── */

  const handle = cur?.author?.handle || "unknown";
  const alt = cur ? excerpt(cur.text, 140) || `Post by @${handle}` : "";
  const skipped = undecidedHeld - undecidedAhead;
  const doneAll = !cur && exhausted;

  let stage;
  if (state.error && !cur) {
    stage = <div className="review-empty"><Icon name="warn" size={28} /><p>Could not load posts: {state.error.message}</p></div>;
  } else if (doneAll) {
    stage = (
      <div className="review-empty">
        <Icon name="check" size={34} className="review-done-mark" />
        <h3>Nothing left to review in this scope</h3>
        <p className="mono">
          this session: {session.kept} kept · {session.trashed} trashed{session.items ? ` · ${session.items} items` : ""}
        </p>
        {skipped > 0 && (
          <button type="button" className="btn-secondary" onClick={() => dispatch({ type: "goto", index: nextUndecided(queue, status, -1) })}>
            Back to the {skipped} skipped post{skipped === 1 ? "" : "s"}
          </button>
        )}
        {undo.length > 0 && <p className="dim">Press <Kbd>Z</Kbd> to undo the last decision.</p>}
      </div>
    );
  } else if (!cur) {
    stage = <div className="review-empty dim">Loading…</div>;
  } else if (post?.error) {
    stage = (
      <div className="review-empty">
        <Icon name="warn" size={28} />
        <p>{post.error.status === 404 ? "This post is no longer in the index." : `Could not load this post: ${post.error.message}`}</p>
        <p className="dim">Press <Kbd>↓</Kbd> to skip it.</p>
      </div>
    );
  } else if (!post) {
    stage = cur.cover && cur.cover.poster !== false
      ? <img className="review-media is-placeholder" src={cur.cover.url} alt={alt} onError={e => { e.currentTarget.style.visibility = "hidden"; }} />
      : <div className="review-empty dim">Loading…</div>;
  } else if (media.length === 0) {
    stage = <div className="review-text"><RichText text={post.text || "(no text)"} className="is-large" /></div>;
  } else if (m.missing) {
    stage = <div className="review-empty"><Icon name="warn" size={28} /><p>This file is missing on disk.</p></div>;
  } else if (m.kind === "video") {
    stage = (
      <video
        key={m.id}
        ref={videoRef}
        className="review-media"
        src={m.url}
        poster={m.poster_url || m.thumb_url || undefined}
        autoPlay
        loop
        controls
        playsInline
        muted={muted}
        tabIndex={-1}
        aria-label={alt}
        onLoadedMetadata={e => { e.currentTarget.muted = muted; e.currentTarget.play().catch(() => {}); }}
      />
    );
  } else {
    stage = <img key={m.id} className="review-media" src={m.url} alt={alt} />;
  }

  return (
    <div className="review" ref={rootRef}>
      <div className="review-stage">
        {stage}
        {media.length > 1 && cur && (
          <>
            <button type="button" className="carousel-arrow prev" onClick={e => act(e, () => stepItem(-1))} aria-label="Previous item (←)">
              <Icon name="chevLeft" size={20} />
            </button>
            <button type="button" className="carousel-arrow next" onClick={e => act(e, () => stepItem(1))} aria-label="Next item (→)">
              <Icon name="chevRight" size={20} />
            </button>
            <span className="carousel-count">{safeItem + 1}/{media.length}</span>
          </>
        )}
        {busy && <span className="review-busy"><Icon name="refresh" size={14} className="spin" /></span>}
        <div hidden aria-hidden="true">
          {preloadVideos.map(url => <video key={url} src={url} preload="auto" muted />)}
        </div>
      </div>

      <aside className="review-side">
        {scopeControls}

        <div className="review-progress">
          <span className="review-left mono">{left == null ? "…" : left.toLocaleString()}</span>
          <span className="dim">left to review</span>
          <span className="review-session mono">
            <span className="ok-text">{session.kept} kept</span> · <span className="warn-text">{session.trashed} trashed</span>
            {session.items > 0 && <> · {session.items} item{session.items === 1 ? "" : "s"}</>}
          </span>
        </div>

        {cur && (
          <div className="review-info">
            <div className="review-byline">
              <Link to={authorFeedPath(cur.platform, cur.author)} className="post-byline-handle">@{handle}</Link>
              {media.length > 1 && <span className="chip">{safeItem + 1}/{media.length}</span>}
              <span className="chip">{cur.kind}</span>
            </div>
            <div className="review-meta mono">
              <time dateTime={fmtIso(cur.posted_at)}>{fmtFullDate(cur.posted_at)}</time>
              {m?.size != null && <span className="dim"> · {fmtBytes(m.size)}</span>}
            </div>
            {post?.album && <div className="review-album"><span className="dim">Highlight</span> {post.album}</div>}
            {cur.text
              ? <div className="review-caption"><RichText text={cur.text} /></div>
              : <p className="dim review-caption">No caption.</p>}
            <div className="review-tags">
              <TagChips tags={curTags} onRemove={name => tagPost({ remove: [name] })} busy={busy} />
              {tagging ? (
                <TagInput
                  autoFocus
                  tags={tagsApi.data || []}
                  exclude={curTags}
                  placeholder="Tag this post…"
                  onAdd={name => { setTagging(false); tagPost({ add: [name] }); }}
                  onClose={() => setTagging(false)}
                />
              ) : (
                <button type="button" className="btn-ghost review-tag-btn" onClick={e => act(e, openTags)} disabled={!!post?.error}>
                  <Icon name="tag" size={13} />Tag<Kbd>T</Kbd>
                </button>
              )}
            </div>
            <div className="review-collections">
              {post?.collections?.length > 0 && (
                <ul className="tag-chips">
                  {post.collections.map(c => (
                    <li key={c.id} className="tag-chip is-collection"><Link to={`/collections/${c.id}`}>{c.name}</Link></li>
                  ))}
                </ul>
              )}
              <button type="button" className="btn-ghost review-tag-btn" onClick={e => act(e, openCollections)} disabled={!post || !!post.error}>
                <Icon name="bookmark" size={13} />Collection<Kbd>C</Kbd>
              </button>
            </div>
            <Link className="text-link review-open" to={`/p/${encodeURIComponent(cur.platform)}/${encodeURIComponent(cur.post_id)}`}>
              Open post page
            </Link>
          </div>
        )}

        <div className="review-actions">
          <button type="button" className="btn-keep" onClick={e => act(e, keep)} disabled={!cur || busy}>
            <Icon name="check" size={15} />Keep<Kbd>K</Kbd>
          </button>
          <button type="button" className="btn-danger" onClick={e => act(e, trashPost)} disabled={!cur || busy}>
            <Icon name="trash" size={15} />Trash post<Kbd>D</Kbd>
          </button>
          {media.length > 1 && (
            <button type="button" className="btn-danger-soft" onClick={e => act(e, trashItem)} disabled={!m || busy}>
              <Icon name="trash" size={14} />Trash this item<Kbd>X</Kbd>
            </button>
          )}
          <div className="review-actions-row">
            <button type="button" className="btn-secondary" onClick={e => act(e, () => dispatch({ type: "prev" }))} title="Previous post (↑ or J)">
              <Icon name="arrowUp" size={14} /><Kbd>↑</Kbd>
            </button>
            <button type="button" className="btn-secondary" onClick={e => act(e, () => dispatch({ type: "next" }))} disabled={!cur} title="Skip to the next post (↓ or L)">
              <Icon name="skip" size={14} />Skip<Kbd>↓</Kbd>
            </button>
            <button type="button" className="btn-secondary" onClick={e => act(e, undoLast)} disabled={!undo.length || busy} title="Undo the last decision (Z)">
              <Icon name="undo" size={14} />Undo<Kbd>Z</Kbd>
            </button>
          </div>
          <div className="review-actions-row">
            <button type="button" className="btn-secondary" onClick={e => act(e, () => setMuted(v => !v))} aria-pressed={muted} title="Mute (M)">
              <Icon name={muted ? "volumeOff" : "volume"} size={14} /><Kbd>M</Kbd>
            </button>
            <button type="button" className="btn-secondary" onClick={e => act(e, toggleFullscreen)} title="Fullscreen (F)">
              <Icon name="expand" size={14} /><Kbd>F</Kbd>
            </button>
            <button type="button" className="btn-secondary" onClick={e => act(e, () => setHelp(true))} title="Keyboard shortcuts (?)">
              <Icon name="keyboard" size={14} /><Kbd>?</Kbd>
            </button>
          </div>
        </div>
      </aside>

      {collecting && cur && post && (
        <CollectionDialog
          posts={[cur.id]}
          member={(post.collections || []).map(c => c.id)}
          onChanged={collectionsChanged}
          onClose={() => setCollecting(false)}
        />
      )}

      {help && (
        <div className="review-help" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" onClick={() => setHelp(false)}>
          <div className="review-help-box" onClick={e => e.stopPropagation()}>
            <h3 className="modal-title">Keyboard shortcuts</h3>
            <dl>
              {SHORTCUTS.map(([k, d]) => (
                <div key={k}><dt><Kbd>{k}</Kbd></dt><dd>{d}</dd></div>
              ))}
            </dl>
            <p className="dim">Trashing moves files to the trash folder; undo brings them back. Empty the trash from Settings.</p>
            <button type="button" className="btn-secondary" onClick={() => setHelp(false)} autoFocus>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Review() {
  const [params, setParams] = useSearchParams();
  const platform = params.get("platform") || "";
  const author   = params.get("author") || "";
  const kind     = params.get("kind") || "";
  const order    = params.get("order") === "asc" ? "asc" : "desc";
  const tag      = params.get("tag") || "";
  const untagged = !tag && params.get("untagged") === "1";
  const scope    = useMemo(() => ({ platform, author, kind, order, tag: tag ? [tag] : [], untagged }),
    [platform, author, kind, order, tag, untagged]);
  const scopeKey = JSON.stringify(scope);

  const { data: authorsData } = useApi(getAuthors, 0);
  const { data: tagsData } = useApi(getTags, 0);
  const authors = useMemo(() => authorsData || [], [authorsData]);
  const platforms = useMemo(() => {
    const set = new Set(authors.map(a => a.platform).filter(Boolean));
    if (platform) set.add(platform);
    return [...set].sort();
  }, [authors, platform]);

  function setParam(changes) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    setParams(next, { replace: true });
  }
  const authorValue = author ? `${platform}:${author}` : "";

  const controls = (
    <div className="review-scope" role="group" aria-label="Review scope">
      <select className="sort-select" aria-label="Platform" value={platform} onChange={e => setParam({ platform: e.target.value, author: "" })}>
        <option value="">All platforms</option>
        {platforms.map(p => <option key={p} value={p}>{platformLabel(p)}</option>)}
      </select>
      <select className="sort-select" aria-label="Kind" value={kind} onChange={e => setParam({ kind: e.target.value })}>
        <option value="">All kinds</option>
        {KINDS.map(k => <option key={k} value={k}>{k}</option>)}
      </select>
      <select
        className="sort-select review-scope-author"
        aria-label="Creator"
        value={authors.some(a => `${a.platform}:${a.id}` === authorValue) ? authorValue : ""}
        onChange={e => {
          const v = e.target.value;
          if (!v) return setParam({ author: "" });
          const i = v.indexOf(":");
          setParam({ platform: v.slice(0, i), author: v.slice(i + 1) });
        }}
      >
        <option value="">All creators</option>
        {authors.filter(a => a.id != null && (!platform || a.platform === platform)).map(a => (
          <option key={`${a.platform}:${a.id}`} value={`${a.platform}:${a.id}`}>@{a.handle || a.id} ({a.count})</option>
        ))}
      </select>
      <select
        className="sort-select review-scope-tag"
        aria-label="Tag"
        value={untagged ? "__untagged" : tag}
        onChange={e => {
          const v = e.target.value;
          setParam(v === "__untagged" ? { untagged: "1", tag: "" } : { tag: v, untagged: "" });
        }}
      >
        <option value="">Any tags</option>
        <option value="__untagged">Untagged only</option>
        {tag && !(tagsData || []).some(t => t.name === tag) && <option value={tag}>{tag}</option>}
        {(tagsData || []).map(t => <option key={t.name} value={t.name}>{t.name} ({t.count})</option>)}
      </select>
      <select className="sort-select" aria-label="Order" value={order} onChange={e => setParam({ order: e.target.value === "asc" ? "asc" : "" })}>
        <option value="desc">Newest first</option>
        <option value="asc">Oldest first</option>
      </select>
    </div>
  );

  // A new scope is a new session: fresh queue, counts and undo stack.
  return <ReviewSession key={scopeKey} scope={scope} scopeControls={controls} />;
}
