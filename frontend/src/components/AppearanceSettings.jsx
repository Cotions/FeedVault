import { useEffect, useState } from "react";
import Icon from "./Icon";
import {
  PRESETS, ROLES, DEFAULT_THEME_ID, PHOSPHOR_PINS, applyTheme, getActiveId, getActiveTheme,
  getCustomThemes, palette, saveCustomThemes, setActiveTheme, themeColors,
} from "../lib/theme";

// Which palette variable each pinnable role shows when left on auto.
const ROLE_VAR = {
  fill: "--accent", buttonText: "--on-accent", glow: "--glow",
  glowText: "--on-glow", highlight: "--accent-text",
};

function ThemeCard({ theme, active, onPick, onCopy, onEdit, onDelete }) {
  const c = themeColors(theme);
  return (
    <div className={`theme-card${active ? " active" : ""}`} onClick={onPick} role="button" tabIndex={0}
         aria-pressed={active} aria-label={`${theme.name} theme`}
         onKeyDown={e => e.target === e.currentTarget && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onPick())}>
      <div className="theme-card-art">
        <span className="theme-orb" style={{ "--orb-a": c["--glow"], "--orb-b": c["--accent"] }} />
        <span className="theme-sample" style={{ background: `linear-gradient(180deg, ${c["--accent-hover"]}, ${c["--accent"]})`, color: c["--on-accent"] }}>Aa</span>
        <span className="theme-sample-text" style={{ color: c["--accent-text"] }}>42</span>
      </div>
      <div className="theme-card-foot">
        <span className="theme-card-name">{theme.name}</span>
        <span className="theme-card-actions" onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
          {onEdit && (
            <button type="button" className="icon-btn-sm" onClick={onEdit} title="Edit theme" aria-label={`Edit ${theme.name}`}>
              <Icon name="pencil" size={13} />
            </button>
          )}
          <button type="button" className="icon-btn-sm" onClick={onCopy} title="Duplicate as a custom theme" aria-label={`Duplicate ${theme.name}`}>
            <Icon name="copy" size={13} />
          </button>
          {onDelete && (
            <button type="button" className="icon-btn-sm danger" onClick={onDelete} title="Delete theme" aria-label={`Delete ${theme.name}`}>
              <Icon name="trash" size={13} />
            </button>
          )}
        </span>
      </div>
    </div>
  );
}

function ThemeEditor({ initial, onSave, onCancel }) {
  const [draft, setDraft] = useState(initial);

  // Paint the page live while editing. However the editor goes away (Save,
  // Cancel, another tab, leaving Settings) the saved theme is put back.
  useEffect(() => {
    applyTheme({ ...initial, id: initial.id || "draft" });
    return () => applyTheme(getActiveTheme());
  }, [initial]);

  function update(next) {
    setDraft(next);
    applyTheme({ ...next, id: next.id || "draft" });
  }

  const derived = palette({ base: draft.base });
  const current = palette(draft);

  return (
    <div className="theme-editor">
      <div className="theme-editor-head">
        <input
          type="text"
          value={draft.name}
          onChange={e => setDraft({ ...draft, name: e.target.value })}
          placeholder="Theme name"
          aria-label="Theme name"
          maxLength={32}
        />
      </div>

      <div className="theme-role">
        <label className="theme-role-swatch" style={{ background: draft.base }}>
          <input type="color" value={draft.base} aria-label="Base colour" onChange={e => update({ ...draft, base: e.target.value })} />
        </label>
        <div className="theme-role-label">
          <span>Base colour</span>
          <small>Everything below follows it unless pinned</small>
        </div>
      </div>

      {ROLES.map(r => {
        const pinned = !!draft[r.key];
        const value = current[ROLE_VAR[r.key]];
        return (
          <div key={r.key} className={`theme-role${pinned ? " pinned" : ""}`}>
            <label className="theme-role-swatch" style={{ background: value }}>
              <input type="color" value={value} aria-label={r.label} onChange={e => update({ ...draft, [r.key]: e.target.value })} />
            </label>
            <div className="theme-role-label">
              <span>{r.label}</span>
              <small>{r.hint}</small>
            </div>
            {pinned ? (
              <button type="button" className="btn-ghost" onClick={() => { const rest = { ...draft }; delete rest[r.key]; update(rest); }}
                      title={`Back to ${derived[ROLE_VAR[r.key]]}, worked out from the base colour`}>
                Use auto
              </button>
            ) : (
              <span className="theme-role-auto">auto</span>
            )}
          </div>
        );
      })}

      <div className="theme-preview" aria-hidden="true">
        <button type="button" className="btn-primary" tabIndex={-1}>Rescan now</button>
        <span className="side-badge">12 new</span>
        <span className="chip chip-new">unsaved</span>
        <span className="theme-preview-play"><Icon name="play" size={14} /></span>
        <span className="status-dot online" />
      </div>

      <div className="theme-editor-foot">
        <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
        <button type="button" className="btn-primary" onClick={() => onSave({ ...draft, name: draft.name.trim() || "Custom" })}>Save theme</button>
      </div>
    </div>
  );
}

