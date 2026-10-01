import { useEffect, useId, useMemo, useRef, useState } from "react";
import { cleanName, foldTag, sameTag } from "../lib/tags";

const MAX_SUGGESTIONS = 8;

/* A tag name field with suggestions from the existing tags. Enter adds the
   highlighted suggestion, or the text as typed (a new tag); ↑/↓ move through
   the list (↓ also opens it on an empty field), Tab completes, Esc calls onClose. Enter on an empty field calls
   onEmptyEnter (default: onClose).

   Props: tags ([{ name, count }]), exclude (names already there), onAdd(name),
          onClose, onEmptyEnter, autoFocus, placeholder, inputRef */
export default function TagInput({ tags = [], exclude = [], onAdd, onClose, onEmptyEnter, autoFocus = false,
                                   placeholder = "Add a tag…", inputRef }) {
  const [text,   setText]   = useState("");
  const [active, setActive] = useState(-1);
  const [open,   setOpen]   = useState(false);
  const ownRef = useRef(null);
  const ref = inputRef || ownRef;
  const listId = useId();

  useEffect(() => { if (autoFocus) ref.current?.focus(); }, [autoFocus, ref]);

  const suggestions = useMemo(() => {
    const t = text.trim().toLowerCase();
    const skip = new Set(exclude.map(foldTag));
    const list = tags.filter(x => !skip.has(foldTag(x.name)) && (!t || x.name.toLowerCase().includes(t)));
    // Names starting with the text first, then the most used.
    if (t) list.sort((a, b) => (b.name.toLowerCase().startsWith(t) - a.name.toLowerCase().startsWith(t)) || b.count - a.count);
    return list.slice(0, MAX_SUGGESTIONS);
  }, [tags, exclude, text]);
  const exact = suggestions.find(x => sameTag(x.name, text.trim()));
  const shown = open && suggestions.length > 0;

  function add(name) {
    const clean = cleanName(name);
    if (!clean) return;
    onAdd(clean);
    setText("");
    setActive(-1);
    setOpen(false);
  }

  function onKeyDown(e) {
    // Handled here, not by a dialog or page around it; Tab still moves focus
    // (a dialog's focus trap needs it) unless it completes a name.
    const completes = e.key === "Tab" && shown && text.trim();
    if (["Escape", "Enter", "ArrowDown", "ArrowUp"].includes(e.key) || completes) e.stopPropagation();
    if (e.key === "Escape") {
      e.preventDefault();
      if (shown && text) { setOpen(false); return; }
      onClose?.();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (shown && active >= 0 && suggestions[active]) add(suggestions[active].name);
      else if (text.trim()) add(exact ? exact.name : text);
      else (onEmptyEnter || onClose)?.();
    } else if (e.key === "ArrowDown" && suggestions.length) {
      e.preventDefault();
      setOpen(true);
      setActive(i => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp" && suggestions.length) {
      e.preventDefault();
      setActive(i => (i <= 0 ? suggestions.length - 1 : i - 1));
    } else if (completes) {
      e.preventDefault();
      setText(suggestions[Math.max(0, active)].name);
      setActive(-1);
    }
  }

  return (
    <div className="tag-input">
      <input
        ref={ref}
        type="text"
        value={text}
        placeholder={placeholder}
        aria-label={placeholder}
        role="combobox"
        aria-expanded={shown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={shown && active >= 0 ? `${listId}-${active}` : undefined}
        maxLength={64}
        onChange={e => { setText(e.target.value); setActive(-1); setOpen(true); }}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {shown && (
        <ul className="tag-suggest" id={listId} role="listbox">
          {suggestions.map((x, i) => (
            <li
              key={x.name}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? "is-active" : ""}
              // mousedown, not click: the input would blur first and close the list
              onMouseDown={e => { e.preventDefault(); add(x.name); }}
            >
              <span>{x.name}</span><span className="tag-suggest-count">{x.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
