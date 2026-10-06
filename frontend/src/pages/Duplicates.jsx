import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useSearchParams } from "react-router-dom";
import { dismissDuplicate, getDuplicates, getDuplicatesStatus, resolveDuplicates } from "../lib/api";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { useSelection } from "../lib/useSelection";
import { fmtBytes, fmtFullDate, fmtInt, fmtShortDate, plural, postPath } from "../lib/fmt";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";
import SelectionBar from "../components/SelectionBar";
import PageHeader from "../components/PageHeader";

const PAGE = 50;
const MAX_PAGE = 500;      // the backend's limit; a reload refetches what was loaded
const STATUS_POLL_MS = 2000;
const THRESHOLD_MAX = 10;  // the backend's; see docs/API.md "Duplicates"
const SLIDER_DELAY_MS = 300;

const KINDS = [
  { value: "copies",  label: "Same post, two folders",
    lede: "The same post downloaded again into another folder (a typo'd profile folder, a second download). Only the first copy is in the feed; the others are listed on Unmatched as \"duplicate of\"." },
  { value: "content", label: "Same file, different posts",
    lede: "Different posts (a repost, the same picture in two carousels) holding byte-for-byte the same file." },
  { value: "similar", label: "Looks the same",
    lede: "Different posts with a picture that looks the same though the files differ: a resized or recompressed repost, a re-upload. Nothing here is certain, so each group is resolved by hand, never in bulk." },
];

const PHASES = { partial: "Hashing files", full: "Hashing whole files", dhash: "Fingerprinting pictures", probe: "Measuring videos" };

const lastPart = path => path.split("/").filter(Boolean).pop() || path;

// What differs for one member, in words: "no item 3 · item 2 other bytes".
const REASONS = {
  missing:     idx => `no item ${idx}`,
  extra:       idx => `extra item ${idx}`,
  size:        idx => `item ${idx} other size`,
  content:     idx => `item ${idx} other bytes`,
  "only here": idx => `item ${idx} only here`,
};
function differsText(g, memberId) {
  return g.differs.filter(d => d.member === memberId).map(d => (REASONS[d.reason] || (i => `item ${i} ${d.reason}`))(d.idx)).join(" · ");
}

// How many items trashing every member but `keep` would lose. In a content
// group, any file of a trashed member the kept one does not hold (by hash).
// In a copies group differences are against the post: a trashed copy loses
// what it has that the post lacks or holds differently, and keeping a copy
// loses the post's version of whatever that copy lacks or holds differently.
function lostItems(g, keep) {
  if (g.kind === "similar") return g.differs.filter(d => d.member !== keep).length;
  if (g.kind === "content") {
    const held = new Set(g.members.find(m => m.id === keep)?.items.filter(i => i.hash).map(i => `${i.size}:${i.hash}`));
    return g.members.filter(m => m.id !== keep)
      .reduce((n, m) => n + m.items.filter(i => !i.hash || !held.has(`${i.size}:${i.hash}`)).length, 0);
  }
  return g.differs.filter(d => (d.member === keep ? d.reason !== "extra" : d.reason !== "missing")).length;
}

const matchOf = m => m.items.find(i => i.idx === m.match) || m.items[0];
const resolution = i => (i?.width && i?.height ? `${i.width}×${i.height}` : null);

