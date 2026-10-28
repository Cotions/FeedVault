"""Links: any web address worth keeping, tied to people or to no one.

A creator's Linktree, Patreon, personal site or Discord invite, an
interview, an article: things that are not an account FeedVault downloads.
A link may be several people's (a band's account is the band's and each
member's): ``link_people`` ties it to each, with its place among their
links. User data, like people: kept in ``links`` and ``link_people``, never
touched by a rescan, and mirrored to JSON by userdata.py, by URL with its
people by name.

A link is only text. FeedVault never fetches it, for a title, a favicon or
anything else: no network here. Whether a link is a social profile ("social")
or anything else ("other") is read from its host on every read, never stored.
"""
import ipaddress
import re
from contextlib import contextmanager
from urllib.parse import unquote_plus, urlsplit, urlunsplit

MAX_URL = 2048
MAX_TITLE = 300
MAX_NOTES = 5000                # as people.MAX_NOTES
MAX_QUERY = 200
MAX_IDS = 5000                  # links per reorder
MAX_PEOPLE = 100                # people one body gives a link
MAX_RECENT = 50                 # people per recent_people

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

# Query parameters that only say where a link was shared from or clicked,
# dropped from every URL as it is cleaned (clean_url): any name starting
# with utm_, these anywhere, and on a site (as site() gives it) the ones it
# adds when a link is copied, which mean something real elsewhere (a "t"
# is a time on YouTube, kept there). Names are matched decoded, any case.
TRACKING = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igsh", "igshid", "_hsenc", "_hsmi",
    "ref_src", "ref_url",
})
TRACKING_ON = {
    "tiktok.com": frozenset({"_r", "_t", "is_from_webapp", "sender_device", "is_copy_url", "lang"}),
    "instagram.com": frozenset({"hl", "img_index"}),
    "x.com": frozenset({"s", "t"}),
    "twitter.com": frozenset({"s", "t"}),
    "youtube.com": frozenset({"si", "feature", "pp"}),
    "youtu.be": frozenset({"si", "feature", "pp"}),
    "spotify.com": frozenset({"si", "nd"}),
    "facebook.com": frozenset({"mibextid", "rdid", "share_url"}),
    "fb.com": frozenset({"mibextid", "rdid", "share_url"}),
}

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
    MAX_URL characters. Tracking parameters are dropped from the query
    (strip_tracking), and a query left empty with them."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL or "\\" in url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    try:
        url.encode("utf-8")                    # half of a surrogate pair (a JSON "\ud800"): the table cannot hold it
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
    query = strip_tracking(parts.query, _site_of(host))
    path = "" if parts.path == "/" and not query and not parts.fragment else parts.path
    return urlunsplit((scheme, netloc, path, query, parts.fragment))


def strip_tracking(query, site_):
    """``query`` (a URL's, without its ?) without its tracking parameters
    (TRACKING, utm_*, and TRACKING_ON for ``site_``); the others are kept as
    they were written, in their order. Unchanged when it has none."""
    if not query:
        return query
    on = TRACKING_ON.get(site_, frozenset())
    kept = []
    for pair in query.split("&"):
        name = unquote_plus(pair.split("=", 1)[0]).lower()
        if not (name.startswith("utm_") or name in TRACKING or name in on):
            kept.append(pair)
    if len(kept) == query.count("&") + 1:
        return query
    return "&".join(p for p in kept if p)


def site(url):
    """The site a URL is on, its host cut to the registrable part (which
    drops www., m. and the like): "patreon.com" for https://www.patreon.com/x,
    "substack.com" for https://someone.substack.com, "bbc.co.uk" for
    https://www.bbc.co.uk/news. An IP address stays whole."""
    return _site_of((urlsplit(url).hostname or "").lower().rstrip("."))


def _site_of(host):
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def kind(url):
    """"social" for a link to a site in SOCIAL_HOSTS, else "other"."""
    return _kind_of(site(url))


def _kind_of(site_):
    return "social" if site_ in SOCIAL_HOSTS else "other"


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

