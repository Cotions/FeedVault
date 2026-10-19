"""A link-in-bio page's links sent by the userscript (POST /api/links/import),
its people picker (GET /api/links/people) and "Add this page" (POST
/api/links from another site).

Nothing is fetched: the links are what the browser rendered. The fetcher
is a fake that fails the test if it is used, and so does any socket.
"""
import socket
import urllib.request

import pytest

from conftest import H

import biofetch
import links

IG = {**H, "Origin": "https://linktr.ee", "Sec-Fetch-Site": "cross-site"}     # as the userscript's requests come
PAGE = "https://linktr.ee/somebody"
SENT = [
    {"url": "https://www.instagram.com/somebody/", "title": "Instagram"},
    {"url": "https://x.com/somebody", "title": "  My   X "},
    {"url": "https://shop.example/somebody?ref=linktree", "title": "Shop my picks"},
    {"url": "http://old-site.example/"},
    {"url": "https://x.com/somebody", "title": "X again"},                    # once, its first label
    {"url": "https://linktr.ee/s/terms", "title": "Terms"},                   # the site's own
    {"url": "https://linktree.com/privacy", "title": "Privacy"},
    {"url": "https://cdn.example.com/logo.png", "title": ""},                 # a page's part
    {"url": "https://192.168.1.1/admin", "title": "router"},                  # refused
    {"url": "https://printer.local/", "title": "printer"},
    {"url": "https://other.example/", "title": "https://evil.example/ looks like"},   # a label naming another site
]


class Untouchable:
    def fetch(self, url):
        raise AssertionError("fetched")


@pytest.fixture
def on(env, client, monkeypatch):
    """The switch on, nothing reachable: no fetcher, no socket."""
    def refuse(*a, **k):
        raise AssertionError("nothing is fetched")
    monkeypatch.setattr(biofetch, "FETCHER", Untouchable())
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert client.post("/api/config", json={"bio_import": True}, headers=H).get_json()["ok"]
    return client


def person(client, name):
    return client.post("/api/people", json={"name": name}, headers=H).get_json()["person"]["id"]


def send(client, body, status=200, headers=IG):
    r = client.post("/api/links/import", json=body, headers=headers)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def saved(client, query):
    return [(x["url"], x["title"]) for x in client.get(f"/api/links?{query}", headers=H).get_json()["links"]]


def test_the_links_shown_are_saved_to_the_person(on):
    alice = person(on, "Alice")
    r = send(on, {"page_url": PAGE, "links": SENT, "person": alice})
    assert r == {"ok": True, "url": PAGE, "person": alice,
                 "links": {"saved": 6, "already": 0, "elsewhere": 0, "over": 0, "dropped": 2}}
    got = on.get(f"/api/people/{alice}", headers=H).get_json()["links"]
    assert [(x["url"], x["title"]) for x in sorted(got, key=lambda x: x["position"])] == [
        (PAGE, ""), ("https://www.instagram.com/somebody/", "Instagram"), ("https://x.com/somebody", "My X"),
        ("https://shop.example/somebody?ref=linktree", "Shop my picks"), ("http://old-site.example", ""),
        ("https://other.example", "")]
    assert saved(on, "person=none") == []


def test_to_no_one(on):
    r = send(on, {"page_url": PAGE, "links": SENT[:2]})
    assert r["person"] is None and r["links"]["saved"] == 3
    assert sorted(saved(on, "person=none")) == sorted([(PAGE, ""), ("https://www.instagram.com/somebody/", "Instagram"),
                                                       ("https://x.com/somebody", "My X")])
    assert all(x["position"] is None for x in on.get("/api/links?person=none", headers=H).get_json()["links"])
    # Again, with null: nothing new.
    assert send(on, {"page_url": PAGE, "links": SENT[:2], "person": None})["links"] == {
        "saved": 0, "already": 3, "elsewhere": 0, "over": 0, "dropped": 0}


