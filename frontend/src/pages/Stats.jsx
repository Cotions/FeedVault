import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { getPeople, getStats } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { fmtBytes, fmtInt, platformLabel } from "../lib/fmt";
import Bars from "../components/Bars";
import StatsHero from "../components/StatsHero";
import CreatorPicker from "../components/CreatorPicker";

export default function Stats() {
  const { refreshKey } = useScan();
  const [params, setParams] = useSearchParams();
  const person = params.get("person") || "";
  const load = useCallback(() => getStats(person || undefined), [person]);
  const { data: s, error } = useApi(load, refreshKey);
  const people = useApi(getPeople, refreshKey);
  const who = person ? (people.data || []).find(p => String(p.id) === person) : null;
  // Links into the Feed and Review keep the person.
  const scoped = (path, more = {}) => {
    const q = new URLSearchParams({ ...(person ? { person } : {}), ...more }).toString();
    return q ? `${path}?${q}` : path;
  };

  const head = (
    <div className="page-head page-head-bare">
      <h2 className="page-title">Stats</h2>
      {person && <span className="page-count">{who ? who.name : `person ${person}`}</span>}
      <div className="page-head-spacer" />
      {(people.data?.length > 0 || person) && (
        <CreatorPicker
          className="storage-person"
          label="Person"
          allLabel="Everyone"
          placeholder="Search people…"
          people={people.data || []}
          value={person ? { person } : null}
          onChange={c => setParams(c ? { person: String(c.person.id) } : {}, { replace: true })}
        />
      )}
    </div>
  );

  if (!s) {
    return (
      <div className="stats-page">
        {head}
        <div className="card"><div className="empty">{error ? `Could not load stats: ${error.message}` : "Loading…"}</div></div>
      </div>
    );
  }

  const hero = [
    { num: fmtInt(s.posts),   label: "posts" },
    { num: fmtInt(s.media),   label: "media files" },
    { num: fmtInt(s.authors), label: "creators", to: "/creators" },
    { num: fmtBytes(s.bytes), label: "on disk" },
    { num: fmtInt(s.missing), label: "missing", warn: s.missing > 0 },
    { num: fmtInt(s.unmatched), label: "unmatched files", warn: s.unmatched > 0, to: "/unmatched" },
    { num: fmtInt(s.kept),       label: "kept", to: scoped("/", { review: "kept" }) },
    { num: fmtInt(s.unreviewed), label: "to review", to: scoped("/review") },
  ];
  const sortDesc = obj => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="stats-page">
      {head}
      <StatsHero cells={hero} />
      <div className="stats-split">
        <Bars
          title="Posts per platform"
          entries={sortDesc(s.by_platform)}
          label={platformLabel}
          linkFor={p => scoped("/", { platform: p })}
          delay={500}
        />
        <Bars
          title="Posts per kind"
          entries={sortDesc(s.by_kind)}
          label={k => k}
          linkFor={k => scoped("/", { kind: k })}
          delay={600}
        />
      </div>
    </div>
  );
}
