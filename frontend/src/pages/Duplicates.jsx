import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { dismissDuplicate, getDismissedDuplicates, getDuplicates, getDuplicatesStatus, resolveDuplicates, undismissDuplicate } from "../lib/api";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { useSelection } from "../lib/useSelection";
import { focusLost, restoreFocus } from "../lib/layout";
import { distinctTail, fmtBytes, fmtFullDate, fmtInt, fmtShortDate, plural, postPath } from "../lib/fmt";
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

/* What a group's resolve does, member by member: "keep" it, trash just its
   "picture" (the item that makes it a duplicate; the rest of the post
   stays), or trash the whole "post" (every item of it, also those no other
   member has). The plan holds what was picked; modes() says what it comes
   to, as a picture only goes when a member that stays holds it too. */
const fileKey = i => `${i.size}:${i.hash}`;

// The items "just the picture" trashes: in a similar group the one that
// matched, while another member is kept whole to show it; in a content
// group those a kept member holds too (by hash). None when only the whole
// member can go: a copies group holds one post twice, a single item takes
// its post along anyway, and a carousel shared item for item is all match.
function pictureItems(g, m, plan) {
  if (g.kind === "copies" || m.type !== "post" || m.items.length < 2) return [];
  const kept = g.members.filter(x => x.id !== m.id && (plan[x.id] || "keep") === "keep");
  let idxs;
  if (g.kind === "similar") idxs = kept.length && m.match != null ? [m.match] : [];
  else {
    const held = new Set(kept.flatMap(x => x.items.filter(i => i.hash).map(fileKey)));
    idxs = m.items.filter(i => i.hash && held.has(fileKey(i))).map(i => i.idx);
  }
  return idxs.length < m.items.length ? idxs : [];
}

// Today's default: keep the suggested member, and of each other one trash
// just the picture where it can, else all of it.
function defaultPlan(g) {
  const base = { [g.suggested]: "keep" };
  return Object.fromEntries(g.members.map(m => [m.id, m.id === g.suggested ? "keep"
    : pictureItems(g, m, base).length ? "picture" : "post"]));
}

// The trash mode a member's checkbox turns on: its picture when it can.
const trashMode = (g, m, plan) => (pictureItems(g, m, plan).length ? "picture" : "post");

// { [member id]: { mode, items, bytes } }: what the plan comes to.
function modes(g, plan) {
  return Object.fromEntries(g.members.map(m => {
    const mode = plan[m.id] || "keep";
    if (mode === "post") return [m.id, { mode, items: m.items.map(i => i.idx), bytes: m.bytes }];
    const items = mode === "picture" ? pictureItems(g, m, plan) : [];
    if (!items.length) return [m.id, { mode: "keep", items, bytes: 0 }];
    return [m.id, { mode, items, bytes: m.items.reduce((n, i) => n + (items.includes(i.idx) ? i.size || 0 : 0), 0) }];
  }));
}

// How many items found nowhere else the plan loses. Only a member trashed
// whole can lose any: a picture trashed alone is held (or, in a similar
// group, looked like) by a member that stays. Similar groups say per item
// what nothing else resembles. Otherwise an item of a member trashed whole
// is lost unless a member that stays holds the same file (by hash): in a
// copies group that is the post's version of what a kept copy lacks or
// holds differently, or what a trashed copy holds that the rest lack.
function lostItems(g, md) {
  const whole = id => md[id]?.mode === "post";
  if (g.kind === "similar") return g.differs.filter(d => whole(d.member)).length;
  const held = new Set(g.members.filter(m => !whole(m.id))
    .flatMap(m => m.items.filter(i => i.hash && !md[m.id].items.includes(i.idx)).map(fileKey)));
  return g.members.filter(m => whole(m.id))
    .reduce((n, m) => n + m.items.filter(i => !i.hash || !held.has(fileKey(i))).length, 0);
}

// "2 pictures + 1 post": what a plan trashes, in words.
function planWords(g, md) {
  const items = g.members.reduce((n, m) => n + (md[m.id].mode === "picture" ? md[m.id].items.length : 0), 0);
  const whole = g.members.filter(m => md[m.id].mode === "post").length;
  return [
    items > 0 && plural(items, g.kind === "similar" ? "picture" : "item"),
    whole > 0 && (g.kind === "copies" ? plural(whole, "copy", "copies") : plural(whole, "post")),
  ].filter(Boolean).join(" + ");
}

const matchOf = m => m.items.find(i => i.idx === m.match) || m.items[0];
const resolution = i => (i?.width && i?.height ? `${i.width}×${i.height}` : null);

