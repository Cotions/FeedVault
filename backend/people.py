"""People: one person's accounts across platforms, linked into one.

An account is a platform and an author id, as the index has it (see
db.accounts). Links are user data, like tags: kept in ``people`` and
``person_accounts``, never touched by a rescan, and mirrored to JSON by
userdata.py, accounts by platform and author id and people by name, so a
rebuilt index gets them back. They are keyed by account, not by post, so a
post trashed and restored, or replaced by a duplicate copy, stays with its
person. An account with no person is shown on its own, as before.

The person is only in the database; files stay where each tool wrote them.
"""
import collections
import hashlib
import json
import os
import re
import sqlite3
import string
import unicodedata

import avatars
import db
import links as web_links           # saved web links; "links" below are person_accounts rows
import organize

MAX_NOTES = 5000
MAX_ACCOUNTS = 500              # accounts per call
MAX_GENDER = 40
MAX_LABELS = 50                 # labels per person
# A rating is 1 to 10: the half stars of five (7 is three and a half), NULL
# for not rated.
MAX_RATING = 10
KEEP = object()                 # update(): leave the rating as it is (None clears it)
# Offered as quick choices; any other gender is kept as typed. One typed in
# another case is stored as the preset.
GENDERS = ("Female", "Male", "Transgender")
# ISO 3166-1 alpha-2: every officially assigned code.
COUNTRIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO
FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE
JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO
MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW
PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM
TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())


def clean_name(name):
    """A person's name with its spaces collapsed, or None (same rules as a tag name)."""
    return organize.clean_name(name)


def clean_gender(value):
    """A gender with its spaces collapsed ('' for none), a preset in its
    own case, or None when it cannot be one."""
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    if len(value) > MAX_GENDER or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return next((g for g in GENDERS if g.lower() == value.lower()), value)


def clean_country(value):
    """An ISO 3166-1 alpha-2 code, uppercase ('' for none), or None when it is not one."""
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value if value == "" or value in COUNTRIES else None


def clean_rating(value):
    """A rating: a whole number from 1 to MAX_RATING (not 3.5, not "7", not
    true), or None when it is not one. Not rated is sent as null, not 0."""
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_RATING:
        return value
    return None


def _fold(name):
    """A label as SQLite's NOCASE compares it: ASCII letters only."""
    return name.translate(_ASCII_LOWER)


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def clean_labels(value):
    """Label names (same rules as a tag name), once each without regard to
    case, in the order given; None when any is malformed or there are too many."""
    if not isinstance(value, list) or len(value) > MAX_LABELS:
        return None
    out = {}
    for v in value:
        name = clean_name(v)
        if name is None:
            return None
        out.setdefault(_fold(name), name)
    return list(out.values())


def restore_row(row):
    """A people.json row as the API would store it: a gender, country,
    picture name or rating it would refuse (the file edited by hand) is
    dropped, not the person. A file from before ratings has none: unrated."""
    return {**row, "gender": clean_gender(row.get("gender") or "") or "",
            "country": clean_country(row.get("country") or "") or "",
            "avatar": avatars.clean_name(row.get("avatar") or "") or "",
            "rating": clean_rating(row.get("rating"))}


def restore_label(row):
    """A person_labels.json row with its label cleaned, or None to skip it."""
    label = clean_name(row.get("label"))
    return {**row, "label": label} if label else None


