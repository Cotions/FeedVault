"""Account health: what each tool's output says about a source's account.

Read from the lines a sync printed (stdout and stderr together, as jobs.py
keeps them), by fixed patterns, one table per tool. Each pattern names the
exact text it matches and where it was seen; output no pattern knows is an
``error`` with its line, never a guess.

States (first match wins, in this order: a throttled client also gets login
pages and missing profiles, so rate limiting comes first):

- ``rate_limited``: the site is limiting requests (the scheduler backs off)
- ``private``: a private profile the session does not follow
- ``login_required``: the site wants a logged-in session, or refused it
- ``not_found``: no such profile (deleted, renamed without a trace, banned)
- ``error``: none of the above; ``ok``: the sync worked
- ``renamed``: the sync worked, and the tool found the profile under a new
  handle (a suggestion for the user, see renamed())

instaloader 4.15.1, read from its installed source (instaloader.py,
instaloadercontext.py, __main__.py)::

    rate_limited    "JSON Query to graphql/query: 429 Too Many Requests [retrying; skip with ^C]"
                    (_response_error: "<status> <reason>")
                    "Please wait a few minutes before you try again." (Instagram's message, relayed)
    private         "<target>: Private but not followed."
                    "Profile <name>: private but not followed."
    login_required  "<target>: Login required."  /  "profile <name> requires login"
                    "Redirected to login page. Use --login or --load-cookies."
                    "Download aborted: Redirected to login page. You've been logged out, please
                     wait some time, recreate the session and try again."
                    "Session file does not exist yet - Logging in."
                    "Login error: …" (LoginException; "checkpoint_required", "challenge_required")
                    "No cookies found for Instagram in <browser>, Are you logged in successfully in <browser>?"
                    "Not logged in. Are you logged in successfully in <browser>?"
                    "… 403 Forbidden …": Instagram turning an anonymous client away; instaloader then
                     says the profile does not exist, so this comes before not_found
    not_found       "Profile <name> does not exist."
    renamed         "Profile <old> has changed its name to <new>." (check_profile_id, after "Trying to
                     find profile <old> using its unique ID <id>.": the profile id --latest-stamps keeps)

gallery-dl 1.32.14, read from its source (exception.py, job.py, output.py,
cookies.py, extractor/common.py, twitter.py, tiktok.py); lines are
``[<category>][<level>] <message>`` (output.LOG_FORMAT). An exception that
ends the run is logged ``[<category>][error] <Exception>: <message>``
(job.py), except AbortExtraction: its message alone::

    rate_limited    "[<category>][error] HttpError: '429 Too Many Requests' for '<url>'" (common.py,
                     once its retries are spent)
                    "[twitter][error] Rate limit exceeded" (AbortExtraction, with the user's
                     ratelimit=abort; else twitter waits: "[twitter][info] Waiting for …")
    private         "[twitter][error] AuthorizationError: <name>'s Tweets are protected"
    login_required  "[<category>][error] AuthRequired: authenticated cookies needed to access this
                     timeline" (AuthRequired: "<auth> needed to access this <resource>", or its
                     message alone: "NSFW Tweet")
                    "[<category>][error] AuthenticationError: …"
                    "[<category>][error] AuthorizationError: …" (any other: "HTTP redirect to login
                     page", "<name> blocked your account", "Account temporarily locked")
                    "[twitter][error] 'Could not authenticate you.'" (AbortExtraction: cookies refused)
                    "[tiktok][error] <url>: Login required to access this profile" (logged, then an
                     ExtractionError no pattern knows)
    not_found       "[<category>][error] NotFoundError: Requested user could not be found"
                    "[twitter][error] NotFoundError: <X's reason>" (UserUnavailable: "User is suspended")
                    "[tiktok][error] <url>: User account could not be found"
    renamed         nothing: gallery-dl never names a profile's new handle

yt-dlp 2026.08.19, read from its installed zipapp (networking/exceptions.py,
extractor/common.py, extractor/tiktok.py, extractor/youtube/_tab.py); lines
are ``ERROR: [<extractor>] <id>: <message>``. YouTube's own texts, which
yt-dlp relays as given, are marked (YouTube)::

    rate_limited    "HTTP Error 429: Too Many Requests"
    private         "This user's account is private. Log into an account that has access" (TikTok)
                    "This user's account is likely either private or all of their videos are private"
                    "Private video. Sign in if you've been granted access to this video" (YouTube)
    login_required  "Sign in to confirm you’re not a bot" (YouTube; ’ or ')
                    "Sign in to confirm your age" (YouTube)
                    "TikTok is requiring login for access to this content"
                    "Use --cookies-from-browser or --cookies for the authentication" (the hint every
                     raise_login_required adds)
    not_found       "The channel/playlist does not exist and the URL redirected to youtube.com home page"
                    "HTTP Error 404: Not Found"
                    "Video unavailable" (YouTube; one video: the Downloaders test item)
                    "YouTube said: This channel does not exist." (YouTube)
                    "YouTube said: This account has been terminated …" (YouTube)
    (none)          a TikTok user that does not exist: "Unable to extract secondary user ID. …",
                    which a private account with embedding off gives too: left an error
    renamed         nothing: a renamed YouTube handle or TikTok user is only not found

Login state (login()): whether the session the source's sync used was
there and whether the site took it, from the same output, never from a
request of FeedVault's::

    instaloader --login         "Loaded session from <path>." (found), "Session file does not exist
                                 yet - Logging in." (missing), "Logged in as <user>." (accepted: it
                                 tests the session before printing it)
    instaloader --load-cookies  "Cookies loaded successfully from <browser>" (found), "No cookies
                                 found for Instagram in <browser>, …" (missing), "<user> has been
                                 successfully logged in." (accepted), "Not logged in. Are you logged
                                 in successfully in <browser>?" (refused)
    instaloader, either         "Redirected to login page. You've been logged out, …" (refused)
    gallery-dl (as above)       "[cookies][info] Extracted <n> cookies from <Browser>" (found; 0: missing,
                                 as "[<category>][warning] cookies: Unable to find <Browser> cookies
                                 database"), "[twitter][error] 'Could not authenticate you.'" (refused)
    yt-dlp                      "Extracted <n> cookies from <browser>" (found; 0: missing),
                                "could not find <browser> cookies database …", "failed to load
                                 cookies" (missing)

Then, whatever the tool: a run that ends ``login_required`` with a session
was refused; one that ends otherwise (ok, renamed, private, not_found) with
the session found had it accepted. Nothing seen: unknown (null).

Errors about one video or file of a profile (a private video in a channel)
are not the profile's: sync._item_errors sets them apart first.

Kept on the source with its sync state (its last_result, see sync._ended),
never in its options:

- ``ok_at``: when its last sync that worked ended (kept across failures)
- ``health``: what the last run's output said (``ok``, ``error``, or a
  detected state: see classify()), kept by a run that never got that
  far (cancelled, interrupted)
- ``line``: the output line behind it, scrubbed (scrub())
- ``login``: {mode, found, accepted} of the session the last run used
  (login()), kept by a run that never got that far
- ``blocking``: blocking results (BLOCKING) in a row, whichever; absent
  once a run says anything else (a run that never got that far keeps it).
  A record from before it with a blocking state counts as 1 (blocking())
- ``resumed``: true once the user changed the schedule (or accepted a
  rename) after a blocking state: the scheduler tries it again (paused())
- ``rename``: {from, to, at}, a new handle the tool reported for the
  target ``from``; kept until the user accepts it (the target changes,
  never the folder) or dismisses it, or the target changes otherwise
- ``failures``: failed syncs in a row (sync._failures)

A last_result stored before any of this (or edited by hand in sources.json)
has none of these keys: what it lacks is null, a value that is not what it
should be counts as none.

Lines are scrubbed before they are stored or shown (scrub()): no cookie,
session id or token value, nor a path under a browser profile or a session
or cookie folder, ever reaches the database, sources.json or the page.
"""
import re

