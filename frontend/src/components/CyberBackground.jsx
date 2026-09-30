import { useEffect, useRef } from "react";

/* Fixed backdrop behind everything: columns of coloured glyphs falling straight
   down over a flat dark field. No haze, no glow, no parallax — the colour lives
   in the glyphs themselves and nothing slides sideways with the cursor.

   The rain lives in document space, not viewport space: a drop's y is a world
   coordinate and the frame loop draws it at y - scrollY, so the field scrolls
   with the page instead of sitting pinned to the screen. The field is as tall as
   the document and drops wrap around it, which keeps the density uniform no
   matter where you are scrolled to.

   Every glyph is pre-rendered once into a small sprite and the frame loop only
   blits those. Calling fillText several hundred times a frame is what made the
   fall stutter. Columns fully outside the viewport are skipped before any of
   their cells are touched, so a very long page costs no more than a short one.

   The cursor still disturbs a column it passes through: that column flares
   white and sheds a few glyphs. Column state lives in refs, never React state,
   so a 120 Hz mousemove cannot re-render the app. */

const DENSITY  = 110;  // columns per viewport-height of document
const MAX_DROPS = 420; // ceiling for very long pages
const SPECKS = 90;
const COLOURS = ["#5b9dff", "#f472b6", "#a855f7", "#c4b5fd", "#e8ecf6"];
// Half-width katakana and a few symbols: the standard rain alphabet, and every
// glyph occupies the same cell so a column never reflows.
const GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789:=*+<>|";
const CELL = 17;    // vertical spacing between glyphs in a column
const SPRITE = 26;  // sprite box; no glow to leave room for, so it fits the cell
const BASE = 16;    // font size the sprites are drawn at; blitted scaled down

function pick() {
  return GLYPHS[(Math.random() * GLYPHS.length) | 0];
}

/* One sprite per (glyph, colour, head) triple, built on first use and kept for
   the life of the page. Bounded by the alphabet, so it tops out around 600
   little canvases and settles within a few seconds of load. */
const sprites = new Map();
function sprite(ch, colour, head) {
  const key = `${ch}|${colour}|${head ? 1 : 0}`;
  let c = sprites.get(key);
  if (c) return c;
  // Baked at device resolution, or a retina screen blits a 1x glyph upscaled
  // and the rain goes soft.
  const ss = Math.min(window.devicePixelRatio || 1, 2);
  c = document.createElement("canvas");
  c.width = c.height = Math.round(SPRITE * ss);
  const g = c.getContext("2d");
  g.setTransform(ss, 0, 0, ss, 0, 0);
  g.font = `${BASE}px "JetBrains Mono", ui-monospace, monospace`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  // Flat ink, no shadow. The head is only a paler tint of the same colour.
  g.fillStyle = head ? "#eef1f8" : colour;
  g.fillText(ch, SPRITE / 2, SPRITE / 2);
  sprites.set(key, c);
  return c;
}

