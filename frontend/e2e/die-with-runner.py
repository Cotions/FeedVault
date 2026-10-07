"""Runs a program that gets SIGTERM when the e2e runner dies (#110).

    python die-with-runner.py RUNNER_PID PROGRAM [ARG...]

Linux's PR_SET_PDEATHSIG: the kernel sends SIGTERM to this process when
the thread that started it ends, however it ends (SIGKILL, a crash), and
the setting stays across the exec below, so the program is the one that
gets it. The backend takes SIGTERM as its usual shutdown: it stops its
jobs, which run in sessions of their own, before it exits.

If the runner died before prctl, nothing would come: this process has
been handed to another parent by then, so the parent is checked after.
"""
import ctypes
import os
import signal
import sys

PR_SET_PDEATHSIG = 1

runner = int(sys.argv[1])
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
    sys.exit(f"die-with-runner: prctl failed: {os.strerror(ctypes.get_errno())}")
if os.getppid() != runner:
    sys.exit(f"die-with-runner: the runner (pid {runner}) is gone")
os.execv(sys.argv[2], sys.argv[2:])
