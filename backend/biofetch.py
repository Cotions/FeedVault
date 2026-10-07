"""Link-in-bio import (#11): the one web page FeedVault fetches itself.

The user pastes a person's linktr.ee (or similar) page and clicks Import;
FeedVault fetches that one page and suggests the accounts it links to.
Everything else that reaches the network is a downloader started as a job,
or the PyPI version check (downloaders.py). So this is kept small and
bounded:

- off unless ``bio_import`` is on in the config (Settings → Downloads);
- ``https`` on port 443 to a host on SITES only, exactly (no subdomains);
- every address the host resolves to must be public, and the connection
  goes to the address that was checked, with TLS verifying the real host
  name, so DNS cannot answer one thing to the check and another to the
  connect;
- nothing from the environment: no proxy, no .netrc, no cookies, no
  Referer; a fixed User-Agent naming FeedVault;
- at most MAX_REDIRECTS redirects, each checked again from scratch, all of
  it within TIMEOUT seconds and MAX_BYTES of body, uncompressed HTML only;
- one fetch at a time, at least MIN_GAP seconds apart, and never a link
  found in the page: the caller only parses what comes back.

The page's body is never logged or stored; its URL may be logged. It is
read with html.parser and json only (never run), and only the profile links
the routing table knows become suggestions; the user adds each one through
the usual calls.
"""
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

import config
import db
import people
import sources

# Link-in-bio sites whose every profile is a path on one host. Left out:
# sites where a profile is a subdomain or the user's own domain (carrd.co,
# bio.link, Linktree's and Beacons' custom domains, komi.io, taplink.cc's
# custom domains): a host check cannot tell one of those from any other site.
SITES = {
    "linktr.ee": "Linktree, the most used one: linktr.ee/<name>",
    "beacons.ai": "Beacons, common with TikTok and Instagram creators: beacons.ai/<name>",
    "lnk.bio": "Lnk.Bio: lnk.bio/<name>",
    "solo.to": "solo.to: solo.to/<name>",
    "campsite.bio": "Campsite: campsite.bio/<name>",
    "linkin.bio": "Later's Linkin.bio, for Instagram: linkin.bio/<name>",
    "allmylinks.com": "AllMyLinks: allmylinks.com/<name>",
}
PORT = 443
URL_MAX = 2000
TIMEOUT = 10                                   # seconds for everything: lookups, connects, TLS, every byte
MAX_BYTES = 2 * 1024 * 1024                    # of body, counted as it is read
MAX_REDIRECTS = 3
MIN_GAP = 5                                    # seconds between two fetches
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
        super().__init__(message)
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
    if host not in SITES:
        raise Refused(f"not an allowed site: {host or u.hostname or 'no host'} "
                      f"(allowed: {', '.join(sorted(SITES))})")
    path = u.path or "/"
    if first and path.strip("/") == "":
        raise Refused("paste the link to a person's page, not the site's home page")
    target = quote(path, safe=_SAFE) + (f"?{quote(u.query, safe=_SAFE + '?')}" if u.query else "")
    return host, target, f"https://{host}{target}"


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
        if ip.ipv4_mapped or ip.sixtofour or ip.teredo:
            return False
        if any(ip in n for n in _V6_TUNNELS):
            return False
    return True


_V6_TUNNELS = [ipaddress.ip_network(n) for n in (
    "::/96",                                   # IPv4-compatible (deprecated)
    "64:ff9b::/96", "64:ff9b:1::/48",          # NAT64
    "2002::/16",                               # 6to4
    "2001::/32",                               # Teredo
)]


# ---------------------------------------------------------------------------
# The fetch
# ---------------------------------------------------------------------------

