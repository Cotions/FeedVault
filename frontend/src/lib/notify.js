import { personPath } from "./people.js";

/* Where a notifications entry leads: the posts its sync brought, or the
   source that failed (its person's page, else that source on Creators,
   which scrolls to it and lights it up). */
export function notificationPath(e) {
  if (e.kind === "new") return `/?notification=${e.id}`;
  if (e.person_id) return personPath(e.person_id);
  return e.source_id != null ? `/creators?${new URLSearchParams({ source: e.source_id })}` : "/creators";
}

/* Whether this tab can show desktop notifications: the browser has the
   Notification API and the user granted it (asked only by the Settings
   switch, never on load). */
export function desktopAllowed() {
  return typeof Notification !== "undefined" && Notification.permission === "granted";
}
