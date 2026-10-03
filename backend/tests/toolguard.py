"""The test guard (#56), shared by the test process (conftest.tool_guard) and
every Python it starts (guard_site/sitecustomize.py, on their PYTHONPATH).

A Guard refuses, by raising ToolGuardError: starting a program (Popen, exec,
posix_spawn, spawn, os.system) whose real path is outside its roots unless
it is allowed, a script whose shebang's interpreter is not; importing a
downloader's (or pip's) Python package found outside its roots; a connection or a name lookup to
anything but this machine. Every refusal is also written to its log file,
where a forked or started child's refusals reach the test too.
"""
import importlib.machinery
import ipaddress
import json
import os
import shutil
import sys

EXEC_EVENTS = {"subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn", "os.system"}
TOOL_MODULES = {"instaloader", "gallery_dl", "yt_dlp", "pip", "pipx"}
ENV = "FEEDVAULT_TEST_GUARD"                   # a Guard's settings, as JSON, for the Pythons a test starts
_which = shutil.which


class ToolGuardError(RuntimeError):
    """A test found or started a program outside its tmp dir and the fakes,
    or reached the network."""


class Guard:
    def __init__(self, roots, allowed, interpreters, log=None):
        """``allowed``: {path: why} run by anyone; ``interpreters``: {path:
        why} only as the shebang of a script that may run (they start
        whatever they are given, unchecked)."""
        self.roots = [os.path.realpath(r) for r in roots]
        self.allowed = {os.path.realpath(p): why for p, why in allowed.items()}
        self.interpreters = {os.path.realpath(p): why for p, why in interpreters.items()}
        self.log = log
        self.runs = []                         # every program this process started, allowed or not
        self.stray = []                        # refusals when there is no log

    def settings(self):
        return json.dumps({"roots": self.roots, "allowed": self.allowed,
                           "interpreters": self.interpreters, "log": self.log})

    def allow(self, path, why):
        """Let this test run ``path`` too (``why`` is for the reader). Its
        children have the settings they started with."""
        self.allowed[os.path.realpath(path)] = why
        os.environ[ENV] = self.settings()

    def inside(self, path):
        real = os.path.realpath(path)
        return any(real == r or real.startswith(r + os.sep) for r in self.roots)

    def allows(self, path):
        return self.inside(path) or os.path.realpath(path) in self.allowed

    def refuse(self, what):
        if _child or os.getpid() != _main_pid:
            what = f"[child {os.getpid()}] {what}"
        if self.log:
            with open(self.log, "a") as f:
                f.write(what.replace("\n", " ") + "\n")
        else:
            self.stray.append(what)
        raise ToolGuardError(f"test guard (#56): {what}")

    @property
    def violations(self):
        try:
            with open(self.log) as f:
                return f.read().splitlines()
        except (TypeError, FileNotFoundError):
            return list(self.stray)

    def taken(self):
        """The refusals so far, which then no longer fail the test."""
        out = self.violations
        if self.log and os.path.exists(self.log):
            os.truncate(self.log, 0)
        self.stray = []
        return out

    def check_run(self, exe, env, cwd, depth=0):
        """Refuse ``exe`` (a path, or a name looked up on PATH as the child
        would, in ``cwd``) when it is not allowed, or its shebang's
        interpreter is not."""
        for path in found(os.fsdecode(exe), env, cwd):
            real = os.path.realpath(path)
            if depth == 0:
                self.runs.append(real)
            if not (self.allows(real) or (depth and real in self.interpreters)):
                kind = "as an interpreter " if depth else ""
                self.refuse(f"run {kind}{path}" + (f" ({real})" if real != path else ""))
            try:
                with open(real, "rb") as f:
                    first = f.readline(256)
            except OSError:
                continue
            words = first[2:].decode("utf-8", "replace").split() if first.startswith(b"#!") else []
            if words and depth < 3:            # the kernel runs the interpreter: it must pass too
                self.check_run(words[0], env, cwd, depth + 1)

    def check_address(self, host, what):
        if not isinstance(host, (str, bytes)) or not host or host in ("localhost", b"localhost"):
            return                             # None, "", or not an internet address (netlink...)
        try:
            loopback = ipaddress.ip_address(os.fsdecode(host).split("%")[0]).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            self.refuse(f"{what} {os.fsdecode(host)}")

    def audit(self, event, args):
        if event in EXEC_EVENTS:
            if event == "os.system":
                self.refuse(f"os.system({os.fsdecode(args[0])!r})")
            elif event == "os.spawn":          # (mode, path, args, env); not raised on Linux: os.exec is
                self.check_run(args[1], args[3], None)
            elif event == "subprocess.Popen":  # (executable, args, cwd, env)
                self.check_run(args[0], args[3], args[2])
            else:                              # (path, args, env)
                self.check_run(args[0], args[2], None)
        elif event == "socket.getaddrinfo":    # (host, port, family, type, proto)
            self.check_address(args[0], "look up")
        elif event == "socket.connect":        # (socket, address)
            address = args[1]
            if isinstance(address, tuple):
                self.check_address(address[0], "connect to")
            elif not (isinstance(address, (str, bytes)) and address and self.inside(os.fsdecode(address))):
                self.refuse(f"connect to {address!r}")


def found(name, env, cwd):
    """What exec would run for ``name``: a path as it is (relative: in
    ``cwd``), a name on PATH (``env``'s, and the caller's for posix_spawnp),
    empty entries meaning ``cwd``. [] when nothing is there."""
    base = os.fspath(cwd) if cwd else os.getcwd()
    base = os.fsdecode(base)
    if os.sep in name:
        path = os.path.join(base, name)
        return [path] if os.path.exists(path) else []
    out = []
    for e in (env, None):
        for d in os.get_exec_path(e):
            path = os.path.join(base, os.fsdecode(d), name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                if path not in out:
                    out.append(path)
                break
    return out


_main_pid = os.getpid()
_child = False                                 # True in a Python a test started
_current = []                                  # [the Guard in force]


def current():
    return _current[0]


class _NoToolModules:
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in TOOL_MODULES:
            spec = importlib.machinery.PathFinder.find_spec(name, path)
            where = spec and (spec.origin or next(iter(spec.submodule_search_locations or []), None))
            if where and not current().inside(where):   # missing: fails as missing; a fake in tmp: fine
                current().refuse(f"import {name} ({where})")
        return None


def install(guard):
    """Put ``guard`` in force in this process: once per process (an audit hook
    cannot be removed), then ``use`` swaps it."""
    if _current:
        _current[0] = guard
        return
    _current.append(guard)
    sys.addaudithook(lambda event, args: current().audit(event, args))
    sys.meta_path.insert(0, _NoToolModules())


def use(guard):
    _current[0] = guard
