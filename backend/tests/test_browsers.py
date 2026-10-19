"""Browser profiles: names, profiles.ini, the folder each tool is given.

HOME is the test's own (conftest.tool_guard): every profiles.ini here is
written by the test, and no cookie database exists anywhere.
"""
import os

import pytest

from conftest import H
from fakes import chromium_profile, firefox_profiles

import app as app_module
import browsers
import jobs
import sources
import sync


def home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


@pytest.fixture
def firefox():
    """The owner's layout: four profiles, and a leftover ``.test`` folder no profile uses."""
    root = home(".mozilla", "firefox")
    folders = firefox_profiles(root, [("cotions", "ab12cd34.cotions"), ("default", "ef56gh78.default"),
                                      ("test", "9k879us5.test"), ("TRND", "zz99yy88.TRND")])
    os.makedirs(os.path.join(root, "itxwkwwt.test"))
    return folders


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["test", "TRND", "default-release", "Work profile", "été", "a" * 100, "a:b", "x.y"])
def test_firefox_names(name):
    assert browsers.clean_name("firefox", name) == name
    assert sources.clean_session({"mode": "cookies", "browser": "firefox", "profile": name}) == \
        {"mode": "cookies", "browser": "firefox", "profile": name}


@pytest.mark.parametrize("name", ["", " ", " test", "test ", "a/b", "a\\b", "/abs", "..\\x", "../x", "-test",
                                  "--exec=id", "a\x00b", "a\nb", "a\tb", "a\x7fb", "a\x1b[0m", "a" * 101,
                                  5, True, [], {}, ["test"]])
@pytest.mark.parametrize("browser", ["firefox", "chrome"])
def test_bad_names(browser, name):
    assert browsers.clean_name(browser, name) is None
    assert sources.clean_session({"mode": "cookies", "browser": browser, "profile": name}) is None


@pytest.mark.parametrize("name", ["Default", "Profile 1", "Guest Profile", "work_2.old-x"])
def test_chromium_names(name):
    for browser in ("chrome", "chromium", "brave", "edge"):
        assert browsers.clean_name(browser, name) == name


@pytest.mark.parametrize("name", [".", "..", ".hidden", "a:b", "a::b", ":x", "$HOME", "~", "~root", "a+b",
                                  "été", "a*", "a?", "a[1]", "%APPDATA%"])
def test_chromium_names_are_plain_folder_names(name):
    assert browsers.clean_name("chrome", name) is None
    assert sources.clean_session({"mode": "cookies", "browser": "brave", "profile": name}) is None


def test_session_profile_is_optional_and_for_cookies_only():
    plain = {"mode": "cookies", "browser": "firefox"}
    assert sources.clean_session(plain) == plain
    assert sources.clean_session({**plain, "profile": None}) == plain          # null: as absent
    assert sources.clean_session({**plain, "profile": "test", "path": "/x"}) is None
    assert sources.clean_session({"mode": "none", "profile": "test"}) is None
    assert sources.clean_session({"mode": "login", "user": "me", "profile": "test"}) is None
    assert sources.clean_session({"mode": "cookies", "profile": "test"}) is None
    assert sources.clean_session({**plain, "profile": "test"}, sources.COOKIE_MODES) == {**plain, "profile": "test"}


# ---------------------------------------------------------------------------
# profiles.ini
# ---------------------------------------------------------------------------

def test_no_firefox_at_all():
    assert browsers.listing() == {"firefox": [], "chrome": [], "chromium": [], "brave": [], "edge": []}
    with pytest.raises(browsers.Unknown, match='the firefox profile "test" was not found'):
        browsers.folder("firefox", "test")


def test_names_come_from_profiles_ini_not_from_folders(firefox):
    assert browsers.names("firefox") == ["cotions", "default", "test", "TRND"]
    # Two folders end in .test: the one profiles.ini names, never the leftover.
    assert browsers.folder("firefox", "test") == home(".mozilla", "firefox", "9k879us5.test") == firefox["test"]
    assert browsers.cookie_file("firefox", "test") == os.path.join(firefox["test"], "cookies.sqlite")
    assert not os.path.exists(browsers.cookie_file("firefox", "test"))      # a path only
    for name in ("itxwkwwt.test", "9k879us5.test", "Test", "tes", "trnd"):
        with pytest.raises(browsers.Unknown, match="was not found"):
            browsers.folder("firefox", name)


