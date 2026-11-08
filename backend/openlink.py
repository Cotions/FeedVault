"""Open a saved link in a browser of the user's choice: which browser, which
of its profiles, and a private window or not (Settings → Downloads → Open
links in, and a link row's "Open in…" menu).

The dashboard is a page in one browser; a plain link opens in that one.
Here FeedVault, which runs on the user's own machine, starts the browser
itself, as the user would from a terminal:

- Firefox: ``firefox -P <name> --new-tab <url>``, or ``--private-window``
  in place of ``--new-tab``. The name is the profile's as in its
  profiles.ini (browsers.names).
- chrome, chromium, brave, edge: ``<binary> --profile-directory=<folder>
  <url>``, ``--incognito`` before the URL for a private window
  (``--inprivate`` for Edge). The folder is the profile's folder name
  (``Default``, ``Profile 1``), as browsers.names lists it.
- No profile: the flag is left out, and the browser takes the one it would
  (the last used one, for the Chromium family).

A browser that runs already gets the URL from the new process, which then
quits; one that does not starts. Either way FeedVault does not wait: the
process starts detached (its own session, no terminal, every stream on
/dev/null), and a thread only reaps it when it ends.

What is checked before anything starts:

- The URL is one links.clean_url takes (http or https, no space, no control
  character), and it may not start with "-" (it could not: it starts with
  its scheme, but a browser must never read it as an option). It is the
  argument list's last element, and no shell ever reads the list.
- The browser is one of browsers.BROWSERS, found on PATH by its usual names
  (BINARIES), else as a Flatpak (FLATPAKS: ``flatpak run <app id>``) when
  ``flatpak`` is on PATH and the app is installed (its folder in the
  user's or the system's Flatpak installation; nothing is run to find out).
- The profile is one the browser has now (browsers.names), else
  browsers.Unknown, which says so.

FEEDVAULT_NO_LAUNCH=1 (the e2e test instance, frontend/e2e/harness.js)
refuses every launch with a message: no test can start a browser through
POST /api/open, whatever a page asks.

The URL is never logged: a link opened in a private window leaves no trace
in FeedVault's output either.
"""
import os
import shutil
import subprocess
import threading

import browsers
import links

NAMES = {"firefox": "Firefox", "chrome": "Google Chrome", "chromium": "Chromium", "brave": "Brave",
         "edge": "Microsoft Edge"}
# Each browser's command names on PATH, most usual first (Debian, Fedora,
# Arch and the vendors' own packages; a Snap's are on PATH too).
BINARIES = {
    "firefox": ("firefox",),
    "chrome": ("google-chrome", "google-chrome-stable"),
    "chromium": ("chromium", "chromium-browser"),
    "brave": ("brave-browser", "brave"),
    "edge": ("microsoft-edge", "microsoft-edge-stable"),
}
# The Flathub app ids, when none of the names above is on PATH.
FLATPAKS = {
    "firefox": "org.mozilla.firefox",
    "chrome": "com.google.Chrome",
    "chromium": "org.chromium.Chromium",
    "brave": "com.brave.Browser",
    "edge": "com.microsoft.Edge",
}
# Where an installed Flatpak app has its folder: the system's installation
# (a constant, so a test points it at its own folder) and the user's.
SYSTEM_FLATPAK = "/var/lib/flatpak/app"
PRIVATE_FLAG = {"firefox": "--private-window", "edge": "--inprivate"}   # the others: --incognito
OFF_ENV = "FEEDVAULT_NO_LAUNCH"
SETTING = "open_links"                         # config.json's key, shared by every profile


class Refused(ValueError):
    """A link that cannot be opened as asked; str() says why."""


def _user_flatpak():
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "flatpak", "app")


def _flatpak_installed(app_id):
    return any(os.path.isdir(os.path.join(root, app_id)) for root in (_user_flatpak(), SYSTEM_FLATPAK))


def command(browser):
    """How ``browser`` is started: [binary] found on PATH, else
    [flatpak, "run", app id] for an installed Flatpak; None when neither."""
    for name in BINARIES.get(browser, ()):
        path = shutil.which(name)
        if path:
            return [path]
    app_id = FLATPAKS.get(browser)
    flatpak = shutil.which("flatpak") if app_id else None
    if flatpak and _flatpak_installed(app_id):
        return [flatpak, "run", app_id]
    return None


