"""Scripts: your own download commands and shell scripts, as files on disk.

They live in ``<config dir>/scripts/`` (the folder of config.json) and are
written and edited there, in a text editor, never through the API: the app
lists them, shows them read-only and runs them. The folder is read again
on every list, view and run, so an edit counts right away.

A script's id is its file name without the suffix. Two kinds:

- a command, ``<id>.json``: ``{"name", "description", "needs", "rescan",
  "argv"}``, ``argv`` a list run as it is (see substitute), its first item
  a downloader (found through jobs.tool_path) or an absolute path;
- a shell script, ``<id>.sh``: an executable file with an absolute ``#!``,
  and a header of comment lines (``# needs: url``). It is run as the file
  itself; its inputs are FV_* environment variables only.

``needs`` (target, url or none) says which input it takes; ``rescan`` the
folder indexed once it has run.

Read strictly, and listed with the reason when refused, never run: the
folder must be a folder (no symlink), ours, not writable by group or
others, nor its parent (unless sticky); a file must be a regular file
directly in it (opened without following a symlink, checked again on what
was opened), ours, not writable by group or others, at most SIZE_MAX,
named ``[a-z0-9_-]{1,64}`` + ``.json`` / ``.sh``; a shell script
executable. Two files with one id are both refused. Built-in templates (``builtin:<name>``)
are the commands downloaders.py and sync.py run, written as such a file
would be: read-only, runnable, to copy.
"""
import contextlib
import hashlib
import json
import os
import re
import stat
import threading
from urllib.parse import urlsplit

import archives
import config
import db
import health
import jobs
import sources
import sync

