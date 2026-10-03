import { personPath } from "./people";

/* Where a notifications entry leads: the posts its sync brought, or the
   source that failed (its person's page, else Creators). */
export function notificationPath(e) {
  if (e.kind === "new") return `/?notification=${e.id}`;
  return e.person_id ? personPath(e.person_id) : "/creators";
}

/* Whether this tab can show desktop notifications: the browser has the
   Notification API and the user granted it (asked only by the Settings
   switch, never on load). */
export function desktopAllowed() {
  return typeof Notification !== "undefined" && Notification.permission === "granted";
}
