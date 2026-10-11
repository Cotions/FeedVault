#!/bin/bash
# FeedVault — build one self-contained executable.
#
#   ./bundle.sh             build ./dist/feedvault
#   ./bundle.sh --install   build, then install to ~/.local/bin + app menu entry
#   ./bundle.sh --uninstall remove the installed copy (config and data stay)
#
# The binary carries its own Python, the backend, the built UI and the
# userscript. It reads ~/.config/feedvault/config.json like ./run.sh does and
# writes nothing next to itself. The downloaders and ffmpeg are not in it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/backend/venv"
OUT="$ROOT/dist/feedvault"

BIN_DIR="$HOME/.local/bin"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
APP_DIR="$DATA_HOME/applications"

say()  { printf '\033[1;32m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

MODE=build
for arg in "$@"; do
  case "$arg" in
    --install)   MODE=install ;;
    --uninstall) MODE=uninstall ;;
    # The comment block under the #! line, up to the first line that is not one.
    --help|-h) awk 'NR == 1 { next } !/^#/ { exit } { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    *) die "Unknown option: $arg (try --help)" ;;
  esac
done

refresh_menu() {
  command -v update-desktop-database &>/dev/null && update-desktop-database "$APP_DIR" 2>/dev/null || true
}

# --- uninstall ------------------------------------------------------------
if [ "$MODE" = uninstall ]; then
  rm -f "$BIN_DIR/feedvault" "$ICON_DIR/feedvault.svg" "$APP_DIR/FeedVault.desktop"
  refresh_menu
  say "Uninstalled. Your config, database and media were left untouched."
  exit 0
fi

command -v python3 &>/dev/null || die "python3 not found"

# --- UI -------------------------------------------------------------------
if command -v bun &>/dev/null; then PM=bun
elif command -v npm &>/dev/null; then PM=npm
else die "Need bun or npm to build the UI"; fi

[ -d "$ROOT/frontend/node_modules" ] || { say "Installing UI dependencies ($PM)"; (cd "$ROOT/frontend" && "$PM" install); }
say "Building UI ($PM)"
(cd "$ROOT/frontend" && "$PM" run build)
[ -f "$ROOT/frontend/dist/index.html" ] || die "UI build produced no index.html"

# --- binary ---------------------------------------------------------------
if [ ! -d "$VENV" ]; then
  say "Creating virtualenv"
  python3 -m venv "$VENV"
fi
say "Installing build dependencies"
"$VENV/bin/pip" install -q -r "$ROOT/backend/requirements.txt" -r "$ROOT/packaging/requirements-build.txt"

say "Freezing with PyInstaller (this takes a minute)"
rm -rf "$ROOT/build" "$ROOT/dist"
"$VENV/bin/pyinstaller" \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build" \
  --noconfirm --clean --log-level WARN \
  "$ROOT/packaging/feedvault.spec"

[ -f "$OUT" ] || die "Build failed: $OUT not found"
say "Built $OUT ($(du -h "$OUT" | cut -f1), $("$OUT" --version))"

if [ "$MODE" = build ]; then
  say "Run it:      $OUT"
  say "Install it:  ./bundle.sh --install"
  exit 0
fi

# --- install --------------------------------------------------------------
mkdir -p "$BIN_DIR" "$ICON_DIR" "$APP_DIR"
install -m 755 "$OUT" "$BIN_DIR/feedvault"
install -m 644 "$ROOT/packaging/feedvault.svg" "$ICON_DIR/feedvault.svg"
# Quoted as the desktop entry spec wants, so a space in $HOME does not split
# it. A path with a character that needs more than quotes there (or that sed
# reads) gets no menu entry rather than a wrong one.
case "$BIN_DIR" in
  *[\\\"\`\$%\|\&]*|*$'\n'*)
    warn "No app menu entry: $BIN_DIR has a character a desktop entry cannot hold as it is."
    say "Installed $BIN_DIR/feedvault"
    exit 0 ;;
esac
sed -e "s|__EXEC__|\"$BIN_DIR/feedvault\"|" -e "s|__ICON__|feedvault|" \
  "$ROOT/packaging/FeedVault.desktop" > "$APP_DIR/FeedVault.desktop"
chmod 644 "$APP_DIR/FeedVault.desktop"
refresh_menu

say "Installed $BIN_DIR/feedvault. Launch it from your app menu, or run: feedvault"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH." ;;
esac
say "It reads the same config as ./run.sh, so it opens the same library: run one or the other, not both."