def test_insert_only(on):
    alice, bob = person(on, "Alice"), person(on, "Bob")
    mine = on.post("/api/links", json={"url": "https://x.com/somebody", "title": "Mine", "notes": "n", "person": alice},
                   headers=H).get_json()["link"]
    on.post("/api/links", json={"url": "https://shop.example/somebody?ref=linktree", "title": "Bob's", "person": bob},
            headers=H)
    r = send(on, {"page_url": PAGE, "links": SENT[:3], "person": alice})
    assert r["links"] == {"saved": 2, "already": 1, "elsewhere": 1, "over": 0, "dropped": 0}
    got = {x["url"]: x for x in on.get(f"/api/people/{alice}", headers=H).get_json()["links"]}
    assert got["https://x.com/somebody"] == mine                      # not retitled, not moved
    assert [x["title"] for x in on.get(f"/api/people/{bob}", headers=H).get_json()["links"]] == ["Bob's"]
    # Sent again with fewer links: nothing is removed.
    send(on, {"page_url": PAGE, "links": [], "person": alice})
    assert len(on.get(f"/api/people/{alice}", headers=H).get_json()["links"]) == 3


def test_the_cap(on, monkeypatch):
    alice = person(on, "Alice")
    many = [{"url": f"https://shop.example/{n}", "title": str(n)} for n in range(biofetch.MAX_LINKS)]
    r = send(on, {"page_url": PAGE, "links": many, "person": alice})
    assert r["links"]["saved"] == biofetch.MAX_SAVED and r["links"]["over"] == biofetch.MAX_LINKS + 1 - biofetch.MAX_SAVED
    r = send(on, {"page_url": PAGE, "links": many + [{"url": "https://shop.example/x"}], "person": alice}, 400)
    assert "at most 1000" in r["error"]


@pytest.mark.parametrize("body", [
    None, [], "x", {}, {"page_url": PAGE}, {"links": []}, {"page_url": PAGE, "links": [], "extra": 1},
    {"page_url": 3, "links": []}, {"page_url": PAGE, "links": {}}, {"page_url": PAGE, "links": ["https://a.example"]},
    {"page_url": PAGE, "links": [{"title": "no url"}]}, {"page_url": PAGE, "links": [{"url": 3}]},
    {"page_url": PAGE, "links": [{"url": "https://a.example", "notes": "x"}]},
    {"page_url": PAGE, "links": [{"url": "javascript:alert(1)"}]}, {"page_url": PAGE, "links": [{"url": "mailto:a@b.example"}]},
    {"page_url": PAGE, "links": [{"url": "//a.example/x"}]}, {"page_url": PAGE, "links": [{"url": "https://a.example/" + "a" * 2048}]},
    {"page_url": PAGE, "links": [{"url": "https://a.example", "title": 3}]},
    {"page_url": PAGE, "links": [{"url": "https://a.example", "title": "t" * (biofetch.LABEL_READ + 1)}]},
    {"page_url": PAGE, "links": [], "person": "1"}, {"page_url": PAGE, "links": [], "person": True},
    {"page_url": PAGE, "links": [], "person": 999},
])
def test_bad_bodies(on, body):
    r = on.post("/api/links/import", json=body, headers=IG)
    assert r.status_code == 400, r.get_json()
    assert saved(on, "") == []


@pytest.mark.parametrize("page, error", [
    ("https://evil.example/somebody", "not an allowed site"),
    ("http://linktr.ee/somebody", "only https links"),
    ("https://linktr.ee/", "not the site's home page"),
    ("https://linktr.ee.evil.example/somebody", "not an allowed site"),
    ("https://user@linktr.ee/somebody", "login part"),
    ("https://linktr.ee:8443/somebody", "443"),
])
def test_only_a_page_of_a_link_in_bio_site(on, page, error):
    assert error in send(on, {"page_url": page, "links": SENT}, 400)["error"]
    assert saved(on, "") == []


