import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { dismissDuplicate, getDuplicates, getDuplicatesStatus, resolveDuplicates } from "../lib/api";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { useSelection } from "../lib/useSelection";
import { fmtBytes, fmtFullDate, fmtInt, fmtShortDate, postPath } from "../lib/fmt";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";
import SelectionBar from "../components/SelectionBar";

const PAGE = 50;
const MAX_PAGE = 500;      // the backend's limit; a reload refetches what was loaded
const STATUS_POLL_MS = 2000;

const KINDS = [
  { value: "copies",  label: "Same post, two folders",
    lede: "The same post downloaded again into another folder (a typo'd profile folder, a second download). Only the first copy is in the feed; the others are listed on Unmatched as \"duplicate of\"." },
  { value: "content", label: "Same file, different posts",
    lede: "Different posts (a repost, the same picture in two carousels) holding byte-for-byte the same file." },
];

const plural = (n, word, many = `${word}s`) => `${fmtInt(n)} ${n === 1 ? word : many}`;
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
  if (g.kind === "content") {
    const held = new Set(g.members.find(m => m.id === keep)?.items.filter(i => i.hash).map(i => `${i.size}:${i.hash}`));
    return g.members.filter(m => m.id !== keep)
      .reduce((n, m) => n + m.items.filter(i => !i.hash || !held.has(`${i.size}:${i.hash}`)).length, 0);
  }
  return g.differs.filter(d => (d.member === keep ? d.reason !== "extra" : d.reason !== "missing")).length;
}

function Member({ m, group, chosen, onChoose, disabled }) {
  const [broken, setBroken] = useState(false);
  const suggested = group.suggested === m.id;
  const diff = differsText(group, m.id);
  const who = m.post?.author?.handle ? `@${m.post.author.handle}` : null;
  return (
    <label className={`big-file dup-member${chosen ? " is-chosen" : ""}`}>
      <span className="big-file-link">
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
        <span className="dup-member-sub">
          {plural(m.files, "file")}
          {" · "}<span title={`Saved ${fmtFullDate(m.saved_at)}`}>saved {fmtShortDate(m.saved_at)}</span>
          {m.post && who && <> · <Link to={postPath(m.post)} className="text-link" onClick={e => e.stopPropagation()}>{who}</Link></>}
        </span>
        {diff && <span className="dup-diff">{diff}</span>}
      </span>
    </label>
  );
}

function Group({ g, index, busy, selectMode, selected, onToggle, onResolve, onDismiss }) {
  const [keep, setKeep] = useState(g.suggested);
  const [seenSuggested, setSeenSuggested] = useState(g.suggested);
  if (g.suggested !== seenSuggested) {               // a reload changed the suggestion: follow it
    setSeenSuggested(g.suggested);
    setKeep(g.suggested);
  }
  const kept = g.members.find(m => m.id === keep) || g.members[0];
  const frees = g.members.reduce((n, m) => n + (m.id === kept.id ? 0 : m.bytes), 0);
  const others = g.members.length - 1;
  const selectable = selectMode && g.identical;

  function toggle(ev) {
    ev.stopPropagation();
    onToggle(ev.shiftKey);
  }

  return (
    <section
      className={`dup-group${selectable ? " is-selecting" : ""}${selected ? " is-selected" : ""}${selectMode && !g.identical ? " is-muted" : ""}`}
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
        {g.identical ? (
          <span className="dup-state is-identical"><Icon name="check" size={13} />identical</span>
        ) : g.pending ? (
          <span className="dup-state is-pending">hashing…</span>
        ) : (
          <span className="dup-state is-differs" title="Trashing a member that has something the kept one lacks loses it"><Icon name="warn" size={13} />differs</span>
        )}
        <span className="dup-group-sub">{plural(g.members.length, "copy", "copies")} · {fmtBytes(g.bytes)}</span>
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
        {g.members.map(m => (
          <Member key={m.id} m={m} group={g} chosen={m.id === kept.id} onChoose={setKeep} disabled={busy || selectMode} />
        ))}
      </div>
    </section>
  );
}

