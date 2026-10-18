import { useState } from "react";
import { avatarUrl } from "../lib/avatar";

/* A person's picture, round, or their initial when they have none (or it
   cannot load: a file gone from the data directory). The same box either
   way (index.css .avatar-img), so nothing moves when it loads.

   Props: person ({ name, avatar }), size: "chip" (22 px), "card" (36 px,
   the default) or "large" (the person page's head) */
export default function PersonAvatar({ person, size = "card" }) {
  const [broken, setBroken] = useState(null);           // the name that failed to load
  const name = person?.avatar;
  const cls = size === "card" ? "" : ` is-${size}`;
  if (name && broken !== name) {
    return (
      <img className={`avatar-img${cls}`} src={avatarUrl(name, size !== "large")} alt="" aria-hidden="true"
           decoding="async" draggable={false} onError={() => setBroken(name)} />
    );
  }
  // The letter drawn by CSS (attr()), not text: a picker's option reads as
  // the name alone, in its text as in its accessible name.
  return <span className={`avatar-letter${cls}`} aria-hidden="true" data-letter={(person?.name || "?").charAt(0).toUpperCase()} />;
}