function Member({ m, group, md, picture, onTrash, onMode, onView, disabled }) {
  const [broken, setBroken] = useState(false);
  const { mode, items } = md;
  const unit = m.type === "copy" ? "copy" : "post";
  const suggested = group.suggested === m.id;
  const diff = differsText(group, m.id);
  const who = m.post?.author?.handle ? `@${m.post.author.handle}` : null;
  // Different posts: who posted it and when tells the original from a repost.
  const posts = group.kind !== "copies";
  const match = posts ? matchOf(m) : null;
  const when = posts ? `posted ${fmtShortDate(m.posted_at)}` : `saved ${fmtShortDate(m.saved_at)}`;
  const files = plural(m.files, "file");
  const pictureWord = group.kind === "similar" ? "picture" : items.length === 1 ? "item" : `${items.length} items`;
  return (
    <label className={`big-file dup-member is-${mode}`}>
      <span
        className={`big-file-link${onView ? " is-viewable" : ""}`}
        onClick={onView ? e => { if (e.target.type === "checkbox") return; e.preventDefault(); onView(); } : undefined}
        title={onView ? "View full size and compare" : undefined}
      >
        {m.thumb_url && !broken ? (
          <img src={m.thumb_url} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          <span className="big-file-ph"><Icon name={m.items[0]?.kind === "video" ? "play" : "image"} size={26} /></span>
        )}
        <input
          type="checkbox"
          className="dup-trash-check"
          checked={mode !== "keep"}
          onChange={e => onTrash(m.id, e.target.checked)}
          disabled={disabled}
          aria-label={`Trash ${m.folder}`}
          title={mode === "keep" ? "Kept: tick to trash it" : "Marked for the trash: untick to keep it"}
        />
        <span className="trash-badges">
          {mode === "keep" ? (
            <span className="trash-badge dup-badge-keep">keep</span>
          ) : mode === "picture" ? (
            <span className="trash-badge dup-badge-trash" title={`Only this ${group.kind === "similar" ? "picture" : "item"} goes: the rest of the post stays`}>
              trash {pictureWord}
            </span>
          ) : (
            <span className="trash-badge dup-badge-trash" title={`The whole ${unit} goes, every item of it`}>
              trash {unit} · {files}
            </span>
          )}
          {suggested && <span className="trash-badge dup-badge-suggested" title="The suggested copy to keep">suggested</span>}
          {m.kept && <span className="trash-badge dup-badge-kept" title="Marked kept in Review">kept</span>}
          {m.type === "copy" && <span className="trash-badge" title="Not in the feed: an extra copy of the post">copy</span>}
        </span>
        <span className="big-file-size mono">{fmtBytes(m.bytes)}</span>
      </span>
      <span className="dup-member-foot">
        <span className="dup-folder" title={m.meta_path}>{distinctTail(m.folder, group.members.map(x => x.folder))}/</span>
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
        {mode !== "keep" && picture > 0 && (
          <span className="dup-mode" role="group" aria-label={`What of ${m.folder} to trash`}>
            <button type="button" className={mode === "picture" ? "is-on" : ""} aria-pressed={mode === "picture"}
              onClick={() => onMode(m.id, "picture")} disabled={disabled}
              title={`Trash only the ${group.kind === "similar" ? "matching picture" : "items another kept post holds"}: the rest of the post stays`}>
              {group.kind === "similar" ? "Picture" : picture > 1 ? "Shared items" : "Shared item"}
            </button>
            <button type="button" className={mode === "post" ? "is-on" : ""} aria-pressed={mode === "post"}
              onClick={() => onMode(m.id, "post")} disabled={disabled}
              title={`Trash every item of the post (${files})`}>
              Whole post
            </button>
          </span>
        )}
      </span>
    </label>
  );
}

