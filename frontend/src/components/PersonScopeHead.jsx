import CreatorPicker from "./CreatorPicker";
import PageHeader from "./PageHeader";

// The head of a page that can show one person (?person=<id>): its title,
// the person's name, and the picker that sets or clears it.
export default function PersonScopeHead({ title, person, people, setParams }) {
  const who = person ? (people || []).find(p => String(p.id) === person) : null;
  return (
    <PageHeader
      title={title}
      sub={person ? (who ? who.name : `person ${person}`) : null}
      actions={(people?.length > 0 || person) && (
        <CreatorPicker
          label="Person"
          allLabel="Everyone"
          placeholder="Search people…"
          people={people || []}
          value={person ? { person } : null}
          onChange={c => setParams(c ? { person: String(c.person.id) } : {}, { replace: true })}
        />
      )}
    />
  );
}
