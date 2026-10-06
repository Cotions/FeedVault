// The head every page opens on (#93): its title, an optional count or
// subtitle, and its page-level actions, above the page's cards and flush
// with the main column. A back link goes first among the actions, never
// before the title, so the title starts at the same place on every page.
// ``heading`` replaces the title while it is edited (a rename form);
// ``sub`` is a string (shown as the page's count) or the spans to show.
export default function PageHeader({ title, sub, back, actions, heading, className }) {
  const subNode = sub == null || sub === false ? null
    : typeof sub === "object" ? sub : <span className="page-count">{sub}</span>;
  return (
    <div className={className ? `page-head ${className}` : "page-head"}>
      <div className="page-head-text">
        {heading || <h2 className="page-title" title={typeof title === "string" ? title : undefined}>{title}</h2>}
        {subNode}
      </div>
      {(back || actions) && <div className="page-head-actions">{back}{actions}</div>}
    </div>
  );
}
