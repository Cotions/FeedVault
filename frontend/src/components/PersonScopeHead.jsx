import CreatorPicker from "./CreatorPicker";

// The head of a page that can show one person (?person=<id>): its title,
// the person's name, and the picker that sets or clears it.
export default function PersonScopeHead({ title, person, people, setParams }) {
  const who = person ? (people || []).find(p => String(p.id) === person) : null;
  return (
    <div className="page-head page-head-bare">
      <h2 className="page-title">{title}</h2>
      {person && <span className="page-count">{who ? who.name : `person ${person}`}</span>}
      <div className="page-head-spacer" />
      {(people?.length > 0 || person) && (
        <CreatorPicker
          className="storage-person"
          label="Person"
          allLabel="Everyone"
          placeholder="Search people…"
          people={people || []}
          value={person ? { person } : null}
          onChange={c => setParams(c ? { person: String(c.person.id) } : {}, { replace: true })}
        />
      )}
    </div>
  );
}
