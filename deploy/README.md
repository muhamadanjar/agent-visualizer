# Private pilot operations

This directory contains the local deployment boundary for the Phase 2
application foundation. Phase 1/M0 remains in progress; Phase 2 is complete and
provides the private web/API/database foundation and minimal owner-scoped
project persistence, plus a one-shot migration runner. Dispatcher and runner
process boundaries are documented, but execution is deferred until later phases.
Backup/restore evidence is recorded in `docs/progress.md`; this does not prove
runtime readiness.

## Topology and exposure boundary

Docker Compose runs PostgreSQL, the FastAPI API, the Next.js web app, and an
Nginx reverse proxy on one private Linux host:

- Nginx is the only published service: `127.0.0.1:${APP_PORT:-8080}:80`.
- Web (`3000`) and API (`8000`) are reachable only on the Compose network.
- PostgreSQL (`5432`) has no host-published port. The one-shot `migrate`
  service applies checksummed SQL migrations and provisions the restricted API
  role before the API starts; it is not a long-running worker.
- Nginx sends `/api/` (including `/api/v1/ws`) and `/health/` to the API, and
  everything else to the web app. The WebSocket upgrade headers and the
  browser's `Origin` header are forwarded unchanged.
- Nginx does not terminate TLS. HTTP is reachable only from the host loopback
  interface. Use an SSH tunnel
  (`rtk ssh -L 8080:127.0.0.1:8080 <host>`) or a VPN for remote access.
- Exposing this stack beyond loopback requires a reviewed TLS reverse proxy or
  load balancer in front of it. Then set `PUBLIC_ORIGIN=https://<exact-host>`,
  `APP_ENV=production`, and `SESSION_COOKIE_SECURE=true`. The API rejects an
  insecure production origin. Because WebSocket origin matching is exact, use
  only that configured hostname; do not add alternate public names without
  changing the configuration deliberately.
- The browser never receives a shell/CLI or database route. Database access is
  only through Compose `exec` for operator scripts.

Do not add port mappings for `db`, `api`, or `web`.

## Prerequisites

- Linux host with Docker Engine and Docker Compose v2.
- `openssl`, `bash`, `stat`, and `realpath` for bootstrap and backup scripts.
- Enough disk for the PostgreSQL volume and backups. Dumps contain private application data even though credentials are not included.

## First-time setup

1. Bootstrap the ignored environment file:

   ```bash
   rtk ./scripts/bootstrap-env.sh
   ```

   The script refuses to overwrite an existing `.env`, prompts for the owner
   password twice without echoing it, and hashes it with the API image's
   `python -m app.password_hash` helper. It generates the PostgreSQL password
   and session secret with `openssl`, writes `.env` atomically with mode `600`,
   and prints no secret values. It builds a temporary local API image to run the
   hash helper; the plaintext password is sent on stdin, never on a command
   line or in the environment.

   Optional non-secret overrides: `APP_PORT`, `PUBLIC_ORIGIN`,
   `OWNER_USERNAME`, `POSTGRES_DB`, `POSTGRES_USER`, and `APP_DB_USER`.
   Leave `OWNER_PASSWORD_HASH` unset to enter a password without echo. The
   bootstrap hashes it using Argon2id and does not print the result.

2. Review `.env`. The default loopback HTTP setup uses
   `APP_ENV=development`, `SESSION_COOKIE_SECURE=false`, and
   `SESSION_COOKIE_SAMESITE=strict`; cookies remain HttpOnly. This is acceptable
   only inside the documented loopback boundary. Production requires HTTPS and
   secure cookies as described above.

3. Build and start:

   ```bash
   rtk docker compose build
   rtk docker compose up -d
   rtk docker compose ps
   ```

4. Check readiness and reach the app:

   ```bash
   rtk curl -fsS http://127.0.0.1:8080/health/live
   rtk curl -fsS http://127.0.0.1:8080/health/ready
   ```

   Open `http://127.0.0.1:8080` and log in as the configured owner. There is no
   signup route or public user.

`docker compose down` stops services and keeps the `db_data` volume. Do not run
`docker compose down -v` unless you intend to destroy the database.

## Configuration and secrets boundary

All secrets and URLs are supplied at runtime by `.env` through Compose. Nothing
is baked into the application images, and no default production credentials
exist:

- `POSTGRES_USER`/`POSTGRES_PASSWORD` are database-administrator credentials
  available to PostgreSQL and the one-shot migration service only.
- `APP_DB_USER`/`APP_DB_PASSWORD` provision a separate non-superuser API role.
  The API receives only `DATABASE_URL`; the migration service applies the
  project schema, revokes inherited/admin privileges, and grants only project
  reads and inserts.
- `MIGRATION_DATABASE_URL` is the one-shot migration administrator connection.
- `SESSION_SECRET` is at least 32 random bytes; rotating it invalidates
  sessions.
- `OWNER_PASSWORD_HASH` is Argon2id; the plaintext owner password is never
  stored by this stack.
- `PUBLIC_ORIGIN` is the exact browser origin used for login and WebSocket
  Origin validation.
- `OWNER_USERNAME` identifies the single owner. Server-side owner/resource
  scope remains authoritative; the browser cannot assert an owner ID.
