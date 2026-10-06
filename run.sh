#!/bin/bash
# FeedVault — one-click start from source.
#
#   ./run.sh          build the UI if needed, start the backend, open the browser
#   ./run.sh --dev    Vite dev server with hot reload + backend (proxied, real data)
#   ./run.sh --build  rebuild the UI, then start
#   ./run.sh --test   run the backend tests
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

# --- relaunch inside a terminal when double-clicked ------------------------
if ! [ -t 0 ] && ! [ -t 1 ] && [ -z "${TERM:-}" ] && [ "${FEEDVAULT_NO_TERMINAL:-}" != "1" ]; then
  for term in gnome-terminal xfce4-terminal konsole xterm; do
    command -v "$term" &>/dev/null || continue
    case "$term" in
      gnome-terminal) exec "$term" -- bash "$0" "$@" ;;
      konsole)        exec "$term" -e bash "$0" "$@" ;;
      *)              exec "$term" -e "bash \"$0\" $*" ;;
    esac
  done
  exit 0
fi

MODE=start
for arg in "$@"; do
  case "$arg" in
    --dev)   MODE=dev ;;
    --build) MODE=build ;;
    --test)  MODE=test ;;
    # The comment block under the #! line, up to the first line that is not one.
    --help|-h) awk 'NR == 1 { next } !/^#/ { exit } { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
  esac
done

VENV="$ROOT/backend/venv"
DIST="$ROOT/frontend/dist"

say() { printf '\033[1;32m▸\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }


command -v python3 &>/dev/null || die "python3 not found"

# --- python deps ----------------------------------------------------------
if [ ! -d "$VENV" ]; then
  say "Creating virtualenv"
  python3 -m venv "$VENV"
fi
if [ ! -f "$VENV/.deps-ok" ] || [ "$ROOT/backend/requirements.txt" -nt "$VENV/.deps-ok" ] \
   || [ "$ROOT/backend/requirements-dev.txt" -nt "$VENV/.deps-ok" ]; then
  say "Installing Python dependencies"
  "$VENV/bin/pip" install -q -r "$ROOT/backend/requirements-dev.txt"
  touch "$VENV/.deps-ok"
fi

if [ "$MODE" = test ]; then
  exec "$VENV/bin/python" -m pytest -q "$ROOT/backend/tests"
fi

# --- port -----------------------------------------------------------------
# Checked, then exported, so the backend and the Vite dev server's proxy
# (frontend/vite.config.js) always use this same port (#100). Set but empty
# is no port either, as for the backend (config.py) and Vite.
PORT="${FEEDVAULT_PORT-3380}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( 10#$PORT >= 1 && 10#$PORT <= 65535 )) \
  || die "FEEDVAULT_PORT must be a port number from 1 to 65535, not '$PORT'"
PORT=$((10#$PORT))
export FEEDVAULT_PORT="$PORT"
# --- port end -------------------------------------------------------------

# --- port check -----------------------------------------------------------
if python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)"; then
  say "Port $PORT already in use."
  read -rp "Kill it and continue? [y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || die "Aborted."
  fuser -k "${PORT}/tcp" 2>/dev/null || true
  sleep 1
fi

# --- frontend -------------------------------------------------------------
pkg_manager() {
  if command -v bun &>/dev/null; then echo bun
  elif command -v npm &>/dev/null; then echo npm
  else return 1; fi
}

ui_is_stale() {
  [ ! -f "$DIST/index.html" ] && return 0
  [ -n "$(find "$ROOT/frontend/src" "$ROOT/frontend/index.html" -newer "$DIST/index.html" -print -quit 2>/dev/null)" ]
}

build_ui() {
  local pm; pm="$(pkg_manager)" || die "Need bun or npm to build the UI"
  [ -d "$ROOT/frontend/node_modules" ] || { say "Installing UI dependencies ($pm)"; (cd "$ROOT/frontend" && "$pm" install); }
  say "Building UI ($pm)"
  (cd "$ROOT/frontend" && "$pm" run build)
}

if [ "$MODE" = dev ]; then
  pm="$(pkg_manager)" || die "Need bun or npm for dev mode"
  [ -d "$ROOT/frontend/node_modules" ] || (cd "$ROOT/frontend" && "$pm" install)
  say "Starting Vite dev server (proxies /api and /media to :$PORT)"
  (cd "$ROOT/frontend" && "$pm" run dev) &
  VITE_PID=$!
  trap 'kill $VITE_PID 2>/dev/null || true' EXIT
  say "Starting backend on :$PORT (UI at the Vite URL above)"
  FEEDVAULT_NO_BROWSER=1 "$VENV/bin/python" "$ROOT/backend/app.py"
  exit
fi

if [ "$MODE" = build ] || ui_is_stale; then
  build_ui
fi

say "Starting FeedVault on http://localhost:$PORT"
exec "$VENV/bin/python" "$ROOT/backend/app.py" "$@"
