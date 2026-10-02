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

gallery-dl: not installed where this was written; its log format
``[<category>][error] <Exception>: <message>`` and the exception names are
those of gallery-dl 1.30 (gallery_dl/exception.py), the messages those its
twitter extractor raises::

    rate_limited    "[twitter][error] HttpError: '429 Too Many Requests' for '<url>'"
    private         "[twitter][error] AuthorizationError: <name>'s Tweets are protected"
    login_required  "[<category>][error] AuthRequired: '<what>' needed …"
                    "[<category>][error] AuthenticationError: …"
                    "[<category>][error] AuthorizationError: …" (any other)
    not_found       "[<category>][error] NotFoundError: Requested user could not be found"
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

Errors about one video or file of a profile (a private video in a channel)
are not the profile's: sync._item_errors sets them apart first.

Kept on the source with its sync state (its last_result, see sync._ended),
never in its options:

- ``ok_at``: when its last sync that worked ended (kept across failures)
- ``health``: what the last run's output said (``ok``, ``error``, or a
  detected state: see classify()), kept by a run that never got that
  far (cancelled, interrupted)
- ``line``: the output line behind it, scrubbed (scrub())
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
    ("private", r"\]\[error\] AuthorizationError: .*'s Tweets are protected"),
    ("login_required", r"\]\[error\] (?:AuthRequired|AuthenticationError|AuthorizationError): "),
    ("not_found", r"\]\[error\] NotFoundError: "),
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
_PATH = re.compile(r"(?:~|\b[A-Za-z]:\\|(?<![\w.:/])/)[^\s'\"<>|]*")
# Where browsers keep cookies and the tools their sessions: a path naming one
# of these is dropped whole.
_PRIVATE_PATH = re.compile(r"(?i)cookie|session|\.mozilla|firefox|librewolf|chrom(?:e|ium)|bravesoftware|"
                           r"microsoft-edge|opera|vivaldi|/\.config/instaloader|instaloader/|gallery-dl/|"
                           r"\.cache/gallery-dl|keyring|kwallet|local state|login data|\.netrc")


def _path(m):
    p = m.group(0)
    return "<private path>" if _PRIVATE_PATH.search(p) else p


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
    t = _PATH.sub(_path, t)
    t = _OPAQUE.sub("…", t)
    t = " ".join(t.split())
    if len(t) > limit:
        t = t[:limit - 1].rstrip() + "…"
    return t or None


# ---------------------------------------------------------------------------
# The record kept on a source (in last_result, with its sync state)
# ---------------------------------------------------------------------------

def record(previous, state, job_state, ended_at, target, rename=None):
    """The health keys of a new last_result: {health, ok_at, rename}.
    ``previous``: the source's last_result as stored (any value:
    sources.json can be edited by hand). ``state``: what the run's output
    said (STATES), None for a run that never got that far (cancelled,
    interrupted): it keeps the previous one. ``rename``: (old, new) from
    renamed(); a suggestion stays while the target is the one it was for."""
    prev = previous if isinstance(previous, dict) else {}
    ok_at = ended_at if job_state == "done" else _ok_at(prev, None)
    suggestion = _rename(prev.get("rename"))
    if rename and rename[0] == str(target).lower():
        suggestion = {"from": rename[0], "to": rename[1], "at": ended_at}
    if suggestion and suggestion["from"] != str(target).lower():
        suggestion = None
    out = {"health": state if state is not None else state_of(prev), "ok_at": ok_at}
    return {**out, "rename": suggestion} if suggestion else out


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


def public(result, last_sync_at, failures, target=None):
    """A source's health for the API, from its stored last_result (None or
    malformed: nothing known, every key null)."""
    r = result if isinstance(result, dict) else {}
    state = state_of(r)
    return {"state": state, "result": r.get("state") if isinstance(r.get("state"), str) else None,
            "ok_at": _ok_at(r, last_sync_at), "last_sync_at": last_sync_at if _time(last_sync_at) else None,
            "line": scrub(r.get("line")) if state not in (None, "ok", "renamed") else None, "failures": failures,
            "rename": _suggested(r.get("rename"), target)}


def _suggested(v, target):
    """The stored suggestion while it is for the source's target."""
    suggestion = _rename(v)
    return suggestion if suggestion and suggestion["from"] == str(target).lower() else None
