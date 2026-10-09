"""Links (links.py): any web address, optionally tied to a person, never fetched."""
import json
import socket
import sqlite3
import urllib.request

import pytest

from conftest import H

import db
import links
import userdata


def get(client, url, status=200):
    r = client.get(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def post(client, url, body, status=200):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def delete(client, url, status=200):
    r = client.delete(url, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def person(client, name):
    return post(client, "/api/people", {"name": name})["person"]["id"]


def add(client, url, status=200, **body):
    return post(client, "/api/links", {"url": url, **body}, status)


def urls(client, query=""):
    return [x["url"] for x in get(client, f"/api/links{query}")["links"]]


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to reach the network fails the test (the tool guard
    refuses it too; this says why here)."""
    def refuse(*a, **k):
        raise AssertionError("links must never be fetched")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


# ---------------------------------------------------------------------------
# Cleaning and derived fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, clean", [
    ("https://Example.COM/", "https://example.com"),
    ("  https://linktr.ee/somebody \n", "https://linktr.ee/somebody"),
    ("HTTPS://www.Patreon.com:443/Somebody?a=1#top", "https://www.patreon.com/Somebody?a=1#top"),
    ("http://example.com:80/", "http://example.com"),
    ("http://example.com:8080/", "http://example.com:8080"),
    ("https://example.com/path/", "https://example.com/path/"),           # only a bare host loses its slash
    ("https://example.com/?q=1", "https://example.com/?q=1"),
    ("http://[::1]:3000/x", "http://[::1]:3000/x"),
    ("https://example.com./a", "https://example.com/a"),
])
def test_clean_url_normalizes(raw, clean):
    assert links.clean_url(raw) == clean


@pytest.mark.parametrize("raw", [
    None, 3, "", "   ", "example.com", "//example.com", "javascript:alert(1)", "JaVaScRiPt:alert(1)",
    "javascript://example.com/%0Aalert(1)", "data:text/html,<script>alert(1)</script>", "file:///etc/passwd",
    "ftp://example.com", "vbscript:msgbox(1)", "https://user:pw@example.com/", "https://user@example.com",
    "https://example.com@evil.com", "https://", "https:///path", "https://exa mple.com",
    "https://example.com/a b", "https://example.com/\x00", "https://example.com/a\tb", "http://example.com\\@evil.com",
    "https://example.com:port/", "https://%65vil.com/", "https://<script>/", "http://[not-ipv6]/",
    "https://example.com/" + "a" * links.MAX_URL,
])
def test_clean_url_refuses(raw):
    assert links.clean_url(raw) is None


@pytest.mark.parametrize("url, site, kind", [
    ("https://www.patreon.com/somebody", "patreon.com", "social"),
    ("https://linktr.ee/somebody", "linktr.ee", "social"),
    ("https://m.youtube.com/@somebody", "youtube.com", "social"),
    ("https://youtu.be/abc", "youtu.be", "social"),
    ("https://x.com/somebody", "x.com", "social"),
    ("https://bsky.app/profile/somebody", "bsky.app", "social"),
    ("https://discord.gg/abcdef", "discord.gg", "social"),
    ("https://somebody.substack.com/p/an-interview", "substack.com", "other"),
    ("https://www.bbc.co.uk/news/1", "bbc.co.uk", "other"),
    ("https://somebody.example", "somebody.example", "other"),
    ("http://192.168.1.2:8080/", "192.168.1.2", "other"),
    ("https://m.co/a", "m.co", "other"),                     # a short host keeps its name
    ("https://www.example/a", "www.example", "other"),
])
def test_site_and_kind(url, site, kind):
    assert (links.site(url), links.kind(url)) == (site, kind)


def test_kind_is_never_stored(env):
    cols = [r[1] for r in db.connect().execute("PRAGMA table_info(links)")]
    assert cols == ["id", "url", "title", "notes", "person_id", "position", "created_at", "tied_at"]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_create_edit_delete(client, no_network):
    pid = person(client, "Alice")
    r = add(client, " https://WWW.Patreon.com/alice/ ", title="  Alice's\n Patreon ", notes="tiers", person=pid)
    link = r["link"]
    assert link["url"] == "https://www.patreon.com/alice/" and link["title"] == "Alice's Patreon"
    assert (link["site"], link["kind"], link["notes"]) == ("patreon.com", "social", "tiers")
    assert link["person"] == {"id": pid, "name": "Alice"} and link["position"] == 1

    r = post(client, f"/api/links/{link['id']}", {"title": "Patreon", "notes": "", "person": None})
    assert (r["link"]["title"], r["link"]["notes"], r["link"]["person"], r["link"]["position"]) == \
        ("Patreon", "", None, None)
    r = post(client, f"/api/links/{link['id']}", {"url": "https://alice.example/about"})
    assert (r["link"]["url"], r["link"]["kind"], r["link"]["title"]) == ("https://alice.example/about", "other", "Patreon")

    delete(client, f"/api/links/{link['id']}")
    assert urls(client) == []
    assert delete(client, f"/api/links/{link['id']}", 404)["error"] == "no such link"
    post(client, f"/api/links/{link['id']}", {"title": "x"}, 404)


def test_bad_bodies(client):
    pid = person(client, "Alice")
    for body in ({}, {"url": "javascript:alert(1)"}, {"url": "https://x.com/a", "title": 3},
                 {"url": "https://x.com/a", "title": "t" * (links.MAX_TITLE + 1)},
                 {"url": "https://x.com/a", "notes": "n" * (links.MAX_NOTES + 1)},
                 {"url": "https://x.com/a", "person": "1"}, {"url": "https://x.com/a", "person": True},
                 {"url": "https://x.com/a", "person": pid + 100}, {"url": "https://x.com/a", "person": 2**70}):
        assert post(client, "/api/links", body, 400)["ok"] is False
    lid = add(client, "https://x.com/a")["link"]["id"]
    post(client, f"/api/links/{lid}", {}, 400)
    post(client, f"/api/links/{lid}", {"url": "data:text/html,x"}, 400)
    post(client, f"/api/links/{lid}", {"person": pid + 100}, 400)
    assert get(client, "/api/links?kind=nope", 400)["ok"] is False
    get(client, "/api/links?q=" + "q" * (links.MAX_QUERY + 1), 400)
    assert urls(client) == ["https://x.com/a"]


@pytest.mark.parametrize("raw", ["[]", '["https://x.com/a"]', '"https://x.com/a"', "3", "true", "null"])
def test_a_body_that_is_not_an_object_is_a_400(client, raw):
    pid = person(client, "Alice")
    lid = add(client, "https://x.com/a", person=pid)["link"]["id"]
    for url in ("/api/links", f"/api/links/{lid}", f"/api/people/{pid}/links/order"):
        r = client.post(url, data=raw, headers={**H, "Content-Type": "application/json"})
        assert r.status_code == 400, (url, raw, r.status_code)
        body = r.get_json()
        assert body["ok"] is False
        if raw != "null":                      # no body to speak of: the fields' own errors
            assert body["error"] == "the body must be a JSON object"
    [link] = get(client, "/api/links")["links"]
    assert (link["url"], link["title"], link["person"]["id"]) == ("https://x.com/a", "", pid)


def test_duplicate_url_is_a_409_with_the_existing_id(client):
    first = add(client, "https://linktr.ee/alice")["link"]["id"]
    r = add(client, "HTTPS://LINKTR.EE/alice", 409)
    assert r == {"ok": False, "error": "that link is saved already", "id": first,
                 "link": get(client, "/api/links")["links"][0]}
    assert r["link"]["id"] == first and r["link"]["person"] is None
    other = add(client, "https://linktr.ee/bob")["link"]["id"]
    assert post(client, f"/api/links/{other}", {"url": "https://linktr.ee/alice"}, 409)["id"] == first
    post(client, f"/api/links/{first}", {"url": "https://linktr.ee/alice/"})       # its own URL, changed
    post(client, f"/api/links/{first}", {"url": "https://linktr.ee/alice/"})       # unchanged: fine


def test_duplicate_409_names_the_person_it_is_tied_to(client):
    alice = person(client, "Alice")
    first = add(client, "https://alice.example/shop", person=alice, title="Shop")["link"]
    r = add(client, "https://alice.example/shop", 409, person=None, title="again")
    assert r["link"] == first
    assert r["link"]["person"] == {"id": alice, "name": "Alice"}
    other = add(client, "https://bob.example")["link"]["id"]
    r = post(client, f"/api/links/{other}", {"url": "https://alice.example/shop"}, 409)
    assert (r["id"], r["link"]["person"]["name"]) == (first["id"], "Alice")


# ---------------------------------------------------------------------------
# Recent people (the quick-add's picker)
# ---------------------------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    """The links routes' clock (app._link_now), moved by hand."""
    import app
    now = [1_000_000]
    monkeypatch.setattr(app, "_link_now", lambda: now[0])
    return now


def recent(client, query=""):
    return [(x["name"], x["at"]) for x in get(client, f"/api/people/recent-links{query}")["people"]]


def test_recent_people_by_when_a_link_was_given(client, clock):
    alice, bob, cleo = person(client, "Alice"), person(client, "Bob"), person(client, "Cleo")
    person(client, "Dan")                                   # no link: never listed
    assert recent(client) == []
    add(client, "https://alice.example/1", person=alice)
    clock[0] += 10
    add(client, "https://bob.example", person=bob)
    clock[0] += 10
    add(client, "https://untied.example")                   # no one: counts for no one
    loose = add(client, "https://loose.example")["link"]["id"]
    clock[0] += 10
    add(client, "https://alice.example/2", person=alice)    # Alice again: her latest counts
    assert recent(client) == [("Alice", 1_000_030), ("Bob", 1_000_010)]

    # A link given later counts from then, not from when it was saved.
    clock[0] += 10
    post(client, f"/api/links/{loose}", {"person": cleo})
    assert recent(client)[0] == ("Cleo", 1_000_040)
    # An edit that keeps its person changes nothing.
    clock[0] += 10
    post(client, f"/api/links/{loose}", {"person": cleo, "title": "renamed"})
    assert recent(client)[0] == ("Cleo", 1_000_040)
    # Untied again: Cleo has no link left, so she is gone from the list.
    post(client, f"/api/links/{loose}", {"person": None})
    assert recent(client) == [("Alice", 1_000_030), ("Bob", 1_000_010)]
    assert db.connect().execute("SELECT tied_at FROM links WHERE id = ?", (loose,)).fetchone()[0] is None

    assert recent(client, "?limit=1") == [("Alice", 1_000_030)]
    # A limit out of range is kept in it, one that is not a number is the default.
    assert len(recent(client, "?limit=0")) == 1
    assert len(recent(client, "?limit=999")) == 2
    assert len(recent(client, "?limit=x")) == 2


def test_recent_people_default_is_ten(client, clock):
    for n in range(12):
        clock[0] += 1
        add(client, f"https://p{n}.example", person=person(client, f"P{n}"))
    assert [name for name, _ in recent(client)] == [f"P{n}" for n in range(11, 1, -1)]


def test_recent_people_forget_a_deleted_person(client, clock):
    alice, bob = person(client, "Alice"), person(client, "Bob")
    add(client, "https://alice.example", person=alice)
    clock[0] += 1
    lid = add(client, "https://bob.example", person=bob)["link"]["id"]
    delete(client, f"/api/people/{bob}")
    assert recent(client) == [("Alice", 1_000_000)]
    assert tuple(db.connect().execute("SELECT person_id, tied_at FROM links WHERE id = ?", (lid,)).fetchone()) \
        == (None, None)


def test_tied_at_survives_a_restore(env, client, clock):
    data = str(env["tmp"] / "data")
    alice, bob = person(client, "Alice"), person(client, "Bob")
    lid = add(client, "https://a.example")["link"]["id"]
    add(client, "https://b.example", person=bob)
    clock[0] += 100
    post(client, f"/api/links/{lid}", {"person": alice})
    conn = db.connect()
    for name in ("people", "links"):
        userdata.export(conn, name, data)
    with conn:
        conn.execute("DELETE FROM links")
    assert userdata.load(conn, "links", data) == 2
    assert recent(client) == [("Alice", 1_000_100), ("Bob", 1_000_000)]


def test_restore_from_a_file_without_tied_at(env, client):
    data = str(env["tmp"] / "data")
    (env["tmp"] / "data" / "userdata").mkdir(parents=True, exist_ok=True)
    with open(userdata.path(data, "links"), "w") as f:
        json.dump({"version": 1, "rows": [
            {"url": "https://a.example", "person": "Alice", "created_at": 50},
            {"url": "https://b.example", "person": "Bob", "created_at": 70, "tied_at": "soon"},
            {"url": "https://c.example", "person": None, "created_at": 90, "tied_at": 95},
        ]}, f)
    conn = db.connect()
    assert userdata.load(conn, "links", data) == 3
    assert recent(client) == [("Bob", 70), ("Alice", 50)]
    assert conn.execute("SELECT tied_at FROM links WHERE url = 'https://c.example'").fetchone()[0] is None


def test_migration_counts_a_tied_link_from_when_it_was_saved(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    for migrate in db.MIGRATIONS[:22]:
        migrate(conn)
    conn.execute("INSERT INTO people(id, name, created_at) VALUES (1, 'Alice', 0)")
    conn.execute("INSERT INTO links(url, person_id, position, created_at) VALUES ('https://a.example', 1, 1, 42)")
    conn.execute("INSERT INTO links(url, created_at) VALUES ('https://b.example', 43)")
    db.MIGRATIONS[22](conn)
    assert conn.execute("SELECT url, tied_at FROM links ORDER BY id").fetchall() == [
        ("https://a.example", 42), ("https://b.example", None)]


def test_filters(client):
    alice, bob = person(client, "Alice"), person(client, "Bob")
    add(client, "https://www.patreon.com/alice", person=alice, title="Support")
    add(client, "https://alice.example", person=alice, notes="her shop")
    add(client, "https://x.com/bob", person=bob)
    add(client, "https://news.example/an-interview-with_bob", title="Interview")
    add(client, "https://discord.gg/abc")

    assert len(urls(client)) == 5
    assert urls(client, "?kind=social") == ["https://discord.gg/abc", "https://x.com/bob", "https://www.patreon.com/alice"]
    assert urls(client, "?kind=other") == ["https://news.example/an-interview-with_bob", "https://alice.example"]
    assert urls(client, f"?person={alice}") == ["https://www.patreon.com/alice", "https://alice.example"]
    assert urls(client, f"?person={alice}&kind=other") == ["https://alice.example"]
    assert urls(client, "?person=none") == ["https://discord.gg/abc", "https://news.example/an-interview-with_bob"]
    assert urls(client, "?person=nobody") == [] and urls(client, "?person=99999") == []
    assert urls(client, "?site=patreon.com") == ["https://www.patreon.com/alice"]
    assert urls(client, "?q=SHOP") == ["https://alice.example"]                     # notes, any case
    assert urls(client, "?q=support") == ["https://www.patreon.com/alice"]          # title
    assert urls(client, "?q=interview-with_") == ["https://news.example/an-interview-with_bob"]
    assert urls(client, "?q=%25") == [] and urls(client, "?q=_") == ["https://news.example/an-interview-with_bob"]
    sites = get(client, f"/api/links?person={bob}")["sites"]                  # over every link, for the filter
    assert {s["site"]: s["count"] for s in sites} == {
        "patreon.com": 1, "alice.example": 1, "x.com": 1, "news.example": 1, "discord.gg": 1}


def test_person_shows_its_links_socials_first_then_order(client):
    alice = person(client, "Alice")
    site = add(client, "https://alice.example", person=alice)["link"]["id"]
    patreon = add(client, "https://patreon.com/alice", person=alice)["link"]["id"]
    talk = add(client, "https://talks.example/alice", person=alice)["link"]["id"]
    insta = add(client, "https://instagram.com/alice", person=alice)["link"]["id"]
    add(client, "https://nobody.example")
    got = get(client, f"/api/people/{alice}")["links"]
    assert [x["id"] for x in got] == [patreon, insta, site, talk]

    r = post(client, f"/api/people/{alice}/links/order", {"ids": [talk, site]})
    assert [x["id"] for x in r["links"]] == [patreon, insta, talk, site]
    r = post(client, f"/api/people/{alice}/links/order", {"ids": [insta, patreon, 999999]})   # unknown ids ignored
    assert [x["id"] for x in r["links"]] == [insta, patreon, talk, site]
    assert [x["id"] for x in get(client, f"/api/people/{alice}")["links"]] == [insta, patreon, talk, site]
    for body in ({}, {"ids": []}, {"ids": ["1"]}, {"ids": [True]}, {"ids": list(range(links.MAX_IDS + 1))}):
        post(client, f"/api/people/{alice}/links/order", body, 400)
    post(client, "/api/people/999999/links/order", {"ids": [site]}, 404)


def test_reorder_numbers_equal_places_first(env, client):
    alice = person(client, "Alice")
    a, b, c = (add(client, f"https://{n}.example", person=alice)["link"]["id"] for n in "abc")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE links SET position = 1")                  # as an old or hand-edited file could leave
    r = post(client, f"/api/people/{alice}/links/order", {"ids": [c, a]})
    assert [x["id"] for x in r["links"]] == [c, b, a]
    assert sorted(x["position"] for x in r["links"]) == [1, 2, 3]


def test_reorder_keeps_links_with_no_place_last(env, client):
    alice = person(client, "Alice")
    a, b, c, d = (add(client, f"https://{n}.example", person=alice)["link"]["id"] for n in "abcd")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE links SET position = NULL WHERE id IN (?, ?)", (c, d))   # as a hand-edited file could leave
    assert [x["id"] for x in get(client, f"/api/people/{alice}")["links"]] == [a, b, c, d]
    r = post(client, f"/api/people/{alice}/links/order", {"ids": [d, c]})             # "move d up"
    assert [x["id"] for x in r["links"]] == [a, b, d, c]
    r = post(client, f"/api/people/{alice}/links/order", {"ids": [d, b]})
    assert [x["id"] for x in r["links"]] == [a, d, b, c]


def test_a_link_given_to_another_person_goes_last(client):
    alice, bob = person(client, "Alice"), person(client, "Bob")
    add(client, "https://b1.example", person=bob)
    lid = add(client, "https://a1.example", person=alice)["link"]["id"]
    r = post(client, f"/api/links/{lid}", {"person": bob})
    assert r["link"]["person"]["id"] == bob and r["link"]["position"] == 2


def test_deleting_a_person_keeps_their_links(client):
    alice = person(client, "Alice")
    lid = add(client, "https://linktr.ee/alice", person=alice)["link"]["id"]
    delete(client, f"/api/people/{alice}")
    [link] = get(client, "/api/links")["links"]
    assert (link["id"], link["person"], link["position"]) == (lid, None, None)
    assert urls(client, "?person=none") == ["https://linktr.ee/alice"]


def test_merging_people_moves_links_to_the_kept_one(client):
    alice, alt, other = person(client, "Alice"), person(client, "Alice (alt)"), person(client, "Other")
    a1 = add(client, "https://a1.example", person=alice)["link"]["id"]
    b1 = add(client, "https://b1.example", person=alt)["link"]["id"]
    b2 = add(client, "https://b2.example", person=alt)["link"]["id"]
    o1 = add(client, "https://o1.example", person=other)["link"]["id"]
    post(client, f"/api/people/{alt}/links/order", {"ids": [b2, b1]})
    post(client, "/api/people/merge", {"ids": [alice, alt]})
    got = get(client, f"/api/people/{alice}")["links"]
    assert [x["id"] for x in got] == [a1, b2, b1]
    assert [x["position"] for x in got] == [1, 2, 3]
    assert [x["id"] for x in get(client, f"/api/people/{other}")["links"]] == [o1]


# ---------------------------------------------------------------------------
# User data
# ---------------------------------------------------------------------------

def test_userdata_round_trip_by_person_name(env, client):
    data = str(env["tmp"] / "data")
    alice = person(client, "Alice")
    add(client, "https://www.patreon.com/alice", person=alice, title="Patreon", notes="tiers")
    add(client, "https://alice.example", person=alice)
    add(client, "https://news.example/a", title="An article")
    post(client, f"/api/people/{alice}/links/order",
         {"ids": [x["id"] for x in reversed(get(client, f"/api/people/{alice}")["links"])]})
    conn = db.connect()
    for name in ("people", "links"):
        userdata.export(conn, name, data)
    with open(userdata.path(data, "links")) as f:
        rows = json.load(f)["rows"]
    assert rows == [
        {"url": "https://alice.example", "title": "", "notes": "", "person": "Alice", "position": 1,
         "created_at": rows[0]["created_at"], "tied_at": rows[0]["created_at"], "id": 2},
        {"url": "https://news.example/a", "title": "An article", "notes": "", "person": None, "position": None,
         "created_at": rows[1]["created_at"], "tied_at": None, "id": 3},
        {"url": "https://www.patreon.com/alice", "title": "Patreon", "notes": "tiers", "person": "Alice",
         "position": 2, "created_at": rows[2]["created_at"], "tied_at": rows[2]["created_at"], "id": 1},
    ]

    # A rebuilt index: each link gets its id back, the person is found again by name.
    with conn:
        conn.execute("DELETE FROM links")
        conn.execute("DELETE FROM people")
        conn.execute("INSERT INTO people(name, created_at) VALUES ('Somebody else', 0)")
    userdata.load(conn, "people", data)                # not empty: left alone
    assert userdata.load(conn, "links", data) == 3
    [p] = [x for x in get(client, "/api/people") if x["name"] == "Alice"]   # created again from links.json
    got = get(client, f"/api/people/{p['id']}")["links"]
    assert [(x["url"], x["title"], x["position"]) for x in got] == [
        ("https://www.patreon.com/alice", "Patreon", 2), ("https://alice.example", "", 1)]
    assert urls(client, "?person=none") == ["https://news.example/a"]
    assert [tuple(r) for r in conn.execute("SELECT id, url FROM links ORDER BY id")] == [
        (1, "https://www.patreon.com/alice"), (2, "https://alice.example"), (3, "https://news.example/a")]


def test_restore_skips_a_url_that_is_not_http(env):
    data = str(env["tmp"] / "data")
    path = userdata.path(data, "links")
    (env["tmp"] / "data" / "userdata").mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"version": 1, "rows": [{"url": "javascript:alert(1)", "title": "x"},
                                          {"url": "https://ok.example", "person": None}]}, f)
    conn = db.connect()
    assert userdata.load(conn, "links", data) == 1
    assert [r[0] for r in conn.execute("SELECT url FROM links")] == ["https://ok.example"]


def test_restore_cleans_a_row_as_the_api_would(env):
    data = str(env["tmp"] / "data")
    (env["tmp"] / "data" / "userdata").mkdir(parents=True, exist_ok=True)
    with open(userdata.path(data, "links"), "w") as f:
        json.dump({"version": 1, "rows": [
            {"url": "HTTPS://X.COM/a", "title": "  An   x  ", "position": "2", "person": None},
            {"url": "https://user@evil.example/", "title": "credentials"},
            {"url": "https://exa mple.com", "title": "a space"},
            {"url": "https://ok.example", "title": "t" * (links.MAX_TITLE + 1)},
            {"url": "https://notes.example", "notes": ["not", "text"]},
            {"url": "https://alice.example", "person": "Alice", "position": True},
        ]}, f)
    conn = db.connect()
    assert userdata.load(conn, "links", data) == 2
    assert [tuple(r) for r in conn.execute("SELECT url, title, position FROM links ORDER BY url")] == [
        ("https://alice.example", "", None), ("https://x.com/a", "An x", None)]


def test_changes_mark_links_for_export(client, monkeypatch):
    seen = []
    monkeypatch.setattr(userdata, "changed", seen.append)
    alice = person(client, "Alice")
    lid = add(client, "https://alice.example", person=alice)["link"]["id"]
    post(client, f"/api/links/{lid}", {"title": "Site"})
    post(client, f"/api/people/{alice}/links/order", {"ids": [lid]})
    post(client, f"/api/people/{alice}", {"name": "Alice B"})          # exported by person name
    delete(client, f"/api/links/{lid}")
    assert seen.count("links") >= 5