STATES = ("ok", "renamed", "rate_limited", "private", "login_required", "not_found", "error")
LINE_MAX = 300


def _table(rows):
    return [(state, re.compile(pattern, re.I)) for state, pattern in rows]


INSTALOADER = _table([
    ("rate_limited", r"\b429 Too Many Requests\b"),
    ("rate_limited", r"Please wait a few minutes before you try again"),
    ("private", r"\bprivate but not followed\b"),
    ("login_required", r"\bLogin required\.|\bprofile \S+ requires login\b"),
    ("login_required", r"Redirected to login page\."),
    ("login_required", r"Session file does not exist yet - Logging in\."),
    ("login_required", r"^Login error: |checkpoint_required|challenge_required"),
    ("login_required", r"No cookies found for Instagram in |Not logged in\. Are you logged in successfully in "),
    ("login_required", r"\b403 Forbidden\b"),
    ("not_found", r"\bProfile \S+ does not exist\."),
])
GALLERY_DL = _table([
    ("rate_limited", r"\]\[error\] HttpError: '429 Too Many Requests'"),
    ("rate_limited", r"^\[twitter\]\[error\] Rate limit exceeded\b"),
    ("private", r"\]\[error\] AuthorizationError: .*'s Tweets are protected"),
    ("login_required", r"\]\[error\] (?:AuthRequired|AuthenticationError|AuthorizationError): "),
    ("login_required", r"^\[twitter\]\[error\] 'Could not authenticate you"),
    ("login_required", r"^\[tiktok\]\[error\] \S+: Login required to access this profile\b"),
    ("not_found", r"\]\[error\] NotFoundError: "),
    ("not_found", r"^\[tiktok\]\[error\] \S+: User account could not be found$"),
])
YT_DLP = _table([
    ("rate_limited", r"HTTP Error 429: Too Many Requests"),
    ("private", r"This user's account is (?:likely either )?private"),
    ("private", r"\bPrivate video\b"),
    ("login_required", r"Sign in to confirm (?:you[’']re not a bot|your age)"),
    ("login_required", r"TikTok is requiring login for access to this content"),
    ("login_required", r"Use --cookies-from-browser or --cookies for the authentication"),
    ("not_found", r"The channel/playlist does not exist and the URL redirected to youtube\.com home page"),
    ("not_found", r"HTTP Error 404: Not Found"),
    ("not_found", r"\bVideo unavailable\b"),
    ("not_found", r"\bVideo not available, status code \d+"),     # TikTok: removed, or never was
    ("not_found", r"YouTube said: This (?:channel does not exist|account has been terminated)"),
])
TABLES = {"instaloader": INSTALOADER, "gallery-dl": GALLERY_DL, "yt-dlp": YT_DLP}
# instaloader's heading of the errors it repeats as it ends: never the line that says what went wrong.
CLOSING = "Errors or warnings occurred:"


