import { useEffect, useId, useRef, useState } from "react";
import { personOptions } from "../lib/people";

/* Who a link goes to: a search field over an open list (not a dropdown:
   the list is the point), "No person (Unsorted)" and the recent people on
   top, everyone below. The arrows move the highlight, Enter picks it, a
   click picks the one clicked. Escape is left to the dialog around it.

   Props: people (as /api/people gives them), recent ([{ id, name }]),
   active (the highlighted option, { key, person }), onActive(option),
   onPick(option), inputRef, label, disabled */
export default function PersonPicker({ people, recent, active, onActive, onPick, inputRef, label = "Person", disabled = false }) {
  const [query, setQuery] = useState("");
  const listRef = useRef(null);
  const listId = useId();
  const options = personOptions(people, recent, query);
  const at = Math.max(0, options.findIndex(o => o.key === active?.key));

  // The highlight stays in view as the arrows move it through a long list.
  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${at}"]`)?.scrollIntoView({ block: "nearest" });
  }, [at]);

  function type(value) {
    setQuery(value);
    onActive(personOptions(people, recent, value)[0]);
  }

  function onKeyDown(e) {
    if (e.nativeEvent.isComposing) return;      // an input method's own keys
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? Math.min(options.length - 1, at + 1) : Math.max(0, at - 1);
      onActive(options[next]);
    } else if (e.key === "Enter") {
      e.preventDefault();                       // not the form's own submit as well
      if (options[at]) onPick(options[at]);
    }
  }

  const firstRest = options.findIndex(o => o.person && !o.recent);
  const anyRecent = options.some(o => o.recent);
  return (
    <div className="person-pick">
      <input
        ref={inputRef}
        type="text"
        className="person-pick-input"
        role="combobox"
        aria-label={label}
        aria-expanded="true"
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={options[at] ? `${listId}-${at}` : undefined}
        placeholder="Search people…"
        autoComplete="off"
        spellCheck={false}
        value={query}
        disabled={disabled}
        onChange={e => type(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <ul ref={listRef} className="person-pick-list" id={listId} role="listbox" aria-label={label} tabIndex={-1}>
        {query.trim() && options.length === 1 && <li className="picker-empty" role="presentation">No person matches “{query.trim()}”</li>}
        {options.map((o, i) => (
          <PickRow key={o.key} o={o} i={i} listId={listId} active={i === at} onPick={onPick} disabled={disabled}
                   head={!query.trim() && (o.recent && i === 1 ? "Recent" : i === firstRest && i > 0 ? (anyRecent ? "Everyone else" : "Everyone") : null)} />
        ))}
      </ul>
    </div>
  );
}

function PickRow({ o, i, listId, active, onPick, disabled, head }) {
  return (
    <>
      {head && <li className="person-pick-head" role="presentation">{head}</li>}
      <li
        id={`${listId}-${i}`}
        data-index={i}
        role="option"
        aria-selected={active}
        aria-disabled={disabled || undefined}
        className={`person-pick-option${active ? " is-active" : ""}${o.person ? "" : " is-none"}`}
        title={o.person ? o.person.name : undefined}
        onMouseDown={e => e.preventDefault()}
        onClick={() => { if (!disabled) onPick(o); }}
      >
        {o.person ? o.person.name : "No person (Unsorted)"}
      </li>
    </>
  );
}
