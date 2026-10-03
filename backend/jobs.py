"""Jobs: command-line tools started from the dashboard, with a live log.

The API never carries a command. A request names a *kind* and its
parameters; the kind, registered here in code, checks the parameters and
builds the argument list. It runs with Popen(argv), never a shell, so a
parameter is always exactly one argument whatever characters it holds.
Executables are found on PATH, or at the path set for the tool in Settings
(config.json ``tools``).

One job runs at a time per lock group (instaloader must never run two
sessions at once), different groups in parallel, at most MAX_RUNNING in all.
Each job runs in a session of its own, so cancelling (SIGTERM, then SIGKILL)
reaches everything it started. When a job that downloads into a media root
exits cleanly, that folder is indexed and the job reports its new posts.

Jobs are kept in the ``jobs`` table: the queue, and the last HISTORY_KEPT
ended jobs with the tail of their output. Output is untrusted text: each
line is scrubbed (health.scrub: no cookie, token or session path) as it is
read, before it is kept, shown or stored; only a kind's hooks see it as the
tool printed it (they parse it), and what they return is theirs to scrub. Jobs a stopped FeedVault left
queued or running are marked interrupted on the next start (recover), and a
process a killed FeedVault left running is stopped, when its pid, start time
and executable all still match.
"""
import collections
import json
import os
import shutil
import signal
import subprocess
import threading
import time

import config
import db
import health
import scanner

TOOLS = ("instaloader", "gallery-dl", "yt-dlp", "ffmpeg")
MAX_RUNNING = 2
LOG_LINES = 5000                               # live log kept per running job
LINE_MAX = 4096                                # bytes; the rest of a longer line is dropped
TAIL_KEPT = 200                                # lines kept once a job has ended
HISTORY_KEPT = 100
KILL_AFTER = 10                                # seconds from SIGTERM to SIGKILL
LOG_PAGE = 1000                                # lines per log request

STATES = ("queued", "running", "done", "failed", "cancelled", "interrupted")
INTERRUPTED = "FeedVault stopped while it ran"


class BadRequest(ValueError):
    """Unknown kind or bad parameters: the API answers 400."""


class Cancelled(Exception):
    """Raised by a kind's start: there is nothing left to run (its source is
    gone). The job ends cancelled with this message, also noted in its log."""


class Kind:
    def __init__(self, name, label, params, build, group, summarize=None, start=None, outcome=None,
                 ended=None, pause=None, describe=None, after=None):
        self.name, self.label, self.params = name, label, params
        self.build, self.group, self.summarize = build, group, summarize
        self.start, self.outcome, self.ended = start, outcome, ended
        self.pause, self.describe, self.after = pause, describe, after


_kinds = {}


def register(name, *, label, params, build, group, summarize=None, start=None, outcome=None, ended=None,
             pause=None, describe=None, after=None):
    """Add a job kind.

    params:    {name: {"type": "choice", "choices": [...]}
                     | {"type": "text", "max": 500}}, each "required"
               unless it says "required": False
    build:     checked params -> {"tool": name or absolute path, "args": [...],
               "cwd": folder or None, "rescan": folder or None, "full_scan": bool}
    group:     lock group, or a function of the params returning one
    summarize: optional, output lines -> (result dict, message) for a job
               that exited 0 and has no rescan target
    start:     optional, (params, note, argv) -> None or a new argument list
               (without the tool), run in the job's thread right before
               the process starts (no other job of its group is running);
               an exception fails the job with its message, Cancelled
               cancels it; note(text) adds a [feedvault] line to its log
    after:     optional, (public job dict, note) -> None, run in the job's
               thread once its process has exited, cancelled or not, before
               the rescan; an exception is noted in its log and changes
               nothing else
    outcome:   optional, (params, exit code, output lines as printed, index result or
               None, note) -> (state, result, message) for a job whose process
               exited and was not cancelled. With it, the rescan folder is
               indexed whatever the exit code (what a download got before it
               failed counts too)
    ended:     optional, public job dict -> None, once the job has ended in
               any way, recover() included
    pause:     optional, params -> seconds: once a job of this kind has run,
               the next one of this kind in its group waits that long
    describe:  optional, (params, argv) -> label shown instead of ``label``
    """
    _kinds[name] = Kind(name, label, params, build, group, summarize, start, outcome, ended, pause, describe, after)


def kinds():
    return [{"kind": k.name, "label": k.label, "params": k.params} for k in _kinds.values()]


