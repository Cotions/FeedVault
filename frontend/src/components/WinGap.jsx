// The spacers of a windowed list (lib/windowing.js): each stands for the
// rows not rendered above or below the viewport, at their height. Hidden
// from screen readers and from the keyboard: there is nothing in them.

// In a table's <tbody>.
export function GapRow({ height, cols }) {
  return <tr className="win-gap" aria-hidden="true"><td colSpan={cols} style={{ height }} /></tr>;
}

// In a list (<ul>: an <li>) or a grid (a <div> across every column).
export function Gap({ height, as: Tag = "div" }) {
  return <Tag className="win-gap" aria-hidden="true" style={{ height }} />;
}