def test_switched_off(env, client, monkeypatch):
    monkeypatch.setattr(biofetch, "FETCHER", Untouchable())
    r = send(client, {"page_url": PAGE, "links": SENT}, 403)
    assert r == {"ok": False, "error": app_bio_off()}
    assert saved(client, "") == []


def app_bio_off():
    import app
    return app.BIO_OFF


def test_header_and_host_still_required(on):
    r = on.post("/api/links/import", json={"page_url": PAGE, "links": SENT}, headers={"Origin": "https://linktr.ee"})
    assert r.status_code == 403 and r.get_json()["error"] == "missing X-FeedVault header"
    r = on.post("/api/links/import", json={"page_url": PAGE, "links": SENT}, headers={**IG, "Host": "evil.example"})
    assert r.status_code == 403 and r.get_json()["error"] == "forbidden host"
    assert on.get("/api/links/people", headers={"Origin": "https://linktr.ee"}).status_code == 403
    assert saved(on, "") == []


def test_another_site_reaches_only_these_link_routes(on):
    alice = person(on, "Alice")
    lid = on.post("/api/links", json={"url": "https://a.example", "person": alice}, headers=H).get_json()["link"]["id"]
    for method, url, body in (("POST", f"/api/people/{alice}/bio-import", {"url": PAGE}), ("GET", "/api/links", None),
                              ("POST", f"/api/links/{lid}", {"title": "x"}), ("DELETE", f"/api/links/{lid}", None),
                              ("POST", "/api/links/assign", {"ids": [lid], "person": None}), ("GET", "/api/people", None),
                              ("GET", "/api/people/recent-links", None), ("POST", "/api/links/delete", {"ids": [lid]})):
        r = on.open(url, method=method, json=body, headers=IG)
        assert r.status_code == 403 and "own dashboard" in r.get_json()["error"], url
    assert on.get(f"/api/people/{alice}", headers=H).get_json()["links"][0]["title"] == ""


def test_the_people_picker_gives_ids_and_names_only(on):
    bob, alice = person(on, "Bob"), person(on, "alice")
    on.post(f"/api/people/{bob}", json={"notes": "private"}, headers=H)
    on.post("/api/links", json={"url": "https://a.example", "person": bob}, headers=H)
    r = on.get("/api/links/people", headers=IG)
    assert r.status_code == 200
    assert r.get_json() == {"people": [{"id": alice, "name": "alice"}, {"id": bob, "name": "Bob"}], "recent": [bob]}


def test_add_this_page_from_another_site(on):
    alice = person(on, "Alice")
    r = on.post("/api/links", json={"url": "https://x.com/somebody/status/1", "title": "A post"}, headers=IG)
    assert r.status_code == 200 and r.get_json()["link"]["person"] is None
    r = on.post("/api/links", json={"url": "https://x.com/somebody/status/1", "title": "A post"}, headers=IG)
    assert r.status_code == 409
    # Only a URL and a title: never a person or notes.
    for extra in ({"person": alice}, {"notes": "n"}, {"person": None}):
        r = on.post("/api/links", json={"url": "https://b.example", **extra}, headers=IG)
        assert r.status_code == 403 and r.get_json()["error"] == "from another site, a link is saved with a url and a title only, to no one"
    assert on.get(f"/api/people/{alice}", headers=H).get_json()["links"] == []
    # The dashboard still sends what it likes.
    assert on.post("/api/links", json={"url": "https://b.example", "person": alice}, headers=H).status_code == 200


def test_keep_to_no_one_counts_another_persons_link_elsewhere(env, client):
    alice = person(client, "Alice")
    client.post("/api/links", json={"url": "https://a.example", "person": alice}, headers=H)
    client.post("/api/links", json={"url": "https://b.example"}, headers=H)
    import db
    found = [("https://a.example", "x"), ("https://www.b.example/", "y"), ("https://c.example", "z")]
    assert links.keep(db.connect(), None, found, 5, 10) == {"saved": 1, "already": 1, "elsewhere": 1, "over": 0}