function Member({ m, group, chosen, onChoose, onView, disabled }) {
  const [broken, setBroken] = useState(false);
  const suggested = group.suggested === m.id;
  const diff = differsText(group, m.id);
  const who = m.post?.author?.handle ? `@${m.post.author.handle}` : null;
  // Different posts: who posted it and when tells the original from a repost.
  const posts = group.kind !== "copies";
  const match = posts ? matchOf(m) : null;
  const when = posts ? `posted ${fmtShortDate(m.posted_at)}` : `saved ${fmtShortDate(m.saved_at)}`;
  const files = plural(m.files, "file");
  return (
    <label className={`big-file dup-member${chosen ? " is-chosen" : ""}`}>
      <span
        className={`big-file-link${onView ? " is-viewable" : ""}`}
        onClick={onView ? e => { e.preventDefault(); onView(); } : undefined}
        title={onView ? "View full size and compare" : undefined}
      >
        {m.thumb_url && !broken ? (
          <img src={m.thumb_url} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          <span className="big-file-ph"><Icon name={m.items[0]?.kind === "video" ? "play" : "image"} size={26} /></span>
        )}
        <input
          type="radio"
          className="dup-radio"
          name={`keep-${group.id}`}
          checked={chosen}
          onChange={() => onChoose(m.id)}
          disabled={disabled}
          aria-label={`Keep ${m.folder}`}
        />
        <span className="trash-badges">
          {suggested && <span className="trash-badge dup-badge-suggested" title="The suggested copy to keep">suggested</span>}
          {m.kept && <span className="trash-badge dup-badge-kept" title="Marked kept in Review">kept</span>}
          {m.type === "copy" && <span className="trash-badge" title="Not in the feed: an extra copy of the post">copy</span>}
        </span>
        <span className="big-file-size mono">{fmtBytes(m.bytes)}</span>
      </span>
      <span className="dup-member-foot">
        <span className="dup-folder" title={m.meta_path}>{lastPart(m.folder)}/</span>
        <span className="dup-member-sub" title={[m.post && who, when, files].filter(Boolean).join(" · ")}>
          {m.post && who && <><Link to={postPath(m.post)} className="text-link" onClick={e => e.stopPropagation()}>{who}</Link> · </>}
          <span title={posts ? `Posted ${fmtFullDate(m.posted_at)} · saved ${fmtFullDate(m.saved_at)}` : `Saved ${fmtFullDate(m.saved_at)}`}>{when}</span>
          {" · "}{files}
        </span>
        {match && (
          <span className="dup-member-sub mono" title="The picture that matched: its resolution and file size">
            {resolution(match) || "size unknown"} · {fmtBytes(match.size)}
          </span>
        )}
        {diff && <span className="dup-diff">{diff}</span>}
      </span>
    </label>
  );
}

