# Private pilot operations

The roadmap's Phase 2 (Agent Runtime) is Complete with live successful Runs,
cancellation, restart recovery, and overlapping execution recorded in
[`docs/progress.md`](../docs/progress.md). Roadmap Phase 3 (Event System) is
Complete; Phase 4 (Virtual Office 3D) remains In Progress. Phase 3 outage,
reconnect, and isolated fan-out evidence is recorded in the progress register.
This deployment note does not claim that a healthy container proves a domain
workflow.

## Topology and exposure boundary

Docker Compose runs PostgreSQL, private Redis Streams, the FastAPI API, the Next.js web app, a
host-side Runner supervisor, a separate Runtime Launcher, a one-shot migration
service, and an Nginx reverse proxy on one private Linux host:

- Nginx is the only published service: `127.0.0.1:${APP_PORT:-8080}:80`.
- Web (`3000`), API (`8000`), Runner, and Runtime Launcher have no published
  ports. Runner and Launcher are attached to private Compose networks; the
  Launcher has a dedicated external-egress network for proxy connectivity.
- PostgreSQL (`5432`) has no host-published port. The one-shot `migrate`
  service applies checksummed SQL migrations, Alembic revisions, and
  restricted-role grants before API startup.
- Redis (`6379`) has no host-published port and is reachable only on the private
  `data` network. AOF and a dedicated volume preserve the fan-out stream across
  container restarts; Redis is transport only, not canonical event history.
- Runner has no Docker socket, provider key, or workspace bind. It holds only
  API/Launcher credentials and its durable event outbox state.
- Runtime Launcher is the only service with Docker socket access and the
  workspace-root bind mount. It runs as the configured workspace UID/GID and
  receives the Docker socket's numeric group through `DOCKER_GID`; set this to
  `stat -c %g /var/run/docker.sock` on the host. It remains trusted host-control
  software because Docker socket access can affect the host. It accepts fixed
  authenticated operations and creates role-labeled per-Run resources only.
- Each worker runs non-root with a read-only root filesystem, dropped
  capabilities, no-new-privileges, CPU/memory/PID/time limits, and only its
  validated workspace mount. It has neither API/Runner credentials nor Docker
  socket. Its isolated per-Run network can reach the selected provider only
  through a constrained proxy.
- Nginx sends `/api/` (including `/api/v1/ws`) and `/health/` to the API, and
  everything else to the web app. WebSocket upgrade and browser `Origin`
  headers are forwarded unchanged.
- Nginx does not terminate TLS. HTTP is reachable only from host loopback. Use
  an SSH tunnel (`rtk ssh -L 8080:127.0.0.1:8080 <host>`) or VPN for remote
  access. Public exposure requires reviewed TLS termination, exact
  `PUBLIC_ORIGIN`, production mode, and secure session cookies.
- The browser never receives a shell/CLI or database route. Do not add port
  mappings for `db`, `api`, `web`, `runner`, or `runtime-launcher`.

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
   password twice without echo, and hashes it with Argon2id. It generates
   PostgreSQL/session secrets, `RUNNER_SHARED_TOKEN`,
   `RUNTIME_LAUNCHER_TOKEN`, and Runner UID/GID defaults. It writes atomically
   with mode `600` and prints no secret values.
   Optional non-secret overrides: `APP_PORT`, `PUBLIC_ORIGIN`,
   `OWNER_USERNAME`, `POSTGRES_DB`, `POSTGRES_USER`, `APP_DB_USER`,
   `RUNNER_UID`, and `RUNNER_GID`.

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

## Optional Codex Subscription gateway

The Codex path is separate from 9Router and keeps the existing LangChain tools.
It uses an isolated OMP OAuth broker profile and a private, pinned
single-attempt gateway; it does not mount the broker's credentials into workers.
The upstream Codex protocol is a community integration and may change. Account
eligibility, subscription terms, quotas, and real inference are not established
by a healthy gateway.

On the private Linux host, provision the `agent-office-codex` OMP profile with
the Owner's separate Codex login. Provision mode-600
`~/.local/share/agent-office/omp-gateway/client.token` and `clients.json` with
one scoped client, one `openai-codex` account and explicit
`openai-codex/<model>` grants; the client token must differ from the broker
bearer. The broker's token stays in its isolated profile. Never put credentials
in the repository, `.env`, images, logs, or browser.