DIR_NAME = "scripts"
SIZE_MAX = 64 * 1024                           # bytes of a script file
BUILTIN = "builtin:"
FILE_RE = re.compile(r"([a-z0-9_-]{1,64})\.(json|sh)")
ID_RE = sources.SCRIPT_ID_RE
KINDS = {"json": "command", "sh": "shell"}
NEEDS = ("target", "url", "none")
PLACEHOLDERS = ("target", "url", "root", "data_dir", "archive")
_PLACEHOLDER_RE = re.compile(r"\{(target|url|root|data_dir|archive)\}")
COMMAND_KEYS = ("name", "description", "needs", "rescan", "argv")
HEADER_KEYS = ("name", "description", "needs", "rescan")
_HEADER_RE = re.compile(r"#\s*([a-z_]+)\s*:\s*(.*)")
ARGV_MAX = 200                                 # items of a command
ARG_MAX = 4096                                 # characters of one item
NAME_MAX = 100
DESCRIPTION_MAX = 500
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Read-only templates, from what downloaders.py and sync.py run (their
# flags that depend on a source's options left out). Placeholders other
# than FeedVault's ({profile}, %(id)s) are the tool's own.
YT_DLP_NAME = "{root}/" + sync.YT_DLP_NAME
BUILTINS = {
    "instaloader-profile": {
        "name": "instaloader: a profile",
        "description": "A profile's new posts, with metadata, stopping at the newest one already "
                       "downloaded (its time kept in FeedVault's stamps file).",
        "needs": "target", "rescan": "{root}",
        "argv": ["instaloader", "--latest-stamps", "{archive}", "--no-compress-json",
                 "--dirname-pattern", "{root}", "--", "{target}"]},
    "instaloader-saved": {
        "name": "instaloader: your saved posts",
        "description": "The posts saved by the account you are logged in as, each in a folder named "
                       "after its owner. Needs a login: add \"--login\", \"<you>\" in your copy.",
        "needs": "none", "rescan": "{root}",
        "argv": ["instaloader", "--no-compress-json", "--dirname-pattern", "{root}/{profile}", "--", ":saved"]},
    "instaloader-post": {
        "name": "instaloader: one post",
        "description": "One post by its shortcode (the part after /p/ in its link), in a folder named "
                       "after its owner.",
        "needs": "target", "rescan": "{root}",
        "argv": ["instaloader", "--no-compress-json", "--dirname-pattern", "{root}/{profile}", "--", "-{target}"]},
    "instaloader-stories": {
        "name": "instaloader: stories and highlights",
        "description": "A profile's current stories and its highlights, no posts. Instagram shows them "
                       "to logged-in viewers only: add \"--login\", \"<you>\" in your copy.",
        "needs": "target", "rescan": "{root}",
        "argv": ["instaloader", "--no-posts", "--no-profile-pic", "--stories", "--highlights",
                 "--no-compress-json", "--dirname-pattern", "{root}", "--", "{target}"]},
    "gallery-dl-user": {
        "name": "gallery-dl: a profile's media",
        "description": "Everything on a profile link not in gallery-dl's download archive yet (FeedVault's), "
                       "with metadata, stopping after 5 files in a row it already has.",
        "needs": "url", "rescan": "{root}",
        "argv": ["gallery-dl", "--write-metadata", "--download-archive", "{archive}", "-o", "skip=abort:5",
                 "-D", "{root}", "--", "{url}"]},
    "gallery-dl-url": {
        "name": "gallery-dl: one link",
        "description": "Whatever one link holds (a post, a gallery), with metadata.",
        "needs": "url", "rescan": "{root}",
        "argv": ["gallery-dl", "--write-metadata", "-D", "{root}", "--", "{url}"]},
    "yt-dlp-video": {
        "name": "yt-dlp: one video",
        "description": "One video, with its info JSON and thumbnail; never the playlist around it.",
        "needs": "url", "rescan": "{root}",
        "argv": ["yt-dlp", "--write-info-json", "--write-thumbnail", "--no-playlist",
                 "-o", YT_DLP_NAME, "--", "{url}"]},
    "yt-dlp-channel": {
        "name": "yt-dlp: a channel or profile",
        "description": "Every video of a channel or profile not in yt-dlp's download archive yet "
                       "(FeedVault's), stopping at the first one it has.",
        "needs": "url", "rescan": "{root}",
        "argv": ["yt-dlp", "--write-info-json", "--write-thumbnail", "--download-archive", "{archive}",
                 "--break-on-existing", "-o", YT_DLP_NAME, "--", "{url}"]},
}
# A shell script to start from: shown to copy, never run (it is no file).
SHELL_TEMPLATE = """#!/bin/sh
# name: My script
# description: What it does, shown in FeedVault.
# needs: url
# rescan: {root}
#
# Its inputs are environment variables only, never pasted into this text:
# FV_TARGET FV_URL FV_ROOT FV_DATA_DIR FV_ARCHIVE. Quote them: "$FV_URL".
set -eu
echo "downloading $FV_URL into $FV_ROOT"
"""


def scripts_dir():
    return os.path.join(os.path.dirname(config.config_path()), DIR_NAME)


def _clean_text(value, limit):
    return isinstance(value, str) and len(value) <= limit and not _CONTROL.search(value)


def _used(texts):
    return {m.group(1) for t in texts if isinstance(t, str) for m in _PLACEHOLDER_RE.finditer(t)}


def _check_common(meta):
    """Why ``meta`` (name, description, needs, rescan) cannot be, else None."""
    if meta.get("needs") not in NEEDS:
        return f"needs must be one of: {', '.join(NEEDS)}"
    if meta.get("name") is not None and not (_clean_text(meta["name"], NAME_MAX) and meta["name"].strip()):
        return f"name must be text of 1 to {NAME_MAX} characters on one line"
    if meta.get("description") is not None and not _clean_text(meta["description"], DESCRIPTION_MAX):
        return f"description must be text of at most {DESCRIPTION_MAX} characters on one line"
    rescan = meta.get("rescan")
    if rescan is not None and not (_clean_text(rescan, ARG_MAX) and rescan.strip()):
        return "rescan must be a folder (a template like {root}/inbox), or null"
    return None


def _check_needs(meta, texts):
    """A placeholder used that ``needs`` does not provide."""
    used = _used(texts)
    for name in ("target", "url"):
        if name in used and meta["needs"] != name:
            return f"uses {{{name}}} but needs {meta['needs']}"
    return None


# The options whose value a tool hands to a shell, evaluates as Python or
# splits into another program's arguments, from their source (yt-dlp
# 2026.08.19, gallery-dl's master): a FeedVault placeholder in one would be
# read as code (a link holding $(...), a folder holding spaces and an
# option), so a command holding one is refused. yt-dlp: --exec,
# --exec-before-download, --netrc-cmd (Popen(shell=True)), --use-postprocessor
# (its Exec post processor runs exec_cmd the same way); --downloader-args and
# --postprocessor-args (shlex.split, then aria2c's or ffmpeg's argv). gallery-dl:
# --exec, --exec-after, and -o / -O, whose KEY=VALUE can set an exec post
# processor's command (a string runs in a shell); the --filter options
# (eval). Not in it: yt-dlp's --downloader (a program's name or path), its
# --match-filters (its own syntax, never eval).
# Both take a long option's unique prefix (optparse, argparse): any prefix
# counts, unless it is another option's whole name (yt-dlp's --netrc,
# gallery-dl's --postprocessor). gallery-dl's -o and -O may follow its
# flags in one item (-qo…), their value after them or in the next item.
# gallery-dl's options whose value is a format string (formatter.parse):
# one starting with \f<kind> and a space picks another formatter (E a
# Python expression, F an f-string, J Jinja, M a module's function, T, TF,
# TJ a template file), so a placeholder in it would be read as code or as
# a file's path; refused there only. -f (path.py, "\\f" read as \f too by
# __init__.py), --rename, --rename-to (rename.py), and the [EVENT:]FORMAT of
# -N, --Print, --print-to-file, --Print-to-file (PrintAction, "\\f" too;
# split at the first ":", which a placeholder before it may move, so the
# text after any ":" counts), whose FILE's name is a format string too
# (metadata.py). Not in it: -D (a path), --mtime (its NAME put in {…}).
SHELL = "a shell"
PYTHON = "Python (gallery-dl evaluates it)"
SPLIT = "another program's arguments, split at spaces"
FORMAT, PRINT, PRINT_FILE, FILE = "format", "print", "print to file", "file"
FORMATS = (FORMAT, PRINT, PRINT_FILE, FILE)
_FORMATTER = ("\f", "\\f")
SHELL_RUN = {
    "yt-dlp": ({"--exec": SHELL, "--exec-before-download": SHELL, "--netrc-cmd": SHELL,
                "--use-postprocessor": SHELL, "--downloader-args": SPLIT, "--external-downloader-args": SPLIT,
                "--postprocessor-args": SPLIT, "--ppa": SPLIT},
               ("--netrc", "--downloader", "--external-downloader"), {}, "",
               "yt-dlp's own fields (%(webpage_url)q, %(filepath)q)"),
    "gallery-dl": ({"--exec": SHELL, "--exec-after": SHELL, "--option": SHELL, "--postprocessor-option": SHELL,
                    "--filter": PYTHON, "--post-filter": PYTHON, "--child-filter": PYTHON,
                    "--file-filter": PYTHON, "--image-filter": PYTHON, "--chapter-filter": PYTHON,
                    "--filename": FORMAT, "--rename": FORMAT, "--rename-to": FORMAT, "--print": PRINT,
                    "--Print": PRINT, "--print-to-file": PRINT_FILE, "--Print-to-file": PRINT_FILE},
                   ("--postprocessor",), {"o": SHELL, "O": SHELL, "f": FORMAT, "N": PRINT}, "hqwvgGjJsEKSU46",
                   "gallery-dl's own fields ({_path}, {_directory}), -D {root}, --download-archive {archive}"),
}
# yt-dlp's --alias puts what follows it into the options it expands to (--exec too).
ALIAS = "--alias"
# A shell's -c text is read as code; env runs the program after its own
# options (-S and --split-string split a text into it).
SHELLS = ("sh", "bash", "dash", "zsh", "ksh", "mksh", "ash", "fish")
# env reads its options as getopt does (+a:C:iS:u:v0): a short item is a
# cluster of flags (i, v, 0) that may end in S (split) or in an option that
# takes a value (u, C, a), the rest of the item or else the next one; a
# long one may be a unique prefix.
_ENV_LONGS = {"--split-string": "S", "--unset": "u", "--chdir": "C", "--argv0": "a"}
_ENV_SHORTS = "SuCa"
_ENV_FLAGS = "iv0"


def _shell_option(a, longs, others, shorts, flags, argparse=False):
    """(the option ``a`` names, its value or None when the value is the
    next item) when ``a`` names one of ``longs`` or ``shorts``, else None.
    ``argparse``: a short option's joined value drops one "=" before it
    (-f=… is the value "…"; optparse and getopt keep it)."""
    if a.startswith("--"):
        name, eq, value = a.partition("=")
        if name in longs:
            return name, value if eq else None
        if len(name) > 2 and name not in others:
            for o in longs:
                if o.startswith(name):
                    return o, value if eq else None
        return None
    if a.startswith("-"):
        for i, c in enumerate(a[1:], 1):
            if c in shorts:
                value = a[i + 1:]
                if argparse and value.startswith("=") and len(value) > 1:
                    value = value[1:]
                return "-" + c, value or None
            if c not in flags:
                return None
    return None


def _program(argv):
    """(the index of the program a command runs, past env and its options,
    or None when env splits a text into it: -S)."""
    i = 0
    while os.path.basename(argv[i]) == "env":
        i += 1
        while i < len(argv) and (argv[i].startswith("-") or "=" in argv[i]):
            found = _shell_option(argv[i], _ENV_LONGS, (), _ENV_SHORTS, _ENV_FLAGS)
            i += 1
            if found and found[0] in ("--split-string", "-S"):
                return None
            if found and found[1] is None:
                i += 1
        if i >= len(argv):
            return len(argv) - 1
    return i


def _check_sh(argv):
    """Why a placeholder would be in a shell's -c text, else None: the first
    item after its options (-o, -O, --rcfile and --init-file take the next
    one), once one of them holds c."""
    run, value = False, False
    for a in argv:
        if _used([a]) and (value or a.startswith(("-", "+"))):
            return f"{a!r}: a placeholder may not be among a shell's options"
        if value:
            value = False
        elif a.startswith("--"):
            value = a in ("--rcfile", "--init-file")
        elif a.startswith(("-", "+")):
            run = run or "c" in a
            value = "o" in a or "O" in a
        else:
            break
    else:
        return None
    if run and _used([a]):
        return (f"a shell's -c text is read as code, so it may not hold a FeedVault placeholder: pass it "
                "after the text (sh -c '… \"$1\"' sh {url}), or use a shell script (its inputs are FV_* variables)")
    return None


def _formatter(value, how):
    """Whether gallery-dl may read ``value``, a format string, with another
    formatter than its plain one (see SHELL_RUN)."""
    if how == FILE:
        return "\f" in value
    starts = [value] if how == FORMAT else [value, *value.split(":")[1:]]
    return any(s.startswith(_FORMATTER) for s in starts)


def _check_shell(argv):
    """Why a FeedVault placeholder would be read as code through one of the
    tool's options, else None."""
    i = _program(argv)
    if i is None:
        return "env -S splits its text into a command: not in a command with a FeedVault placeholder" \
            if _used(argv) else None
    tool, args = os.path.basename(argv[i]), argv[i + 1:]
    if tool in SHELLS:
        return _check_sh(args)
    if tool not in SHELL_RUN:
        return None
    longs, others, shorts, flags, fields = SHELL_RUN[tool]
    pending = None
    for a in args:
        name = a.partition("=")[0]
        if tool == "yt-dlp" and len(name) > 2 and ALIAS.startswith(name) and _used(argv):
            return (f"{ALIAS} carries what follows it into the options it expands to, a shell's too: "
                    "not in a command with a FeedVault placeholder (use a shell script)")
        if pending:
            (option, how), value, pending = pending, a, None
        else:
            found = _shell_option(a, longs, others, shorts, flags, tool == "gallery-dl")
            if found is None:
                continue
            option, value = found
            how = longs.get(option) or shorts[option[1]]
            if value is None:
                pending = option, how
                continue
        if how == PRINT_FILE:
            pending = f"{option}'s FILE", FILE
        used = _used([value])
        if not used or how in FORMATS and not _formatter(value, how):
            continue
        found = f"found {{{sorted(used)[0]}}} in {value!r}"
        if how in FORMATS:
            what = option if how == FILE else f"{option}'s value"
            return (f"{what} is a format string that gallery-dl evaluates as Python or reads "
                    "as a template file when it starts with \\f, so there it may not hold a FeedVault "
                    f"placeholder: use {fields}, or a shell script (its inputs are FV_* variables); {found}")
        return (f"{option}'s value can reach {how}, so it may not hold a FeedVault placeholder: use "
                f"{fields}, or a shell script (its inputs are FV_* variables); {found}")
    return None


def parse_command(text):
    """(fields, None) of a command's JSON text, or (None, why it is refused)."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as e:
        return None, f"not valid JSON: {e}"
    if not isinstance(data, dict):
        return None, "must be a JSON object"
    unknown = sorted(set(data) - set(COMMAND_KEYS))
    if unknown:
        return None, f"unknown key {unknown[0]!r}: keys are {', '.join(COMMAND_KEYS)}"
    argv = data.get("argv")
    if not isinstance(argv, list) or not 1 <= len(argv) <= ARGV_MAX \
            or not all(isinstance(a, str) and len(a) <= ARG_MAX and "\0" not in a for a in argv):
        return None, f"argv must be a list of 1 to {ARGV_MAX} strings"
    tool = argv[0]
    if tool not in jobs.TOOLS and not (os.path.isabs(tool) and not _used([tool])):
        return None, (f"argv[0] must be one of {', '.join(jobs.TOOLS)} (found as in Settings → Downloaders), "
                      "or an absolute path to a program")
    error = _check_common(data) or _check_needs(data, [*argv, data.get("rescan")]) or _check_shell(argv)
    if error:
        return None, error
    return {"name": data.get("name"), "description": data.get("description"), "needs": data["needs"],
            "rescan": data.get("rescan"), "tool": tool, "argv": list(argv)}, None


def parse_shell(text):
    """(fields, None) of a shell script's text, or (None, why it is refused):
    an absolute ``#!`` first, then its header, comment lines up to the
    first that is not one."""
    lines = text.split("\n")
    shebang = lines[0][2:].strip().split() if lines[0].startswith("#!") else []
    if not shebang or not os.path.isabs(shebang[0]):
        return None, "the first line must be #! and an absolute path (#!/bin/sh)"
    meta = {}
    for line in lines[1:]:
        if not line.startswith("#"):
            break
        m = _HEADER_RE.fullmatch(line.rstrip("\r"))
        if m and m.group(1) in HEADER_KEYS:
            if m.group(1) in meta:
                return None, f"# {m.group(1)}: is there twice"
            meta[m.group(1)] = m.group(2).strip()
    if "needs" not in meta:
        return None, "the header must say what it needs: # needs: target, url or none"
    error = _check_common(meta) or _check_needs(meta, [meta.get("rescan")])
    if error:
        return None, error
    return {"name": meta.get("name") or None, "description": meta.get("description") or None,
            "needs": meta["needs"], "rescan": meta.get("rescan") or None, "tool": None, "argv": None}, None


def _builtin(name):
    t = BUILTINS[name]
    return {"id": BUILTIN + name, "builtin": True, "kind": "command", "file": None, "path": None,
            "name": t["name"], "description": t["description"], "needs": t["needs"], "rescan": t["rescan"],
            "tool": t["argv"][0], "argv": list(t["argv"]), "refused": None,
            "sha256": hashlib.sha256(template(name).encode()).hexdigest(),
            "size": None, "mtime": None}


def _entry(name, folder):
    m = FILE_RE.fullmatch(name)
    sid = m.group(1) if m else name
    return {"id": sid, "builtin": False, "kind": KINDS[m.group(2)] if m else None, "file": name,
            "path": os.path.join(folder, name), "name": None, "description": None, "needs": None,
            "rescan": None, "tool": None, "argv": None, "refused": None, "sha256": None, "size": None,
            "mtime": None}


def _folder_refused(st, what, sticky_ok=False):
    """Why a folder (its lstat) may not hold scripts, else None."""
    if stat.S_ISLNK(st.st_mode):
        return f"{what} is a symlink"
    if not stat.S_ISDIR(st.st_mode):
        return f"{what} is not a folder"
    if st.st_uid != os.getuid():
        return f"{what} belongs to another user"
    # A sticky folder (/tmp) lets nobody else rename or remove what is ours.
    if st.st_mode & 0o022 and not (sticky_ok and st.st_mode & stat.S_ISVTX):
        return f"{what} is writable by group or others (chmod go-w)"
    return None


def _file_refused(st, kind):
    """Why a file (its stat) may not run, else None."""
    if stat.S_ISLNK(st.st_mode):
        return "a symlink: a script must be a regular file in the folder itself"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if st.st_uid != os.getuid():
        return "belongs to another user"
    if st.st_mode & 0o022:
        return "writable by group or others (chmod go-w)"
    if st.st_size > SIZE_MAX:
        return f"larger than {SIZE_MAX // 1024} KiB"
    if kind == "shell" and not st.st_mode & stat.S_IXUSR:
        return "not executable (chmod u+x)"
    return None


def _read(name, folder, dir_fd):
    """(script dict, raw bytes or None) of one name in the folder (open as
    ``dir_fd``). The file is opened without following a symlink, and
    checked again on what was opened."""
    out = _entry(name, folder)
    if out["kind"] is None:
        out["refused"] = "the name must be [a-z0-9_-] (at most 64), then .json or .sh"
        return out, None
    try:
        before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        out["refused"] = _file_refused(before, out["kind"])
        if out["refused"]:
            return out, None
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            out["refused"] = _file_refused(st, out["kind"])
            if (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino):
                out["refused"] = "it changed while it was read"
            if out["refused"]:
                return out, None
            raw = f.read(SIZE_MAX + 1)
    except OSError as e:
        out["refused"] = f"cannot be read: {e.strerror or e}"
        return out, None
    if len(raw) > SIZE_MAX:
        out["refused"] = f"larger than {SIZE_MAX // 1024} KiB"
        return out, None
    out.update(size=len(raw), mtime=int(st.st_mtime), sha256=hashlib.sha256(raw).hexdigest())
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        out["refused"] = "not UTF-8 text"
        return out, raw
    fields, error = (parse_command if out["kind"] == "command" else parse_shell)(text)
    if error:
        out["refused"] = error
    else:
        out.update(fields)
        out["name"] = out["name"] or out["id"]
    return out, raw


def _files():
    """(the folder's refusal or None, [(script dict, raw bytes or None)] by
    name). Nothing is listed from a folder that is refused: a symlink,
    someone else's, writable by others, or in a parent others can write to."""
    folder = scripts_dir()
    parent = os.path.dirname(folder)
    try:
        st = os.lstat(folder)
        refused = _folder_refused(st, "the scripts folder") \
            or _folder_refused(os.stat(parent), f"its parent folder ({parent})", sticky_ok=True)
        if refused:
            return refused, []
        dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None, []
    except OSError as e:
        return f"the scripts folder cannot be read: {e.strerror or e}", []
    try:
        opened = os.fstat(dir_fd)
        if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
            return "the scripts folder changed while it was read", []
        found = [_read(n, folder, dir_fd) for n in sorted(os.listdir(dir_fd))]
    except OSError as e:
        return f"the scripts folder cannot be read: {e.strerror or e}", []
    finally:
        os.close(dir_fd)
    ids = {}
    for s, _ in found:
        ids.setdefault(s["id"], []).append(s)
    for same in ids.values():
        if len(same) > 1:
            for s in same:
                s["refused"] = f"two files have the id {s['id']}: {', '.join(x['file'] for x in same)}"
    return None, found


_once = threading.local()                      # .found: [] or [(refusal, files)] while read_once holds


@contextlib.contextmanager
def read_once():
    """Within it, this thread reads the scripts folder at most once, and
    every lookup gets that read (Sync all: one for all its sources). A
    script sync's start, in its job's thread, still reads it again."""
    if getattr(_once, "found", None) is not None:
        yield
        return
    _once.found = []
    try:
        yield
    finally:
        _once.found = None


def _read_folder():
    """_files(), or the read already made under read_once."""
    found = getattr(_once, "found", None)
    if found is None:
        return _files()
    if not found:
        found.append(_files())
    return found[0]


def listing():
    """{dir, dir_refused, shell_template, scripts}: the built-ins, then the
    files by name."""
    refused, found = _files()
    return {"dir": scripts_dir(), "dir_refused": refused, "shell_template": SHELL_TEMPLATE,
            "scripts": [_builtin(n) for n in BUILTINS] + [s for s, _ in found]}


def lookup(sid, content=False):
    """(one script by id, read now, or None; the scripts folder's refusal
    or None). ``content``: with its text (the file's, or a built-in's JSON
    as a file would hold it)."""
    if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
        return None, None
    if sid.startswith(BUILTIN):
        name = sid[len(BUILTIN):]
        if name not in BUILTINS:
            return None, None
        out = _builtin(name)
        if content:
            out["content"] = template(name)
        return out, None
    refused, found = _read_folder()
    for s, raw in found:
        if s["id"] == sid and s["kind"] is not None:
            s = dict(s)                        # its own: the read may be read_once's, shared
            if content:
                s["content"] = raw.decode("utf-8", "replace") if raw is not None else None
            return s, None
    return None, refused


def get(sid, content=False):
    """One script by id, read now, or None (see lookup)."""
    return lookup(sid, content)[0]


def missing(sid, refused):
    """Why there is no script ``sid``: its folder's refusal, else that it is not there."""
    return f"{refused}, so nothing in it runs" if refused else f"no script {sid} (in {scripts_dir()})"


def template(name):
    """A built-in as the JSON file to create from it."""
    return json.dumps(BUILTINS[name], indent=2) + "\n"


# ---------------------------------------------------------------------------
# Inputs and values
# ---------------------------------------------------------------------------

URL_MAX = 500
TEXT_MAX = 200
# instaloader's: a profile name, or a post's shortcode (the built-in passes -<shortcode>).
_INSTALOADER_TARGET = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]{0,39}")
_URL_TEXT = re.compile(r"[^\s\x00-\x1f\x7f]+")