function Group({ g, index, busy, selectMode, selectable: canSelect, selected, onToggle, onResolve, onDismiss }) {
  const [viewing, setViewing] = useState(null);       // index of the member open in the compare view
  const [keep, setKeep] = useState(g.suggested);
  const [seenSuggested, setSeenSuggested] = useState(g.suggested);
  if (g.suggested !== seenSuggested) {               // a reload changed the suggestion: follow it
    setSeenSuggested(g.suggested);
    setKeep(g.suggested);
  }
  const kept = g.members.find(m => m.id === keep) || g.members[0];
  const frees = g.members.reduce((n, m) => n + (m.id === kept.id ? 0 : m.bytes), 0);
  const others = g.members.length - 1;
  const selectable = selectMode && canSelect;

  function toggle(ev) {
    ev.stopPropagation();
    onToggle(ev.shiftKey);
  }

  return (
    <section
      className={`dup-group${selectable ? " is-selecting" : ""}${selected ? " is-selected" : ""}${selectMode && !canSelect ? " is-muted" : ""}`}
      style={{ animationDelay: `${Math.min(index % PAGE, 30) * 18}ms` }}
      aria-label={`Duplicate group ${index + 1}`}
    >
      <div className="dup-group-head">
        {selectable && (
          <button type="button" className="select-check dup-check" role="checkbox" aria-checked={selected}
            aria-label={`Select group ${index + 1}`} onClick={toggle}>
            {selected && <Icon name="check" size={14} />}
          </button>
        )}
        {g.kind === "similar" ? (
          <span className="dup-state is-similar" title="Perceptual fingerprints this many bits apart, of 64">
            looks the same · {g.distance === 0 ? "0 bits" : `≤ ${plural(g.distance, "bit")}`}
          </span>
        ) : g.identical ? (
          <span className="dup-state is-identical"><Icon name="check" size={13} />identical</span>
        ) : g.pending ? (
          <span className="dup-state is-pending">hashing…</span>
        ) : (
          <span className="dup-state is-differs" title="Trashing a member that has something the kept one lacks loses it"><Icon name="warn" size={13} />differs</span>
        )}
        {g.repost && (
          <span className="dup-state is-repost" title="Posted by different accounts: one is likely a repost. The earliest posted is suggested.">
            repost
          </span>
        )}
        <span className="dup-group-sub">
          {g.kind === "copies" ? plural(g.members.length, "copy", "copies") : plural(g.members.length, "post")} · {fmtBytes(g.bytes)}
        </span>
        <div className="page-head-spacer" />
        {!selectMode && (
          <>
            <button type="button" className="btn-ghost" onClick={() => onDismiss(g)} disabled={busy}
              title="Not a duplicate: hide this group for good">
              Not a duplicate
            </button>
            <button
              type="button"
              className="btn-danger-soft"
              onClick={() => onResolve(g, kept.id)}
              disabled={busy || g.pending}
              title={g.pending ? "Wait until every file is hashed" : `Keep ${kept.folder}, move the ${others === 1 ? "other one" : `other ${others}`} to the trash`}
            >
              <Icon name="trash" size={14} />Keep this, trash the rest · {fmtBytes(frees)}
            </button>
          </>
        )}
      </div>
      <div className="dup-members" onClick={selectable ? toggle : undefined}>
        {g.members.map((m, i) => (
          <Member key={m.id} m={m} group={g} chosen={m.id === kept.id} onChoose={setKeep} disabled={busy || selectMode}
            onView={g.kind === "similar" && !selectMode ? () => setViewing(i) : undefined} />
        ))}
      </div>
      {viewing != null && g.members[viewing] && (
        <Compare
          g={g}
          at={viewing}
          keep={kept.id}
          onMove={step => setViewing(v => (v + step + g.members.length) % g.members.length)}
          onKeep={id => setKeep(id)}
          onClose={() => setViewing(null)}
        />
      )}
    </section>
  );
}

/* Full-size side-by-side for a similar group: one member at a time, ←/→
   between them, Esc closes. "Keep this one" only picks the radio; trashing
   still goes through the group's button and its confirmation. */
