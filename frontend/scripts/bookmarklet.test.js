// Checks for lib/bookmarklet.js: the "Save to FeedVault" bookmark (#165 C). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { POPUP_FEATURES, bookmarkletCode, bookmarkletHref } from "../src/lib/bookmarklet.js";
import { MAX_TITLE } from "../src/lib/links.js";

// Runs the bookmark as a browser would (its address percent-decoded, then
// the script) on a fake page; ``opens`` is what window.open returns.
function click(href, { url, title, opens = { focus() {} } }) {
  assert.ok(href.startsWith("javascript:"));
  const code = decodeURIComponent(href.slice("javascript:".length));
  const opened = [];
  const page = { href: url };
  const win = { open: (...args) => { opened.push(args); return opens; } };
  new Function("window", "location", "document", code)(win, page, { title });
  return { opened, page };
}

// What /links/add reads: the query (popup=1) and the fragment (url, title).
function given(to) {
  const u = new URL(to);
  return { at: u.origin + u.pathname, query: [...u.searchParams], ...Object.fromEntries(new URLSearchParams(u.hash.slice(1))) };
}

const ORIGIN = "http://feedvault.test";

test("it opens /links/add in a window of its own, the page's address and title in the fragment", () => {
  const url = "https://example.org/a b?x=1&y=%41&token=s3cret#frag";
  const title = "Café & co: 100% \"quoted\" <b>#1</b> + more   😀";
  const focused = [];
  const { opened, page } = click(bookmarkletHref(ORIGIN), { url, title, opens: { focus: () => focused.push(1) } });
  assert.equal(opened.length, 1);
  const [to, name, features] = opened[0];
  assert.equal(name, "_blank", "a new window each click: one open already keeps its typing");
  assert.equal(features, POPUP_FEATURES);
  assert.deepEqual(given(to), { at: `${ORIGIN}/links/add`, query: [["popup", "1"]], url, title });
  assert.equal(new URL(to).search, "?popup=1", "nothing of the page in what the server is sent");
  assert.equal(focused.length, 1);
  assert.equal(page.href, url, "the page itself is left alone");
});

test("a blocked window: the page opens in the tab itself, without popup=1", () => {
  const { opened, page } = click(bookmarkletHref(ORIGIN), { url: "https://example.org/x?a=1", title: "X", opens: null });
  assert.equal(opened.length, 1);
  assert.deepEqual(given(page.href), { at: `${ORIGIN}/links/add`, query: [], url: "https://example.org/x?a=1", title: "X" });
});

test("a title with a lone surrogate still opens the window (it is dropped); a long one is cut whole", () => {
  for (const [title, sent] of [["a\uD800b", "ab"], ["\uDC00x\uD83D", "x"], ["\uDC00\uDC00😀", "😀"]]) {
    const { opened } = click(bookmarkletHref(ORIGIN), { url: "https://example.org/", title });
    assert.equal(given(opened[0][0]).title, sent, JSON.stringify(title));
  }
  const long = "😀".repeat(MAX_TITLE + 20);
  const { opened } = click(bookmarkletHref(ORIGIN), { url: "https://example.org/", title: long });
  assert.equal(given(opened[0][0]).title, "😀".repeat(MAX_TITLE));
});

test("the address is the script encoded whole: nothing a browser would decode or cut", () => {
  const href = bookmarkletHref(ORIGIN);
  assert.match(href.slice("javascript:".length), /^[A-Za-z0-9\-_.!~*'()%]+$/);
  assert.equal(decodeURIComponent(href.slice("javascript:".length)), bookmarkletCode(ORIGIN));
  // A "%" in the script reaches it as a "%", not as what it would decode to.
  assert.ok(bookmarkletHref('http://h"%22').includes("%2522"));
});

test("an origin cannot inject code: it stays a string", () => {
  const evil = 'http://x"+(globalThis.pwned=1)+"\\\'</script>%27';
  const { opened } = click(bookmarkletHref(evil), { url: "https://example.org/", title: "t" });
  assert.equal(globalThis.pwned, undefined);
  assert.ok(opened[0][0].startsWith(`${evil}/links/add?popup=1#url=`));
  assert.ok(bookmarkletCode(evil).includes(JSON.stringify(`${evil}/links/add`)));
});