def clean_accounts(value):
    """[(platform, author id)] from [{"platform", "id"}], or None when any is malformed."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ACCOUNTS:
        return None
    out = []
    for a in value:
        if not isinstance(a, dict) or not isinstance(a.get("platform"), str) or not a["platform"] \
                or not isinstance(a.get("id"), str) or not a["id"]:
            return None
        out.append((a["platform"], a["id"]))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Aliases (derived, on every scan)
# ---------------------------------------------------------------------------

FILENAMES = db.FILENAMES       # tool of posts rebuilt from file names


def _folder(path, folder_id):
    """The folder of ``path`` named ``folder_id`` (any case), nearest first, or None."""
    d = os.path.dirname(path)
    while True:
        if os.path.basename(d).lower() == folder_id:
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def refresh_aliases(conn):
    """Rebuild account_aliases from the index, and move links to the
    account's id. Returns the user tables it changed (for userdata.py).

    Posts rebuilt from file names carry the folder's name as author id; posts
    with metadata carry the platform's own id. The folder name becomes an
    alias of the id, so it is shown and linked with it, when:

    - instaloader's id file in (or beside) the folder names the id: it is
      the folder's account, whatever its posts' handles;
    - else the folder holds both for the same handle, and exactly one
      account with metadata in that folder has that handle, so a folder of
      mixed downloads never merges two people."""
    # Two passes over posts, not three queries per account: on an archive of
    # file-name posts those were a second per rescan.
    tops = conn.execute("SELECT platform, author_id, MIN(meta_path) FROM posts "
                        "WHERE tool LIKE ? AND author_id IS NOT NULL GROUP BY 1, 2", (FILENAMES,)).fetchall()
    handles = collections.defaultdict(set)
    for platform, folder_id, h in conn.execute(
            "SELECT DISTINCT platform, author_id, lower(author_handle) FROM posts "
            "WHERE tool LIKE ? AND author_id IS NOT NULL", (FILENAMES,)):
        handles[platform, folder_id].add(h or "")
    in_dir, beside = {}, {}                    # id files: folder -> account, (parent, name) -> account
    for path, platform, aid, handle in conn.execute("SELECT path, platform, author_id, handle FROM account_files"):
        d, n = os.path.split(path)
        if n == "id":
            in_dir[d] = (platform, aid)
        else:
            beside[d, handle] = (platform, aid)
    folders = collections.defaultdict(list)    # (platform, handle) -> [(folder + "/", folder id)]
    ids = collections.defaultdict(set)
    for platform, folder_id, meta_path in tops:
        folder = _folder(meta_path, folder_id)
        if folder is None:
            continue
        said = in_dir.get(folder) or beside.get((os.path.dirname(folder), folder_id))
        if said and said[0] == platform:
            if said[1] != folder_id:
                ids[platform, folder_id] = {said[1]}
            continue
        for h in handles[platform, folder_id] | {folder_id}:
            folders[platform, h].append((folder + os.sep, folder_id))
    if folders:
        for platform, author_id, h, meta_path in conn.execute(
                "SELECT DISTINCT platform, author_id, lower(author_handle), meta_path FROM posts "
                "WHERE tool NOT LIKE ? AND author_id IS NOT NULL AND meta_path IS NOT NULL "
                "AND lower(author_handle) IN (SELECT value FROM json_each(?))",
                (FILENAMES, json.dumps(sorted({h for _, h in folders})))):
            for folder, folder_id in folders.get((platform, h), ()):
                if author_id != folder_id and meta_path.startswith(folder):
                    ids[platform, folder_id].add(author_id)
    # Nor when the two are linked to different people: that is the user's
    # merge to make, and a merged account must show under one person.
    links = {(r[0], r[1]): r[2] for r in conn.execute("SELECT platform, author_id, person_id FROM person_accounts")}
    found = []
    for (p, f), i in ids.items():
        if len(i) == 1:
            target = next(iter(i))
            mine, theirs = links.get((p, f)), links.get((p, target))
            if mine is None or theirs is None or mine == theirs:
                found.append((p, f, target))
    have = set(conn.execute("SELECT platform, alias_id, author_id FROM account_aliases").fetchall())
    if have != set(found):                     # an unchanged index stays unchanged (and cached)
        conn.execute("DELETE FROM account_aliases")
        conn.executemany("INSERT OR REPLACE INTO account_aliases(platform, alias_id, author_id) VALUES (?, ?, ?)",
                         found)
    return _move_links(conn, found, links)


def _move_links(conn, found, links):
    """Links (and sources) made to a folder name move to the account's id
    once its metadata is indexed, so a folder renamed after the account
    (instaloader does) or gone keeps nobody from their person. Also a link
    to an account no post has any more (its folder was renamed), when
    exactly one account had that handle: the same account, renamed.
    Returns the user tables changed."""
    indexed = lambda p, a: conn.execute(  # noqa: E731
        "SELECT 1 FROM posts WHERE platform = ? AND author_id = ? LIMIT 1", (p, a)).fetchone() is not None
    moves = [(p, f, t) for p, f, t in found if indexed(p, t)]
    alias = {(p, f): t for p, f, t in found}
    for (p, a), pid in links.items():
        if (p, a) in alias or indexed(p, a):
            continue
        had = {alias.get((p, r[0]), r[0]) for r in conn.execute(
            "SELECT DISTINCT author_id FROM posts WHERE platform = ? AND lower(author_handle) = ? "
            "AND author_id IS NOT NULL UNION SELECT author_id FROM account_files WHERE platform = ? AND handle = ?",
            (p, a.lower(), p, a.lower()))} - {a}
        if len(had) == 1 and links.get((p, next(iter(had))), pid) == pid:
            moves.append((p, a, next(iter(had))))
    changed = []
    for p, f, t in moves:
        pid = links.get((p, f))
        if pid is not None:
            theirs = links.get((p, t))
            if theirs is not None and theirs != pid:
                continue                       # two folders of one id, linked to two people: the user's call
            if theirs == pid:
                conn.execute("DELETE FROM person_accounts WHERE platform = ? AND author_id = ?", (p, f))
            else:
                conn.execute("UPDATE person_accounts SET author_id = ? WHERE platform = ? AND author_id = ?",
                             (t, p, f))
                links[p, t] = pid
            del links[p, f]
            changed.append("person_accounts")
        for table in ("sources", "handle_renames"):
            if conn.execute(f"UPDATE OR REPLACE {table} SET author_id = ? WHERE platform = ? AND author_id = ?",
                            (t, p, f)).rowcount:
                changed.append(table)
    return sorted(set(changed))


def canonical(conn, platform, author_id):
    """The account an id stands for: itself, or the indexed id it is an alias of."""
    target = db.aliases(conn).get((platform, author_id))
    return (platform, target) if target is not None else (platform, author_id)


def account_set(conn, person_id):
    """{(platform, author id)} of a person, aliases included both ways."""
    return {(r[0], r[1]) for r in conn.execute(db.PERSON_ACCOUNTS, (person_id,) * 3)}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _person(row, accounts, labels, no_sync=False):
    """A person as the API shows it. ``accounts`` are account rows (db.accounts).
    ``no_sync``: the person is set to Don't sync (nosync.py)."""
    newest = [a["newest"] for a in accounts if a["newest"] is not None]
    return {
        "id": row["id"], "name": row["name"], "notes": row["notes"], "created_at": row["created_at"],
        "gender": row["gender"], "country": row["country"], "labels": labels,
        "avatar": row["avatar"] or None, "rating": row["rating"], "no_sync": no_sync,
        "accounts": accounts,
        "platforms": sorted({a["platform"] for a in accounts}),
        "count": sum(a["count"] for a in accounts),
        "bytes": sum(a["bytes"] for a in accounts),
        "newest": max(newest) if newest else None,
    }