def test_relative_and_absolute_paths(tmp_path):
    elsewhere = tmp_path / "elsewhere" / "My Profile"
    firefox_profiles(home(".mozilla", "firefox"), [("in", "Profiles/aaaa.in"), ("out", str(elsewhere))])
    assert browsers.folder("firefox", "in") == home(".mozilla", "firefox", "Profiles", "aaaa.in")
    assert browsers.folder("firefox", "out") == str(elsewhere)
    assert browsers.names("firefox") == ["in", "out"]


def test_a_profile_whose_folder_is_gone(firefox):
    os.rmdir(firefox["test"])
    assert browsers.names("firefox") == ["cotions", "default", "TRND"]
    with pytest.raises(browsers.Unknown, match='the firefox profile "test" was not found'):
        browsers.folder("firefox", "test")
    # A file there is no profile folder either.
    open(firefox["test"], "w").close()
    with pytest.raises(browsers.Unknown):
        browsers.folder("firefox", "test")


@pytest.mark.parametrize("text", ["", "not an ini at all\n\x00\xff", "[Profile0]\nName=test\n",
                                  "[Profile0]\nPath=9k879us5.test\n", "Name=test\nPath=9k879us5.test\n",
                                  "[Other]\nName=test\nPath=9k879us5.test\n"])
def test_unreadable_profiles_ini(firefox, text):
    with open(home(".mozilla", "firefox", "profiles.ini"), "w", encoding="latin-1") as f:
        f.write(text)
    assert browsers.names("firefox") == []
    with pytest.raises(browsers.Unknown, match="was not found"):
        browsers.folder("firefox", "test")


def test_a_path_with_a_nul_is_no_profile(firefox):
    # os.path would raise on it: the profile is skipped, the others stay.
    with open(home(".mozilla", "firefox", "profiles.ini"), "a") as f:
        f.write("\n[Profile9]\nName=nul\nIsRelative=1\nPath=a\x00b\n"
                "\n[Profile10]\nName=absnul\nIsRelative=0\nPath=/tmp/a\x00b\n")
    assert browsers.names("firefox") == ["cotions", "default", "test", "TRND"]
    assert browsers.folder("firefox", "test") == firefox["test"]
    with pytest.raises(browsers.Unknown, match="was not found"):
        browsers.folder("firefox", "nul")


def test_profiles_ini_too_large_or_not_a_file(firefox):
    ini = home(".mozilla", "firefox", "profiles.ini")
    with open(ini, "a") as f:
        f.write("#" * (browsers.INI_MAX + 1))
    assert browsers.names("firefox") == []
    os.remove(ini)
    os.mkdir(ini)
    assert browsers.names("firefox") == []


@pytest.mark.parametrize("where", [("snap", "firefox", "common", ".mozilla", "firefox"),
                                   (".var", "app", "org.mozilla.firefox", ".mozilla", "firefox"),
                                   (".var", "app", "org.mozilla.firefox", "config", "mozilla", "firefox"),
                                   (".config", "mozilla", "firefox")])
def test_snap_flatpak_and_xdg_locations(where):
    folders = firefox_profiles(home(*where), [("test", "9k879us5.test")])
    assert browsers.names("firefox") == ["test"]
    assert browsers.folder("firefox", "test") == folders["test"] == home(*where, "9k879us5.test")


def test_a_name_two_installations_share_is_refused(firefox):
    firefox_profiles(home("snap", "firefox", "common", ".mozilla", "firefox"),
                     [("test", "qqqq1111.test"), ("snap-only", "rrrr2222.snap")])
    assert browsers.names("firefox") == ["cotions", "default", "snap-only", "test", "TRND"]
    with pytest.raises(browsers.Unknown, match='more than one Firefox profile is named "test"'):
        browsers.folder("firefox", "test")
    assert browsers.folder("firefox", "snap-only").endswith("rrrr2222.snap")
    assert browsers.folder("firefox", "TRND") == firefox["TRND"]


def test_one_folder_reached_twice_is_one_profile(firefox):
    # ~/.config/mozilla/firefox a symlink to ~/.mozilla/firefox: the same profiles, once.
    os.makedirs(home(".config", "mozilla"), exist_ok=True)
    os.symlink(home(".mozilla", "firefox"), home(".config", "mozilla", "firefox"))
    assert browsers.names("firefox") == ["cotions", "default", "test", "TRND"]
    assert os.path.realpath(browsers.folder("firefox", "test")) == os.path.realpath(firefox["test"])


