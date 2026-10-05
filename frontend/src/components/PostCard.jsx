import { useState } from "react";
import { Link } from "react-router-dom";
import Icon from "./Icon";
import TagChips from "./TagChips";
import { excerpt, fmtShortDate, fmtFullDate, platformLabel, platformShort, postPath, authorFeedPath } from "../lib/fmt";

/* One tile in the masonry feed. The cover keeps its natural aspect ratio (the
   grid is CSS columns, so tall and wide media sit side by side). Text-only
   posts put the text itself where the cover would be. */
export default function PostCard({ post, index = 0, selectMode = false, selected = false, onToggle }) {
  const [broken, setBroken] = useState(false);
  const cover  = post.cover;
  const text   = post.text || "";
  const handle = post.author?.handle || post.author?.name || "unknown";
  const alt    = excerpt(text, 140) || `${platformLabel(post.platform)} post by @${handle}`;
  const to     = postPath(post);
  // A video with no poster: cover.url is the video file itself.
  const videoOnly = cover?.kind === "video" && cover.poster === false;
  const isVideo   = cover?.kind === "video" || post.kind === "video";

  // In select mode every click inside the card toggles it instead of
  // following a link. Capture phase, so the Links never see the click.
  function onClickCapture(e) {
    if (!selectMode) return;
    e.preventDefault();
    e.stopPropagation();
    onToggle?.(index, e.shiftKey);
  }

  return (
    <article
      className={`post-card${post.missing ? " is-missing" : ""}${cover ? "" : " is-text"}${selectMode ? " is-selecting" : ""}${selected ? " is-selected" : ""}`}
      style={{ animationDelay: `${Math.min(index % 60, 24) * 30}ms` }}
      onClickCapture={onClickCapture}
    >
      {selectMode && (
        <button
          type="button"
          className="select-check"
          role="checkbox"
          aria-checked={selected}
          aria-label={`Select post: ${alt}`}
        >
          {selected && <Icon name="check" size={14} />}
        </button>
      )}
      {cover ? (
        <Link to={to} className="post-cover" aria-label={`Open post: ${alt}`}>
          {broken ? (
            <div className="post-cover-broken"><Icon name="image" size={26} /><span>media unavailable</span></div>
          ) : videoOnly ? (
            <video
              className="post-cover-media"
              src={`${cover.url}#t=0.1`}
              preload="metadata"
              muted
              playsInline
              aria-label={alt}
              onError={() => setBroken(true)}
            />
          ) : (
            <img
              className="post-cover-media"
              src={cover.url}
              alt={alt}
              loading="lazy"
              decoding="async"
              onError={() => setBroken(true)}
            />
          )}
          {isVideo && !broken && (
            <span className="post-play" aria-hidden="true"><Icon name="play" size={18} className="icon-fill" /></span>
          )}
          {post.media_count > 1 && (
            <span className="post-count-badge" title={`${post.media_count} media items`}>
              <Icon name="layers" size={12} />1/{post.media_count}
            </span>
          )}
          {post.missing && <span className="missing-badge" title="The metadata file is gone from disk">missing</span>}
        </Link>
      ) : (
        <Link to={to} className="post-textbody">
          {post.missing && <span className="missing-badge" title="The metadata file is gone from disk">missing</span>}
          <p>{text || <em className="dim">(empty post)</em>}</p>
        </Link>
      )}

      <div className="post-info">
        <div className="post-meta">
          <Link to={authorFeedPath(post.platform, post.author)} className="post-author"
                title={post.author?.name && post.author.name !== handle ? `${post.author.name} · @${handle}` : `@${handle}`}>
            @{handle}
          </Link>
          <span className="post-platform" title={platformLabel(post.platform)}>{platformShort(post.platform)}</span>
          {post.decision === "keep" && <span className="kept-dot" title="Marked as kept" aria-label="kept" role="img" />}
          <time className="post-date" dateTime={post.posted_at ? new Date(post.posted_at * 1000).toISOString() : undefined} title={fmtFullDate(post.posted_at)}>
            {fmtShortDate(post.posted_at)}
          </time>
        </div>
        {cover && text && (
          <Link to={to} className="post-excerpt" title={text}>{text}</Link>
        )}
        <TagChips tags={post.tags} max={3} compact />
      </div>
    </article>
  );
}