def _linked(conn, rows):
    """{person id: [account row]}: indexed accounts as db.accounts has them,
    linked ones not in the index (not scanned yet, or every post trashed)
    as empty rows."""
    known = db.accounts(conn)
    out = {r["id"]: [] for r in rows}
    seen = set()                               # (person, account): a link and its alias show once
    ids = list(out)
    if not ids:
        return out
    for pid, platform, aid in conn.execute(
            "SELECT person_id, platform, author_id FROM person_accounts "
            f"WHERE person_id IN ({', '.join('?' for _ in ids)}) ORDER BY platform, author_id", ids):
        key = canonical(conn, platform, aid)
        if (pid, key) in seen:
            continue
        seen.add((pid, key))
        a = known.get(key)
        if a is None:
            a = {"platform": platform, "id": aid, "handle": None, "name": None, "count": 0, "bytes": 0,
                 "newest": None, "aliases": [], "person": None, "handles": [], "names": [], "url": None,
                 "no_sync": conn.execute("SELECT 1 FROM no_sync_accounts WHERE platform = ? AND author_id = ?",
                                         (platform, aid)).fetchone() is not None}
        out[pid].append(a)
    for accounts in out.values():
        accounts.sort(key=lambda a: (-a["count"], a["platform"], a["handle"] or a["id"]))
    return out