def installed():
    """The browsers that can be started, in browsers.BROWSERS's order:
    [{"id", "name", "flatpak", "profiles": [names]}]. A Flatpak's
    Chromium-family profiles are in its own folder, which browsers.names
    does not read: it is offered without one (the browser picks)."""
    out = []
    for b in browsers.BROWSERS:
        cmd = command(b)
        if cmd is None:
            continue
        flatpak = len(cmd) > 1
        profiles = browsers.names(b) if b == "firefox" or not flatpak else []
        out.append({"id": b, "name": NAMES[b], "flatpak": flatpak, "profiles": profiles})
    return out


def _clean_choice(browser, profile, private):
    """(browser, profile or None, private) as sent, or Refused."""
    if browser not in browsers.BROWSERS:
        raise Refused(f"browser must be one of {', '.join(browsers.BROWSERS)}")
    if profile in (None, ""):
        profile = None
    elif browsers.clean_name(browser, profile) is None:
        raise Refused(f"{str(profile)[:100]!r} is not a {browser} profile's name")
    if not isinstance(private, bool):
        raise Refused("private must be true or false")
    return browser, profile, private


def clean_setting(value):
    """The saved default from Settings, or (None, error): {"browser",
    "profile" (a name, or null: the browser picks), "private"}. Returns
    (value, None). Whether the browser is installed is checked when a link
    is opened, not here: one removed since keeps its setting."""
    if not isinstance(value, dict) or not set(value) <= {"browser", "profile", "private"} or "browser" not in value:
        return None, "open_links must be { browser, profile?, private? } or null (this browser)"
    try:
        browser, profile, private = _clean_choice(value["browser"], value.get("profile"), value.get("private", False))
    except Refused as e:
        return None, f"open_links: {e}"
    return {"browser": browser, "profile": profile, "private": private}, None


def setting(cfg):
    """The default in use: config.json's, else None (links open in the
    browser showing FeedVault). One a hand edit broke is None too."""
    saved = cfg.get(SETTING)
    if saved is None:
        return None
    clean, error = clean_setting(saved)
    return clean if error is None else None


def argv(url, browser, profile=None, private=False):
    """The argument list that opens ``url`` as asked. Raises Refused (a bad
    URL, browser or flag; a browser not installed) or browsers.Unknown (a
    profile it does not have)."""
    clean = links.clean_url(url) if isinstance(url, str) else None
    if clean is None or url.strip().startswith("-") or clean.startswith("-"):
        raise Refused("only an http:// or https:// address can be opened")
    browser, profile, private = _clean_choice(browser, profile, private)
    cmd = command(browser)
    if cmd is None:
        tried = ", ".join(BINARIES[browser])
        raise Refused(f"{NAMES[browser]} was not found: none of {tried} is on PATH, "
                      f"nor is the Flatpak {FLATPAKS[browser]} installed")
    if profile is not None and profile not in browsers.names(browser):
        raise browsers.Unknown(f'{NAMES[browser]} has no profile named "{profile}" now (renamed or removed?): '
                               "pick another in Settings → Downloads → Open links in")
    # The cleaned address goes (a saved link's is already): http(s), its
    # host lowercased, nothing a browser could read as an option.
    url = clean
    if browser == "firefox":
        flags = (["-P", profile] if profile else []) + [PRIVATE_FLAG["firefox"] if private else "--new-tab"]
    else:
        flags = ([f"--profile-directory={profile}"] if profile else []) \
            + ([PRIVATE_FLAG.get(browser, "--incognito")] if private else [])
    return [*cmd, *flags, url]


def _reap(proc):
    try:
        proc.wait()
    except OSError:
        pass


def open_url(url, browser, profile=None, private=False):
    """Start the browser on ``url`` (argv()), detached, without waiting.
    Raises as argv() does, Refused when launching is off (OFF_ENV), and
    Refused when the program cannot be started."""
    args = argv(url, browser, profile, private)
    if os.environ.get(OFF_ENV) == "1":
        raise Refused(f"opening links in another browser is off here ({OFF_ENV}=1)")
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError as e:
        raise Refused(f"could not start {NAMES[browser]}: {e.strerror or e}") from None
    # Reaped when it ends (at once when a running browser took the URL), so
    # no zombie is left behind; a daemon thread, so it never holds up a quit.
    threading.Thread(target=_reap, args=(proc,), daemon=True, name="openlink-reap").start()
    return args
