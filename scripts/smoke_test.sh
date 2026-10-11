#!/bin/bash
# FeedVault — start a built binary on a demo vault and check it serves.
#
#   scripts/smoke_test.sh ./dist/feedvault
#
# Run by CI on every push and by the release workflow before it publishes.
# The binary gets an invented vault (scripts/make_demo.py), a HOME and XDG
# folders of its own in a temp dir and a PATH that holds one stand-in ffmpeg
# and nothing else: it cannot find a real downloader, and nothing of yours is
# read or written. SMOKE_PORT (3392) is the port, SMOKE_PYTHON the Python
# that builds the demo (backend/venv's), SMOKE_VERSION what --version must say.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${1:?usage: scripts/smoke_test.sh <path to the feedvault binary>}"
PORT="${SMOKE_PORT:-3392}"
PY="${SMOKE_PYTHON:-$ROOT/backend/venv/bin/python}"

die() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -x "$BIN" ] || die "No executable at $BIN. Run ./bundle.sh first."
[ -x "$PY" ] || die "No Python at $PY. Run ./bundle.sh first, or set SMOKE_PYTHON."
BIN="$(realpath -- "$BIN")"
# Never the live app's port nor testapp.sh's.
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( 10#$PORT >= 1 && 10#$PORT <= 65535 )) \
  || die "SMOKE_PORT must be a port number from 1 to 65535, not '$PORT'"
[ "$PORT" != 3380 ] && [ "$PORT" != 3389 ] || die "SMOKE_PORT must not be the live or the test app's port ($PORT)"
if "$PY" -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)"; then
  die "Port $PORT is already in use. Set SMOKE_PORT to something else."
fi