_SELECT = "SELECT l.id, l.url, l.title, l.notes, l.created_at, {position} AS position FROM links l"
# A person's links: each with its place among theirs.
_THEIRS = _SELECT.format(position="lp.position") + " JOIN link_people lp ON lp.link_id = l.id AND lp.person_id = ?"
_ANY = _SELECT.format(position="NULL")
_NO_ONE = "NOT EXISTS (SELECT 1 FROM link_people x WHERE x.link_id = l.id)"


def _people(conn, ids):
    """{link id: [{"id", "name"}]}: the people of these links, by name."""
    sql = ("SELECT lp.link_id, p.id, p.name FROM link_people lp JOIN people p ON p.id = lp.person_id{} "
           "ORDER BY p.name COLLATE NOCASE, p.id")
    if not ids:
        return {}
    if len(ids) > 500:                             # many: one read of them all
        rows = conn.execute(sql.format("")).fetchall()
    else:
        rows = conn.execute(sql.format(f" WHERE lp.link_id IN ({','.join('?' * len(ids))})"), list(ids)).fetchall()
    out = {}
    for lid, pid, name in rows:
        out.setdefault(lid, []).append({"id": pid, "name": name})
    return out


def _link(row, people):
    s = site(row["url"])
    return {
        "id": row["id"], "url": row["url"], "title": row["title"], "notes": row["notes"],
        "site": s, "kind": _kind_of(s), "people": people.get(row["id"], []),
        "position": row["position"], "created_at": row["created_at"],
    }


def _links(conn, rows):
    people = _people(conn, [r["id"] for r in rows])
    return [_link(r, people) for r in rows]


def _order(links):
    """A person's links as their page shows them: socials first, then in the user's order."""
    return sorted(links, key=lambda x: (x["kind"] != "social", x["position"] is None, x["position"] or 0, x["id"]))


def listing(conn, person=None, no_person=False, kind_=None, site_=None, q=None, q_sites=()):
    """{"links": the links that match, newest first (a person's, any link
    they are one of the people of, in their order, ``position`` their place
    among them; else ``position`` None), "sites": [{"site", "count"}] over
    every link, for a filter}.
    ``q_sites``: the sites whose label holds ``q`` (linklabels.matching): a
    link with no title on one of them matches too, by the label it shows."""
    where, args, select = [], [], _ANY
    if no_person:
        where.append(_NO_ONE)
    elif person is not None:
        select, args = _THEIRS, [person]
    untitled = [*where, "l.title = ''"], list(args)
    if q:
        like = "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
        where.append("(l.url LIKE ? ESCAPE '\\' OR l.title LIKE ? ESCAPE '\\' OR l.notes LIKE ? ESCAPE '\\')")
        args += [like] * 3
    order = " ORDER BY l.created_at DESC, l.id DESC"
    rows = conn.execute(f"{select}{' WHERE ' + ' AND '.join(where) if where else ''}{order}", args).fetchall()
    if q and q_sites:
        # A site is read from the URL, not stored: those links are picked here.
        found = {r["id"] for r in rows}
        extra = [r for r in conn.execute(f"{select} WHERE {' AND '.join(untitled[0])}{order}", untitled[1])
                 if r["id"] not in found and site(r["url"]) in q_sites]
        if extra:
            rows = sorted(rows + extra, key=lambda r: (-r["created_at"], -r["id"]))
    if kind_ is not None or site_ is not None:
        rows = [r for r in rows if (kind_ is None or kind(r["url"]) == kind_) and (site_ is None or site(r["url"]) == site_)]
    out = _links(conn, rows)
    if person is not None and not no_person:
        out = _order(out)
    # The sites of every link: the rows just read when nothing narrowed them.
    narrowed = where or select is not _ANY or kind_ is not None or site_ is not None
    every = (site(url) for (url,) in conn.execute("SELECT url FROM links")) if narrowed else (x["site"] for x in out)
    counts = {}
    for s in every:
        counts[s] = counts.get(s, 0) + 1
    sites = [{"site": s, "count": n} for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"links": out, "sites": sites}


def of_person(conn, pid):
    """A person's links, socials first, then in their order."""
    return _order(_links(conn, conn.execute(_THEIRS, (pid,)).fetchall()))


def get(conn, lid):
    if not 0 <= lid < 2**53:
        return None
    row = conn.execute(f"{_ANY} WHERE l.id = ?", (lid,)).fetchone()
    return _link(row, _people(conn, [lid])) if row else None