def _labels(conn, ids=None):
    """{person id: [label]}, each person's by name."""
    out = collections.defaultdict(list)
    where = f"WHERE person_id IN ({', '.join('?' for _ in ids)}) " if ids is not None else ""
    for pid, label in conn.execute(f"SELECT person_id, label FROM person_labels {where}ORDER BY label",
                                   list(ids or ())):
        out[pid].append(label)
    return out


def people(conn):
    """Every person, by name."""
    def compute(conn):
        rows = conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE, id").fetchall()
        linked, labels, off = _linked(conn, rows), _labels(conn), _no_sync_people(conn)
        return [_person(r, linked[r["id"]], labels[r["id"]], r["id"] in off) for r in rows]
    return db._memo(conn, ("people",), compute)


def attributes(conn):
    """What people are described by, for the pickers and filters: labels and
    genders in use, most used first, the gender presets, and the countries
    a person can have."""
    def compute(conn):
        used = lambda sql: [{"name": r[0], "count": r[1]} for r in conn.execute(sql)]  # noqa: E731
        return {
            "labels": used("SELECT MIN(label), COUNT(*) FROM person_labels GROUP BY label "
                           "ORDER BY 2 DESC, 1 COLLATE NOCASE"),
            "genders": used("SELECT MIN(gender), COUNT(*) FROM people WHERE gender != '' "
                            "GROUP BY gender COLLATE NOCASE ORDER BY 2 DESC, 1 COLLATE NOCASE"),
            "presets": list(GENDERS),
            "countries": sorted(COUNTRIES),
        }
    return db._memo(conn, ("people-attributes",), compute)


def exists(conn, pid):
    """Whether a person has that id (an id SQLite cannot hold has nobody)."""
    return 0 <= pid < 2**53 and conn.execute("SELECT 1 FROM people WHERE id = ?", (pid,)).fetchone() is not None


def person(conn, pid):
    row = conn.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
    return _person(row, _linked(conn, [row])[pid], _labels(conn, [pid])[pid], pid in _no_sync_people(conn)) \
        if row else None


def _no_sync_people(conn):
    """{person id} of the people set to Don't sync (nosync.py)."""
    return {r[0] for r in conn.execute("SELECT person_id FROM no_sync_people")}


# ---------------------------------------------------------------------------
# Changes (each returns what the API answers; the caller notes userdata)
# ---------------------------------------------------------------------------

class Refused(ValueError):
    """A change that cannot be made; the message is for the user."""


def _name_free(conn, name, but=()):
    row = conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
    return row is None or row[0] in but


def _check_accounts(conn, accounts):
    """The accounts as stored (aliases replaced by the id they stand for);
    Refused when one is not in the index."""
    known = db.accounts(conn)
    out = []
    for platform, aid in accounts:
        key = canonical(conn, platform, aid)
        if key not in known:
            raise Refused(f"unknown account {platform}:{aid}")
        out.append(key)
    return list(dict.fromkeys(out))


