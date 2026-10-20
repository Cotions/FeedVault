import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Link } from "react-router-dom";
import Icon from "./Icon";

/* A row's or a card's secondary actions behind one "⋯" button, the same
   menu everywhere (accounts, people, sources, a person's page). ``label``
   is the button's accessible name and names the subject ("More for
   @handle"); the menu takes it too. ``buttonRef``: a ref to the button,
   for a page that gives focus back to it (the person page's rename).

   ``items``, in order (falsy ones are skipped, so a list can say
   ``cond && {…}``):
   - an action: { label, onSelect, icon?, title?, disabled?, danger? }
   - a link in the app: { label, to, icon?, title? } (a router Link)
   - a link to another site: { label, href, icon?, title? } (a new tab)
   - "separator" (one at the start, the end or after another is dropped)
   A toggle is an action whose label says what a click does (Mute and
   Unmute, Don't sync and Sync again): the card or row shows the state
   itself. ``disabled`` is aria-disabled: the item stays focusable and its
   title (why it is off) still shows; a click does nothing.

   The menu is a popover (the top layer): a card's overflow or a scrolling
   box never cuts it off. Placed under the button, its right edge on the
   button's, or above it when there is no room below. An action closes the
   menu and gives focus back to the button before it runs, so a dialog it
   opens gives focus back there too. Keys as in the ARIA menu button
   pattern: Enter, Space or Arrow Down open it on the first item, Arrow Up
   on the last; Arrow keys, Home and End move, a letter jumps to the next
   item starting with it, Enter and Space activate, Escape closes it onto
   the button, Tab closes it and moves on from the button. A click outside
   closes it. */
export default function ActionMenu({ label, items, className = "", buttonRef = null }) {
  const [open, setOpen] = useState(null);       // null (closed), "first" or "last": the item focused on opening
  const ownRef = useRef(null);
  const btnRef = buttonRef || ownRef;           // a page's own ref, to give focus back to the button itself
  const menuRef = useRef(null);
  const btnId = useId();
  const menuId = useId();
  const entries = tidy(items);

  // Shown in the top layer and placed under the button; again on a scroll,
  // a resize, or when its size changes (a late font).
  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!open || !menu) return undefined;
    if (menu.showPopover && !menu.matches(":popover-open")) menu.showPopover();
    function place() {
      const r = btnRef.current?.getBoundingClientRect();
      if (!r) return;
      const w = menu.offsetWidth, h = menu.offsetHeight;
      const vw = document.documentElement.clientWidth, vh = window.innerHeight;
      const left = Math.max(8, Math.min(r.right - w, vw - w - 8));
      const below = r.bottom + 4 + h <= vh - 8 || r.top - 4 - h < 8;
      menu.style.left = `${left}px`;
      menu.style.top = `${below ? r.bottom + 4 : Math.max(8, r.top - 4 - h)}px`;
    }
    place();
    const items = menuItems(menu);
    const start = open === "last" ? items.at(-1) : items.find(i => i.getAttribute("aria-disabled") !== "true") || items[0];
    start?.focus({ preventScroll: true });
    const sized = typeof ResizeObserver === "function" ? new ResizeObserver(place) : null;
    sized?.observe(menu);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      sized?.disconnect();
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, btnRef]);

  // A press anywhere but the menu and its button closes it (focus goes
  // where the press puts it).
  useEffect(() => {
    if (!open) return undefined;
    function away(e) {
      if (!menuRef.current?.contains(e.target) && !btnRef.current?.contains(e.target)) setOpen(null);
    }
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [open, btnRef]);

  if (!entries.length) return null;

  // Closed at once (not at the next render), then focus on the button: a
  // dialog the action opens gives focus back to it, and Tab moves on from it.
  function closeOnButton() {
    flushSync(() => setOpen(null));
    btnRef.current?.focus({ preventScroll: true });
  }

  function activate(e, it) {
    if (it.disabled) { e.preventDefault(); return; }
    if (it.to) { setOpen(null); return; }        // the app goes to another page: the router moves on
    closeOnButton();
    it.onSelect?.();
  }

  function onButtonKey(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(e.key === "ArrowUp" ? "last" : "first");
    }
  }

  function onMenuKey(e) {
    const items = menuItems(menuRef.current);
    const at = items.indexOf(document.activeElement);
    const go = i => items[(i + items.length) % items.length]?.focus({ preventScroll: true });
    if (e.key === "ArrowDown") { e.preventDefault(); go(at + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); go(at < 0 ? -1 : at - 1); }
    else if (e.key === "Home") { e.preventDefault(); go(0); }
    else if (e.key === "End") { e.preventDefault(); go(-1); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeOnButton(); }
    else if (e.key === "Tab") closeOnButton();      // not prevented: focus moves on from the button
    else if (e.key === " " && document.activeElement?.tagName === "A") { e.preventDefault(); document.activeElement.click(); }
    else if (e.key.length === 1 && /\S/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const k = e.key.toLowerCase();
      for (let n = 1; n <= items.length; n++) {
        const i = (at + n) % items.length;
        if (items[i].textContent.trim().toLowerCase().startsWith(k)) { go(i); break; }
      }
    }
  }

  return (
    <span className={`action-menu-wrap${className ? ` ${className}` : ""}`}>
      <button
        ref={btnRef}
        id={btnId}
        type="button"
        className={`icon-btn action-menu-btn${open ? " is-open" : ""}`}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={!!open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(o => (o ? null : "first"))}
        onKeyDown={onButtonKey}
      >
        <Icon name="more" size={16} />
      </button>
      {open && (
        <div ref={menuRef} id={menuId} className="action-menu" role="menu" aria-labelledby={btnId} popover="manual"
             onKeyDown={onMenuKey}>
          {entries.map((it, i) => {
            if (it === "separator") return <div key={`sep-${i}`} className="action-menu-sep" role="separator" />;
            const key = `${i}-${it.label}`;
            const props = {
              role: "menuitem",
              tabIndex: -1,
              title: it.title,
              className: `action-menu-item${it.danger ? " is-danger" : ""}`,
              "aria-disabled": it.disabled || undefined,
              onClick: e => activate(e, it),
            };
            const body = <>{it.icon ? <Icon name={it.icon} size={14} /> : <span className="action-menu-gap" />}<span>{it.label}</span></>;
            if (it.to) return <Link key={key} {...props} to={it.to}>{body}</Link>;
            if (it.href) return <a key={key} {...props} href={it.href} target="_blank" rel="noreferrer noopener">{body}</a>;
            return <button key={key} {...props} type="button">{body}</button>;
          })}
        </div>
      )}
    </span>
  );
}

// The items left once the falsy ones and the separators that separate
// nothing are dropped.
function tidy(items) {
  const out = [];
  for (const it of items || []) {
    if (!it) continue;
    if (it === "separator" && (!out.length || out.at(-1) === "separator")) continue;
    out.push(it);
  }
  if (out.at(-1) === "separator") out.pop();
  return out;
}

function menuItems(menu) {
  return menu ? [...menu.querySelectorAll('[role="menuitem"]')] : [];
}
