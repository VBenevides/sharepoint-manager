#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

die() {
    printf 'create-release: %s\n' "$*" >&2
    exit 1
}

version=$(tr -d '\r\n' < VERSION)
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] ||
    die "invalid VERSION: $version"

notes_file=$(mktemp)
trap 'rm -f "$notes_file"' EXIT
awk -v version="$version" '
    index($0, "## [" version "]") == 1 { printing=1; next }
    printing && /^## \[/ { exit }
    printing { print }
' CHANGELOG.md > "$notes_file"
[[ -s $notes_file ]] || die "CHANGELOG.md has no notes for $version"

if [[ ${1:-} == --dry-run ]]; then
    printf 'Release v%s from main\n\n' "$version"
    cat "$notes_file"
    exit
fi
[[ $# == 0 ]] || die "usage: ./create-release.sh [--dry-run]"

command -v gh >/dev/null || die "GitHub CLI (gh) is required"
[[ $(git branch --show-current) == main ]] || die "run from the main branch"
[[ -z $(git status --porcelain) ]] || die "working tree is not clean"
git fetch --quiet origin main
[[ $(git rev-parse HEAD) == $(git rev-parse origin/main) ]] ||
    die "main does not match origin/main"

gh auth status >/dev/null
gh release create "v$version" --target main --title "v$version" --notes-file "$notes_file"