def _check_params(kind, params):
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise BadRequest("params must be an object")
    unknown = sorted(set(params) - set(kind.params))
    if unknown:
        raise BadRequest(f"unknown parameter: {unknown[0]}")
    out = {}
    for name, spec in kind.params.items():
        v = params.get(name)
        if v is None:
            if spec.get("required", True):
                raise BadRequest(f"{name} is required")
            continue
        if spec["type"] == "choice":
            if not isinstance(v, str) or v not in spec["choices"]:
                raise BadRequest(f"{name} must be one of: {', '.join(spec['choices'])}")
        elif not isinstance(v, str) or not v or len(v) > spec.get("max", 500) or "\0" in v:
            raise BadRequest(f"{name} must be text of 1 to {spec.get('max', 500)} characters")
        out[name] = v
    return out


def tool_path(name):
    """The executable to run for a tool: the path set in Settings when there
    is one (None if it no longer works), else the first on PATH."""
    if name in TOOLS:
        configured = (config.load().get("tools") or {}).get(name)
        if configured:
            return configured if config.is_executable(configured) else None
    return shutil.which(name)


def version_line(texts):
    """A tool's version from what ``--version`` printed: the first line
    (ffmpeg's runs on into its copyright notice). Also downloaders.py's."""
    line = next((t.strip() for t in texts if t.strip()), "")
    return line.split(" Copyright")[0][:200]


def _first_line(lines):
    """tool-version: the version is the first line."""
    version = health.scrub(version_line(t for _, t in lines), LINE_MAX) or ""
    return {"version": version}, version or "no version printed"


register("tool-version", label="Check a tool's version",
         params={"tool": {"type": "choice", "choices": list(TOOLS)}},
         # ffmpeg prints its version for -version and fails on --version.
         build=lambda p: {"tool": p["tool"], "args": ["-version" if p["tool"] == "ffmpeg" else "--version"]},
         group="tool-version", summarize=_first_line)


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, job_id, kind, params, spec, group, cwd, rescan, now):
        self.id, self.kind, self.params, self.group, self.cwd = job_id, kind, params, group, cwd
        self.tool, self.args = spec["tool"], [str(a) for a in spec.get("args", [])]
        self.argv = [self.tool, *self.args]        # for display; the tool's path is resolved at start
        self.rescan, self.full_scan = rescan, bool(spec.get("full_scan"))
        self.state = "queued"
        self.created_at, self.started_at, self.ended_at = now, None, None
        self.exit_code, self.result, self.message = None, None, None
        self.lines = collections.deque(maxlen=LOG_LINES)    # (n, text scrubbed): kept, shown, stored
        # (n, text as printed): for the kind's hooks only, kept when it has one
        hooks = _kinds[kind].outcome or _kinds[kind].summarize
        self.raw = collections.deque(maxlen=LOG_LINES if hooks else 0)
        self.n = 0
        self.proc = None
        self.cancelled = False
        self.exited = False
        self.interrupted = False
        self.shown = None                      # what public() says while it is finishing

    def public(self, live=False):
        if self.shown is not None and not live:
            return self.shown
        waits = _cool.get(self.group) if self.state == "queued" and _pauses(self.kind) else None
        return {"id": self.id, "kind": self.kind, "label": _label(self.kind, self.params, self.argv),
                "params": self.params, "argv": self.argv, "cwd": self.cwd, "group": self.group,
                "state": self.state, "created_at": self.created_at, "started_at": self.started_at,
                "ended_at": self.ended_at, "exit_code": self.exit_code, "rescan": self.rescan,
                "result": self.result, "message": self.message,
                "waits_until": int(waits) + 1 if waits and waits > time.time() else None}


def _label(kind_name, params, argv):
    kind = _kinds.get(kind_name)
    if kind is None:
        return kind_name
    return kind.describe(params, argv) if kind.describe else kind.label


def _pauses(kind_name):
    kind = _kinds.get(kind_name)
    return kind is not None and kind.pause is not None


_lock = threading.Lock()
_active = collections.OrderedDict()            # id -> Job, queued and running, oldest first
_closing = False
_cool = {}                                     # group -> time.time() before which a pausing kind may not start
_wake = None                                   # (time, timer) that pumps once a pause is over


