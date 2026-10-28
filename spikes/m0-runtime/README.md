# Historical M0 utility probes

**Status:** These isolated diagnostics are not production code or current PRD acceptance evidence. The OMP/Codex-specific adapter/task probes and the separate M0 3D spike were removed on 2026-10-27 after the product scope changed. Historical work logs remain in `docs/progress.md`; they do not reproduce every detail from the removed feasibility report.

## Remaining probes

- `provider-completions-smoke.ts` checks a direct OpenAI-compatible Chat Completions endpoint. `--self-test` uses a loopback mock; `--provider` requires the explicit `M0_PROVIDER_ALLOW_REAL_REQUEST=I_ACKNOWLEDGE_PROVIDER_COST` opt-in and may incur charges. This is not the selected LangChain Runtime acceptance path.
- `sandbox-smoke.ts` checks a generic Bubblewrap filesystem/network boundary without a model or external network request.
- `sandbox-resource-smoke.ts` checks bounded per-process RLIMITs and cgroup-v2 process-count controls. It does not prove production Run resource limits.
- `sandbox-egress-smoke.ts` checks the Bubblewrap-to-local-mock egress bridge. It does not contact a real provider or prove production endpoint-only egress.

## Local checks

```sh
rtk bun run spikes/m0-runtime/provider-completions-smoke.ts --self-test
rtk bun run spikes/m0-runtime/sandbox-smoke.ts
rtk bun run spikes/m0-runtime/sandbox-resource-smoke.ts
rtk bun run spikes/m0-runtime/sandbox-egress-smoke.ts
```

The sandbox checks require Bubblewrap; the resource check also requires an available delegated cgroup-v2 `pids` controller. These probes remain feasibility diagnostics. Current Runtime acceptance is defined by the PRD and `docs/design-docs/2026-10-27-02-agent-runtime.md`.