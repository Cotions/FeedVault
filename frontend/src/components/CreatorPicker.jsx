import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { platformLabel, platformShort } from "../lib/fmt";
import { accountKey, accountText, matchedFormer, matches, personText } from "../lib/people";

const MAX_SHOWN = 60;

// An option's second line: platforms and count, and what matched.
function personSub(p) {
  return `${p.platforms.map(platformShort).join(" · ") || "no account"} · ${p.count}`;
}
function accountSub(a, query) {
  const former = matchedFormer(a, query);
  return `${platformLabel(a.platform)} · ${a.count}${former ? ` · was @${former}` : ""}${a.person ? ` · ${a.person.name}` : ""}`;
}

/* A searchable creator filter: people first, then accounts, matched by name,
   any handle the account ever had, or a folder alias. Replaces a <select>,
   which cannot search and lists 130 handles in one go.

   value: { person: id } | { platform, id } | null
   onChange({ person } | { account } | null)
   platform: list only accounts of that platform. exclude: account keys to
   leave out. people: null to offer accounts only. */
export default function CreatorPicker({
  people = null, accounts = [], value = null, onChange, platform = "", exclude,
  allLabel = "All", label = "Creator", placeholder = "Search name or handle…", className = "",
}) {
  const [open,   setOpen]   = useState(false);
  const [query,  setQuery]  = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const listId = useId();

  const options = useMemo(() => {
    const out = [];
    for (const p of people || []) {
      if (!query || matches(personText(p), query)) out.push({ key: `person:${p.id}`, person: p });
    }
    for (const a of accounts) {
      if (a.id == null || (platform && a.platform !== platform) || exclude?.has(accountKey(a))) continue;
      if (!query || matches(accountText(a), query)) out.push({ key: accountKey(a), account: a });
    }
    return out.slice(0, MAX_SHOWN);
  }, [people, accounts, platform, exclude, query]);

  // The list hangs from the field's left edge. When that would run it past
  // the right edge of the room it has (the window, or a scrolling box around
  // the picker such as Review's side panel), it hangs from the field's right
  // edge if that fits, else it is only as wide as the room.
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!open || !list) return undefined;
    function place() {
      delete list.dataset.side;
      list.style.maxWidth = "";
      const box = list.parentElement.getBoundingClientRect(), w = list.offsetWidth;
      let lo = 0, hi = document.documentElement.clientWidth;
      for (let a = list.parentElement.parentElement; a && a !== document.body; a = a.parentElement) {
        if (getComputedStyle(a).overflowX === "visible") continue;
        const r = a.getBoundingClientRect();
        lo = Math.max(lo, r.left + a.clientLeft);
        hi = Math.min(hi, r.left + a.clientLeft + a.clientWidth);
      }
      if (box.left + w <= hi) return;
      if (box.right - w >= lo) list.dataset.side = "left";
      else list.style.maxWidth = `${Math.max(box.width, hi - box.left)}px`;
    }
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open, options]);

  const current = value?.person != null
    ? (people || []).find(p => p.id === Number(value.person))
    : value?.id != null ? accounts.find(a => (a.id === value.id || a.aliases?.includes(value.id))
                                          && (!value.platform || a.platform === value.platform)) : null;
  const shown = value?.person != null
    ? current ? current.name : `person ${value.person}`
    : value?.id != null ? current ? `@${current.handle || current.id}` : `id ${value.id}` : "";

  function pick(o) {
    setOpen(false);
    setQuery("");
    inputRef.current?.blur();
    onChange(o ? (o.person ? { person: o.person } : { account: o.account }) : null);
  }

  function onKeyDown(e) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive(i => Math.min(options.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(i => Math.max(0, i - 1));
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      if (options[active]) pick(options[active]);
    } else if (e.key === "Escape" && open) {
      e.stopPropagation();
      setOpen(false);
      setQuery("");
    }
  }

  return (
    <div className={`picker ${className}`}>
      <input
        ref={inputRef}
        type="text"
        className="sort-select picker-input"
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
        placeholder={shown || allLabel}
        value={open ? query : shown}
        onFocus={() => { setOpen(true); setQuery(""); setActive(0); }}
        onBlur={() => { setOpen(false); setQuery(""); }}
        onChange={e => { setQuery(e.target.value); setActive(0); setOpen(true); }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul ref={listRef} className="picker-list" id={listId} role="listbox" aria-label={label}>
          {!query && value && (
            <li role="option" aria-selected={false} className="picker-option picker-all"
                onMouseDown={e => { e.preventDefault(); pick(null); }}>
              {allLabel}
            </li>
          )}
          {options.length === 0 && <li className="picker-empty">{placeholder.replace(/…$/, "")}: no match</li>}
          {options.map((o, i) => (
            <li
              key={o.key}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={`picker-option${i === active ? " is-active" : ""}`}
              onMouseDown={e => { e.preventDefault(); pick(o); }}
              onMouseEnter={() => setActive(i)}
            >
              {o.person ? (
                <>
                  <span className="picker-person">{o.person.name}</span>
                  <span className="picker-sub" title={personSub(o.person)}>{personSub(o.person)}</span>
                </>
              ) : (
                <>
                  <span className="picker-handle">@{o.account.handle || o.account.id}</span>
                  <span className="picker-sub" title={accountSub(o.account, query)}>{accountSub(o.account, query)}</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