- `SESSION_TTL_SECONDS` is capped at 8 hours. Keep it at or below `28800`.

`.env`, `.env.*`, and `backups/` are git-ignored; `.env.example` contains only
placeholders. The API fails closed on missing or weak production secrets.

To rotate the owner password, build the API image and feed the new password to
the Argon2id helper through stdin. The helper prints only the encoded hash;
treat that output as credential material:

```bash
rtk docker compose build api
read -r -s -p "New owner password: " owner_password
printf '\n'
printf '%s\n' "$owner_password" | rtk docker compose run --rm -T --no-deps --entrypoint python api -m app.password_hash
unset owner_password
```

Replace the quoted `OWNER_PASSWORD_HASH` in `.env` and restart the API with
`rtk docker compose up -d --force-recreate api`. Rotate `SESSION_SECRET` too
when existing signed sessions must be invalidated. The migration process
reconciles the `APP_DB_USER` role password from `APP_DB_PASSWORD`; stop the API,
run `rtk docker compose run --rm migrate`, then recreate the API when rotating
that password. Rotate the PostgreSQL admin password separately in PostgreSQL
and update `POSTGRES_PASSWORD` plus `MIGRATION_DATABASE_URL`; changing only the
Compose value does not change an initialized database role.

## Logs and health

```bash
rtk docker compose ps
rtk docker compose logs --tail=100 api
rtk docker compose logs -f proxy
```

The API emits structured logs with correlation IDs and returns a correlation
header; Nginx passes request headers through unchanged. Secrets, cookies,
passwords, raw login bodies, and prompts must not appear in logs. Docker log
rotation is capped at three 10 MiB files per service.

Health endpoints: `/health/live` proves the process is up; `/health/ready`
indicates dependencies are ready. A healthy container is not evidence that a
domain workflow or runtime works.

## Backup

`scripts/backup.sh` creates a PostgreSQL custom-format dump through the
running `db` container:

```bash
rtk ./scripts/backup.sh
```

The script verifies that PostgreSQL is accepting connections, writes to the
repository's `backups/` directory (which must not be a symlink and must have
mode `700`) with a mode-`600` temporary file, validates the archive with
`pg_restore --list`, and publishes it without overwriting an existing dump. It
does not print secrets.

Run one backup manually first to create `backups/` before cron's lock and log
paths are used. Then schedule a daily backup (24-hour RPO target) with an
absolute path, for example:
```cron
15 3 * * * cd /path/to/agent-office-visualizer && rtk flock -n backups/.backup.lock ./scripts/backup.sh >> backups/backup.log 2>&1
```

The scripts do not prune old dumps automatically. Review and copy them to
access-controlled storage; deleting backups is an explicit operator action.
Backups taken while the API is running are consistent PostgreSQL snapshots, but
restore verification is still required.

## Non-destructive restore

```bash
rtk ./scripts/restore.sh backups/agent-office-<timestamp>.dump
```

The script creates a **new** database named
`agent_office_restore_<UTC timestamp>` and restores into it. It validates the
archive before creating the database, rejects reserved names, rejects the
configured live database name, and refuses to touch an existing target. It
never drops, renames, truncates, or writes to the live database.

Restore only backups from a trusted source. `pg_restore` runs with the Compose
database administrator; archive listing and `--no-owner`/`--no-privileges` do
not sandbox archive contents. Inspect untrusted dumps only in a disposable,
isolated PostgreSQL instance.

To choose a target name explicitly:

```bash
rtk ./scripts/restore.sh backups/agent-office-<timestamp>.dump --target agent_office_restore_check
```

On success it prints only the scratch and live database names and verifies the
scratch database is reachable. Inspect it read-only:

```bash
rtk docker compose exec db sh -c 'exec psql -U "$POSTGRES_USER" -d agent_office_restore_check'
```

A failed restore leaves the scratch database for inspection and leaves the live
database untouched; examine the API/`pg_restore` output before deciding whether
to remove that scratch database. Removing a scratch database is destructive;
verify its name first. This script intentionally has no live-replacement mode.
Promoting a verified restore requires a separate, reviewed maintenance
procedure with fresh backups and stopped writers.

The restore script is intentionally non-destructive. The Phase 2 restore
demonstration and its scratch-database evidence are recorded in
`docs/progress.md`; repeat it after material schema or backup changes.

## Known Phase 2 limits

- Phase 1/M0 remains in progress; its gates are neither complete nor waived.
- The API currently persists only minimal owner-scoped project records. It does
  not yet implement canonical task/run/event domain state.
- Dispatcher/runner boundaries are documented under `apps/dispatcher/` and
  `apps/runner/`, but there is no production dispatch, runtime, provider, or
  work-execution implementation; those remain later-phase work.
- Owner sessions are opaque random tokens held in API-process memory. Logout
  revokes the server-side session immediately; API restart invalidates all active
  sessions. A shared session store is required before scaling beyond one API
  replica.
- Login rate limiting is per API process; limits reset on restart and are not
  shared across replicas.
- There is no TLS termination, public ingress, or automated off-host backup.
- Only one owner exists; there is no user administration or multi-tenant scope.
