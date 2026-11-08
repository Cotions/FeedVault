"""Opening a link in a browser, a profile of it, a private window
(openlink.py, POST /api/open, GET /api/open/browsers).

No browser is ever started: every test here replaces subprocess.Popen in
openlink with a recorder (``launched``) and asserts on the argument list it
was given. The browsers "found" are empty files in the guard's own PATH
folder (conftest.tool_guard), never run; the Flatpak installations are
folders of the test's own.
"""
import os
import subprocess

import pytest

from conftest import H
from fakes import chromium_profile, firefox_profiles

import browsers
import config
import openlink

URL = "https://example.org/a?b=1"


class Recorder:
    """What Popen was called with; a process that has ended already."""

    def __init__(self, calls):
        self.calls = calls

    def __call__(self, args, **kw):
        self.calls.append((list(args), kw))
        return self

    def wait(self):
        return 0


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(openlink.subprocess, "Popen", Recorder(calls))
    monkeypatch.delenv(openlink.OFF_ENV, raising=False)
    return calls


@pytest.fixture
def path_bin(tool_guard):
    """The PATH folder (empty): ``install(name)`` puts a program there."""
    folder = tool_guard.dir / "bin"

    def install(name):
        exe = folder / name
        exe.write_text("#!/bin/false\n")
        exe.chmod(0o755)
        return str(exe)
    return install


@pytest.fixture
def flatpak_roots(tmp_path, monkeypatch):
    """The system's Flatpak installation, the test's own folder; returns
    a function that installs an app id there."""
    system = tmp_path / "flatpak-system"
    system.mkdir()
    monkeypatch.setattr(openlink, "SYSTEM_FLATPAK", str(system))

    def install(app_id, user=False):
        root = openlink._user_flatpak() if user else str(system)
        os.makedirs(os.path.join(root, app_id, "current"), exist_ok=True)
    return install


def home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


@pytest.fixture
def profiles():
    """Firefox: "work" and "Private stuff"; Chrome, Brave, Edge, Chromium: Default and "Profile 1"."""
    firefox_profiles(home(".mozilla", "firefox"), [("work", "ab12.work"), ("Private stuff", "cd34.private")])
    for sub in ("google-chrome", "chromium", "BraveSoftware/Brave-Browser", "microsoft-edge"):
        for name in ("Default", "Profile 1"):
            chromium_profile(home(".config", *sub.split("/")), name)


# ---------------------------------------------------------------------------
# The argument list, per browser, profile and private window
# ---------------------------------------------------------------------------

BIN = {"firefox": "firefox", "chrome": "google-chrome", "chromium": "chromium", "brave": "brave-browser",
       "edge": "microsoft-edge"}


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("profile", [None, "work", "Private stuff"])
def test_firefox_argv(path_bin, profiles, launched, profile, private):
    exe = path_bin("firefox")
    openlink.open_url(URL, "firefox", profile, private)
    args, kw = launched[0]
    want = [exe] + (["-P", profile] if profile else []) + ["--private-window" if private else "--new-tab", URL]
    assert args == want
    assert len(launched) == 1


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("profile", [None, "Default", "Profile 1"])
@pytest.mark.parametrize("browser", ["chrome", "chromium", "brave", "edge"])
def test_chromium_family_argv(path_bin, profiles, launched, browser, profile, private):
    exe = path_bin(BIN[browser])
    openlink.open_url(URL, browser, profile, private)
    flag = "--inprivate" if browser == "edge" else "--incognito"
    want = [exe] + ([f"--profile-directory={profile}"] if profile else []) + ([flag] if private else []) + [URL]
    assert launched[0][0] == want


def test_the_process_is_detached_and_not_waited_for(path_bin, launched):
    path_bin("firefox")
    openlink.open_url(URL, "firefox")
    _, kw = launched[0]
    assert kw["start_new_session"] is True and kw["close_fds"] is True
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == subprocess.DEVNULL
    assert "shell" not in kw                   # an argument list, never a shell


@pytest.mark.parametrize("browser, names", list(openlink.BINARIES.items()))
def test_each_binary_name_is_found(path_bin, launched, browser, names):
    # The last of a browser's names, alone on PATH (google-chrome-stable, chromium-browser...).
    exe = path_bin(names[-1])
    assert openlink.command(browser) == [exe]


