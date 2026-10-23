#!/usr/bin/env bash
# Restore a backup into a NEW scratch database. The live database is never
# dropped, renamed, or written to by this script.
# Usage: scripts/restore.sh <backup.dump> [--target <new-database-name>]
set -Eeuo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require_db

usage() {
    printf 'usage: %s <backup.dump> [--target <new-database-name>]\n' "${0##*/}" >&2
}

dump="${1:-}"
[ -n "$dump" ] || { usage; exit 2; }
shift
[ -f "$dump" ] || die "backup file not found: $dump"
[ -s "$dump" ] || die "backup file is empty: $dump"

target=""
while (( $# > 0 )); do
    case "$1" in
        --target)
            (( $# >= 2 )) || { usage; exit 2; }
            target="$2"
            shift 2
            ;;
        *)
            usage
            die "unknown argument: $1"
            ;;
    esac
done

[ -n "$target" ] || target="agent_office_restore_$(date -u +%Y%m%d%H%M%S)"
[[ "$target" =~ ^[a-z][a-z0-9_]{0,62}$ ]] \
    || die "target database name must match ^[a-z][a-z0-9_]{0,62}$"
case "$target" in
    postgres|template0|template1) die "refusing reserved database name: $target" ;;
esac

live_db="$("${COMPOSE[@]}" exec -T db sh -c 'printf "%s" "$POSTGRES_DB"')"
[ "$target" != "$live_db" ] || die "refusing to restore over the live database: $live_db"

# Validate the archive before creating anything.
"${COMPOSE[@]}" exec -T db pg_restore --list <"$dump" >/dev/null \
    || die "not a valid PostgreSQL custom-format dump: $dump"

exists="$("${COMPOSE[@]}" exec -T db sh -c \
    "exec psql --username=\"\$POSTGRES_USER\" --dbname=postgres --tuples-only --no-align --command=\"SELECT 1 FROM pg_database WHERE datname = '$target'\"")"
[ -z "$exists" ] || die "target database already exists: $target"

"${COMPOSE[@]}" exec -T db sh -c \
    'exec createdb --username="$POSTGRES_USER" --encoding=UTF8 --template=template0 "$1"' \
    sh "$target"

if ! "${COMPOSE[@]}" exec -T db sh -c \
    'exec pg_restore --username="$POSTGRES_USER" --dbname="$1" --no-owner --no-privileges --clean --if-exists --exit-on-error' \
    sh "$target" <"$dump"; then
    printf 'restore failed; live database "%s" was not modified. Left scratch database "%s" for inspection.\n' \
        "$live_db" "$target" >&2
    exit 1
fi

"${COMPOSE[@]}" exec -T db sh -c \
    'exec psql --username="$POSTGRES_USER" --dbname="$1" --tuples-only --no-align --command="SELECT 1"' \
    sh "$target" >/dev/null \
    || die "restore finished but the scratch database is not reachable: $target"

printf 'restore verified in scratch database: %s\n' "$target"
printf 'live database unchanged: %s\n' "$live_db"
printf 'inspect with: docker compose exec db psql -U <owner-role> -d %s\n' "$target"