def check_url(value):
    """``value`` when it is an http(s) link of at most URL_MAX characters
    without spaces or control characters, else None. Anything else in it
    ($, backticks, ;) is passed on as it is: it is never read by a shell."""
    if not isinstance(value, str) or len(value) > URL_MAX or not _URL_TEXT.fullmatch(value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return None
    return value if parts.scheme.lower() in ("http", "https") and host else None


def program(script):
    """The name of the program a command runs (a downloader named by its
    path or behind env too), else the script's tool."""
    argv = script.get("argv")
    i = _program(argv) if script["kind"] == "command" and argv else None
    return script["tool"] if i is None else os.path.basename(argv[i])


def check_target(script, value):
    """(target, None) when ``value`` is one for the program the script runs
    (instaloader: a profile name or shortcode; gallery-dl and yt-dlp: a
    link; anything else: text without control characters), else (None,
    why). Never starting with "-": no target reads as an option."""
    tool = program(script)
    if tool == "instaloader":
        if isinstance(value, str) and _INSTALOADER_TARGET.fullmatch(value):
            return value, None
        return None, "target must be a profile name or a post's shortcode (letters, digits, . _ -)"
    if tool in ("gallery-dl", "yt-dlp"):
        if check_url(value):
            return value, None
        return None, f"target must be an http(s) link for {tool}"
    if isinstance(value, str) and 1 <= len(value) <= TEXT_MAX and not _CONTROL.search(value) \
            and not value.startswith("-"):
        return value, None
    return None, f"target must be text of 1 to {TEXT_MAX} characters on one line, not starting with -"


def archive(script, data_dir):
    """{archive}: the file the tool keeps what it has in, FeedVault's own."""
    if script["tool"] == "instaloader":
        return sync.stamps_path({"data_directory": data_dir})
    if script["tool"] in ("gallery-dl", "yt-dlp"):
        return archives.path(script["tool"], data_dir)
    return os.path.join(data_dir, DIR_NAME, script["id"].replace(BUILTIN, "") + ".archive")


def values(script, cfg, root, target=None, url=None):
    """The placeholders' values, each one text."""
    return {"target": target or "", "url": url or "", "root": root, "data_dir": cfg["data_directory"],
            "archive": archive(script, cfg["data_directory"])}


def substitute(text, vals):
    """``text`` with FeedVault's placeholders replaced, in one pass (a value
    holding "{root}" stays as it is); every other {…} is left."""
    return _PLACEHOLDER_RE.sub(lambda m: vals[m.group(1)], text)


# What a shell script's environment keeps of FeedVault's, besides its FV_* inputs.
SHELL_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "USER", "LOGNAME", "TMPDIR",
             "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")


