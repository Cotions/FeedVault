import { useCallback, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { getPost, deleteItems, getTags, applyTags } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { useToast } from "../lib/toast";
import { excerpt, fmt, fmtBytes, fmtFullDate, fmtIso, platformLabel, safeUrl, authorFeedPath } from "../lib/fmt";
import MediaCarousel from "../components/MediaCarousel";
import RichText from "../components/RichText";
import Icon from "../components/Icon";
import ConfirmDialog from "../components/ConfirmDialog";
import DeleteErrors from "../components/DeleteErrors";
import TagChips from "../components/TagChips";
import TagInput from "../components/TagInput";
import CollectionDialog from "../components/CollectionDialog";

/* The post's tags: remove with ×, add with autocomplete. */
function PostTags({ post, onChanged }) {
  const toast = useToast();
  const tagsApi = useApi(getTags, 0);
  const [busy, setBusy] = useState(false);

  async function change(body) {
    setBusy(true);
    try {
      const r = await applyTags([post.id], body);
      if (!r?.ok) { toast(r?.error || "Could not change the tags.", "err"); return; }
      onChanged();
      tagsApi.reload();
    } catch (e) {
      toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  }

  const tags = post.tags || [];
  return (
    <div className="card post-tags">
      <div className="card-title"><Icon name="tag" size={13} />Tags</div>
      {tags.length > 0
        ? <TagChips tags={tags} onRemove={name => change({ remove: [name] })} busy={busy} />
        : <p className="dim post-tags-none">No tags yet.</p>}
      <TagInput tags={tagsApi.data || []} exclude={tags} onAdd={name => change({ add: [name] })} />
    </div>
  );
}

function CopyField({ label, value }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch { /* clipboard blocked: the value is selectable anyway */ }
  }
  return (
    <div className="kv-row">
      <span className="kv-key">{label}</span>
      <code className="kv-val kv-copy">{value}</code>
      <button type="button" className="icon-btn" onClick={copy} title={copied ? "Copied" : "Copy path"} aria-label={`Copy ${label}`}>
        <Icon name={copied ? "check" : "copy"} size={14} />
      </button>
    </div>
  );
}

export default function PostPage() {
  const { platform, postId } = useParams();
  const { refreshKey } = useScan();
  const navigate = useNavigate();
  const location = useLocation();
  const load = useCallback(() => getPost(platform, postId), [platform, postId]);
  const { data: post, error, loading, reload } = useApi(load, refreshKey);
  const toast = useToast();

  // Pending deletion: null, { type: "post" }, or { type: "item", item, index }.
  const [confirm,   setConfirm]   = useState(null);
  const [busy,      setBusy]      = useState(false);
  const [dlgError,  setDlgError]  = useState(null);
  const [delErrors, setDelErrors] = useState(null);
  const [collecting, setCollecting] = useState(false);

  // Back returns to the feed exactly as it was (filters, scroll) when we came
  // from inside the app; a direct link has no history, so go to the feed.
  const back = () => (location.key !== "default" ? navigate(-1) : navigate("/"));

  // A stale post from the previous route must not render under the new URL.
  const matches = post && post.platform === platform && post.post_id === postId;

  function ask(c) { setDlgError(null); setConfirm(c); }

  async function runDelete() {
    setBusy(true);
    setDlgError(null);
    try {
      if (confirm.type === "post") {
        const r = await deleteItems({ posts: [post.id] });
        if (!r?.ok && !r?.posts?.length) { setDlgError(r?.error || "Nothing could be deleted."); return; }
        if (r.posts?.includes(post.id)) {
          setConfirm(null);
          toast(`Post moved to the trash (${r.files ?? 0} file${r.files === 1 ? "" : "s"}, ${fmtBytes(r.bytes ?? 0)}).`);
          back();
          return;
        }
        setConfirm(null);
        setDelErrors(r.errors?.length ? r.errors : [{ path: post.id, error: "the post could not be removed" }]);
        reload();
      } else {
        const id = confirm.item.id;
        const r = await deleteItems({ media: [id] });
        if (!r?.ok && !r?.media?.length) { setDlgError(r?.error || "Nothing could be deleted."); return; }
        setConfirm(null);
        if (r.errors?.length) setDelErrors(r.errors);
        if (!r.media?.includes(id)) { reload(); return; }
        // Removing the last item removes the post: check before reloading.
        try {
          await getPost(platform, postId);
          toast(`Item moved to the trash (${fmtBytes(r.bytes ?? 0)}).`);
          reload();
        } catch (e) {
          if (e.status !== 404) throw e;
          toast("Last item deleted, so the post was removed.");
          back();
        }
      }
    } catch (e) {
      setDlgError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const head = (
    <div className="post-page-head">
      <button type="button" className="btn-secondary btn-back" onClick={back}>
        <Icon name="back" size={15} />Back
      </button>
      <div className="page-head-spacer" />
      {matches && (
        <button type="button" className="btn-danger-soft" onClick={() => ask({ type: "post" })}>
          <Icon name="trash" size={14} />Delete post
        </button>
      )}
    </div>
  );

  if (error && (!matches || error.status === 404)) {
    return (
      <div className="post-page">
        {head}
        <div className="card"><div className="empty">
          {error.status === 404 ? "This post is not in the index." : `Could not load the post: ${error.message}`}
        </div></div>
      </div>
    );
  }
  if (loading || !matches) {
    return <div className="post-page">{head}<div className="card"><div className="empty">Loading…</div></div></div>;
  }

  const handle = post.author?.handle || "unknown";
  const alt = excerpt(post.text, 140) || `${platformLabel(post.platform)} post by @${handle}`;
  const media = post.media || [];
  const original = safeUrl(post.url);
  const stats = post.stats || {};
  const statCells = [
    { key: "likes",    icon: "heart",   label: "likes",    v: stats.likes },
    { key: "comments", icon: "comment", label: "comments", v: stats.comments },
    { key: "views",    icon: "eye",     label: "views",    v: stats.views },
  ];
  const hasStats = statCells.some(s => s.v != null);
  const totalBytes = media.reduce((s, m) => s + (m.size || 0), 0);
  const src = post.source || {};

  return (
    <div className="post-page">
      {head}
      <DeleteErrors errors={delErrors} onDismiss={() => setDelErrors(null)} />
      <ConfirmDialog
        open={!!confirm}
        danger
        busy={busy}
        error={dlgError}
        title={confirm?.type === "item" ? "Delete this item?" : "Delete this post?"}
        confirmLabel={confirm?.type === "item" ? "Delete item" : "Delete post"}
        onConfirm={runDelete}
        onCancel={() => setConfirm(null)}
      >
        {confirm?.type === "item" ? (
          <p>
            Move item {confirm.index + 1} of {media.length} ({confirm.item.kind}
            {confirm.item.size != null ? `, ${fmtBytes(confirm.item.size)}` : ""}) to the trash?
            The rest of the post stays. You can empty the trash from Settings.
          </p>
        ) : (
          <p>
            Move this post ({media.length} media item{media.length === 1 ? "" : "s"}, {fmtBytes(totalBytes)},
            plus its thumbnails and metadata files) to the trash? You can empty the trash from Settings.
          </p>
        )}
      </ConfirmDialog>
      {post.missing && (
        <div className="msg err post-missing-note">
          <Icon name="warn" size={14} /> The metadata file for this post is gone from disk. FeedVault keeps the post in the index.
        </div>
      )}

      <div className={`post-layout${media.length ? "" : " is-text-only"}`}>
        {media.length > 0 && (
          <div className="post-media">
            <MediaCarousel
              key={post.id}
              media={media}
              alt={alt}
              onDeleteItem={media.length > 1 ? (item, index) => ask({ type: "item", item, index }) : undefined}
            />
          </div>
        )}

        <div className="post-side">
          <div className="card post-head-card">
            <div className="post-byline">
              <span className="avatar-letter" aria-hidden="true">{handle.charAt(0).toUpperCase()}</span>
              <div className="post-byline-id">
                <Link to={authorFeedPath(post.platform, post.author)} className="post-byline-handle">@{handle}</Link>
                {post.author?.name && post.author.name !== handle && <span className="post-byline-name">{post.author.name}</span>}
              </div>
              <span className="chip">{platformLabel(post.platform)}</span>
              <span className="chip">{post.kind}</span>
            </div>

            {post.text
              ? <RichText text={post.text} className={media.length ? "" : "is-large"} />
              : <p className="dim">
                  {src.tool?.includes("(filenames)")
                    ? "No caption: this post was downloaded without metadata, so only its media and file names are known."
                    : "No text."}
                </p>}

            <dl className="post-dates">
              <div>
                <dt>Posted</dt>
                <dd><time dateTime={fmtIso(post.posted_at)}>{fmtFullDate(post.posted_at)}</time></dd>
              </div>
              <div>
                <dt>Saved</dt>
                <dd><time dateTime={fmtIso(post.saved_at)}>{fmtFullDate(post.saved_at)}</time></dd>
              </div>
              {post.album && (
                <div>
                  <dt>Highlight</dt>
                  <dd><Link to={`/?q=${encodeURIComponent(post.album)}`}>{post.album}</Link></dd>
                </div>
              )}
              {post.location && (
                <div>
                  <dt>Location</dt>
                  <dd className="post-location"><Icon name="pin" size={13} />{post.location}</dd>
                </div>
              )}
            </dl>

            {original && (
              <a className="btn-primary btn-original" href={original} target="_blank" rel="noreferrer">
                <Icon name="external" size={14} />Open original on {platformLabel(post.platform)}
              </a>
            )}
          </div>

          {hasStats && (
            <div className="vp-stats">
              {statCells.map((s, i) => (
                <div key={s.key} className="vp-stat" style={{ animationDelay: `${i * 80}ms` }} title={s.v != null ? s.v.toLocaleString() : "not captured"}>
                  <span className="vp-stat-num">{fmt(s.v)}</span>
                  <span className="vp-stat-label"><Icon name={s.icon} size={11} />{s.label}</span>
                </div>
              ))}
            </div>
          )}

          <PostTags post={post} onChanged={reload} />

          <div className="card post-collections">
            <div className="card-title"><Icon name="bookmark" size={13} />Collections</div>
            {post.collections?.length > 0 ? (
              <ul className="tag-chips">
                {post.collections.map(c => (
                  <li key={c.id} className="tag-chip is-collection"><Link to={`/collections/${c.id}`}>{c.name}</Link></li>
                ))}
              </ul>
            ) : <p className="dim post-tags-none">In no collection.</p>}
            <button type="button" className="btn-secondary" onClick={() => setCollecting(true)}>
              <Icon name="plus" size={14} />Add to collection…
            </button>
            {collecting && (
              <CollectionDialog
                posts={[post.id]}
                member={(post.collections || []).map(c => c.id)}
                onChanged={reload}
                onClose={() => setCollecting(false)}
              />
            )}
          </div>

          <div className="card post-source">
            <div className="card-title">Source</div>
            <div className="kv-row">
              <span className="kv-key">tool</span>
              <span className="kv-val">{src.tool || "—"}{src.version ? <span className="dim"> {src.version}</span> : null}</span>
            </div>
            <div className="kv-row">
              <span className="kv-key">id</span>
              <code className="kv-val">{post.id}</code>
            </div>
            <div className="kv-row">
              <span className="kv-key">media</span>
              <span className="kv-val">
                {media.length} file{media.length === 1 ? "" : "s"}
                {totalBytes > 0 && <span className="dim"> · {fmtBytes(totalBytes)}</span>}
                {media.some(m => m.missing) && <span className="warn-text"> · {media.filter(m => m.missing).length} missing</span>}
              </span>
            </div>
            {src.meta_path && <CopyField label="meta" value={src.meta_path} />}
          </div>
        </div>
      </div>
    </div>
  );
}