# A new handle, as the tool names it; only instaloader does.
RENAMED = {"instaloader": re.compile(r"^Profile ([A-Za-z0-9._]{1,30}) has changed its name to ([A-Za-z0-9._]{1,30})\.$")}
# instaloader's other line of the same lookup, when the name did not change.
_SAME_NAME = re.compile(r"^Warning: Profile \S+ could not be retrieved by its name, but by its ID\.$")
_HANDLE = re.compile(r"[a-z0-9._]{1,30}")


# Login signals per tool: (what it says, pattern), matched on whole lines.
LOGIN = {
    "instaloader": _table([
        ("found", r"^Loaded session from .+\.$"),
        ("found", r"^Cookies loaded successfully from \w+$"),
        ("missing", r"^Session file does not exist yet - Logging in\.$"),
        ("missing", r"^(?:Login error: )?No cookies found for Instagram in "),
        ("accepted", r"^Logged in as [A-Za-z0-9._]+\.$"),
        ("accepted", r"^[A-Za-z0-9._]+ has been successfully logged in\.$"),
        ("refused", r"Not logged in\. Are you logged in successfully in "),
        ("refused", r"Redirected to login page\. You've been logged out"),
    ]),
    "gallery-dl": _table([
        ("found", r"^\[cookies\]\[info\] Extracted [1-9]\d* cookies from "),
        ("missing", r"^\[cookies\]\[info\] Extracted 0 cookies from "),
        ("missing", r"^\[[\w:-]+\]\[warning\] cookies: Unable to find \w+ cookies database"),
        ("refused", r"^\[twitter\]\[error\] 'Could not authenticate you"),
    ]),
    "yt-dlp": _table([
        ("found", r"^Extracted [1-9]\d* cookies from "),
        ("missing", r"^Extracted 0 cookies from "),
        ("missing", r"could not find \w+ cookies database|failed to load cookies"),
    ]),
}
SESSION_MODES = ("none", "cookies", "login")
# The scheduler stops syncing a source in one of these (scheduler.py), and
# the Creators list warns about it: syncing again on its own will not fix them.
# instaloader says both to a throttled anonymous client too ("does not exist",
# "403 Forbidden"), so one alone only backs off: see paused().
BLOCKING = {"not_found": "account not found", "login_required": "login required"}
STOP_AFTER = 2                                 # blocking results in a row before the scheduler stops
WARN_FAILURES = 3                              # failed syncs in a row before the Creators list warns


