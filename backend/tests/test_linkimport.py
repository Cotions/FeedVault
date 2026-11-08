"""Many links at once (linkimport.py, POST /api/links/import-many): pasted
text and exported bookmarks, saved to no one, never fetched."""
import json
import socket
import urllib.request

import pytest

from conftest import H

import db
import linkimport
import links

NOW = 1_800_000_000

FIREFOX = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file.
     It will be read and overwritten.
     DO NOT EDIT! -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; script-src 'none'; img-src data: *; object-src 'none'"></meta>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks Menu</H1>

<DL><p>
    <DT><A HREF="place:parent=menu________&amp;queryType=1" ADD_DATE="1700000000">Recent</A>
    <DT><H3 ADD_DATE="1600000000" LAST_MODIFIED="1600000001">Art</H3>
    <DL><p>
        <DT><H3 ADD_DATE="1600000000" LAST_MODIFIED="1600000001">Artists &amp; friends</H3>
        <DL><p>
            <DT><A HREF="https://www.patreon.com/someone?utm_source=x&amp;a=1" ADD_DATE="1650000000" LAST_MODIFIED="1650000001" ICON_URI="https://p.example/i.ico" TAGS="art">Some&nbsp;One &lt;3 &#233;</A>
            <DD>A description
        </DL><p>
        <DT><A HREF="https://gallery.example/" ADD_DATE="1660000000">Gallery</A>
    </DL><p>
    <HR>
    <DT><A HREF="javascript:alert(1)" ADD_DATE="1">Bookmarklet</A>
    <DT><H3 ADD_DATE="1600000000" LAST_MODIFIED="1600000001" PERSONAL_TOOLBAR_FOLDER="true">Bookmarks Toolbar</H3>
    <DL><p>
        <DT><A HREF="https://news.example/a" ADD_DATE="9999999999">From the future</A>
        <DT><A HREF="about:config">About</A>
    </DL><p>
    <DT><A HREF="https://loose.example/top" ADD_DATE="garbage">Top level</A>