export default function Duplicates() {
  const { refreshKey, running: scanning } = useScan();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const kind = KINDS.some(k => k.value === params.get("kind")) ? params.get("kind") : "copies";

  const [result,  setResult]  = useState(null);     // the last /api/duplicates answer, groups accumulated
  const [loaded,  setLoaded]  = useState(null);     // kind the result belongs to
  const [error,   setError]   = useState(null);
  const [tick,    setTick]    = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [status,  setStatus]  = useState(null);

  const shown = loaded === kind ? result?.groups.length ?? 0 : 0;
  useEffect(() => {
    let alive = true;
    getDuplicates({ kind, offset: 0, limit: Math.min(MAX_PAGE, Math.max(PAGE, shown)) }).then(
      r => { if (alive) { setResult(r); setLoaded(kind); setError(null); } },
      e => { if (alive) setError(e); },
    );
    return () => { alive = false; };
    // `shown` is read, not watched: loading more must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, refreshKey, tick]);
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

  async function loadMore() {
    setLoadingMore(true);
    try {
      const r = await getDuplicates({ kind, offset: result.groups.length, limit: PAGE });
      setResult(prev => ({ ...r, groups: [...prev.groups, ...r.groups.filter(g => !prev.groups.some(p => p.id === g.id))] }));
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setLoadingMore(false);
    }
  }

  const current = loaded === kind;
  const groups = (current && result?.groups) || [];
  const identical = groups.filter(g => g.identical);

  /* ── Actions ─────────────────────────────────────────── */
  const [busy,     setBusy]     = useState(false);
  const [confirm,  setConfirm]  = useState(null);    // { choices, count, bytes, lost } awaiting confirmation
  const [dlgError, setDlgError] = useState(null);
  const [errors,   setErrors]   = useState(null);
  const sel = useSelection(identical, { resetKey: kind, escapeBlocked: !!confirm });
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
      const r = await resolveDuplicates(choices);
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
  // the members to trash hold something the kept one lacks.
  function resolveOne(g, keep) {
    const lost = lostItems(g, keep);
    const bytes = g.members.reduce((n, m) => n + (m.id === keep ? 0 : m.bytes), 0);
    const choices = [{ group: g.id, keep }];
    if (g.identical || lost === 0) { run(choices); return; }
    setDlgError(null);
    setConfirm({ choices, count: 1, bytes, lost });
  }

  function askBulk() {
    setDlgError(null);
    setConfirm({ choices: sel.selectedItems.map(g => ({ group: g.id, keep: g.suggested })), count: sel.count, bytes: selBytes, lost: 0 });
  }

  async function dismiss(g) {
    setBusy(true);
    try {
      const r = await dismissDuplicate(g.id);
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
    <div className="page-head">
      <h2 className="page-title">Duplicates</h2>
      {current && (
        <span className="page-count">
          {plural(result.total, "group")} · {fmtBytes(result.frees)} to free
        </span>
      )}
      <div className="page-head-spacer" />
      {identical.length > 0 && !sel.active && (
        <button type="button" className="btn-secondary" onClick={sel.enter} title="Pick identical groups to resolve with the suggested copy">
          <Icon name="check" size={14} />Select
        </button>
      )}
      {sel.active && <button type="button" className="btn-secondary" onClick={sel.exit}>Done</button>}
    </div>
  );

  const progress = status?.running ? (
    <span className="dup-status is-running">
      <Icon name="refresh" size={13} className="spin" />
      {status.paused ? "Hashing paused while the index is busy" : `Hashing ${status.phase === "full" ? "whole files" : "files"}`}
      {" "}{fmtInt(status.done)} / {fmtInt(status.total)}…
    </span>
  ) : status ? (
    <span className="dup-status" title={status.finished_at ? `Last pass ${fmtFullDate(status.finished_at)}` : ""}>
      {plural(status.hashed, "file")} hashed{status.errors?.length ? ` · ${plural(status.errors.length, "file")} unreadable` : ""}
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
            onClick={() => setParams(x.value === "copies" ? {} : { kind: x.value }, { replace: true })}
          >
            {x.label}
          </button>
        ))}
        <div className="page-head-spacer" />
        {progress}
      </div>
      <p className="page-lede">
        {k.lede} Keeping one moves the others to the trash, so you can restore them from{" "}
        <Link to="/trash" className="text-link">Trash</Link>. Files are compared by content, read in the background
        after each scan.
      </p>
      {error && <div className="msg err" role="alert">Could not load duplicates: {error.message}{" "}
        <button type="button" className="btn-link" onClick={reload}>Retry</button></div>}

      <DeleteErrors errors={errors} onDismiss={() => setErrors(null)} />

      {!current ? (
        !error && <div className="card"><div className="empty">Loading…</div></div>
      ) : groups.length === 0 ? (
        <div className="card">
          <div className="empty">
            {hashing || scanning ? "Nothing found yet: files are still being compared." : `No ${kind === "copies" ? "doubled downloads" : "shared files"} found.`}
            {result.dismissed > 0 && ` ${plural(result.dismissed, "group")} marked not a duplicate.`}
          </div>
        </div>
      ) : (
        <>
          {current && result.identical > 0 && !sel.active && (
            <div className="dup-summary">
              {plural(result.identical, "identical group")} would free {fmtBytes(result.identical_frees)}.
              {" "}Use <strong>Select</strong> to resolve them with the suggested copy in one go.
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
        title={confirm?.lost ? "Trash copies that differ?" : `Resolve ${plural(confirm?.count ?? 0, "group")}?`}
        confirmLabel={`Move ${fmtBytes(confirm?.bytes ?? 0)} to the trash`}
        onConfirm={() => run(confirm.choices)}
        onCancel={() => setConfirm(null)}
      >
        {confirm?.lost ? (
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