def _attach(conn, pid, accounts, now):
    """Link accounts to a person, taking them from any other person (an
    account belongs to one person). Returns how many links changed.

    One taken from a person set to Don't sync (nosync.py) by one who is not
    gets the switch of its own, so none of its sources starts syncing (the
    caller notes userdata's no_sync_accounts, nosync.count_accounts)."""
    off = {r[0] for r in conn.execute("SELECT person_id FROM no_sync_people")}
    n = 0
    for platform, aid in accounts:
        if off and pid not in off:
            owners = {r[0] for r in conn.execute(
                "SELECT person_id FROM person_accounts WHERE platform = ? AND (author_id = ? OR author_id IN "
                "(SELECT alias_id FROM account_aliases WHERE platform = ? AND author_id = ?))",
                (platform, aid, platform, aid))}
            if owners & off:
                conn.execute("INSERT OR IGNORE INTO no_sync_accounts(platform, author_id, at) VALUES (?, ?, ?)",
                             (platform, aid, now))
        # Drop the alias either way, so an account is never linked twice.
        conn.execute("DELETE FROM person_accounts WHERE platform = ? AND author_id IN "
                     "(SELECT alias_id FROM account_aliases WHERE platform = ? AND author_id = ?)",
                     (platform, platform, aid))
        row = conn.execute("SELECT person_id FROM person_accounts WHERE platform = ? AND author_id = ?",
                           (platform, aid)).fetchone()
        if row and row[0] == pid:
            continue
        conn.execute("INSERT OR REPLACE INTO person_accounts(person_id, platform, author_id, at) VALUES (?, ?, ?, ?)",
                     (pid, platform, aid, now))
        n += 1
    return n


def create(conn, name, accounts, now):
    """A new person with these accounts (moved from any other person)."""
    if not _name_free(conn, name):
        raise Refused("a person with that name exists")
    accounts = _check_accounts(conn, accounts)
    try:
        with conn:
            pid = conn.execute("INSERT INTO people(name, created_at) VALUES (?, ?)", (name, now)).lastrowid
            _attach(conn, pid, accounts, now)
    except sqlite3.IntegrityError:              # the name, taken by another request since the check
        raise Refused("a person with that name exists")
    return person(conn, pid)


def _set_labels(conn, pid, labels, now):
    """A person's labels become ``labels``: the ones kept keep their date
    (and take the case they are given in)."""
    have = {_fold(r[0]): r[0] for r in conn.execute("SELECT label FROM person_labels WHERE person_id = ?", (pid,))}
    wanted = {_fold(name): name for name in labels}
    for key, name in have.items():
        if key not in wanted:
            conn.execute("DELETE FROM person_labels WHERE person_id = ? AND label = ?", (pid, name))
    for key, name in wanted.items():
        if key in have:
            conn.execute("UPDATE person_labels SET label = ? WHERE person_id = ? AND label = ?", (name, pid, have[key]))
        else:
            conn.execute("INSERT INTO person_labels(person_id, label, at) VALUES (?, ?, ?)", (pid, name, now))


def update(conn, pid, name=None, notes=None, gender=None, country=None, labels=None, now=0, avatar=None,
           rating=KEEP):
    """Change what is given; None leaves it as it is (``avatar``: '' for none;
    the caller drops the files of the one replaced, avatars.drop_unused).
    ``rating``: 1 to 10, None for not rated, KEEP to leave it."""
    if name is not None and not _name_free(conn, name, but=(pid,)):
        raise Refused("a person with that name exists")
    try:
        with conn:
            if name is not None:
                conn.execute("UPDATE people SET name = ? WHERE id = ?", (name, pid))
            if notes is not None:
                conn.execute("UPDATE people SET notes = ? WHERE id = ?", (notes, pid))
            if gender is not None:
                conn.execute("UPDATE people SET gender = ? WHERE id = ?", (gender, pid))
            if country is not None:
                conn.execute("UPDATE people SET country = ? WHERE id = ?", (country, pid))
            if labels is not None:
                _set_labels(conn, pid, labels, now)
            if avatar is not None:
                conn.execute("UPDATE people SET avatar = ? WHERE id = ?", (avatar, pid))
            if rating is not KEEP:
                conn.execute("UPDATE people SET rating = ? WHERE id = ?", (rating, pid))
    except sqlite3.IntegrityError:              # the name, taken by another request since the check
        raise Refused("a person with that name exists")
    return person(conn, pid)