def find(conn, url):
    """The id of the link saved with this URL, or None."""
    row = conn.execute("SELECT id FROM links WHERE url = ?", (url,)).fetchone()
    return row[0] if row else None


def names(conn):
    """[{"id", "name"}] of every person, by name: all a picker outside the
    dashboard (the userscript's) is given of them."""
    return [{"id": i, "name": n} for i, n in
            conn.execute("SELECT id, name FROM people ORDER BY name COLLATE NOCASE, id")]


def recent_people(conn, limit):
    """[{"id", "name", "at"}]: the people most recently given a link, the
    latest first. ``at`` is when the last of their links was given to them
    (``tied_at``: created with them, or given to them later), not when it
    was saved: a link sorted out of the untied ones counts as recent."""
    rows = conn.execute(
        "SELECT p.id, p.name, MAX(lp.tied_at) AS at "
        "FROM link_people lp JOIN people p ON p.id = lp.person_id "
        "GROUP BY p.id ORDER BY at DESC, p.id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "at": r["at"]} for r in rows]


def people_of(conn, lid):
    """The ids of a link's people."""
    return [pid for (pid,) in conn.execute("SELECT person_id FROM link_people WHERE link_id = ? ORDER BY person_id",
                                           (lid,))]


# ---------------------------------------------------------------------------
# Changes (each returns what the API answers; the caller notes userdata)
# ---------------------------------------------------------------------------

class NoSuchPerson(LookupError):
    """A person given is gone: deleted between the request's check and the
    write (the routes answer 400, not a 409 or a 500)."""


@contextmanager
def _write(conn):
    """One transaction, the write lock taken first (BEGIN IMMEDIATE): what
    is read inside is what is written over."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:                # some errors already rolled back
            conn.execute("ROLLBACK")
        raise


def _people_exist(conn, pids):
    """Inside the write: NoSuchPerson unless each of ``pids`` is a person."""
    for pid in pids:
        if conn.execute("SELECT 1 FROM people WHERE id = ?", (pid,)).fetchone() is None:
            raise NoSuchPerson(pid)


def _next_position(conn, pid):
    return conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM link_people WHERE person_id = ?",
                        (pid,)).fetchone()[0]


def _tie(conn, lid, pid, now):
    """Inside a transaction: the link ``pid``'s too, last in their order,
    tied to them at ``now``, unless it is theirs already. Whether it was
    not."""
    if conn.execute("SELECT 1 FROM link_people WHERE link_id = ? AND person_id = ?", (lid, pid)).fetchone():
        return False
    conn.execute("INSERT INTO link_people(link_id, person_id, position, tied_at) VALUES (?, ?, ?, ?)",
                 (lid, pid, _next_position(conn, pid), now))
    return True


def _untie(conn, lid, pid):
    """Inside a transaction: the link no longer ``pid``'s (it loses its place
    among theirs); its other people keep it. Whether it was theirs."""
    return conn.execute("DELETE FROM link_people WHERE link_id = ? AND person_id = ?", (lid, pid)).rowcount > 0


def create(conn, url, title, notes, pids, now):
    """A new link (``url`` cleaned and not saved yet), last in the order of
    each of ``pids`` (people's ids; none: a link of no one). NoSuchPerson
    when one is gone, and nothing is saved."""
    with _write(conn):
        _people_exist(conn, pids)
        lid = conn.execute("INSERT INTO links(url, title, notes, created_at) VALUES (?, ?, ?, ?)",
                           (url, title, notes, now)).lastrowid
        for pid in dict.fromkeys(pids):
            _tie(conn, lid, pid, now)
    return get(conn, lid)


def spelling(url):
    """What two spellings of one address share: http or https, with or
    without www., with or without a last slash."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return (host[4:] if host.startswith("www.") else host, parts.port, parts.path.rstrip("/"), parts.query,
            parts.fragment)