def _under_root(path, roots):
    """``path`` resolved, if it is a folder inside (or equal to) a media root."""
    real = os.path.realpath(path)
    for r in roots:
        rr = os.path.realpath(r)
        if real == rr or real.startswith(rr.rstrip(os.sep) + os.sep):
            return real if os.path.isdir(real) else None
    return None


def submit(kind_name, params):
    """Queue a job. Raises BadRequest for an unknown kind or bad parameters."""
    kind = _kinds.get(kind_name) if isinstance(kind_name, str) else None
    if kind is None:
        raise BadRequest("unknown kind")
    params = _check_params(kind, params)
    spec = kind.build(params)
    group = kind.group(params) if callable(kind.group) else kind.group
    cfg = config.load()
    rescan = spec.get("rescan")
    if rescan is not None:
        if _under_root(rescan, cfg["media_roots"]) is None:
            raise BadRequest("the job's folder is not inside a media root")
        # As given, not resolved: the scanner names files under the root as
        # configured, symlinks and all, and so must a rescan.
        rescan = os.path.normpath(os.path.abspath(rescan))
    cwd = spec.get("cwd") or cfg["data_directory"]
    os.makedirs(cwd, exist_ok=True)
    if _closing:
        raise BadRequest("FeedVault is stopping")
    now = int(time.time())
    # Written before taking the lock, which every output line needs: a
    # database busy with a scan must not stall running jobs.
    job = Job(_insert(kind.name, params, spec, group, cwd, rescan, now),
              kind.name, params, spec, group, cwd, rescan, now)
    with _lock:
        closing = _closing
        if not closing:
            _active[job.id] = job
    if closing:
        job.state, job.ended_at, job.message = "interrupted", now, INTERRUPTED
        _save(job, [])
        raise BadRequest("FeedVault is stopping")
    _pump()
    return job.public()


def _pump():
    """Start whatever queued jobs may run now, oldest first. A job of a kind
    with a pause waits until its group's pause is over (and holds the rest
    of its group, so the queue keeps its order)."""
    global _wake
    starting = []
    with _lock:
        if _closing:
            return
        running = [j for j in _active.values() if j.state == "running"]
        busy = {j.group for j in running}
        now, wake = time.time(), None
        for job in _active.values():
            if len(running) + len(starting) >= MAX_RUNNING:
                break
            if job.state != "queued" or job.group in busy:
                continue
            until = _cool.get(job.group, 0) if _pauses(job.kind) else 0
            if until > now:
                busy.add(job.group)
                wake = min(wake or until, until)
                continue
            job.state, job.started_at = "running", int(now)
            busy.add(job.group)
            starting.append(job)
        if wake is not None and (_wake is None or wake < _wake[0]):
            if _wake:
                _wake[1].cancel()
            _wake = (wake, threading.Timer(wake - now + 0.05, _woken))
            _wake[1].daemon = True
            _wake[1].start()
    for job in starting:
        _save(job)
        threading.Thread(target=_run, args=(job,), daemon=True, name=f"job-{job.id}").start()


def _woken():
    global _wake
    with _lock:
        _wake = None
    _pump()


