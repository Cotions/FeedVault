"""Link-in-bio import (#11): the one web page FeedVault fetches itself.

The user pastes a person's linktr.ee (or similar) page and clicks Import;
FeedVault fetches that one page, suggests the accounts it links to, and
keeps the page's own address and the links it shows a visitor in that
person's Links (links.py), so they outlast the page.
Everything else that reaches the network is a downloader started as a job,
or the PyPI version check (downloaders.py). So this is kept small and
bounded:

- off unless ``bio_import`` is on in the config (Settings → Downloads);
- ``https`` on port 443 to a host on SITES only, exactly (no subdomains),
  or to a person's own subdomain of a SUBDOMAIN_SITES site (one label,
  never the site itself nor a name it keeps for its own workings);
- every address the host resolves to must be public, and the connection
  goes to the address that was checked, with TLS verifying the real host
  name, so DNS cannot answer one thing to the check and another to the
  connect;
- nothing from the environment: no proxy, no .netrc, no cookies, no
  Referer; a fixed User-Agent naming FeedVault;
- at most MAX_REDIRECTS redirects, each checked again from scratch, all of
  it within TIMEOUT seconds and MAX_BYTES of body, uncompressed HTML only;
- one import at a time, at least MIN_GAP seconds apart, and never a link
  found in the page: the caller only parses what comes back. One narrow
  exception, for sites whose buttons are redirects on their own host
  (REDIRECT_LINKS: mydeck.bio's /go/<n>, whose address is nowhere in the
  page): a link on the page's own exact host whose request target is the
  site's pattern is asked once, by the same fetcher and its rules, for its
  status line and headers only (never its body), without following; the
  Location it answers is where the button goes, taken as a link found in
  the page and never fetched. At most MAX_LOOKUPS per import, LOOKUP_GAP
  seconds apart, each within LOOKUP_TIMEOUT, all of the import within
  IMPORT_TIME; one that fails or does not redirect is left out.

The page's body is never logged or stored; its URL may be logged. It is
read with html.parser and json only (never run). Only the profile links
the routing table knows become suggestions; the user adds each one through
the usual calls. What is stored is the links a visitor would click
(shown_links: no image, font, script, CDN or tracking address, nothing of
the bio site itself), each through links.py's own cleaning, with the label
the page gives it as plain text. A saved link is text like any other in
Links: never fetched, here or anywhere, and an import only ever adds.
"""
import codecs
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
import unicodedata
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import config
import db
import links
import people
import sources

# Link-in-bio sites whose every profile is a path on one host. Sites where
# a profile is a subdomain are in SUBDOMAIN_SITES. Left out: sites where it
# is the user's own domain (Linktree's and Beacons' custom domains,
# taplink.cc's custom domains), which a host check cannot tell from any
# other site, and subdomain sites not added yet (carrd.co, bio.link, komi.io).
SITES = {
    "linktr.ee": "Linktree, the most used one: linktr.ee/<name>",
    "beacons.ai": "Beacons, common with TikTok and Instagram creators: beacons.ai/<name>",
    "lnk.bio": "Lnk.Bio: lnk.bio/<name>",
    "solo.to": "solo.to: solo.to/<name>",
    "campsite.bio": "Campsite: campsite.bio/<name>",
    "linkin.bio": "Later's Linkin.bio, for Instagram: linkin.bio/<name>",
    "allmylinks.com": "AllMyLinks: allmylinks.com/<name>",
    "link.me": "Linkme: link.me/<name>",
}
# Link-in-bio sites whose every profile is a subdomain: <name>.<site>/ is
# the profile. One label only, never the site itself nor _NOT_PROFILES.
SUBDOMAIN_SITES = {
    "mydeck.bio": "MyDeck: <name>.mydeck.bio",
}
# Subdomains a site keeps for its own workings, not a person's page (any
# name _ASSET_LABEL tells as one too: cdn2, static, media, api…).
_NOT_PROFILES = frozenset({
    "www", "app", "admin", "dashboard", "mail", "smtp", "imap", "ftp", "ns1", "ns2", "mx", "blog", "help",
    "support", "docs", "status", "auth", "login", "account", "accounts", "dev", "staging", "beta", "test", "go",
})
# Sites whose buttons are links on the page's own host that redirect to
# where they go, the address in no part of the page: the request target
# (path, no query) such a link must be, exactly. See the module's docstring.
REDIRECT_LINKS = {
    "mydeck.bio": re.compile(r"/go/[0-9]{1,9}"),
}
PORT = 443
URL_MAX = 2000
TIMEOUT = 10                                   # seconds for everything: lookups, connects, TLS, every byte
MAX_BYTES = 2 * 1024 * 1024                    # of body, counted as it is read
MAX_REDIRECTS = 3
MIN_GAP = 5                                    # seconds between two fetches
MAX_LOOKUPS = 30                               # redirect links (REDIRECT_LINKS) asked per import
LOOKUP_GAP = 0.4                               # seconds before each of them
LOOKUP_TIMEOUT = 5                             # seconds for each, as TIMEOUT is for the page
IMPORT_TIME = 40                               # seconds for the whole import: the page and every lookup
HEAD_MAX = 32 * 1024                           # bytes of status line and headers
HEADERS_MAX = 100
REDIRECTS = (301, 302, 303, 307, 308)
USER_AGENT = (f"FeedVault/{config.__version__} (self-hosted archive; fetches this one page because its user "
              "asked to import the accounts it lists)")


class Refused(ValueError):
    """A fetch refused or failed; the message is short, for the user.
    ``status``: what the API answers (400 the link, 403 switched off, 429
    busy, 502 the site)."""

    def __init__(self, message, status=400):
        # Messages quote the site (a Content-Type, a redirect's host) and are
        # logged: no escape sequence or other control character gets through.
        super().__init__("".join(c if c.isprintable() else "?" for c in message))
        self.status = status


