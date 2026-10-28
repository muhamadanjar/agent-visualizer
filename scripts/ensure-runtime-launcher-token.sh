#!/usr/bin/env bash
# Add only the runtime launcher's bearer token to an existing ignored .env.
# The existing file is never printed or replaced with unrelated values.
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die "regular root .env file is required"
command -v openssl >/dev/null 2>&1 || die "openssl is required"

count=0
token=""
while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
        RUNTIME_LAUNCHER_TOKEN=*)
            count=$((count + 1))
            token="${line#*=}"
            ;;
    esac
done < "$ENV_FILE"
(( count <= 1 )) || die ".env contains duplicate RUNTIME_LAUNCHER_TOKEN entries"
if (( count == 1 )); then
    [[ ${#token} -ge 32 ]] || die "RUNTIME_LAUNCHER_TOKEN must be at least 32 characters"
    printf 'RUNTIME_LAUNCHER_TOKEN is already configured; no file changes made.\n'
    exit 0
fi

token="$(openssl rand -hex 32)"
tmp="$(mktemp "$ROOT_DIR/.env.XXXXXX")"
trap 'rm -f -- "$tmp"' EXIT
cat -- "$ENV_FILE" > "$tmp"
printf '\nRUNTIME_LAUNCHER_TOKEN=%s\n' "$token" >> "$tmp"
chmod 600 "$tmp"
mv -f -- "$tmp" "$ENV_FILE"
trap - EXIT
printf 'Added RUNTIME_LAUNCHER_TOKEN to .env without printing its value.\n'