def _killpg(proc, sig):
    """Signal the job's whole process group (its pid: it leads its own session)."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _run(job):
    try:
        exe = tool_path(job.tool)
        if exe is None:
            _finish(job, "failed", result={"error": "missing"}, message=f"{job.tool} not found; set its path in Settings")
            return
        if job.cancelled:
            _finish(job, "cancelled", message="cancelled")
            return
        kind = _kinds[job.kind]
        if kind.start:
            try:
                args = kind.start(job.params, lambda text: _note(job, f"[feedvault] {text}"), job.argv)
                if args is not None:
                    _set_args(job, args)
            except Cancelled as e:
                _note(job, f"[feedvault] {e}")
                _finish(job, "cancelled", message=str(e))
                return
            except Exception as e:
                _finish(job, "failed", message=str(e) or type(e).__name__)
                return
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}      # the downloaders are Python: live output
        try:
            proc = subprocess.Popen([exe, *job.args], cwd=job.cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as e:
            _finish(job, "failed", message=f"{job.tool} could not start: {e.strerror or e}")
            return
        with _lock:
            job.proc = proc
            stop = job.cancelled or job.interrupted
        if stop:
            _terminate(job)
        _record_process(job, proc.pid)
        reader = threading.Thread(target=_read, args=(job, proc.stdout), daemon=True, name=f"job-{job.id}-log")
        reader.start()
        code = proc.wait()
        with _lock:
            job.exited = True                  # too late to cancel: what it downloaded gets indexed
        _killpg(proc, signal.SIGKILL)          # whatever it left behind in its group
        reader.join(5)                         # a process that left the group may still hold the pipe
        if not reader.is_alive():
            proc.stdout.close()
        job.exit_code = code
        if kind.after:
            try:
                kind.after(job.public(live=True), lambda text: _note(job, f"[feedvault] {text}"))
            except Exception as e:             # the run's files are there: it still ends as it went
                _note(job, f"[feedvault] after-run step failed: {e}")
        if job.cancelled:
            _finish(job, "cancelled", message="cancelled")
        elif job.interrupted:                  # FeedVault is stopping: no indexing on the way out
            _finish(job, "interrupted")
        elif kind.outcome:
            index = None
            if job.rescan:
                _note(job, f"[feedvault] indexing {job.rescan}")
                index = _index(job)
            state, result, message = kind.outcome(job.params, code, list(job.raw), index,
                                                  lambda text: _note(job, f"[feedvault] {text}"))
            _finish(job, state, result=result, message=message)
        elif code != 0:
            _finish(job, "failed", message=_last_line(job) or f"exit code {code}")
        elif job.rescan:
            _note(job, f"[feedvault] indexing {job.rescan}")
            result = _index(job)
            n = result["added"]
            _finish(job, "done", result=result, message=f"{n} new post{'' if n == 1 else 's'}")
        elif _kinds[job.kind].summarize:
            result, message = _kinds[job.kind].summarize(list(job.raw))
            _finish(job, "done", result=result, message=message)
        else:
            _finish(job, "done", message="finished")
    except Exception as e:                     # keep the app alive; show it in the UI
        _finish(job, "failed", message=f"job runner error: {e}")


PROC = "/proc"                                 # Linux only: without it, no process is ever stopped at startup


def identity(pid):
    """(start time, executable) of a live process, else None. The start time
    is in clock ticks since boot: a pid reused by another process, or after a
    reboot, has another."""
    try:
        with open(f"{PROC}/{pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()   # the name, in brackets, may hold anything
        if fields[0] == "Z":
            return None                        # a zombie has no executable left
        return int(fields[19]), os.readlink(f"{PROC}/{pid}/exe")
    except (OSError, IndexError, ValueError):
        return None


def _record_process(job, pid):
    """Remember who the job's process is, for recover() after a crash."""
    found = identity(pid)
    try:
        conn = db.connect()
        conn.execute("UPDATE jobs SET pid = ?, pid_start = ?, pid_exe = ? WHERE id = ?",
                     (pid, *(found or (None, None)), job.id))
        conn.commit()
    except Exception as e:                     # the job runs on; only a crash would need it
        print(f"[jobs] #{job.id}: could not record its process: {e}")


def _index(job):
    roots = config.load()["media_roots"]
    if job.full_scan:
        report = scanner.run(roots)            # every root: a scan of one would mark the others missing
    else:
        report = scanner.index_dirs(roots, list(scanner.folders(job.rescan)), new=True)
    return {"added": report["added"], "updated": report["updated"]}


PROGRESS_EVERY = 1.0                           # seconds between two kept progress redraws


def _read(job, stream):
    """Split the output into lines at \n, and at a lone \r too: progress
    bars redraw with \r and might not print a \n for minutes. A redraw is
    kept at most once per PROGRESS_EVERY; a line ended by \n always is."""
    buf, skipping, last_redraw = b"", False, 0.0
    after_cr = False                           # the last line ended with \r: a \n now completes a \r\n
    while True:
        chunk = stream.read1(65536)
        eof = not chunk
        buf += chunk
        while buf:
            if after_cr and buf.startswith(b"\n"):
                buf = buf[1:]
            after_cr = False
            cut = min((k for k in (buf.find(b"\n"), buf.find(b"\r")) if k >= 0), default=-1)
            if cut < 0:
                break
            crlf = buf[cut:cut + 2] == b"\r\n"
            redraw = buf[cut:cut + 1] == b"\r" and not crlf
            line, buf = buf[:cut], buf[cut + (2 if crlf else 1):]
            after_cr = redraw and not buf      # the \n may come in the next read
            if skipping:
                skipping = False
            elif not redraw:
                _note(job, _decode(line))
            elif line and time.monotonic() - last_redraw >= PROGRESS_EVERY:
                last_redraw = time.monotonic()
                _note(job, _decode(line))
        if len(buf) > LINE_MAX:                # cut it; drop the rest up to the next line end
            if not skipping:
                _note(job, _decode(buf[:LINE_MAX]) + " …")
            buf, skipping = b"", True
        if eof:
            if buf and not skipping:
                _note(job, _decode(buf))
            return