def enabled(cfg):
    return cfg.get("bio_import") is True


# ---------------------------------------------------------------------------
# What may be fetched
# ---------------------------------------------------------------------------

_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
# A path or query as sent: what a browser would leave as it is, the rest %-encoded.
_SAFE = "/%:@!$&'()*+,;=-._~"


def _host(name):
    """A host name as it is matched: lowercase, IDNA (so fullwidth or
    other Unicode forms of an allowed name become it, and look-alikes become
    xn-- names that match nothing), no trailing dot, no ``www.``. None when
    it is not a domain name (an IP address, an empty label)."""
    name = (name or "").lower()
    if name.endswith("."):
        name = name[:-1]
    try:
        name = name.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if name.startswith("www."):
        name = name[4:]
    return name if _HOST_RE.fullmatch(name) else None


def check_url(text, first=True):
    """(host, request target, URL) for a link that may be fetched, else
    raises Refused. ``first``: the pasted link, which must name a page (a
    redirect may go to the site's home page)."""
    if not isinstance(text, str) or not text.strip():
        raise Refused("paste a link-in-bio page's link")
    text = text.strip()
    if len(text) > URL_MAX:
        raise Refused("the link is too long")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in text):
        raise Refused("the link has spaces or control characters")
    if first and "://" not in text:
        text = "https://" + text               # pasted as linktr.ee/name
    try:
        u = urlsplit(text)
        port = u.port
    except ValueError:
        raise Refused("not a link")
    if u.scheme.lower() != "https":
        raise Refused("only https links")
    if "@" in u.netloc:
        raise Refused("a link with a login part is refused")
    if port not in (None, PORT):
        raise Refused("only https's own port (443)")
    host = _host(u.hostname)
    path = u.path or "/"
    # A subdomain site's own host is its home page; a person's page there
    # is <name>.<site>/, so its path may be "/".
    if host in SUBDOMAIN_SITES or (host in SITES and first and path.strip("/") == ""):
        raise Refused("paste the link to a person's page, not the site's home page")
    if host not in SITES and not _profile_host(host):
        allowed = [*sorted(SITES), *(f"<name>.{site}" for site in sorted(SUBDOMAIN_SITES))]
        raise Refused(f"not an allowed site: {host or u.hostname or 'no host'} (allowed: {', '.join(allowed)})")
    target = quote(path, safe=_SAFE) + (f"?{quote(u.query, safe=_SAFE + '?')}" if u.query else "")
    return host, target, f"https://{host}{target}"


def site_of(host):
    """The SITES or SUBDOMAIN_SITES name a host (as _host gives it) is on:
    the site itself or, for SUBDOMAIN_SITES, any subdomain of it. Else None."""
    if host in SITES:
        return host
    return next((site for site in SUBDOMAIN_SITES if host == site or (host or "").endswith("." + site)), None)


def _profile_host(host):
    """Whether a host is a person's page on a SUBDOMAIN_SITES site: one
    label above the site's name (never a.b.<site>), not one the site keeps
    for itself (_NOT_PROFILES, _ASSET_LABEL)."""
    site = site_of(host)
    if site not in SUBDOMAIN_SITES or host == site:
        return False
    label = host[:-len(site) - 1]
    return ("." not in label and _LABEL_RE.fullmatch(label) is not None and label not in _NOT_PROFILES
            and _ASSET_LABEL.fullmatch(label) is None)


def redirect_link(href, page_url):
    """The URL (as check_url gives it) of a link of the page at
    ``page_url`` that its site answers with a redirect to where the link
    goes (REDIRECT_LINKS): on the page's own host exactly, its request
    target the site's pattern. Else None: no other link is ever asked."""
    try:
        host = check_url(page_url, first=False)[0]
        pattern = REDIRECT_LINKS.get(site_of(host))
        if pattern is None or not isinstance(href, str):
            return None
        to, target, url = check_url(urljoin(page_url, href.strip()), first=False)
    except ValueError:                         # Refused too
        return None
    return url if to == host and pattern.fullmatch(target) else None


def public(address):
    """Whether an address is one a page may be fetched from: global, and
    no form that reaches another address (IPv4-mapped, -compatible, 6to4,
    NAT64, Teredo) nor multicast, which ``is_global`` lets through."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip.is_site_local:
            return False
        if any(ip in n for n in _V6_TUNNELS):
            return False
    return True


_V6_TUNNELS = [ipaddress.ip_network(n) for n in (
    "::/96",                                   # IPv4-compatible (deprecated)
    "64:ff9b::/96", "64:ff9b:1::/48",          # NAT64
    "2002::/16",                               # 6to4
    "2001::/32",                               # Teredo
    "3fff::/20",                               # documentation (2024), global to Python 3.12
)]
# The charsets a page may name (codecs.lookup names): C codecs, linear in the
# page's size, that web pages use. Any other is read as UTF-8: Python also
# has punycode, whose decoder took 156 s of CPU on 2 MB.
WEB_CHARSETS = {
    "utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "ascii",
    *(f"iso8859-{n}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16)),
    *(f"cp{n}" for n in range(1250, 1259)), "cp874", "tis-620", "koi8-r", "koi8-u", "mac-roman",
    "shift_jis", "cp932", "euc_jp", "iso2022_jp", "gb2312", "gbk", "gb18030", "big5", "big5hkscs", "euc_kr", "cp949",
}
# Web names Python's codecs do not know.
_CHARSET_ALIASES = {"windows-874": "cp874", "windows-31j": "cp932", "x-sjis": "shift_jis", "x-cp1252": "cp1252"}


# ---------------------------------------------------------------------------
# The fetch
# ---------------------------------------------------------------------------

class Page:
    """A page fetched. ``resolved``: {a redirect link's URL (redirect_link):
    the address its redirect names}, for the links that answered one."""

    def __init__(self, url, text, resolved=None):
        self.url, self.text, self.resolved = url, text, resolved or {}


def system_resolve(host, port, timeout):
    """[(family, sockaddr)] of a host name, by the system's resolver, in a
    thread so the lookup counts against the time left (getaddrinfo has no
    timeout of its own)."""
    out = {}

    def run():
        try:
            out["r"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        except OSError as e:
            out["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(max(timeout, 0))
    if t.is_alive():
        raise socket.timeout("lookup")
    if "e" in out:
        raise out["e"]
    return [(family, sockaddr) for family, _, _, _, sockaddr in out["r"]]


def system_connect(family, sockaddr, timeout):
    """A TCP socket connected to exactly that address (no name lookup)."""
    s = socket.socket(family, socket.SOCK_STREAM)
    try:
        s.settimeout(max(timeout, 0.001))
        s.connect(sockaddr)
    except BaseException:
        s.close()
        raise
    return s


def tls_context():
    """Certificates and the host name checked, TLS 1.2 at least."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def system_wrap(sock, host):
    """TLS over a connected socket, sending ``host`` as SNI and checking the
    certificate against it, whatever address the socket is connected to."""
    return tls_context().wrap_socket(sock, server_hostname=host)


