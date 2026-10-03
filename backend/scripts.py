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
folder indexed once it has run. Built-in templates (``builtin:<name>``)
are the commands downloaders.py and sync.py run, written as such a file
would be: read-only, runnable, to copy.
"""
import hashlib
import json
import os
import re
import stat

import config
import jobs

DIR_NAME = "scripts"
SIZE_MAX = 64 * 1024                           # bytes of a script file
BUILTIN = "builtin:"
FILE_RE = re.compile(r"([a-z0-9_-]{1,64})\.(json|sh)")
ID_RE = re.compile(r"(?:builtin:)?[a-z0-9_-]{1,64}")
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
YT_DLP_NAME = "{root}/%(uploader_id)s-%(upload_date)s-%(id)s.%(ext)s"
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
    error = _check_common(data) or _check_needs(data, [*argv, data.get("rescan")])
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
            "tool": t["argv"][0], "argv": list(t["argv"]), "refused": None, "sha256": None,
            "size": None, "mtime": None}


def _entry(name, folder):
    m = FILE_RE.fullmatch(name)
    sid = m.group(1) if m else name
    return {"id": sid, "builtin": False, "kind": KINDS[m.group(2)] if m else None, "file": name,
            "path": os.path.join(folder, name), "name": None, "description": None, "needs": None,
            "rescan": None, "tool": None, "argv": None, "refused": None, "sha256": None, "size": None,
            "mtime": None}


def _read(name, folder):
    """(script dict, raw bytes or None) of one name in the folder."""
    out = _entry(name, folder)
    if out["kind"] is None:
        out["refused"] = "the name must be [a-z0-9_-] (at most 64), then .json or .sh"
        return out, None
    try:
        st = os.stat(out["path"])
        if st.st_size > SIZE_MAX:
            out["refused"] = f"larger than {SIZE_MAX // 1024} KiB"
            return out, None
        with open(out["path"], "rb") as f:
            raw = f.read(SIZE_MAX + 1)
    except OSError as e:
        out["refused"] = f"cannot be read: {e.strerror or e}"
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
    """[(script dict, raw bytes or None)] of the scripts folder, by name;
    (folder's refusal or None, …)."""
    folder = scripts_dir()
    try:
        names = sorted(os.listdir(folder))
    except FileNotFoundError:
        return None, []
    except OSError as e:
        return f"cannot be read: {e.strerror or e}", []
    found = [_read(n, folder) for n in names]
    ids = {}
    for s, _ in found:
        ids.setdefault(s["id"], []).append(s)
    for same in ids.values():
        if len(same) > 1:
            for s in same:
                s["refused"] = f"two files have the id {s['id']}: {', '.join(x['file'] for x in same)}"
    return None, found


def listing():
    """{dir, dir_refused, shell_template, scripts}: the built-ins, then the
    files by name."""
    refused, found = _files()
    return {"dir": scripts_dir(), "dir_refused": refused, "shell_template": SHELL_TEMPLATE,
            "scripts": [_builtin(n) for n in BUILTINS] + [s for s, _ in found]}


def get(sid, content=False):
    """One script by id, read now, or None. ``content``: with its text
    (the file's, or a built-in's JSON as a file would hold it)."""
    if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
        return None
    if sid.startswith(BUILTIN):
        name = sid[len(BUILTIN):]
        if name not in BUILTINS:
            return None
        out = _builtin(name)
        if content:
            out["content"] = template(name)
        return out
    _, found = _files()
    for s, raw in found:
        if s["id"] == sid and s["kind"] is not None:
            if content:
                s["content"] = raw.decode("utf-8", "replace") if raw is not None else None
            return s
    return None


def template(name):
    """A built-in as the JSON file to create from it."""
    return json.dumps(BUILTINS[name], indent=2) + "\n"