@pytest.mark.parametrize("folder", ["a::b.test", "$HOME.test", "x*.test", "what?.test", "x[1].test", "a\x7fb.test"])
def test_a_folder_the_tools_would_misread_is_refused(folder):
    firefox_profiles(home(".mozilla", "firefox"), [("test", folder), ("ok", "a:b c+d.ok")])
    with pytest.raises(browsers.Unknown, match='the firefox profile "test" is in a folder the tools cannot be given'):
        browsers.folder("firefox", "test")
    assert browsers.folder("firefox", "ok") == home(".mozilla", "firefox", "a:b c+d.ok")


def test_dot_dot_in_a_path_is_normalized_and_must_exist():
    root = home(".mozilla", "firefox")
    firefox_profiles(root, [("up", "../../outside"), ("gone", "../../nowhere")], make=False)
    os.makedirs(home("outside"))
    assert browsers.folder("firefox", "up") == home("outside")           # Firefox's own reading of it
    with pytest.raises(browsers.Unknown):
        browsers.folder("firefox", "gone")


# ---------------------------------------------------------------------------
# Chrome-family
# ---------------------------------------------------------------------------

def test_chromium_profiles():
    chrome = home(".config", "google-chrome")
    for name in ("Default", "Profile 1", "Profile 10"):
        chromium_profile(chrome, name)
    os.makedirs(os.path.join(chrome, "Crashpad"))                           # no Preferences: no profile
    open(os.path.join(chrome, "Local State"), "w").close()
    chromium_profile(home(".config", "BraveSoftware", "Brave-Browser"), "Default")
    assert browsers.listing() == {"firefox": [], "chrome": ["Default", "Profile 1", "Profile 10"], "chromium": [],
                                  "brave": ["Default"], "edge": []}
    assert browsers.folder("chrome", "Profile 1") == os.path.join(chrome, "Profile 1")
    assert browsers.cookie_file("chrome", "Profile 1") == os.path.join(chrome, "Profile 1", "Cookies")
    for browser, name in (("chrome", "Profile 2"), ("chromium", "Default"), ("edge", "Default"),
                          ("chrome", "Local State")):
        with pytest.raises(browsers.Unknown, match=f'the {browser} profile "{name}" was not found'):
            browsers.folder(browser, name)
    with pytest.raises(browsers.Unknown, match="is not a chrome profile's name"):
        browsers.folder("chrome", "..")


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

def test_flags_without_a_profile_are_unchanged(firefox):
    session = {"mode": "cookies", "browser": "firefox"}
    assert sync.session_flags(session) == ["--load-cookies", "firefox"]
    assert sync.cookie_flags(session) == ["--cookies-from-browser", "firefox"]
    assert sync.session_flags({"mode": "none"}) == sync.cookie_flags({"mode": "none"}) == []
    assert sync.session_flags({"mode": "login", "user": "me"}) == ["--login", "me"]


def test_flags_with_a_profile(firefox):
    session = {"mode": "cookies", "browser": "firefox", "profile": "test"}
    assert sync.session_flags(session) == ["--load-cookies", "firefox", "--cookiefile",
                                           os.path.join(firefox["test"], "cookies.sqlite")]
    assert sync.cookie_flags(session) == ["--cookies-from-browser", "firefox:" + firefox["test"]]
    chrome = chromium_profile(home(".config", "chromium"), "Profile 1")
    session = {"mode": "cookies", "browser": "chromium", "profile": "Profile 1"}
    assert sync.session_flags(session) == ["--load-cookies", "chromium", "--cookiefile", os.path.join(chrome, "Cookies")]
    assert sync.cookie_flags(session) == ["--cookies-from-browser", "chromium:" + chrome]


def test_flags_refuse_a_profile_that_is_gone(firefox):
    session = {"mode": "cookies", "browser": "firefox", "profile": "work"}
    for flags in (sync.session_flags, sync.cookie_flags):
        with pytest.raises(jobs.BadRequest, match='the firefox profile "work" was not found'):
            flags(session)


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------