def test_the_first_name_wins(path_bin):
    first = path_bin("google-chrome")
    path_bin("google-chrome-stable")
    assert openlink.command("chrome") == [first]


def test_the_url_goes_cleaned_and_last(path_bin, launched):
    exe = path_bin("chromium")
    openlink.open_url("  HTTPS://Example.ORG/x  ", "chromium", None, True)
    assert launched[0][0] == [exe, "--incognito", "https://example.org/x"]


# ---------------------------------------------------------------------------
# Flatpak, when the binary is not on PATH
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("browser", list(openlink.FLATPAKS))
def test_flatpak_fallback(path_bin, flatpak_roots, launched, browser):
    flatpak = path_bin("flatpak")
    flatpak_roots(openlink.FLATPAKS[browser])
    assert openlink.command(browser) == [flatpak, "run", openlink.FLATPAKS[browser]]
    openlink.open_url(URL, browser, None, True)
    flag = {"firefox": "--private-window", "edge": "--inprivate"}.get(browser, "--incognito")
    assert launched[0][0] == [flatpak, "run", openlink.FLATPAKS[browser], flag, URL]


def test_a_user_flatpak_counts_too(path_bin, flatpak_roots):
    flatpak = path_bin("flatpak")
    flatpak_roots("org.mozilla.firefox", user=True)
    assert openlink.command("firefox") == [flatpak, "run", "org.mozilla.firefox"]


def test_the_binary_wins_over_the_flatpak(path_bin, flatpak_roots):
    exe = path_bin("brave")
    path_bin("flatpak")
    flatpak_roots("com.brave.Browser")
    assert openlink.command("brave") == [exe]


def test_no_flatpak_program_no_fallback(flatpak_roots):
    flatpak_roots("org.mozilla.firefox")
    assert openlink.command("firefox") is None


def test_flatpak_installed_but_not_that_app(path_bin, flatpak_roots):
    path_bin("flatpak")
    flatpak_roots("org.chromium.Chromium")
    assert openlink.command("firefox") is None and openlink.command("chromium") is not None


def test_flatpak_firefox_with_a_profile(path_bin, flatpak_roots, launched):
    # A Flatpak Firefox keeps its profiles.ini under ~/.var/app, which browsers.py reads.
    flatpak = path_bin("flatpak")
    flatpak_roots("org.mozilla.firefox")
    firefox_profiles(home(".var", "app", "org.mozilla.firefox", ".mozilla", "firefox"), [("flat", "x.flat")])
    openlink.open_url(URL, "firefox", "flat")
    assert launched[0][0] == [flatpak, "run", "org.mozilla.firefox", "-P", "flat", "--new-tab", URL]


# ---------------------------------------------------------------------------
# Refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["-P evil", "--new-window=x", "-https://example.org", "javascript:alert(1)",
                                 "file:///etc/passwd", "data:text/html,x", "ftp://example.org", "example.org",
                                 "https://exa mple.org", "https://example.org/\n--x", "", None, 5, ["https://x.org"]])
def test_only_an_http_url_is_opened(path_bin, launched, url):
    path_bin("firefox")
    with pytest.raises(openlink.Refused, match="http"):
        openlink.open_url(url, "firefox")
    assert launched == []


@pytest.mark.parametrize("browser", ["safari", "", None, "FIREFOX", "firefox ", 1])
def test_an_unknown_browser_is_refused(launched, browser):
    with pytest.raises(openlink.Refused, match="browser must be one of"):
        openlink.open_url(URL, browser)
    assert launched == []


@pytest.mark.parametrize("browser, profile", [("firefox", "-P"), ("firefox", "a/b"), ("chrome", "../x"),
                                              ("chrome", "a:b"), ("edge", "--incognito"), ("brave", 5)])
def test_a_bad_profile_name_is_refused(path_bin, launched, browser, profile):
    path_bin(BIN[browser])
    with pytest.raises(openlink.Refused, match="profile"):
        openlink.open_url(URL, browser, profile)
    assert launched == []


