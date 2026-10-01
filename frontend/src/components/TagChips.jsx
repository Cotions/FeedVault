import { Link } from "react-router-dom";
import Icon from "./Icon";
import { tagFeedPath } from "../lib/fmt";

/* A post's tags. Each opens the Feed filtered by it; with `onRemove` each
   gets a × button. `max` shows that many and a "+n" for the rest. */
export default function TagChips({ tags = [], onRemove, max, compact = false, busy = false }) {
  if (!tags.length) return null;
  const shown = max ? tags.slice(0, max) : tags;
  const more = tags.length - shown.length;
  return (
    <ul className={`tag-chips${compact ? " is-compact" : ""}`} aria-label="Tags">
      {shown.map(name => (
        <li key={name} className="tag-chip">
          <Link to={tagFeedPath(name)} title={`Posts tagged ${name}`}>
            {!compact && <Icon name="tag" size={11} />}{name}
          </Link>
          {onRemove && (
            <button type="button" className="tag-chip-x" onClick={() => onRemove(name)} disabled={busy}
                    title={`Remove ${name}`} aria-label={`Remove tag ${name}`}>
              <Icon name="close" size={10} />
            </button>
          )}
        </li>
      ))}
      {more > 0 && <li className="tag-chip is-more" title={tags.slice(shown.length).join(", ")}>+{more}</li>}
    </ul>
  );
}