def shell_env(vals):
    env = {k: os.environ[k] for k in SHELL_ENV if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env.update({"FV_" + k.upper(): v for k, v in vals.items()})
    return env


def group(script):
    """A downloader's command runs in that tool's lock group (never beside
    a sync of it); anything else in "scripts"."""
    return script["tool"] if script["tool"] in jobs.TOOLS else "scripts"


def _rescan(script, vals, roots):
    """The declared folder, filled in and made: inside a media root, else BadRequest."""
    if script["rescan"] is None:
        return None
    folder = substitute(script["rescan"], vals)
    inside = sources.inside_root(folder, roots)
    if inside is None:
        raise jobs.BadRequest(f"the script's rescan folder is not inside a media root: {folder}")
    try:
        os.makedirs(inside, exist_ok=True)
    except OSError as e:
        raise jobs.BadRequest(f"cannot create {inside}: {e.strerror or e}")
    return inside


# The options whose value a tool formats (instaloader: str.format, yt-dlp:
# %, gallery-dl: its formatter, str.format's syntax), and how a value put in
# one is escaped, as sync.py does for its own. The value is the next item,
# or in the same one: after "=" for a long option (--output=…), right after
# a short one (-o…; -o=… is the value "=…"), which gallery-dl's flags may
# come before (-qf…). instaloader and gallery-dl (argparse) also take a long
# option's unique prefix (--dirname); yt-dlp's are never unique
# (--output-na-placeholder, --exec-before-download). gallery-dl's
# --print-to-file FILE is left as it is: its folder is no format string.
FORMATTED = {"instaloader": (("--dirname-pattern", "--filename-pattern", "--title-pattern"), (), True,
                             sync._escape, ""),
             "yt-dlp": (("--output", "--exec"), ("-o",), False, lambda v: v.replace("%", "%%"), ""),
             "gallery-dl": (("--filename", "--rename", "--rename-to", "--print", "--Print", "--print-to-file",
                             "--Print-to-file"), ("-f", "-N"), True,
                            lambda v: v.replace("{", "{{").replace("}", "}}"), SHELL_RUN["gallery-dl"][3])}


def _formatted(a, longs, shorts, prefixes, flags):
    """(the option part of ``a``, its value or None when the value is the
    next item) when ``a`` names an option its tool formats, else None."""
    name, eq, value = a.partition("=")
    if len(name) > 2 and name.startswith("--") \
            and any(o == name or (prefixes and o.startswith(name)) for o in longs):
        return name + eq, value if eq else None
    if a.startswith("-") and not a.startswith("--"):
        for i, c in enumerate(a[1:], 1):
            if "-" + c in shorts:
                return a[:i + 1], a[i + 1:] or None
            if c not in flags:
                return None
    return None


def command(script, vals):
    """A command's argument list, its placeholders filled in: escaped in
    the value of an option its tool formats."""
    longs, shorts, prefixes, escape, flags = FORMATTED.get(script["tool"], ((), (), False, None, ""))
    escaped = {k: escape(v) for k, v in vals.items()} if escape else vals
    argv, formatted = [], False
    for a in script["argv"]:
        option = None if formatted else _formatted(a, longs, shorts, prefixes, flags)
        if formatted:
            argv.append(substitute(a, escaped))
        elif option and option[1] is not None:
            argv.append(option[0] + substitute(option[1], escaped))
        else:
            argv.append(substitute(a, vals))
        formatted = option is not None and option[1] is None
    return argv


def _spec(script, vals, cfg, rescan):
    cwd = os.path.join(cfg["data_directory"], DIR_NAME)
    if script["kind"] == "command":
        argv = command(script, vals)
        return {"tool": argv[0], "args": argv[1:], "cwd": cwd, "rescan": rescan, "group": group(script)}
    return {"tool": script["path"], "args": [], "env": shell_env(vals), "cwd": cwd, "rescan": rescan,
            "group": group(script)}


def runnable(sid, sha256=None):
    """The script ``sid``, read now, when it may run (and is still the one
    whose SHA-256 is ``sha256``, when given). Raises jobs.BadRequest."""
    script, refused = lookup(sid)
    if script is None:
        raise jobs.BadRequest(missing(sid, refused))
    return _usable(script, sha256)


def _usable(script, sha256=None):
    """``script`` (read already) when it may run, else jobs.BadRequest."""
    if script["refused"]:
        raise jobs.BadRequest(f"{script['file'] or script['id']} is refused: {script['refused']}")
    if script["kind"] == "shell" and script["builtin"]:
        raise jobs.BadRequest("a template only: copy it into a file to run it")
    if sha256 is not None and script["sha256"] != sha256:
        raise jobs.BadRequest(f"{script['file']} changed since it was queued: run it again")
    return script


def _say(script, vals, note):
    """The log's first lines: what runs, and a shell script's inputs."""
    if script["builtin"]:
        note(f"built-in script {script['id']}")
    else:
        note(f"script {script['path']} (sha256 {script['sha256'][:16]}…)")
    if script["kind"] == "shell":
        for k, v in vals.items():
            note(f"FV_{k.upper()}={v}")


# ---------------------------------------------------------------------------
# Running one on its own (POST /api/scripts/<id>/run)
# ---------------------------------------------------------------------------

KIND = "script"
INPUTS = ("target", "url", "folder")


def _root(folder, cfg):
    roots = cfg["media_roots"]
    if not roots:
        raise jobs.BadRequest("add a media root in Settings first")
    if folder is None:
        return os.path.normpath(roots[0])
    inside = sources.inside_root(folder, roots)
    if inside is None:
        raise jobs.BadRequest("folder must be an absolute path inside a media root")
    return inside


def _inputs(script, params):
    """(target, url) from a run's params, checked for the script."""
    target = url = None
    for name in ("target", "url"):
        if params.get(name) is not None and script["needs"] != name:
            raise jobs.BadRequest(f"this script takes no {name} (it needs {script['needs']})")
    if script["needs"] == "target":
        target, error = check_target(script, params.get("target"))
        if error:
            raise jobs.BadRequest(error)
    if script["needs"] == "url":
        url = check_url(params.get("url"))
        if url is None:
            raise jobs.BadRequest(f"url must be an http(s) link of at most {URL_MAX} characters, without spaces")
    return target, url


def _vals(script, params, cfg):
    target, url = _inputs(script, params)
    root = _root(params.get("folder"), cfg)
    # Both expand $NAME in the folder they are given (as sync._archive_source says).
    tool = program(script)
    if tool in ("gallery-dl", "yt-dlp") and "$" in root:
        raise jobs.BadRequest(f"the folder holds a $, which {tool} would expand")
    return values(script, cfg, root, target, url)


def _build(params):
    script = runnable(params["script"], params.get("sha256"))
    cfg = config.load()
    vals = _vals(script, params, cfg)
    os.makedirs(os.path.join(cfg["data_directory"], DIR_NAME), exist_ok=True)
    return _spec(script, vals, cfg, _rescan(script, vals, cfg["media_roots"]))


def _check(params, note):
    """Right before it starts: the script is read again, and must be the
    one it was queued with."""
    try:
        script = runnable(params["script"], params.get("sha256"))
    except jobs.BadRequest as e:
        note(str(e))
        raise
    _say(script, _vals(script, params, config.load()), note)


def _tool_pause(tool):
    """The pause after a sync of ``tool``; none for another program."""
    if tool == "instaloader":
        return sync.settings()["pause"]
    return sync.tool_settings(tool)["pause"] if tool in sources.TOOLS else 0


def _pause(params):
    """A downloader's command pauses as that tool's syncs do."""
    try:
        return _tool_pause((get(params["script"]) or {}).get("tool"))
    except Exception:                          # reads files: never left holding the queue
        return 0


def run(sid, inputs):
    """Queue a run of script ``sid`` with ``inputs`` ({target, url, folder},
    each optional): the job's public dict. Raises LookupError for an
    unknown id, jobs.BadRequest for a refused script or bad inputs."""
    script, refused = lookup(sid)
    if script is None:
        if refused:
            raise jobs.BadRequest(missing(sid, refused))
        raise LookupError(sid)
    _usable(script)
    params = {"script": sid, **{k: inputs[k] for k in INPUTS if inputs.get(k) not in (None, "")}}
    if script["sha256"]:
        params["sha256"] = script["sha256"]
    return jobs.submit(KIND, params)


jobs.register(KIND, label="Run a script",
              params={"script": {"type": "text", "max": 80},
                      "target": {"type": "text", "max": URL_MAX + 100, "required": False},
                      "url": {"type": "text", "max": URL_MAX + 100, "required": False},
                      "folder": {"type": "text", "max": 4096, "required": False},
                      "sha256": {"type": "text", "max": 64, "required": False}},
              build=_build, group="scripts", check=_check, pause=_pause, scrub=("argv", "params"),
              describe=lambda params, argv: f"Script {params.get('script', '')}")


# ---------------------------------------------------------------------------
# A source's script (its option "script"), run by its Sync instead of the tool's command
# ---------------------------------------------------------------------------

SYNC_KIND = sync.SCRIPT_KIND


WHY_MAX = 500                            # a refusal's reason, kept in a script sync's params


def sync_params(sid, src):
    """The params a source's script sync is queued with (sync._job): the
    script's id and SHA-256 now, or why it cannot run (missing or refused:
    the run then fails with that reason), and the source's target."""
    base = {"script": sid, "target": src["target"]}
    try:
        return {**base, "sha256": runnable(sid)["sha256"]}
    except jobs.BadRequest as e:
        return {**base, "why": health.scrub(str(e))[:WHY_MAX]}


def _source(params):
    """The source row a script sync is for, still the one it was queued for."""
    src = sources.row(db.connect(), sync._source_id(params))
    if src is None:
        raise jobs.BadRequest("no such source")
    if src["target"] != params["target"]:
        raise jobs.BadRequest("the source changed since its sync was queued")
    return src


def _source_values(script, src, cfg):
    """The placeholders' values for a source: its target, its link, its folder."""
    folder = sources.inside_root(src["folder"], cfg["media_roots"])
    url = src["target"] if src["tool"] != "instaloader" else db.profile_url(src["platform"], src["target"])
    return values(script, cfg, folder, src["target"], url)


def _sync_build(params):
    """As the tool's own sync checks a source (stored data is checked again:
    sources.json can be edited by hand), then the script's command. A
    script missing or refused is not an error here: the run fails with
    the reason (_sync_check), so the source and its schedule see it."""
    src = _source(params)
    if src["tool"] != "instaloader":
        # gallery-dl's and yt-dlp's checks are their sync's own (a $ in the folder too).
        src, _, folder, cfg = sync._archive_source(params, src["tool"])
    else:
        cfg = config.load()
        target = sources.parse_target("instaloader", src["target"])
        if target is None or target != src["target"]:
            raise jobs.BadRequest("the source's target is not a profile name")
        folder = sources.inside_root(src["folder"], cfg["media_roots"])
        if folder is None:
            raise jobs.BadRequest("the source's folder is not inside a media root")
        if sources.in_saved(folder, cfg["media_roots"]):
            raise jobs.BadRequest(sources.SAVED_REFUSED.format(folder=folder))
    roots = cfg["media_roots"]
    try:
        os.makedirs(folder, exist_ok=True)
        os.makedirs(os.path.join(cfg["data_directory"], DIR_NAME), exist_ok=True)
    except OSError as e:
        raise jobs.BadRequest(f"cannot create the source's folder: {e.strerror or e}")
    try:
        if "sha256" not in params:             # it could not run when queued: that run fails
            raise jobs.BadRequest(params.get("why"))
        script = runnable(params["script"], params["sha256"])
    except jobs.BadRequest as e:
        # The run fails at _sync_check, which sees no sha256: never this spec's program.
        if "sha256" in params:
            del params["sha256"]
            params["why"] = health.scrub(str(e))[:WHY_MAX]
        return {"tool": params["script"], "args": [], "rescan": folder, "group": src["tool"]}
    vals = _source_values(script, src, cfg)
    spec = _spec(script, vals, cfg, _rescan(script, vals, roots) or folder)
    return {**spec, "group": src["tool"]}


def _sync_check(params, note):
    """Right before it starts: the script is read again; missing, refused
    or changed since it was queued, the run fails and says why. Never the
    tool's own command instead."""
    # Gone, or its id names another source now: cancelled, as a tool's sync is.
    src = sync._queued_source(db.connect(), params, None)
    try:
        if "sha256" not in params:
            # Why it could not run when queued; if that is fixed by now, it says so.
            runnable(params["script"])
            why = params.get("why") or "it could not be run"
            raise jobs.BadRequest(f"{why}; that was when the sync was queued, it can run now: sync again")
        script = runnable(params["script"], params["sha256"])
    except jobs.BadRequest as e:
        message = f"the source's script: {e}"
        note(message)
        raise jobs.BadRequest(message)
    _say(script, _source_values(script, src, config.load()), note)


def _tool(params):
    """The source's tool: whose outcome, lock group and pause a script sync has."""
    try:
        src = sources.row(db.connect(), sync._source_id(params))
    except jobs.BadRequest:
        src = None
    return src["tool"] if src is not None else "instaloader"


_TOOL_START = {"instaloader": sync._start, "gallery-dl": sync._start_archive("gallery-dl"),
               "yt-dlp": sync._start_yt_dlp}


def _sync_start(params, note, argv=None):
    """What the tool's own sync does first (seed its stamps or archive, which
    {archive} names; gather saved posts; note the trash), its arguments
    kept: the script's."""
    _TOOL_START[_tool(params)](params, note, None)
    return None


def _sync_pause(params):
    return _tool_pause(_tool(params))


jobs.register(SYNC_KIND, label="Sync with a script",
              params={**sync.PARAMS, "script": {"type": "text", "max": 80},
                      "target": {"type": "text", "max": sources.URL_MAX},
                      "sha256": {"type": "text", "max": 64, "required": False},
                      "why": {"type": "text", "max": WHY_MAX, "required": False}},
              build=_sync_build, group=_tool, check=_sync_check, start=_sync_start, after=sync._strip_cookies,
              outcome=lambda p, code, lines, index, note: sync._outcome(p, code, lines, index, note, _tool(p)),
              ended=sync._ended, pause=_sync_pause, scrub=("argv",),
              describe=lambda params, argv: f"Sync {params.get('target', '').removeprefix('https://')} "
                                            f"with {params.get('script')}")
JOB_KINDS = (KIND, SYNC_KIND)