</DL>
"""

CHROME = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file.
     It will be read and overwritten.
     DO NOT EDIT! -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1600000000" LAST_MODIFIED="0" PERSONAL_TOOLBAR_FOLDER="true">Bookmarks bar</H3>
    <DL><p>
        <DT><A HREF="https://shop.example/item?id=7&amp;fbclid=abc" ADD_DATE="1610000000" ICON="data:image/png;base64,iVBORw0KGgo=">Shop</A>
        <DT><H3 ADD_DATE="1600000000" LAST_MODIFIED="0">Art</H3>
        <DL><p>
            <DT><H3 ADD_DATE="1600000000" LAST_MODIFIED="0">Artists</H3>
            <DL><p>
                <DT><A HREF="https://artist.example/" ADD_DATE="1620000000">Artist</A>
                <DT><A HREF="http://www.artist.example" ADD_DATE="1630000000">Same, another spelling</A>
            </DL><p>
        </DL><p>
        <DT><A HREF="chrome://settings" ADD_DATE="1620000000">Settings</A>
        <DT><A HREF="file:///home/me/x.html" ADD_DATE="1620000000">A file</A>
    </DL><p>
    <DT><H3 ADD_DATE="1600000000" LAST_MODIFIED="0">Other bookmarks</H3>
    <DL><p>
        <DT><A HREF="https://other.example/x" ADD_DATE="1640000000">Other</A>
    </DL><p>
</DL><p>
"""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any attempt to reach the network fails the test: an import never fetches."""
    def refuse(*a, **k):
        raise AssertionError("an import must never fetch anything")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def clock(monkeypatch):
    import app
    monkeypatch.setattr(app, "_link_now", lambda: NOW)


def found(text):
    return linkimport.read(text, NOW)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def test_firefox_html_with_nested_folders_dates_and_entities():
    r = found(FIREFOX)
    assert r["found"] == [
        ("https://www.patreon.com/someone?a=1", "Some One <3 é", 1650000000, "Folder: Art / Artists & friends"),
        ("https://gallery.example", "Gallery", 1660000000, "Folder: Art"),
        ("https://news.example/a", "From the future", NOW, "Folder: Bookmarks Toolbar"),   # clamped to now
        ("https://loose.example/top", "Top level", NOW, ""),                                # garbage date: now
    ]
    assert r["skipped"] == 3 and r["repeated"] == 0                # place:, javascript:, about:


def test_chrome_html():
    r = found(CHROME)
    assert r["found"] == [
        ("https://shop.example/item?id=7", "Shop", 1610000000, "Folder: Bookmarks bar"),
        ("https://artist.example", "Artist", 1620000000, "Folder: Bookmarks bar / Art / Artists"),
        ("https://other.example/x", "Other", 1640000000, "Folder: Other bookmarks"),
    ]
    assert r["skipped"] == 2 and r["repeated"] == 1                # chrome://, file:; www. spelling


def test_pasted_text():
    text = ("https://a.example/1 https://b.example/2\n"
            "\n"
            "  Cool site - https://c.example/page.\n"
            "[Docs](https://d.example/wiki/Thing_(x))\n"
            "(https://e.example/x)\n"
            "www.f.example/path,\n"
            "https://a.example/1/\n"
            "http://user:pw@g.example/\n")
    r = found(text)
    assert r["found"] == [
        ("https://a.example/1", "", NOW, ""), ("https://b.example/2", "", NOW, ""),
        ("https://c.example/page", "Cool site", NOW, ""),
        ("https://d.example/wiki/Thing_(x)", "Docs", NOW, ""), ("https://e.example/x", "", NOW, ""),
        ("https://www.f.example/path", "", NOW, ""),
    ]
    assert r["repeated"] == 1 and r["skipped"] == 1                 # the last slash; a user name


def test_firefox_json_backup():
    tree = {"guid": "root________", "title": "", "root": "placesRoot", "children": [
        {"title": "menu", "root": "bookmarksMenuFolder", "children": [
            {"title": "Art", "type": "text/x-moz-place-container", "children": [
                {"title": "Painter", "uri": "https://painter.example/", "dateAdded": 1650000000123456},
                {"type": "text/x-moz-place-separator"},
                {"title": "Recent", "uri": "place:sort=8"},
            ]},
        ]},
        {"title": "toolbar", "root": "toolbarFolder", "children": [
            {"title": "News", "uri": "https://news.example/", "dateAdded": "x"},
        ]},
    ]}
    r = found(json.dumps(tree))
    assert r["found"] == [
        ("https://painter.example", "Painter", 1650000000, "Folder: Bookmarks Menu / Art"),
        ("https://news.example", "News", NOW, "Folder: Bookmarks Toolbar"),
    ]
    assert r["skipped"] == 1


def test_long_titles_are_cut_and_text_past_the_limit_refused(monkeypatch):
    r = found(f'<DT><A HREF="https://a.example">{"x" * 500}</A>')
    assert r["found"][0][1] == "x" * links.MAX_TITLE
    monkeypatch.setattr(linkimport, "MAX_TEXT", 100)
    with pytest.raises(linkimport.TooLarge):
        found("x" * 101)
    assert found("not a link at all") == {"found": [], "skipped": 0, "repeated": 0}


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------

def post(client, body, status=200):
    r = client.post("/api/links/import-many", json=body, headers=H)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def test_import_saves_to_unsorted_keeping_dates_and_folders(client, clock):
    client.post("/api/links", json={"url": "https://other.example/x", "title": "Mine", "notes": "kept"}, headers=H)
    pid = client.post("/api/people", json={"name": "Ann"}, headers=H).get_json()["person"]["id"]
    client.post("/api/links", json={"url": "https://shop.example/item?id=7", "person": pid}, headers=H)
    assert post(client, {"text": CHROME}) == {"ok": True, "saved": 1, "already": 3, "skipped": 2, "over": 0}
    got = {x["url"]: x for x in client.get("/api/links?person=none", headers=H).get_json()["links"]}
    assert set(got) == {"https://other.example/x", "https://artist.example"}
    assert got["https://artist.example"]["created_at"] == 1620000000
    assert got["https://artist.example"]["notes"] == "Folder: Bookmarks bar / Art / Artists"
    assert got["https://artist.example"]["title"] == "Artist"
    assert (got["https://other.example/x"]["title"], got["https://other.example/x"]["notes"]) == ("Mine", "kept")
    # Ann's link stays hers alone.
    shop = links.get(db.connect(), links.find(db.connect(), "https://shop.example/item?id=7"))
    assert [p["name"] for p in shop["people"]] == ["Ann"]
    # Again: all there already.
    assert post(client, {"text": CHROME}) == {"ok": True, "saved": 0, "already": 4, "skipped": 2, "over": 0}


def test_pasted_text_is_saved_now(client, clock):
    assert post(client, {"text": "One - https://one.example\nhttps://two.example"}) == \
        {"ok": True, "saved": 2, "already": 0, "skipped": 0, "over": 0}
    got = client.get("/api/links?person=none", headers=H).get_json()["links"]
    assert [(x["url"], x["title"], x["created_at"]) for x in got] == [
        ("https://two.example", "", NOW), ("https://one.example", "One", NOW)]


def test_past_the_cap_is_over(client, clock, monkeypatch):
    monkeypatch.setattr(linkimport, "MAX_LINKS", 2)
    text = "\n".join(f"https://n.example/{i}" for i in range(5))
    assert post(client, {"text": text}) == {"ok": True, "saved": 2, "already": 0, "skipped": 0, "over": 3}
    assert len(client.get("/api/links", headers=H).get_json()["links"]) == 2


def test_the_link_file_is_written_after_an_import(client, clock, monkeypatch):
    import userdata
    noted = []
    monkeypatch.setattr(userdata, "changed", noted.append)
    post(client, {"text": "https://a.example"})
    assert noted == ["links"]
    post(client, {"text": "https://a.example"})                   # nothing new: nothing to write
    assert noted == ["links"]


@pytest.mark.parametrize("body", [{}, {"text": 5}, {"text": None}, {"text": "x", "person": 1}, {"file": "x"}])
def test_a_bad_body_is_a_400(client, body):
    assert post(client, body, 400)["ok"] is False


def test_too_much_text_is_a_413(client, monkeypatch):
    monkeypatch.setattr(linkimport, "MAX_TEXT", 1000)
    assert post(client, {"text": "https://a.example " * 100}, 413)["ok"] is False      # over the text limit
    assert post(client, {"text": "\"" * 1600}, 413)["ok"] is False                     # over the body limit
    assert client.get("/api/links", headers=H).get_json()["links"] == []


def test_another_site_is_refused(client):
    r = client.post("/api/links/import-many", json={"text": "https://a.example"},
                    headers={**H, "Origin": "https://www.instagram.com"})
    assert r.status_code == 403
    assert client.get("/api/links", headers=H).get_json()["links"] == []
