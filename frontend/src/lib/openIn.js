// Where a link opens (docs/API.md "Opening a link elsewhere"): the choices
// a link row's "Open in…" menu and Settings offer, and their names. No
// import: scripts/openIn.test.js runs it in Node.

/* A left click with no key held: the one a set default takes over. A
   middle click (auxclick), Ctrl/Cmd-click (a tab here), Shift-click (a
   window here) and Alt-click stay the browser's own. */
export function isPlainClick(e) {
  return e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey;
}

/* A choice's name: "Firefox · work", "Chromium · private". ``browsers``:
   GET /api/open/browsers's list, for the browser's name (else its id: one
   removed since it was saved). */
export function choiceLabel(choice, browsers) {
  if (!choice) return "This browser";
  const name = (browsers || []).find(b => b.id === choice.browser)?.name || choice.browser;
  return [name, choice.profile, choice.private && "private"].filter(Boolean).join(" · ");
}

/* Whether two choices are one (null: this browser). */
export function sameChoice(a, b) {
  if (!a || !b) return !a && !b;
  return a.browser === b.browser && (a.profile || null) === (b.profile || null) && !!a.private === !!b.private;
}

/* Every choice but this browser, in the menu's order: each installed
   browser's profiles (none listed: the browser picks one), each in a
   normal then a private window. */
export function openChoices(browsers) {
  const out = [];
  for (const b of browsers || []) {
    for (const profile of b.profiles?.length ? b.profiles : [null]) {
      for (const priv of [false, true]) out.push({ browser: b.id, profile, private: priv });
    }
  }
  return out;
}
