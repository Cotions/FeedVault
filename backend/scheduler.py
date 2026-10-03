"""Schedules: sources synced on their own (a source's "schedule" option,
sources.py: off, hourly, daily or weekly).

One thread (start, stop) wakes every TICK seconds and queues the sources
that are due through sync.sync, so a scheduled sync is an ordinary job: the
tool's pause between syncs applies, and a source already queued or running
is never queued twice. Settings → Sync can pause them all
(``schedules_paused`` in the config, off by default: schedules run).

When a source is due (all times are UTC seconds, as last_sync_at):

- never synced, or its last sync was interrupted (FeedVault stopped while
  it ran): now;
- else when its last sync ended (last_sync_at, whatever the outcome) plus
  its delay. The delay is the schedule's interval (1 h, 24 h, 7 days);
  after n failed syncs in a row (last_result.failures, see sync._failures)
  it is the interval × 2^n, at most a day, and never less than the interval
  (a weekly schedule stays weekly). A failing source so waits 2 h, 4 h,
  8 h… before trying again, never a tight loop; a sync that works brings
  it back to its interval.

A due time long past (FeedVault was off) is only due: the source runs once
at startup, not once per missed slot, since its next time counts from the
end of that sync.

Spreading: per tick, at most one source per platform is queued, the most
overdue, and only when no sync of that platform is queued or running and
the scheduler queued the last one SPREAD seconds ago or more. A set of
Instagram profiles due together so starts one by one, minutes apart.

Skipped, with a note on the source (status: "skipped"), and tried again at
the next tick:

- its tool is missing (jobs.tool_path, the lookup a sync and Settings →
  Downloaders use: the path set in Settings, else PATH);
- the media root holding its folder is offline: not a folder, or empty
  while the index has posts under it (a mount point with nothing mounted).

Stopped: a source whose syncs said its account is not found (deleted, or
renamed without a trace) or that a login is required (health.BLOCKING)
twice in a row, or once with a session the site accepted, is not synced on
its own any more, as trying again would not change that; status says why
("paused: account not found", "paused: login required"). A lone one is
backed off like any failure: instaloader says the same to a throttled
anonymous client (health.paused). It comes back when a sync of it works
(Sync clicked), or when its schedule or session changes (for a source
without its own, the tool's session in Settings: sources.resume_tool) or a
rename is accepted (last_result.resumed). Rate limited is not stopped: the back-off
above applies.

A sync refused (jobs.BadRequest: its folder is no longer inside a media
root…) is noted too and held for one interval. Notes and holds live in
memory: a restart tries again at once. A note no longer shows once the
source has synced since (Sync clicked), and changing its schedule or
removing it forgets both.
"""
import os
import threading
import time

import config
import db
import health
import jobs
import scanner
import sources
import sync

INTERVALS = {"hourly": 3600, "daily": 86400, "weekly": 7 * 86400}
BACKOFF_MAX = 86400                            # a failing source still tries once a day
TICK = 60
STARTUP_DELAY = 20                             # the first tick, once the server is up
SPREAD = 300                                   # between two scheduled syncs of one platform

clock = time.time                              # tests set a fake one

_lock = threading.Lock()
_notes = {}                                    # source id -> (why its schedule was skipped, when)
_held = {}                                     # source id -> not before (after a refusal)
_last = {}                                     # platform -> when the scheduler last queued one
_thread = None
_stop = None


def delay(every, failures):
    """Seconds from a sync's end to the next: the interval, doubled per
    failure in a row up to BACKOFF_MAX, never less than the interval."""
    interval = INTERVALS[every]
    if not failures:
        return interval
    return max(interval, min(interval * 2 ** min(failures, 32), BACKOFF_MAX))


def due_at(every, last_sync_at, result):
    """When a source with schedule ``every`` (not off) is due: 0 when now.
    ``last_sync_at`` and ``result`` as stored (sources.json can be edited by
    hand: a value that is not a time or an object counts as none)."""
    if not isinstance(result, dict):
        result = {}
    if not isinstance(last_sync_at, int) or isinstance(last_sync_at, bool) or result.get("state") == "interrupted":
        return 0
    return last_sync_at + delay(every, sources.failures(result))


