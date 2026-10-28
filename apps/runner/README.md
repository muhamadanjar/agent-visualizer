# Runner boundary

The Runner is the host-side supervisor for explicit, persisted Task Runs. It authenticates to the API using `RUNNER_SHARED_TOKEN`, heartbeats, claims queued dispatches, and submits lifecycle events through a durable local SQLite WAL outbox. The API acknowledges events only after durable persistence.

## Execution contract

- Builds a fresh LangChain `create_agent` in a dedicated worker container for each Run, from its immutable non-secret snapshot, using `ChatOpenAI` against the selected OpenAI-compatible Model Endpoint.
- The Runner process owns the lease, event outbox, and lifecycle monitor. It asks the separate Runtime Launcher to create/stop containers; it does not mount workspaces, receive the provider key, or access the Docker socket.
- The Runtime Launcher alone has Docker socket access. It constructs fixed, bounded container/network specs, mounts only the validated workspace into the worker, passes the selected provider key, and returns worker stdout frames to the Runner. It only manages resources with the expected per-Run labels.
- The worker runs non-root, with a read-only root filesystem, dropped capabilities, no-new-privileges, 1 CPU, 2 GiB memory, 128 PIDs, and a maximum 3,600-second deadline.
- Worker egress is routed through its own proxy on an isolated per-Run Docker network. The proxy permits only the selected endpoint, resolves and pins public DNS addresses, rejects private/metadata destinations, and permits `host.docker.internal:20128` only when the selected endpoint explicitly names that local NineRouter.
- The worker receives only `list_files`, `read_file`, and `write_file` after Definition/Project policy intersection. It never receives a shell or process-execution tool.
- Workspace paths are relative, reject traversal and symlinks, and cap file size. Provider responses, model/tool calls, output tokens, and request duration are bounded. HTTP redirects are rejected.
- The worker writes bounded lifecycle/action/result frames to its stdout stream. The Runner persists events through its outbox and writes `Run Result` before terminal success. Run Result is owner-scoped and capped at 1 MiB.
- Cancellation is a request, not terminal evidence. The Runner records `cancellation_pending`, asks the Launcher to stop the container, and emits `canceled` only when stop is verified; otherwise it records `unknown`.
- Active Runs sharing one configured workspace path remain serialized. The implementation does not create a per-Run clone/worktree; an operator editing the same workspace concurrently is outside the guarantee.
- Startup/reconciliation queries the Launcher for owned containers; ambiguous start outcomes become `unknown`, not a blind retry.

## Deployment

Compose runs Runner and Runtime Launcher privately without published ports. Runner receives the API/Launcher tokens and state, but not the provider key. Runtime Launcher receives `RUNTIME_LAUNCHER_TOKEN`, `NINEROUTER_KEY`, a Docker socket mount, and the workspace-root bind mount; it is trusted host-control software. It injects the provider key only into the per-Run worker container. The worker receives neither shared token nor Docker socket.

`scripts/bootstrap-env.sh` creates both shared tokens and UID/GID defaults without overwriting an existing `.env`. `RUNNER_UID`/`RUNNER_GID` identify the non-root worker and workspace ownership. Configure the selected Model Endpoint URL in the application and provide `NINEROUTER_KEY` to Compose; Compose passes the credential only to Runtime Launcher, which supplies it to the worker for provider-backed Runs.

## Remaining P0 limits

Unit/component tests, isolated PostgreSQL migration, image builds, Compose validation, Launcher health, and fixture-backed browser checks pass. No actual API-created Run has traversed Runner → Launcher → worker container → configured provider → persisted UI/3D. Live provider behavior, two concurrent independent Runs, restart/reconnect against a live deployment, endpoint-denial negative tests in Docker, and Phase 4 live 3D acceptance remain unverified. Do not treat simulated activity or an API-created Run record as successful operational work.
