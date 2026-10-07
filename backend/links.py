"""Links: any web address worth keeping, tied to a person or to no one.

A creator's Linktree, Patreon, personal site or Discord invite, an
interview, an article: things that are not an account FeedVault downloads.
User data, like people: kept in ``links``, never touched by a rescan, and
mirrored to JSON by userdata.py, by URL with the person by name.

A link is only text. FeedVault never fetches it, for a title, a favicon or
anything else: no network here. Whether a link is a social profile ("social")
or anything else ("other") is read from its host on every read, never stored.
"""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

MAX_URL = 2048
MAX_TITLE = 300
MAX_NOTES = 5000                # as people.MAX_NOTES
MAX_QUERY = 200
MAX_IDS = 5000                  # links per reorder

# The hosts (as site() gives them) that make a link "social". The only
# source of truth for it: the API, the filters and the page's groups all
# read kind(), which reads this.
SOCIAL_HOSTS = frozenset({
    "instagram.com", "x.com", "twitter.com", "tiktok.com", "youtube.com", "youtu.be", "twitch.tv",
    "reddit.com", "bsky.app", "threads.net", "threads.com", "facebook.com", "fb.com", "snapchat.com",
    "pinterest.com", "pin.it", "tumblr.com", "kick.com", "onlyfans.com", "fansly.com", "patreon.com",
    "linktr.ee", "linktree.com", "discord.gg", "discord.com", "t.me", "linkedin.com", "ko-fi.com",
    "beacons.ai", "vk.com", "weibo.com",
})

# Prefixes that name the same site (www.patreon.com is patreon.com).
_PREFIXES = ("www.", "m.", "mobile.")
# Second-level labels under a country code that are not a site of their own
# (bbc.co.uk is "bbc.co.uk", not "co.uk").
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu", "ne", "or"}
# What a host may hold once split from the URL: a name, or an IPv6 address
# in brackets (urlsplit drops the brackets).
_HOST_RE = re.compile(r"^[^\s/\\?#@:<>\"'`{}|^%]+$")
_DEFAULT_PORT = {"http": 80, "https": 443}


def clean_url(value):
    """The URL as stored, or None when it is not a plain http(s) address.

    Whitespace around it is dropped; the scheme and host are lowercased,
    a default port dropped, and so is the slash of a bare host
    (``https://Example.com/`` is ``https://example.com``). Refused: any other
    scheme (javascript:, data:, file:), a user name or password in it, a
    backslash, whitespace or a control character inside, no host, more than
    MAX_URL characters."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL or "\\" in url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port                      # a port that is not a number raises
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORT or "@" in parts.netloc or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    if not host:
        return None
    if ":" in host:                            # IPv6
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        host = f"[{host}]"
    elif not _HOST_RE.match(host):
        return None
    netloc = host if port is None or port == _DEFAULT_PORT[scheme] else f"{host}:{port}"
    path = "" if parts.path == "/" and not parts.query and not parts.fragment else parts.path
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


def site(url):
    """The site a URL is on, its host without www., m. or mobile. and cut to
    the registrable part: "patreon.com" for https://www.patreon.com/x,
    "substack.com" for https://someone.substack.com, "bbc.co.uk" for
    https://www.bbc.co.uk/news. An IP address stays whole."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    for p in _PREFIXES:
        if host.startswith(p):
            host = host[len(p):]
            break
    labels = host.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def kind(url):
    """"social" for a link to a site in SOCIAL_HOSTS, else "other"."""
    return "social" if site(url) in SOCIAL_HOSTS else "other"


