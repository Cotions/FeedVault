import { useSyncExternalStore } from "react";
import { getOpenBrowsers } from "./api";

/* The browsers a link can open in and the saved default, for every link
   row on screen (GET /api/open/browsers, read once, and again after a
   failed read) and Settings' Open links in. Settings hands a saved default
   over with setOpenDefault, so the rows follow it at once.

   ``browsers`` is null until read; ``default`` null is this browser (a
   plain link, as before). */
let state = { browsers: null, default: null };
let status = "idle";                // idle | loading | loaded
const listeners = new Set();

function publish(next) {
  state = { ...state, ...next };
  listeners.forEach(l => l());
}

function load() {
  if (status !== "idle") return;
  status = "loading";
  getOpenBrowsers().then(
    r => { status = "loaded"; publish({ browsers: r?.browsers || [], default: r?.default ?? null }); },
    () => { status = "idle"; setTimeout(() => { if (listeners.size) load(); }, 5000); },
  );
}

// The default just saved in Settings (POST /api/config's open_links).
export function setOpenDefault(value) { publish({ default: value ?? null }); }

// Read the browsers again (Settings' Check again: one installed since).
export function reloadOpenLinks() {
  if (status === "loading") return;
  status = "idle";
  load();
}

function subscribe(listener) {
  listeners.add(listener);
  load();
  return () => listeners.delete(listener);
}

export function useOpenLinks() {
  return useSyncExternalStore(subscribe, () => state);
}
