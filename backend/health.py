"""Account health: how a source's syncs have been going, kept with its sync
state (the source's last_result, see sync._ended), never in its options.

- ``ok_at``: when its last sync that worked ended (kept across failures)
- ``health``: what the last run's output said (``ok``, ``error``, or a
  detected state: see sync.classify), kept by a run that never got that
  far (cancelled, interrupted)
- ``line``: the output line behind it, scrubbed (scrub())
- ``failures``: failed syncs in a row (sync._failures)

A last_result stored before any of this (or edited by hand in sources.json)
has none of these keys: what it lacks is null, a value that is not what it
should be counts as none.

Lines are scrubbed before they are stored or shown (scrub()): no cookie,
session id or token value, nor a path under a browser profile or a session
or cookie folder, ever reaches the database, sources.json or the page.
"""
import re

STATES = ("ok", "rate_limited", "private", "login_required", "not_found", "error")
LINE_MAX = 300


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

def record(previous, state, job_state, ended_at):
    """The health keys of a new last_result: {health, ok_at}. ``previous``:
    the source's last_result as stored (any value: sources.json can be
    edited by hand). ``state``: what the run's output said (STATES), None
    for a run that never got that far (cancelled, interrupted): it keeps
    the previous one."""
    prev = previous if isinstance(previous, dict) else {}
    ok_at = ended_at if job_state == "done" else _ok_at(prev, None)
    return {"health": state if state is not None else state_of(prev), "ok_at": ok_at}


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


def public(result, last_sync_at, failures):
    """A source's health for the API, from its stored last_result (None or
    malformed: nothing known, every key null)."""
    r = result if isinstance(result, dict) else {}
    state = state_of(r)
    return {"state": state, "result": r.get("state") if isinstance(r.get("state"), str) else None,
            "ok_at": _ok_at(r, last_sync_at), "last_sync_at": last_sync_at if _time(last_sync_at) else None,
            "line": scrub(r.get("line")) if state not in (None, "ok") else None, "failures": failures}
