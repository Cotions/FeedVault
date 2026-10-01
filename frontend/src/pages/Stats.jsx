import { getStats } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useScan } from "../lib/scan";
import { fmtBytes, fmtInt, platformLabel } from "../lib/fmt";
import Bars from "../components/Bars";
import StatsHero from "../components/StatsHero";

export default function Stats() {
  const { refreshKey } = useScan();
  const { data: s, error } = useApi(getStats, refreshKey);

  if (!s) {
    return (
      <div className="stats-page">
        <div className="page-head page-head-bare"><h2 className="page-title">Stats</h2></div>
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
    { num: fmtInt(s.kept),       label: "kept", to: "/?review=kept" },
    { num: fmtInt(s.unreviewed), label: "to review", to: "/review" },
  ];
  const sortDesc = obj => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="stats-page">
      <div className="page-head page-head-bare"><h2 className="page-title">Stats</h2></div>
      <StatsHero cells={hero} />
      <div className="stats-split">
        <Bars
          title="Posts per platform"
          entries={sortDesc(s.by_platform)}
          label={platformLabel}
          linkFor={p => `/?platform=${encodeURIComponent(p)}`}
          delay={500}
        />
        <Bars
          title="Posts per kind"
          entries={sortDesc(s.by_kind)}
          label={k => k}
          linkFor={k => `/?kind=${encodeURIComponent(k)}`}
          delay={600}
        />
      </div>
    </div>
  );
}
