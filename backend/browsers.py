"""Browser profiles: which of a browser's profiles a cookies session names.

A cookies session may carry ``"profile"`` (sources.clean_session). Without
it the tool picks a profile on its own; with it FeedVault finds that
profile's folder when the command is built and hands the folder on:

- Firefox: the profile's name as in Firefox's ``profiles.ini`` (``Name=``
  of a ``[ProfileN]`` section), resolved through that file's ``Path`` and
  ``IsRelative``. Never guessed from folder names: ``xxxx.test`` may be
  there twice, one of them a leftover no profile uses.
- chrome, chromium, brave, edge: the profile's folder name in the
  browser's own folder (``Default``, ``Profile 1``).

Read here: ``profiles.ini`` (names and paths), and which folders exist.
No cookie database is opened or read, by any function of this module: the
tool that is given the folder does that.

Linux locations, the ones gallery-dl 1.32.16 and yt-dlp 2026.08.19 look in
themselves (their cookies.py): Firefox's in ``$XDG_CONFIG_HOME/mozilla/firefox``,
``~/.mozilla/firefox``, the Flatpak's and the Snap's; the others' in
``$XDG_CONFIG_HOME``.
"""
import configparser
import os
import re

NAME_MAX = 100
INI_MAX = 1 << 20                              # a profiles.ini is a few hundred bytes
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# A Chrome-family profile is a folder's name, joined onto the browser's
# folder: nothing a path or a tool's option syntax would read otherwise.
_FOLDER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]*")
_CHROMIUM = {"chrome": "google-chrome", "chromium": "chromium", "brave": "BraveSoftware/Brave-Browser",
             "edge": "microsoft-edge"}
BROWSERS = ("firefox", *_CHROMIUM)             # sources.BROWSERS
# In a folder's path, what the tools would read as something else:
# --cookies-from-browser BROWSER:PROFILE::CONTAINER ends the profile at "::",
# yt-dlp expands $NAME in it (expand_path) and globs it (_firefox_cookie_dbs).
_UNSAFE = re.compile(r"::|[$*?\[]")
# The cookie database in a profile's folder, for instaloader's --cookiefile
# (browser_cookie3 0.20.1: Firefox's, and Chrome's on Linux).
COOKIE_FILE = {"firefox": "cookies.sqlite"}


class Unknown(ValueError):
    """A profile that cannot be used; str() says why, naming it."""


def clean_name(browser, value):
    """A profile's name as stored, or None when it is not one: short text,
    no path separator, no control character, no leading "-" or outer
    space. A Chrome-family one is a plain folder name besides."""
    if not isinstance(value, str) or not 0 < len(value) <= NAME_MAX or value != value.strip():
        return None
    if "/" in value or "\\" in value or value.startswith("-") or _CONTROL.search(value):
        return None
    if browser != "firefox" and not _FOLDER_NAME.fullmatch(value):
        return None
    return value


def _home():
    return os.path.expanduser("~")


def _config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")


def firefox_roots():
    """Where a Firefox keeps its profiles.ini, in the order the tools look."""
    home = _home()
    return [os.path.join(_config_home(), "mozilla", "firefox"),
            os.path.join(home, ".mozilla", "firefox"),
            os.path.join(home, ".var", "app", "org.mozilla.firefox", "config", "mozilla", "firefox"),
            os.path.join(home, ".var", "app", "org.mozilla.firefox", ".mozilla", "firefox"),
            os.path.join(home, "snap", "firefox", "common", ".mozilla", "firefox")]


def _ini(root):
    """A profiles.ini's (name, folder) pairs; none when it is missing or unreadable."""
    path = os.path.join(root, "profiles.ini")
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(INI_MAX + 1)
        if len(text) > INI_MAX:
            return []
        parser.read_string(text)
    except (OSError, ValueError, configparser.Error):
        return []
    out = []
    for section in parser.sections():
        if not section.startswith("Profile"):
            continue
        name, folder = parser.get(section, "Name", fallback=None), parser.get(section, "Path", fallback=None)
        if not name or not folder:
            continue
        if parser.get(section, "IsRelative", fallback="1").strip() == "1":
            folder = os.path.join(root, folder)
        if os.path.isabs(folder):
            out.append((name, os.path.normpath(folder)))
    return out


def _firefox():
    """Every Firefox profile whose folder exists: [(name, folder)], a folder once."""
    out, seen = [], set()
    for root in firefox_roots():
        for name, folder in _ini(root):
            real = os.path.realpath(folder)
            if real not in seen and os.path.isdir(folder):
                seen.add(real)
                out.append((name, folder))
    return out


def _chromium_root(browser):
    return os.path.join(_config_home(), *_CHROMIUM[browser].split("/"))


def names(browser):
    """The names of ``browser``'s profiles, sorted: what a session's
    "profile" can be. Names only; empty when the browser is not there."""
    if browser == "firefox":
        found = {name for name, _ in _firefox() if clean_name(browser, name)}
    else:
        root = _chromium_root(browser)
        try:
            entries = os.listdir(root)
        except OSError:
            entries = []
        # A profile's folder holds its Preferences (looked for, not opened).
        found = {e for e in entries if clean_name(browser, e) and os.path.isfile(os.path.join(root, e, "Preferences"))}
    return sorted(found, key=lambda n: (n.lower(), n))


def listing():
    """{browser: [profile names]} for every browser a session can name."""
    return {b: names(b) for b in BROWSERS}


def folder(browser, name):
    """The existing folder of ``browser``'s profile ``name``, absolute.
    Raises Unknown when there is none, when two Firefox installations each
    have one (a guess could be the wrong account's), or when its path is
    one the tools would not read as a path."""
    if clean_name(browser, name) is None:
        raise Unknown(f'"{name}" is not a {browser} profile\'s name')
    if browser == "firefox":
        found = [f for n, f in _firefox() if n == name]
        if len(found) > 1:
            raise Unknown(f'more than one Firefox profile is named "{name}" (two installations?): '
                          "rename one in Firefox (about:profiles)")
    else:
        path = os.path.join(_chromium_root(browser), name)
        found = [path] if os.path.isdir(path) else []
    if not found:
        raise Unknown(f'the {browser} profile "{name}" was not found (renamed or removed in the browser?): '
                      "pick a browser profile in the session's settings")
    if not os.path.isabs(found[0]) or _UNSAFE.search(found[0]) or _CONTROL.search(found[0]):
        raise Unknown(f'the {browser} profile "{name}" is in a folder the tools cannot be given '
                      "(its path holds ::, $, *, ?, [ or a control character)")
    return found[0]


def cookie_file(browser, name):
    """The cookie database of a profile, for a tool that takes the file
    (instaloader --cookiefile): a path built from folder(), never opened."""
    return os.path.join(folder(browser, name), COOKIE_FILE.get(browser, "Cookies"))