def delete(conn, pid):
    """Drop a person and its links. Posts are never touched, nor their saved
    web links (links.py), which stay, tied to no one. Returns the accounts
    unlinked."""
    with conn:
        n = conn.execute("DELETE FROM person_accounts WHERE person_id = ?", (pid,)).rowcount
        conn.execute("UPDATE links SET person_id = NULL, position = NULL, tied_at = NULL WHERE person_id = ?", (pid,))
        conn.execute("DELETE FROM people WHERE id = ?", (pid,))
    return n


def link(conn, pid, add, remove, now):
    """Add accounts to a person (moved from any other) and remove some of
    its own. Removing an account removes its aliases' links too."""
    add = _check_accounts(conn, add)
    removed = 0
    with conn:
        added = _attach(conn, pid, add, now)
        for platform, aid in remove:
            platform, aid = canonical(conn, platform, aid)
            removed += conn.execute(
                "DELETE FROM person_accounts WHERE person_id = ? AND platform = ? AND (author_id = ? OR author_id IN "
                "(SELECT alias_id FROM account_aliases WHERE platform = ? AND author_id = ?))",
                (pid, platform, aid, platform, aid)).rowcount
    return {"added": added, "removed": removed, "person": person(conn, pid)}


def merge(conn, ids, name, accounts, now):
    """Fold several people (and accounts) into the first id: its links,
    notes and name stay, the others' accounts, sources and saved web links
    move to it and they are gone. Labels are put together; the gender, the
    country and the picture are those of the first person (in ``ids``) that
    has one (the caller drops the files of the others', avatars.drop_unused).
    The rating is the highest of theirs: a merge never lowers anyone's,
    whichever comes first (unrated when none of them is rated)."""
    keep = ids[0]
    if name is not None and not _name_free(conn, name, but=ids):
        raise Refused("a person with that name exists")
    accounts = _check_accounts(conn, accounts)
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM people WHERE id IN ({', '.join('?' for _ in ids)})", ids)}
    with conn:
        notes = [rows[i]["notes"].strip() for i in ids if rows[i]["notes"].strip()]
        first = lambda column: next((rows[i][column] for i in ids if rows[i][column]), "")  # noqa: E731
        rating = max((rows[i]["rating"] for i in ids if rows[i]["rating"] is not None), default=None)
        web_links.merge_into(conn, keep, ids[1:])
        for other in ids[1:]:
            conn.execute("UPDATE person_accounts SET person_id = ? WHERE person_id = ?", (keep, other))
            conn.execute("UPDATE sources SET person_id = ? WHERE person_id = ?", (keep, other))
            # A label both have stays the kept person's; the other's copy goes with them.
            conn.execute("UPDATE OR IGNORE person_labels SET person_id = ? WHERE person_id = ?", (keep, other))
            conn.execute("DELETE FROM people WHERE id = ?", (other,))
        _attach(conn, keep, accounts, now)
        conn.execute("UPDATE people SET notes = ?, gender = ?, country = ?, avatar = ?, rating = ? WHERE id = ?",
                     ("\n\n".join(notes)[:MAX_NOTES], first("gender"), first("country"), first("avatar"), rating,
                      keep))
        if name is not None:
            conn.execute("UPDATE people SET name = ? WHERE id = ?", (name, keep))
    return person(conn, keep)


