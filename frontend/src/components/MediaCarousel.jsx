import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Icon from "./Icon";
import { fmtBytes } from "../lib/fmt";
import { PHONE } from "../lib/layout";
import { useSwipe } from "../lib/swipe";

function MissingMedia({ item, label = "File missing on disk" }) {
  return (
    <div className="media-missing" role="img" aria-label={label}>
      <Icon name="warn" size={26} />
      <span>{label}</span>
      {item?.kind && <span className="dim mono">{item.kind}{item.size != null ? ` · ${fmtBytes(item.size)}` : ""}</span>}
    </div>
  );
}

function Slide({ item, alt, active }) {
  const [failed, setFailed] = useState(false);
  const videoRef = useRef(null);

  // Leaving a slide stops its video, so two never play over each other.
  useEffect(() => {
    if (!active) videoRef.current?.pause();
  }, [active]);

  if (item.missing) return <MissingMedia item={item} />;
  if (failed) return <MissingMedia item={item} label="Could not load this file" />;
  if (item.kind === "video") {
    return (
      <video
        ref={videoRef}
        className="media-el"
        src={item.url}
        poster={item.poster_url || undefined}
        controls
        preload="metadata"
        playsInline
        aria-label={alt}
        onError={() => setFailed(true)}
      />
    );
  }
  return <img className="media-el" src={item.url} alt={alt} onError={() => setFailed(true)} />;
}

/* Media carousel for the post page: arrows, dots, a swipe on a touch
   screen, and the Left/Right keys (ignored while typing). The keys page
   even when a <video> has focus: a clicked video keeps focus, and arrows
   that silently stopped paging after pressing play felt broken. Leaving a
   slide pauses its video anyway. Every slide stays mounted so video
   position survives paging back. */
export default function MediaCarousel({ media, alt, onDeleteItem }) {
  const [idx, setIdx] = useState(0);
  const n = media.length;
  const safe = Math.min(idx, n - 1);
  const stageRef = useRef(null);
  const swipe = useSwipe(step => { if (n > 1) setIdx(i => (Math.min(i, n - 1) + step + n) % n); });

  // On a phone the stage ends above the screen's bottom (#159): a video's
  // controls show without a scroll. index.css sizes it from --stage-top,
  // where it starts on the page, measured from layout (not the scroll
  // position), again whenever the page above it changes size.
  useLayoutEffect(() => {
    const phone = window.matchMedia(PHONE);
    function fit() {
      const stage = stageRef.current;
      if (!stage) return;
      if (!phone.matches) { stage.style.removeProperty("--stage-top"); return; }
      const top = `${Math.round(stage.getBoundingClientRect().top + window.scrollY)}px`;
      if (stage.style.getPropertyValue("--stage-top") !== top) stage.style.setProperty("--stage-top", top);
    }
    fit();
    const seen = new ResizeObserver(fit);
    seen.observe(document.body);
    window.addEventListener("resize", fit);
    phone.addEventListener("change", fit);
    return () => {
      seen.disconnect();
      window.removeEventListener("resize", fit);
      phone.removeEventListener("change", fit);
    };
  }, []);

  useEffect(() => {
    if (n < 2) return;
    function onKey(e) {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const tag = e.target.tagName;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || e.target.isContentEditable) return;
      if (e.key === "ArrowLeft")  { e.preventDefault(); setIdx(i => (i - 1 + n) % n); }
      if (e.key === "ArrowRight") { e.preventDefault(); setIdx(i => (i + 1) % n); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [n]);

  if (n === 0) return null;

  return (
    <section className="carousel" aria-roledescription="carousel" aria-label="Post media">
      <div className="carousel-stage" ref={stageRef} {...swipe}>
        {media.map((m, i) => (
          <div
            key={m.id ?? i}
            className={`carousel-slide${i === safe ? " is-active" : ""}`}
            aria-roledescription="slide"
            aria-label={`${i + 1} of ${n}`}
            aria-hidden={i !== safe}
          >
            <Slide item={m} alt={n > 1 ? `${alt} (${i + 1} of ${n})` : alt} active={i === safe} />
          </div>
        ))}
        {n > 1 && (
          <>
            <button type="button" className="carousel-arrow prev" onClick={() => setIdx((safe - 1 + n) % n)} aria-label="Previous media">
              <Icon name="chevLeft" size={20} />
            </button>
            <button type="button" className="carousel-arrow next" onClick={() => setIdx((safe + 1) % n)} aria-label="Next media">
              <Icon name="chevRight" size={20} />
            </button>
            <span className="carousel-count" aria-live="polite">{safe + 1}/{n}</span>
          </>
        )}
      </div>
      {(n > 1 || onDeleteItem) && (
        <div className="carousel-foot">
        <div className="carousel-dots">
          {media.map((m, i) => (
            <button
              key={m.id ?? i}
              type="button"
              className={`carousel-dot${i === safe ? " is-active" : ""}${m.missing ? " is-missing" : ""}`}
              onClick={() => setIdx(i)}
              aria-label={`Show media ${i + 1}${m.kind === "video" ? " (video)" : ""}`}
              aria-current={i === safe}
            />
          ))}
        </div>
        {onDeleteItem && (
          <button
            type="button"
            className="btn-ghost carousel-del"
            onClick={() => onDeleteItem(media[safe], safe)}
            title={`Move item ${safe + 1} to the trash`}
          >
            <Icon name="trash" size={13} />Delete this item
          </button>
        )}
        </div>
      )}
    </section>
  );
}
