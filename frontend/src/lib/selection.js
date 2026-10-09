/* Pure steps of useSelection, kept apart so node tests can run them. */

/* The selection after a click on `id`. A shift-click adds every item from
   the anchor (the last clicked id) to it, both ends in; the anchor is looked
   up now, so rows added or removed since the last click do not shift the
   range. Without shift, or when the anchor left the list, it toggles `id`. */
export function toggled(selected, items, id, shift, anchorId) {
  const next = new Set(selected);
  const at = items.findIndex(p => p.id === id);
  if (at < 0) return next;
  const from = shift && anchorId != null ? items.findIndex(p => p.id === anchorId) : -1;
  if (from >= 0) {
    const [a, b] = from < at ? [from, at] : [at, from];
    for (let i = a; i <= b; i++) next.add(items[i].id);
  } else if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  return next;
}

/* `selected` without the ids no item has any more, or null when nothing
   left: an id can be given to a new item later (links), which must not come
   in checked. */
export function pruned(selected, items) {
  if (!selected.size) return null;
  const here = new Set(items.map(p => p.id));
  for (const id of selected) {
    if (!here.has(id)) return new Set([...selected].filter(x => here.has(x)));
  }
  return null;
}