def keep(conn, pid, found, now, limit):
    """Save the links a person does not have yet, from ``found`` ([(url,
    title)], each cleaned, no URL twice): last in their order, in the order
    given, in one transaction with the write lock taken first (as assign).
    A URL saved already for someone else, or for no one, becomes this
    person's too (last in their order, tied to them now) and stays the
    others': it is never moved, retitled or removed (the link-in-bio
    import, biofetch.py, run again on a page that changed; a band's
    account on each member's page). A URL is saved already in any of its
    spellings (spelling: http or https, www., a last slash): another
    spelling of a link they have is theirs already, of someone else's link
    is that link, theirs too, never a second link. At most ``limit`` are
    saved. ``pid`` None saves them to no one (the Links page's Unsorted),
    with no place, as create does; a URL saved already is then left as it
    is.

    {"saved", "already" (this person's; with no person, any link saved
    already), "shared" (saved already, not theirs: now theirs too), "over"
    (new, but past the limit)}, or None when the person is gone."""
    out = {"saved": 0, "already": 0, "shared": 0, "over": 0}
    conn.execute("BEGIN IMMEDIATE")
    try:
        if pid is not None and conn.execute("SELECT 1 FROM people WHERE id = ?", (pid,)).fetchone() is None:
            conn.execute("ROLLBACK")
            return None
        mine = f"SELECT l.url FROM links l WHERE {_NO_ONE}" if pid is None else \
            "SELECT l.url FROM links l JOIN link_people lp ON lp.link_id = l.id WHERE lp.person_id = ?"
        have = {spelling(u) for (u,) in conn.execute(mine, () if pid is None else (pid,))}
        # Every saved link by spelling, the oldest of two spellings first.
        saved = {}
        for lid, u in conn.execute("SELECT id, url FROM links ORDER BY id DESC"):
            saved[spelling(u)] = lid
        for url, title in found:
            s = spelling(url)
            lid = find(conn, url) or saved.get(s)
            if s in have or (lid is not None and pid is None):
                out["already"] += 1
            elif lid is not None:
                _tie(conn, lid, pid, now)
                out["shared"] += 1
                have.add(s)
            elif out["saved"] >= limit:
                out["over"] += 1
            else:
                lid = conn.execute("INSERT INTO links(url, title, notes, created_at) VALUES (?, ?, '', ?)",
                                   (url, title, now)).lastrowid
                if pid is not None:
                    _tie(conn, lid, pid, now)
                out["saved"] += 1
                saved[s] = lid
                have.add(s)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return out


UNSET = _UNSET = object()                # an argument not given (None means no one)


def _give(conn, lid, pids, now):
    """Inside a transaction: the link's people become exactly ``pids``
    (people's ids; none: no one), unless they are already (or it is gone).
    Those it keeps keep their place and tied_at; each new one gets it last
    in their order, tied to them at ``now``, in the order of ``pids``; one
    it loses loses its place. Whether it changed."""
    if conn.execute("SELECT 1 FROM links WHERE id = ?", (lid,)).fetchone() is None:
        return False
    pids = list(dict.fromkeys(pids))
    had = set(people_of(conn, lid))
    if had == set(pids):
        return False
    for pid in had - set(pids):
        _untie(conn, lid, pid)
    for pid in pids:
        if pid not in had:
            _tie(conn, lid, pid, now)
    return True


def update(conn, lid, url=None, title=None, notes=None, pids=_UNSET, now=None):
    """Change what is given; new people (a list of ids) as _give.
    NoSuchPerson when one is gone, and nothing is changed."""
    with _write(conn):
        if pids is not _UNSET:
            _people_exist(conn, pids)
        for column, value in (("url", url), ("title", title), ("notes", notes)):
            if value is not None:
                conn.execute(f"UPDATE links SET {column} = ? WHERE id = ?", (value, lid))
        if pids is not _UNSET:
            _give(conn, lid, pids, now)
    return get(conn, lid)


def missing(conn, ids):
    """The ids no link has, in the order given."""
    have = set()
    for n in range(0, len(ids), 500):              # well under SQLite's limit of variables
        part = ids[n:n + 500]
        have.update(i for (i,) in conn.execute(f"SELECT id FROM links WHERE id IN ({','.join('?' * len(part))})", part))
    return [i for i in ids if i not in have]


