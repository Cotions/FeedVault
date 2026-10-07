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
# media folders and the live database are mounted read-only.
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
# Where the copy goes (#111): an absolute path, resolved, that is not the
# live data, a media folder, HOME or /, holds none of them and is inside
# neither of the first two. Only a folder this script made (it carries a
# .feedvault-test marker) is ever reset or written into.
LIVE_DATA="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1])).get("data_directory")
print(d if isinstance(d, str) else "")' "$LIVE_CONFIG")" || die "Cannot read $LIVE_CONFIG"
[[ "$LIVE_DATA" = /* ]] \
  || die "data_directory in $LIVE_CONFIG must be an absolute path, not '$LIVE_DATA'"
LIVE_DATA="$(realpath -m -- "$LIVE_DATA")"

# Every media root in the live config, there or not (an unmounted disk too).
mapfile -t ALL_MEDIA_ROOTS < <(python3 - "$LIVE_CONFIG" <<'PY'
import json, sys
for r in json.load(open(sys.argv[1])).get("media_roots") or []:
    if isinstance(r, str) and r and "\n" not in r:
        print(r)
PY
)
MEDIA_ROOTS=()
for r in "${ALL_MEDIA_ROOTS[@]}"; do [ -d "$r" ] && MEDIA_ROOTS+=("$r"); done

TEST_DATA="${FEEDVAULT_TEST_DATA:-$LIVE_DATA-test}"
[[ "$TEST_DATA" = /* ]] || die "FEEDVAULT_TEST_DATA must be an absolute path, not '$TEST_DATA'"
TEST_DATA="$(realpath -m -- "$TEST_DATA")"
TEST_MARK="$TEST_DATA/.feedvault-test"

under() { [ "$1" = "$2" ] || [[ "$1" == "${2%/}/"* ]]; }    # $1 is $2 or inside it
for p in "$LIVE_DATA" "${ALL_MEDIA_ROOTS[@]}"; do
  p="$(realpath -m -- "$p")"
  if under "$TEST_DATA" "$p" || under "$p" "$TEST_DATA"; then
    die "Test data dir $TEST_DATA overlaps $p (the live data or a media folder). Set FEEDVAULT_TEST_DATA elsewhere."
  fi
done
for p in "${HOME:-/}" /; do
  p="$(realpath -m -- "$p")"
  if under "$p" "$TEST_DATA"; then
    die "Test data dir $TEST_DATA would hold $p. Set FEEDVAULT_TEST_DATA elsewhere."
  fi
done

marked() { [ -f "$TEST_MARK" ] && [ ! -L "$TEST_MARK" ]; }
if [ "$MODE" != status ] && { [ -e "$TEST_DATA" ] || [ -L "$TEST_DATA" ]; } && ! marked; then
  die "$TEST_DATA exists but has no .feedvault-test marker, so testapp.sh did not make it; nothing was changed. If it is an old test copy, check it, remove it by hand (rm -rf -- '$TEST_DATA') and run again; otherwise set FEEDVAULT_TEST_DATA to a new folder."
fi
# --- test data end ------------------------------------------------------

if [ "$MODE" = status ]; then
  echo "live config   $LIVE_CONFIG"
  echo "live data     $LIVE_DATA"
  echo "test config   $TEST_CONFIG $([ -f "$TEST_CONFIG" ] && echo '(exists)' || echo '(not created yet)')"
  echo "test data     $TEST_DATA $([ -d "$TEST_DATA" ] && echo "($(du -sh "$TEST_DATA" | cut -f1)$(marked || echo ', no .feedvault-test marker: not made here'))" || echo '(not created yet)')"
  echo "test port     $PORT"
  echo "shared, read-only in the sandbox:"
  printf '              %s\n' "${MEDIA_ROOTS[@]}"
  exit 0
fi

# --- test copy ----------------------------------------------------------
if [ "$MODE" = reset ] && [ -d "$TEST_DATA" ]; then
  marked || die "Not removing $TEST_DATA: it has no .feedvault-test marker"
  say "Removing the old copy at $TEST_DATA"
  rm -rf -- "$TEST_DATA"
fi

if [ ! -f "$TEST_DATA/feedvault.db" ]; then
  [ -f "$LIVE_DATA/feedvault.db" ] || die "No database at $LIVE_DATA/feedvault.db"
  say "Copying your database to $TEST_DATA"
  mkdir -p "$TEST_DATA"
  marked || printf 'A test copy made by FeedVault'"'"'s testapp.sh; --reset deletes this folder.\n' > "$TEST_MARK"
  # The backup API gives a consistent copy even while the live app is writing.
  python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d)' \
    "$LIVE_DATA/feedvault.db" "$TEST_DATA/feedvault.db"
fi
# --- test copy end -----------------------------------------------------

python3 - "$TEST_CONFIG" "$LIVE_CONFIG" "$TEST_DATA" <<'PY'
import json, sys
out, live, data = sys.argv[1:]
c = json.load(open(live))
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
  [ -d "$LIVE_DATA" ] && RO+=(--ro-bind "$LIVE_DATA" "$LIVE_DATA")
  RO+=(--ro-bind "$LIVE_CONFIG" "$LIVE_CONFIG")
  say "Sandbox on: media and the live database are read-only for this instance"
  CMD=(bwrap --dev-bind / / "${RO[@]}" --die-with-parent "${CMD[@]}")
else
  warn "bwrap not found — running without the read-only protection"
fi

say "Test instance → http://localhost:$PORT   (live app stays on ${FEEDVAULT_PORT:-3380})"
exec "${CMD[@]}"