export default function CyberBackground() {
  const rootRef   = useRef(null);
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let cx = -999, cy = -999;
    function onMove(e) { cx = e.clientX; cy = e.clientY; }
    window.addEventListener("pointermove", onMove, { passive: true });

    const ctx = canvas.getContext("2d");
    let w = 0, h = 0, fieldH = 0, raf = 0, last = 0;

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width  = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    // depth 0.2..1 drives size, fall speed and brightness.
    function reset(d, i, first, t) {
      const depth = 0.2 + Math.random() * 0.8;
      d.depth = depth;
      // Golden-ratio stride on first fill so the columns spread out instead of
      // clumping the way pure random does at this density.
      d.x = first ? ((i * 0.6180339887) % 1) * w : Math.random() * w;
      d.len = 6 + Math.round(depth * 14);
      d.y = first ? Math.random() * fieldH : -CELL * d.len - Math.random() * h * 0.3;
      d.speed = 40 + depth * 130;                // px/s downward
      d.colour = COLOURS[(Math.random() * COLOURS.length) | 0];
      d.chars = Array.from({ length: d.len }, pick);
      // Each cell rewrites on its own clock, so the trail churns instead of the
      // whole column flipping at once.
      d.next = Array.from({ length: d.len }, () => t + Math.random() * 1.2);
      d.hit = 0;
    }

    const drops = [];
    // Document height drives both the field and the column count, so density per
    // screenful stays the same whether the page is one viewport or ten.
    function measure(t) {
      const doc = Math.max(
        document.documentElement.scrollHeight,
        document.body ? document.body.scrollHeight : 0,
        h,
      );
      fieldH = doc;
      const want = Math.min(MAX_DROPS, Math.round(DENSITY * (fieldH / h)));
      while (drops.length > want) drops.pop();
      while (drops.length < want) {
        const d = {};
        reset(d, drops.length, true, t);
        drops.push(d);
      }
    }

    resize();
    measure(0);

    /* Glyphs shaken loose by the cursor. Fixed-size pool: a sweeping cursor can
       brush the field many times a second, and allocating per touch would hand
       the GC a steady drip of garbage. Speck y is a world coordinate too. */
    const specks = Array.from({ length: SPECKS }, () => ({
      x: 0, y: 0, vy: 0, life: 0, born: 1, ch: "0", colour: "#fff",
    }));
    let speckAt = 0;

    function shed(x, y, colour, depth) {
      const n = 2 + Math.round(depth * 3);
      for (let i = 0; i < n; i++) {
        const p = specks[speckAt = (speckAt + 1) % SPECKS];
        p.x = x + (i - n / 2) * 3;
        p.y = y + i * 6;
        p.vy = 30 + Math.random() * 50 * depth;   // they just drop away
        p.ch = pick();
        p.born = p.life = 1.1 + Math.random() * 1.1;
        p.colour = colour;
      }
    }

    let sinceMeasure = 0;

    function frame(now) {
      raf = requestAnimationFrame(frame);
      const dt = last ? Math.min((now - last) / 1000, 0.05) : 0.016;
      last = now;
      const t = now / 1000;

      // Page height changes as routes render; re-measure a few times a second
      // rather than every frame. Read-only, so it never thrashes layout.
      sinceMeasure += dt;
      if (sinceMeasure > 0.4) { sinceMeasure = 0; measure(t); }

      const scroll = window.scrollY || window.pageYOffset || 0;
      const worldCy = cy + scroll;

      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";

      for (const d of drops) {
        d.y += d.speed * dt;

        const tail = d.y - CELL * (d.len - 1);
        // Off the bottom of the document: wrap to the top of the field. Every
        // drop that leaves feeds back in above, so density holds everywhere.
        if (tail > fieldH + CELL) { reset(d, 0, false, t); continue; }

        d.hit = Math.max(0, d.hit - dt * 0.9);       // ~1.1s to settle

        // Whole column outside the viewport: nothing to draw, and the cursor
        // cannot be touching it either.
        const headY = d.y - scroll;
        if (headY < -CELL || tail - scroll > h + CELL) continue;

        // Cursor against the column as a vertical segment, not a box: a box
        // fires on empty air beside the glyphs.
        const near = Math.abs(cx - d.x) < 22 &&
                     worldCy > tail - CELL && worldCy < d.y + CELL;
        if (d.hit < 0.25 && near) {
          d.hit = 1;
          shed(d.x, worldCy, d.colour, d.depth);
        }

        const scale = (10 + d.depth * 6) / BASE;
        const box = SPRITE * scale;
        const dim = 0.09 + d.depth * 0.2;

        for (let i = 0; i < d.len; i++) {
          const y = headY - i * CELL;

          // Rewrite on this cell's own clock. Deeper in the tail it churns
          // faster, since that is where it is dim enough not to read as a blink;
          // the head holds its glyph long enough to stay legible.
          if (t > d.next[i]) {
            d.next[i] = t + (i === 0 ? 0.7 : 0.22) + Math.random() * (i === 0 ? 0.9 : 0.7);
            d.chars[i] = pick();
          }

          if (y < -CELL) break;   // cells above the head are further up still
          if (y > h + CELL) continue;
          // Head is the bright one; the tail falls away on a curve so the column
          // reads as a streak rather than a dotted line.
          const head = i === 0;
          const fade = 1 - i / d.len;
          ctx.globalAlpha = Math.min(0.9,
            (head ? dim * 2.6 : dim * fade * fade) + d.hit * (head ? 0.35 : 0.16));
          ctx.drawImage(sprite(d.chars[i], d.colour, head && d.hit < 0.2),
                        d.x - box / 2, y - box / 2, box, box);
        }
      }

      const speckBox = SPRITE * (11 / BASE);
      for (const p of specks) {
        if (p.life <= 0) continue;
        p.life -= dt;
        p.y += p.vy * dt;
        p.vy *= Math.max(0, 1 - dt * 0.4);
        const y = p.y - scroll;
        if (y < -speckBox || y > h + speckBox) continue;
        const k = Math.max(0, p.life / p.born);
        ctx.globalAlpha = k * k * 0.5;              // squared: soft tail
        ctx.drawImage(sprite(p.ch, p.colour, false),
                      p.x - speckBox / 2, y - speckBox / 2, speckBox, speckBox);
      }

      ctx.globalAlpha = 1;
    }

    if (!reduced) raf = requestAnimationFrame(frame);

    function onVisibility() {
      if (document.hidden) { cancelAnimationFrame(raf); raf = 0; }
      else if (!raf && !reduced) { last = 0; raf = requestAnimationFrame(frame); }
    }

    function onResize() { resize(); measure(performance.now() / 1000); }

    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div className="cyber-bg" ref={rootRef} aria-hidden="true">
      <canvas className="cyber-rain" ref={canvasRef} />
      <div className="cyber-grain" />
      <div className="cyber-vignette" />
    </div>
  );
}