def _batch(conn, ids, each, all_or_none=True, people=()):
    """``each(lid)`` for these links (each id once, in the order given) in
    one transaction. The write lock is taken first (BEGIN IMMEDIATE), so
    what is read (which links exist, the next places) is what is written
    over: no other write comes in between. With ``all_or_none``, an id no
    link has changes nothing; else ids no link has are passed over.
    ``people`` given to them: NoSuchPerson when one is gone, nothing changed.

    (the ids ``each`` said yes to, or None when nothing changed for an id
    no link has; the ids no link has)."""
    ids = list(dict.fromkeys(ids))
    conn.execute("BEGIN IMMEDIATE")
    try:
        _people_exist(conn, people)
        gone = missing(conn, ids)
        if gone and all_or_none:
            conn.execute("ROLLBACK")
            return None, gone
        absent = set(gone)
        done = [lid for lid in ids if lid not in absent and each(lid)]
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return done, gone


def assign(conn, ids, pids, now, only_from=_UNSET):
    """Give these links to ``pids`` (people's ids, often one; none: no one)
    in one transaction (_batch), each as _give: their people become those
    (replacing any other), a new one last in their order, in the order of
    ``ids``.

    With ``only_from`` (a person id, or None), only the links that are that
    person's alone now (None: no one's) move; the others, and ids no link
    has, are passed over (an Undo: a link changed since is left as it is).
    Without it, it is all or none: an id no link has changes nothing.

    (moved, missing, skipped): how many moved, the ids no link has, and the
    ids passed over (with ``only_from``)."""
    want = [] if only_from is _UNSET or only_from is None else [only_from]
    passed = set()

    def each(lid):
        if only_from is not _UNSET and people_of(conn, lid) != want:
            passed.add(lid)
            return False
        return _give(conn, lid, pids, now)
    done, gone = _batch(conn, ids, each, all_or_none=only_from is _UNSET, people=pids)
    if done is None:
        return 0, gone, []
    skipped = [i for i in dict.fromkeys(ids) if i in passed or i in gone] if only_from is not _UNSET else []
    return len(done), gone, skipped


def add_person(conn, ids, pid, now):
    """Also give these links to ``pid``, each last in their order, in the
    order of ``ids``; their other people keep them. All or none, as
    assign (_batch). (the ids that were not theirs yet, or None; missing)."""
    return _batch(conn, ids, lambda lid: _tie(conn, lid, pid, now), people=[pid])


def remove_person(conn, ids, pid):
    """Take these links from ``pid`` (_batch); their other people keep them,
    and a link left with no one goes to Unsorted. Ids no link has are
    passed over (an Undo). (the ids that were theirs, missing)."""
    return _batch(conn, ids, lambda lid: _untie(conn, lid, pid), all_or_none=False)


def delete(conn, lid):
    with conn:
        conn.execute("DELETE FROM link_people WHERE link_id = ?", (lid,))
        return conn.execute("DELETE FROM links WHERE id = ?", (lid,)).rowcount > 0


def delete_many(conn, ids):
    """Delete these links in one transaction, passing over ids no link has.
    How many went."""
    with conn:
        n = 0
        for lid in dict.fromkeys(ids):
            conn.execute("DELETE FROM link_people WHERE link_id = ?", (lid,))
            n += conn.execute("DELETE FROM links WHERE id = ?", (lid,)).rowcount
        return n


def unsorted(conn):
    """How many links are tied to no one: the Links page's Unsorted."""
    return conn.execute(f"SELECT COUNT(*) FROM links l WHERE {_NO_ONE}").fetchone()[0]


def reorder(conn, pid, ids):
    """Put the given links of a person in this order, in the places they held
    between them; the others do not move (as organize.reorder does for a
    collection's posts), nor does a link among its other people's. Ids not
    of this person are ignored."""
    held = dict(conn.execute("SELECT link_id, position FROM link_people WHERE person_id = ?", (pid,)))
    ids = [i for i in dict.fromkeys(ids) if i in held]
    put = "UPDATE link_people SET position = ? WHERE link_id = ? AND person_id = ?"
    if None in held.values() or len(set(held.values())) < len(held):
        # No place or equal places (a hand-edited restore): number them all
        # first, in the order the page shows them (no place last).
        with conn:
            for n, i in enumerate(sorted(held, key=lambda i: (held[i] is None, held[i] or 0, i)), 1):
                conn.execute(put, (n, i, pid))
        return reorder(conn, pid, ids)
    places = sorted(held[i] for i in ids)
    with conn:
        conn.executemany(put, [(n, i, pid) for n, i in zip(places, ids)])