function Compare({ g, at, keep, onMove, onKeep, onClose }) {
  const boxRef = useRef(null);
  const [broken, setBroken] = useState(null);   // the url that failed to load (file gone, unreadable)
  const m = g.members[at];
  const item = matchOf(m);
  const who = m.post?.author?.handle ? `@${m.post.author.handle}` : m.folder;

  useEffect(() => {
    const prev = document.activeElement;
    boxRef.current?.focus();
    return () => { if (prev && prev.focus && document.contains(prev)) prev.focus(); };
  }, []);

  function onKeyDown(e) {
    if (e.key === "Tab") {
      // Keep focus inside the dialog, like the other modals.
      const els = [...boxRef.current.querySelectorAll("button, a[href], video")];
      if (!els.length) return;
      const first = els[0], last = els[els.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === boxRef.current)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      onMove(e.key === "ArrowLeft" ? -1 : 1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    }
  }

  return createPortal(
    <div className="modal-overlay dup-compare" onKeyDown={onKeyDown}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={boxRef} className="dup-compare-box" role="dialog" aria-modal="true" tabIndex={-1}
        aria-label={`Compare: ${at + 1} of ${g.members.length}`}>
        <div className="dup-compare-head">
          <span className="mono">{at + 1} / {g.members.length}</span>
          <strong>{who}</strong>
          <span title={fmtFullDate(m.posted_at)}>posted {fmtShortDate(m.posted_at)}</span>
          <span className="mono">{resolution(item) || "size unknown"} · {fmtBytes(item?.size)}</span>
          {g.suggested === m.id && <span className="trash-badge dup-badge-suggested">suggested</span>}
          {m.kept && <span className="trash-badge dup-badge-kept">kept</span>}
          <div className="page-head-spacer" />
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button>
        </div>
        <div className="dup-compare-stage">
          <button type="button" className="dup-compare-nav" onClick={() => onMove(-1)} aria-label="Previous member">
            <Icon name="chevLeft" size={22} />
          </button>
          {!item?.url || broken === item.url ? (
            <span className="big-file-ph"><Icon name="image" size={40} /></span>
          ) : item.kind === "video" ? (
            <video key={item.url} src={item.url} controls muted loop autoPlay playsInline
              onError={() => setBroken(item.url)} />
          ) : (
            <img key={item.url} src={item.url} alt="" onError={() => setBroken(item.url)} />
          )}
          <button type="button" className="dup-compare-nav" onClick={() => onMove(1)} aria-label="Next member">
            <Icon name="chevRight" size={22} />
          </button>
        </div>
        <div className="dup-compare-foot">
          <span className="dup-folder" title={m.meta_path}>{lastPart(m.folder)}/</span>
          {m.post && <Link to={postPath(m.post)} className="text-link">Open post</Link>}
          <span className="dup-member-sub">← → to switch · Esc to close</span>
          <div className="page-head-spacer" />
          {keep === m.id ? (
            <span className="dup-state is-identical"><Icon name="check" size={13} />keeping this one</span>
          ) : (
            <button type="button" className="btn-secondary" onClick={() => onKeep(m.id)}>Keep this one</button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default function Duplicates() {
  const { refreshKey, running: scanning } = useScan();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const kind = KINDS.some(k => k.value === params.get("kind")) ? params.get("kind") : "copies";
  const similar = kind === "similar";
  // The similar threshold lives in the URL (?t=), the config's default until moved.
  const tParam = similar && /^\d+$/.test(params.get("t") || "") ? Math.min(THRESHOLD_MAX, +params.get("t")) : null;
  const view = similar ? `${kind}:${tParam ?? ""}` : kind;

  const [result,  setResult]  = useState(null);     // the last /api/duplicates answer, groups accumulated
  const [loaded,  setLoaded]  = useState(null);     // view (kind and threshold) the result belongs to
  const [error,   setError]   = useState(null);
  const [tick,    setTick]    = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [status,  setStatus]  = useState(null);

  const shown = loaded === view ? result?.groups.length ?? 0 : 0;
  useEffect(() => {
    let alive = true;
    const threshold = tParam ?? undefined;
    getDuplicates({ kind, threshold, offset: 0, limit: Math.min(MAX_PAGE, Math.max(PAGE, shown)) }).then(
      r => { if (alive) { setResult(r); setLoaded(view); setError(null); } },
      e => { if (alive) setError(e); },
    );
    return () => { alive = false; };
    // `shown` is read, not watched: loading more must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, refreshKey, tick]);

  // The slider moves at once; the list follows once it rests.
  const [slider, setSlider] = useState(null);
  useEffect(() => {
    if (slider == null || slider === tParam) return;
    const t = setTimeout(() => setParams({ kind: "similar", t: String(slider) }, { replace: true }), SLIDER_DELAY_MS);
    return () => clearTimeout(t);
  }, [slider, tParam, setParams]);
  const threshold = slider ?? tParam ?? (loaded === view ? result?.threshold : null) ?? 6;
  const reload = useCallback(() => setTick(t => t + 1), []);

  // Hashing runs in the background after every scan: follow it, and reload
  // the groups when a pass ends.
  const hashing = !!status?.running;
  useEffect(() => {
    let alive = true;
    let wasRunning = null;
    function poll() {
      getDuplicatesStatus().then(s => {
        if (!alive) return;
        setStatus(s);
        if (wasRunning && !s.running) reload();
        wasRunning = s.running;
      }, () => {});
    }
    poll();
    const t = setInterval(poll, STATUS_POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [reload]);

  // A page that comes back after the tab or threshold changed is dropped.
  const viewRef = useRef(view);
  useEffect(() => { viewRef.current = view; }, [view]);

  async function loadMore() {
    const asked = view;
    setLoadingMore(true);
    try {
      const r = await getDuplicates({ kind, threshold: tParam ?? undefined, offset: result.groups.length, limit: PAGE });
      if (viewRef.current !== asked) return;
      setResult(prev => ({ ...r, groups: [...prev.groups, ...r.groups.filter(g => !prev.groups.some(p => p.id === g.id))] }));
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setLoadingMore(false);
    }
  }

  const current = loaded === view;
  const groups = (current && result?.groups) || [];
  // Bulk select takes identical groups, but a repost only when asked for:
  // one click must never trash another account's post unseen.
  const [withReposts, setWithReposts] = useState(false);
  const bulkable = useCallback(g => g.identical && (!g.repost || withReposts), [withReposts]);
  const identical = groups.filter(bulkable);
  const reposts = groups.filter(g => g.identical && g.repost).length;

  /* ── Actions ─────────────────────────────────────────── */
  const [busy,     setBusy]     = useState(false);
  const [confirm,  setConfirm]  = useState(null);    // { choices, count, bytes, lost } awaiting confirmation
  const [dlgError, setDlgError] = useState(null);
  const [errors,   setErrors]   = useState(null);
  const sel = useSelection(identical, { resetKey: `${kind}:${withReposts}`, escapeBlocked: !!confirm });
  if (sel.active && current && identical.length === 0) sel.exit();
  const selBytes = sel.selectedItems.reduce((n, g) => n + g.frees, 0);

  function drop(ids) {
    const gone = new Set(ids);
    setResult(prev => prev && ({ ...prev, groups: prev.groups.filter(g => !gone.has(g.id)) }));
    sel.drop(ids);
  }

  async function run(choices) {
    setBusy(true);
    setDlgError(null);
    setErrors(null);
    try {
      const r = await resolveDuplicates(choices, similar ? result.threshold : undefined);
      if (!r?.ok) {
        const why = r?.skipped?.[0]?.error || r?.error || "Nothing could be moved to the trash.";
        if (confirm) setDlgError(why); else toast(why, "err");
        return;
      }
      setConfirm(null);
      drop(r.resolved || []);
      if (r.errors?.length) setErrors(r.errors);
      const moved = (r.posts?.length ?? 0) + (r.copies?.length ?? 0);
      toast(`Moved ${plural(moved, "duplicate")} to the trash (${fmtBytes(r.bytes ?? 0)}). Restore from the Trash page.`);
      if (r.skipped?.length) toast(`${plural(r.skipped.length, "group")} skipped: ${r.skipped[0].error}`, "err");
      reload();
    } catch (e) {
      if (confirm) setDlgError(e.message); else toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  // One click for an identical group; a group that differs asks first when
  // the members to trash hold something the kept one lacks. A similar group
  // always asks: its posts only look alike.
  function resolveOne(g, keep) {
    const lost = lostItems(g, keep);
    const bytes = g.members.reduce((n, m) => n + (m.id === keep ? 0 : m.bytes), 0);
    const choices = [{ group: g.id, keep }];
    if (g.kind !== "similar" && (g.identical || lost === 0)) { run(choices); return; }
    setDlgError(null);
    setConfirm({ choices, count: 1, bytes, lost, similar: g.kind === "similar", others: g.members.length - 1 });
  }

  function askBulk() {
    setDlgError(null);
    setConfirm({ choices: sel.selectedItems.map(g => ({ group: g.id, keep: g.suggested })), count: sel.count, bytes: selBytes, lost: 0 });
  }

  async function dismiss(g) {
    setBusy(true);
    try {
      const r = await dismissDuplicate(g.id, similar ? result.threshold : undefined);
      if (!r?.ok) { toast(r?.error || "Could not dismiss.", "err"); reload(); return; }
      drop([g.id]);
      toast("Marked not a duplicate. It will not show again.");
      reload();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  /* ── Render ──────────────────────────────────────────── */
  const k = KINDS.find(x => x.value === kind);
  const head = (
    <PageHeader
      title="Duplicates"
      sub={current && `${plural(result.total, "group")} · ${fmtBytes(result.frees)} to free`}
      actions={<>
        {reposts > 0 && (
          <button
            type="button"
            className={`btn-secondary select-toggle${withReposts ? " is-on" : ""}`}
            aria-pressed={withReposts}
            onClick={() => setWithReposts(v => !v)}
            title="Reposts are posts by different accounts. Include them in Select, where the earliest posted is kept."
          >
            {withReposts ? "Reposts included" : "Include reposts"}
          </button>
        )}
        {identical.length > 0 && !sel.active && (
          <button type="button" className="btn-secondary" onClick={sel.enter} title="Pick identical groups to resolve with the suggested copy">
            <Icon name="check" size={14} />Select
          </button>
        )}
        {sel.active && <button type="button" className="btn-secondary" onClick={sel.exit}>Done</button>}
      </>}
    />
  );

  const progress = status?.running ? (
    <span className="dup-status is-running">
      <Icon name="refresh" size={13} className="spin" />
      {status.paused ? "Hashing paused while the index is busy" : PHASES[status.phase] || "Hashing"}
      {" "}{fmtInt(status.done)} / {fmtInt(status.total)}…
    </span>
  ) : status ? (
    <span className="dup-status" title={status.finished_at ? `Last pass ${fmtFullDate(status.finished_at)}` : ""}>
      {similar ? `${plural(status.fingerprinted ?? 0, "picture")} fingerprinted` : `${plural(status.hashed, "file")} hashed`}
      {status.errors?.length ? ` · ${plural(status.errors.length, "file")} unreadable` : ""}
    </span>
  ) : null;

  return (
    <div className="trash-page dup-page">
      {head}
      <div className="feed-filters dup-kinds" role="tablist" aria-label="Kind of duplicate">
        {KINDS.map(x => (
          <button
            key={x.value}
            type="button"
            role="tab"
            aria-selected={kind === x.value}
            className={`btn-secondary select-toggle${kind === x.value ? " is-on" : ""}`}
            onClick={() => { setSlider(null); setParams(x.value === "copies" ? {} : { kind: x.value }, { replace: true }); }}
          >
            {x.label}
          </button>
        ))}
        <div className="page-head-spacer" />
        {progress}
      </div>
      {similar && (
        <div className="dup-threshold">
          <label htmlFor="dup-threshold">How alike</label>
          <span className="dup-threshold-end">tighter</span>
          <input
            id="dup-threshold"
            type="range"
            min={0}
            max={THRESHOLD_MAX}
            step={1}
            value={threshold}
            onChange={e => setSlider(+e.target.value)}
            aria-valuetext={`${threshold} of 64 bits may differ`}
          />
          <span className="dup-threshold-end">looser</span>
          <span className="dup-threshold-value mono" title="Fingerprint bits (of 64) that may differ">{plural(threshold, "bit")}</span>
          {loaded !== view && <span className="dup-status">updating…</span>}
        </div>
      )}
      <p className="page-lede">
        {k.lede} Keeping one moves the others to the trash, so you can restore them from{" "}
        <Link to="/trash" className="text-link">Trash</Link>.{" "}
        {similar
          ? "Pictures are fingerprinted in the background after each scan; click one to compare them full size."
          : "Files are compared by content, read in the background after each scan."}
      </p>
      {error && <div className="msg err" role="alert">Could not load duplicates: {error.message}{" "}
        <button type="button" className="btn-link" onClick={reload}>Retry</button></div>}

      <DeleteErrors errors={errors} onDismiss={() => setErrors(null)} />

      {!current ? (
        !error && <div className="card"><div className="empty">Loading…</div></div>
      ) : groups.length === 0 ? (
        <div className="card">
          <div className="empty">
            {hashing || scanning ? "Nothing found yet: files are still being compared."
              : similar ? `No pictures that look the same at ${plural(result.threshold, "bit")}.`
              : `No ${kind === "copies" ? "doubled downloads" : "shared files"} found.`}
            {result.dismissed > 0 && ` ${plural(result.dismissed, "group")} marked not a duplicate.`}
          </div>
        </div>
      ) : (
        <>
          {current && result.identical > 0 && !sel.active && (
            <div className="dup-summary">
              {plural(result.identical, "identical group")} would free {fmtBytes(result.identical_frees)}.
              {identical.length > 0
                ? <> Use <strong>Select</strong> to resolve them with the suggested copy in one go</>
                : " Select leaves reposts out"}
              {reposts > 0 && !withReposts ? ` (${plural(reposts, "repost")} left out: turn on Include reposts)` : ""}.
            </div>
          )}
          <div className="dup-groups" aria-busy={busy}>
            {groups.map((g, i) => {
              const at = identical.indexOf(g);
              return (
                <Group
                  key={g.id}
                  g={g}
                  index={i}
                  busy={busy}
                  selectMode={sel.active}
                  selectable={bulkable(g)}
                  selected={sel.isSelected(g.id)}
                  onToggle={shift => at >= 0 && sel.toggle(at, shift)}
                  onResolve={resolveOne}
                  onDismiss={dismiss}
                />
              );
            })}
          </div>
          {groups.length < result.total && (
            <div className="feed-more">
              <button type="button" className="btn-secondary" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Loading…" : `Load more (${fmtInt(result.total - groups.length)} left)`}
              </button>
            </div>
          )}
        </>
      )}

      {sel.active && (
        <SelectionBar selection={sel} loaded={identical.length}>
          {sel.count > 0 && <span className="select-size mono" title="What the selected groups would free">{fmtBytes(selBytes)}</span>}
          <button type="button" className="btn-danger" onClick={askBulk} disabled={!sel.count || busy}>
            <Icon name="trash" size={14} />Keep suggested, trash the rest…
          </button>
        </SelectionBar>
      )}

      <ConfirmDialog
        open={!!confirm}
        danger
        busy={busy}
        error={dlgError}
        title={confirm?.similar ? `Trash ${plural(confirm.others, "other post")}?`
          : confirm?.lost ? "Trash copies that differ?" : `Resolve ${plural(confirm?.count ?? 0, "group")}?`}
        confirmLabel={`Move ${fmtBytes(confirm?.bytes ?? 0)} to the trash`}
        onConfirm={() => run(confirm.choices)}
        onCancel={() => setConfirm(null)}
      >
        {confirm?.similar ? (
          <p>
            These posts only <strong>look</strong> alike: their files differ, and each has its own caption and
            author. {confirm.others === 1 ? "The other post goes" : `The ${fmtInt(confirm.others)} other posts go`} to
            the trash ({fmtBytes(confirm.bytes)})
            {confirm.lost > 0 && <>, with <strong>{plural(confirm.lost, "item")}</strong> that nothing in the kept post
            resembles</>}. You can restore them from the Trash page.
          </p>
        ) : confirm?.lost ? (
          <p>
            The members to trash hold <strong>{plural(confirm.lost, "item")}</strong> the one you keep does not
            have. They go to the trash with the rest ({fmtBytes(confirm.bytes)}), where you can still restore them.
          </p>
        ) : (
          <p>
            For each of the <strong>{plural(confirm?.count ?? 0, "identical group")}</strong>, the suggested copy is
            kept and the others move to the trash, <strong>{fmtBytes(confirm?.bytes ?? 0)}</strong> in all. Every
            group is checked again first; one whose files changed is skipped. You can restore from the Trash page.
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}