def test_listing_endpoint_has_names_only(firefox, client):
    chromium_profile(home(".config", "microsoft-edge"), "Default")
    r = client.get("/api/browsers/profiles", headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"profiles": {"firefox": ["cotions", "default", "test", "TRND"], "chrome": [],
                                         "chromium": [], "brave": [], "edge": ["Default"]}}
    assert "9k879us5" not in r.get_data(as_text=True) and os.path.expanduser("~") not in r.get_data(as_text=True)


def test_settings_take_a_profile(firefox, client):
    def save(body):
        return client.post("/api/config", json=body, headers=H).get_json()

    session = {"mode": "cookies", "browser": "firefox", "profile": "test"}
    for tool in ("instaloader", "gallery-dl", "yt-dlp"):
        assert save({tool: {"session": session}})["config"][tool]["session"] == session
        r = save({tool: {"session": {**session, "profile": "../test"}}})
        assert r["ok"] is False and '"profile": "<a browser profile\'s name, optional>"' in r["error"]
        # Saved whether or not the browser has it now: found when a command is built.
        assert save({tool: {"session": {**session, "profile": "later"}}})["ok"] is True
        assert save({tool: {"session": {**session, "profile": None}}})["config"][tool]["session"] == \
            {"mode": "cookies", "browser": "firefox"}
    assert save({"gallery-dl": {"session": {"mode": "cookies", "browser": "chrome", "profile": "a:b"}}})["ok"] is False


# ---------------------------------------------------------------------------
# Kept to the dashboard, and out of what a job shows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv, shown", [
    (["yt-dlp", "--cookies-from-browser", "firefox:/home/u/.mozilla/firefox/9k879us5.test", "--", "url"],
     ["yt-dlp", "--cookies-from-browser", "firefox:<browser profile>", "--", "url"]),
    (["instaloader", "--load-cookies", "chrome", "--cookiefile", "/home/u/.config/google-chrome/Profile 1/Cookies"],
     ["instaloader", "--load-cookies", "chrome", "--cookiefile", "<browser profile>"]),
    # No profile: as it is, a keyring or a container too.
    (["gallery-dl", "--cookies-from-browser", "firefox", "--", "url"], None),
    (["gallery-dl", "--cookies-from-browser", "chrome+gnomekeyring", "--cookies-from-browser"], None),
    (["instaloader", "--load-cookies", "firefox", "--", "--cookiefile"], None),
    (["--cookiefile"], None), ([], None),
])
def test_a_job_is_shown_without_the_folder(argv, shown):
    before = list(argv)
    assert browsers.shown(argv) == (argv if shown is None else shown)
    assert argv == before                       # the list that runs is left as it is
    assert jobs._shown_argv("no such kind", argv) == browsers.shown(argv)


FOREIGN = {**H, "Origin": "https://www.instagram.com"}


def test_another_site_cannot_name_a_profile(firefox, env, client):
    """The userscript adds sources (POST /api/sources is its): not with a
    browser profile, which would say whether one exists and pick an account."""
    assert client.get("/api/browsers/profiles", headers=FOREIGN).status_code == 403
    body = {"target": "https://x.com/someone", "folder": str(env["media"] / "x")}
    cookies = {"mode": "cookies", "browser": "firefox"}
    for profile in ("test", "nope", "../x", 5):
        r = client.post("/api/sources", json={**body, "options": {"session": {**cookies, "profile": profile}}},
                        headers=FOREIGN)
        assert r.status_code == 403 and r.get_json() == {"ok": False, "error": app_module.FOREIGN_REFUSED}
    # Nor any login: a browser's cookies without a profile, a saved session.
    for session in (cookies, {**cookies, "profile": None}, {"mode": "login", "user": "me"}):
        r = client.post("/api/sources", json={**body, "options": {"session": session}}, headers=FOREIGN)
        assert r.status_code == 403 and r.get_json() == {"ok": False, "error": app_module.FOREIGN_REFUSED}
    assert client.get("/api/sources", headers=H).get_json()["sources"] == []
    # No login it adds as before, and the dashboard may name a profile.
    for headers, session in ((FOREIGN, {"mode": "none"}), (FOREIGN, None), (H, {**cookies, "profile": "test"})):
        r = client.post("/api/sources", json={**body, "options": {"session": session}}, headers=headers)
        assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_json()
        sid = r.get_json()["source"]["id"]
        assert client.delete(f"/api/sources/{sid}", headers=H).status_code == 200