@pytest.mark.parametrize("browser, profile", [("firefox", "nope"), ("chrome", "Profile 9"), ("edge", "Default")])
def test_an_unknown_profile_is_refused(path_bin, launched, browser, profile):
    path_bin(BIN[browser])
    firefox_profiles(home(".mozilla", "firefox"), [("work", "ab12.work")])
    chromium_profile(home(".config", "google-chrome"), "Default")
    with pytest.raises(browsers.Unknown, match=f'no profile named "{profile}"'):
        openlink.open_url(URL, browser, profile)
    assert launched == []


def test_private_must_be_a_bool(path_bin, launched):
    path_bin("firefox")
    with pytest.raises(openlink.Refused, match="private"):
        openlink.open_url(URL, "firefox", None, "yes")
    assert launched == []


@pytest.mark.parametrize("browser", list(openlink.BINARIES))
def test_a_browser_not_installed(flatpak_roots, launched, browser):
    with pytest.raises(openlink.Refused, match=f"{openlink.NAMES[browser]} was not found"):
        openlink.open_url(URL, browser)
    assert launched == []


def test_launching_off_refuses_after_every_check(path_bin, launched, monkeypatch):
    path_bin("firefox")
    monkeypatch.setenv(openlink.OFF_ENV, "1")
    with pytest.raises(openlink.Refused, match=openlink.OFF_ENV):
        openlink.open_url(URL, "firefox")
    assert launched == []


def test_a_program_that_cannot_start(path_bin, monkeypatch):
    path_bin("firefox")
    monkeypatch.delenv(openlink.OFF_ENV, raising=False)

    def fail(*a, **kw):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(openlink.subprocess, "Popen", fail)
    with pytest.raises(openlink.Refused, match="could not start Firefox: Permission denied"):
        openlink.open_url(URL, "firefox")


# ---------------------------------------------------------------------------
# Installed browsers
# ---------------------------------------------------------------------------

def test_installed_lists_only_what_is_there(path_bin, flatpak_roots, profiles):
    path_bin("firefox")
    path_bin("chromium-browser")
    path_bin("flatpak")
    flatpak_roots("com.microsoft.Edge")
    assert openlink.installed() == [
        {"id": "firefox", "name": "Firefox", "flatpak": False, "profiles": ["Private stuff", "work"]},
        {"id": "chromium", "name": "Chromium", "flatpak": False, "profiles": ["Default", "Profile 1"]},
        # A Chromium-family Flatpak keeps its profiles in its own folder: offered without one.
        {"id": "edge", "name": "Microsoft Edge", "flatpak": True, "profiles": []},
    ]


def test_nothing_installed(flatpak_roots):
    assert openlink.installed() == []


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, want", [
    ({"browser": "firefox"}, {"browser": "firefox", "profile": None, "private": False}),
    ({"browser": "chrome", "profile": "Profile 1", "private": True},
     {"browser": "chrome", "profile": "Profile 1", "private": True}),
    ({"browser": "edge", "profile": "", "private": False}, {"browser": "edge", "profile": None, "private": False}),
])
def test_clean_setting(value, want):
    assert openlink.clean_setting(value) == (want, None)


@pytest.mark.parametrize("value", [None, "firefox", [], {}, {"profile": "x"}, {"browser": "safari"},
                                   {"browser": "firefox", "private": 1}, {"browser": "chrome", "profile": "../x"},
                                   {"browser": "firefox", "extra": True}])
def test_clean_setting_refuses(value):
    clean, error = openlink.clean_setting(value)
    assert clean is None and "open_links" in error


def test_a_broken_hand_edit_reads_as_this_browser():
    assert openlink.setting({"open_links": {"browser": "netscape"}}) is None
    assert openlink.setting({}) is None


def test_config_saves_and_clears_the_setting(client):
    assert client.get("/api/config", headers=H).get_json()["open_links"] is None
    sent = {"browser": "firefox", "profile": "work", "private": True}
    r = client.post("/api/config", json={"open_links": sent}, headers=H).get_json()
    assert r["ok"] and r["config"]["open_links"] == sent
    assert config.load()["open_links"] == sent
    assert client.get("/api/config", headers=H).get_json()["open_links"] == sent
    r = client.post("/api/config", json={"open_links": {"browser": "lynx"}}, headers=H).get_json()
    assert r["ok"] is False and "browser must be one of" in r["error"]
    assert config.load()["open_links"] == sent
    r = client.post("/api/config", json={"open_links": None}, headers=H).get_json()
    assert r["ok"] and r["config"]["open_links"] is None
    assert "open_links" not in config.load()