class _Late(Exception):
    """The time limit passed."""


class _Reader:
    """Reads a socket within a deadline: each recv gets only the time left."""

    def __init__(self, sock, deadline, clock):
        self.sock, self.deadline, self.clock = sock, deadline, clock
        self.buf = b""
        self.eof = False

    def _more(self):
        left = self.deadline - self.clock()
        if left <= 0:
            raise _Late()
        self.sock.settimeout(left)
        chunk = self.sock.recv(65536)
        if not chunk:
            self.eof = True
        self.buf += chunk

    def line(self, limit):
        """A line without its CRLF; Refused past ``limit`` bytes."""
        while b"\n" not in self.buf:
            if len(self.buf) > limit:
                raise Refused("the site's answer is malformed", 502)
            if self.eof:
                raise Refused("the site closed the connection early", 502)
            self._more()
        line, _, self.buf = self.buf.partition(b"\n")
        if len(line) > limit:
            raise Refused("the site's answer is malformed", 502)
        return line.rstrip(b"\r")

    def exactly(self, n):
        while len(self.buf) < n:
            if self.eof:
                raise Refused("the page was cut short", 502)
            self._more()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def rest(self, limit):
        """Everything until the site closes; Refused past ``limit`` bytes."""
        while not self.eof:
            if len(self.buf) > limit:
                raise Refused("page too large", 502)
            self._more()
        if len(self.buf) > limit:
            raise Refused("page too large", 502)
        out, self.buf = self.buf, b""
        return out