# ---------------------------------------------------------------------------
# Suggestions: accounts that are likely one person
#
# Read from the index and what downloaded metadata says; nothing is fetched.
# ---------------------------------------------------------------------------

# Most sure first; a group found for several reasons scores a little higher.
SCORES = {"bio_link": 0.95, "same_handle": 0.9, "similar_handle": 0.7, "same_name": 0.6}
# A group this large shares something common ("official", a first name),
# not a person: left out, which also keeps the list, and its cost, bounded.
MAX_GROUP = 8
_PREFIXES = ("the", "real", "its", "official")
_SUFFIXES = ("official",)
_LINK_RE = re.compile(r"(?<![\w.-])(?:https?://)?(?:www\.|m\.|mobile\.)?(instagram\.com|x\.com|twitter\.com|tiktok\.com|"
                      r"youtube\.com)"
                      r"/(@?[A-Za-z0-9._-]{1,30})", re.IGNORECASE)
_LINK_PLATFORMS = {"instagram.com": "instagram", "x.com": "twitter", "twitter.com": "twitter", "tiktok.com": "tiktok",
                   "youtube.com": "youtube"}
# First path parts that are pages, not profiles.
_NOT_HANDLES = {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "i", "intent", "share", "home",
                "hashtag", "search", "status", "video", "tag", "discover", "music", "login", "settings"}


def handle_parts(handle):
    """(base, digits): a handle with what people add to a taken one taken
    off (case, dots, underscores and dashes, a leading "the", "real", "its" or
    "official", a trailing "official"), and its trailing digits apart. The
    base is None when too little is left."""
    h = re.sub(r"[._-]", "", (handle or "").lower())
    for p in _PREFIXES:
        if h.startswith(p) and len(h) - len(p) >= 3:
            h = h[len(p):]
            break
    for x in _SUFFIXES:
        if h.endswith(x) and len(h) - len(x) >= 3:
            h = h[:-len(x)]
    base = h.rstrip("0123456789")
    return (base if len(base) >= 3 else None), h[len(base):]


def norm_name(name):
    """A display name compared without case, accents, emoji or punctuation;
    None when under 4 letters."""
    folded = unicodedata.normalize("NFKD", (name or "").casefold())
    words = "".join(c if c.isalnum() else " " for c in folded if not unicodedata.combining(c)).split()
    out = " ".join(words)
    return out if sum(len(w) for w in words) >= 4 else None


def profile_links(text):
    """[(platform, handle)] of the profile links in a bio or URL, in order."""
    out = []
    for site, path in _LINK_RE.findall(text or ""):
        platform = _LINK_PLATFORMS[site.lower()]
        if platform in ("tiktok", "youtube") and not path.startswith("@"):
            continue                           # tiktok.com/@handle, youtube.com/@handle only
        handle = path.lstrip("@").rstrip(".").lower()
        if handle and handle not in _NOT_HANDLES:
            out.append((platform, handle))
    return list(dict.fromkeys(out))


def _key(accounts):
    return json.dumps(sorted(f"{p}:{i}" for p, i in accounts))


def suggestion_id(key):
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def _label(a):
    from_platform = {"instagram": "Instagram", "twitter": "X", "tiktok": "TikTok", "youtube": "YouTube"}
    return f"{from_platform.get(a['platform'], a['platform'])} @{a['handle'] or a['id']}"


