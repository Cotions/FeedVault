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
"""
import collections
import os
import shutil
import signal
import subprocess
import threading
import time

import config
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


class Kind:
    def __init__(self, name, label, params, build, group, summarize=None):
        self.name, self.label, self.params = name, label, params
        self.build, self.group, self.summarize = build, group, summarize


_kinds = {}


def register(name, *, label, params, build, group, summarize=None):
    """Add a job kind.

    params:    {name: {"type": "choice", "choices": [...]}
                     | {"type": "text", "max": 500}}, each "required"
               unless it says "required": False
    build:     checked params -> {"tool": name or absolute path, "args": [...],
               "cwd": folder or None, "rescan": folder or None, "full_scan": bool}
    group:     lock group, or a function of the params returning one
    summarize: optional, output lines -> (result dict, message) for a job
               that exited 0 and has no rescan target
    """
    _kinds[name] = Kind(name, label, params, build, group, summarize)


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


def _first_line(lines):
    """tool-version: the version is the first line (ffmpeg's runs on into
    its copyright notice)."""
    line = next((t.strip() for _, t in lines if t.strip()), "")
    version = line.split(" Copyright")[0][:200]
    return {"version": version}, version or "no version printed"


register("tool-version", label="Check a tool's version",
         params={"tool": {"type": "choice", "choices": list(TOOLS)}},
         build=lambda p: {"tool": p["tool"], "args": ["--version"]},
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
        self.lines = collections.deque(maxlen=LOG_LINES)    # (n, text)
        self.n = 0
        self.proc = None
        self.cancelled = False
        self.interrupted = False

    def public(self):
        return {"id": self.id, "kind": self.kind, "label": _kinds[self.kind].label if self.kind in _kinds else self.kind,
                "params": self.params, "argv": self.argv, "cwd": self.cwd, "group": self.group,
                "state": self.state, "created_at": self.created_at, "started_at": self.started_at,
                "ended_at": self.ended_at, "exit_code": self.exit_code, "rescan": self.rescan,
                "result": self.result, "message": self.message}


_lock = threading.Lock()
_active = collections.OrderedDict()            # id -> Job, queued and running, oldest first
_closing = False
_next_id = 0
_history = collections.OrderedDict()           # id -> (public dict, tail), ended jobs


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
        rescan = _under_root(rescan, cfg["media_roots"])
        if rescan is None:
            raise BadRequest("the job's folder is not inside a media root")
    cwd = spec.get("cwd") or cfg["data_directory"]
    os.makedirs(cwd, exist_ok=True)
    global _next_id
    with _lock:
        if _closing:
            raise BadRequest("FeedVault is stopping")
        _next_id += 1
        job = Job(_next_id, kind.name, params, spec, group, cwd, rescan, int(time.time()))
        _active[job.id] = job
    _save(job)
    _pump()
    return job.public()


def _pump():
    """Start whatever queued jobs may run now, oldest first."""
    starting = []
    with _lock:
        if _closing:
            return
        running = [j for j in _active.values() if j.state == "running"]
        busy = {j.group for j in running}
        for job in _active.values():
            if len(running) + len(starting) >= MAX_RUNNING:
                break
            if job.state == "queued" and job.group not in busy:
                job.state, job.started_at = "running", int(time.time())
                busy.add(job.group)
                starting.append(job)
    for job in starting:
        _save(job)
        threading.Thread(target=_run, args=(job,), daemon=True, name=f"job-{job.id}").start()


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
            _finish(job, "failed", message=f"{job.tool} not found; set its path in Settings")
            return
        if job.cancelled:
            _finish(job, "cancelled", message="cancelled")
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
        reader = threading.Thread(target=_read, args=(job, proc.stdout), daemon=True, name=f"job-{job.id}-log")
        reader.start()
        code = proc.wait()
        _killpg(proc, signal.SIGKILL)          # whatever it left behind in its group
        reader.join(5)                         # a process that left the group may still hold the pipe
        if not reader.is_alive():
            proc.stdout.close()
        job.exit_code = code
        if job.cancelled:
            _finish(job, "cancelled", message="cancelled")
        elif code != 0:
            _finish(job, "failed", message=_last_line(job) or f"exit code {code}")
        elif job.rescan:
            _note(job, f"[feedvault] indexing {job.rescan}")
            result = _index(job)
            n = result["added"]
            _finish(job, "done", result=result, message=f"{n} new post{'' if n == 1 else 's'}")
        elif _kinds[job.kind].summarize:
            result, message = _kinds[job.kind].summarize(list(job.lines))
            _finish(job, "done", result=result, message=message)
        else:
            _finish(job, "done", message="finished")
    except Exception as e:                     # keep the app alive; show it in the UI
        _finish(job, "failed", message=f"job runner error: {e}")


def _index(job):
    roots = config.load()["media_roots"]
    if job.full_scan:
        report = scanner.scan(roots)           # every root: a scan of one would mark the others missing
    else:
        dirs = []
        for dirpath, dirnames, _ in os.walk(job.rescan):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in scanner._SKIP_DIRS]
            dirs.append(dirpath)
        report = scanner.index_dirs(roots, dirs)
    return {"added": report["added"], "updated": report["updated"]}


def _decode(raw):
    text = raw.decode("utf-8", "replace").rstrip("\r\n")
    if "\r" in text:                           # progress bars redraw with \r: keep the last state
        text = next((s for s in reversed(text.split("\r")) if s), "")
    return text


def _read(job, stream):
    skipping = False                           # inside a line longer than LINE_MAX
    for raw in iter(lambda: stream.readline(LINE_MAX), b""):
        if not skipping:
            _note(job, _decode(raw) + ("" if raw.endswith(b"\n") else " …"))
        skipping = not raw.endswith(b"\n")


def _note(job, text):
    with _lock:
        job.n += 1
        job.lines.append((job.n, text))


def _last_line(job):
    with _lock:
        return next((t for _, t in reversed(job.lines) if t.strip() and not t.startswith("[feedvault]")), None)


def _finish(job, state, result=None, message=None):
    with _lock:
        if job.interrupted:
            state, message = "interrupted", INTERRUPTED
        job.state, job.result, job.message = state, result, message
        job.ended_at = int(time.time())
        tail = list(job.lines)[-TAIL_KEPT:]
    _save(job, tail)                           # before leaving _active: the log never has a gap
    with _lock:
        _active.pop(job.id, None)
        job.lines.clear()
    _prune()
    _pump()


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


def cancel(job_id):
    """Cancel a queued or running job: its public dict, or None if it is not active."""
    with _lock:
        job = _active.get(job_id)
        if job is None:
            return None
        if job.state == "queued":
            del _active[job_id]
            job.state, job.ended_at, job.message = "cancelled", int(time.time()), "cancelled"
            queued = True
        else:
            job.cancelled = True
            queued = False
    if queued:
        _save(job, [])
        _prune()
    elif job.proc is not None:                 # else _run sees the flag once it has started it
        _terminate(job)
    return job.public()


def shutdown():
    """Stop every job before the app exits, the same way as cancel, waiting
    at most KILL_AFTER. Ended jobs are recorded as interrupted."""
    global _closing
    with _lock:
        _closing = True
        jobs = list(_active.values())
        for job in jobs:
            job.interrupted = True
    procs = [j.proc for j in jobs if j.proc is not None]
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
            if job.ended_at is None:
                job.state, job.ended_at, job.message = "interrupted", now, INTERRUPTED
            tail = list(job.lines)[-TAIL_KEPT:]
        _save(job, tail)
    if jobs:
        print(f"[jobs] stopped {len(jobs)} job{'' if len(jobs) == 1 else 's'}")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _save(job, tail=None):
    with _lock:
        if job.state in ("queued", "running"):
            return
        _history[job.id] = (job.public(), tail or [])
        _history.move_to_end(job.id)


def _prune():
    with _lock:
        while len(_history) > HISTORY_KEPT:
            _history.popitem(last=False)


def listing():
    """Active jobs and recent history, newest first."""
    with _lock:
        active = [j.public() for j in _active.values()]
        ended = [h[0] for h in _history.values() if h[0]["id"] not in _active]
    jobs = sorted(active + ended, key=lambda j: j["id"], reverse=True)
    return {"running": sum(j["state"] == "running" for j in active),
            "queued": sum(j["state"] == "queued" for j in active),
            "jobs": jobs[:HISTORY_KEPT + len(active)]}


def get(job_id):
    with _lock:
        job = _active.get(job_id)
        if job is not None:
            return job.public()
        h = _history.get(job_id)
        return h[0] if h else None


def log(job_id, after=0):
    """Lines numbered above ``after``: the live buffer while the job is
    active, the kept tail once it has ended. None for an unknown job.
    ``first`` is the oldest line still kept (older ones were dropped)."""
    with _lock:
        job = _active.get(job_id)
        if job is not None:
            lines, state = list(job.lines), job.state
            first = lines[0][0] if lines else job.n + 1
        else:
            h = _history.get(job_id)
            if h is None:
                return None
            lines, state = h[1], h[0]["state"]
            first = lines[0][0] if lines else 1
    new = [ln for ln in lines if ln[0] > after]
    page = new[:LOG_PAGE]
    return {"state": state, "first": first, "next": page[-1][0] if page else after,
            "more": len(new) > len(page), "lines": [{"n": n, "text": t} for n, t in page]}
