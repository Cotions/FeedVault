#!/bin/bash
# FeedVault — run a throwaway instance.
#
#   ./testapp.sh            copy of your live database, your real media read-only
#   ./testapp.sh --demo     invented demo vault instead (no real data involved)
#   ./testapp.sh --reset    throw the copy away and take a fresh one from live
#   ./testapp.sh --status   show what exists and where, then exit
#
# The test instance runs on port 3389 with its own config file, so the live app
# never sees it. With live data it runs inside a bubblewrap sandbox where the
# media folders and the live database are mounted read-only. With several
# profiles, each one's database is copied and each one's media is read-only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}/feedvault"
LIVE_CONFIG="$CONFIG_HOME/config.json"
TEST_CONFIG="$CONFIG_HOME/config.test.json"
DEMO_DIR="${FEEDVAULT_DEMO_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/feedvault-demo}"
PORT="${FEEDVAULT_TEST_PORT-3389}"
VENV="$ROOT/backend/venv"

say()  { printf '\033[1;32m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

MODE=start
for arg in "$@"; do
  case "$arg" in
    --demo)   MODE=demo ;;
    --reset)  MODE=reset ;;
    --status) MODE=status ;;
    # The comment block under the #! line, up to the first line that is not one.
    --help|-h) awk 'NR == 1 { next } !/^#/ { exit } { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    *) die "Unknown option: $arg (try --help)" ;;
  esac
done

