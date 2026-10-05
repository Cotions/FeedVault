/* Inline stroke icons, same set and stroke weight as ChannelVault. Sized by
   `size`, coloured by currentColor so they inherit the surrounding text. */

const PATHS = {
  feed:      <><rect x="3.5" y="3.5" width="7.5" height="10" rx="1.6" /><rect x="13" y="3.5" width="7.5" height="6" rx="1.6" /><rect x="3.5" y="15.5" width="7.5" height="5" rx="1.6" /><rect x="13" y="11.5" width="7.5" height="9" rx="1.6" /></>,
  users:     <><circle cx="9.5" cy="8" r="3.4" /><path d="M3.5 20c0-3.3 2.7-5.6 6-5.6s6 2.3 6 5.6" /><path d="M16.4 5.2a3.4 3.4 0 0 1 0 6.4" /><path d="M18 14.9c2.1.7 3.5 2.5 3.5 5.1" /></>,
  chart:     <><path d="M3.5 20.5h17" /><path d="M6.5 20.5V11" /><path d="M11.5 20.5V4.5" /><path d="M16.5 20.5v-6.6" /></>,
  unmatched: <><path d="M14 3.5H6.5a1.5 1.5 0 0 0-1.5 1.5v14a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5V8.5z" /><path d="M14 3.5v5h5" /><path d="M12 11.5v3.2" /><path d="M12 17.4h.01" /></>,
  settings:  <><circle cx="12" cy="12" r="3.1" /><path d="M19.6 14.6a1.5 1.5 0 0 0 .3 1.7l.1.1a1.8 1.8 0 1 1-2.6 2.6l-.1-.1a1.5 1.5 0 0 0-2.5 1.1v.3a1.8 1.8 0 1 1-3.6 0v-.2a1.5 1.5 0 0 0-2.6-1.1l-.1.1a1.8 1.8 0 1 1-2.6-2.6l.1-.1a1.5 1.5 0 0 0-1.1-2.5h-.3a1.8 1.8 0 1 1 0-3.6h.2a1.5 1.5 0 0 0 1.1-2.6l-.1-.1a1.8 1.8 0 1 1 2.6-2.6l.1.1a1.5 1.5 0 0 0 1.7.3h.1a1.5 1.5 0 0 0 .9-1.4v-.3a1.8 1.8 0 1 1 3.6 0v.2a1.5 1.5 0 0 0 2.5 1.1l.1-.1a1.8 1.8 0 1 1 2.6 2.6l-.1.1a1.5 1.5 0 0 0 1.1 2.5h.3a1.8 1.8 0 1 1 0 3.6h-.2a1.5 1.5 0 0 0-1.4.9z" /></>,
  refresh:   <><path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1" /><path d="M20.8 4.2v5h-5" /></>,
  plus:      <><path d="M12 5v14" /><path d="M5 12h14" /></>,
  trash:     <><path d="M4.5 6.6h15" /><path d="M9.5 6.6V4.4h5v2.2" /><path d="M6.6 6.6 7.5 20h9l.9-13.4" /><path d="M10.4 10.4v5.8" /><path d="M13.6 10.4v5.8" /></>,
  close:     <><path d="M6 6l12 12" /><path d="M18 6 6 18" /></>,
  check:     <><path d="M4.5 12.6 9.5 17.5 19.5 6.9" /></>,
  play:      <><path d="M8 5.2 19 12 8 18.8z" /></>,
  search:    <><circle cx="10.8" cy="10.8" r="6.3" /><path d="M15.4 15.4 20.5 20.5" /></>,
  back:      <><path d="M20 12H4.5" /><path d="M10.6 5.6 4.2 12l6.4 6.4" /></>,
  chevLeft:  <><path d="M14.8 5.5 8.3 12l6.5 6.5" /></>,
  chevRight: <><path d="M9.2 5.5 15.7 12l-6.5 6.5" /></>,
  chevDown:  <><path d="M5.5 9.2 12 15.7l6.5-6.5" /></>,
  filter:    <><path d="M4 5.5h16l-6.2 7.3v5.4l-3.6 1.8v-7.2z" /></>,
  warn:      <><path d="M12 4.4 21 19.6H3z" /><path d="M12 10v4.2" /><path d="M12 17.1h.01" /></>,
  power:     <><path d="M12 3.4v8.2" /><path d="M7.6 6.6a6.9 6.9 0 1 0 8.8 0" /></>,
  folder:    <><path d="M3.5 7.2a1.7 1.7 0 0 1 1.7-1.7h4.2l2 2.2h7.4a1.7 1.7 0 0 1 1.7 1.7v8.9a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7z" /></>,
  external:  <><path d="M13.5 4.5h6v6" /><path d="M19.5 4.5 11 13" /><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10" /></>,
  copy:      <><rect x="8.5" y="8.5" width="11.5" height="11.5" rx="1.8" /><path d="M15.5 8.5V5.8A1.8 1.8 0 0 0 13.7 4H5.8A1.8 1.8 0 0 0 4 5.8v7.9a1.8 1.8 0 0 0 1.8 1.8h2.7" /></>,
  layers:    <><path d="M12 3.8 20.5 8.3 12 12.8 3.5 8.3z" /><path d="M3.5 12.3 12 16.8l8.5-4.5" /><path d="M3.5 16.2 12 20.7l8.5-4.5" /></>,
  image:     <><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><circle cx="9" cy="10" r="1.8" /><path d="m20.5 15.5-4.8-4.8L6 19.5" /></>,
  heart:     <><path d="M12 19.8s-7.8-4.6-7.8-10.1A4.4 4.4 0 0 1 12 7a4.4 4.4 0 0 1 7.8 2.7c0 5.5-7.8 10.1-7.8 10.1z" /></>,
  comment:   <><path d="M20.5 11.6a8 8 0 0 1-11.7 7.1L3.5 20l1.4-4.6a8 8 0 1 1 15.6-3.8z" /></>,
  eye:       <><path d="M2.8 12S6.3 5.5 12 5.5 21.2 12 21.2 12 17.7 18.5 12 18.5 2.8 12 2.8 12z" /><circle cx="12" cy="12" r="2.8" /></>,
  pin:       <><path d="M12 21s-6.5-5.9-6.5-11a6.5 6.5 0 0 1 13 0c0 5.1-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></>,
  review:    <><rect x="4" y="3.5" width="12.5" height="15" rx="1.8" /><path d="M7.5 20.5h11a1.5 1.5 0 0 0 1.5-1.5V7" /><path d="m7.4 11.3 2.3 2.3 4-4.4" /></>,
  undo:      <><path d="M9 14 4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" /></>,
  expand:    <><path d="M14.4 3.6h6v6" /><path d="M20.4 3.6 13.2 10.8" /><path d="M9.6 20.4h-6v-6" /><path d="M3.6 20.4 10.8 13.2" /></>,
  volume:    <><path d="M4 9.2h3.4L12 5.2v13.6l-4.6-4H4z" /><path d="M15.6 9.4a3.7 3.7 0 0 1 0 5.2" /><path d="M18.2 6.8a7.3 7.3 0 0 1 0 10.4" /></>,
  volumeOff: <><path d="M4 9.2h3.4L12 5.2v13.6l-4.6-4H4z" /><path d="M16.2 9.8 21 14.6" /><path d="M21 9.8l-4.8 4.8" /></>,
  skip:      <><path d="M5 5.5 14.5 12 5 18.5z" /><path d="M18.5 5.5v13" /></>,
  keyboard:  <><rect x="2.8" y="6" width="18.4" height="12" rx="2" /><path d="M6.5 9.5h.01M10 9.5h.01M13.5 9.5h.01M17 9.5h.01M6.5 12.5h.01M17 12.5h.01M8.5 15h7" /></>,
  arrowUp:   <><path d="M12 19.5v-15" /><path d="M5.8 10.6 12 4.4l6.2 6.2" /></>,
  disk:      <><ellipse cx="12" cy="6" rx="7.5" ry="2.6" /><path d="M4.5 6v12c0 1.4 3.4 2.6 7.5 2.6s7.5-1.2 7.5-2.6V6" /><path d="M4.5 12c0 1.4 3.4 2.6 7.5 2.6s7.5-1.2 7.5-2.6" /></>,
  tag:       <><path d="M3.8 12.6V5.2a1.4 1.4 0 0 1 1.4-1.4h7.4l7.6 7.6a1.5 1.5 0 0 1 0 2.1l-6.2 6.2a1.5 1.5 0 0 1-2.1 0z" /><circle cx="8.3" cy="8.3" r="1.5" /></>,
  bookmark:  <><path d="M6.5 3.8h11a1 1 0 0 1 1 1v15.6L12 16.3l-6.5 4.1V4.8a1 1 0 0 1 1-1z" /></>,
  menu:      <><path d="M4 6.5h16" /><path d="M4 12h16" /><path d="M4 17.5h16" /></>,
  grip:      <><path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01" /></>,
  terminal:  <><rect x="3" y="4.5" width="18" height="15" rx="2" /><path d="m7.2 9.6 2.8 2.4-2.8 2.4" /><path d="M12.6 14.6h4.4" /></>,
  pencil:    <><path d="M4 20h4.2L19.4 8.8a2.1 2.1 0 0 0-3-3L5.2 17z" /><path d="M14.9 5.4 18.6 9" /></>,
  download:  <><path d="M12 3.8v10.8" /><path d="M7.4 10.4 12 15l4.6-4.6" /><path d="M4.5 18.6h15" /></>,
  palette:   <><path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.8-1.7 1.7-1.7h2.1a3.9 3.9 0 0 0 3.9-3.9C20.5 7 16.7 3.5 12 3.5z" /><circle cx="7.6" cy="11.4" r="1" /><circle cx="10.2" cy="7.6" r="1" /><circle cx="14.8" cy="7.6" r="1" /></>,
  info:      <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5.2" /><path d="M12 7.8h.01" /></>,
  bell:      <><path d="M6.2 16.8V11a5.8 5.8 0 0 1 11.6 0v5.8l1.7 1.7H4.5z" /><path d="M10 20.6a2.2 2.2 0 0 0 4 0" /></>,
  bellOff:   <><path d="M6.2 16.8V11a5.8 5.8 0 0 1 1.3-3.7M10 5.5a5.8 5.8 0 0 1 7.8 5.5v5.8l1.7 1.7H8" /><path d="M10 20.6a2.2 2.2 0 0 0 4 0" /><path d="M4 4l16 16" /></>,
  clock:     <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.4V12l3 2" /></>,
  vault:     <><rect x="3.2" y="4.2" width="17.6" height="15.6" rx="2.4" /><circle cx="10.6" cy="12" r="3.6" /><path d="M10.6 8.4v7.2" /><path d="M7 12h7.2" /><path d="M17 9.4v5.2" /></>,
};

export default function Icon({ name, size = 16, className = "", style }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <svg
      className={`icon${className ? ` ${className}` : ""}`}
      style={style}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {d}
    </svg>
  );
}
