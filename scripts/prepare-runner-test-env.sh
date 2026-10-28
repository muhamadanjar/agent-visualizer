#!/usr/bin/env bash
# Add a generated Runner token and a private, disposable PostgreSQL test DB URL
# to the existing ignored .env without sourcing or printing secret values.
set -Eeuo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require_db

ENV_FILE="$ROOT_DIR/.env"

env_value() {
    local key="$1" line value="" count=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            "$key"=*)
                count=$((count + 1))
                value="${line#*=}"
                ;;
        esac
    done < "$ENV_FILE"
    (( count <= 1 )) || die ".env contains duplicate $key entries"
    printf '%s' "$value"
}

postgres_db="$(env_value POSTGRES_DB)"
migration_url="$(env_value MIGRATION_DATABASE_URL)"
runner_token="$(env_value RUNNER_SHARED_TOKEN)"
launcher_token="$(env_value RUNTIME_LAUNCHER_TOKEN)"
test_database_url="$(env_value TEST_DATABASE_URL)"

[[ "$postgres_db" =~ ^[a-z][a-z0-9_]{0,56}$ ]] || die "POSTGRES_DB must be a safe identifier"
[[ "$migration_url" =~ ^postgresql(\+asyncpg)?://[^/]+/[^/?#]+$ ]] \
    || die "MIGRATION_DATABASE_URL must be a simple PostgreSQL URL"

if [[ -z "$runner_token" ]]; then
    command -v openssl >/dev/null 2>&1 || die "openssl is required to generate RUNNER_SHARED_TOKEN"
    runner_token="$(openssl rand -hex 32)"
fi

if [[ -z "$launcher_token" ]]; then
    command -v openssl >/dev/null 2>&1 || die "openssl is required to generate RUNTIME_LAUNCHER_TOKEN"
    launcher_token="$(openssl rand -hex 32)"
fi
[[ ${#launcher_token} -ge 32 ]] || die "RUNTIME_LAUNCHER_TOKEN must be at least 32 characters"
[[ ${#runner_token} -ge 32 ]] || die "RUNNER_SHARED_TOKEN must be at least 32 characters"

expected_test_database="${postgres_db}_test"
if [[ -z "$test_database_url" ]]; then
    test_database_name="$expected_test_database"
    test_database_url="${migration_url%/*}/$test_database_name"
else
    test_database_name="${test_database_url##*/}"
fi
[[ "$test_database_name" == "$expected_test_database" ]] \
    || die "TEST_DATABASE_URL must target exactly POSTGRES_DB_test"
case "${test_database_url#*://}" in
    *@db:5432/*) ;;
    *) die "TEST_DATABASE_URL must use the private Compose db:5432 host" ;;
esac

query="SELECT 1 FROM pg_database WHERE datname = '$test_database_name'"
exists="$("${COMPOSE[@]}" exec -T db sh -c \
    'exec psql -X -qAt -U "$POSTGRES_USER" -d postgres -c "$1"' \
    sh "$query")"
if [[ "$exists" == "1" ]]; then
    "${COMPOSE[@]}" exec -T db sh -c \
        'exec dropdb -U "$POSTGRES_USER" "$1"' sh "$test_database_name" >/dev/null
fi
"${COMPOSE[@]}" exec -T db sh -c \
    'exec createdb -U "$POSTGRES_USER" "$1"' sh "$test_database_name" >/dev/null

umask 077
tmp="$(mktemp "$ROOT_DIR/.env.XXXXXX")"
trap 'rm -f -- "$tmp"' EXIT
RUNNER_SHARED_TOKEN="$runner_token" RUNTIME_LAUNCHER_TOKEN="$launcher_token" TEST_DATABASE_URL="$test_database_url" \
    awk '
        /^RUNNER_SHARED_TOKEN=/ || /^RUNTIME_LAUNCHER_TOKEN=/ || /^TEST_DATABASE_URL=/ { next }
        { print }
        END {
            printf "RUNNER_SHARED_TOKEN=%s\n", ENVIRON["RUNNER_SHARED_TOKEN"]
            printf "RUNTIME_LAUNCHER_TOKEN=%s\n", ENVIRON["RUNTIME_LAUNCHER_TOKEN"]
            printf "TEST_DATABASE_URL=%s\n", ENVIRON["TEST_DATABASE_URL"]
        }
    ' "$ENV_FILE" > "$tmp"
chmod 600 "$tmp"
mv -f -- "$tmp" "$ENV_FILE"
trap - EXIT

printf 'Runner and launcher tokens configured; secret values were not printed.\n'
printf 'Restart API and Runner to activate the token.\n'