def clean_title(value):
    """A title with its whitespace collapsed, "" for none, or None when too long or not text."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    title = " ".join(value.split())
    return title if len(title) <= MAX_TITLE else None


def clean_notes(value):
    if value is None:
        return ""
    return value if isinstance(value, str) and len(value) <= MAX_NOTES else None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

_SELECT = ("SELECT l.id, l.url, l.title, l.notes, l.person_id, p.name AS person_name, l.position, l.created_at "
           "FROM links l LEFT JOIN people p ON p.id = l.person_id")


def _link(row):
    return {
        "id": row["id"], "url": row["url"], "title": row["title"], "notes": row["notes"],
        "site": site(row["url"]), "kind": kind(row["url"]),
        "person": {"id": row["person_id"], "name": row["person_name"]} if row["person_id"] is not None else None,
        "position": row["position"], "created_at": row["created_at"],
    }


def _order(links):
    """A person's links as their page shows them: socials first, then in the user's order."""
    return sorted(links, key=lambda x: (x["kind"] != "social", x["position"] is None, x["position"] or 0, x["id"]))


def listing(conn, person=None, no_person=False, kind_=None, site_=None, q=None):
    """{"links": the links that match, newest first (a person's in their
    order), "sites": [{"site", "count"}] over every link, for a filter}."""
    where, args = [], []
    if no_person:
        where.append("l.person_id IS NULL")
    elif person is not None:
        where.append("l.person_id = ?")
        args.append(person)
    if q:
        like = "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
        where.append("(l.url LIKE ? ESCAPE '\\' OR l.title LIKE ? ESCAPE '\\' OR l.notes LIKE ? ESCAPE '\\')")
        args += [like] * 3
    rows = conn.execute(f"{_SELECT}{' WHERE ' + ' AND '.join(where) if where else ''} "
                        "ORDER BY l.created_at DESC, l.id DESC", args).fetchall()
    out = [x for x in map(_link, rows)
           if (kind_ is None or x["kind"] == kind_) and (site_ is None or x["site"] == site_)]
    if person is not None and not no_person:
        out = _order(out)
    counts = {}
    for (url,) in conn.execute("SELECT url FROM links"):
        s = site(url)
        counts[s] = counts.get(s, 0) + 1
    sites = [{"site": s, "count": n} for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"links": out, "sites": sites}


def of_person(conn, pid):
    """A person's links, socials first, then in their order."""
    return _order([_link(r) for r in conn.execute(f"{_SELECT} WHERE l.person_id = ?", (pid,))])


def get(conn, lid):
    if not 0 <= lid < 2**53:
        return None
    row = conn.execute(f"{_SELECT} WHERE l.id = ?", (lid,)).fetchone()
    return _link(row) if row else None


def find(conn, url):
    """The id of the link saved with this URL, or None."""
    row = conn.execute("SELECT id FROM links WHERE url = ?", (url,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Changes (each returns what the API answers; the caller notes userdata)
# ---------------------------------------------------------------------------

def _next_position(conn, pid):
    return conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM links WHERE person_id = ?", (pid,)).fetchone()[0]


def create(conn, url, title, notes, pid, now):
    """A new link (``url`` cleaned and not saved yet), last in its person's order."""
    with conn:
        lid = conn.execute(
            "INSERT INTO links(url, title, notes, person_id, position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (url, title, notes, pid, None if pid is None else _next_position(conn, pid), now)).lastrowid
    return get(conn, lid)


_UNSET = object()


def update(conn, lid, url=None, title=None, notes=None, pid=_UNSET):
    """Change what is given. A link given to another person goes last in
    their order; one given to no one loses its place."""
    with conn:
        for column, value in (("url", url), ("title", title), ("notes", notes)):
            if value is not None:
                conn.execute(f"UPDATE links SET {column} = ? WHERE id = ?", (value, lid))
        if pid is not _UNSET:
            row = conn.execute("SELECT person_id FROM links WHERE id = ?", (lid,)).fetchone()
            if row[0] != pid:
                conn.execute("UPDATE links SET person_id = ?, position = ? WHERE id = ?",
                             (pid, None if pid is None else _next_position(conn, pid), lid))
    return get(conn, lid)


def delete(conn, lid):
    with conn:
        return conn.execute("DELETE FROM links WHERE id = ?", (lid,)).rowcount > 0


def reorder(conn, pid, ids):
    """Put the given links of a person in this order, in the places they held
    between them; the others do not move (as organize.reorder does for a
    collection's posts). Ids not of this person are ignored."""
    held = dict(conn.execute("SELECT id, position FROM links WHERE person_id = ?", (pid,)))
    ids = [i for i in dict.fromkeys(ids) if i in held]
    places = sorted(held[i] or 0 for i in ids)
    if len(set(places)) < len(places):         # equal places (an old restore): number them all first
        with conn:
            for n, i in enumerate(sorted(held, key=lambda i: (held[i] or 0, i)), 1):
                conn.execute("UPDATE links SET position = ? WHERE id = ?", (n, i))
        return reorder(conn, pid, ids)
    with conn:
        conn.executemany("UPDATE links SET position = ? WHERE id = ?", list(zip(places, ids)))


def merge_into(conn, keep, others):
    """In a people merge (inside its transaction): the others' links move to
    ``keep``, after its own, each person's in their order."""
    n = _next_position(conn, keep) - 1
    for other in others:
        for (lid,) in conn.execute("SELECT id FROM links WHERE person_id = ? ORDER BY position IS NULL, position, id",
                                   (other,)).fetchall():
            n += 1
            conn.execute("UPDATE links SET person_id = ?, position = ? WHERE id = ?", (keep, n, lid))
