/* The "Save to FeedVault" bookmarklet (#165 C): a bookmark whose address is
   a script. Clicked on any page, it opens FeedVault's /links/add in a small
   window with that page's address and title, and does nothing else: the
   other site is sent nothing, and the save is made by FeedVault's own page,
   same-origin, when the user presses Save there.

   Should the browser block the window, it opens the same page in the tab
   itself (Back returns): a new tab would most likely be blocked as well,
   and leaving the page is less surprising than a click that does nothing.
   Opened that way (no popup=1), the page ends on links instead of closing. */

export const POPUP_NAME = "feedvault-add";
export const POPUP_FEATURES = "width=480,height=640";

// The script, readable: ``origin`` (FeedVault's, from location) goes in as
// a JSON string, so no character of it can end the literal.
export function bookmarkletCode(origin) {
  const page = JSON.stringify(`${origin}/links/add`);
  return [
    "(()=>{",
    `const q="url="+encodeURIComponent(location.href)+"&title="+encodeURIComponent(document.title);`,
    `const w=window.open(${page}+"?popup=1&"+q,${JSON.stringify(POPUP_NAME)},${JSON.stringify(POPUP_FEATURES)});`,
    `if(w)w.focus();else location.href=${page}+"?"+q;`,
    "})()",
  ].join("");
}

// The bookmark's address. A javascript: URL is percent-decoded before it
// runs, so the script is encoded whole: a "%" in it stays a "%".
export function bookmarkletHref(origin) {
  return `javascript:${encodeURIComponent(bookmarkletCode(origin))}`;
}
