import Icon from "./Icon";

// The "Filters" button that folds a page's filters on a phone (hidden on a
// desktop by the CSS), with a chip counting the active ones.
export default function FiltersToggle({ open, onToggle, active, controls }) {
  return (
    <button type="button" className="btn-secondary filters-toggle" aria-expanded={open}
            aria-controls={controls} onClick={onToggle}>
      <Icon name="filter" size={14} />Filters{active > 0 && <span className="chip">{active}</span>}
      <Icon name="chevDown" size={14} className="filters-toggle-chev" />
    </button>
  );
}
