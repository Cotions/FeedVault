import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

/* Windowed rendering for the long list pages (Storage, Unmatched, Links,
   Creators): only the rows near the viewport are in the DOM, with a spacer
   for the rest above and below, so a vault of thousands of rows renders,
   sorts and filters a few dozen. The page scrolls as before (the window,
   not a box of its own), and a list shorter than WINDOW_FROM renders whole.

   Rows are grouped in lines: one row a line in a table or a list, a grid's
   row of cards in a grid (``grid: true``, its column count read from the
   CSS). Each line's height is measured once rendered; a line not yet
   rendered is taken as the average of those measured.

   What stays as a full list had it:
   - keyboard: the line holding focus is always rendered, so focus never
     falls to <body> when its row scrolls away; Tab and Shift+Tab reach the
     next row because the lines just past the viewport are rendered too, and
     the browser scrolls each focused one into view;
   - ``pins``: rows that must stay rendered (a row being edited, whose form
     holds unsaved text);
   - ``scrollTo(i)``: brings row i into view and centres it, for the links
     that point at one row (Links' flash, Creators' ?source=);
   - the find bar (Ctrl+F) only sees rendered rows: every windowed page has
     a search box over all of its rows instead. */

// Below this many rows a list renders whole: the window is not worth it.
export const WINDOW_FROM = 120;
// How far past the viewport, above and below, lines are rendered (px).
const OVERSCAN = 800;

// offsets[i]: where line i starts, from the list's top; offsets[n]: the end.
export function lineOffsets(pitches) {
  const out = new Array(pitches.length + 1);
  out[0] = 0;
  for (let i = 0; i < pitches.length; i++) out[i + 1] = out[i] + pitches[i];
  return out;
}

