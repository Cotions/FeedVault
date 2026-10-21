"""Link labels (linklabels.py): Settings → Link labels names the links with
no title by their site, at each read, never in the stored link."""
import json

import pytest

from conftest import H

import config
import db
import linklabels


def get(client, url):
    r = client.get(url, headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def post(client, url, body, status=200):
    r = client.post(url, json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def add(client, url, **body):
    return post(client, "/api/links", {"url": url, **body})["link"]


def set_labels(client, rules):
    return post(client, "/api/config", {"link_labels": rules})


def labels(client, query=""):
    return {x["url"]: x["label"] for x in get(client, f"/api/links{query}")["links"]}


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("given, site", [
    ("patreon.com", "patreon.com"),
    ("https://www.patreon.com/somebody", "patreon.com"),
    ("  WWW.Patreon.COM/  ", "patreon.com"),
    ("http://someone.substack.com", "substack.com"),
    ("www.bbc.co.uk/news", "bbc.co.uk"),
    ("//ko-fi.com/x", "ko-fi.com"),
    ("https://patreon.com:443/x?y=1#z", "patreon.com"),
    ("192.168.1.20", "192.168.1.20"),
    ("http://[::1]:8080/", "::1"),
])
def test_a_site_is_cut_as_a_links_site_is(given, site):
    assert linklabels.clean_site(given) == site


@pytest.mark.parametrize("given", [
    "", "   ", None, 3, ["patreon.com"], "localhost", "patreon", "javascript:alert(1)", "ftp://patreon.com",
    "https://user@patreon.com", "patreon .com", "patreon.com\\x", "a..com", "https://", "x" * 3000,
])
def test_a_site_that_names_no_host_is_refused(given):
    assert linklabels.clean_site(given) is None


def test_a_label_is_trimmed_and_capped():
    assert linklabels.clean_label("  My   Patreon \n") == "My Patreon"
    assert linklabels.clean_label("x" * linklabels.MAX_LABEL) == "x" * linklabels.MAX_LABEL
    for bad in ("", "   ", None, 7, "x" * (linklabels.MAX_LABEL + 1), "a\x00b", "a\x7fb", "a\x9bb", "\ud800"):
        assert linklabels.clean_label(bad) is None, bad


def test_rules_are_cleaned_and_a_site_given_twice_is_refused():
    assert linklabels.clean_rules({"https://www.Patreon.com/x": " Patreon "}) == ({"patreon.com": "Patreon"}, None)
    assert linklabels.clean_rules({}) == ({}, None)
    _, error = linklabels.clean_rules({"patreon.com": "A", "www.patreon.com": "B"})
    assert error == "patreon.com is given twice"
    assert linklabels.clean_rules({"nope": "A"})[1] == "not a site: nope"
    assert "label" in linklabels.clean_rules({"patreon.com": ""})[1]
    assert linklabels.clean_rules(["patreon.com"])[1]
    too_many = {f"site{n}.example": "x" for n in range(linklabels.MAX_RULES + 1)}
    assert linklabels.clean_rules(too_many)[1]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_a_rule_wins_else_an_unsynced_site_gets_its_own_name_else_none(env):
    name = linklabels.namer({"link_labels": {"patreon.com": "Patreon", "youtube.com": "YT"}})
    assert name("patreon.com") == "Patreon"
    assert name("youtube.com") == "YT"                       # a rule wins even for a synced site
    assert name("ko-fi.com") == "Ko-fi.com"
    assert name("bbc.co.uk") == "Bbc.co.uk"
    assert name("192.168.1.20") == "192.168.1.20"
    assert name("instagram.com") is None                     # FeedVault syncs it: the address says whose
    assert name("x.com") is None


def test_the_routing_table_in_use_decides_what_is_synced(env):
    name = linklabels.namer({"routes": {"patreon.com": "gallery-dl"}})
    assert name("patreon.com") is None
    assert name("instagram.com") == "Instagram.com"          # not in this table


def test_a_hand_edited_rule_that_does_not_clean_is_left_out():
    cfg = {"link_labels": {"patreon.com": "Patreon", "nope": "x", "ko-fi.com": "", "www.patreon.com": "dup"}}
    assert linklabels.rules(cfg) == {"patreon.com": "Patreon"}
    assert linklabels.rules({"link_labels": ["x"]}) == {}
    assert linklabels.rules({}) == {}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_rules_are_saved_normalised_and_shown_in_config(client):
    r = set_labels(client, {"https://www.patreon.com/x": "  Patreon  ", "ko-fi.com": "Ko-fi"})
    assert r["ok"] and r["config"]["link_labels"] == {"patreon.com": "Patreon", "ko-fi.com": "Ko-fi"}
    assert get(client, "/api/config")["link_labels"] == {"patreon.com": "Patreon", "ko-fi.com": "Ko-fi"}
    saved = json.loads(open(config.config_path()).read())
    assert saved["link_labels"] == {"patreon.com": "Patreon", "ko-fi.com": "Ko-fi"}


@pytest.mark.parametrize("rules", [
    {"patreon.com": "A", "www.patreon.com": "B"},
    {"nope": "A"},
    {"patreon.com": "x" * (linklabels.MAX_LABEL + 1)},
    {"patreon.com": 3},
    ["patreon.com"],
    "patreon.com",
])
def test_bad_rules_are_refused_and_nothing_is_saved(client, rules):
    set_labels(client, {"ko-fi.com": "Ko-fi"})
    r = set_labels(client, rules)
    assert r["ok"] is False and r["error"]
    assert get(client, "/api/config")["link_labels"] == {"ko-fi.com": "Ko-fi"}


def test_links_get_their_label_at_each_read_and_never_stored(client):
    a = add(client, "https://www.patreon.com/somebody")
    b = add(client, "https://ko-fi.com/somebody")
    c = add(client, "https://instagram.com/somebody")
    d = add(client, "https://patreon.com/other", title="Her page")
    assert a["label"] == "Patreon.com"                       # no rule, not synced: its own name
    assert labels(client) == {a["url"]: "Patreon.com", b["url"]: "Ko-fi.com", c["url"]: None, d["url"]: "Patreon.com"}

    set_labels(client, {"patreon.com": "Patreon"})
    assert labels(client)[a["url"]] == "Patreon"
    set_labels(client, {"patreon.com": "Patreon page"})      # an edit renames every link of the site
    got = labels(client)
    assert got[a["url"]] == got[d["url"]] == "Patreon page"
    set_labels(client, {})                                   # and a removal loses nothing
    assert labels(client)[a["url"]] == "Patreon.com"

    # The title as given stays in the table, the label never in it.
    rows = dict(db.connect().execute("SELECT url, title FROM links").fetchall())
    assert rows == {a["url"]: "", b["url"]: "", c["url"]: "", d["url"]: "Her page"}


def test_every_answer_with_a_link_has_its_label(client):
    set_labels(client, {"patreon.com": "Patreon"})
    pid = post(client, "/api/people", {"name": "Some Body"})["person"]["id"]
    link = add(client, "https://patreon.com/somebody", person=pid)
    assert link["label"] == "Patreon"
    assert post(client, f"/api/links/{link['id']}", {"notes": "hi"})["link"]["label"] == "Patreon"
    assert get(client, f"/api/people/{pid}")["links"][0]["label"] == "Patreon"
    assert post(client, f"/api/people/{pid}/links/order", {"ids": [link["id"]]})["links"][0]["label"] == "Patreon"
    assert post(client, "/api/links/assign", {"ids": [link["id"]], "person": None})["links"][0]["label"] == "Patreon"
    r = client.post("/api/links", json={"url": "https://patreon.com/somebody"}, headers=H)
    assert r.status_code == 409 and r.get_json()["link"]["label"] == "Patreon"


def test_a_search_finds_an_untitled_link_by_its_label(client):
    set_labels(client, {"example.org": "Fan shop"})
    a = add(client, "https://example.org/somebody")
    add(client, "https://example.org/titled", title="Something else")
    add(client, "https://other.example/x")
    pid = post(client, "/api/people", {"name": "Some Body"})["person"]["id"]
    b = add(client, "https://shop.example.org/y", person=pid)
    assert [x["url"] for x in get(client, "/api/links?q=fan%20SHOP")["links"]] == [b["url"], a["url"]]
    assert [x["url"] for x in get(client, f"/api/links?q=shop&person={pid}")["links"]] == [b["url"]]
    assert [x["url"] for x in get(client, "/api/links?q=shop&person=none")["links"]] == [a["url"]]
    # The usual matches stay, once each.
    assert [x["url"] for x in get(client, "/api/links?q=example.org")["links"]] == [
        b["url"], "https://example.org/titled", a["url"]]
