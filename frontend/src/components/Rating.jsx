import { useState } from "react";
import { ratingFromClick, ratingFromKey, ratingLabel, starFills } from "../lib/rating";

/* Five stars, in half steps (lib/rating.js). Each star is an outline with
   the filled star over it, cut to its share (a half star: its left half). */

const STAR = "M12 2.6 14.9 8.6 21.5 9.4 16.6 13.9 17.9 20.4 12 17.1 6.1 20.4 7.4 13.9 2.5 9.4 9.1 8.6z";

function Star({ fill, size }) {
  return (
    <span className="star" style={{ width: size, height: size }}>
      <svg viewBox="0 0 24 24" width={size} height={size} className="star-empty" aria-hidden="true"><path d={STAR} /></svg>
      {fill > 0 && (
        <span className="star-fill" style={{ width: `${fill * 100}%` }}>
          <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true"><path d={STAR} /></svg>
        </span>
      )}
    </span>
  );
}

// A rating to read: a card's small stars. Nothing for one not rated.
export function Stars({ rating, size = 12 }) {
  if (!rating) return null;
  const label = ratingLabel(rating);
  return (
    <span className="stars" role="img" aria-label={label} title={label}>
      {starFills(rating).map((f, i) => <Star key={i} fill={f} size={size} />)}
    </span>
  );
}

/* The person page's rating: one control (a slider, one Tab stop). A click
   on a star's left half gives half a star, on its right half the whole
   star; a click on the rating it has clears it. Keys: arrows ±½ (down
   stops at ½), Home and End ½ and 5, Delete or Backspace clears. The stars under the pointer show what a
   click would give. ``onChange(rating or null)``; nothing while ``busy``. */
export function RatingInput({ value, onChange, busy = false, size = 24 }) {
  const [hover, setHover] = useState(null);
  const shown = hover ?? value;

  // Which star and which half the pointer is over, from where it is.
  function at(e) {
    const box = e.currentTarget.getBoundingClientRect();
    const x = Math.max(0, Math.min(box.width - 1, e.clientX - box.left));
    const step = box.width / 5;
    return { index: Math.floor(x / step), left: x % step < step / 2 };
  }

  function change(next) {
    if (!busy && next !== undefined && next !== value) onChange(next);
  }

  return (
    <span
      className={`rating-input${busy ? " is-busy" : ""}`}
      role="slider"
      tabIndex={0}
      aria-label={ratingLabel(value)}
      aria-valuemin={0}
      aria-valuemax={5}
      aria-valuenow={(value || 0) / 2}
      aria-valuetext={value ? `${value / 2} of 5 stars` : "Not rated"}
      aria-disabled={busy || undefined}
      title={value ? `${ratingLabel(value)}. Click it again to clear` : "Rate: click the left half of a star for half"}
      onMouseMove={e => { const s = at(e); setHover(s.index * 2 + (s.left ? 1 : 2)); }}
      onMouseLeave={() => setHover(null)}
      onClick={e => { const s = at(e); change(ratingFromClick(value, s.index, s.left)); setHover(null); }}
      onKeyDown={e => {
        const next = ratingFromKey(value, e.key);
        if (next === undefined) return;
        e.preventDefault();
        change(next);
      }}
    >
      {starFills(shown).map((f, i) => <Star key={i} fill={f} size={size} />)}
    </span>
  );
}