def _texts(lines):
    return [t.strip() for _, t in lines if t.strip() and not t.startswith("[feedvault]") and t.strip() != CLOSING]


def classify(lines, table):
    """(state, line): the first state of ``table`` whose pattern a line
    matches (the latest such line), else ("error", the last line). The
    line is raw: scrub() it before storing it."""
    texts = _texts(lines)
    for state in dict.fromkeys(st for st, _ in table):
        patterns = [p for st, p in table if st == state]
        for t in reversed(texts):
            if any(p.search(t) for p in patterns):
                return state, t
    return "error", texts[-1] if texts else None


def renamed(tool, lines):
    """(old, new) handles when the tool reported the profile's new name
    (lowercase, as instaloader keeps names), else None."""
    pattern = RENAMED.get(tool)
    for t in reversed(_texts(lines)) if pattern else ():
        m = pattern.search(t)
        if m and m.group(1).lower() != m.group(2).lower():
            return m.group(1).lower(), m.group(2).lower()
    return None


def only_renamed(tool, lines):
    """Whether instaloader's closing list of errors holds nothing but the
    rename lookup: it exits 1 for it, although the profile was downloaded."""
    if tool != "instaloader":
        return False
    texts = [t.strip() for _, t in lines if t.strip() and not t.startswith("[feedvault]")]
    if CLOSING not in texts:
        return False
    after = texts[len(texts) - texts[::-1].index(CLOSING):]
    return bool(after) and all(RENAMED["instaloader"].search(t) or _SAME_NAME.search(t) for t in after)


def login(tool, lines, session, state):
    """{mode, found, accepted} for the session a sync used (``session``:
    sync.session_of's): found (the session file or the browser's cookies
    were there), accepted (the site took them); each true, false or None
    when the output does not say. Mode "none": both None. A not_found run
    says nothing of the session: the site answers it to a throttled or
    logged-out client too, so only a line saying so marks it accepted."""
    mode = session.get("mode") if isinstance(session, dict) else None
    out = {"mode": mode if mode in SESSION_MODES else "none", "found": None, "accepted": None}
    if out["mode"] == "none":
        return out
    seen = {signal for t in _texts(lines) for signal, pattern in LOGIN.get(tool, ()) if pattern.search(t)}
    if "missing" in seen and not seen & {"found", "accepted"}:
        return {**out, "found": False, "accepted": False}
    if seen & {"found", "accepted", "refused"}:
        out["found"] = True
    if "refused" in seen or state == "login_required":
        out["accepted"] = False
    elif "accepted" in seen or (out["found"] and state in ("ok", "renamed", "private")):
        out["accepted"] = True
    return out


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Control and invisible characters (bidi overrides, zero-width): a line is
# shown as text, never as anything that reorders or hides it.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")
_COOKIE_HEADER = re.compile(r"(?i)\b(set-cookie|cookie)\s*:\s*.*")
_SECRET_NAMES = (r"cookies?|sessionid|session[_-]?id|sessid|sid|csrftoken|csrf[_-]?token|x-csrftoken|ds_user_id|"
                 r"auth[_-]?token|ct0|twid|kdt|ttwid|mstoken|odin_tt|sid_tt|sid_guard|uid_tt|sessionid_ss|"
                 r"access[_-]?token|refresh[_-]?token|id[_-]?token|token|api[_-]?key|secret|password|passwd|"
                 r"authorization|sapisid|apisid|hsid|ssid|login_info|__secure-[\w-]+|rur|mid|ig_did|shbid|shbts")