function Group({ g, index, busy, selectMode, selectable: canSelect, selected, onToggle, onResolve, onDismiss }) {
  const [viewing, setViewing] = useState(null);       // index of the member open in the compare view
  const [plan, setPlan] = useState(() => defaultPlan(g));
  const [seen, setSeen] = useState(`${g.id}:${g.suggested}`);
  if (`${g.id}:${g.suggested}` !== seen) {           // a reload changed the members or the suggestion: start over
    setSeen(`${g.id}:${g.suggested}`);
    setPlan(defaultPlan(g));
  }
  // Select mode resolves with the suggested member: show that, not the plan.
  const md = modes(g, selectMode ? Object.fromEntries(g.members.map(m => [m.id, m.id === g.suggested ? "keep" : "post"])) : plan);
  const frees = g.members.reduce((n, m) => n + md[m.id].bytes, 0);
  const lost = lostItems(g, md);
  const words = planWords(g, md);
  const whole = g.members.filter(m => md[m.id].mode === "post").length;
  const parts = g.members.filter(m => md[m.id].mode === "picture").length;
  const allGo = whole === g.members.length;
  const selectable = selectMode && canSelect;

  const setMode = (id, mode) => setPlan(p => ({ ...p, [id]: mode }));
  const setTrash = (id, on) => setPlan(p => ({ ...p, [id]: on ? trashMode(g, g.members.find(m => m.id === id), p) : "keep" }));
  // Compare's "Keep only this one": it stays, every other member goes.
  const keepOnly = id => setPlan(() => {
    const base = { [id]: "keep" };
    return Object.fromEntries(g.members.map(m => [m.id, m.id === id ? "keep" : trashMode(g, m, base)]));
  });
  const why = g.pending ? "Wait until every file is hashed"
    : !words ? "Tick the members to trash first"
    : allGo ? "Keep one member at least: untick one"
    : `Move ${words} to the trash. A post trashed whole goes with every item of it${lost ? `, ${plural(lost, "item")} found nowhere else among them` : ""}`;
  const choice = { group: g.id, trash: g.members.filter(m => md[m.id].mode !== "keep")
    .map(m => (md[m.id].mode === "post" ? m.id : { member: m.id, items: md[m.id].items })) };

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
              title="Not a duplicate: hide this group (restore it from Dismissed below)">
              Not a duplicate
            </button>
            <button
              type="button"
              className="btn-danger-soft"
              onClick={() => onResolve(g, choice, { words, bytes: frees, lost, whole, parts })}
              disabled={busy || g.pending || !words || allGo}
              title={why}
            >
              <Icon name="trash" size={14} />Trash selected · {fmtBytes(frees)}
            </button>
          </>
        )}
      </div>
      {!selectMode && (
        <p className={`dup-plan${lost ? " is-lossy" : ""}`}>
          {!words ? "Nothing marked for the trash: tick a member to trash it."
            : allGo ? "Every member is marked for the trash: keep one at least."
            : <>Trashes {words} · {fmtBytes(frees)} · {lost
              ? <><Icon name="warn" size={13} />loses {plural(lost, "item")} found nowhere else</>
              : "loses nothing found nowhere else"}</>}
        </p>
      )}
      <div className="dup-members" onClick={selectable ? toggle : undefined}>
        {g.members.map((m, i) => (
          <Member key={m.id} m={m} group={g} md={md[m.id]} picture={pictureItems(g, m, plan).length}
            onTrash={setTrash} onMode={setMode} disabled={busy || selectMode}
            onView={g.kind === "similar" && !selectMode ? () => setViewing(i) : undefined} />
        ))}
      </div>
      {viewing != null && g.members[viewing] && (
        <Compare
          g={g}
          at={viewing}
          md={md}
          onMove={step => setViewing(v => (v + step + g.members.length) % g.members.length)}
          onTrash={setTrash}
          onKeepOnly={keepOnly}
          onClose={() => setViewing(null)}
        />
      )}
    </section>
  );
}

function DismissedThumb({ m }) {
  const [broken, setBroken] = useState(false);
  return m.thumb_url && !broken ? (
    <img src={m.thumb_url} alt="" loading="lazy" onError={() => setBroken(true)} />
  ) : (
    <span className="big-file-ph"><Icon name="image" size={16} /></span>
  );
}

// Who a dismissed member is: the post's author, else its folder; "gone" once
// it left the index (trashed, deleted).
function dismissedName(m) {
  if (m.post?.author?.handle) return `@${m.post.author.handle}`;
  const folder = m.path ? lastPart(m.path.slice(0, m.path.lastIndexOf("/"))) : null;
  if (folder) return `${folder}/`;
  return m.id || "gone";
}

/* "Not a duplicate" is undone here: every dismissal of this kind, newest
   first, each with Restore. Collapsed unless the URL asks for it
   (/duplicates#dismissed, from Unmatched). */