class Page:
    def __init__(self, url, text):
        self.url, self.text = url, text


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
    ``clock``, so no test opens a socket."""

    def __init__(self, resolve=system_resolve, connect=system_connect, wrap=system_wrap, clock=time.monotonic,
                 timeout=TIMEOUT, max_bytes=MAX_BYTES, max_redirects=MAX_REDIRECTS):
        self.resolve, self.connect, self.wrap, self.clock = resolve, connect, wrap, clock
        self.timeout, self.max_bytes, self.max_redirects = timeout, max_bytes, max_redirects

    def fetch(self, url):
        """The Page at ``url``, after at most max_redirects redirects, or raises Refused."""
        deadline = self.clock() + self.timeout
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
                return Page(url, value)
            if hop == self.max_redirects:
                break
            try:
                host, target, url = check_url(urljoin(url, value), first=False)
            except Refused as e:
                raise Refused(f"the page redirects elsewhere: {e}", 502)
        raise Refused("too many redirects", 502)

    def _addresses(self, host, deadline):
        found = self.resolve(host, PORT, deadline - self.clock())
        if not found:
            raise Refused(f"no address found for {host}", 502)
        for _, sockaddr in found:
            if not public(sockaddr[0]):
                raise Refused(f"{host}'s address is not public ({sockaddr[0]})", 502)
        return found

    def _one(self, host, target, deadline):
        """("page", text) or ("redirect", location) of one request."""
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
            return self._exchange(tls, host, target, deadline)
        finally:
            tls.close()

    def _exchange(self, sock, host, target, deadline):
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
            charset = m.group(1)
        try:
            text = body.decode(charset, "replace")
        except (LookupError, UnicodeError, ValueError, TypeError):   # unknown, or not a text codec (idna, undefined)
            text = body.decode("utf-8", "replace")
        return "page", text

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
    """FETCHER.fetch(url), one at a time and MIN_GAP seconds apart. A link
    refused before anything is fetched does not count."""
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
_JSON_NODES = 200_000                          # values walked in the page's JSON, all blocks together
_URL_START = re.compile(r"https?://", re.IGNORECASE)


class _Links(HTMLParser):
    """The href of every <a> and <area>, and the text of JSON <script>
    blocks (Next.js' __NEXT_DATA__, application/json, application/ld+json),
    which some sites draw their links from. Nothing else is read."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs, self.blocks, self._json = [], [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("a", "area") and a.get("href"):
            self.hrefs.append(a["href"])
        elif tag == "script":
            kind = (a.get("type") or "").split(";")[0].strip().lower()
            if a.get("id") == "__NEXT_DATA__" or kind in ("application/json", "application/ld+json"):
                self._json = []

    def handle_data(self, data):
        if self._json is not None:
            self._json.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._json is not None:
            self.blocks.append("".join(self._json))
            self._json = None


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


def page_links(text):
    """The distinct absolute http(s) links of a page, in order: anchors,
    then JSON blocks. Malformed HTML gives what was read before it broke."""
    parser = _Links()
    try:
        parser.feed(text)
        parser.close()
    except Exception:                          # html.parser on broken markup: keep what it found
        pass
    if parser._json is not None:               # a JSON block the page never closed
        parser.blocks.append("".join(parser._json))
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


_BEFORE_HANDLE = {"user", "users", "u", "profile", "channel", "c", "en", "ja", "member", "creator"}


def profile(link, table):
    """(platform, handle, url, tool, target) of a link to a profile the
    routing table knows, else None: a post, a video, a search or a site's
    home page is not one. ``url`` is the link as sources.parse_url
    normalizes it (https, the table's host, the path checked)."""
    parsed, _ = sources.parse_url(link, table)
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


def suggest(conn, pid, links, table, roots):
    """{accounts: [suggestion], other: n} for a person from a page's links.

    A suggestion: {platform, handle, url, tool, status, profile_url,
    account, person, source}. ``status``: "linked" (theirs already, as an account or a
    source), "other" (``person``'s), "indexed" (an account in the index
    linked to nobody: add it to them), "source" (a source linked to nobody
    yet), "new" (nothing downloaded: add it as a source of theirs).
    ``other``: the links that are not a profile the routing table knows,
    the bio site's own pages aside."""
    accounts = db.accounts(conn)
    by_name = _by_name(accounts)
    out, seen, other = [], set(), 0
    for link in links:
        found = profile(link, table)
        if found is None:
            try:
                own = _host(urlsplit(link).hostname) in SITES
            except ValueError:                 # https://[oops: not a link at all
                own = False
            if not own:
                other += 1
            continue
        platform, handle, url, tool, target = found
        if (platform, handle.lower()) in seen:
            continue
        seen.add((platform, handle.lower()))
        if len(out) >= MAX_SUGGESTIONS:
            other += 1
            continue
        keys = by_name.get((platform, handle.lower()), set())
        account = accounts[next(iter(keys))] if len(keys) == 1 else None
        folder = sources.default_folder(tool, platform, target, roots) if roots else None
        sid = sources.existing(conn, tool, target, folder)
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
                    "person": {"id": owner["id"], "name": owner["name"]} if owner else None, "source": sid})
    return {"accounts": out, "other": other}