WORK="$(mktemp -d)"
PID=
cleanup() {
  [ -z "$PID" ] || kill "$PID" 2>/dev/null || true
  rm -rf -- "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/home" "$WORK/path"
# What the binary and the demo's builder see: nothing of the caller's.
ENV=(env -i "HOME=$WORK/home" "XDG_CONFIG_HOME=$WORK/home/.config" "XDG_DATA_HOME=$WORK/home/.local/share"
     "XDG_CACHE_HOME=$WORK/home/.cache" "XDG_STATE_HOME=$WORK/home/.local/state" "PATH=$WORK/path"
     "LANG=C.UTF-8")

# The one program on its PATH: it says what LD_LIBRARY_PATH a program
# started by FeedVault gets (the bundle's own folder must not be in it).
cat > "$WORK/path/ffmpeg" <<EOF
#!/bin/sh
printf '%s\n' "\${LD_LIBRARY_PATH-unset}" > '$WORK/ld_library_path'
echo "ffmpeg version 0-smoke"
EOF
chmod 755 "$WORK/path/ffmpeg"

"${ENV[@]}" "$PY" "$ROOT/scripts/make_demo.py" "$WORK/demo" >/dev/null
# No sync starts on its own, not even of the demo's stand-in downloaders.
"$PY" - "$WORK/demo/config.json" <<'PY'
import json, sys
path = sys.argv[1]
c = json.load(open(path))
c["schedules_paused"] = True
json.dump(c, open(path, "w"), indent=2)
PY

fail=0
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
bad() { printf '\033[1;31m✗\033[0m %s\n' "$*"; fail=1; }

echo "-- version"
version="$("${ENV[@]}" "$BIN" --version)"
if [[ "$version" == "FeedVault "?* ]] && { [ -z "${SMOKE_VERSION:-}" ] || [ "$version" = "FeedVault $SMOKE_VERSION" ]; }; then
  ok "--version → $version"
else
  bad "--version → '$version' (want FeedVault ${SMOKE_VERSION:-<a version>})"
fi

"${ENV[@]}" "FEEDVAULT_CONFIG=$WORK/demo/config.json" "FEEDVAULT_PORT=$PORT" "FEEDVAULT_NO_BROWSER=1" \
  "$BIN" > "$WORK/log" 2>&1 &
PID=$!

BASE="http://127.0.0.1:$PORT"
H='X-FeedVault: 1'
up=0
for _ in $(seq 1 60); do
  kill -0 "$PID" 2>/dev/null || break
  if curl -sf -o /dev/null -H "$H" "$BASE/api/stats"; then up=1; break; fi
  sleep 0.5
done
if [ "$up" != 1 ]; then cat "$WORK/log"; die "FeedVault did not come up on port $PORT"; fi

# expect <code> <content type prefix or -> <path> [curl args...]
expect() {
  local want=$1 type=$2 path=$3 got; shift 3
  got="$(curl -s -o "$WORK/body" -w '%{http_code} %{content_type}' "$@" "$BASE$path")"
  if [ "${got%% *}" != "$want" ]; then bad "$path → ${got%% *} (want $want)"
  elif [ "$type" != - ] && [[ "${got#* }" != "$type"* ]]; then bad "$path → ${got#* } (want $type)"
  else ok "$path → $got"; fi
}
# The first match of a regex in the last body.
first() { grep -oE "$1" "$WORK/body" | head -n 1 || true; }
field() { "$PY" -c 'import json, sys; print(eval(sys.argv[2], {"d": json.load(open(sys.argv[1]))}))' "$WORK/body" "$1"; }

echo "-- dashboard, from the bundle"
expect 200 text/html /settings
expect 200 text/html /
js="$(first '/assets/index-[A-Za-z0-9_-]+\.js')"
css="$(first '/assets/index-[A-Za-z0-9_-]+\.css')"
[ -n "$js" ] && [ -n "$css" ] || bad "index.html names no script or style sheet"
expect 200 text/javascript "${js:-/assets/missing.js}"
expect 200 image/svg+xml /favicon.svg
expect 200 text/css "${css:-/assets/missing.css}"
font="$(first '/assets/[A-Za-z0-9_-]+\.woff2')"
[ -n "$font" ] || bad "the style sheet names no font"
expect 200 font/woff2 "${font:-/assets/missing.woff2}"
[ "$(head -c 4 "$WORK/body")" = wOF2 ] && ok "$font is a WOFF2 file" || bad "$font is not a WOFF2 file"

echo "-- userscript, on this port"
expect 200 text/javascript /userscript/feedvault.user.js
grep -q "const API_BASE = \"http://localhost:$PORT\"" "$WORK/body" \
  && ok "the userscript talks to port $PORT" || bad "the userscript does not name port $PORT"

echo "-- API"
expect 403 application/json /api/stats
expect 403 application/json /api/stats -H "$H" -H 'Host: evil.example'
expect 200 application/json /api/stats -H "$H"
expect 200 application/json /api/config -H "$H"
[ "$(field 'd["data_directory"]')" = "$WORK/demo/data" ] \
  && ok "data is in the demo vault" || bad "data_directory is $(field 'd["data_directory"]')"
expect 200 application/json /api/people -H "$H"
expect 200 application/json /api/jobs -H "$H"
# The first scan of the demo's media may still be running.
total=0
for _ in $(seq 1 60); do
  expect 200 application/json '/api/posts?kind=image&limit=1' -H "$H" >/dev/null
  total="$(field 'd["total"]')"
  [ "$total" -gt 0 ] && break
  sleep 0.5
done
[ "$total" -gt 0 ] && ok "/api/posts → $total posts with a picture" || bad "/api/posts → no posts"

echo "-- media and thumbnails (Pillow, from the bundle)"
if [ "$total" -gt 0 ]; then
  post="$(field '"/api/posts/%s/%s" % (d["posts"][0]["platform"], d["posts"][0]["post_id"])')"
  expect 200 application/json "$post" -H "$H"
  media="$(field 'next(m["url"] for m in d["media"] if m["kind"] == "image")')"
  expect 200 image/ "$media"
  expect 200 image/jpeg "$media/thumb"
  [ "$(head -c 3 "$WORK/body" | od -An -tx1 | tr -d ' ')" = ffd8ff ] \
    && ok "$media/thumb is a JPEG" || bad "$media/thumb is not a JPEG"
  # Made by Pillow, in the data folder: not the original sent as it is.
  [ -n "$(find "$WORK/demo/data" -name '*.jpg' -print -quit)" ] \
    && ok "the thumbnail was written under the data folder" || bad "no thumbnail under $WORK/demo/data"
fi
expect 404 - /media/999999999/thumb

echo "-- programs it starts do not get the bundle's libraries"
expect 200 application/json /api/downloaders -H "$H"
if [ -f "$WORK/ld_library_path" ]; then
  got="$(cat "$WORK/ld_library_path")"
  [ "$got" = unset ] && ok "LD_LIBRARY_PATH is unset for ffmpeg" || bad "ffmpeg got LD_LIBRARY_PATH=$got"
else
  bad "the stand-in ffmpeg on PATH was not run"
fi

# Last: it takes the server down.
echo "-- quit"
expect 200 application/json /api/quit -X POST -H "$H"
for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
if kill -0 "$PID" 2>/dev/null; then bad "still running after /api/quit"; else ok "exited after /api/quit"; PID=; fi

if grep -q "Traceback" "$WORK/log"; then bad "a traceback in its output:"; cat "$WORK/log"; fi
[ "$fail" = 0 ] || { echo "-- its output"; cat "$WORK/log"; }
exit $fail
