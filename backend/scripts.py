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
  and a header of comment lines (``# needs: url``). It runs from the bytes
  whose SHA-256 was checked right before it starts, never from its file
  again: its ``#!`` interpreter is given them as /dev/fd/N (a sealed memfd,
  jobs.Script), so ``$0`` is /dev/fd/N and FV_SCRIPT its file's path. Its
  inputs are FV_* environment variables only.

``needs`` (target, url or none) says which input it takes; ``rescan`` the
folder indexed once it has run.

Read strictly, and listed with the reason when refused, never run: the
folder must be a folder (no symlink), ours, not writable by group or
others, and every folder above it (as written and as resolved) root's or
ours, not writable by group or others unless sticky; a file must be a regular file
directly in it (opened without following a symlink, checked again on what
was opened), ours, not writable by group or others, at most SIZE_MAX,
named ``[a-z0-9_-]{1,64}`` + ``.json`` / ``.sh``; a shell script
executable. Two files with one id are both refused. Built-in templates (``builtin:<name>``)
are the commands downloaders.py and sync.py run, written as such a file
would be: read-only, runnable, to copy.
"""
import collections
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
# $0 is /dev/fd/N (the bytes FeedVault checked); FV_SCRIPT is this file's path.
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


# Each tool's options that FeedVault reads, from their source (yt-dlp
# 2026.08.19, gallery-dl's master, instaloader's __main__.py), described
# once (TOOLS): the parse-time check (_check_shell), command()'s escaping
# and its path refusals all read them through one walker (_walk).
#
# What a value may be (its kinds; one option's value may have several):
# - SHELL, PYTHON, SPLIT: read as code, so a command with a FeedVault
#   placeholder there is refused. yt-dlp: --exec, --exec-before-download,
#   --netrc-cmd (Popen(shell=True)), --use-postprocessor (its Exec post
#   processor runs exec_cmd the same way); --downloader-args and
#   --postprocessor-args (shlex.split, then aria2c's or ffmpeg's argv).
#   gallery-dl: --exec, --exec-after, and -o / -O, whose KEY=VALUE can set
#   an exec post processor's command (a string runs in a shell); the
#   --filter options (eval). instaloader: --post-filter, --only-if,
#   --storyitem-filter (compiled and evaluated against a post: EVAL). Not
#   in it: yt-dlp's --downloader (a program's name or path), its
#   --match-filters (its own syntax, never eval).
# - FORMAT, PRINT, FILE: gallery-dl's format strings (formatter.parse): one
#   starting with \f<kind> and a space picks another formatter (E a Python
#   expression, F an f-string, J Jinja, M a module's function, T, TF, TJ a
#   template file), so a placeholder there is refused (_formatter). -f
#   (path.py, "\\f" read as \f too by __init__.py), --rename, --rename-to
#   (rename.py); PRINT: the [EVENT:]FORMAT of -N, --Print, --print-to-file,
#   --Print-to-file (PrintAction, "\\f" too; split at the first ":", which
#   a placeholder before it may move, so the text after any ":" counts);
#   FILE: the latter two's FILE (nargs=2), its folder a plain path that
#   gallery-dl makes and expands, its file name a format string
#   (metadata.py; _file_name). Not in it: -D (a path), --mtime (its NAME
#   put in {…}). A value put in one is escaped (braces doubled).
# - TEMPLATE: a value the tool fills in, escaped as sync.py escapes its
#   own (instaloader: str.format, yt-dlp: %): instaloader's
#   --dirname-pattern, --filename-pattern, --title-pattern; yt-dlp's -o,
#   --output, --exec, and --print-to-file's [WHEN:]TEMPLATE and FILE (output
#   templates: a link's %(a|..)s would be a .. once filled in).
# - PATH: a path the tool reads, writes or appends to (made when missing)
#   as it is, where a link's .. (and where the tool expands them, its ~ or
#   $) is refused (_check_path). gallery-dl (option.py; util.expand_path):
#   -d, --destination (base-directory, path.py), -D, --directory
#   (__init__.py: base-directory too), -e, --error-file, --write-log,
#   --write-unsupported (a logging FileHandler, output.py),
#   --download-archive (archive.py), -c, --config, --config-json,
#   --config-yaml, --config-toml (config.load), -C, --cookies,
#   --cookies-export (extractor/common.py), -i, -I, -x, --input-file,
#   --input-file-comment, --input-file-delete (the last two rewrite it),
#   -X, --extractors (modules loaded from it), --cache-file (cache.py).
#   yt-dlp (utils.expand_path): -P, --paths, -o, --output ([TYPES:] before
#   it, which a link never starts: "https" is no type), --print-to-file's
#   FILE (appended to), --download-archive, --cookies, -a, --batch-file,
#   --load-info-json, --cache-dir, --config-locations, --netrc-location,
#   --plugin-dirs (code loaded from it), --ffmpeg-location and
#   --js-runtimes' RUNTIME[:PATH] (a program run).
#   instaloader (no expansion): its three patterns (formatted, then made:
#   its own fields are sanitized, FeedVault's text is not),
#   --resume-prefix, --latest-stamps, -B, --cookiefile, -f, --sessionfile
#   (written). env: -C, --chdir (the folder the program runs in).
# - No kind: yt-dlp's --replace-in-metadata (nargs=3), followed so that a
#   "--" among its values ends nothing.
# - ALIAS: yt-dlp's --alias puts what follows it into the options it
#   expands to (--exec too): refused in a command with a placeholder.
SHELL = "a shell"
PYTHON = "Python (gallery-dl evaluates it)"
EVAL = "Python (instaloader evaluates it)"
SPLIT = "another program's arguments, split at spaces"
FORMAT, PRINT, FILE = "format", "print", "file"
PATH, TEMPLATE, ALIAS = "path", "template", "alias"
CODE = frozenset((SHELL, PYTHON, EVAL, SPLIT))
FORMATS = frozenset((FORMAT, PRINT, FILE))
ESCAPED = frozenset((FORMAT, PRINT, TEMPLATE))
_FORMATTER = ("\f", "\\f")
# A tool's options: {name: one tuple of kinds per value it takes} for its
# long ones and its short ones (by letter), an empty tuple for an "other":
# another option whose whole name is a prefix of one of those, itself
# (yt-dlp's --netrc, gallery-dl's --postprocessor), its values not
# followed. ``flags``: its short options that take no value, any of them
# before one that does in one item (-qo…, -io…). ``argparse``: a short
# option's joined value drops one "=" before it (-f=… is the value "…";
# optparse and getopt keep it). ``expands``: ~ and $NAME in a path.
# ``optional``: the options whose one value may be left out (argparse's
# nargs="?": taken only when the next item is no option, nor "--").
# Every one takes a long option's unique prefix (optparse's
# _match_long_opt, argparse, getopt_long); a prefix that names several
# (which the tool refuses) is checked as any of them and escaped only as
# what all of them are (yt-dlp's --output and --exec never have a unique
# prefix: --output-na-placeholder, --exec-before-download).
# After "--" no item is an option (optparse's _process_args, argparse's
# _parse_known_args: "--" and all after it positional); an option's value
# is its value though, "--" or an option's name too (optparse takes it,
# argparse refuses to run).
# These are the options FeedVault reads, not all of them: one missing that
# takes a value would put the walker out of step (yt-dlp --referer -P
# --exec …: -P is the referer). So what is refused (code, a format, a
# path) is also looked for as if every item naming an option were one,
# and past a "--" yt-dlp may read as such an option's value; what is
# escaped follows the one reading.
Tool = collections.namedtuple("Tool", "longs shorts flags argparse expands escape fields optional")
TOOLS = {
    "yt-dlp": Tool(
        {"--exec": ((SHELL, TEMPLATE),), "--exec-before-download": ((SHELL,),), "--netrc-cmd": ((SHELL,),),
         "--use-postprocessor": ((SHELL,),), "--downloader-args": ((SPLIT,),),
         "--external-downloader-args": ((SPLIT,),), "--postprocessor-args": ((SPLIT,),), "--ppa": ((SPLIT,),),
         "--alias": ((ALIAS,), (ALIAS,)), "--output": ((TEMPLATE, PATH),),
         "--print-to-file": ((TEMPLATE,), (TEMPLATE, PATH)), "--paths": ((PATH,),),
         "--download-archive": ((PATH,),), "--cookies": ((PATH,),), "--batch-file": ((PATH,),),
         "--load-info-json": ((PATH,),), "--cache-dir": ((PATH,),), "--config-locations": ((PATH,),),
         "--netrc-location": ((PATH,),), "--plugin-dirs": ((PATH,),), "--ffmpeg-location": ((PATH,),),
         "--js-runtimes": ((PATH,),), "--replace-in-metadata": ((), (), ()),
         "--netrc": (), "--downloader": (), "--external-downloader": (), "--print": (),
         "--output-na-placeholder": ()},
        {"o": ((TEMPLATE, PATH),), "P": ((PATH,),), "a": ((PATH,),)},
        "46FJUceghijknqsvwx", False, True, lambda v: v.replace("%", "%%"),
        "yt-dlp's own fields (%(webpage_url)q, %(filepath)q)", ()),
    "gallery-dl": Tool(
        {"--exec": ((SHELL,),), "--exec-after": ((SHELL,),), "--option": ((SHELL,),),
         "--postprocessor-option": ((SHELL,),), "--filter": ((PYTHON,),), "--post-filter": ((PYTHON,),),
         "--child-filter": ((PYTHON,),), "--file-filter": ((PYTHON,),), "--image-filter": ((PYTHON,),),
         "--chapter-filter": ((PYTHON,),), "--filename": ((FORMAT,),), "--rename": ((FORMAT,),),
         "--rename-to": ((FORMAT,),), "--print": ((PRINT,),), "--Print": ((PRINT,),),
         "--print-to-file": ((PRINT,), (FILE,)), "--Print-to-file": ((PRINT,), (FILE,)),
         "--destination": ((PATH,),), "--directory": ((PATH,),), "--error-file": ((PATH,),),
         "--write-log": ((PATH,),), "--write-unsupported": ((PATH,),), "--download-archive": ((PATH,),),
         "--config": ((PATH,),), "--config-json": ((PATH,),), "--config-yaml": ((PATH,),),
         "--config-toml": ((PATH,),), "--cookies": ((PATH,),), "--cookies-export": ((PATH,),),
         "--input-file": ((PATH,),), "--input-file-comment": ((PATH,),), "--input-file-delete": ((PATH,),),
         "--extractors": ((PATH,),), "--cache-file": ((PATH,),),
         "--postprocessor": ()},
        {"o": ((SHELL,),), "O": ((SHELL,),), "f": ((FORMAT,),), "N": ((PRINT,),), "d": ((PATH,),),
         "D": ((PATH,),), "e": ((PATH,),), "c": ((PATH,),), "C": ((PATH,),), "i": ((PATH,),), "I": ((PATH,),),
         "x": ((PATH,),), "X": ((PATH,),)},
        "hqwvgGjJsEKSU46", True, True, lambda v: v.replace("{", "{{").replace("}", "}}"),
        "gallery-dl's own fields ({_path}, {_directory}), -D {root}, --download-archive {archive}", ()),
    "instaloader": Tool(
        {"--dirname-pattern": ((TEMPLATE, PATH),), "--filename-pattern": ((TEMPLATE, PATH),),
         "--title-pattern": ((TEMPLATE, PATH),), "--resume-prefix": ((PATH,),), "--latest-stamps": ((PATH,),),
         "--cookiefile": ((PATH,),), "--sessionfile": ((PATH,),), "--post-filter": ((EVAL,),),
         "--only-if": ((EVAL,),), "--storyitem-filter": ((EVAL,),)},
        {"B": ((PATH,),), "f": ((PATH,),)}, "CFGPSVhqs", True, False, sync._escape,
        "the post's own attributes (likes, date_utc)", ("--latest-stamps",)),
}
# What env's option values are (LAUNCHERS has its grammar): -C a path,
# -S a text split into the command it runs; the others' no kind.
ENV_KINDS = {"-C": PATH, "--chdir": PATH, "-S": SPLIT, "--split-string": SPLIT}
# A program that runs the one named after its own options (and after
# ``operands`` items of its own: timeout's duration, taskset's mask), read
# as GNU getopt_long reads them with a leading "+": options end at "--"
# (taken) or at the first item that is none ("-" too); a short item is a
# cluster of ``flags`` that may end in one of ``values``, which takes the
# rest of the item, else the next one; a long one ("--name", "--name=v")
# may be a unique prefix of one of ``longs`` ({name: FLAG, VALUE, OPTIONAL
# (a value after "=" only) or NONE}). ``none`` (and NONE): options after
# which it runs no program (--help, --version, a pid's). ``numbers``: nice's
# -NUM / --NUM / -+NUM, an adjustment. An option it does not have, a value
# or operand missing: it runs nothing either. From their source: coreutils
# 9.4 (env.c, nice.c, nohup.c, timeout.c, stdbuf.c; env's -a, --argv0 from
# 9.5 on: an older env refuses it) and util-linux 2.39.3 (ionice.c,
# taskset.c). Named by its bare name or a path in SYSTEM_DIRS only: a
# program of the user's under one of these names is not one. Not followed:
# setsid (a job leads its own process group, so setsid forks, exits at once
# and leaves the program out of the job's reach), chrt (its priority, an
# operand, became optional in later versions), flock (its -c runs $SHELL
# -c with the text after the file), and the RUNNERS.
Launcher = collections.namedtuple("Launcher", "flags values none longs operands numbers", defaults=(0, False))
FLAG, VALUE, OPTIONAL, NONE = "flag", "value", "optional", "none"
_GNU = {"help": NONE, "version": NONE}
LAUNCHERS = {
    "env": Launcher("iv0", "CSua", "", {
        "ignore-environment": FLAG, "null": FLAG, "unset": VALUE, "chdir": VALUE, "split-string": VALUE,
        "argv0": VALUE, "default-signal": OPTIONAL, "ignore-signal": OPTIONAL, "block-signal": OPTIONAL,
        "list-signal-handling": FLAG, "debug": FLAG, **_GNU}),
    "nice": Launcher("", "n", "", {"adjustment": VALUE, **_GNU}, numbers=True),
    "nohup": Launcher("", "", "", _GNU),
    "timeout": Launcher("v", "ks", "", {"kill-after": VALUE, "signal": VALUE, "verbose": FLAG, "foreground": FLAG,
                                         "preserve-status": FLAG, **_GNU}, 1),
    "stdbuf": Launcher("", "ioe", "", {"input": VALUE, "output": VALUE, "error": VALUE, **_GNU}),
    "ionice": Launcher("t", "nc", "pPuhV", {"classdata": VALUE, "class": VALUE, "ignore": FLAG, "pid": NONE,
                                             "pgid": NONE, "uid": NONE, **_GNU}),
    "taskset": Launcher("ac", "", "phV", {"all-tasks": FLAG, "cpu-list": FLAG, "pid": NONE, **_GNU}, 1),
}
# Programs that run a command (or a text as one) FeedVault does not
# follow: refused with a placeholder anywhere, as any program it does not read.
RUNNERS = ("xargs", "sudo", "doas", "su", "runuser", "ssh", "watch", "script", "parallel", "find", "chrt",
           "flock", "setsid", "exec", "time", "busybox", "unshare", "nsenter", "systemd-run", "firejail", "bwrap")
# Where a launcher, a DATA_ONLY program, a shell or Python named by its
# path must be (_system; a shell's or Python's symlinks must end at one of
# the same kind: _interpreter).
SYSTEM_DIRS = ("/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin")
# Programs that only ever treat their arguments as data (printed), so a
# placeholder may reach them: echo (coreutils: -n, -e, -E, its escapes
# print text); printf, but never in its format (its first argument, after
# a "--"), whose % directives consume the others. By its bare name or a
# path in SYSTEM_DIRS only, as a launcher.
DATA_ONLY = ("echo", "printf")
# A shell's -c text is read as code, and the file its first argument names
# when it has no -c (nor -s) is run. fish is not one: its -c, -C and more
# take values of their own. By its bare name or a path in SYSTEM_DIRS only
# (_interpreter), as Python.
SHELLS = ("sh", "bash", "dash", "zsh", "ksh", "mksh", "ash")
# Each shell's short options that take the next item (bash -o, -O; ksh93
# -R; mksh -T); sh and ksh may be any of them, so they get them all:
# reading one as a value that is not only ever makes the check look
# later in argv.
SHELL_VALUES = {"bash": "oO", "dash": "o", "ash": "o", "zsh": "o", "mksh": "oT"}
SHELL_ANY_VALUES = "oOTR"
SHELL_LONG_VALUES = ("--rcfile", "--init-file", "--emulate")
# A downloader run by Python (#98): `python3 -m yt_dlp` runs the package's
# __main__.py, which the tool's own command runs too; `python3 /x/yt-dlp`
# runs that file. CPython reads its options as getopt does
# (Python/getopt.c, "bBc:dEhiIJm:OPqRsStuvVW:xX:?"): an item is a cluster
# of flags that may end in -c (code: no module), -m (the module: the rest
# of the item, else the next one) or -W / -X (a value, likewise);
# --check-hash-based-pycs takes the next item, any other long option
# (--help, --version) runs nothing, and "--" ends them.
MODULES = {"yt_dlp": "yt-dlp", "gallery_dl": "gallery-dl", "instaloader": "instaloader"}
PYTHON_RE = re.compile(r"(?:python|pypy)[0-9.]*")
PY_FLAGS, PY_VALUES = "bBdEhiIJOPqRsStuvVx?", "WX"


class Found(collections.namedtuple("Found", "index given options values maybe", defaults=(False,))):
    """An option of env's or of the program's in a command's argv: its
    item's index, its name as given (-o, --outp), [(name, kinds per value)]
    the options it may name (one, unless a prefix of several),
    [(item's index, offset)] where its values start (the same item past
    "=" or a short option, then the next ones), and ``maybe``: read so
    only by the checks (see Tool)."""

    def kinds(self, n):
        """The kinds of its n-th value: those of any option it may name."""
        return {k for _, k in self.pairs(n)}

    def pairs(self, n):
        """(option, kind) of its n-th value, for each option it may name."""
        return [(o, k) for o, spec in self.options if len(spec) > n for k in spec[n]]

    def sure(self, n):
        """The kinds of its n-th value that every option it may name has."""
        return set.intersection(*(set(spec[n] if len(spec) > n else ()) for _, spec in self.options))

    def name(self, kind, n):
        """The option whose n-th value has ``kind``, named in full: the
        first that has it (a path's or FILE's: the only one, else the
        name as given)."""
        named = [o for o, k in self.pairs(n) if k == kind]
        return named[0] if len(named) == 1 or kind not in (PATH, FILE) else self.given


def _match(a, tool):
    """(``a``'s option as given, [(name, kinds)] the options of ``tool`` it
    may name, the offset of its value in ``a`` or None when the value is
    the next item) when it names one whose values are followed, else None."""
    if a.startswith("--"):
        given, eq, _ = a.partition("=")
        if given in tool.longs:
            names = [given]
        elif len(given) > 2:
            names = [o for o in tool.longs if o.startswith(given)]
        else:
            return None
        if not any(tool.longs[o] for o in names):
            return None
        return given, [(o, tool.longs[o]) for o in names], len(given) + 1 if eq else None
    if a.startswith("-"):
        for i, c in enumerate(a[1:], 1):
            if c in tool.shorts:
                at = i + 2 if tool.argparse and a[i + 1:i + 2] == "=" and len(a) > i + 2 else i + 1
                return "-" + c, [("-" + c, tool.shorts[c])], at if at < len(a) else None
            if c not in tool.flags:
                return None
    return None


def _found(argv, n, match, tool):
    """The Found of ``match`` at item ``n``, and the index of the item after
    its values (past the end when they are missing)."""
    given, options, at = match
    joined = at is not None
    count = min(len(spec) for _, spec in options if spec) - joined
    if count and all(o in tool.optional for o, spec in options if spec) \
            and (n + 1 >= len(argv) or argv[n + 1].startswith("-") and argv[n + 1] != "-"):
        count = 0
    values = [(n, at)] * joined + [(m, 0) for m in range(n + 1, min(n + 1 + count, len(argv)))]
    return Found(n, given, options, values), n + 1 + count


def _ends(argv, end, start, found, tool):
    """Whether the "--" at ``end`` ends the options however the tool reads
    the items before it: argparse never takes it as a value; optparse does,
    for an option missing from TOOLS (yt-dlp --referer --), and the items
    after one are read out of step (--referer -P --user-agent --: -P is the
    referer, "--" the user agent), so it ends them only when none is where
    an option goes (no Found's name nor value) but its value's joined to it
    or it is a cluster of flags."""
    taken = {m for f in found for m, _ in f.values} | {f.index for f in found}
    return tool.argparse or not any(
        m not in taken and argv[m].startswith("-") and argv[m] != "-"
        and not (argv[m].startswith("--") and "=" in argv[m])
        and (argv[m].startswith("--") or not all(c in tool.flags for c in argv[m][1:]))
        for m in range(start + 1, end))


def _known(name):
    """Whether ``name`` is a program the checks or the lock groups read."""
    return name in TOOLS or name in jobs.TOOLS or name in SHELLS or name in LAUNCHERS \
        or name in DATA_ONLY or PYTHON_RE.fullmatch(name) is not None


def _system(item):
    """Whether ``item`` names a program by its bare name (the job's PATH
    finds it) or by a path in one of SYSTEM_DIRS. Never through "..": the
    kernel reads /x/link/../../usr/bin/sh past the link, normpath not."""
    return "/" not in item or ".." not in item.split("/") \
        and os.path.dirname(os.path.normpath(item)) in SYSTEM_DIRS


LINK_HOPS = 40                                 # as the kernel's limit on a path's symlinks


def _links(item):
    """The file names along the symlinks an absolute ``item`` leads
    through, one at a time, its own first and the one it runs last (just
    its own for a name found on PATH: the job's PATH decides)."""
    names, path = [os.path.basename(item)], item
    if not os.path.isabs(item):
        return names
    for _ in range(LINK_HOPS):
        try:
            link = os.readlink(path)
        except OSError:                        # not a link (any more), or unreadable
            break
        path = os.path.join(os.path.dirname(path), link)
        names.append(os.path.basename(path))
    return names


def _name(item):
    """The name of the program ``item`` runs: its file name, or, for an
    absolute path whose name is none of the known ones (_known), the first
    known name along the symlinks it leads through (_links: ~/bin/ytdl to
    yt-dlp; ~/bin/ytdl to /snap/bin/yt-dlp to /usr/bin/snap: yt-dlp)."""
    name = os.path.basename(item)
    if _known(name):
        return name
    return next((n for n in _links(item)[1:] if _known(n)), name)


def _interpreter(item, name):
    """Whether ``item``, a shell or Python named ``name`` (_name), is one
    FeedVault reads: by its bare name, or by a path in SYSTEM_DIRS whose
    symlinks end at a program of the same kind (/bin/sh to dash,
    /usr/bin/python3 to python3.12; not a sh linked to perl or busybox).
    One of the user's elsewhere named so may be anything (#105)."""
    if "/" not in item:
        return True
    end = _links(item)[-1]
    same = end in SHELLS if name in SHELLS else PYTHON_RE.fullmatch(end) is not None
    return _system(item) and same


def _shell_values(item, shell):
    """The short options that take the next item for the shell ``item``
    (_name ``shell``): those of each shell along its symlinks (a bash
    linked to zsh may be read as either)."""
    shells = {n for n in _links(item) if n in SHELLS} | {shell}
    return "".join(SHELL_VALUES.get(s, SHELL_ANY_VALUES) for s in sorted(shells))


def _python(argv, i):
    """(the index of the item naming the downloader the Python at ``i``
    runs, its name) for `-m yt_dlp` (`-m yt_dlp.__main__`, `-Im yt_dlp`,
    `-myt_dlp`, `-m runpy yt_dlp`), a file named as one (`python3
    /usr/bin/yt-dlp`) or its package (`python3 /x/yt_dlp`, `.../yt_dlp/__main__.py`),
    else None. Its own options go after that item."""
    n = i + 1
    while n < len(argv) and argv[n].startswith("-") and argv[n] != "-":
        a = argv[n]
        if a == "--":
            n += 1
            break
        if a.startswith("--"):
            if a != "--check-hash-based-pycs":
                return None
            n += 2
            continue
        for k, c in enumerate(a[1:], 1):
            if c == "m":
                at, module = (n, a[k + 1:]) if a[k + 1:] else (n + 1, argv[n + 1] if n + 1 < len(argv) else "")
                if module == "runpy":          # runpy's own __main__ runs the module named next
                    at, module = at + 1, argv[at + 1] if at + 1 < len(argv) else ""
                name = MODULES.get(module.removesuffix(".__main__"))
                return (at, name) if name else None
            if c in PY_VALUES:
                n += 0 if a[k + 1:] else 1
                break
            if c not in PY_FLAGS:
                return None
        n += 1
    if n >= len(argv):
        return None
    # The tool's file, its package's folder (python3 /x/yt_dlp) or that
    # folder's __main__.py: yt-dlp's and gallery-dl's put their folder's
    # parent on sys.path when run so.
    item = argv[n].rstrip("/") or argv[n]
    name = _name(item)
    if name == "__main__.py":
        name = MODULES.get(os.path.basename(os.path.dirname(item)))
    else:
        name = name if name in TOOLS else MODULES.get(name)
    return (n, name) if name else None


def _options(argv, i, spec, name):
    """(the index of the first item after the options of the launcher at
    ``i`` (``spec``, named ``name``), [(index of an option item that takes
    a value, its name as given, as named in full, (index of the item its
    value starts in, offset))], None), or (None, [], why it runs no
    program, or not as FeedVault reads it)."""
    n, took = i + 1, []
    while n < len(argv):
        a = argv[n]
        if a == "--":
            return n + 1, took, None
        if not a.startswith("-") or a == "-":
            break
        digit = a[2:3] if a[1] in "-+" else a[1:2]
        if spec.numbers and digit and digit in "0123456789":
            n += 1
            continue
        if a.startswith("--"):
            given, eq, _ = a[2:].partition("=")
            names = [given] if given in spec.longs else [o for o in spec.longs if o.startswith(given)]
            kind = spec.longs[names[0]] if len(names) == 1 else None
            if kind is None or kind == FLAG and eq:
                return None, [], f"{name} reads {a!r} in a way FeedVault does not follow"
            if kind == NONE:
                return None, [], f"{name} {a} runs no program"
            if kind == VALUE and not eq:
                if n + 1 >= len(argv):
                    return None, [], f"{name} {a} has no value"
                took.append((n, "--" + given, "--" + names[0], (n + 1, 0)))
                n += 2
                continue
            if eq:
                took.append((n, "--" + given, "--" + names[0], (n, len(given) + 3)))
            n += 1
            continue
        for k, c in enumerate(a[1:], 1):
            if c in spec.flags:
                continue
            if c in spec.none:
                return None, [], f"{name} -{c} runs no program"
            if c not in spec.values:
                return None, [], f"{name} reads {a!r} in a way FeedVault does not follow"
            if k + 1 < len(a):
                took.append((n, "-" + c, "-" + c, (n, k + 1)))
                n += 1
            elif n + 1 < len(argv):
                took.append((n, "-" + c, "-" + c, (n + 1, 0)))
                n += 2
            else:
                return None, [], f"{name} -{c} has no value"
            break
        else:
            n += 1
    return n, took, None


def _launch(argv):
    """(the index of the item naming the program a command runs, past its
    LAUNCHERS (env, nice, timeout ...: one after the other) and their
    options, [Found] for env's options that take a value, [(index, name)]
    of each launcher, None), or (None, those, why FeedVault cannot tell
    which program runs: env -S splits a text into it, a launcher that runs
    none or reads an option FeedVault does not follow)."""
    found, chain, i = [], [], 0
    while (name := _name(argv[i])) in LAUNCHERS and _system(argv[i]):
        chain.append((i, name))
        spec = LAUNCHERS[name]
        n, took, why = _options(argv, i, spec, name)
        if n is None:
            return None, found, chain, why
        if name == "env":
            for m, given, option, value in took:
                kind = ENV_KINDS.get(option)
                found.append(Found(m, given, [(option, ((kind,) if kind else (),))], [value]))
                if kind == SPLIT:
                    return None, found, chain, "env -S splits its text into a command"
            n += n < len(argv) and argv[n] == "-"
            while n < len(argv) and "=" in argv[n]:
                n += 1
        n += spec.operands
        if n >= len(argv):
            return None, found, chain, f"{name} runs no program here"
        i = n
    return i, found, chain, None


def _walk(argv):
    """(the index of the item naming the program a command runs, past its
    launchers (_launch), and past Python for a downloader it runs
    (_python), or None when FeedVault cannot tell which program runs; that
    program's name (_name), or None; [Found] for each option of env's and
    then of the program's, when it is one of TOOLS)."""
    i, found, _, _ = _launch(argv)
    if i is None:
        return None, None, found
    name = _name(argv[i])
    if PYTHON_RE.fullmatch(name):
        i, name = _python(argv, i) or (i, name)
    tool = TOOLS.get(name)
    if tool is None:
        return i, name, found
    read, n, end = [], i + 1, None
    while n < len(argv):
        if argv[n] == "--":
            end = n
            break
        match = _match(argv[n], tool)
        if match is None:
            n += 1
            continue
        f, n = _found(argv, n, match, tool)
        read.append(f)
    # The checks' reading too (see Tool): every other item naming an option.
    last = end if end is not None and _ends(argv, end, i, read, tool) else len(argv)
    names = {f.index for f in read} | {end}
    maybe = [_found(argv, m, match, tool)[0]._replace(maybe=True) for m in range(i + 1, last)
             if m not in names and (match := _match(argv[m], tool))]
    return i, name, found + sorted(read + maybe)


def _check_sh(argv, letters):
    """Why a placeholder would be read as code by a shell given ``argv``,
    else None: in its options, or in the first item after them (its
    ``letters`` (_shell_values) and SHELL_LONG_VALUES take the next one):
    its -c text once one of them holds c, else, unless one holds s
    (commands from stdin), the file it runs."""
    run, stdin, value = False, False, False
    for a in argv:
        if _used([a]) and (value or a.startswith(("-", "+"))):
            return f"{a!r}: a placeholder may not be among a shell's options"
        if value:
            value = False
        elif a.startswith("--"):
            value = a in SHELL_LONG_VALUES
        elif a.startswith(("-", "+")):
            run = run or "c" in a
            stdin = stdin or a.startswith("-") and "s" in a
            value = any(c in a for c in letters)
        else:
            break
    else:
        return None
    if run and _used([a]):
        return (f"a shell's -c text is read as code, so it may not hold a FeedVault placeholder: pass it "
                "after the text (sh -c '… \"$1\"' sh {url}), or use a shell script (its inputs are FV_* variables)")
    if not run and not stdin and _used([a]):
        return (f"{a!r}: a shell runs the file its first argument names, so it may not hold a FeedVault "
                "placeholder: pass it after the file, or use a shell script (its inputs are FV_* variables)")
    return None


def _formatter(value, how):
    """Whether gallery-dl may read ``value``, a format string, with another
    formatter than its plain one (see TOOLS)."""
    if how == FILE:
        return "\f" in value
    starts = [value] if how == FORMAT else [value, *value.split(":")[1:]]
    return any(s.startswith(_FORMATTER) for s in starts)


SCRIPT_HINT = "use a shell script instead (its inputs are FV_* variables, never pasted into code)"


def _check_program(argv):
    """Why a FeedVault placeholder in ``argv`` could reach code, as far as
    which program reads it goes, else None. Every program on the way to the
    one that reads the arguments must be one FeedVault follows (LAUNCHERS,
    Python running a downloader), with no placeholder in its own items; the
    last one a downloader (its options are checked: _check_shell), a shell
    (_check_sh) or DATA_ONLY. A placeholder never names a program. A
    shell or Python counts by its bare name or a system path only
    (_interpreter)."""
    if not _used(argv):
        return None
    start, _, chain, why = _launch(argv)
    if start is None:
        return f"{why}: not in a command with a FeedVault placeholder"
    for n in range(start):
        if _used([argv[n]]):
            launcher = next(name for at, name in reversed(chain) if at <= n)
            why = (" (a variable can be code to the program, LD_PRELOAD or BASH_ENV, and -C picks the folder "
                   "its relative names, configs and modules are found in)") if launcher == "env" else ""
            return (f"{argv[n]!r}: a placeholder may not be among {launcher}'s options or operands{why}: "
                    "pass it to the program it runs")
    name = _name(argv[start])
    if _used([argv[start]]):
        return f"{argv[start]!r}: a placeholder may not name the program to run"
    if (name in SHELLS or PYTHON_RE.fullmatch(name)) and not _interpreter(argv[start], name):
        return (f"{argv[start]} is not a program FeedVault reads the arguments of: a shell or Python counts "
                f"only by its bare name or by a path in {', '.join(SYSTEM_DIRS)} that leads to one, so it may "
                f"read a FeedVault placeholder as code: {SCRIPT_HINT}")
    if PYTHON_RE.fullmatch(name):
        ran = _python(argv, start)
        if ran is None:
            return (f"{name} runs Python code that FeedVault does not read, so no FeedVault placeholder may "
                    f"reach it (only python -m yt_dlp, gallery_dl or instaloader may take one): {SCRIPT_HINT}")
        for n in range(start + 1, ran[0] + 1):
            if _used([argv[n]]):
                return (f"{argv[n]!r}: a placeholder may not be among Python's options or name what it runs "
                        "(-W imports a warning's category, -X sets how it runs)")
        return None
    if name in TOOLS or name in SHELLS:
        return None
    if name == "printf" and _system(argv[start]):
        rest = argv[start + 1:]
        fmt = rest[1:2] if rest[:1] == ["--"] else rest[:1]
        if _used(fmt):
            return f"{fmt[0]!r}: printf's format may not hold a FeedVault placeholder: pass it after the format (%s)"
        return None
    if name in DATA_ONLY and _system(argv[start]):
        return None
    if name in RUNNERS:
        return (f"{name} runs a command FeedVault does not follow, so no FeedVault placeholder may be in it: "
                f"{SCRIPT_HINT}")
    return (f"{name} is not a program FeedVault reads the arguments of (a downloader, a shell, Python running "
            f"a downloader, {', '.join(DATA_ONLY)}), so it may read a FeedVault placeholder as code: {SCRIPT_HINT}")


def _check_shell(argv):
    """Why a FeedVault placeholder would be read as code through one of the
    tool's options, else None."""
    start, tool, found = _walk(argv)
    if start is None:                          # _check_program's to refuse
        return None
    if tool in SHELLS:
        return _check_sh(argv[start + 1:], _shell_values(argv[start], tool))
    for f in found:
        if f.index < start:
            continue
        if ALIAS in f.kinds(0) and _used(argv):
            return (f"{f.name(ALIAS, 0)} carries what follows it into the options it expands to, a shell's too: "
                    "not in a command with a FeedVault placeholder (use a shell script)")
        for n, (item, at) in enumerate(f.values):
            value = argv[item][at:]
            used = _used([value])
            if not used:
                continue
            for option, how in f.pairs(n):
                if how in CODE or how in FORMATS and _formatter(value, how):
                    return _code(option, how, value, used, TOOLS[tool].fields)
    return None


def _code(option, how, value, used, fields):
    """The reason a placeholder in ``value``, ``option``'s, is refused."""
    found = f"found {{{sorted(used)[0]}}} in {value!r}"
    if how in FORMATS:
        what = f"{option}'s FILE" if how == FILE else f"{option}'s value"
        return (f"{what} is a format string that gallery-dl evaluates as Python or reads "
                "as a template file when it starts with \\f, so there it may not hold a FeedVault "
                f"placeholder: use {fields}, or a shell script (its inputs are FV_* variables); {found}")
    return (f"{option}'s value can reach {how}, so it may not hold a FeedVault placeholder: use "
            f"{fields}, or a shell script (its inputs are FV_* variables); {found}")


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
    error = _check_common(data) or _check_needs(data, [*argv, data.get("rescan")]) \
        or _check_program(argv) or _check_shell(argv)
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


def _folder_refused(st, what, path):
    """Why a folder (``path``'s lstat) may not hold scripts, else None."""
    if stat.S_ISLNK(st.st_mode):
        return f"{what} is a symlink"
    if not stat.S_ISDIR(st.st_mode):
        return f"{what} is not a folder"
    if st.st_uid != os.getuid():
        return f"{what} belongs to another user"
    if st.st_mode & 0o022:
        return f"{what} is writable by group or others ({config.chmod_hint(path)})"
    return None


def _ancestors_refused(folder):
    """config.ancestors_refused: the folders above the scripts folder."""
    return config.ancestors_refused(folder)


def _file_refused(st, kind, path):
    """Why a file (``path``'s stat) may not run, else None."""
    if stat.S_ISLNK(st.st_mode):
        return "a symlink: a script must be a regular file in the folder itself"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if st.st_uid != os.getuid():
        return "belongs to another user"
    if st.st_mode & 0o022:
        return f"writable by group or others ({config.chmod_hint(path)})"
    if st.st_size > SIZE_MAX:
        return f"larger than {SIZE_MAX // 1024} KiB"
    if kind == "shell" and not st.st_mode & stat.S_IXUSR:
        return f"not executable ({config.chmod_hint(path, 'u+x')})"
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
        out["refused"] = _file_refused(before, out["kind"], out["path"])
        if out["refused"]:
            return out, None
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            out["refused"] = _file_refused(st, out["kind"], out["path"])
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
    someone else's, writable by others, or below a folder others can write
    to (_ancestors_refused)."""
    folder = scripts_dir()
    try:
        st = os.lstat(folder)
        refused = _folder_refused(st, "the scripts folder", folder) or _ancestors_refused(folder)
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


def make_folder():
    """Create the scripts folder (and the config folder) 0700 when it is not
    there yet, so the first script needs no chmod: config.make_private_dir.
    One that cannot be made is left to _files to say why."""
    try:
        config.make_private_dir(scripts_dir())
    except OSError:
        pass


def tighten():
    """On start: chmod go-w FeedVault's own folders when a umask of 002 (or
    an older FeedVault) left them writable by group or others, so they are
    not refused: the scripts folder, and the config folder when it is the
    default one (``<XDG_CONFIG_HOME>/feedvault``, FEEDVAULT_CONFIG unset;
    a config file set elsewhere may sit in a folder of the user's). Only
    folders that are ours and not symlinks (config.tighten_private_dir),
    never a folder above them. Returns [(folder, its mode before)]."""
    folders = [scripts_dir()]
    if not os.environ.get("FEEDVAULT_CONFIG"):
        folders.insert(0, os.path.dirname(config.config_path()))
    out = []
    for folder in folders:
        before = config.tighten_private_dir(folder)
        if before is not None:
            print(f"[scripts] {folder} was writable by group or others ({before:o}): now {before & ~0o022:o}")
            out.append((folder, before))
    return out


def listing():
    """{dir, dir_refused, shell_template, scripts}: the built-ins, then the
    files by name. The scripts folder is made when it is not there."""
    make_folder()
    refused, found = _files()
    return {"dir": scripts_dir(), "dir_refused": refused, "shell_template": SHELL_TEMPLATE,
            "scripts": [_builtin(n) for n in BUILTINS] + [s for s, _ in found]}


def lookup(sid, content=False, raw=False):
    """(one script by id, read now, or None; the scripts folder's refusal
    or None). ``content``: with its text (the file's, or a built-in's JSON
    as a file would hold it); ``raw``: with its bytes as read ("raw", None
    for a built-in), the ones its sha256 is of."""
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
    for s, data in found:
        if s["id"] == sid and s["kind"] is not None:
            s = dict(s)                        # its own: the read may be read_once's, shared
            if content:
                s["content"] = data.decode("utf-8", "replace") if data is not None else None
            if raw:
                s["raw"] = data
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
    """The name of the program a command runs (_walk: a downloader named by
    its path, a symlink to it, run behind a launcher or by Python too), else the
    script's tool."""
    argv = script.get("argv")
    start, name, _ = _walk(argv) if script["kind"] == "command" and argv else (None, None, None)
    return script["tool"] if start is None else name


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
    a sync of it), however it names it (program); anything else in
    "scripts"."""
    tool = program(script)
    return tool if tool in jobs.TOOLS else "scripts"


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


# The placeholders whose value comes from a run's input or a source's
# target: a link (check_url; check_target for gallery-dl and yt-dlp), or an
# instaloader profile name, which may be "..". {root}, {data_dir} and
# {archive} are the operator's paths.
LINKS = ("target", "url")


def _filled(text, vals):
    """(``text`` split at its placeholders, the same with each one's value
    in its place): what substitute joins."""
    parts = _PLACEHOLDER_RE.split(text)
    return parts, [vals[p] if i % 2 else p for i, p in enumerate(parts)]


def _check_folder(parts, filled, cut, where, tool):
    """jobs.BadRequest when a link's value in ``filled`` (``parts`` filled
    in) would lead the path before ``cut`` out of the folder its author
    wrote: a .. segment it is part of (alone, or joined with the author's
    text up to the "/" around it), or a ~ it starts the path with (when
    ``tool`` expands it, else None). The author's own .. stays as it is.
    %2e%2e is not one: no tool decodes a path."""
    path = "".join(filled)[:cut]
    up, s = [], 0
    for segment in path.split("/"):
        if segment == "..":
            up.append((s, s + 2))
        s += len(segment) + 1
    at = 0
    for i, p in enumerate(filled):
        end = min(at + len(p), cut)
        if i % 2 and parts[i] in LINKS and at < end:
            if any(s < end and e > at for s, e in up):
                raise jobs.BadRequest(f"{{{parts[i]}}} puts a .. in {where}, "
                                      "which would lead out of the folder written there")
            if tool and at == 0 and p.startswith("~"):
                raise jobs.BadRequest(f"{{{parts[i]}}} starts {where} with ~, which {tool} would expand")
        at += len(p)


def _check_path(text, vals, option, tool):
    """jobs.BadRequest when a link's value would take ``text``, the value of
    an option of the PATH kind (TOOLS, env's -C), out of its folder
    (_check_folder), or puts a $ in it where ``tool`` expands $NAME (else
    None)."""
    parts, filled = _filled(text, vals)
    for i in range(1, len(parts), 2):
        if tool and parts[i] in LINKS and "$" in filled[i]:
            raise jobs.BadRequest(f"{{{parts[i]}}} puts a $ in {option}'s path, which {tool} would expand")
    _check_folder(parts, filled, sum(map(len, filled)), f"{option}'s path", tool)


def _file_name(text, vals, escape, option):
    """``text``, a --print-to-file FILE, its placeholders filled in and
    escaped only where they land after the item's last "/": gallery-dl
    splits FILE there (os.path.split) and formats the file name alone; its
    folder is a plain path, where an escaped brace would be a second one. A
    value that brings its own "/" moves that split, so the split is found
    on the filled-in item: what of the value comes before it stays as it is.
    jobs.BadRequest for a value's $ in the folder (gallery-dl expands $NAME
    there: util.expand_path), a link's .. or leading ~ there
    (_check_folder: gallery-dl makes the folder and appends to the file),
    or \\f in the file name (another formatter). ``option``: the one FILE
    is for, as the reasons name it."""
    parts, filled = _filled(text, vals)
    cut, at, out = "".join(filled).rfind("/") + 1, 0, []
    for i, p in enumerate(filled):
        keep = max(cut - at, 0)
        if i % 2 and "$" in p[:keep]:
            raise jobs.BadRequest(f"{{{parts[i]}}} puts a $ in {option}'s folder, "
                                  "which gallery-dl would expand")
        if i % 2 and "\f" in p[keep:]:
            raise jobs.BadRequest(f"{{{parts[i]}}} puts \\f in {option}'s file name, "
                                  "which gallery-dl may evaluate as Python")
        out.append(p[:keep] + escape(p[keep:]) if i % 2 else p)
        at += len(p)
    _check_folder(parts, filled, cut, f"{option}'s folder", "gallery-dl")
    return "".join(out)


def command(script, vals):
    """A command's argument list, its placeholders filled in: escaped in
    the value of an option its tool formats (the program's, past its launchers or
    its path, as _check_shell reads it: _walk; env's own items as they
    are). jobs.BadRequest for a link's value that would take a path
    option's value, or env's -C, out of its folder (_check_path)."""
    argv = script["argv"]
    out = [substitute(a, vals) for a in argv]
    start, tool, found = _walk(argv)
    if start is None:
        return out
    spec = TOOLS.get(tool)
    expands = tool if spec and spec.expands else None
    escaped = {k: spec.escape(v) for k, v in vals.items()} if spec else vals
    for f in found:
        for n, (item, at) in enumerate(f.values):
            head, value = argv[item][:at], argv[item][at:]
            if PATH in f.kinds(n):
                _check_path(value, vals, f.name(PATH, n), expands if f.index > start else None)
            file = _file_name(value, vals, spec.escape, f.name(FILE, n)) if FILE in f.kinds(n) else None
            sure = set() if f.maybe else f.sure(n)
            if FILE in sure:
                out[item] = head + file
            elif sure & ESCAPED:
                out[item] = head + substitute(value, escaped)
    return out


def _spec(script, vals, cfg, rescan):
    cwd = os.path.join(cfg["data_directory"], DIR_NAME)
    if script["kind"] == "command":
        argv = command(script, vals)
        return {"tool": argv[0], "args": argv[1:], "cwd": cwd, "rescan": rescan, "group": group(script)}
    # Shown as its path; what runs is the check's jobs.Script, its bytes.
    return {"tool": script["path"], "args": [], "env": {**shell_env(vals), "FV_SCRIPT": script["path"]},
            "cwd": cwd, "rescan": rescan, "group": group(script)}


def runnable(sid, sha256=None, raw=False):
    """The script ``sid``, read now, when it may run (and is still the one
    whose SHA-256 is ``sha256``, when given; ``raw``: with the bytes that
    is of, see lookup). Raises jobs.BadRequest."""
    script, refused = lookup(sid, raw=raw)
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
    """The log's first lines: what runs, and a shell script's inputs. What
    the check returns: a shell script's jobs.Script (the bytes just checked,
    which run), else None."""
    if script["builtin"]:
        note(f"built-in script {script['id']}")
    else:
        note(f"script {script['path']} (sha256 {script['sha256'][:16]}…)")
    if script["kind"] != "shell":
        return None
    for k, v in vals.items():
        note(f"FV_{k.upper()}={v}")
    note(f"FV_SCRIPT={script['path']}")
    return jobs.Script(script["path"], script["raw"])


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
    config.make_private_dir(os.path.join(cfg["data_directory"], DIR_NAME))
    return _spec(script, vals, cfg, _rescan(script, vals, cfg["media_roots"]))


def _check(params, note):
    """Right before it starts: the script is read again, and must be the
    one it was queued with."""
    try:
        script = runnable(params["script"], params.get("sha256"), raw=True)
    except jobs.BadRequest as e:
        note(str(e))
        raise
    return _say(script, _vals(script, params, config.load()), note)


def _tool_pause(tool):
    """The pause after a sync of ``tool``; none for another program."""
    if tool == "instaloader":
        return sync.settings()["pause"]
    return sync.tool_settings(tool)["pause"] if tool in sources.TOOLS else 0


def _pause(params):
    """A downloader's command pauses as that tool's syncs do, however it
    names it (program, as its lock group)."""
    try:
        script = get(params["script"])
        return _tool_pause(program(script)) if script else 0
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
        config.make_private_dir(os.path.join(cfg["data_directory"], DIR_NAME))
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
        script = runnable(params["script"], params["sha256"], raw=True)
    except jobs.BadRequest as e:
        message = f"the source's script: {e}"
        note(message)
        raise jobs.BadRequest(message)
    return _say(script, _source_values(script, src, config.load()), note)


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