_SECRET_VALUE = re.compile(rf"(?i)(?<![\w-])({_SECRET_NAMES})(\"?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s;,&'\"]+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
# Opaque strings long enough to be a token (hex, base64, JWT): not kept.
_OPAQUE = re.compile(r"(?<![A-Za-z0-9._~+/-])[A-Za-z0-9_~+/-]{40,}={0,2}(?:\.[A-Za-z0-9_~+/-]{10,}={0,2})*")
# Folder names with a space that browsers keep profiles under: read as part
# of the path, or its tail would be left after the space.
_SPACED = (r"(?<=[/\\])(?i:application support|user data|local state|login data|local storage|session storage|"
           r"network persistent state|google chrome(?: beta| canary| dev)?|microsoft edge(?: beta| dev)?|"
           r"brave browser|opera software|opera gx stable|profile \d+)(?=[/\\'\"\s]|$)")
_PATH = re.compile(rf"(?:(?<![A-Za-z0-9._~+/-])~|\b[A-Za-z]:\\|(?<![\w.:/+])/)(?:{_SPACED}|[^\s'\"<>|])*")
# A path segment of a token's characters only, mixing upper and lower case
# and digits: a base64 token's piece between two "/", not a folder's name.
_TOKEN_PART = re.compile(r"(?=[^/]*[A-Z])(?=[^/]*[a-z])(?=[^/]*[0-9])[A-Za-z0-9_~+=-]{24,}")
# Where browsers keep cookies and the tools their sessions: a path naming one
# of these is dropped whole. A name is a whole word: "Cooperative" names no
# Opera, "Sessions2024"'s photo shoots are a folder of sessions all the same.
_PRIVATE_PATH = re.compile(r"(?i)(?<![a-z])(?:cookie(?:s|jars?)?|sessions?|firefox|librewolf|chrom(?:e|ium)|"
                           r"bravesoftware|microsoft-edge|opera|vivaldi|keyring|kwallet|local state|login data)"
                           r"(?![a-z])|\.mozilla|/\.config/instaloader|instaloader/|gallery-dl/|"
                           r"\.cache/gallery-dl|\.netrc")


def _path(m):
    """A path dropped whole when private, else kept with only a segment
    long and opaque enough to be a token replaced (or a token's piece,
    _TOKEN_PART): a media folder's path runs past 40 characters with no
    dot, and a log names one per file."""
    p = m.group(0)
    if _PRIVATE_PATH.search(p):
        return "<private path>"
    return "/".join("…" if _TOKEN_PART.fullmatch(s) else _OPAQUE.sub("…", s) for s in p.split("/"))


def _opaque(t):
    """_OPAQUE replaced outside paths, _path() in them."""
    out, at = [], 0
    for m in _PATH.finditer(t):
        out += [_OPAQUE.sub("…", t[at:m.start()]), _path(m)]
        at = m.end()
    return "".join(out) + _OPAQUE.sub("…", t[at:])


def scrub(text, limit=LINE_MAX):
    """One line of tool output made safe to store and show: escape codes
    and control characters gone, cookie and token values, Authorization
    headers and long opaque strings replaced by "…", paths under a browser
    profile or a session or cookie folder by "<private path>", spaces
    collapsed, at most ``limit`` characters. None for nothing left."""
    if not isinstance(text, str):
        return None
    t = _ANSI.sub("", text)
    t = _CONTROL.sub(" ", t)
    t = _COOKIE_HEADER.sub(lambda m: f"{m.group(1)}: …", t)
    t = _BEARER.sub(lambda m: f"{m.group(1)} …", t)
    t = _SECRET_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}…", t)
    t = _opaque(t)
    t = " ".join(t.split())
    if len(t) > limit:
        t = t[:limit - 1].rstrip() + "…"
    return t or None


# ---------------------------------------------------------------------------
# The record kept on a source (in last_result, with its sync state)
# ---------------------------------------------------------------------------

def record(previous, state, job_state, ended_at, target, rename=None, login_state=None):
    """The health keys of a new last_result: {health, ok_at, login, rename}.
    ``previous``: the source's last_result as stored (any value:
    sources.json can be edited by hand). ``state``: what the run's output
    said (STATES), None for a run that never got that far (cancelled,
    interrupted): it keeps the previous one. ``rename``: (old, new) from
    renamed(); a suggestion stays while the target is the one it was for.
    ``login_state``: login()'s, None for a run that never got that far.
    Such a run also keeps ``resumed`` and ``blocking``: it said nothing new
    about the account. A blocking state adds one to ``blocking``, counted
    from none after a resume (a new schedule or session)."""
    prev = previous if isinstance(previous, dict) else {}
    ok_at = ended_at if job_state == "done" else _ok_at(prev, None)
    suggestion = _rename(prev.get("rename"))
    if rename and rename[0] == str(target).lower():
        suggestion = {"from": rename[0], "to": rename[1], "at": ended_at}
    if suggestion and suggestion["from"] != str(target).lower():
        suggestion = None
    out = {"health": state if state is not None else state_of(prev), "ok_at": ok_at,
           "login": _login(login_state) or _login(prev.get("login"))}
    if state is None and prev.get("resumed") is True:
        out["resumed"] = True
    before = 0 if state is not None and prev.get("resumed") is True else blocking(prev)
    if state in BLOCKING or (state is None and before):
        out["blocking"] = before + (state in BLOCKING)
    return {**out, "rename": suggestion} if suggestion else out


