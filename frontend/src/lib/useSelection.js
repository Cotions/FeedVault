import { useCallback, useEffect, useState } from "react";
import { pruned, toggled } from "./selection.js";

/* Select mode over a list shown in order (Feed now; Trash, Duplicates, Tags
   later). Click toggles one item, shift-click adds the range from the last
   click, Escape leaves the mode. Items need an `id`.

   `resetKey`: when it changes the list shows different items, so the
   selection is dropped. `escapeBlocked`: true while a dialog owns Escape. */
export function useSelection(items, { resetKey, escapeBlocked = false } = {}) {
  const [active,   setActive]   = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [anchor,   setAnchor]   = useState(null);   // last clicked id, for shift ranges

  // Adjusting state during render, not in an effect.
  const [seenKey, setSeenKey] = useState(resetKey);
  if (seenKey !== resetKey) {
    setSeenKey(resetKey);
    if (selected.size) setSelected(new Set());
    setAnchor(null);
  }
  // Ids that left the list are forgotten, so one given to a new item later
  // does not come in checked.
  const kept = pruned(selected, items);
  if (kept) setSelected(kept);

  const clear = useCallback(() => {
    setSelected(new Set());
    setAnchor(null);
  }, []);

  const exit = useCallback(() => {
    setActive(false);
    clear();
  }, [clear]);

  useEffect(() => {
    if (!active) return;
    function onKey(e) {
      if (e.key === "Escape" && !escapeBlocked && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) exit();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, escapeBlocked, exit]);

  function toggle(index, shift) {
    const id = items[index]?.id;
    if (!id) return;
    setSelected(prev => toggled(prev, items, id, shift, anchor));
    setAnchor(id);
  }

  // After items left the list (deleted): forget them, keep the rest.
  function drop(ids) {
    const gone = new Set(ids);
    setSelected(prev => new Set([...prev].filter(id => !gone.has(id))));
    setAnchor(null);
  }

  // Only what is still on screen counts; the Set may hold ids that left.
  const selectedItems = items.filter(p => selected.has(p.id));

  return {
    active,
    enter: () => setActive(true),
    exit,
    isSelected: id => selected.has(id),
    toggle,
    selectAll: () => setSelected(new Set(items.map(p => p.id))),
    clear,
    drop,
    selectedItems,
    count: selectedItems.length,
  };
}
