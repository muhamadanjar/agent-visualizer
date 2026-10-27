# Runner boundary

The Compose Runner is the isolated execution worker for explicit, persisted Task Runs. It authenticates to the API using `RUNNER_SHARED_TOKEN`, heartbeats, claims queued dispatches, and submits lifecycle events through a durable local SQLite WAL outbox. Events remain queued until the API acknowledges their event ID and cursor.

## Execution contract

- Uses the non-secret Run snapshot plus the current matching project execution-policy revision.
- Supports OpenAI-compatible non-streaming Chat Completions and only the `list_files`, `read_file`, and `write_file` workspace tools. It never exposes a shell or process-execution tool.
- Enforces workspace-relative paths, rejects traversal/symlinks/sensitive file paths, and caps file/provider payload size, inference turns, tool calls, elapsed Run duration, and each provider request with an absolute wall-clock deadline.
- The absolute provider deadline uses the Runner's POSIX main thread; provider calls from worker threads are rejected rather than run without a hard deadline.
- Runner events carry a tool identifier; the API derives action summaries from a fixed mapping, never from Runner-supplied prose or model output.
- Serializes active Runs sharing one configured workspace path. Different workspace paths may be claimed concurrently. This prevents concurrent writes to a shared checkout; it does not create a per-Run clone or git worktree.
- An expired lease becomes `unknown`; the Runner does not automatically retry uncertain side effects.
- Deterministic setup failures emit a terminal `run.failed` event before execution starts. New event acknowledgements require an active lease; rejected stale events stop the current job.

## Deployment

Compose runs the Runner without published ports, as a non-root user, with a read-only container filesystem and only the app network. The writable mounts are `./workspaces` and `./.runner-state`; set `RUNNER_UID`/`RUNNER_GID` to the host directory owner. `scripts/bootstrap-env.sh` creates the shared Runner token and UID/GID defaults without overwriting an existing `.env`. Configure the intended `NINEROUTER_URL` and `NINEROUTER_KEY` before expecting provider-backed jobs to execute.

The Runner validates the configured endpoint and refuses redirects, but application-level URL checks are not an OS/container egress firewall. Do not expose the Runner to untrusted networks. Local HTTP-mock tool-loop and outbox restart tests pass; a live configured-provider task and full Runner/API/PostgreSQL/3D flow have not been demonstrated.

## Remaining P0 limits

Cancellation and cancel acknowledgement, final-answer/artifact persistence, independent two-Run 3D acceptance, full service restart/reconnect reconciliation, and OS-enforced provider-only egress remain unverified or unimplemented. Do not treat a claimed Run, model response, or simulated activity as successful operational work.
