"""Paths and the user's config file.

The config lives in ~/.config/feedvault/config.json (or FEEDVAULT_CONFIG), never
next to the code, so the repo holds the app only and a frozen binary works too.
"""
import json
import os
import sys
import threading

FROZEN     = getattr(sys, "frozen", False)
BUNDLE_DIR = getattr(sys, "_MEIPASS", "")
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_DIR   = os.path.dirname(BASE_DIR)

PORT = int(os.environ.get("FEEDVAULT_PORT", "3380"))
# Release builds rewrite this line with the tag being built.
__version__ = "0.0.0-dev"

DEFAULT_DATA = os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
    "feedvault",
)

_lock = threading.Lock()


def config_path():
    override = os.environ.get("FEEDVAULT_CONFIG")
    if override:
        return os.path.abspath(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "feedvault", "config.json")


def static_dir():
    """Bundled UI when frozen, freshly built UI when running from source."""
    for candidate in (
        os.path.join(BUNDLE_DIR, "static") if FROZEN else "",
        os.path.join(REPO_DIR, "frontend", "dist"),
    ):
        if candidate and os.path.exists(os.path.join(candidate, "index.html")):
            return candidate
    return None


def load():
    cfg = {}
    path = config_path()
    if os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)
    cfg.setdefault("data_directory", DEFAULT_DATA)
    cfg.setdefault("media_roots", [])
    cfg.setdefault("similar_threshold", 6)        # dHash bits that may differ, see duplicates.py
    return cfg


def save(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with _lock:
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, path)


def clean_roots(roots):
    """Normalize a list of media roots. Returns (roots, error)."""
    if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
        return None, "media_roots must be a list of paths"
    out = []
    for r in roots:
        r = r.strip()
        if not r:
            continue
        r = os.path.abspath(os.path.expanduser(r))
        if not os.path.isdir(r):
            return None, f"not a folder: {r}"
        if r not in out:
            out.append(r)
    # A root inside another root would index the same files twice.
    for a in out:
        for b in out:
            if a != b and b.startswith(a.rstrip(os.sep) + os.sep):
                return None, f"{b} is inside {a}; keep only one"
    return out, None


def db_path(cfg=None):
    cfg = cfg or load()
    return os.path.join(cfg["data_directory"], "feedvault.db")
