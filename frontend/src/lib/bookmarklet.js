/* The "Save to FeedVault" bookmarklet (#165 C): a bookmark whose address is
   a script. Clicked on any page, it opens FeedVault's /links/add in a small
   window with that page's address and title, and does nothing else: the
   other site is sent nothing, and the save is made by FeedVault's own page,
   same-origin, once the user chooses who the link goes to.

   The address and title go in the fragment (#url=…&title=…), which the
   browser never sends: a page's address may carry a token, and it stays
   out of FeedVault's request log. Each click opens a window of its own
   (_blank): a second one never takes over the first, typing and all.

   Should the browser block the window, it opens the same page in the tab
   itself (Back returns): a new tab would most likely be blocked as well,
   and leaving the page is less surprising than a click that does nothing.
   Opened that way (no popup=1), the page ends on links instead of closing. */

import { MAX_TITLE } from "./links.js";

export const POPUP_FEATURES = "width=480,height=640";

// The script, readable: ``origin`` (FeedVault's, from location) goes in as
// a JSON string, so no character of it can end the literal. The title
// loses any lone surrogate first (encodeURIComponent would throw on it,
// and the bookmark do nothing) and is cut, whole characters only.
export function bookmarkletCode(origin) {
  const page = JSON.stringify(`${origin}/links/add`);
  return [
    "(()=>{",
    // Array.from splits by character: a pair is one (two units), a lone surrogate one unit.
    `const t=Array.from(String(document.title)).filter(c=>!/^[\\uD800-\\uDFFF]$/.test(c)).slice(0,${MAX_TITLE}).join("");`,
    `const f="#url="+encodeURIComponent(location.href)+"&title="+encodeURIComponent(t);`,
    `const w=window.open(${page}+"?popup=1"+f,"_blank",${JSON.stringify(POPUP_FEATURES)});`,
    `if(w)w.focus();else location.href=${page}+f;`,
    "})()",
  ].join("");
}

// The bookmark's address. A javascript: URL is percent-decoded before it
// runs, so the script is encoded whole: a "%" in it stays a "%".
export function bookmarkletHref(origin) {
  return `javascript:${encodeURIComponent(bookmarkletCode(origin))}`;
}