class Fetcher:
    """Fetches one allowed page. ``resolve(host, port, timeout)`` gives
    [(family, sockaddr)], ``connect(family, sockaddr, timeout)`` a connected
    socket and ``wrap(sock, host)`` its TLS layer; tests replace them, and
    ``clock`` and ``sleep``, so no test opens a socket or waits."""

    def __init__(self, resolve=system_resolve, connect=system_connect, wrap=system_wrap, clock=time.monotonic,
                 sleep=time.sleep, timeout=TIMEOUT, max_bytes=MAX_BYTES, max_redirects=MAX_REDIRECTS,
                 max_lookups=MAX_LOOKUPS, import_time=IMPORT_TIME):
        self.resolve, self.connect, self.wrap, self.clock, self.sleep = resolve, connect, wrap, clock, sleep
        self.timeout, self.max_bytes, self.max_redirects = timeout, max_bytes, max_redirects
        self.max_lookups, self.import_time = max_lookups, import_time

    def fetch(self, url):
        """The Page at ``url``, after at most max_redirects redirects, with
        where its redirect links go (_resolve), or raises Refused."""
        started = self.clock()
        deadline = started + self.timeout
        host, target, url = check_url(url)
        for hop in range(self.max_redirects + 1):
            try:
                kind, value = self._one(host, target, deadline)
            except _Late:
                raise Refused("timed out", 502)
            except socket.timeout:
                raise Refused("timed out", 502)
            except ssl.SSLCertVerificationError:
                raise Refused(f"{host}'s certificate is not valid", 502)
            except ssl.SSLError:
                raise Refused(f"could not set up a secure connection to {host}", 502)
            except OSError as e:
                raise Refused(f"could not reach {host}: {e.strerror or type(e).__name__}", 502)
            if kind == "page":
                return self._resolve(Page(url, value), host, started + self.import_time)
            if hop == self.max_redirects:
                break
            try:
                host, target, url = check_url(urljoin(url, value), first=False)
            except Refused as e:
                raise Refused(f"the page redirects elsewhere: {e}", 502)
        raise Refused("too many redirects", 502)

    def _resolve(self, page, host, end):
        """The page, with where its redirect links go (Page.resolved): each
        distinct one (redirect_link), in order, at most max_lookups, asked
        on the page's own host for its status line and headers only, by the
        same rules as the page, LOOKUP_GAP seconds after the request before,
        all of it before ``end``. A redirect's Location, made absolute, is
        kept as text; anything else (an error, no redirect, too late) is
        left out without a word. Nothing it names is fetched."""
        if REDIRECT_LINKS.get(site_of(host)) is None:
            return page
        found = dict.fromkeys(filter(None, (redirect_link(h, page.url) for h in read(page.text).hrefs)))
        for go in list(found)[:self.max_lookups]:
            if self.clock() + LOOKUP_GAP >= end:
                break
            self.sleep(LOOKUP_GAP)
            deadline = min(self.clock() + LOOKUP_TIMEOUT, end)
            try:
                kind, value = self._one(host, check_url(go, first=False)[1], deadline, head_only=True)
                if kind == "redirect" and len(value) <= URL_MAX:
                    page.resolved[go] = urljoin(go, value)
            except (_Late, OSError, ValueError):   # Refused, a timeout, TLS, a Location urljoin refuses
                continue
        return page

    def _addresses(self, host, deadline):
        found = self.resolve(host, PORT, deadline - self.clock())
        if not found:
            raise Refused(f"no address found for {host}", 502)
        for _, sockaddr in found:
            if not public(sockaddr[0]):
                raise Refused(f"{host}'s address is not public ({sockaddr[0]})", 502)
        return found

    def _one(self, host, target, deadline, head_only=False):
        """("page", text) or ("redirect", location) of one request; with
        ``head_only``, ("redirect", location) or ("other", status), the body
        never read."""
        if deadline - self.clock() <= 0:
            raise _Late()
        found = self._addresses(host, deadline)
        sock, error = None, None
        for family, sockaddr in found:         # each checked address in turn, never a new lookup
            left = deadline - self.clock()
            if left <= 0:
                raise _Late()
            try:
                sock = self.connect(family, sockaddr, left)
                break
            except OSError as e:
                error = e
        if sock is None:
            raise error
        try:
            left = deadline - self.clock()
            if left <= 0:
                raise _Late()
            sock.settimeout(left)              # the TLS handshake, as a whole
            tls = self.wrap(sock, host)
        except BaseException:
            sock.close()
            raise
        try:
            return self._exchange(tls, host, target, deadline, head_only)
        finally:
            tls.close()

    def _exchange(self, sock, host, target, deadline, head_only=False):
        request = (f"GET {target} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {USER_AGENT}\r\n"
                   "Accept: text/html\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n")
        left = deadline - self.clock()
        if left <= 0:
            raise _Late()
        sock.settimeout(left)
        sock.sendall(request.encode("ascii"))
        r = _Reader(sock, deadline, self.clock)
        status, headers = self._head(r)
        if status in REDIRECTS:
            location = headers.get("location")
            if not location:
                raise Refused(f"the site answered {status} with nowhere to go", 502)
            return "redirect", location
        if head_only:
            return "other", status
        if not 200 <= status < 300:
            raise Refused(f"the site answered {status}", 502)
        kind, _, params = headers.get("content-type", "").partition(";")
        if kind.strip().lower() != "text/html":
            raise Refused(f"not a web page ({kind.strip().lower()[:60] or 'no type'})", 502)
        if headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
            raise Refused("the site sent a compressed page, which is refused", 502)
        body = self._body(r, headers)
        charset = "utf-8"
        m = re.search(r"charset\s*=\s*\"?([A-Za-z0-9._-]{1,40})", params)
        if m:
            try:
                given = m.group(1).lower()
                name = codecs.lookup(_CHARSET_ALIASES.get(given, given)).name
            except LookupError:
                name = None
            if name in WEB_CHARSETS:
                charset = name
        return "page", body.decode(charset, "replace")

    def _head(self, r):
        """(status, {lowercase name: value}): the status line and headers."""
        used = 0
        line = r.line(HEAD_MAX)
        used += len(line)
        m = re.fullmatch(rb"HTTP/1\.[01] (\d{3})(?: .*)?", line)
        if m is None:
            raise Refused("the site's answer is not HTTP", 502)
        status, headers = int(m.group(1)), {}
        for _ in range(HEADERS_MAX + 1):
            line = r.line(HEAD_MAX - used)
            used += len(line) + 2
            if not line:
                return status, headers
            name, sep, value = line.partition(b":")
            if not sep or not name.strip() or name != name.strip():
                raise Refused("the site's answer is malformed", 502)
            name = name.decode("latin-1").lower()
            value = value.strip().decode("latin-1")
            if name in headers and name in ("content-length", "location", "content-type", "transfer-encoding",
                                            "content-encoding") and headers[name] != value:
                raise Refused("the site's answer is malformed", 502)
            headers[name] = value
        raise Refused("the site's answer has too many headers", 502)

    def _body(self, r, headers):
        te = headers.get("transfer-encoding", "").strip().lower()
        if te:
            if te != "chunked":
                raise Refused("the site's answer is in an encoding that is refused", 502)
            return self._chunked(r)
        length = headers.get("content-length")
        if length is not None:
            if not re.fullmatch(r"[0-9]{1,15}", length):   # isdigit() takes "²", which int() refuses
                raise Refused("the site's answer is malformed", 502)
            if int(length) > self.max_bytes:
                raise Refused("page too large", 502)
            return r.exactly(int(length))
        return r.rest(self.max_bytes)

    def _chunked(self, r):
        out, size = [], 0
        while True:
            line = r.line(1024)
            n = line.split(b";", 1)[0].strip()
            if not n or not re.fullmatch(rb"[0-9A-Fa-f]{1,8}", n):
                raise Refused("the site's answer is malformed", 502)
            n = int(n, 16)
            if n == 0:
                break
            size += n
            if size > self.max_bytes:
                raise Refused("page too large", 502)
            out.append(r.exactly(n))
            if r.exactly(2) != b"\r\n":
                raise Refused("the site's answer is malformed", 502)
        for _ in range(HEADERS_MAX + 1):     # trailers
            if not r.line(HEAD_MAX):
                return b"".join(out)
        raise Refused("the site's answer is malformed", 502)


FETCHER = Fetcher()                            # tests replace it

_busy = threading.Lock()
_last = [None]                                 # clock() of the last fetch


def fetch(url, clock=time.monotonic):
    """FETCHER.fetch(url), its redirect links' lookups with it, one at a
    time and MIN_GAP seconds apart. A link refused before anything is
    fetched does not count."""
    check_url(url)
    if not _busy.acquire(blocking=False):
        raise Refused("another link-in-bio page is being fetched; try again in a moment", 429)
    try:
        now = clock()
        if _last[0] is not None and 0 <= now - _last[0] < MIN_GAP:
            raise Refused(f"wait a few seconds between two imports (at least {MIN_GAP} s)", 429)
        _last[0] = now
        return FETCHER.fetch(url)
    finally:
        _busy.release()