def _decode(raw):
    return raw.decode("utf-8", "replace")


def _note(job, text):
    """One line of output (or a [feedvault] note): kept scrubbed, one line
    for one line (an empty one stays, empty), and as printed for the hooks."""
    shown = health.scrub(text, LINE_MAX) or ""
    with _lock:
        job.n += 1
        job.lines.append((job.n, shown))
        job.raw.append((job.n, text))


def _last_line(job):
    with _lock:
        return next((t for _, t in reversed(job.lines) if t.strip() and not t.startswith("[feedvault]")), None)


def _finish(job, state, result=None, message=None):
    kind = _kinds.get(job.kind)
    pause = 0
    if kind and kind.pause and job.proc is not None:         # it ran, so it reached the site
        try:
            pause = max(0, kind.pause(job.params))
        except Exception as e:                 # reads config.json: never left holding the queue
            print(f"[jobs] #{job.id}: no pause: {e}")
    with _lock:
        recorded = job.interrupted and job.ended_at is not None
        if not recorded:
            # Listed as it was until its effects are in (_ended): whoever
            # sees it ended sees them.
            job.shown = job.public()
            if job.interrupted:
                state, message = "interrupted", INTERRUPTED
            job.state, job.result, job.message = state, result, message
            job.ended_at = int(time.time())
        tail = list(job.lines)[-TAIL_KEPT:]
        if pause:
            _cool[job.group] = time.time() + pause
    if not recorded:                           # else shutdown() saved it and ran _ended
        _save(job, tail)                       # before leaving _active: the log never has a gap
        _ended(job.public(live=True))
    with _lock:
        _active.pop(job.id, None)
        job.lines.clear()
        job.raw.clear()
    _prune()
    _pump()


def _ended(public):
    kind = _kinds.get(public["kind"])
    if kind and kind.ended:
        try:
            kind.ended(public)
        except Exception as e:                 # a hook must not stop the queue
            print(f"[jobs] #{public['id']} {public['kind']}: after-job step failed: {e}")


def _terminate(job):
    """SIGTERM to the job's group; SIGKILL if it is still running KILL_AFTER later."""
    proc = job.proc
    _killpg(proc, signal.SIGTERM)

    def kill():
        if proc.poll() is None:
            _killpg(proc, signal.SIGKILL)
    t = threading.Timer(KILL_AFTER, kill)
    t.daemon = True
    t.start()


class TooLate(Exception):
    """The job's process has exited; it is indexing what it downloaded."""


def cancel(job_id):
    """Cancel a queued or running job: its public dict, or None if it is not
    active. Raises TooLate once its process has exited."""
    with _lock:
        job = _active.get(job_id)
        if job is None:
            return None
        if job.exited:
            raise TooLate("the job has finished and is indexing its files")
        if job.state == "queued":
            del _active[job_id]
            job.state, job.ended_at, job.message = "cancelled", int(time.time()), "cancelled"
            queued = True
        else:
            job.cancelled = True
            queued = False
        public = job.public()                  # as it was: the job may end right after SIGTERM
    if queued:
        _save(job, [])
        _ended(job.public())
        _prune()
    elif job.proc is not None:                 # else _run sees the flag once it has started it
        _terminate(job)
    return public


def shutdown():
    """Stop every job before the app exits, the same way as cancel, waiting
    at most KILL_AFTER. Ended jobs are recorded as interrupted."""
    global _closing, _wake
    with _lock:
        _closing = True
        if _wake:
            _wake[1].cancel()
            _wake = None
        jobs = list(_active.values())
        for job in jobs:
            job.interrupted = True
    procs = [j.proc for j in jobs if j.proc is not None and not j.exited]
    for proc in procs:
        _killpg(proc, signal.SIGTERM)
    deadline = time.monotonic() + KILL_AFTER
    for proc in procs:
        try:
            proc.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        _killpg(proc, signal.SIGKILL)
    now = int(time.time())
    for job in jobs:
        with _lock:
            ended = job.ended_at is not None
            if not ended:
                job.state, job.ended_at, job.message = "interrupted", now, INTERRUPTED
            tail = list(job.lines)[-TAIL_KEPT:]
        _save(job, tail)
        if not ended:
            _ended(job.public())
    if jobs:
        print(f"[jobs] stopped {len(jobs)} job{'' if len(jobs) == 1 else 's'}")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def active():
    """Public dicts of the queued and running jobs, oldest first."""
    with _lock:
        return [j.public() for j in _active.values()]