With Bun, OMP, Docker Compose and user systemd available, build the images and
apply the additive migration as described above. When no Runs are active, run:

```bash
rtk python scripts/start-omp-gateway.py
```

The script installs the frozen gateway dependencies and user services, checks
authenticated single-attempt policy readiness, then recreates the idle Runner
and Launcher. The Launcher runs with the configured workspace UID/GID and
`DOCKER_GID`, the numeric group ID of the host Docker socket. It includes an
existing `docker-compose.override.yml` so the configured workspace bind mount
survives the restart. By default it refuses to overwrite differing installed
files or restart an active Run. For an intentional gateway code update, pass
`--update-managed-gateway-code`; this replaces only user-owned `gateway.ts`,
`policy.ts`, and `transport.ts` before restarting the private gateway. For the
Model Endpoint choose Codex Subscription with an explicit
`openai-codex/<model>`; the endpoint and credential reference are fixed to the
private gateway and `OMP_GATEWAY_KEY`.
Gateway readiness is not evidence of successful inference. Stock OMP gateway
retry behavior does not meet this integration's no-retry policy and must not
replace this gateway.

## Runner and isolated API integration tests

For local Runner authentication and the optional restricted-role PostgreSQL
integration test, first prepare the ignored `.env` and a disposable database:

```bash
rtk docker compose up -d db
rtk bash scripts/prepare-runner-test-env.sh
rtk docker compose up -d --build api runner
rtk docker compose --profile test run --rm --build api-postgres-test
```

The preparation script generates `RUNNER_SHARED_TOKEN` if it is missing and
sets `TEST_DATABASE_URL` to an administrator URL for the exact
`<POSTGRES_DB>_test` database. Each preparation drops and recreates only that
database so the legacy-migration integration test always starts fresh; do not
store user data there. It refuses other database names, updates `.env`
atomically with mode `600`, and prints neither secret values nor the database
URL. The test container connects only over Compose's private `data` network;
PostgreSQL remains unpublished. The PostgreSQL test may create temporary
database roles and records, and cleans up its own fixtures. Never point
`TEST_DATABASE_URL` at the application database.

### Isolated Event System fan-out benchmark

This benchmark uses only the disposable `<POSTGRES_DB>_test` database on the
Compose `db` service and Redis database 15 on the Compose `redis` service.
Preparing the test environment drops and recreates only the test database;
Redis stream key `agent-office:activity-events:v1` must not already exist in
database 15. The script enforces both backend targets before connecting.
Synthetic events are written only to that isolated Redis database. The script
starts a temporary API on container loopback and measures authenticated TCP
WebSocket fan-out from the Redis consumer. It does not exercise the PostgreSQL
outbox, a real Run, or the 3D office, and it is not a production-capacity claim.

```bash
rtk docker compose up -d db redis
rtk bash scripts/prepare-runner-test-env.sh
rtk docker compose --profile test run --rm api-postgres-test
rtk docker compose --profile test run --rm \
  -e REDIS_URL=redis://redis:6379/15 \
  -v "$PWD/apps/api/scripts/activity_ws_benchmark.py:/tmp/activity_ws_benchmark.py:ro" \
  api-postgres-test python /tmp/activity_ws_benchmark.py
```

The benchmark fails on event loss, a producer rate below 100 events/second,
or p95 delivery latency above 1 second. It deletes only the stream key it
created and refuses to overwrite a pre-existing key.

- API writes Activity Event, Run projection, and Redis fan-out outbox in one
  PostgreSQL transaction. A background publisher retries outbox delivery;
  Redis outage delays live fan-out but does not undo committed events or Run
  state. Reconnect replays persisted events from the PostgreSQL cursor.
  PostgreSQL Activity Event records are preserved during the pilot; Redis
  Streams uses an approximate 100,000-entry cap. The temporary target is 10
  WebSocket clients, 100 events/second, and p95 delivery latency ≤1 second;
  it is not a production capacity claim. Two TCP profile runs delivered all
  1,000 events/client at about 100 events/second; p95 was 599.32 ms and
  132.99 ms. Both exclude PostgreSQL outbox processing. A browser request made
  after API process restart returned HTTP 200 for authentication/snapshot, the
  WebSocket was accepted, and the rendered Office showed
  `Browser event feed: connected`.

## Configuration and secrets boundary