# ---------------------------------------------------------------------------
# What the page links to
# ---------------------------------------------------------------------------

MAX_LINKS = 1000                               # distinct links read from a page
MAX_SUGGESTIONS = 100
MAX_SAVED = 200                                # links one import may add to a person's Links
MAX_READ = 20_000                              # links of a page looked at for that, its images and scripts too
LABEL_READ = 2000                              # characters of a link's label read from the page
_JSON_NODES = 200_000                          # values walked in the page's JSON, all blocks together
_URL_START = re.compile(r"https?://", re.IGNORECASE)


class _Links(HTMLParser):
    """The href of every <a> and <area>, and the text of JSON <script>
    blocks (Next.js' __NEXT_DATA__, application/json, application/ld+json),
    which some sites draw their links from. Nothing else is read.
    ``anchors``: the same hrefs, each with the label a visitor reads on it
    (its text, else its aria-label, title or image's alt)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs, self.blocks, self._json = [], [], None
        self.anchors, self._a, self._quiet = [], None, False

    def _close_anchor(self):
        if self._a is not None:
            href, text, spare, _ = self._a
            self.anchors.append((href, "".join(text) if "".join(text).strip() else spare))
            self._a = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("a", "area") and a.get("href"):
            self.hrefs.append(a["href"])
            self._close_anchor()               # an <a> the page never closed
            spare = a.get("aria-label") or a.get("title") or a.get("alt") or ""
            if tag == "a":
                self._a = [a["href"], [], spare, 0]   # href, text read, label without text, its length
            else:
                self.anchors.append((a["href"], spare))
        elif tag == "script":
            self._quiet = True
            kind = (a.get("type") or "").split(";")[0].strip().lower()
            if a.get("id") == "__NEXT_DATA__" or kind in ("application/json", "application/ld+json"):
                self._json = []
        elif tag == "style":
            self._quiet = True
        elif tag == "img" and self._a is not None and not self._a[2]:
            self._a[2] = a.get("alt") or ""

    def handle_data(self, data):
        if self._json is not None:
            self._json.append(data)
        elif self._a is not None and not self._quiet and self._a[3] < LABEL_READ:
            self._a[1].append(data[:LABEL_READ])
            self._a[3] += len(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._json is not None:
            self.blocks.append("".join(self._json))
            self._json = None
        if tag in ("script", "style"):
            self._quiet = False
        elif tag == "a":
            self._close_anchor()


def read(text, page_url=None, resolved=None):
    """A page parsed once, for page_links and shown_links. Malformed HTML
    gives what was read before it broke. ``resolved`` (Page.resolved): each
    redirect link of the page at ``page_url`` that answered one is read as
    the address it named, its label kept; any other stays as it is."""
    parser = _Links()
    try:
        parser.feed(text)
        parser.close()
    except Exception:                          # html.parser on broken markup: keep what it found
        pass
    if parser._json is not None:               # a JSON block the page never closed
        parser.blocks.append("".join(parser._json))
        parser._json = None
    parser._close_anchor()
    if resolved:
        to = {}
        for href in set(parser.hrefs):
            go = redirect_link(href, page_url)
            if go in resolved:
                to[href] = resolved[go]
        parser.hrefs = [to.get(h, h) for h in parser.hrefs]
        parser.anchors = [(to.get(h, h), label) for h, label in parser.anchors]
    return parser


def _json_urls(block, budget):
    """The http(s) strings anywhere in a JSON text, in order; ``budget``
    ([values left]) bounds the walk over every block of a page."""
    try:
        stack = [json.loads(block)]
    except (ValueError, RecursionError):
        return []
    out = []
    while stack and budget[0] > 0:
        budget[0] -= 1
        v = stack.pop()
        if isinstance(v, str):
            if _URL_START.match(v.strip()):
                out.append(v)
        elif isinstance(v, dict):
            stack.extend(reversed(list(v.values())))
        elif isinstance(v, list):
            stack.extend(reversed(v))
    return out


def page_links(page):
    """The distinct absolute http(s) links of a page (its text, or as read()
    gives it), in order: anchors, then JSON blocks. Every one, an image's or
    a script's address too: what profile() is asked about, not what is kept."""
    parser = page if isinstance(page, _Links) else read(page)
    found, budget = list(parser.hrefs), [_JSON_NODES]
    for block in parser.blocks:
        found.extend(_json_urls(block, budget))
    out = []
    for link in dict.fromkeys(f.strip() for f in found):
        if _URL_START.match(link):
            out.append(link)
            if len(out) >= MAX_LINKS:
                break
    return out


_BEFORE_HANDLE = {"user", "users", "u", "profile", "channel", "c", "en", "ja", "member", "creator", "artist"}


def profile(link, table, any_host=False):
    """(platform, handle, url, tool, target) of a link to a profile the
    routing table knows, else None: a post, a video, a search or a site's
    home page is not one. ``url`` is the link as sources.parse_url
    normalizes it (https, the table's host, the path checked). With
    ``any_host``, any site's profile, sources.NONE as the tool of one the
    table does not know."""
    parsed, _ = sources.parse_url(link, table, any_host)
    if parsed is None:
        return None
    url, host, tool = parsed
    platform = sources.platform_of(host)
    if tool == "instaloader":
        target = sources.parse_target("instaloader", url)
        return (platform, target, url, tool, target) if target else None
    parts = [p for p in urlsplit(url).path.split("/") if p]
    # Only words that come before a profile's name are skipped (/user/name,
    # /channel/id, /en/users/id); /shorts/<id> or /artworks/<id> is a post.
    i = next((i for i, p in enumerate(parts) if p.lower() not in _BEFORE_HANDLE), None)
    if i is None:
        return None
    handle = parts[i].lstrip("@")
    if not handle or handle.lower() in people._NOT_HANDLES or handle.lower() in sources._PAGE_WORDS:
        return None
    # A profile's own tab (/@name/videos) is still the profile; /name/status/1 is a post.
    if any(p.lower() not in sources._PAGE_WORDS for p in parts[i + 1:]):
        return None
    if platform == "tiktok" and not parts[i].startswith("@"):
        return None                            # tiktok.com/@name only
    return platform, handle, url, tool, url


