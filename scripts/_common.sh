#!/usr/bin/env bash
# Sourced by operator scripts. Never source a user-controlled .env here: that
# would execute file content. Compose is the only consumer of .env.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose --project-directory "$ROOT_DIR" -f "$ROOT_DIR/docker-compose.yml")

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_compose() {
    command -v docker >/dev/null 2>&1 || die "docker is required"
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
}

require_env() {
    [[ ! -L "$ROOT_DIR/.env" ]] || die ".env must not be a symlink"
    [ -f "$ROOT_DIR/.env" ] || die "missing .env; run scripts/bootstrap-env.sh first"
    local mode
    mode="$(stat -c '%a' "$ROOT_DIR/.env")" || die "cannot stat .env"
    (( (8#$mode & 077) == 0 )) || die ".env is accessible by group/other; chmod 600 .env"
}

require_db() {
    require_compose
    require_env
    "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx 'db' \
        || die "db service is not running; start it with: docker compose up -d db"
    "${COMPOSE[@]}" exec -T db sh -c 'exec pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null \
        || die "db is not accepting connections"
}
