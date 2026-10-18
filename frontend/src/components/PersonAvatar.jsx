import { useState } from "react";
import { avatarUrl } from "../lib/avatar";

// Each size: its class and the picture's file (lib/avatar).
const SIZES = {
  chip:     { cls: " is-chip",                  file: "small" },   // 22 px, round
  card:     { cls: "",                          file: "small" },   // 36 px, round (Compact rows)
  portrait: { cls: " is-portrait",              file: "card" },    // 2:3, as wide as its box (Portraits cards)
  large:    { cls: " is-portrait is-large",     file: "large" },   // 2:3, the person page's head
};

/* A person's picture, or their initial when they have none (or it cannot
   load: a file gone from the data directory). The same box either way
   (index.css .avatar-img), so nothing moves when it loads: round for the
   chips and rows, a 2:3 portrait (as Stash shows performers) for the
   Creators cards and the person's page. A picture saved square, before
   portraits, covers the portrait's box.

   Props: person ({ name, avatar }), size: "chip", "card" (the default),
   "portrait" or "large" */
export default function PersonAvatar({ person, size = "card" }) {
  const [broken, setBroken] = useState(null);           // the name that failed to load
  const name = person?.avatar;
  const { cls, file } = SIZES[size] || SIZES.card;
  if (name && broken !== name) {
    return (
      <img className={`avatar-img${cls}`} src={avatarUrl(name, file)} alt="" aria-hidden="true"
           loading={size === "large" ? undefined : "lazy"} decoding="async" draggable={false}
           onError={() => setBroken(name)} />
    );
  }
  // The letter drawn by CSS (attr()), not text: a picker's option reads as
  // the name alone, in its text as in its accessible name.
  return <span className={`avatar-letter${cls}`} aria-hidden="true" data-letter={(person?.name || "?").charAt(0).toUpperCase()} />;
}
