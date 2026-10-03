import { useSyncExternalStore } from "react";
import { getTags } from "./api";
import { foldTag } from "./tags";

/* Tag colours for every chip on screen (folded name → "#rrggbb"). Pages
   that read /api/tags anyway (Feed, Review, a post, Tags) hand their answer
   over with setTagColors, so colours follow a rescan or a change; chips
   elsewhere read it once themselves, and again after a failed read. */
let colors = new Map();
let state = "idle";                 // idle | loading | loaded
const listeners = new Set();

function publish(list) {
  colors = new Map(list.filter(t => t.color).map(t => [foldTag(t.name), t.color]));
  state = "loaded";
  listeners.forEach(l => l());
}

function load() {
  // After the page's own effects: one that hands over its list saves the read.
  setTimeout(() => {
    if (state !== "idle") return;
    state = "loading";
    getTags().then(publish, () => { state = "idle"; setTimeout(() => { if (listeners.size) load(); }, 5000); });
  }, 0);
}

// A fresh /api/tags answer (the Tags page has one after each change).
export function setTagColors(list) { if (Array.isArray(list)) publish(list); }

function subscribe(listener) {
  listeners.add(listener);
  if (state === "idle") load();
  return () => listeners.delete(listener);
}

export function useTagColors() {
  return useSyncExternalStore(subscribe, () => colors);
}