/* Settings → Appearance: preset and custom accent themes, per browser. */
export default function AppearanceSettings() {
  const [custom,   setCustom]   = useState(getCustomThemes);
  const [activeId, setActiveId] = useState(getActiveId);
  const [editing,  setEditing]  = useState(null);

  function pick(theme) {
    setActiveTheme(theme);
    setActiveId(theme.id);
  }

  function copyOf(theme) {
    const rest = { ...theme };
    delete rest.id;
    const pins = theme.id === DEFAULT_THEME_ID ? PHOSPHOR_PINS : {};
    setEditing({ ...rest, ...pins, name: `${theme.name} copy`.slice(0, 32) });
  }

  function save(theme) {
    const t = theme.id ? theme : { ...theme, id: `c-${Date.now().toString(36)}` };
    const list = custom.some(c => c.id === t.id) ? custom.map(c => (c.id === t.id ? t : c)) : [...custom, t];
    saveCustomThemes(list);
    setCustom(list);
    setEditing(null);
    pick(t);
  }

  function remove(theme) {
    const list = custom.filter(c => c.id !== theme.id);
    saveCustomThemes(list);
    setCustom(list);
    if (activeId === theme.id) pick(PRESETS[0]);
  }

  return (
    <div className="card">
      <div className="theme-section-head">
        <div className="card-title">Themes</div>
        {!editing && (
          <button type="button" className="btn-secondary" onClick={() => copyOf(getActiveTheme())}>
            <Icon name="plus" size={14} />Create theme
          </button>
        )}
      </div>

      {editing ? (
        <ThemeEditor key={editing.id || "new"} initial={editing} onSave={save} onCancel={() => setEditing(null)} />
      ) : (
        <>
          <div className="theme-grid">
            {PRESETS.map(t => (
              <ThemeCard key={t.id} theme={t} active={t.id === activeId} onPick={() => pick(t)} onCopy={() => copyOf(t)} />
            ))}
          </div>

          {custom.length > 0 && (
            <>
              <div className="card-title card-title-mine">Your themes</div>
              <div className="theme-grid">
                {custom.map(t => (
                  <ThemeCard
                    key={t.id}
                    theme={t}
                    active={t.id === activeId}
                    onPick={() => pick(t)}
                    onCopy={() => copyOf(t)}
                    onEdit={() => setEditing(t)}
                    onDelete={() => remove(t)}
                  />
                ))}
              </div>
            </>
          )}

          <p className="page-lede theme-note">
            Themes recolour icons, buttons, highlights and glows. The dark background stays the same, and
            success messages stay green. Duplicate any theme to pin exact colours for each part, including
            button text. Saved in this browser.
          </p>
        </>
      )}
    </div>
  );
}
