import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getPosts, getPostsSummary, getAuthors, getPeople, getTags, deleteItems, setDecision, markSeen } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useSelection } from "../lib/useSelection";
import { useScan } from "../lib/scan";
import { useJobs } from "../lib/jobs";
import { useToast } from "../lib/toast";
import { KINDS, platformLabel, fmtBytes, fmtInt } from "../lib/fmt";
import { sameTag, searchTags, tagsMatch, withTags } from "../lib/tags";
import { personPath } from "../lib/people";
import PostCard from "../components/PostCard";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";
import SelectionBar from "../components/SelectionBar";
import BulkTagDialog from "../components/BulkTagDialog";
import CollectionDialog from "../components/CollectionDialog";
import CreatorPicker from "../components/CreatorPicker";

const PAGE = 60;
const MAX_LIMIT = 200;
const FILTERS = ["platform", "kind", "author", "person", "review", "tag", "untagged", "new", "sort"];
const SUMMARY_DELAY = 250;

export default function Feed() {
  const { refreshKey, running, start } = useScan();
  const [params, setParams] = useSearchParams();
  const rawQ     = params.get("q") || "";
  // Already debounced: the header search box pushes to the URL after a pause.
  const q        = rawQ.trim();
  const platform = params.get("platform") || "";
  const kind     = params.get("kind") || "";
  const author   = params.get("author") || "";
  const person   = params.get("person") || "";
  const sort     = params.get("sort") === "saved" ? "saved" : "posted";
  const reviewP  = params.get("review");
  const review   = reviewP === "unreviewed" || reviewP === "kept" ? reviewP : "";
  const tagKey   = JSON.stringify(params.getAll("tag").filter(t => t.trim()));
  const untagged = params.get("untagged") === "1";
  const newOnly  = params.get("new") === "1";
  const { newCount, started } = useJobs();
  const [seenTick, setSeenTick] = useState(0);         // bumped by "Mark all seen"

  const filters = useMemo(() => ({ q, platform, kind, author, person, review, tag: JSON.parse(tagKey), untagged, new: newOnly, sort }),
    [q, platform, kind, author, person, review, tagKey, untagged, newOnly, sort]);
  const tagFilter = filters.tag;
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
  }, [filters, filterKey, refreshKey, seenTick]);

  /* ── "Would free": size of everything the filters match ─ */
  // Only with a filter: unfiltered, it is the whole archive (see Storage).
  // Built from the cleaned values, so ?review=bogus or a blank q is no filter.
  const anyFilter  = !!(rawQ || FILTERS.some(f => f !== "sort" && params.get(f)));
  const summaryKey = q || platform || kind || author || person || review || tagFilter.length || untagged || newOnly
    ? JSON.stringify({ q, platform, kind, author, person, review, tag: tagFilter, untagged, new: newOnly }) : null;
  const [summary, setSummary] = useState({ key: null, data: null });
  const [summaryTick, setSummaryTick] = useState(0);    // bumped after a delete or keep
  useEffect(() => {
    if (!summaryKey) return;
    // Debounced with the filters and cancelled when they change again, so a
    // run of quick changes ends in one request whose answer cannot be stale.
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      getPostsSummary(JSON.parse(summaryKey), { signal: ctrl.signal }).then(
        data  => setSummary({ key: summaryKey, data }),
        error => { if (error.name !== "AbortError") setSummary({ key: summaryKey, data: null }); },
      );
    }, SUMMARY_DELAY);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [summaryKey, refreshKey, summaryTick, seenTick]);
  const freeable = summary.key === summaryKey ? summary.data : null;

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
  const [confirmDel, setConfirmDel] = useState(false);
  const [tagging,    setTagging]    = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [deleting,   setDeleting]   = useState(false);
  const [dlgError,   setDlgError]   = useState(null);
  const [delErrors,  setDelErrors]  = useState(null);
  // A new filter shows different cards: drop a selection the user can no longer see.
  const sel = useSelection(posts, { resetKey: filterKey, escapeBlocked: confirmDel || tagging || collecting });
  const selectedPosts = sel.selectedItems;
  const selectedCount = sel.count;
  const selectedBytes = selectedPosts.reduce((n, p) => n + (p.bytes || 0), 0);

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
      sel.clear();
      setSummaryTick(t => t + 1);
      toast(`${done.size} post${done.size === 1 ? "" : "s"} marked as kept.`);
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setKeeping(false);
    }
  }

  // Everything indexed so far stops being new; the sidebar and Creators
  // counts follow with the jobs poll.
  const [marking, setMarking] = useState(false);
  async function runMarkSeen() {
    setMarking(true);
    try {
      const r = await markSeen();
      if (!r?.ok) { toast(r?.error || "Could not mark the posts seen.", "err"); return; }
      started();
      setSeenTick(t => t + 1);
      toast(`${fmtInt(newCount)} new post${newCount === 1 ? "" : "s"} marked seen.`);
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setMarking(false);
    }
  }

  function onTagged(r, add, remove) {
    setTagging(false);
    const done = new Set(r.posts || []);
    // Tag names as the server spells them, for those that already existed.
    const known = tagsApi.data || [];
    add = add.map(a => known.find(t => sameTag(t.name, a))?.name || a);
    // A post whose tags no longer pass the tag filters leaves the list.
    const wanted = [...tagFilter, ...searchTags(q)];
    setResult(prev => {
      const posts = prev.posts.map(p => (done.has(p.id) ? { ...p, tags: withTags(p.tags, add, remove) } : p));
      const kept = posts.filter(p => !done.has(p.id) || tagsMatch(p.tags, wanted, untagged));
      return { ...prev, posts: kept, total: Math.max(0, prev.total - (posts.length - kept.length)) };
    });
    tagsApi.reload();
    setSummaryTick(t => t + 1);
    const parts = [r.added && `${r.added} tag${r.added === 1 ? "" : "s"} added`,
                   r.removed && `${r.removed} removed`].filter(Boolean);
    toast(`${done.size} post${done.size === 1 ? "" : "s"}: ${parts.join(", ") || "nothing to change"}.`);
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
      sel.drop(gone);
      if (gone.size) setSummaryTick(t => t + 1);
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
  const peopleApi  = useApi(getPeople, refreshKey);
  const tagsApi    = useApi(getTags, refreshKey);
  const allTags    = tagsApi.data || [];
  const authors    = useMemo(() => authorsApi.data || [], [authorsApi.data]);
  const platforms  = useMemo(() => {
    const set = new Set(authors.map(a => a.platform).filter(Boolean));
    if (platform) set.add(platform);
    return [...set].sort();
  }, [authors, platform]);

  function setParam(changes) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      next.delete(k);
      if (Array.isArray(v)) v.forEach(x => next.append(k, x));
      else if (v) next.set(k, v);
    }
    setParams(next);
  }

  function onTagFilter(value) {
    if (value === "__untagged") setParam({ untagged: "1", tag: [] });
    else if (value) setParam({ tag: [...tagFilter, value], untagged: "" });
  }

  // Author ids are only unique within a platform, so an account carries both.
  function onCreator(c) {
    if (!c) setParam({ author: "", person: "" });
    else if (c.person) setParam({ person: String(c.person.id), author: "", platform: "" });
    else setParam({ platform: c.account.platform, author: c.account.id, person: "" });
  }
  const selectedAuthor = author
    ? authors.find(a => (a.id === author || a.aliases?.includes(author)) && (!platform || a.platform === platform)) : null;
  const selectedPerson = person ? (peopleApi.data || []).find(p => String(p.id) === person) : null;

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
          {q ? "Results" : newOnly && !author && !person && !tagFilter.length ? "New since last visit"
            : selectedPerson ? selectedPerson.name : selectedAuthor ? `@${selectedAuthor.handle}`
            : tagFilter.length === 1 ? <span className="page-title-tag"><Icon name="tag" size={17} />{tagFilter[0]}</span> : "Feed"}
        </h2>
        <span className="page-count">
          {current && !error
            ? q ? `${result.total.toLocaleString()} for “${q}”` : result.total.toLocaleString()
            : "…"}
        </span>
        {freeable && (
          <span className="page-count would-free" title="What deleting every match would move to the trash (media files only)">
            {fmtInt(freeable.posts)} post{freeable.posts === 1 ? "" : "s"} · {fmtBytes(freeable.bytes)}
          </span>
        )}
        <div className="page-head-spacer" />
        {newCount > 0 && (
          <button type="button" className="btn-secondary" onClick={runMarkSeen} disabled={marking}
                  title="Posts indexed until now stop being new">
            <Icon name="check" size={14} />{marking ? "Marking…" : "Mark all seen"}
          </button>
        )}
      </div>

      <div className="feed-filters" role="group" aria-label="Filters">
        <button
          type="button"
          className={`btn-secondary select-toggle${newOnly ? " is-on" : ""}`}
          aria-pressed={newOnly}
          onClick={() => setParam({ new: newOnly ? "" : "1" })}
          title="Only posts indexed since you last marked everything seen"
        >
          <Icon name="refresh" size={13} />New since last visit{newCount > 0 ? ` (${fmtInt(newCount)})` : ""}
        </button>
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
        <div className="filter filter-author">
          <span>Creator</span>
          <CreatorPicker
            people={peopleApi.data || []}
            accounts={authors}
            platform={person ? "" : platform}
            value={person ? { person } : author ? { platform, id: author } : null}
            onChange={onCreator}
          />
        </div>
        <label className="filter filter-tags">
          <span>Tags</span>
          <select className="sort-select" value="" onChange={e => onTagFilter(e.target.value)}>
            <option value="">{untagged ? "Untagged" : tagFilter.length ? "Add another…" : "Any"}</option>
            {!untagged && <option value="__untagged">Untagged only</option>}
            {allTags.filter(t => !tagFilter.some(f => sameTag(f, t.name))).map(t => (
              <option key={t.name} value={t.name}>{t.name} ({t.count})</option>
            ))}
          </select>
        </label>
        {(tagFilter.length > 0 || untagged) && (
          <ul className="tag-chips filter-tag-chips" aria-label="Tag filters">
            {untagged && (
              <li className="tag-chip"><span>untagged</span>
                <button type="button" className="tag-chip-x" onClick={() => setParam({ untagged: "" })} aria-label="Remove the untagged filter">
                  <Icon name="close" size={10} />
                </button>
              </li>
            )}
            {tagFilter.map(t => (
              <li key={t} className="tag-chip"><span><Icon name="tag" size={11} />{t}</span>
                <button type="button" className="tag-chip-x" onClick={() => setParam({ tag: tagFilter.filter(x => x !== t) })} aria-label={`Remove the ${t} filter`}>
                  <Icon name="close" size={10} />
                </button>
              </li>
            ))}
          </ul>
        )}
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
        {person && (
          <Link className="btn-secondary review-link" to={personPath(person)} title="Accounts, handles and notes of this person">
            <Icon name="users" size={14} />Person
          </Link>
        )}
        {newOnly && !author && !person && (
          <Link className="btn-secondary review-link" to="/review?new=1" title="Keep or trash the new posts, one by one">
            <Icon name="review" size={14} />Review new posts
          </Link>
        )}
        {(author || person) && (
          <Link
            className="btn-secondary review-link"
            to={`/review?${new URLSearchParams({ ...(person ? { person } : { ...(platform ? { platform } : {}), author }), ...(newOnly ? { new: "1" } : {}) })}`}
            title={`Keep or trash this ${person ? "person" : "creator"}'s unreviewed posts, one by one`}
          >
            <Icon name="review" size={14} />Review this {person ? "person" : "creator"}
          </Link>
        )}
        {posts.length > 0 && (
          <button
            type="button"
            className={`btn-secondary select-toggle${sel.active ? " is-on" : ""}`}
            onClick={() => (sel.active ? sel.exit() : sel.enter())}
            aria-pressed={sel.active}
            title={sel.active ? "Leave select mode (Esc)" : "Select posts to tag, keep or delete"}
          >
            <Icon name={sel.active ? "close" : "check"} size={14} />
            {sel.active ? "Done" : "Select"}
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
        newOnly && !q && !platform && !kind && !author && !person && !review && !tagFilter.length && !untagged ? (
          <div className="feed-empty">
            <span className="feed-empty-mark"><Icon name="check" size={30} /></span>
            <h3>Nothing new</h3>
            <p>No post has been indexed since you last marked everything seen. A sync or a rescan that finds new posts brings them here.</p>
            <button type="button" className="btn-secondary" onClick={() => setParam({ new: "" })}>Show all posts</button>
          </div>
        ) : anyFilter ? (
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
                selectMode={sel.active}
                selected={sel.isSelected(p.id)}
                onToggle={sel.toggle}
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
          {sel.active && (
            <SelectionBar selection={sel} loaded={posts.length}>
              {selectedCount > 0 && (
                <span className="select-size mono" title="Media files of the selected posts">{fmtBytes(selectedBytes)}</span>
              )}
              <button type="button" className="btn-secondary" onClick={() => setTagging(true)} disabled={!selectedCount}>
                <Icon name="tag" size={14} />Tag…
              </button>
              <button type="button" className="btn-secondary" onClick={() => setCollecting(true)} disabled={!selectedCount}>
                <Icon name="bookmark" size={14} />Collection…
              </button>
              <button type="button" className="btn-keep" onClick={runKeep} disabled={!selectedCount || keeping}>
                <Icon name="check" size={14} />{keeping ? "Keeping…" : "Keep"}
              </button>
              <button type="button" className="btn-danger" onClick={() => { setDlgError(null); setConfirmDel(true); }} disabled={!selectedCount}>
                <Icon name="trash" size={14} />Delete…
              </button>
            </SelectionBar>
          )}
          {current && !hasMore && posts.length > PAGE && (
            <div className="feed-end">end of feed · {posts.length.toLocaleString()} posts</div>
          )}
        </>
      )}

      {tagging && (
        <BulkTagDialog posts={selectedPosts} tags={allTags} onApplied={onTagged} onCancel={() => setTagging(false)} />
      )}

      {collecting && (
        <CollectionDialog posts={selectedPosts.map(p => p.id)} onClose={() => setCollecting(false)} />
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
          ({selectedPosts.reduce((n, p) => n + (p.media_count || 0), 0)} media, {fmtBytes(selectedBytes)}) to the trash?
          You can empty the trash from Settings.
        </p>
      </ConfirmDialog>
    </div>
  );
}
