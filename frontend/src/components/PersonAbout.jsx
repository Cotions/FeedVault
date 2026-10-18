import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Icon from "./Icon";
import TagInput from "./TagInput";
import { cleanGender, countryFlag, countryName, countryOptions, GENDER_PRESETS, MAX_GENDER, withLabel } from "../lib/people";
import { usePopoverUnder } from "../lib/usePopoverUnder";

const MAX_LABELS = 50;

/* A country field: type a name or a code, ↑/↓ through the matches, Enter
   picks, Esc puts the field back. Shows the country picked, flag first.
   Props: codes (every code it may be), value ("" for none), onChange(code). */
export function CountryPicker({ codes, value, onChange, id }) {
  const [text,   setText]   = useState(null);           // what is typed, null when not searching
  const [active, setActive] = useState(0);
  const fieldRef = useRef(null);
  const listRef = useRef(null);
  const listId = useId();
  const options = useMemo(() => (text == null ? [] : countryOptions(codes, text)), [codes, text]);
  const shown = text != null && options.length > 0;
  usePopoverUnder(shown, listRef, fieldRef, options);
  useEffect(() => {
    if (shown) listRef.current?.querySelector(`[aria-selected="true"]`)?.scrollIntoView({ block: "nearest" });
  }, [shown, active]);

  function pick(code) {
    onChange(code);
    setText(null);
  }

  function onKeyDown(e) {
    if (["Escape", "Enter", "ArrowDown", "ArrowUp"].includes(e.key) && (text != null || e.key !== "Escape")) e.stopPropagation();
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (text == null) { setText(""); setActive(0); return; }
      const n = options.length;
      if (n) setActive(i => (e.key === "ArrowDown" ? (i + 1) % n : (i - 1 + n) % n));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (shown) pick(options[Math.min(active, options.length - 1)].code);
    } else if (e.key === "Escape" && text != null) {
      e.preventDefault();
      setText(null);
    }
  }

  const label = value ? `${countryFlag(value)} ${countryName(value)}` : "";
  return (
    <div className="country-picker">
      <input
        ref={fieldRef}
        id={id}
        type="text"
        className="page-filter"
        role="combobox"
        aria-expanded={shown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={shown ? `${listId}-${Math.min(active, options.length - 1)}` : undefined}
        placeholder={value ? undefined : "Type a country…"}
        value={text ?? label}
        onFocus={e => e.target.select()}
        onChange={e => { setText(e.target.value); setActive(0); }}
        onBlur={() => setText(null)}
        onKeyDown={onKeyDown}
      />
      {value && (
        <button type="button" className="icon-btn country-clear" onClick={() => onChange("")}
                title="No country" aria-label="Clear the country">
          <Icon name="close" size={13} />
        </button>
      )}
      {shown && (
        <ul ref={listRef} className="tag-suggest country-suggest" id={listId} role="listbox" popover="manual">
          {options.map((c, i) => (
            <li key={c.code} id={`${listId}-${i}`} role="option" aria-selected={i === active}
                className={i === active ? "is-active" : ""}
                // mousedown, not click: the input would blur first and close the list
                onMouseDown={e => { e.preventDefault(); pick(c.code); }}>
              <span><span className="country-flag" aria-hidden="true">{c.flag}</span> {c.name}</span>
              <span className="tag-suggest-count">{c.code}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* A person's gender, country and labels, edited together and saved as
   the notes are: nothing is sent until Save (Enter in the gender field,
   or in an empty label field, saves too).

   Props: person, attributes (GET /api/people/attributes, or null while it
   loads), draft ({ gender, country, labels }, each null when untouched),
   onDraft(changes), dirty, busy, onSave(), onDiscard(). */
export default function PersonAbout({ person: p, attributes, draft, onDraft, dirty, busy, onSave, onDiscard }) {
  const ids = useId();
  const gender = draft.gender ?? p.gender;
  const country = draft.country ?? p.country;
  const labels = draft.labels ?? p.labels;
  const presets = GENDER_PRESETS;
  const isPreset = g => cleanGender(gender) === g;
  // Genders others have, beyond the presets: suggested as they are typed.
  const used = (attributes?.genders || []).map(g => g.name).filter(g => !presets.some(x => x.toLowerCase() === g.toLowerCase()));

  return (
    <section className="person-section person-about">
      <h3 className="card-title">About</h3>
      <form className="person-about-form" onSubmit={e => { e.preventDefault(); if (dirty && !busy) onSave(); }}>
        <div className="about-field">
          <label className="about-label" htmlFor={`${ids}-gender`}>Gender</label>
          <div className="about-gender">
            <div className="links-tabs about-presets" role="group" aria-label="Gender presets">
              {presets.map(g => (
                <button key={g} type="button" className={`links-tab${isPreset(g) ? " is-on" : ""}`} aria-pressed={isPreset(g)}
                        onClick={() => onDraft({ gender: isPreset(g) ? "" : g })}>
                  {g}
                </button>
              ))}
            </div>
            <input
              id={`${ids}-gender`}
              type="text"
              className="page-filter about-gender-input"
              list={`${ids}-genders`}
              maxLength={MAX_GENDER}
              placeholder="Or type another…"
              value={gender}
              onChange={e => onDraft({ gender: e.target.value })}
            />
            <datalist id={`${ids}-genders`}>
              {used.map(g => <option key={g} value={g} />)}
            </datalist>
          </div>
        </div>

        <div className="about-field">
          <label className="about-label" htmlFor={`${ids}-country`}>Country</label>
          <CountryPicker id={`${ids}-country`} codes={attributes?.countries || []} value={country}
                         onChange={code => onDraft({ country: code })} />
        </div>

        <div className="about-field about-labels">
          <span className="about-label" id={`${ids}-labels`}>Labels</span>
          <div className="about-labels-edit">
            {labels.length > 0 && (
              <ul className="tag-chips label-chips" aria-labelledby={`${ids}-labels`}>
                {labels.map(name => (
                  <li key={name} className="tag-chip label-chip">
                    <Link to={`/creators?${new URLSearchParams({ label: name })}`} title={`Creators labelled ${name}`}>
                      <span className="tag-chip-name">{name}</span>
                    </Link>
                    <button type="button" className="tag-chip-x" disabled={busy}
                            onClick={() => onDraft({ labels: labels.filter(l => l !== name) })}
                            title={`Remove ${name}`} aria-label={`Remove label ${name}`}>
                      <Icon name="close" size={10} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {labels.length < MAX_LABELS && (
              <TagInput
                tags={attributes?.labels || []}
                exclude={labels}
                placeholder="Add a label…"
                onAdd={name => onDraft({ labels: withLabel(labels, name) })}
                onEmptyEnter={() => { if (dirty && !busy) onSave(); }}
                onClose={() => {}}
              />
            )}
          </div>
        </div>

        {dirty && (
          <div className="person-notes-actions">
            <button type="submit" className="btn-primary" disabled={busy}>Save</button>
            <button type="button" className="btn-ghost" disabled={busy} onClick={onDiscard}>Discard</button>
          </div>
        )}
      </form>
    </section>
  );
}