# ---------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------

def test_open_with_a_browser_named(client, path_bin, profiles, launched):
    exe = path_bin("google-chrome")
    r = client.post("/api/open", json={"url": URL, "browser": "chrome", "profile": "Profile 1", "private": True},
                    headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "browser": "chrome", "profile": "Profile 1", "private": True}
    assert [a for a, _ in launched] == [[exe, "--profile-directory=Profile 1", "--incognito", URL]]


def test_open_uses_the_saved_default(client, path_bin, profiles, launched):
    exe = path_bin("firefox")
    client.post("/api/config", json={"open_links": {"browser": "firefox", "profile": "work"}}, headers=H)
    r = client.post("/api/open", json={"url": URL}, headers=H)
    assert r.get_json()["ok"] and launched[-1][0] == [exe, "-P", "work", "--new-tab", URL]
    # private sent alone: the default's browser and profile, in a private window this once.
    client.post("/api/open", json={"url": URL, "private": True}, headers=H)
    assert launched[-1][0] == [exe, "-P", "work", "--private-window", URL]
    # A browser named: the default is not used at all.
    chromium = path_bin("chromium")
    client.post("/api/open", json={"url": URL, "browser": "chromium"}, headers=H)
    assert launched[-1][0] == [chromium, URL]


def test_open_without_a_default_or_a_browser(client, launched):
    r = client.post("/api/open", json={"url": URL}, headers=H)
    assert r.status_code == 400 and "this browser" in r.get_json()["error"]
    assert launched == []


@pytest.mark.parametrize("body, error", [
    ({"url": "javascript:alert(1)", "browser": "firefox"}, "http"),
    ({"url": "-P", "browser": "firefox"}, "http"),
    ({"url": URL, "browser": "firefox", "profile": "gone"}, 'no profile named "gone"'),
    ({"url": URL, "browser": "edge"}, "Microsoft Edge was not found"),
    ({"url": URL, "browser": "firefox", "private": "yes"}, "private must be"),
    ({"url": URL, "browser": "opera"}, "browser must be one of"),
])
def test_open_refusals_are_400s_with_a_message(client, path_bin, profiles, flatpak_roots, launched, body, error):
    path_bin("firefox")
    r = client.post("/api/open", json=body, headers=H)
    assert r.status_code == 400 and r.get_json()["ok"] is False and error in r.get_json()["error"]
    assert launched == []


def test_the_url_is_never_logged(client, path_bin, launched, capsys):
    path_bin("firefox")
    client.post("/api/open", json={"url": "https://secret.example/x", "browser": "firefox", "private": True},
                headers=H)
    out = capsys.readouterr().out
    assert "[open] a link in firefox, private" in out and "secret" not in out


def test_browsers_endpoint(client, path_bin, profiles, flatpak_roots):
    path_bin("brave")
    client.post("/api/config", json={"open_links": {"browser": "brave", "profile": "Default"}}, headers=H)
    r = client.get("/api/open/browsers", headers=H).get_json()
    assert r == {"browsers": [{"id": "brave", "name": "Brave", "flatpak": False, "profiles": ["Default", "Profile 1"]}],
                 "default": {"browser": "brave", "profile": "Default", "private": False}}


@pytest.mark.parametrize("headers", [{**H, "Origin": "https://evil.example"}, {**H, "Sec-Fetch-Site": "cross-site"},
                                     {"Origin": "http://localhost:3380"}])
def test_another_site_cannot_open_a_link(client, path_bin, launched, headers):
    path_bin("firefox")
    r = client.post("/api/open", json={"url": URL, "browser": "firefox"}, headers=headers)
    assert r.status_code == 403
    assert client.get("/api/open/browsers", headers=headers).status_code == 403
    assert launched == []
