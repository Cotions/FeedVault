"""Many links at once, for the Links page's Unsorted (POST /api/links/import-many).

What the user brings is read here, and only read: a block of pasted text
(addresses with anything between them, "title - address" lines, Markdown
links), a browser's bookmarks exported to HTML (the Netscape bookmark file
Firefox, Chrome and Edge write: ``<DT><A HREF=... ADD_DATE=...>Title</A>``,
folders as ``<H3>`` over a ``<DL>``), or Firefox's JSON bookmarks backup.
Nothing is fetched, ever: a link is the text it was given (links.py).

read() turns it into ``[(url, title, at, notes)]`` for links.keep, each URL
cleaned (links.clean_url: tracking parameters dropped) and listed once,
with how many entries were passed over (not an http(s) address: place:,
javascript:, about:, file:, chrome://, or one clean_url refuses) and how
many repeated an address listed before.
"""
import json
import re
from html.parser import HTMLParser

import links

MAX_TEXT = 10 * 1024 * 1024        # characters of text or file read (a Chrome export carries icons)
MAX_LINKS = links.MAX_IDS          # new links one import saves; the rest are "over"
FOLDER_SEP = " / "

# A pasted address: http(s)://… or a bare www.… up to whitespace or a
# character no address is written with in running text.
_URL_RE = re.compile(r"""(?:https?://|www\.)[^\s<>"'`]+""", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]\n]*)\]\(\s*((?:https?://|www\.)[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)\s*\)", re.IGNORECASE)
_TRAILING = ".,;:!?'\""
# What is left of a "title - address" line once the address is taken out.
_TITLE_EDGES = " \t-–—:|•*>#()[]"
# Bookmark HTML, as opposed to text that merely holds an address.
_HTML_HINT = re.compile(r"<!DOCTYPE\s+NETSCAPE-Bookmark-file|<a\s[^>]*\bhref\s*=", re.IGNORECASE)

# Firefox's JSON backup names its root folders by these.
_MOZ_ROOTS = {"bookmarksMenuFolder": "Bookmarks Menu", "toolbarFolder": "Bookmarks Toolbar",
              "unfiledBookmarksFolder": "Other Bookmarks", "mobileFolder": "Mobile Bookmarks"}


class TooLarge(ValueError):
    """More text than MAX_TEXT."""


def read(text, now):
    """{"found": [(url, title, at, notes)], "skipped", "repeated"} from
    ``text`` (bookmark HTML, Firefox JSON, or anything else as pasted
    text). ``at`` is when the bookmark was added (seconds, never after
    ``now``), ``now`` for pasted text. TooLarge past MAX_TEXT."""
    if len(text) > MAX_TEXT:
        raise TooLarge(len(text))
    stripped = text.lstrip("﻿ \t\r\n")
    entries = None
    if stripped.startswith("{"):
        entries = _from_json(stripped)
    if entries is None:
        entries = _from_html(text) if _HTML_HINT.search(text) else _from_text(text)
    return _clean(entries, now)


def _clean(entries, now):
    """Entries [(raw url, title, add date or None, folder path)] as links.keep takes them."""
    found, seen, skipped, repeated = [], set(), 0, 0
    for raw, title, added, folder in entries:
        url = links.clean_url(raw)
        if url is None:
            skipped += 1
            continue
        s = links.spelling(url)
        if s in seen:
            repeated += 1
            continue
        seen.add(s)
        notes = f"Folder: {folder}"[:links.MAX_NOTES] if folder else ""
        found.append((url, _title(title), _when(added, now), notes))
    return {"found": found, "skipped": skipped, "repeated": repeated}


def _title(value):
    """A title as stored: whitespace collapsed, cut to MAX_TITLE."""
    return links.clean_title(" ".join((value or "").split())[:links.MAX_TITLE]) or ""


def _when(value, now):
    """A bookmark's add date as seconds since the epoch, at most ``now``;
    ``now`` for none or garbage. Milliseconds or microseconds (Firefox's
    JSON) are brought down to seconds."""
    try:
        at = int(str(value).strip()) if value is not None else 0
    except (TypeError, ValueError):
        return now
    if at <= 0:
        return now
    while at > 100_000_000_000:                    # past the year 5000 in seconds: finer units
        at //= 1000
    return min(at, now)


# ---------------------------------------------------------------------------
# Pasted text
# ---------------------------------------------------------------------------

def _address(raw):
    """A URL found in running text, without the punctuation after it and
    with a scheme."""
    url = raw.rstrip(_TRAILING)
    for open_, close in (("(", ")"), ("[", "]")):
        while url.endswith(close) and url.count(close) > url.count(open_):
            url = url[:-1].rstrip(_TRAILING)
    return url if url.lower().startswith(("http://", "https://")) else "https://" + url


def _from_text(text):
    out = []
    for line in text.splitlines():
        for m in _MD_LINK.finditer(line):
            out.append((_address(m.group(2)), m.group(1), None, ""))
        rest = _MD_LINK.sub(" ", line)
        urls = list(_URL_RE.finditer(rest))
        if len(urls) == 1:                         # "title - address", "address - title": the rest is its title
            m = urls[0]
            title = (rest[:m.start()].strip(_TITLE_EDGES) + " " + rest[m.end():].strip(_TITLE_EDGES)).strip()
            out.append((_address(m.group(0)), title, None, ""))
        else:
            out += [(_address(m.group(0)), "", None, "") for m in urls]
    return out


# ---------------------------------------------------------------------------
# Bookmark HTML (Netscape bookmark file)
# ---------------------------------------------------------------------------

class _Bookmarks(HTMLParser):
    """Each <A HREF> with its text, its ADD_DATE and the folders (<H3>)
    over it: a <DL> after an <H3> holds that folder's bookmarks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self.folders = []          # one per open <DL>: its folder's name, or None
        self.pending = None        # the name of the last <H3>, for the next <DL>
        self.h3 = None             # the text of the <H3> being read
        self.a = None              # [href, add date, text] of the <A> being read

    def path(self):
        return FOLDER_SEP.join(f for f in self.folders if f)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._end_a()
            at = {k: v for k, v in attrs}
            self.a = [at.get("href"), at.get("add_date"), []]
        elif tag == "h3":
            self._end_a()
            self.h3 = []
        elif tag == "dl":
            self._end_a()
            self.folders.append(self.pending)
            self.pending = None
        elif tag in ("dt", "dd", "h1"):
            self._end_a()

    def handle_endtag(self, tag):
        if tag == "a":
            self._end_a()
        elif tag == "h3" and self.h3 is not None:
            self.pending = " ".join("".join(self.h3).split()) or None
            self.h3 = None
        elif tag == "dl":
            self._end_a()
            if self.folders:
                self.folders.pop()
            self.pending = None

    def handle_data(self, data):
        if self.a is not None:
            self.a[2].append(data)
        elif self.h3 is not None:
            self.h3.append(data)

    def _end_a(self):
        if self.a is None:
            return
        href, added, text = self.a
        self.a = None
        self.entries.append((href or "", "".join(text), added, self.path()))

    def close(self):
        super().close()
        self._end_a()


def _from_html(text):
    p = _Bookmarks()
    p.feed(text)
    p.close()
    return p.entries


# ---------------------------------------------------------------------------
# Firefox's JSON backup (Bookmarks > Manage > Backup)
# ---------------------------------------------------------------------------

def _from_json(text):
    """The bookmarks of a Firefox JSON backup, or None when the text is not one."""
    try:
        tree = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(tree, dict) or "children" not in tree:
        return None
    out = []
    stack = [(tree, ())]                           # no recursion: a deep tree is no harm
    while stack:
        node, folders = stack.pop()
        if not isinstance(node, dict):
            continue
        uri = node.get("uri")
        if isinstance(uri, str):
            title = node.get("title")
            out.append((uri, title if isinstance(title, str) else "", node.get("dateAdded"), FOLDER_SEP.join(folders)))
        children = node.get("children")
        if isinstance(children, list):
            name = _MOZ_ROOTS.get(node.get("root")) or (node.get("title") if isinstance(node.get("title"), str) else "")
            inner = folders + (" ".join(name.split()),) if name and node is not tree else folders
            stack += [(c, inner) for c in reversed(children)]
    return out