def status(s, cfg=None):
    """A public source's schedule, for the dashboard: {every, next_at (UTC
    seconds, in the past when due; null when off or stopped), paused (all
    schedules are), skipped (why it was not queued, or null), stopped (why
    the scheduler no longer syncs it, or null), failures}."""
    cfg = cfg or config.load()
    every = s["options"]["schedule"]
    with _lock:
        note, held = _notes.get(s["id"]), _held.get(s["id"], 0)
    why = health.paused(s["last_result"])
    stopped = f"paused: {why}" if why and every in INTERVALS else None
    next_at = None
    if every in INTERVALS and not stopped:
        next_at = max(due_at(every, s["last_sync_at"], s["last_result"]), held)
    synced = s["last_sync_at"] if isinstance(s["last_sync_at"], int) else None
    shown = note is not None and every in INTERVALS and (synced is None or synced < note[1])
    return {"every": every, "next_at": next_at, "paused": cfg.get("schedules_paused") is True,
            "skipped": note[0] if shown else None, "stopped": stopped, "failures": sources.failures(s["last_result"])}


def forget(sid):
    """A source's note and hold, after its schedule changed or it was removed."""
    with _lock:
        _notes.pop(sid, None)
        _held.pop(sid, None)


def _offline_root(folder, roots, conn):
    """The media root holding ``folder`` when it is offline, else None."""
    held = [r for r in roots if folder == r or folder.startswith(r.rstrip(os.sep) + os.sep)]
    if not held:
        return None                            # sync.sync refuses it
    root = max(held, key=len)
    try:
        with os.scandir(root) as it:
            empty = next(it, None) is None
    except OSError:
        return root
    return root if empty and not scanner.nothing_under(conn, root) else None


def _skipped(row, roots, conn):
    """Why a due source cannot run now, or None."""
    if jobs.tool_path(row["tool"]) is None:
        return f"skipped: {row['tool']} was not found (Settings → Downloaders)"
    root = _offline_root(row["folder"], roots, conn)
    if root is not None:
        return f"skipped: its media root {root} is offline"
    return None


def _note(sid, text, now):
    with _lock:
        before = _notes.get(sid)
        _notes[sid] = (text, now)
    if before is None or before[0] != text:
        print(f"[schedule] source {sid}: {text}")


def tick(now=None):
    """Queue the sources due at ``now`` (default: clock()), at most one
    per platform. Returns the jobs queued."""
    now = int(clock() if now is None else now)
    cfg = config.load()
    if cfg.get("schedules_paused") is True:
        return []
    conn = db.connect()
    rows = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
    busy = sync.active()
    platform = {r["id"]: r["platform"] for r in rows}
    taken = {platform.get(sid) for sid in busy}
    with _lock:
        held, last = dict(_held), dict(_last)
    due = []
    for r in rows:
        every = sources.stored_options(r)["schedule"]
        if every not in INTERVALS or r["id"] in busy or health.paused(sources.last_result(r)):
            continue
        at = max(due_at(every, r["last_sync_at"], sources.last_result(r)), held.get(r["id"], 0))
        if at <= now:
            due.append((at, r["id"], every, r))
    queued = []
    for at, sid, every, r in sorted(due, key=lambda d: d[:2]):
        p = r["platform"]
        if p in taken or now - last.get(p, now - SPREAD) < SPREAD:
            continue
        why = _skipped(r, cfg["media_roots"], conn)
        if why:
            _note(sid, why, now)
            continue
        if _stop is not None and _stop.is_set():
            break                              # FeedVault is stopping: its jobs too
        try:
            job = sync.sync(sid, scheduled=True)
        except sync.Busy:
            continue
        except jobs.BadRequest as e:
            _note(sid, f"skipped: {e}", now)
            with _lock:
                _held[sid] = now + INTERVALS[every]
            continue
        with _lock:
            _notes.pop(sid, None)
            _held.pop(sid, None)
            _last[p] = now
        taken.add(p)
        queued.append(job)
        print(f"[schedule] source {sid}: {every} sync queued (#{job['id']})")
    return queued


def _loop(stop):
    if stop.wait(STARTUP_DELAY):
        return
    while True:
        try:
            tick()
        except Exception as e:                 # the next tick tries again
            print(f"[schedule] {type(e).__name__}: {e}")
        if stop.wait(TICK):
            return


def start():
    """Start the scheduler thread (once)."""
    global _thread, _stop
    if _thread is not None and _thread.is_alive():
        return
    _stop = threading.Event()
    _thread = threading.Thread(target=_loop, args=(_stop,), name="scheduler", daemon=True)
    _thread.start()


def stop(timeout=10):
    """Stop the thread, waiting for a tick in progress, before jobs.shutdown:
    nothing is queued while the jobs stop."""
    global _thread, _stop
    if _thread is None:
        return
    _stop.set()
    _thread.join(timeout)
    if not _thread.is_alive():
        _stop = None                           # else a tick still running sees it set and queues nothing
    _thread = None
