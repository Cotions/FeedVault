import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getPosts, getAuthors, deleteItems, setDecision } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { KINDS, platformLabel, fmtBytes } from "../lib/fmt";
import PostCard from "../components/PostCard";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";

const PAGE = 60;
const MAX_LIMIT = 200;
const FILTERS = ["platform", "kind", "author", "review", "sort"];

export default function Feed() {
  const { refreshKey, running, start } = useScan();
  const [params, setParams] = useSearchParams();
  const rawQ     = params.get("q") || "";
  // Already debounced: the header search box pushes to the URL after a pause.
  const q        = rawQ.trim();
  const platform = params.get("platform") || "";
  const kind     = params.get("kind") || "";
  const author   = params.get("author") || "";
  const sort     = params.get("sort") === "saved" ? "saved" : "posted";
  const reviewP  = params.get("review");
  const review   = reviewP === "unreviewed" || reviewP === "kept" ? reviewP : "";

  const filters = useMemo(() => ({ q, platform, kind, author, review, sort }), [q, platform, kind, author, review, sort]);
  const filterKey = JSON.stringify(filters);

  // One result object tagged with the filters it answers. While a new filter
  // loads, the old cards stay on screen (dimmed) instead of flashing empty.
  const [result, setResult] = useState({ key: null, posts: [], total: 0, error: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const busy     = useRef(false);
  const countRef = useRef(0);
  const sentinel = useRef(null);

  useEffect(() => { countRef.current = result.key === filterKey ? result.posts.length : 0; });

  useEffect(() => {
    let alive = true;
    // After a rescan, reload as many posts as were already shown so the scroll
    // position still has content under it.
    const limit = Math.min(MAX_LIMIT, Math.max(PAGE, countRef.current));
    getPosts({ ...filters, offset: 0, limit }).then(
      r     => { if (alive) setResult({ key: filterKey, posts: r.posts || [], total: r.total ?? 0, error: null }); },
      error => { if (alive) setResult(prev => ({ ...prev, key: filterKey, error })); },
    );
    return () => { alive = false; };
  }, [filters, filterKey, refreshKey]);

  const current = result.key === filterKey;
  const posts   = result.posts;
  const hasMore = current && !result.error && posts.length < result.total;

  const loadMore = useCallback(async () => {
    if (busy.current || !hasMore) return;
    busy.current = true;
    setLoadingMore(true);
    try {
      const r = await getPosts({ ...filters, offset: posts.length, limit: PAGE });
      setResult(prev => {
        if (prev.key !== filterKey) return prev;
        const seen = new Set(prev.posts.map(p => p.id));
        const more = (r.posts || []).filter(p => !seen.has(p.id));
        return { ...prev, posts: [...prev.posts, ...more], total: r.total ?? prev.total };
      });
    } catch { /* the button stays, the user can retry */ }
    finally {
      busy.current = false;
      setLoadingMore(false);
    }
  }, [hasMore, filters, filterKey, posts.length]);

  /* ── Select mode ─────────────────────────────────────── */
  const toast = useToast();
  const [selectMode, setSelectMode] = useState(false);
  const [selected,   setSelected]   = useState(() => new Set());
  const [anchor,     setAnchor]     = useState(null);   // last clicked index, for shift ranges
  const [confirmDel, setConfirmDel] = useState(false);
  const [deleting,   setDeleting]   = useState(false);
  const [dlgError,   setDlgError]   = useState(null);
  const [delErrors,  setDelErrors]  = useState(null);

  // A new filter shows different cards: drop a selection the user can no
  // longer see (adjusting state during render, not in an effect).
  const [selKey, setSelKey] = useState(filterKey);
  if (selKey !== filterKey) {
    setSelKey(filterKey);
    if (selected.size) setSelected(new Set());
    setAnchor(null);
  }

  const exitSelect = useCallback(() => {
    setSelectMode(false);
    setSelected(new Set());
    setAnchor(null);
  }, []);

  useEffect(() => {
    if (!selectMode) return;
    function onKey(e) {
      if (e.key === "Escape" && !confirmDel && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) exitSelect();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectMode, confirmDel, exitSelect]);

  function toggle(index, shift) {
    const id = posts[index]?.id;
    if (!id) return;
    setSelected(prev => {
      const next = new Set(prev);
      if (shift && anchor != null && anchor < posts.length) {
        const [a, b] = anchor < index ? [anchor, index] : [index, anchor];
        for (let i = a; i <= b; i++) next.add(posts[i].id);
      } else if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
    setAnchor(index);
  }

  const selectedPosts = posts.filter(p => selected.has(p.id));
  const selectedCount = selectedPosts.length;

  const [keeping, setKeeping] = useState(false);
  async function runKeep() {
    setKeeping(true);
    try {
      const ids = selectedPosts.map(p => p.id);
      const r = await setDecision(ids, "keep");
      if (!r?.ok) { toast(r?.error || "Could not mark as kept.", "err"); return; }
      const done = new Set(r.posts || ids);
      // Under the Unreviewed filter a kept post no longer belongs in the list.
      const leaves = review === "unreviewed";
      setResult(prev => ({
        ...prev,
        posts: leaves
          ? prev.posts.filter(p => !done.has(p.id))
          : prev.posts.map(p => (done.has(p.id) ? { ...p, decision: "keep" } : p)),
        total: leaves ? Math.max(0, prev.total - done.size) : prev.total,
      }));
      setSelected(new Set());
      setAnchor(null);
      toast(`${done.size} post${done.size === 1 ? "" : "s"} marked as kept.`);
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setKeeping(false);
    }
  }

  async function runDelete() {
    setDeleting(true);
    setDlgError(null);
    try {
      const ids = selectedPosts.map(p => p.id);
      const r = await deleteItems({ posts: ids });
      const gone = new Set(r?.posts || []);
      if (!r?.ok && gone.size === 0) { setDlgError(r?.error || "Nothing could be deleted."); return; }
      setResult(prev => ({
        ...prev,
        posts: prev.posts.filter(p => !gone.has(p.id)),
        total: Math.max(0, prev.total - gone.size),
      }));
      setSelected(prev => new Set([...prev].filter(id => !gone.has(id))));
      setAnchor(null);
      setConfirmDel(false);
      setDelErrors(r.errors?.length ? r.errors : null);
      if (gone.size) {
        toast(`${gone.size} post${gone.size === 1 ? "" : "s"} moved to the trash (${r.files ?? 0} files, ${fmtBytes(r.bytes ?? 0)}).`);
      }
      if (gone.size < ids.length && !r.errors?.length) {
        toast(`${ids.length - gone.size} post${ids.length - gone.size === 1 ? "" : "s"} could not be deleted.`, "err");
      }
    } catch (e) {
      setDlgError(e.message);
    } finally {
      setDeleting(false);
    }
  }

  // Infinite scroll: fetch the next page when the sentinel nears the viewport.
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasMore) return;
    const io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) loadMore();
    }, { rootMargin: "900px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, loadMore]);

  const authorsApi = useApi(getAuthors, refreshKey);
  const authors    = useMemo(() => authorsApi.data || [], [authorsApi.data]);
  const platforms  = useMemo(() => {
    const set = new Set(authors.map(a => a.platform).filter(Boolean));
    if (platform) set.add(platform);
    return [...set].sort();
  }, [authors, platform]);

  function setParam(changes) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v); else next.delete(k);
    }
    setParams(next);
  }

  // Author ids are only unique within a platform, so the select carries both.
  const authorValue = author
    ? `${platform || (authors.find(a => String(a.id) === author)?.platform ?? "")}:${author}`
    : "";
  function onAuthor(value) {
    if (!value) return setParam({ author: "" });
    const i = value.indexOf(":");
    setParam({ platform: value.slice(0, i), author: value.slice(i + 1) });
  }
  const authorKnown = !author || authors.some(a => `${a.platform}:${a.id}` === authorValue);
  const selectedAuthor = authors.find(a => `${a.platform}:${a.id}` === authorValue);

  const anyFilter = !!(rawQ || FILTERS.some(f => f !== "sort" && params.get(f)));
  function clearFilters() {
    const next = new URLSearchParams();
    if (sort === "saved") next.set("sort", "saved");
    setParams(next);
  }

  const firstLoad = result.key === null && !result.error;
  const error = current ? result.error : null;
  const showEmpty = current && !error && result.total === 0;

  return (
    <div className="card feed">
      <div className="page-head">
        <h2 className="page-title">
          {q ? "Results" : selectedAuthor ? `@${selectedAuthor.handle}` : "Feed"}
        </h2>
        <span className="page-count">
          {current && !error
            ? q ? `${result.total.toLocaleString()} for “${q}”` : result.total.toLocaleString()
            : "…"}
        </span>
        <div className="page-head-spacer" />
      </div>

      <div className="feed-filters" role="group" aria-label="Filters">
        <label className="filter">
          <span>Platform</span>
          <select className="sort-select" value={platform} onChange={e => setParam({ platform: e.target.value, author: "" })}>
            <option value="">All</option>
            {platforms.map(p => <option key={p} value={p}>{platformLabel(p)}</option>)}
          </select>
        </label>
        <label className="filter">
          <span>Kind</span>
          <select className="sort-select" value={kind} onChange={e => setParam({ kind: e.target.value })}>
            <option value="">All</option>
            {KINDS.map(k => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <label className="filter filter-author">
          <span>Author</span>
          <select className="sort-select" value={authorKnown ? authorValue : "__unknown"} onChange={e => onAuthor(e.target.value)}>
            <option value="">All</option>
            {!authorKnown && <option value="__unknown" disabled>id {author}</option>}
            {authors.filter(a => a.id != null && (!platform || a.platform === platform)).map(a => (
              <option key={`${a.platform}:${a.id}`} value={`${a.platform}:${a.id}`}>
                @{a.handle || a.name || a.id}{platform ? "" : ` · ${platformLabel(a.platform)}`} ({a.count})
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span>Review</span>
          <select className="sort-select" value={review} onChange={e => setParam({ review: e.target.value })}>
            <option value="">All</option>
            <option value="unreviewed">Unreviewed</option>
            <option value="kept">Kept</option>
          </select>
        </label>
        <label className="filter">
          <span>Sort</span>
          <select className="sort-select" value={sort} onChange={e => setParam({ sort: e.target.value === "saved" ? "saved" : "" })}>
            <option value="posted">Newest posted</option>
            <option value="saved">Newest saved</option>
          </select>
        </label>
        {anyFilter && (
          <button type="button" className="btn-ghost filter-clear" onClick={clearFilters}>
            <Icon name="close" size={12} /> clear filters
          </button>
        )}
        <div className="page-head-spacer" />
        {author && (
          <Link
            className="btn-secondary review-link"
            to={`/review?${new URLSearchParams({ ...(platform ? { platform } : {}), author })}`}
            title="Keep or trash this creator's unreviewed posts, one by one"
          >
            <Icon name="review" size={14} />Review this creator
          </Link>
        )}
        {posts.length > 0 && (
          <button
            type="button"
            className={`btn-secondary select-toggle${selectMode ? " is-on" : ""}`}
            onClick={() => (selectMode ? exitSelect() : setSelectMode(true))}
            aria-pressed={selectMode}
            title={selectMode ? "Leave select mode (Esc)" : "Select posts to delete"}
          >
            <Icon name={selectMode ? "close" : "check"} size={14} />
            {selectMode ? "Done" : "Select"}
          </button>
        )}
      </div>

      <DeleteErrors errors={delErrors} onDismiss={() => setDelErrors(null)} />

      {error ? (
        <div className="empty">
          Could not load posts: {error.message}
        </div>
      ) : firstLoad ? (
        <div className="empty">Loading…</div>
      ) : showEmpty ? (
        anyFilter ? (
          <div className="empty">
            No posts match{q ? <> “{q}”</> : ""}.{" "}
            <button type="button" className="btn-link" onClick={clearFilters}>Clear filters</button>
          </div>
        ) : (
          <div className="feed-empty">
            <span className="feed-empty-mark"><Icon name="feed" size={30} /></span>
            <h3>No posts yet</h3>
            <p>
              FeedVault catalogs posts that a downloader already saved to disk. To fill it:
            </p>
            <ol>
              <li>
                Open <Link to="/settings" className="text-link">Settings</Link> and add the folder
                your downloads go to as a media root.
              </li>
              <li>
                Download some posts with instaloader, for example
                <code className="cmd">instaloader --no-compress-json -- -SHORTCODE</code>
                <span className="dim"> (plain <code>.json</code> is easiest to read by hand; compressed <code>.json.xz</code> works too).</span>
              </li>
              <li>Rescan, and they show up here.</li>
            </ol>
            <button type="button" className="btn-primary" onClick={start} disabled={running}>
              <Icon name="refresh" size={14} className={running ? "spin" : ""} />
              {running ? "Scanning…" : "Rescan now"}
            </button>
          </div>
        )
      ) : (
        <>
          <div className={`masonry${current ? "" : " is-stale"}`} aria-busy={!current}>
            {posts.map((p, i) => (
              <PostCard
                key={p.id}
                post={p}
                index={i}
                selectMode={selectMode}
                selected={selected.has(p.id)}
                onToggle={toggle}
              />
            ))}
          </div>
          {hasMore && (
            <div className="feed-more" ref={sentinel}>
              <button type="button" className="btn-secondary" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Loading…" : `Load more (${(result.total - posts.length).toLocaleString()} left)`}
              </button>
            </div>
          )}
          {selectMode && (
            <div className="select-bar" role="toolbar" aria-label="Selection">
              <span className="select-count"><b>{selectedCount}</b> selected</span>
              <button type="button" className="btn-ghost" onClick={() => setSelected(new Set(posts.map(p => p.id)))}>
                Select all loaded ({posts.length})
              </button>
              <button type="button" className="btn-ghost" onClick={() => { setSelected(new Set()); setAnchor(null); }} disabled={!selectedCount}>
                Clear
              </button>
              <div className="page-head-spacer" />
              <span className="select-hint">Shift-click selects a range · Esc exits</span>
              <button type="button" className="btn-keep" onClick={runKeep} disabled={!selectedCount || keeping}>
                <Icon name="check" size={14} />{keeping ? "Keeping…" : "Keep"}
              </button>
              <button type="button" className="btn-danger" onClick={() => { setDlgError(null); setConfirmDel(true); }} disabled={!selectedCount}>
                <Icon name="trash" size={14} />Delete…
              </button>
            </div>
          )}
          {current && !hasMore && posts.length > PAGE && (
            <div className="feed-end">end of feed · {posts.length.toLocaleString()} posts</div>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmDel}
        danger
        busy={deleting}
        error={dlgError}
        title={`Delete ${selectedCount} post${selectedCount === 1 ? "" : "s"}?`}
        confirmLabel={`Delete ${selectedCount} post${selectedCount === 1 ? "" : "s"}`}
        onConfirm={runDelete}
        onCancel={() => setConfirmDel(false)}
      >
        <p>
          Move {selectedCount === 1 ? "this post" : `these ${selectedCount} posts`} and
          all {selectedCount === 1 ? "its" : "their"} files
          ({selectedPosts.reduce((n, p) => n + (p.media_count || 0), 0)} media) to the trash?
          You can empty the trash from Settings.
        </p>
      </ConfirmDialog>
    </div>
  );
}