def _candidates(conn, accounts):
    """[(reason, detail, {account keys})], before any filtering."""
    out = []
    by_handle, by_base, by_name = {}, {}, {}
    for key, a in accounts.items():
        for h in {x["handle"].lower() for x in a["handles"]} | {i.lower() for i in a["aliases"]}:
            by_handle.setdefault(h, set()).add(key)
            base, digits = handle_parts(h)
            if base:
                by_base.setdefault(base, {}).setdefault(digits, {}).setdefault(key, h)
        for x in a["names"]:
            n = norm_name(x["name"])
            if n:
                by_name.setdefault(n, {}).setdefault(key, x["name"])
    for h, keys in by_handle.items():
        if len({k[0] for k in keys}) > 1:      # across platforms (one platform: one account)
            out.append(("same_handle", f"@{h}", keys))
    for by_digits in by_base.values():
        # foo, foo_, thefoo and foo2 are alike; foo1 and foo2 are not.
        plain = by_digits.get("", {})
        for digits in [d for d in by_digits if d] or [""]:
            seen = {**plain, **by_digits[digits]}
            if len(seen) > 1 and len(set(seen.values())) > 1:
                out.append(("similar_handle", " ~ ".join(f"@{h}" for h in sorted(set(seen.values()))), set(seen)))
    for n, seen in by_name.items():
        if len(seen) > 1:
            out.append(("same_name", sorted(seen.values())[0], set(seen)))
    # Bio and website links to another indexed account, by any of its handles.
    for platform, aid, handle, bio, urls in conn.execute(
            "SELECT platform, author_id, handle, bio, urls FROM profiles").fetchall():
        src = canonical(conn, platform, aid)
        if src not in accounts:
            continue
        for p, h in profile_links(" ".join([bio or "", *json.loads(urls or "[]")])):
            for dst in by_handle.get(h, ()):
                if dst[0] == p and dst != src:
                    out.append(("bio_link", f"{_label(accounts[src])} links to {_label(accounts[dst])}", {src, dst}))
    return out


def suggestions(conn):
    """Groups of accounts likely to be one person, most likely first, with
    why. Only groups linking would change: not all in one person already, and
    not two people (that is a merge, the user's call). Dismissed groups are
    left out; a group that gains an account shows again."""
    def compute(conn):
        accounts = db.accounts(conn)
        dismissed = {r[0] for r in conn.execute("SELECT key FROM dismissed_suggestions")}
        groups = {}
        for reason, detail, keys in _candidates(conn, accounts):
            if len(keys) > MAX_GROUP:
                continue
            people = {accounts[k]["person"]["id"] for k in keys if accounts[k]["person"]}
            unlinked = [k for k in keys if not accounts[k]["person"]]
            if len(people) > 1 or not unlinked:
                continue
            key = _key(keys)
            g = groups.setdefault(key, {"keys": keys, "reasons": []})
            if not any(r["reason"] == reason and r["detail"] == detail for r in g["reasons"]):
                g["reasons"].append({"reason": reason, "detail": detail})
        out = []
        for key, g in groups.items():
            if key in dismissed:
                continue
            g["reasons"].sort(key=lambda r: -SCORES[r["reason"]])
            score = SCORES[g["reasons"][0]["reason"]] + 0.02 * (len({r["reason"] for r in g["reasons"]}) - 1)
            members = sorted((accounts[k] for k in g["keys"]), key=lambda a: (-a["count"], a["platform"], a["id"]))
            person = next((a["person"] for a in members if a["person"]), None)
            out.append({"id": suggestion_id(key), "score": round(min(score, 1.0), 2),
                        "reason": g["reasons"][0]["reason"], "reasons": g["reasons"],
                        "accounts": members, "person": person})
        out.sort(key=lambda s: (-s["score"], -sum(a["count"] for a in s["accounts"]), s["id"]))
        return {"suggestions": out, "dismissed": len(dismissed)}
    return db._memo(conn, ("suggestions",), compute)


def dismiss(conn, sid, now):
    """Store a suggestion as "not the same person". False when it is not listed."""
    s = next((s for s in suggestions(conn)["suggestions"] if s["id"] == sid), None)
    if s is None:
        return False
    with conn:
        conn.execute("INSERT OR IGNORE INTO dismissed_suggestions(key, at) VALUES (?, ?)",
                     (_key((a["platform"], a["id"]) for a in s["accounts"]), now))
    return True
