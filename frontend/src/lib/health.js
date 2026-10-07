// Account health (docs/API.md "Account health"): what a source's syncs
// said about its account, as badges, lines and the Creators warning.
import { fmtAgo } from "./fmt.js";

// health.state → [badge, tone]; tone picks the chip's colours.
export const STATES = {
  ok: ["ok", "done"],
  renamed: ["renamed", "interrupted"],
  rate_limited: ["rate limited", "interrupted"],
  private: ["private", "failed"],
  login_required: ["login needed", "failed"],
  not_found: ["not found", "failed"],
  error: ["failed", "failed"],
};

/* The state badge, or null (no sync yet, or it worked). */
export function healthBadge(h) {
  if (!h?.state || h.state === "ok") return null;
  const [label, tone] = STATES[h.state] || STATES.error;
  return { label, tone };
}

/* "last good sync 3h ago", "no sync has worked yet", or "" before any. */
export function lastGood(h, now = Date.now()) {
  if (!h?.result) return "";
  return h.ok_at ? `last good sync ${fmtAgo(h.ok_at, now)}` : "no sync has worked yet";
}

/* The session the last sync used, as its output told: "" when none was
   used or the output said nothing. */
export function loginText(h) {
  const l = h?.login;
  if (!l || l.mode === "none") return "";
  const what = l.mode === "login" ? "saved login" : "browser cookies";
  if (l.found === false) return `${what} not found by the last sync`;
  if (l.accepted === false) return `${what} refused by the last sync`;
  if (l.accepted) return `${what} accepted by the last sync`;
  return l.found ? `${what} found by the last sync` : "";
}

/* Why a card's sources need a look (health.warning: not found, login
   needed, 3+ failures in a row; script_warning: its script would fail
   its next sync), one line each, or [] when none. */
export function warnings(sources, name = s => s.target) {
  return (sources || []).flatMap(s => [
    ...(s.health?.warning ? [`${name(s)}: ${s.health.warning}`] : []),
    ...(s.script_warning ? [`${name(s)}: script ${s.options?.script}: ${s.script_warning.reason}`] : []),
  ]);
}