All secrets and URLs are supplied at runtime by `.env` through Compose. Nothing
is baked into the application images, and no default production credentials
exist:

- `APP_DB_USER`/`APP_DB_PASSWORD` provision a separate non-admin API role. The
  API uses a restricted `DATABASE_URL` and private `REDIS_URL`; the migration
  service applies project and versioned definition/skill, Agent Instance,
  Task/Run, owner-scoped Model Endpoint, legacy provider-configuration migration,
  artifact-metadata, scene-version, and Activity Event outbox schemas. Grants are
  scoped per table: mutable owner records get the required `SELECT`/`INSERT`/`UPDATE`;
  append-only versions, events, decisions, artifacts, and scene versions get
  `SELECT`/`INSERT`. The outbox gets `SELECT`/`INSERT`/`UPDATE` so the API can
  claim and acknowledge publication; the role cannot delete evidence or
  administer the database.
- API writes Activity Event, Run projection, and Redis fan-out outbox in one
  PostgreSQL transaction. A background publisher retries outbox delivery;
  Redis outage delays live fan-out but does not undo committed events or Run
  state. Reconnect replays persisted events from the PostgreSQL cursor.
  PostgreSQL Activity Event records are preserved during the pilot; Redis
  Streams uses an approximate 100,000-entry cap. The temporary load target is
  10 WebSocket clients, 100 events/second, and p95 delivery latency ≤1 second;
  it is not a production capacity claim. The Redis-outage recovery and isolated
  TCP fan-out profile passed; the profile excludes PostgreSQL outbox processing.
  The post-restart authenticated browser session, snapshot, and WebSocket
  connection were verified separately.
- `MIGRATION_DATABASE_URL` is the one-shot migration administrator connection.
- `SESSION_SECRET` is at least 32 random bytes; rotating it invalidates
  sessions.
- `OWNER_PASSWORD_HASH` is Argon2id; the plaintext owner password is never
  stored by this stack.
- `PUBLIC_ORIGIN` is the exact browser origin used for login and WebSocket
  Origin validation.
- `OWNER_USERNAME` identifies the single owner. Server-side owner/resource
  scope remains authoritative; the browser cannot assert an owner ID.
- `RUNNER_SHARED_TOKEN` authenticates internal API Runner calls.
  `RUNTIME_LAUNCHER_TOKEN` separately authenticates Runner-to-Launcher calls;
  keep both secret. `NINEROUTER_KEY` supplies the credential for the selected
  Model Endpoint. Its URL is stored on the endpoint record; a global
  `NINEROUTER_URL` is not required. Endpoint records store the URL and
  allowlisted environment-variable name, never the key value.
- `REDIS_URL` selects the private Redis Streams service; Compose defaults it to
  `redis://redis:6379/0`. Do not publish Redis to the host.
  Runner/Launcher/worker identities and workspace ownership are configured by
  `RUNNER_UID`/`RUNNER_GID`.
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

## Known Phase 2–6 limits

- Phase 2 now implements per-Run worker containers, bounded worker resources,
  an authenticated Docker Runtime Launcher, per-Run selected-endpoint proxy,
  cancellation verification contract, lease reconciliation, and owner-scoped
  Run Result persistence. Launcher readiness was exercised with the provider
  unset; it correctly reported `provider_ready: false`.
- An isolated disposable PostgreSQL 17 instance applied all legacy SQL
  migrations and Alembic through `runtime_0013`; `run_results` has SELECT and
  INSERT but no UPDATE grant for the application role. The temporary database
  was removed. This did not touch the Compose application database.
- API suite and Runner/Launcher tests pass; images build and Compose validates.
  Fixture-backed Chromium exercised Run cancellation-pending and persisted
  Run Result rendering. These do not establish live persistence or runtime
  execution.
- No actual API-created Run has yet passed through Runner → Launcher → worker
  → configured provider, nor has a real provider response populated the UI or
  3D scene. Two independent Runs, full restart/reconnect, and Docker-level
  egress-denial negatives remain open.
- Active Runs sharing one configured workspace path are serialized. There is
  no per-Run clone/worktree; concurrent operator edits are outside the
  serialization guarantee.
- Owner sessions are held in API-process memory; API restart invalidates them.
- Login rate limiting is per API process, not shared across replicas.
- There is no TLS termination, public ingress, automated off-host backup,
  user administration, or multi-tenant scope.
