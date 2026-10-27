# Private pilot operations

This directory documents the local deployment boundary. Phase 1/M0 is complete
only for bounded provider/3D feasibility probes; it does not prove managed
execution. Phase 2 foundation is complete. Phase 3 policy/dispatch and Phase 4
Runner event paths have implementation and isolated PostgreSQL/component test
evidence. Phase 6 Runner execution is in progress, not operationally qualified.
Backup/restore evidence is recorded in `docs/progress.md`; it does not prove a
live provider task or 3D runtime readiness.

## Topology and exposure boundary

Docker Compose runs PostgreSQL, the FastAPI API, the Next.js web app, an isolated
Runner, a one-shot migration service, and an Nginx reverse proxy on one private
Linux host:

- Nginx is the only published service: `127.0.0.1:${APP_PORT:-8080}:80`.
- Web (`3000`), API (`8000`), and Runner have no published ports. Web/API are
  reachable only on the Compose app network; Runner is app-network-only and
  has no direct database network access.
- PostgreSQL (`5432`) has no host-published port. The one-shot `migrate`
  service applies checksummed SQL migrations, applies Alembic revisions, and
  provisions the restricted API role before API startup; it is not a worker.
- Runner claims persisted dispatches from the API and mounts `./workspaces`
  plus `./.runner-state`. It runs non-root, with a read-only root filesystem,
  dropped Linux capabilities, and no-new-privileges. Its configured provider
  endpoint still needs OS/container egress enforcement; application URL checks
  alone are not a network firewall.
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
Do not add port mappings for `db`, `api`, `web`, or `runner`.

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
   `python -m app.password_hash` helper. It generates the PostgreSQL password,
   session secret, and Runner shared token with `openssl`, plus default Runner
   UID/GID values. It writes `.env` atomically with mode `600` and prints no
   secret values. It builds a temporary local API image to run the hash helper;
   the plaintext password is sent on stdin, never on a command line or in the
   environment.

   Optional non-secret overrides: `APP_PORT`, `PUBLIC_ORIGIN`,
   `OWNER_USERNAME`, `POSTGRES_DB`, `POSTGRES_USER`, `APP_DB_USER`,
   `RUNNER_UID`, and `RUNNER_GID`.
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

- `APP_DB_USER`/`APP_DB_PASSWORD` provision a separate non-admin API role. The
  API receives only `DATABASE_URL`; the migration service applies project,
  versioned definition/skill, Agent Instance, Task/Run, provider configuration,
  workspace policy, Runner connection/dispatch, activity/projection, approval,
  artifact-metadata, and scene-version schemas. Grants are scoped per table:
  mutable owner records get the required `SELECT`/`INSERT`/`UPDATE`;
  append-only versions, events, decisions, artifacts, and scene versions get
  `SELECT`/`INSERT`. The role cannot update/delete immutable evidence or
  administer the database.
- `MIGRATION_DATABASE_URL` is the one-shot migration administrator connection.
- `SESSION_SECRET` is at least 32 random bytes; rotating it invalidates
  sessions.
- `OWNER_PASSWORD_HASH` is Argon2id; the plaintext owner password is never
  stored by this stack.
- `PUBLIC_ORIGIN` is the exact browser origin used for login and WebSocket
  Origin validation.
- `OWNER_USERNAME` identifies the single owner. Server-side owner/resource
  scope remains authoritative; the browser cannot assert an owner ID.
- `RUNNER_SHARED_TOKEN` authenticates internal Runner calls; keep it secret.
  `NINEROUTER_URL` and `NINEROUTER_KEY` configure the provider endpoint/key for
  Runner use only. `RUNNER_UID`/`RUNNER_GID` should match host ownership of
  `workspaces/` and `.runner-state/`.
- `SESSION_TTL_SECONDS` is capped at 8 hours. Keep it at or below `28800`.

`.env`, `.env.*`, and `backups/` are git-ignored; `.env.example` contains only
placeholders. The API fails closed on missing or weak production secrets.

When running the API directly for development, install `apps/api/requirements.txt`.
For example, from `apps/api/`:

```bash
rtk uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

The API loads the repository-root `.env` automatically; variables already
exported in the process take precedence. This does not change Compose startup,
which continues to inject its configured environment.

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

## Schema evolution

- The checksummed SQL files `apps/api/migrations/0001`–`0008` are the immutable legacy baseline. The one-shot runner applies them before Alembic; do not edit an applied SQL file because its checksum is verified at startup.
- SQLModel table metadata in `apps/api/app/infrastructure/postgres/models.py` is the default schema definition, and ordinary runtime repository CRUD uses SQLModel/SQLAlchemy async sessions. Generate versioned revisions with Alembic, review the generated operations, then apply them through the migration service.
- Raw SQL remains available through the session-backed adapter for PostgreSQL-specific runtime paths such as advisory locks, recursive CTEs, and atomic event/projection updates. Prefer `op.execute()` in Alembic revisions for PostgreSQL-specific DDL/data operations; the legacy checksummed SQL runner also remains available. Do not replace explicit per-table grants with broad grants when adding tables.

From the repository root, with the Compose environment configured:

```bash
rtk docker compose run --rm --entrypoint alembic migrate -c /app/alembic.ini revision --autogenerate -m "describe schema change"
```

Review the generated revision before applying it:

```bash
rtk docker compose run --rm migrate
```

Update `ensure_application_role()` in `apps/api/app/infrastructure/postgres/migrate.py` when a new application table needs access. Its default is no application-role access until an explicit grant is added.

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

## Known Phase 3–6 limits

- Phase 3 workspace policy/tool allowlist, Run dispatch, and same-workspace
  serialization are implemented and passed restricted-role PostgreSQL 17
  integration on a disposable `*_test` database. API-to-Runner full task
  execution remains unverified.
- Phase 4 internal Runner heartbeat/claim/event routes authenticate with the
  shared token, derive scope from persisted dispatch, and acknowledge durable
  deduplicated events. Action summaries are fixed labels from the file-tool
  allowlist. Component tests pass; deployed restart/reconnect reconciliation
  remains open.
- Phase 6 Runner supports bounded non-streaming Chat Completions with
  `list_files`, `read_file`, and `write_file`; a local HTTP-mock tool loop and
  SQLite outbox restart tests pass. No live configured-provider Run or
  end-to-end live 3D visualization has been observed.
- Active Runs sharing one configured workspace path are serialized. The Runner
  does not create a per-Run clone/worktree, expose a shell, or isolate writes
  inside that path from an operator editing it concurrently.
- Provider URL validation disables redirects but is not an OS/container
  egress firewall. Provider-only egress, cancellation/acknowledgement,
  final-answer/artifact persistence, and two independent live Runs remain open.
- Owner sessions are opaque random tokens held in API-process memory. Logout
  revokes the server-side session immediately; API restart invalidates all active
  sessions. A shared session store is required before scaling beyond one API
  replica.
- Login rate limiting is per API process; limits reset on restart and are not
  shared across replicas.
- There is no TLS termination, public ingress, or automated off-host backup.
- Only one owner exists; there is no user administration or multi-tenant scope.
