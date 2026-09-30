import { Link } from "react-router-dom";
import { safeUrl } from "../lib/fmt";

/* Post text with line breaks kept (the container sets white-space: pre-wrap),
   #hashtags and @mentions highlighted and linked to a feed search, and bare
   URLs made clickable. Everything else renders as plain text, never as HTML. */
const TOKEN = /(https?:\/\/[^\s<>"']+|[#＃][\p{L}\p{N}_]+|@[\p{L}\p{N}_](?:[\p{L}\p{N}_.]*[\p{L}\p{N}_])?)/gu;

export default function RichText({ text, className = "" }) {
  if (!text) return null;
  const parts = text.split(TOKEN);
  return (
    <div className={`rich-text ${className}`}>
      {parts.map((part, i) => {
        if (i % 2 === 0) return part;
        if (part.startsWith("http")) {
          // Trailing punctuation belongs to the sentence, not the link.
          const m = part.match(/^(.*?)([).,!?;:]*)$/);
          const href = safeUrl(m[1]);
          return href
            ? <span key={i}><a className="rt-link" href={href} target="_blank" rel="noreferrer">{m[1]}</a>{m[2]}</span>
            : part;
        }
        const word = part.slice(1);
        const cls = part[0] === "@" ? "rt-mention" : "rt-hashtag";
        return (
          <Link key={i} className={cls} to={`/?q=${encodeURIComponent(word)}`} title={`Search “${word}”`}>
            {part}
          </Link>
        );
      })}
    </div>
  );
}