def _insert(kind, params, spec, group, cwd, rescan, now):
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO jobs(kind, params, argv, cwd, lock_group, state, created_at, rescan, full_scan) "
        "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
        (kind, json.dumps(params), json.dumps([spec["tool"], *map(str, spec.get("args", []))]), cwd, group,
         now, rescan, int(bool(spec.get("full_scan")))))
    conn.commit()
    return cur.lastrowid


def _set_args(job, args):
    """A start hook's argument list replaces the one built at queue time."""
    job.args = [str(a) for a in args]
    job.argv = [job.tool, *job.args]
    conn = db.connect()
    conn.execute("UPDATE jobs SET argv = ? WHERE id = ?", (json.dumps(job.argv), job.id))
    conn.commit()


def _save(job, tail=None):
    """Write a job's state; ``tail`` (its last lines) once it has ended."""
    conn = db.connect()
    conn.execute(
        "UPDATE jobs SET state = ?, started_at = ?, ended_at = ?, exit_code = ?, result = ?, message = ?, "
        "tail = COALESCE(?, tail) WHERE id = ?",
        (job.state, job.started_at, job.ended_at, job.exit_code,
         None if job.result is None else json.dumps(job.result), job.message,
         None if tail is None else json.dumps(tail), job.id))
    conn.commit()


def _prune():
    conn = db.connect()
    conn.execute("DELETE FROM jobs WHERE state NOT IN ('queued', 'running') "
                 "AND id NOT IN (SELECT id FROM jobs ORDER BY id DESC LIMIT ?)", (HISTORY_KEPT,))
    conn.commit()


def recover():
    """At startup: jobs a stopped FeedVault left queued or running. A process
    one of them left behind is stopped, its whole group, if it is still the
    same process: same pid, start time and executable."""
    conn = db.connect()
    rows = conn.execute("SELECT id, pid, pid_start, pid_exe FROM jobs WHERE state IN ('queued', 'running')").fetchall()
    ids = [r["id"] for r in rows]
    left = [r for r in rows if r["pid"] and r["pid_start"] is not None]
    if left and not os.path.isdir(PROC):
        print(f"[jobs] no {PROC}: processes left running when FeedVault last stopped are not looked for")
        left = []
    stopped = _stop_leftovers([(r["id"], r["pid"], (r["pid_start"], r["pid_exe"])) for r in left])
    # When it really ended is unknown: ended_at stays NULL.
    conn.executemany("UPDATE jobs SET state = 'interrupted', message = ?, ended_at = NULL WHERE id = ?",
                     [(INTERRUPTED, i) for i in ids])
    conn.commit()
    for i in stopped:
        _note_tail(conn, i, f"[feedvault] {LEFT_RUNNING.format(pid=stopped[i])}")
    for i in ids:
        _ended(get(i))
    if ids:
        print(f"[jobs] {len(ids)} job{'' if len(ids) == 1 else 's'} interrupted when FeedVault last stopped")


LEFT_RUNNING = "process {pid} was still running after FeedVault stopped: stopped at the next start"


def _pin(pid, recorded):
    """A pidfd of ``pid`` when it is still the process recorded (start time
    and executable), else None; -1 when pidfds cannot be had (the check is
    then made again right before each signal). While the pidfd's process lives, its
    pid cannot be given to another."""
    try:
        fd = os.pidfd_open(pid)
    except AttributeError:
        fd = -1
    except ProcessLookupError:
        return None                            # gone
    except OSError:
        fd = -1                                # pidfds unsupported or not allowed (ENOSYS, EPERM)
    if identity(pid) != recorded:
        if fd >= 0:
            os.close(fd)
        return None
    return fd


