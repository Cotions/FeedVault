// Checks for lib/bookmarklet.js: the "Save to FeedVault" bookmark (#165 C). Run with `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { POPUP_FEATURES, POPUP_NAME, bookmarkletCode, bookmarkletHref } from "../src/lib/bookmarklet.js";

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

const ORIGIN = "http://localhost:3380";

test("it opens /links/add in a small window with the page's address and title", () => {
  const url = "https://example.org/a b?x=1&y=%41#frag";
  const title = "Café & co: 100% \"quoted\" <b>#1</b> + more   😀";
  const focused = [];
  const { opened, page } = click(bookmarkletHref(ORIGIN), { url, title, opens: { focus: () => focused.push(1) } });
  assert.equal(opened.length, 1);
  const [to, name, features] = opened[0];
  assert.equal(name, POPUP_NAME);
  assert.equal(features, POPUP_FEATURES);
  const u = new URL(to);
  assert.equal(u.origin, ORIGIN);
  assert.equal(u.pathname, "/links/add");
  assert.deepEqual([...u.searchParams.keys()], ["popup", "url", "title"]);
  assert.equal(u.searchParams.get("popup"), "1");
  assert.equal(u.searchParams.get("url"), url);
  assert.equal(u.searchParams.get("title"), title);
  assert.equal(focused.length, 1);
  assert.equal(page.href, url, "the page itself is left alone");
});

test("a blocked window: the page opens in the tab itself, without popup=1", () => {
  const { opened, page } = click(bookmarkletHref(ORIGIN), { url: "https://example.org/x?a=1", title: "X", opens: null });
  assert.equal(opened.length, 1);
  const u = new URL(page.href);
  assert.equal(u.origin + u.pathname, `${ORIGIN}/links/add`);
  assert.equal(u.searchParams.get("popup"), null);
  assert.equal(u.searchParams.get("url"), "https://example.org/x?a=1");
  assert.equal(u.searchParams.get("title"), "X");
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
  assert.ok(opened[0][0].startsWith(`${evil}/links/add?popup=1&url=`));
  assert.ok(bookmarkletCode(evil).includes(JSON.stringify(`${evil}/links/add`)));
});
