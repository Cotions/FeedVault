"""The gate around the open library (the active profile's database, data
directory and media roots, see config.py).

A profile switch (app.py, activate_profile) repoints the whole process at
another library. Whatever reads the config and then writes the database must
not straddle it: a delete that read profile A's media roots would otherwise
write into profile B's index. So every request is answered inside the gate
(enter, leave), the scheduler ticks inside it (shared), and a switch waits
until it is the only one in (alone); what arrives meanwhile waits for the
switch to end, then sees the new library only.

Jobs, scans and the hashing pass are not counted here: a switch is refused
while a job or a scan runs, and stops the hashing pass (app.py).
"""
import contextlib
import threading

WAIT = 10.0                                    # seconds a switch waits for the requests being answered

_gate = threading.Condition()
_inside = 0
_switching = False


class Busy(Exception):
    """The library cannot be switched now; str() says why."""


def enter():
    """Before using the library; waits for a switch under way. Matched by
    one leave()."""
    global _inside
    with _gate:
        _gate.wait_for(lambda: not _switching)
        _inside += 1


def leave():
    global _inside
    with _gate:
        _inside = max(0, _inside - 1)
        _gate.notify_all()


@contextlib.contextmanager
def shared():
    enter()
    try:
        yield
    finally:
        leave()


@contextlib.contextmanager
def alone(own=1, timeout=None):
    """For the switch: nothing else is inside while it is held, and nothing
    enters. ``own``: how many times the caller itself is inside (a request:
    once). Raises Busy when another switch is under way, or when the others
    are not out within ``timeout`` seconds (WAIT)."""
    global _switching
    with _gate:
        if _switching:
            raise Busy("another profile switch is under way")
        _switching = True
        if not _gate.wait_for(lambda: _inside <= own, WAIT if timeout is None else timeout):
            _switching = False
            _gate.notify_all()
            raise Busy("FeedVault is still answering another request; try again in a moment")
    try:
        yield
    finally:
        with _gate:
            _switching = False
            _gate.notify_all()
