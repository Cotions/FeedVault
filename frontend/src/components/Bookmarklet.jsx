import { useEffect, useRef } from "react";
import { bookmarkletHref } from "../lib/bookmarklet";
import { useToast } from "../lib/toast";
import Icon from "./Icon";

/* The "Save to FeedVault" bookmarklet, to drag to the bookmarks bar
   (lib/bookmarklet.js), built for the address FeedVault is open at.

   On the Links page it is a strip over the list, shown from the page's
   More menu; onClose (its ×) hides it again.

   React refuses a javascript: href (a warning in 18, blocked in 19), so
   the attribute is set on the element itself, once mounted; React never
   renders one. A click here does nothing but say how it is used. */
export default function Bookmarklet({ onClose }) {
  const ref = useRef(null);
  const toast = useToast();

  useEffect(() => {
    ref.current?.setAttribute("href", bookmarkletHref(window.location.origin));
  }, []);

  return (
    <div className="links-bookmarklet">
      <span className="links-bookmarklet-label">Bookmarklet</span>
      <a ref={ref} className="links-bookmarklet-btn" title="Drag to your bookmarks bar"
         onClick={e => { e.preventDefault(); toast("Drag it to your bookmarks bar, then click it on any page.", "info"); }}>
        <Icon name="link" size={13} />Save to FeedVault
      </a>
      <span className="dim">Drag it to your bookmarks bar to save any page you are on.</span>
      {onClose && (
        <button type="button" className="del-btn" onClick={onClose} aria-label="Hide the bookmarklet" title="Hide">
          <Icon name="close" size={14} />
        </button>
      )}
    </div>
  );
}