def _by_name(accounts):
    """{(platform, lowercase name): {account keys}}: every handle an
    account had, its aliases and its id."""
    out = {}
    for key, a in accounts.items():
        names = {key[1], a["handle"], *(h["handle"] for h in a["handles"]), *a["aliases"]}
        for n in names:
            if n:
                out.setdefault((key[0], n.lower().lstrip("@")), set()).add(key)
    return out


def _account(a):
    return {k: a[k] for k in ("platform", "id", "handle", "name", "count", "url")}


def suggest(conn, pid, found_links, table, roots, profile_only=()):
    """[suggestion] for a person from a page's links (page_links): the
    profiles the routing table knows, and those of the ``profile_only``
    platforms (Settings → Profile only, #218) whatever the table says, each
    once, at most MAX_SUGGESTIONS.

    A suggestion: {platform, handle, url, tool, status, profile_url,
    account, person, source, profile_only}. ``status``: "linked" (theirs already, as an account or a
    source), "other" (``person``'s), "indexed" (an account in the index
    linked to nobody: add it to them), "source" (a source linked to nobody
    yet), "new" (nothing downloaded: add it as a source of theirs).
    ``profile_only``: added, it is a profile-only source (no downloader,
    tool sources.NONE, or its platform is listed)."""
    accounts = db.accounts(conn)
    by_name = _by_name(accounts)
    out, seen = [], set()
    for link in found_links:
        found = profile(link, table)
        if found is None and profile_only:
            found = profile(link, table, any_host=True)
            if found is not None and found[0] not in profile_only:
                found = None
        if found is None:
            continue
        platform, handle, url, tool, target = found
        if (platform, handle.lower()) in seen:
            continue
        seen.add((platform, handle.lower()))
        if len(out) >= MAX_SUGGESTIONS:
            break
        keys = by_name.get((platform, handle.lower()), set())
        account = accounts[next(iter(keys))] if len(keys) == 1 else None
        folder = sources.default_folder(tool, platform, target, roots) if roots else None
        sid = sources.existing(conn, tool, target, folder, platform)
        source_owner = sources._owner(conn, sources.row(conn, sid), accounts)[1] if sid is not None else None
        owner = (account or {}).get("person") or source_owner
        if owner and owner["id"] == pid:
            status = "linked"
        elif owner:
            status = "other"
        elif account:
            status = "indexed"
        elif sid is not None:
            status = "source"
        else:
            status = "new"
        # The one link the dashboard shows: built here from the platform's
        # address and the handle, never the page's own link.
        own = handle if platform != "youtube" or f"/@{handle}" in url else None
        out.append({"platform": platform, "handle": handle, "url": url, "tool": tool, "status": status,
                    "profile_url": (account or {}).get("url") or db.profile_url(platform, own),
                    "account": _account(account) if account else None,
                    "person": {"id": owner["id"], "name": owner["name"]} if owner else None, "source": sid,
                    "profile_only": tool == sources.NONE or platform in profile_only})
    return out


# ---------------------------------------------------------------------------
# A person's profiles: an account or a source, never also in their Links (#217)
# ---------------------------------------------------------------------------

def link_profile(url, table):
    """The profile a link is, as sources.profile_key names a source's
    ((platform, lowercase handle): www., m., a query or a last slash do not
    count), any site's; else None."""
    found = profile(url, table, any_host=True)
    return sources.profile_key(found[0], found[3], found[4]) if found else None


def own_profiles(conn, pid):
    """{(platform, lowercase handle)} of a person's profiles: every name
    their accounts had (the id, the posts' handles, instaloader's id files,
    renames) and each source added to them or of one of their accounts.
    Plain SQL, nothing cached: migration 31 runs it too."""
    mine = f"WITH mine(platform, author_id) AS ({db.PERSON_ACCOUNTS})"
    args = (pid,) * 3
    out = set()
    for platform, name in conn.execute(f"""{mine}
            SELECT platform, author_id FROM mine
            UNION SELECT p.platform, p.author_handle FROM posts p JOIN mine m
              ON m.platform = p.platform AND m.author_id = p.author_id
            UNION SELECT f.platform, f.handle FROM account_files f JOIN mine m
              ON m.platform = f.platform AND m.author_id = f.author_id
            UNION SELECT r.platform, r.old FROM handle_renames r JOIN mine m
              ON m.platform = r.platform AND m.author_id = r.author_id
            UNION SELECT r.platform, r.new FROM handle_renames r JOIN mine m
              ON m.platform = r.platform AND m.author_id = r.author_id""", args):
        if name:
            out.add((platform, name.lower().lstrip("@")))
    for platform, tool, target in conn.execute(f"""{mine}
            SELECT s.platform, s.tool, s.target FROM sources s WHERE s.person_id = ?
              OR EXISTS (SELECT 1 FROM mine m WHERE m.platform = s.platform AND m.author_id = s.author_id)""",
                                               (*args, pid)):
        out.add(sources.profile_key(platform, tool, target))
    return out


def own_links(conn, pid, table):
    """The ids of a person's links that are one of their profiles
    (own_profiles, link_profile): theirs as an account or a source already."""
    own = own_profiles(conn, pid)
    if not own:
        return []
    return [lid for lid, url in conn.execute(
        "SELECT l.id, l.url FROM links l JOIN link_people lp ON lp.link_id = l.id WHERE lp.person_id = ? "
        "ORDER BY lp.position, l.id", (pid,)) if link_profile(url, table) in own]