function Dismissed({ items, busy, onRestore, startOpen }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(startOpen);
  useEffect(() => {
    if (startOpen) ref.current?.scrollIntoView({ block: "start" });
  }, [startOpen]);
  return (
    <details ref={ref} id="dismissed" className="card dup-dismissed" open={open}
      onToggle={e => setOpen(e.currentTarget.open)}>
      <summary>Dismissed ({fmtInt(items.length)})</summary>
      {items.length === 0 ? (
        <p className="dim">Nothing marked not a duplicate.</p>
      ) : (
        <ul className="dup-dismissed-list">
          {items.map(d => (
            <li key={d.id} className="dup-dismissed-row">
              <span className="dup-dismissed-thumbs" aria-hidden="true">
                {d.members.slice(0, 4).map((m, i) => <span key={i} className="dup-dismissed-thumb"><DismissedThumb m={m} /></span>)}
              </span>
              <span className="dup-dismissed-who" title={d.members.map(m => m.path || m.id).filter(Boolean).join("\n")}>
                {d.members.map(dismissedName).join(" · ")}
              </span>
              <span className="dup-member-sub" title={`Marked not a duplicate ${fmtFullDate(d.at)}`}>{fmtShortDate(d.at)}</span>
              <button type="button" className="btn-secondary" onClick={() => onRestore(d.id)} disabled={busy}
                aria-label={`Restore ${d.members.map(dismissedName).join(", ")}`}
                title="It is a duplicate after all: list this group again">
                <Icon name="undo" size={14} />Restore
              </button>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

/* Full-size side-by-side for a similar group: one member at a time, ←/→
   between them, Esc closes. Keep, Trash and Keep only this one only change
   the group's plan; trashing still goes through the group's button and its
   confirmation. */
function Compare({ g, at, md, onMove, onTrash, onKeepOnly, onClose }) {
  const boxRef = useRef(null);
  const [broken, setBroken] = useState(null);   // the url that failed to load (file gone, unreadable)
  const m = g.members[at];
  const item = matchOf(m);
  const who = m.post?.author?.handle ? `@${m.post.author.handle}` : m.folder;

  useEffect(() => {
    const prev = document.activeElement;
    boxRef.current?.focus();
    return () => restoreFocus(prev);
  }, []);

  // Keep or Trash this one goes once clicked, and focus with it, onto <body>
  // (#136): back to the box, where the keys below are heard. Should it
  // get there some other way, they are heard on the document too.
  useLayoutEffect(() => { if (focusLost()) boxRef.current?.focus({ preventScroll: true }); });
  const keysRef = useRef(null);
  useEffect(() => { keysRef.current = onKeyDown; });
  useEffect(() => {
    const onKey = e => { if (focusLost()) keysRef.current?.(e); };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
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
          <span className="dup-folder" title={m.meta_path}>{distinctTail(m.folder, g.members.map(x => x.folder))}/</span>
          {m.post && <Link to={postPath(m.post)} className="text-link">Open post</Link>}
          <span className="dup-member-sub">← → to switch · Esc to close</span>
          <div className="page-head-spacer" />
          {md[m.id].mode === "keep" ? (
            <>
              <span className="dup-state is-identical"><Icon name="check" size={13} />keeping this one</span>
              <button type="button" className="btn-secondary" onClick={() => onTrash(m.id, true)}>Trash this one</button>
            </>
          ) : (
            <>
              <span className="dup-state is-trash"><Icon name="trash" size={13} />
                {md[m.id].mode === "picture" ? "trashing this picture" : "trashing the whole post"}
              </span>
              <button type="button" className="btn-secondary" onClick={() => onTrash(m.id, false)}>Keep this one</button>
            </>
          )}
          {g.members.some(x => x.id !== m.id && md[x.id].mode === "keep") || md[m.id].mode !== "keep" ? (
            <button type="button" className="btn-secondary" onClick={() => onKeepOnly(m.id)}
              title="Keep this one and mark every other member for the trash">Keep only this one</button>
          ) : null}
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
  const location = useLocation();
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
  // the groups when a pass ends, and when it goes on to its next phase:
  // copies and same-content groups are settled once the byte hashes are
  // (partial, then full), not at the end of the fingerprints after them,
  // which kept Trash selected disabled for the rest of a long pass (#149).
  const hashing = !!status?.running;
  useEffect(() => {
    let alive = true;
    let was = null;
    function poll() {
      getDuplicatesStatus().then(s => {
        if (!alive) return;
        setStatus(s);
        if (was?.running && (!s.running || s.phase !== was.phase)) reload();
        was = s;
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
  const [confirm,  setConfirm]  = useState(null);    // { choices, count, bytes, lost, one… } awaiting confirmation
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
      const moved = (r.posts?.length ?? 0) + (r.copies?.length ?? 0) + (r.media?.length ?? 0);
      toast(`Moved ${plural(moved, "duplicate")} to the trash (${fmtBytes(r.bytes ?? 0)}). Restore from the Trash page.`);
      if (r.skipped?.length) toast(`${plural(r.skipped.length, "group")} skipped: ${r.skipped[0].error}`, "err");
      reload();
    } catch (e) {
      if (confirm) setDlgError(e.message); else toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  // One click when nothing found nowhere else goes; a plan that loses such
  // items (only a post trashed whole can) asks first. A similar group always
  // asks: its posts only look alike.
  function resolveOne(g, choice, { words, bytes, lost, whole, parts }) {
    const choices = [choice];
    if (g.kind !== "similar" && lost === 0) { run(choices); return; }
    setDlgError(null);
    setConfirm({ choices, count: 1, bytes, lost, one: true, similar: g.kind === "similar", words, whole, parts,
      unit: g.kind === "copies" ? "copy" : "post" });
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
      // The dismissal keeps the group's id: Undo sends it straight back.
      toast("Marked not a duplicate.", "ok", { label: "Undo", onClick: () => restore(g.id) });
      reload();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function restore(id) {
    setBusy(true);
    try {
      const r = await undismissDuplicate(id);
      if (!r?.ok) { toast(r?.error || "Could not restore.", "err"); reload(); return; }
      toast("Restored: the group is listed again.");
      reload();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  // What was marked not a duplicate, for this kind: refetched with the groups.
  const [dismissedList, setDismissedList] = useState(null);   // { kind, items }
  useEffect(() => {
    let alive = true;
    getDismissedDuplicates(kind).then(r => { if (alive) setDismissedList({ kind, items: r.dismissed }); }, () => {});
    return () => { alive = false; };
  }, [kind, refreshKey, tick]);
  const dismissedItems = dismissedList?.kind === kind ? dismissedList.items : null;
  const askedDismissed = location.hash === "#dismissed";

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
        {k.lede} Tick what to trash in each group: a whole post goes with every item of it, a picture alone leaves the rest of its post. It all moves to the trash, so you can restore it from{" "}
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
            {result.dismissed > 0 && ` ${plural(result.dismissed, "group")} marked not a duplicate: see Dismissed below.`}
          </div>
        </div>
      ) : (
        <>
          {current && (result.identical > 0 || result.pending > 0) && !sel.active && (
            <div className="dup-summary">
              {result.identical > 0 && <>
                {plural(result.identical, "identical group")} would free {fmtBytes(result.identical_frees)}.
                {/* None of them listed yet: they are reposts left out, or further down (QA pass 3). */}
                {identical.length > 0
                  ? <> Use <strong>Select</strong> to resolve them with the suggested copy in one go</>
                  : reposts > 0 && !withReposts ? " Select leaves reposts out" : " Load more to reach them"}
                {reposts > 0 && !withReposts ? ` (${plural(reposts, "repost")} left out: turn on Include reposts)` : ""}.
              </>}
              {result.pending > 0 && <>
                {result.identical > 0 && " "}
                {result.pending === 1 ? "1 group is" : `${fmtInt(result.pending)} groups are`} still being hashed: Select takes {result.pending === 1 ? "it" : "them"} once {result.pending === 1 ? "it is" : "they are"}.
              </>}
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

      {current && dismissedItems && (dismissedItems.length > 0 || askedDismissed) && (
        <Dismissed items={dismissedItems} busy={busy} onRestore={restore} startOpen={askedDismissed} />
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
        title={confirm?.one ? `Trash ${confirm.words}?` : `Resolve ${plural(confirm?.count ?? 0, "group")}?`}
        confirmLabel={`Move ${fmtBytes(confirm?.bytes ?? 0)} to the trash`}
        onConfirm={() => run(confirm.choices)}
        onCancel={() => setConfirm(null)}
      >
        {confirm?.one ? (
          <>
            {confirm.similar && (
              <p>
                These posts only <strong>look</strong> alike: their files differ, and each has its own caption and
                author.
              </p>
            )}
            <p>
              {confirm.whole > 0 && <>
                A {confirm.unit} trashed whole goes with <strong>every item</strong> of it, not only the one that
                matched{confirm.lost > 0
                  ? <>: that is <strong className="dup-warn">{plural(confirm.lost, "item")} found nowhere else</strong>{confirm.similar ? " (nothing in the posts you keep resembles them)" : ""}.</>
                  : "; the members you keep hold all of it."}{" "}
              </>}
              {confirm.parts > 0 && <>
                A {confirm.similar ? "picture" : "shared item"} trashed on its own leaves the rest of its post in place.{" "}
              </>}
              Everything goes to the trash ({fmtBytes(confirm.bytes)}), where you can restore it.
            </p>
          </>
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