# --- port ---------------------------------------------------------------
# A number from 1 to 65535 before it goes anywhere (the Python check below),
# and never the live app's: 3380, or FEEDVAULT_PORT when run.sh is moved
# (with the live app down, the busy-port check below would not see it).
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( 10#$PORT >= 1 && 10#$PORT <= 65535 )) \
  || die "FEEDVAULT_TEST_PORT must be a port number from 1 to 65535, not '$PORT'"
PORT=$((10#$PORT))
LIVE_PORT="${FEEDVAULT_PORT:-3380}"
[[ "$LIVE_PORT" =~ ^[0-9]{1,5}$ ]] && LIVE_PORT=$((10#$LIVE_PORT))
[ "$PORT" != 3380 ] && [ "$PORT" != "$LIVE_PORT" ] \
  || die "FEEDVAULT_TEST_PORT must not be the live app's port ($PORT)"
# --- port end -----------------------------------------------------------

[ -x "$VENV/bin/python" ] || die "No virtualenv at $VENV. Run ./run.sh once."
[ -f "$ROOT/frontend/dist/index.html" ] || die "UI not built. Run: ./run.sh --build"

if python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)"; then
  die "Port $PORT is already in use. Set FEEDVAULT_TEST_PORT to something else."
fi

# --- demo: invented data, nothing to protect -------------------------------
if [ "$MODE" = demo ]; then
  [ -f "$DEMO_DIR/config.json" ] || "$VENV/bin/python" "$ROOT/scripts/make_demo.py" "$DEMO_DIR"
  say "Demo instance → http://localhost:$PORT"
  exec env "FEEDVAULT_CONFIG=$DEMO_DIR/config.json" "FEEDVAULT_PORT=$PORT" "FEEDVAULT_NO_BROWSER=1" \
    "$VENV/bin/python" "$ROOT/backend/app.py"
fi

[ -f "$LIVE_CONFIG" ] || die "No live config at $LIVE_CONFIG. Start the real app once first, or use --demo."

# --- test data ----------------------------------------------------------
# Where the copies go (#111): absolute paths, resolved, that are not the
# live data, a media folder, HOME or /, hold none of them and are inside
# neither of the first two. Only a folder this script made (it carries a
# .feedvault-test marker) is ever reset or written into.
#
# The live config holds one library, or a list of profiles, each a library
# of its own (data_directory, media_roots); a config from before profiles
# is flat, and read as one profile, as the app reads it (backend/config.py).
# Every profile's database is copied: the active one's to TEST_DATA, each
# other's beside its own data directory, with a -test suffix. Every check
# below is made for each copy, against every profile's data and media.
PROFILES_PY='
import json, sys
c = json.load(open(sys.argv[1]))
profiles = c.get("profiles")
profiles = [p for p in profiles if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"]] \
    if isinstance(profiles, list) else []
if not profiles:
    profiles = [{"id": "default", **{k: c[k] for k in ("data_directory", "media_roots") if k in c}}]
active = next((p for p in profiles if p["id"] == c.get("active_profile")), profiles[0])
others = [p for p in profiles if p is not active]
def data(p):
    d = p.get("data_directory")
    return d if isinstance(d, str) and "\n" not in d else ""
'
LIVE_DATA="$(python3 -c "$PROFILES_PY
print(data(active))" "$LIVE_CONFIG")" || die "Cannot read $LIVE_CONFIG"
[[ "$LIVE_DATA" = /* ]] \
  || die "data_directory in $LIVE_CONFIG must be an absolute path, not '$LIVE_DATA'"
LIVE_DATA="$(realpath -m -- "$LIVE_DATA")"

# The other profiles' data directories (none with a flat config), in the
# config's order: one line each, after a "=", so that an empty one is a line too.
OTHER_RAW="$(python3 -c "$PROFILES_PY
for p in others:
    print(\"=\" + data(p))" "$LIVE_CONFIG")" || die "Cannot read $LIVE_CONFIG"
OTHER_LIVE=()
OTHER_TEST=()
while IFS= read -r d; do
  d="${d#=}"
  [[ "$d" = /* ]] \
    || die "data_directory of a profile in $LIVE_CONFIG must be an absolute path, not '$d'"
  d="$(realpath -m -- "$d")"
  OTHER_LIVE+=("$d")
  OTHER_TEST+=("$d-test")
done < <([ -n "$OTHER_RAW" ] && printf '%s\n' "$OTHER_RAW")

# Every media root in the live config, of every profile, there or not (an unmounted disk too).
mapfile -t ALL_MEDIA_ROOTS < <(python3 -c "$PROFILES_PY
for p in profiles:
    roots = p.get(\"media_roots\")
    for r in roots if isinstance(roots, list) else []:
        if isinstance(r, str) and r and \"\\n\" not in r:
            print(r)" "$LIVE_CONFIG")
MEDIA_ROOTS=()
for r in "${ALL_MEDIA_ROOTS[@]}"; do [ -d "$r" ] && MEDIA_ROOTS+=("$r"); done

TEST_DATA="${FEEDVAULT_TEST_DATA:-$LIVE_DATA-test}"
[[ "$TEST_DATA" = /* ]] || die "FEEDVAULT_TEST_DATA must be an absolute path, not '$TEST_DATA'"
TEST_DATA="$(realpath -m -- "$TEST_DATA")"
TEST_MARK="$TEST_DATA/.feedvault-test"
# Every copy: the active profile's first.
TEST_DIRS=("$TEST_DATA" "${OTHER_TEST[@]}")

under() { [ "$1" = "$2" ] || [[ "$1" == "${2%/}/"* ]]; }    # $1 is $2 or inside it
for t in "${TEST_DIRS[@]}"; do
  # FEEDVAULT_TEST_DATA moves the active profile's copy only.
  [ "$t" = "$TEST_DATA" ] && hint="Set FEEDVAULT_TEST_DATA elsewhere." \
    || hint="It is the copy of another profile's data directory: give that profile another one, or remove it."
  for p in "$LIVE_DATA" "${OTHER_LIVE[@]}" "${ALL_MEDIA_ROOTS[@]}"; do
    p="$(realpath -m -- "$p")"
    if under "$t" "$p" || under "$p" "$t"; then
      die "Test data dir $t overlaps $p (the live data or a media folder). $hint"
    fi
  done
  for p in "${HOME:-/}" /; do
    p="$(realpath -m -- "$p")"
    if under "$p" "$t"; then
      die "Test data dir $t would hold $p. $hint"
    fi
  done
done
# No two copies in one folder, nor one inside another.
for i in "${!TEST_DIRS[@]}"; do
  for j in "${!TEST_DIRS[@]}"; do
    if [ "$i" -lt "$j" ] && { under "${TEST_DIRS[$i]}" "${TEST_DIRS[$j]}" || under "${TEST_DIRS[$j]}" "${TEST_DIRS[$i]}"; }; then
      die "Test data dirs ${TEST_DIRS[$i]} and ${TEST_DIRS[$j]} overlap (two profiles' copies). Set FEEDVAULT_TEST_DATA elsewhere."
    fi
  done
done

marked() { local t="${1:-$TEST_DATA}"; [ -f "$t/.feedvault-test" ] && [ ! -L "$t/.feedvault-test" ]; }
for t in "${TEST_DIRS[@]}"; do
  if [ "$MODE" != status ] && { [ -e "$t" ] || [ -L "$t" ]; } && ! marked "$t"; then
    die "$t exists but has no .feedvault-test marker, so testapp.sh did not make it; nothing was changed. If it is an old test copy, check it, remove it by hand (rm -rf -- '$t') and run again; otherwise set FEEDVAULT_TEST_DATA to a new folder."
  fi
done
# --- test data end ------------------------------------------------------

if [ "$MODE" = status ]; then
  about() { [ -d "$1" ] && echo "($(du -sh "$1" | cut -f1)$(marked "$1" || echo ', no .feedvault-test marker: not made here'))" || echo '(not created yet)'; }
  echo "live config   $LIVE_CONFIG"
  echo "live data     $LIVE_DATA"
  echo "test config   $TEST_CONFIG $([ -f "$TEST_CONFIG" ] && echo '(exists)' || echo '(not created yet)')"
  echo "test data     $TEST_DATA $(about "$TEST_DATA")"
  for i in "${!OTHER_LIVE[@]}"; do
    echo "other profile ${OTHER_LIVE[$i]}"
    echo "  its copy    ${OTHER_TEST[$i]} $(about "${OTHER_TEST[$i]}")"
  done
  echo "test port     $PORT"
  echo "shared, read-only in the sandbox:"
  printf '              %s\n' "${MEDIA_ROOTS[@]}"
  exit 0
fi

# --- test copy ----------------------------------------------------------
if [ "$MODE" = reset ]; then
  for t in "${TEST_DIRS[@]}"; do
    [ -d "$t" ] || continue
    marked "$t" || die "Not removing $t: it has no .feedvault-test marker"
    say "Removing the old copy at $t"
    rm -rf -- "$t"
  done
fi

# $1: a live data directory, $2: where its copy goes. The folder is made and
# marked first; a profile that has no database yet gets an empty, marked one.
copy_data() {
  [ -f "$2/feedvault.db" ] && return 0
  mkdir -p "$2"
  marked "$2" || printf 'A test copy made by FeedVault'"'"'s testapp.sh; --reset deletes this folder.\n' > "$2/.feedvault-test"
  if [ ! -f "$1/feedvault.db" ]; then
    say "No database at $1/feedvault.db yet: its copy at $2 starts empty"
    return 0
  fi
  say "Copying your database to $2"
  # The backup API gives a consistent copy even while the live app is writing.
  python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d)' \
    "$1/feedvault.db" "$2/feedvault.db"
}

if [ ! -f "$TEST_DATA/feedvault.db" ]; then
  [ -f "$LIVE_DATA/feedvault.db" ] || die "No database at $LIVE_DATA/feedvault.db"
  copy_data "$LIVE_DATA" "$TEST_DATA"
fi
for i in "${!OTHER_LIVE[@]}"; do copy_data "${OTHER_LIVE[$i]}" "${OTHER_TEST[$i]}"; done
# --- test copy end -----------------------------------------------------

# The live config with each profile on its copy: never config.json itself.
python3 - "$TEST_CONFIG" "$LIVE_CONFIG" "$TEST_DATA" "${OTHER_TEST[@]}" <<'PY'
import json, sys
out, live, data, *others = sys.argv[1:]
c = json.load(open(live))
profiles = c.get("profiles")
profiles = [p for p in profiles if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"]] \
    if isinstance(profiles, list) else []
if profiles:
    # As the test data block lists them: the active one, then the others in order.
    active = next((p for p in profiles if p["id"] == c.get("active_profile")), profiles[0])
    rest = [p for p in profiles if p is not active]
    if len(rest) != len(others):
        sys.exit("testapp.sh: the live config changed while it was read; run again")
    active["data_directory"] = data
    for p, d in zip(rest, others):
        p["data_directory"] = d
    c["profiles"] = profiles
else:
    c["data_directory"] = data
# A copy of your sources never syncs on its own (Settings → Sync can undo it).
c["schedules_paused"] = True
json.dump(c, open(out, "w"), indent=2)
PY

CMD=(env "FEEDVAULT_CONFIG=$TEST_CONFIG" "FEEDVAULT_PORT=$PORT" "FEEDVAULT_NO_BROWSER=1"
     "$VENV/bin/python" "$ROOT/backend/app.py")

if command -v bwrap &>/dev/null; then
  RO=()
  for r in "${MEDIA_ROOTS[@]}"; do RO+=(--ro-bind "$r" "$r"); done
  for d in "$LIVE_DATA" "${OTHER_LIVE[@]}"; do [ -d "$d" ] && RO+=(--ro-bind "$d" "$d"); done
  RO+=(--ro-bind "$LIVE_CONFIG" "$LIVE_CONFIG")
  say "Sandbox on: media and the live databases are read-only for this instance"
  CMD=(bwrap --dev-bind / / "${RO[@]}" --die-with-parent "${CMD[@]}")
else
  warn "bwrap not found — running without the read-only protection"
fi

say "Test instance → http://localhost:$PORT   (live app stays on ${FEEDVAULT_PORT:-3380})"
exec "${CMD[@]}"