def not_own(conn, pid, found, table):
    """(``found`` without the links that are one of the person's profiles,
    how many were): what an import saves to their Links (#217). ``pid``
    None: all of it."""
    own = own_profiles(conn, pid) if pid is not None else set()
    kept = [(url, title) for url, title in found if not own or link_profile(url, table) not in own]
    return kept, len(found) - len(kept)


# ---------------------------------------------------------------------------
# What the page shows a visitor: kept in the person's Links (links.py)
# ---------------------------------------------------------------------------

# A link-in-bio site's other names: its own pages too, like its host's.
_ALSO_OWN = {"linktr.ee": ("linktree.com",), "linkin.bio": ("later.com",)}
# Sites that serve a page's parts or count its visitors, never somewhere a
# visitor goes; their subdomains too.
_ASSET_SITES = frozenset({
    "cloudfront.net", "amazonaws.com", "akamaihd.net", "akamaized.net", "akamai.net", "fastly.net", "fastlylb.net",
    "azureedge.net", "b-cdn.net", "bunnycdn.com", "cloudinary.com", "imgix.net", "jsdelivr.net", "unpkg.com",
    "cdnjs.com", "gstatic.com", "googleapis.com", "googleusercontent.com", "ggpht.com", "typekit.net",
    "fontawesome.com", "gravatar.com", "fbcdn.net", "facebook.net", "cdninstagram.com", "twimg.com", "ytimg.com",
    "tiktokcdn.com", "tiktokcdn-us.com", "googletagmanager.com", "google-analytics.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "cloudflareinsights.com", "sentry.io", "datadoghq.com",
    "newrelic.com", "nr-data.net", "bugsnag.com", "segment.com", "segment.io", "hotjar.com", "amplitude.com",
    "mixpanel.com", "statsig.com", "clarity.ms", "scorecardresearch.com", "cookielaw.org", "onetrust.com",
    "cookiebot.com", "recaptcha.net", "hcaptcha.com", "stripe.network", "r2.dev",
    "w3.org", "schema.org", "ogp.me", "gmpg.org", "xmlns.com", "purl.org",     # namespaces, not pages
})
# The same, told by a subdomain's name on any site (cdn.example.com,
# static2.example.com, api.example.com).
_ASSET_LABEL = re.compile(r"(?:cdn|static|assets?|img|images?|media|fonts?|js|css|ugc|thumbs?|thumbnails?|avatars?"
                          r"|analytics|tracking|track|pixel|telemetry|metrics|stats|sentry|api|graph|ingress)"
                          r"(?:[-0-9].*)?")
# Files a page is built from or plays, by the path's ending.
_ASSET_ENDINGS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp", ".heic", ".tif", ".tiff",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".js", ".mjs", ".css", ".map", ".json", ".webmanifest", ".wasm",
    ".mp4", ".webm", ".mov", ".m3u8", ".mp3", ".m4a", ".ogg", ".wav",
)
# Names no public site has: a link to one would open something on the
# user's own network.
_PRIVATE_ENDINGS = {"local", "localhost", "internal", "intranet", "lan", "home", "corp", "arpa", "test", "invalid"}
_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_NUMBER_RE = re.compile(r"[0-9]+|0x[0-9a-f]*")     # a last label browsers read as part of an IPv4 address
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
# In a JSON block, a link is a text under a key named for one (url, href,
# linkUrl, links, sameAs…), its label a text beside it under one of _LABEL_KEYS.
_LINK_KEY = re.compile(r".*(?:url|href|link|uri)s?|sameas|website")
_LABEL_KEYS = ("title", "label", "text", "name", "buttonText", "caption")
# Nothing under a key named for a part of the page or for the site's own
# workings is read at all (thumbnailUrl, profilePictureUrl, metaImage,
# environment, statsigInitValues, dynamicConfigs…).
_PLUMBING_KEY = re.compile(
    r"image|img|avatar|thumb|picture|photo|icon|logo|background|banner|poster|sprite|font|video|audio|asset|cdn"
    r"|css|style|theme|embed|seo|canonical|pixel|tracking|analytic|metric|endpoint|webhook|config|environment"
    r"|statsig|experiment|sponsor|datafeed|manifest|i18n|locale")
_CONSTANT_KEY = re.compile(r"[A-Z0-9]+(?:_[A-Z0-9]+)+")      # GRAPHQL_API_ENDPOINT: the site's settings
# An address written in a label: a word that starts as one.
_ADDRESS_IN_LABEL = re.compile(r"(?:https?://|www\.)\S+|(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*", re.IGNORECASE)


def _own(host):
    """The bio site's own names for a page on ``host``: the host and its
    other names (_ALSO_OWN), or for a SUBDOMAIN_SITES page the whole site,
    so every other profile and page of it is the site's own too."""
    if not host:
        return ()
    site = site_of(host)
    if site in SUBDOMAIN_SITES:
        return (site,)
    return (host, *_ALSO_OWN.get(host, ()))


