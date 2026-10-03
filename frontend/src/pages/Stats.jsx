import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { getPeople, getStats } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { fmtBytes, fmtInt, platformLabel } from "../lib/fmt";
import Bars from "../components/Bars";
import StatsHero from "../components/StatsHero";
import PersonScopeHead from "../components/PersonScopeHead";

export default function Stats() {
  const { refreshKey } = useScan();
  const [params, setParams] = useSearchParams();
  const person = params.get("person") || "";
  const load = useCallback(() => getStats(person || undefined), [person]);
  const { data: s, error } = useApi(load, refreshKey);
  const people = useApi(getPeople, refreshKey);
  // Links into the Feed and Review keep the person.
  const scoped = (path, more = {}) => {
    const q = new URLSearchParams({ ...(person ? { person } : {}), ...more }).toString();
    return q ? `${path}?${q}` : path;
  };

  const head = <PersonScopeHead title="Stats" person={person} people={people.data} setParams={setParams} />;

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