def _login(v):
    """A stored login state, checked again, else None."""
    if not isinstance(v, dict) or v.get("mode") not in SESSION_MODES:
        return None
    return {"mode": v["mode"], **{k: v[k] if isinstance(v.get(k), bool) else None for k in ("found", "accepted")}}


def _rename(v):
    """A stored rename suggestion, checked again (two handles), else None."""
    if not isinstance(v, dict) or not all(isinstance(v.get(k), str) and _HANDLE.fullmatch(v[k]) for k in ("from", "to")):
        return None
    return {"from": v["from"], "to": v["to"], "at": v["at"] if _time(v.get("at")) else None}


def _time(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _ok_at(result, last_sync_at):
    """When the last sync that worked ended: stored, else (a last_result
    from before) the last sync's end when it worked."""
    if _time(result.get("ok_at")):
        return result["ok_at"]
    return last_sync_at if result.get("state") == "done" and _time(last_sync_at) else None


def state_of(result):
    """The health state a stored last_result says, or None: its ``health``
    when there is one, else read from what older syncs stored (``error``)."""
    if not isinstance(result, dict):
        return None
    if result.get("health") in STATES:
        return result["health"]
    if result.get("state") == "done":
        return "ok"
    error = result.get("error")
    if error in STATES:
        return error
    return "error" if result.get("state") == "failed" else None


def blocking(result):
    """Blocking results in a row in a stored last_result: its ``blocking``,
    else (stored before it) 1 when its state is BLOCKING, else 0."""
    if not isinstance(result, dict) or result.get("health") not in BLOCKING:
        return 0
    n = result.get("blocking")
    return n if _time(n) and n > 0 else 1


def paused(result):
    """Why the scheduler leaves a source alone ("account not found", "login
    required"), else None: its last state is BLOCKING, said STOP_AFTER
    times in a row or once with a session the site accepted (login.accepted:
    the profile really is gone for a logged-in viewer), and nothing resumed
    it since (a sync's end stores a new result, without ``resumed``). A
    lone one with no accepted session is the back-off's, as rate_limited:
    instaloader says the same to a throttled anonymous client. Only a state
    these tables read (``health``) stops it: an ``error`` stored before them
    came from broader patterns, and keeps the back-off."""
    if not isinstance(result, dict) or result.get("resumed") is True:
        return None
    why = BLOCKING.get(result.get("health"))
    accepted = (_login(result.get("login")) or {}).get("accepted") is True
    return why if why and (blocking(result) >= STOP_AFTER or accepted) else None


def warning(result, failures):
    """Why the Creators list warns about a source, else None: what stops
    the scheduler (paused(), also when its schedule is off), so a lone
    blocking result, which only backs off, does not; or WARN_FAILURES
    failed syncs in a row."""
    why = paused(result)
    if why:
        return why
    return f"{failures} failed syncs in a row" if failures >= WARN_FAILURES else None


def public(result, last_sync_at, failures, target=None):
    """A source's health for the API, from its stored last_result (None or
    malformed: nothing known, every key null)."""
    r = result if isinstance(result, dict) else {}
    state = state_of(r)
    return {"state": state, "result": r.get("state") if isinstance(r.get("state"), str) else None,
            "ok_at": _ok_at(r, last_sync_at), "last_sync_at": last_sync_at if _time(last_sync_at) else None,
            "line": scrub(r.get("line")) if state not in (None, "ok", "renamed") else None, "failures": failures,
            "rename": _suggested(r.get("rename"), target), "login": _login(r.get("login")),
            "paused": paused(r), "warning": warning(r, failures)}


def _suggested(v, target):
    """The stored suggestion while it is for the source's target."""
    suggestion = _rename(v)
    return suggestion if suggestion and suggestion["from"] == str(target).lower() else None