def _key(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _json_shown(block, budget):
    """[(link, label)] of a JSON text, in order: the texts under a link's
    key (_LINK_KEY), outside anything named for the page's parts or the
    site's settings (_PLUMBING_KEY, _CONSTANT_KEY). ``budget`` as _json_urls."""
    try:
        stack = [(json.loads(block), "", None)]
    except (ValueError, RecursionError):
        return []
    out = []
    while stack and budget[0] > 0:
        budget[0] -= 1
        v, key, owner = stack.pop()            # a value, the key it is under, the object it is in
        if isinstance(v, str):
            if _LINK_KEY.fullmatch(key):
                out.append((v, next((owner[k] for k in _LABEL_KEYS if isinstance(owner.get(k), str)
                                     and owner[k].strip()), "") if owner is not None else ""))
        elif isinstance(v, dict):
            stack.extend((x, _key(k), v) for k, x in reversed(list(v.items()))
                         if not _CONSTANT_KEY.fullmatch(k) and not _PLUMBING_KEY.search(_key(k)))
        elif isinstance(v, list):
            stack.extend((x, key, None) for x in reversed(v))
    return out


def clean_label(text, url=None):
    """A label from the page as a saved link's title: plain text, bounded.
    Control and format characters go (an escape sequence, a right-to-left
    override, a zero-width space; the joiner inside an emoji stays),
    whitespace is collapsed, and it is cut to links.MAX_TITLE. A label
    that shows another site's address than the link's is not used at all:
    it would read as where the link goes. "" for none."""
    if not isinstance(text, str):
        return ""
    kept = []
    for c in text[:LABEL_READ]:
        kind = unicodedata.category(c)
        if kind in ("Cc", "Zl", "Zp"):
            kept.append(" ")
        elif c == "\u200d" or not kind.startswith("C"):
            kept.append(c)
    title = links.clean_title(" ".join("".join(kept).split())[:links.MAX_TITLE]) or ""
    if url is not None:
        for m in _ADDRESS_IN_LABEL.finditer(title):
            named = links.clean_url(m.group(0) if "://" in m.group(0) else "https://" + m.group(0))
            if named is None or links.site(named) != links.site(url):
                return ""
    return title


def _visitor_url(link, own):
    """(URL as links.py stores it, None), (None, "refused") or (None,
    "plumbing") for a link found in a page whose sites are ``own``.

    Refused: what links.clean_url refuses (another scheme, a login part, a
    control character, too long), a port of its own, and a host that is not
    a public site's name (an IP address in any spelling, a single word,
    .local and the like). Plumbing: the bio site's own pages, a host or a
    file that serves a page's parts. The host is stored in its ASCII form,
    so a look-alike name shows as the xn-- name it is."""
    url = links.clean_url(link)
    if url is None:
        return None, "refused"
    parts = urlsplit(url)
    try:
        if parts.port is not None:
            return None, "refused"
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, ValueError):
        return None, "refused"
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.fullmatch(x) for x in labels) or _NUMBER_RE.fullmatch(labels[-1]) \
            or labels[-1] in _PRIVATE_ENDINGS:
        return None, "refused"
    # The rest as a browser would send it (check_url does the same for the
    # page fetched): nothing that is not ASCII is stored as it is, so no
    # right-to-left override or look-alike letter shows in the address.
    try:
        rest = [quote(x, safe=_SAFE + "?") for x in (parts.path, parts.query, parts.fragment)]
    except UnicodeError:                       # half of a surrogate pair, from a JSON escape
        return None, "refused"
    url = links.clean_url(urlunsplit((parts.scheme, host, *rest)))
    if url is None:
        return None, "refused"
    if any(host == site or host.endswith("." + site) for site in (*own, *_ASSET_SITES)):
        return None, "plumbing"
    under = host[:-len(links.site(url))].split(".")        # the labels below the site's own name
    if any(_ASSET_LABEL.fullmatch(x) for x in under) or parts.path.lower().endswith(_ASSET_ENDINGS):
        return None, "plumbing"
    return url, None


def shown_links(page, page_url, table):
    """([(url, title)], dropped): what an import keeps in a person's Links.

    First the page's own address, then every link it shows a visitor, each
    once, in order: its anchors (the label is the anchor's text), the
    links of its JSON blocks (_json_shown: Linktree's buttons and icons,
    the label a button's title), and any other address in it that is a
    profile the routing table knows (what suggest() offers is always kept).
    Each goes through _visitor_url: the page's images, fonts, scripts, CDN
    and tracking addresses and the bio site's own pages are left out
    without a word, and ``dropped`` counts the links refused (a mailto: or
    javascript: link, an address on the user's own network…). URLs are as
    links.clean_url gives them, titles as clean_label does; "" for none."""
    parser = page if isinstance(page, _Links) else read(page)
    first = links.clean_url(page_url)
    own = _own(_host(urlsplit(page_url).hostname))
    found, budget = list(parser.anchors), [_JSON_NODES]
    for block in parser.blocks:
        found.extend(_json_shown(block, budget))
    found.extend((link, "") for link in page_links(parser) if profile(link, table) is not None)
    out, refused = ({first: ""} if first else {}), set()
    for link, label in found[:MAX_READ]:
        link = link.strip()
        if link.startswith("//"):
            link = "https:" + link
        if not _SCHEME_RE.match(link):
            continue                           # /a/path, #top, a word: the site's own, or no link
        if len(out) + len(refused) >= MAX_LINKS:
            break
        url, why = _visitor_url(link, own)
        if url is None:
            if why == "refused":
                refused.add(link)
            continue
        if not out.get(url) and url != first:  # the first label a link is given
            out[url] = clean_label(label, url)
    return list(out.items()), len(refused)


# ---------------------------------------------------------------------------
# What the browser shows: the userscript's import, nothing fetched
# ---------------------------------------------------------------------------

def sent_links(page_url, sent):
    """([(url, title)], dropped): what an import from the browser (the
    userscript, on a page of SITES) keeps, as shown_links does for a page
    fetched: the page's own address first, untitled, then each link once,
    its first label kept, through _visitor_url and clean_label. ``sent``
    is [(link, label)], checked as text by the caller (an http or https
    address, bounded). The bio site's own pages and a page's parts are left
    out without a word; ``dropped`` counts the links refused. Raises
    Refused for the page's address (check_url: https, on SITES, a page; the
    userscript runs on no SUBDOMAIN_SITES page)."""
    host, _, first = check_url(page_url)
    if host not in SITES:
        raise Refused(f"not an allowed site: {host} (the browser saves links from {', '.join(sorted(SITES))})")
    first = links.clean_url(first)
    own = (host, *_ALSO_OWN.get(host, ()))
    out, refused = {first: ""}, set()
    for link, label in sent:
        url, why = _visitor_url(link, own)
        if url is None:
            if why == "refused":
                refused.add(link)
            continue
        if not out.get(url) and url != first:
            out[url] = clean_label(label, url)
    return list(out.items()), len(refused)