// The line at ``y`` (list coordinates): the last one starting at or above it.
export function lineAt(offsets, y) {
  let lo = 0, hi = offsets.length - 2;
  if (hi < 0) return 0;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (offsets[mid] <= y) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// The lines meeting [top, bottom] (list coordinates), as [first, end).
export function visibleLines(offsets, top, bottom) {
  const n = offsets.length - 1;
  if (n <= 0) return [0, 0];
  const first = lineAt(offsets, Math.max(0, top));
  const end = Math.min(n, lineAt(offsets, Math.max(0, bottom)) + 1);
  return [first, Math.max(first + 1, end)];
}

// What to render, in order: { line } for a line, { gap, key } for a spacer
// standing for the lines between. ``gap`` is the CSS gap between lines (a
// flex or grid gap), which the spacer gets too, so it is left out of it.
export function segments(lines, [first, end], pins, offsets, gap = 0) {
  const shown = new Set();
  for (let l = first; l < Math.min(end, lines); l++) shown.add(l);
  for (const p of pins) if (p >= 0 && p < lines) shown.add(p);
  const out = [];
  let at = 0;
  for (const l of [...shown].sort((a, b) => a - b)) {
    if (l > at) out.push({ gap: Math.max(0, offsets[l] - offsets[at] - gap), key: `gap:${at}` });
    out.push({ line: l });
    at = l + 1;
  }
  if (at < lines) out.push({ gap: Math.max(0, offsets[lines] - offsets[at] - gap), key: `gap:${at}` });
  return out;
}

/* The hook. ``count`` rows; ``estimate``: a row's height before any is
   measured. Put ``listRef`` and ``focusProps`` on the element whose
   children are the rows (a <tbody>, <ul> or grid), ``data-index={i}`` on
   each row, and build its children with ``render(row, gap)``: row(i) for
   each row to show, gap(height, key) for a spacer element of that height.
   ``settled`` turns true once the list scrolled: rows mounted from then on
   should not play an entry animation (they would blink in while
   scrolling). */
export function useWindow(count, { estimate = 48, grid = false, pins = [], from = WINDOW_FROM } = {}) {
  const on = count >= from;
  const listRef = useRef(null);
  const [cols, setCols] = useState(1);
  const lines = on ? Math.ceil(count / cols) : 0;
  // Each measured line's pitch (its height and the gap after it), and the gap.
  const [meas, setMeas] = useState(() => ({ gap: 0, pitch: new Map() }));
  const [range, setRange] = useState(() => [0, Math.ceil((900 + OVERSCAN) / estimate)]);
  const [settled, setSettled] = useState(false);
  const [focusRow, setFocusRow] = useState(-1);   // the row holding focus, or -1
  const [wanted, setWanted] = useState(null);     // a row scrollTo asked for, until centred

  const offsets = useMemo(() => {
    let sum = 0, n = 0;
    for (const [l, p] of meas.pitch) if (l < lines) { sum += p; n++; }
    const avg = n ? sum / n : estimate + meas.gap;
    const pitches = new Array(lines);
    for (let l = 0; l < lines; l++) pitches[l] = meas.pitch.get(l) ?? avg;
    return lineOffsets(pitches);
  }, [lines, estimate, meas]);
  const offsetsRef = useRef(offsets);

  // The lines the viewport meets, plus the overscan, from where the list is now.
  const update = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top;
    const next = visibleLines(offsetsRef.current, -top - OVERSCAN, window.innerHeight - top + OVERSCAN);
    setRange(r => (r[0] === next[0] && r[1] === next[1] ? r : next));
  }, [setRange]);

  // After every render: the grid's columns, the rendered lines' heights,
  // then the range again; a row scrollTo asked for, once it is in the DOM.
  // On every render on purpose: any of the caller's changes (a row opened
  // for editing, new data) may change a row's height. It settles: a state
  // is set only when a measure or the range moved.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    offsetsRef.current = offsets;
    const el = listRef.current;
    if (!on || !el) return;
    const style = getComputedStyle(el);
    const n = grid ? style.gridTemplateColumns.split(" ").filter(Boolean).length || 1 : 1;
    if (n !== cols) {
      setCols(n);
      setMeas(m => ({ gap: m.gap, pitch: new Map() }));
      return;
    }
    const gap = parseFloat(style.rowGap) || 0;
    const heights = new Map();
    for (const child of el.children) {
      const i = child.dataset.index;
      if (i == null) continue;
      const l = Math.floor(Number(i) / cols);
      heights.set(l, Math.max(heights.get(l) || 0, child.getBoundingClientRect().height));
    }
    let pitch = null;                          // a new map, once a measure changed
    if (gap !== meas.gap) pitch = new Map();
    for (const [l, h] of heights) {
      const was = (pitch || meas.pitch).get(l);
      if (was == null || Math.abs(was - (h + gap)) > 0.5) {
        pitch = pitch || new Map(meas.pitch);
        pitch.set(l, h + gap);
      }
    }
    if (pitch) { setMeas({ gap, pitch }); return; }
    if (wanted != null) {
      const row = el.querySelector(`:scope > [data-index="${wanted}"]`);
      if (row) {
        setWanted(null);
        row.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
    update();
  });

  // Scrolling and resizing move the window; so does the page above the
  // list changing height (a panel that opens), seen through <body>'s size.
  useLayoutEffect(() => {
    if (!on) return undefined;
    let frame = 0;
    const later = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; update(); }); };
    const scrolled = () => { setSettled(true); later(); };
    window.addEventListener("scroll", scrolled, { passive: true });
    window.addEventListener("resize", later);
    const ro = new ResizeObserver(later);
    ro.observe(document.body);
    return () => {
      window.removeEventListener("scroll", scrolled);
      window.removeEventListener("resize", later);
      ro.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [on, update]);

  // Which row holds focus. Leaving for a dialog keeps it: the dialog gives
  // focus back. Leaving for nowhere (a click on the page) keeps it too:
  // one line more, and nothing can lose focus.
  const onFocus = useCallback(e => {
    const i = e.target.closest?.("[data-index]")?.dataset.index;
    setFocusRow(i == null ? -1 : Number(i));
  }, []);
  const onBlur = useCallback(e => {
    const to = e.relatedTarget;
    if (!to || e.currentTarget.contains(to) || to.closest?.(".modal-overlay")) return;
    setFocusRow(-1);
  }, []);

  // Row i into view and centred: straight to about where it is, then,
  // once rendered and measured, centred smoothly.
  const scrollTo = useCallback(i => {
    const el = listRef.current;
    if (!on || !el) {
      el?.querySelector(`:scope > [data-index="${i}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    const o = offsetsRef.current;
    const l = Math.min(Math.floor(i / cols), o.length - 2);
    const top = el.getBoundingClientRect().top + window.scrollY + o[l] - (window.innerHeight - (o[l + 1] - o[l])) / 2;
    window.scrollTo({ top: Math.max(0, top), behavior: "instant" });
    setWanted(i);
    update();
  }, [on, cols, update]);

  const render = (row, gap) => {
    if (!on) return Array.from({ length: count }, (_, i) => row(i));
    const pinned = [...pins, focusRow, wanted ?? -1]
      .filter(i => i >= 0 && i < count).map(i => Math.floor(i / cols));
    const out = [];
    for (const s of segments(lines, range, pinned, offsets, meas.gap)) {
      if (s.gap != null) { out.push(gap(s.gap, s.key)); continue; }
      for (let i = s.line * cols; i < Math.min(count, (s.line + 1) * cols); i++) out.push(row(i));
    }
    return out;
  };

  return { listRef, focusProps: { onFocus, onBlur }, render, scrollTo, settled: on && settled, windowed: on };
}
