import { useId, useMemo, useRef, useState } from "react";
import { platformLabel, platformShort } from "../lib/fmt";
import { accountKey, accountText, matchedFormer, matches, personText } from "../lib/people";

const MAX_SHOWN = 60;

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

  const current = value?.person != null
    ? (people || []).find(p => p.id === Number(value.person))
    : value?.id != null ? accounts.find(a => a.id === value.id && (!value.platform || a.platform === value.platform)) : null;
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
        <ul className="picker-list" id={listId} role="listbox" aria-label={label}>
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
                  <span className="picker-sub">
                    {o.person.platforms.map(platformShort).join(" · ") || "no account"} · {o.person.count}
                  </span>
                </>
              ) : (
                <>
                  <span className="picker-handle">@{o.account.handle || o.account.id}</span>
                  <span className="picker-sub">
                    {platformLabel(o.account.platform)} · {o.account.count}
                    {matchedFormer(o.account, query) && <> · was @{matchedFormer(o.account, query)}</>}
                    {o.account.person && <> · {o.account.person.name}</>}
                  </span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
