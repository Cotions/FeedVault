import { useSyncExternalStore } from "react";
import { getTags } from "./api";
import { foldTag } from "./tags";

/* Tag colours for every chip on screen: one /api/tags read shared by all
   of them (folded name → "#rrggbb"), read again after a change. */
let colors = new Map();
let state = "idle";                 // idle | loading | loaded
const listeners = new Set();

function publish(list) {
  colors = new Map(list.filter(t => t.color).map(t => [foldTag(t.name), t.color]));
  state = "loaded";
  listeners.forEach(l => l());
}

function load() {
  state = "loading";
  getTags().then(publish, () => { state = "idle"; });
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
