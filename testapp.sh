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

read -r LIVE_DATA < <(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data_directory"])' "$LIVE_CONFIG")
TEST_DATA="${FEEDVAULT_TEST_DATA:-${LIVE_DATA%/}-test}"
[ "$TEST_DATA" != "$LIVE_DATA" ] || die "Test data dir must differ from the live one ($LIVE_DATA)."

mapfile -t MEDIA_ROOTS < <(python3 - "$LIVE_CONFIG" <<'PY'
import json, os, sys
for r in json.load(open(sys.argv[1])).get("media_roots") or []:
    if os.path.isdir(r):
        print(r)
PY
)

if [ "$MODE" = status ]; then
  echo "live config   $LIVE_CONFIG"
  echo "live data     $LIVE_DATA"
  echo "test config   $TEST_CONFIG $([ -f "$TEST_CONFIG" ] && echo '(exists)' || echo '(not created yet)')"
  echo "test data     $TEST_DATA $([ -d "$TEST_DATA" ] && echo "($(du -sh "$TEST_DATA" | cut -f1))" || echo '(not created yet)')"
  echo "test port     $PORT"
  echo "shared, read-only in the sandbox:"
  printf '              %s\n' "${MEDIA_ROOTS[@]}"
  exit 0
fi

if [ "$MODE" = reset ] && [ -d "$TEST_DATA" ]; then
  say "Removing the old copy at $TEST_DATA"
  rm -rf "$TEST_DATA"
fi

if [ ! -f "$TEST_DATA/feedvault.db" ]; then
  [ -f "$LIVE_DATA/feedvault.db" ] || die "No database at $LIVE_DATA/feedvault.db"
  say "Copying your database to $TEST_DATA"
  mkdir -p "$TEST_DATA"
  # The backup API gives a consistent copy even while the live app is writing.
  python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d)' \
    "$LIVE_DATA/feedvault.db" "$TEST_DATA/feedvault.db"
fi

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