def merge_into(conn, keep, others):
    """In a people merge (inside its transaction): the others' links become
    ``keep``'s, after its own, each person's in their order. A link that is
    ``keep``'s already stays where it is among theirs."""
    n = _next_position(conn, keep) - 1
    for other in others:
        for (lid,) in conn.execute("SELECT link_id FROM link_people WHERE person_id = ? "
                                   "ORDER BY position IS NULL, position, link_id", (other,)).fetchall():
            if conn.execute("SELECT 1 FROM link_people WHERE link_id = ? AND person_id = ?", (lid, keep)).fetchone():
                conn.execute("DELETE FROM link_people WHERE link_id = ? AND person_id = ?", (lid, other))
                continue
            n += 1
            conn.execute("UPDATE link_people SET person_id = ?, position = ? WHERE link_id = ? AND person_id = ?",
                         (keep, n, lid, other))


# ---------------------------------------------------------------------------
# User data (userdata.py: links.json, by URL, each person by name)
# ---------------------------------------------------------------------------

def _place(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**53 else None


def _when(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**53 else None


def restore_row(row):
    """A links.json row as the API would have saved it (URL, title and notes
    cleaned, each place a whole number), or None to skip it: the file may
    have been edited by hand, and a link is rendered as a link. ``people``
    is [{"name", "position", "tied_at"}], each name once, a row that is not
    one left out. A file from before a link had several people has
    ``person`` (a name, or None for no one), ``position`` and ``tied_at``
    instead: read as one person."""
    url = clean_url(row.get("url"))
    title, notes = clean_title(row.get("title")), clean_notes(row.get("notes"))
    if url is None or title is None or notes is None:
        return None
    given = row.get("people") if "people" in row else \
        [{"name": row.get("person"), "position": row.get("position"), "tied_at": row.get("tied_at")}]
    people, seen = [], set()
    for p in given if isinstance(given, list) else ():
        name = p.get("name") if isinstance(p, dict) else None
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        # No tied_at (a file from before it was kept): from created_at, as restore puts it back.
        people.append({"name": name, "position": _place(p.get("position")), "tied_at": _when(p.get("tied_at"))})
    return {**row, "url": url, "title": title, "notes": notes, "people": people}


def export_rows(conn):
    """links.json's rows, by URL: (url, title, notes, people, created_at,
    id), ``people`` as restore_row reads them, by name."""
    ties = {}
    for lid, name, position, tied_at in conn.execute(
            "SELECT lp.link_id, p.name, lp.position, lp.tied_at FROM link_people lp "
            "JOIN people p ON p.id = lp.person_id ORDER BY p.name, p.id"):
        ties.setdefault(lid, []).append({"name": name, "position": position, "tied_at": tied_at})
    return [(url, title, notes, ties.get(lid, []), created_at, lid) for lid, url, title, notes, created_at in
            conn.execute("SELECT id, url, title, notes, created_at FROM links ORDER BY url")]


def restore(conn, row):
    """Put one links.json row (restore_row's) back, inside load's
    transaction: the link under its id (userdata._with_ids), and each of
    its people by name (one missing from people.json is created again). A
    URL put back already (two rows of a hand-edited or older file that
    clean to one) gets this row's people too; its title and notes stay."""
    created = _when(row.get("created_at")) or 0
    conn.execute("INSERT OR IGNORE INTO links(id, url, title, notes, created_at) VALUES (?, ?, ?, ?, ?)",
                 (row.get("id"), row["url"], row.get("title") or "", row.get("notes") or "", created))
    lid = find(conn, row["url"])
    if lid is None:                            # its id taken by another URL: not put back
        return
    for p in row.get("people") or ():
        conn.execute("INSERT OR IGNORE INTO people(name, created_at) VALUES (?, ?)", (p["name"], created))
        conn.execute("INSERT OR IGNORE INTO link_people(link_id, person_id, position, tied_at) "
                     "SELECT ?, id, ?, ? FROM people WHERE name = ?",
                     (lid, p["position"], created if p["tied_at"] is None else p["tied_at"], p["name"]))
