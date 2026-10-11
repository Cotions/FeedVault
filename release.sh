#!/bin/bash
# FeedVault — cut a release.
#
#   ./release.sh v0.1.0
#
# Tags the current commit and pushes the branch and the tag. GitHub Actions
# (.github/workflows/release.yml) then builds the binary with that version in
# it, starts it once on a demo vault, and publishes it as a GitHub release.
# Nothing is built here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

say() { printf '\033[1;32m▸\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

VERSION=
for arg in "$@"; do
  case "$arg" in
    # The comment block under the #! line, up to the first line that is not one.
    --help|-h) awk 'NR == 1 { next } !/^#/ { exit } { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    -*) die "Unknown option: $arg (try --help)" ;;
    *) [ -z "$VERSION" ] || die "One version only (try --help)"; VERSION="$arg" ;;
  esac
done

# The same pattern as the workflow's, which refuses any other tag.
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]] \
  || die "Usage: ./release.sh v1.2.3"

[ -z "$(git status --porcelain)" ] || die "Working tree is dirty. Commit first."
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" != HEAD ] || die "Not on a branch (detached HEAD)."
! git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null || die "Tag $VERSION already exists."

say "Tagging $VERSION on $BRANCH ($(git rev-parse --short HEAD))"
read -rp "Push the tag and publish a release? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || die "Aborted."

git tag -a "$VERSION" -m "FeedVault $VERSION"
git push origin "$BRANCH"
git push origin "$VERSION"

say "Pushed. Watch the build:"
say "  https://github.com/Cotions/FeedVault/actions/workflows/release.yml"
