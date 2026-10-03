import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getStorage, getConfig, getPeople, deleteItems } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { fmtBytes, fmtInt, platformLabel, authorFeedPath } from "../lib/fmt";
import Bars from "../components/Bars";
import StatsHero from "../components/StatsHero";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";
import PersonScopeHead from "../components/PersonScopeHead";
import { personPath } from "../lib/people";

// Sortable creator columns. Share is the size as a fraction of the whole, so
// it sorts like size.
const COLUMNS = [
  { key: "posts", label: "Posts" },
  { key: "media", label: "Media" },
  { key: "bytes", label: "Size" },
  { key: "share", label: "Share", by: "bytes" },
];

function pct(part, whole) {
  if (!whole) return "0%";
  const p = (part / whole) * 100;
  return `${p >= 10 || p === 0 ? p.toFixed(0) : p.toFixed(1)}%`;
}

function CreatorTable({ rows, total }) {
  const [sort, setSort] = useState({ key: "bytes", dir: "desc" });
  const sorted = useMemo(() => {
    const field = COLUMNS.find(c => c.key === sort.key)?.by || sort.key;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => sign * (a[field] - b[field]) || (a.handle || "").localeCompare(b.handle || ""));
  }, [rows, sort]);
  const max = Math.max(1, ...rows.map(r => r.bytes));

  function toggle(key) {
    setSort(s => (s.key === key ? { key, dir: s.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" }));
  }

  return (
    <div className="table-wrap">
      <table className="data-table storage-table">
        <thead>
          <tr>
            <th scope="col">Creator</th>
            {COLUMNS.map(c => (
              <th
                key={c.key}
                scope="col"
                className={c.key === "share" ? "" : "num"}
                aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
              >
                <button
                  type="button"
                  className={`th-sort${sort.key === c.key ? " is-on" : ""}`}
                  onClick={() => toggle(c.key)}
                  title={`Sort by ${c.label.toLowerCase()}`}
                >
                  {c.label}
                  <span className="th-sort-arrow" aria-hidden="true">
                    {sort.key === c.key ? (sort.dir === "asc" ? "↑" : "↓") : ""}
                  </span>
                </button>
              </th>
            ))}
            <th scope="col">Kept · unreviewed</th>
            <th scope="col" aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {sorted.map((a, i) => (
            <tr key={`${a.platform}:${a.id}`} style={{ animationDelay: `${Math.min(i, 30) * 20}ms` }}>
              <td>
                <Link to={authorFeedPath(a.platform, a)} className="storage-creator" title={`Show posts by @${a.handle}`}>
                  <span className="storage-creator-name">@{a.handle || a.id}</span>
                  <span className="storage-creator-sub">
                    {a.name && a.name !== a.handle ? `${a.name} · ` : ""}{platformLabel(a.platform)}
                  </span>
                </Link>
                {a.person && (
                  <Link to={personPath(a.person.id)} className="chip person-chip" title={`Person: ${a.person.name}`}>
                    <Icon name="users" size={11} />{a.person.name}
                  </Link>
                )}
              </td>
              <td className="num">{fmtInt(a.posts)}</td>
              <td className="num">{fmtInt(a.media)}</td>
              <td className="num storage-size">{fmtBytes(a.bytes)}</td>
              <td className="storage-share">
                <span className="channel-bar-track" aria-hidden="true">
                  <span className="channel-bar-fill" style={{ width: `${(a.bytes / max) * 100}%`, "--d": `${Math.min(i, 30) * 20}ms` }} />
                </span>
                <span className="mono storage-pct">{pct(a.bytes, total)}</span>
              </td>
              <td className="storage-split" title={`Kept ${fmtBytes(a.kept_bytes)} · unreviewed ${fmtBytes(a.unreviewed_bytes)}`}>
                <span className="split-track" aria-hidden="true">
                  <span className="split-kept" style={{ width: `${a.bytes ? (a.kept_bytes / a.bytes) * 100 : 0}%` }} />
                  <span className="split-unrev" style={{ width: `${a.bytes ? (a.unreviewed_bytes / a.bytes) * 100 : 0}%` }} />
                </span>
                <span className="split-labels mono">
                  <span className="split-kept-label">{fmtBytes(a.kept_bytes)}</span>
                  <span className="split-unrev-label">{fmtBytes(a.unreviewed_bytes)}</span>
                </span>
              </td>
              <td>
                <div className="storage-actions">
                  <Link to={authorFeedPath(a.platform, a)} className="icon-btn" title={`Feed: @${a.handle}'s posts`} aria-label={`Feed of @${a.handle}`}>
                    <Icon name="feed" size={15} />
                  </Link>
                  <Link
                    to={`/review?${new URLSearchParams({ platform: a.platform, author: a.id })}`}
                    className="icon-btn"
                    title={`Review @${a.handle}'s unreviewed posts`}
                    aria-label={`Review @${a.handle}`}
                  >
                    <Icon name="review" size={15} />
                  </Link>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BigFile({ item, index, onTrash }) {
  const [broken, setBroken] = useState(false);
  const d = `${Math.min(index, 40) * 18}ms`;
  return (
    <div className="big-file" style={{ animationDelay: d }}>
      <Link to={`/p/${encodeURIComponent(item.platform)}/${encodeURIComponent(item.post_id)}`} className="big-file-link" title="Open the post">
        {item.thumb_url && !broken ? (
          <img src={item.thumb_url} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          <span className="big-file-ph"><Icon name={item.kind === "video" ? "play" : "image"} size={26} /></span>
        )}
        {item.kind === "video" && <span className="big-file-kind"><Icon name="play" size={11} className="icon-fill" /></span>}
        <span className="big-file-size mono">{fmtBytes(item.bytes)}</span>
      </Link>
      <div className="big-file-foot">
        <span className="big-file-author" title={item.author?.handle ? `@${item.author.handle}` : ""}>
          @{item.author?.handle || item.author?.id || "?"}
        </span>
        <button
          type="button"
          className="icon-btn icon-btn-danger big-file-trash"
          onClick={() => onTrash(item)}
          title="Move this file to the trash"
          aria-label={`Move this ${fmtBytes(item.bytes)} ${item.kind} to the trash`}
        >
          <Icon name="trash" size={14} />
        </button>
      </div>
    </div>
  );
}

export default function Storage() {
  const { refreshKey, running, start } = useScan();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const person = params.get("person") || "";
  const load = useCallback(() => getStorage(person || undefined), [person]);
  const { data: s, error, reload } = useApi(load, refreshKey);
  const config = useApi(getConfig, refreshKey);
  const people = useApi(getPeople, refreshKey);

  const [pending,   setPending]   = useState(null);    // the largest-file item awaiting confirmation
  const [busy,      setBusy]      = useState(false);
  const [dlgError,  setDlgError]  = useState(null);
  const [delErrors, setDelErrors] = useState(null);
  // Hidden at once on success; the reload that follows brings the new totals.
  const [gone, setGone] = useState(() => new Set());

  async function runTrash() {
    const item = pending;
    setBusy(true);
    setDlgError(null);
    try {
      const r = await deleteItems({ media: [item.media_id] });
      const done = (r?.media || []).includes(item.media_id);
      if (!r?.ok && !done && !r?.errors?.length) { setDlgError(r?.error || "Nothing could be moved."); return; }
      setPending(null);
      setDelErrors(r.errors?.length ? r.errors : null);
      if (done) {
        setGone(prev => new Set(prev).add(item.media_id));
        const post = (r.posts || []).includes(item.post) ? " The post had no other files and went with it." : "";
        toast(`Moved to the trash (${r.files ?? 0} file${r.files === 1 ? "" : "s"}, ${fmtBytes(r.bytes ?? 0)}).${post}`);
      }
      reload();
    } catch (e) {
      setDlgError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const head = <PersonScopeHead title="Storage" person={person} people={people.data} setParams={setParams} />;

  if (!s) {
    return (
      <div className="stats-page">
        {head}
        <div className="card">
          {error ? (
            <div className="empty">
              Could not load storage: {error.message}{" "}
              <button type="button" className="btn-link" onClick={reload}>Retry</button>
            </div>
          ) : <div className="empty">Loading…</div>}
        </div>
      </div>
    );
  }

  const trashNote = s.trash.bytes > 0 && (
    <p className="storage-note">
      <Icon name="trash" size={13} />
      <span>
        {fmtBytes(s.trash.bytes)} in the <Link to="/trash" className="text-link">trash</Link> is not
        freed until it is deleted for good there, or the trash is emptied in{" "}
        <Link to="/settings" className="text-link">Settings</Link>.
      </span>
    </p>
  );
  const noRoots = config.data && !(config.data.media_roots || []).length;
  if (s.totals.posts === 0) {
    return (
      <div className="stats-page">
        {head}
        <div className="card">
          <div className="empty">
            {person ? (
              <>This person has no posts in the index.{" "}
                <Link to={personPath(person)} className="text-link">Open the person</Link>
              </>
            ) : noRoots ? (
              <>No media folder yet. Add one in <Link to="/settings" className="text-link">Settings</Link>, and
                its disk use shows up here.</>
            ) : (
              <>Nothing indexed yet.{" "}
                <button type="button" className="btn-link" onClick={start} disabled={running}>
                  {running ? "Scanning…" : "Rescan now"}
                </button>
              </>
            )}
          </div>
        </div>
        {trashNote}
      </div>
    );
  }

  const largest = s.largest.filter(m => !gone.has(m.media_id));
  const hero = [
    { num: fmtBytes(s.totals.bytes), label: "on disk" },
    { num: fmtInt(s.totals.media),   label: "media files" },
    { num: fmtInt(s.totals.posts),   label: "posts" },
    { num: fmtBytes(s.trash.bytes),  label: `in the trash · ${fmtInt(s.trash.files)} files`, warn: s.trash.bytes > 0, to: "/trash", title: "See what is in the trash" },
  ];

  return (
    <div className="stats-page storage-page">
      {head}
      {error && <div className="msg err" role="alert">Could not refresh: {error.message}</div>}
      <StatsHero cells={hero} />
      {trashNote}

      <div className="card">
        <div className="card-title">Per creator · {fmtInt(s.by_author.length)}</div>
        {s.by_author.length === 0
          ? <div className="empty">No creator has posts yet.</div>
          : <CreatorTable rows={s.by_author} total={s.totals.bytes} />}
      </div>

      <div className="stats-split storage-bars">
        <Bars
          title="Size per kind"
          entries={s.by_kind.map(k => [k.kind, k.bytes])}
          label={k => k}
          format={fmtBytes}
          linkFor={k => `/?${new URLSearchParams({ kind: k, ...(person ? { person } : {}) })}`}
          delay={400}
        />
        <Bars
          title="Size per year posted"
          entries={s.by_year.map(y => [y.year ?? "no date", y.bytes])}
          label={y => String(y)}
          format={fmtBytes}
          delay={500}
        />
      </div>

      <div className="card">
        <div className="card-title">Largest files · top {s.largest.length}</div>
        <DeleteErrors errors={delErrors} onDismiss={() => setDelErrors(null)} />
        {largest.length === 0 ? (
          <div className="empty">No media files.</div>
        ) : (
          <div className="big-files">
            {largest.map((m, i) => (
              <BigFile key={m.media_id} item={m} index={i} onTrash={item => { setDlgError(null); setPending(item); }} />
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!pending}
        danger
        busy={busy}
        error={dlgError}
        title="Move this file to the trash?"
        confirmLabel={`Trash ${pending ? fmtBytes(pending.bytes) : ""}`}
        onConfirm={runTrash}
        onCancel={() => setPending(null)}
      >
        {pending && (
          <p>
            Move this {pending.kind} by @{pending.author?.handle || pending.author?.id} ({fmtBytes(pending.bytes)}) to
            the trash? If it is the post's only file, the whole post goes with it. You can empty the trash from Settings.
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}