def _signal_group(fd, pid, recorded, sig):
    """Signal the group ``pid`` leads, if ``pid`` is still the process
    pinned by ``fd`` (else the one recorded). False when it is gone."""
    try:
        if fd >= 0:
            signal.pidfd_send_signal(fd, 0)    # ProcessLookupError once it has exited
        elif identity(pid) != recorded:
            return False
        os.killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _stop_leftovers(found):
    """``found``: [(job id, pid, (start time, executable) recorded)]. SIGTERM
    to the group of each pid that is still that process, then SIGKILL after
    KILL_AFTER to those still there. A pid that is someone else's now is
    never signalled. Returns {job id: pid} of those signalled."""
    groups, stopped = {}, {}
    for job_id, pid, recorded in found:
        fd = _pin(pid, recorded)
        if fd is None:
            if identity(pid) is not None:
                print(f"[jobs] process {pid} of job #{job_id} is another program now: left alone")
            continue
        if not _signal_group(fd, pid, recorded, signal.SIGTERM):
            _close(fd)
            continue
        groups[pid] = (fd, recorded)
        stopped[job_id] = pid
        print(f"[jobs] stopping process {pid} (job #{job_id}), left running when FeedVault last stopped")
    deadline = time.monotonic() + KILL_AFTER
    while groups and time.monotonic() < deadline:
        for pid, (fd, recorded) in list(groups.items()):
            if identity(pid) != recorded:
                _close(fd)
                del groups[pid]
        time.sleep(0.05)
    for pid, (fd, recorded) in groups.items():
        if _signal_group(fd, pid, recorded, signal.SIGKILL):
            print(f"[jobs] process {pid} ignored SIGTERM: killed")
        _close(fd)
    return stopped


def _close(fd):
    if fd >= 0:
        os.close(fd)


def _note_tail(conn, job_id, text):
    """Add a line to an ended job's kept log."""
    row = conn.execute("SELECT tail FROM jobs WHERE id = ?", (job_id,)).fetchone()
    try:
        tail = json.loads(row["tail"]) if row and row["tail"] else []
    except ValueError:
        tail = []
    n = tail[-1][0] + 1 if tail and isinstance(tail[-1], list) and isinstance(tail[-1][0], int) else 1
    conn.execute("UPDATE jobs SET tail = ? WHERE id = ?", (json.dumps([*tail[-(TAIL_KEPT - 1):], [n, text]]), job_id))
    conn.commit()


_COLUMNS = "id, kind, params, argv, cwd, lock_group, state, created_at, started_at, ended_at, " \
           "exit_code, rescan, result, message"


def _public(row):
    params, argv = json.loads(row["params"]), json.loads(row["argv"])
    return {"id": row["id"], "kind": row["kind"], "label": _label(row["kind"], params, argv),
            "params": params, "argv": argv, "cwd": row["cwd"],
            "group": row["lock_group"], "state": row["state"], "created_at": row["created_at"],
            "started_at": row["started_at"], "ended_at": row["ended_at"], "exit_code": row["exit_code"],
            "rescan": row["rescan"], "result": json.loads(row["result"]) if row["result"] else None,
            "message": row["message"], "waits_until": None}


def listing():
    """Active jobs and recent history, newest first."""
    with _lock:
        active = {j.id: j.public() for j in _active.values()}
    rows = db.connect().execute(f"SELECT {_COLUMNS} FROM jobs ORDER BY id DESC LIMIT ?",
                                (HISTORY_KEPT + len(active),)).fetchall()
    # The live state wins: a row is written a moment after the change.
    jobs = [active.get(r["id"]) or _public(r) for r in rows]
    return {"running": sum(j["state"] == "running" for j in active.values()),
            "queued": sum(j["state"] == "queued" for j in active.values()),
            "jobs": jobs}


def get(job_id):
    with _lock:
        job = _active.get(job_id)
        if job is not None:
            return job.public()
    row = db.connect().execute(f"SELECT {_COLUMNS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _public(row) if row else None


def log(job_id, after=0):
    """Lines numbered above ``after``: the live buffer while the job is
    active, the kept tail once it has ended. None for an unknown job.
    ``first`` is the oldest line still kept (older ones were dropped)."""
    with _lock:
        job = _active.get(job_id)
        if job is not None:
            lines, state = list(job.lines), job.state
            first = lines[0][0] if lines else job.n + 1
    if job is None:
        row = db.connect().execute("SELECT state, tail FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        lines, state = [tuple(x) for x in json.loads(row["tail"])], row["state"]
        first = lines[0][0] if lines else 1
    new = [ln for ln in lines if ln[0] > after]
    page = new[:LOG_PAGE]
    return {"state": state, "first": first, "next": page[-1][0] if page else after,
            "more": len(new) > len(page), "lines": [{"n": n, "text": t} for n, t in page]}
