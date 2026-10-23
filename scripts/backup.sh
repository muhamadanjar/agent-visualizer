#!/usr/bin/env bash
# Create a validated PostgreSQL custom-format dump owned by the current user.
# Usage: scripts/backup.sh
set -Eeuo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require_db

backup_dir="$ROOT_DIR/backups"
if [[ -L "$backup_dir" ]]; then
    die "backups directory must not be a symlink"
fi
if [[ ! -e "$backup_dir" ]]; then
    mkdir -m 700 "$backup_dir" || die "cannot create private backups directory"
fi
[[ -d "$backup_dir" ]] || die "backups path is not a directory"
mode="$(stat -c '%a' "$backup_dir")" || die "cannot inspect backups directory"
(( (8#$mode & 077) == 0 )) || die "backups directory is accessible by group/other; chmod 700 backups"
resolved_root="$(realpath -e "$ROOT_DIR")"
resolved_backup="$(realpath -e "$backup_dir")"
[[ "$resolved_backup" == "$resolved_root/backups" ]] || die "backups directory resolves outside the repository"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="$backup_dir/agent-office-$stamp.dump"
[ ! -e "$output" ] || die "backup already exists: $output"

umask 077
tmp="$(mktemp "$backup_dir/.incomplete.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

"${COMPOSE[@]}" exec -T db sh -c \
    'set -eu; exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
    >"$tmp"

[ -s "$tmp" ] || die "pg_dump produced an empty file"
"${COMPOSE[@]}" exec -T db pg_restore --list <"$tmp" >/dev/null \
    || die "dump failed pg_restore --list validation"

chmod 600 "$tmp"
ln -- "$tmp" "$output" || die "backup already exists or cannot be published"
rm -f -- "$tmp"
trap - EXIT

printf 'backup written: %s\n' "$output"
