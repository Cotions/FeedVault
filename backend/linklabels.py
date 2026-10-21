"""Link labels: what a saved link with no title is called, by its site.

Settings → Link labels keeps rules ``site → label`` ("patreon.com" →
"Patreon") in config.json's ``link_labels``, shared by every profile, as
the routing table is. A label is never written into a link: each read of a
link (GET /api/links, a person's page) gives it the label of its site as it
is then, as ``label``, so editing or removing a rule changes every link of
that site and loses nothing. A title the user gave always wins (the
dashboard shows ``title || label || url``).

A site with no rule gets a name of its own when FeedVault cannot sync it
(no entry of the routing table covers it, sources.route): the site with a
capital first letter, "patreon.com" → "Patreon.com". A site FeedVault syncs
(instagram.com, youtube.com, …) gets ``null``: the link shows its address,
which says whose profile it is.
"""
import ipaddress

import links
import sources

MAX_LABEL = 80
MAX_RULES = 500
MAX_SITE = 253


def clean_site(value):
    """The site a rule is for, as links.site() gives it ("https://www.patreon.com/x",
    "www.Patreon.com" and "patreon.com" are all "patreon.com"), or None when it
    names no host: a domain name of two parts or more, or an IP address."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > links.MAX_URL:
        return None
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    url = links.clean_url(text)
    if url is None:
        return None
    site = links.site(url)
    if not site or len(site) > MAX_SITE:
        return None
    try:
        ipaddress.ip_address(site)
        return site
    except ValueError:
        pass
    parts = site.split(".")
    return site if len(parts) >= 2 and all(parts) else None


def clean_label(value):
    """A label with its whitespace collapsed, or None when empty, longer than
    MAX_LABEL, not text, or holding a control character."""
    if not isinstance(value, str):
        return None
    label = " ".join(value.split())
    if not label or len(label) > MAX_LABEL or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in label):
        return None
    try:
        label.encode("utf-8")                  # half of a surrogate pair
    except UnicodeEncodeError:
        return None
    return label


def clean_rules(value):
    """Rules from Settings, or (None, error): {site: label}, at most
    MAX_RULES, each site cleaned (clean_site) and given once, each label
    cleaned. An empty object is no rule at all."""
    if not isinstance(value, dict) or len(value) > MAX_RULES:
        return None, f"link_labels must map at most {MAX_RULES} sites to a label"
    out = {}
    for raw, label in value.items():
        site = clean_site(raw)
        if site is None:
            return None, f"not a site: {raw}"
        if site in out:
            return None, f"{site} is given twice"
        clean = clean_label(label)
        if clean is None:
            return None, f"{site}: the label must be 1 to {MAX_LABEL} characters of text"
        out[site] = clean
    return out, None


def rules(cfg):
    """The rules in use: config.json's, each one that does not clean left
    out (a file edited by hand keeps the others)."""
    saved = cfg.get("link_labels")
    if not isinstance(saved, dict):
        return {}
    out = {}
    for raw, label in list(saved.items())[:MAX_RULES]:
        site, clean = clean_site(raw), clean_label(label)
        if site is not None and clean is not None and site not in out:
            out[site] = clean
    return out


def auto(site):
    """A site's own name, for one with no rule that FeedVault cannot sync:
    the site with a capital first letter ("Patreon.com", "Bbc.co.uk"); an
    IP address as it is."""
    try:
        ipaddress.ip_address(site)
        return site
    except ValueError:
        return site[:1].upper() + site[1:]


def namer(cfg):
    """label(site): the rule's label, else auto(site) for a site the routing
    table does not cover, else None."""
    given, table, seen = rules(cfg), sources.routes(cfg), {}

    def label(site):
        if site not in seen:
            seen[site] = given.get(site) or (None if not site or sources.route(site, table) else auto(site))
        return seen[site]
    return label


def apply(cfg, items):
    """Give each link (links._link's dict) its ``label``; the same list back."""
    label = namer(cfg)
    for x in items:
        x["label"] = label(x["site"])
    return items


def matching(cfg, q):
    """The sites whose rule's label holds ``q`` (case aside): a search
    finds a link with no title by the label it shows."""
    q = q.casefold()
    return {site for site, label in rules(cfg).items() if q in label.casefold()}
