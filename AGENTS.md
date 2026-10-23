# Repository Guide

## Product and Planning

- [`PRD.md`](PRD.md) is the draft product contract. Preserve its domain terms, FR/AC identifiers, and the distinction between confirmed decisions, proposals, and unverified capabilities.
- [`docs/phase-plan.md`](docs/phase-plan.md) defines implementation phases, dependencies, deliverables, and exit criteria. Respect its gates unless the user explicitly authorizes proceeding; if work proceeds past an open gate, retain that phase's unresolved status and criteria.
- [`docs/progress.md`](docs/progress.md) is the evidence register. Update it when phase status, decisions, blockers, deliverables, or verification results change. Mark phases complete only with recorded exit-criteria evidence.
- [`docs/phase-0-design.md`](docs/phase-0-design.md) is the design baseline. Keep architecture proposals and assumptions labeled; do not describe them as implemented behavior.
- [`docs/m0-feasibility.md`](docs/m0-feasibility.md) records M0 spike evidence and remaining feasibility gaps.

## Feature Planning Workflow

Before implementing a feature:

1. Identify its PRD requirements and acceptance criteria. If it is not covered, record the proposed scope and decision before treating it as committed product behavior.
2. Map the feature to the phase plan. Check dependencies, status, deliverables, and exit criteria; do not silently cross a phase gate. If the user explicitly authorizes proceeding, record the override and keep the unresolved gate open.
3. Preserve domain boundaries: Agent Definition, Agent Instance, Runtime Session, Task, Run / Attempt, delegation, approval, and visual presentation are distinct concepts.
4. Keep unknown runtime, security, performance, and deployment behavior explicit. Simulations and feasibility spikes are not evidence of operational MVP behavior.
5. After work, record changed deliverables and actual verification in `docs/progress.md`; update the phase plan only when scope, dependencies, or exit criteria change.

## Project Structure

- `spikes/m0-3d/` is an isolated Next.js/TypeScript feasibility spike for the office scene, not the Agent Office application. Its `AGENTS.md` contains framework-specific instructions.
- `spikes/m0-runtime/` contains isolated runtime feasibility probes. Neither spike implements the production application or proves the operational MVP.
- `docs/` contains the implementation roadmap, design baseline, feasibility evidence, and progress register.

## Development and Verification

Phase 2 application stack: follow [`deploy/README.md`](deploy/README.md) for environment setup and private Docker Compose startup. From the repository root, `rtk docker compose config --quiet` validates the Compose configuration; do not expose the API or database ports.

The web application lives in `apps/web/` and uses Next.js/TypeScript. Run Bun install/typecheck/build from that directory; Bun 1.3.14+ and Node.js 20.9+ are required. API tests live in `apps/api/tests/`; install `requirements-dev.txt` in an isolated Python environment and run `pytest` from `apps/api/`. No linter is configured.

`spikes/m0-3d/` is an isolated rendering feasibility spike, not the production app. Its README documents browser interactions and known limitations. For spike changes, exercise the affected browser path or build and report simulated scene behavior separately from real-runtime evidence. The spike has no configured test or lint script. For documentation changes, check links, requirement identifiers, terminology, and status consistency.

## Coding Conventions

- Keep Markdown concise and consistent with the PRD and phase documents.
- The spike uses TypeScript and React in the Next.js App Router. Follow `spikes/m0-3d/AGENTS.md` and consult installed Next.js documentation before changing framework APIs.
- No formatter or linter is configured.
- Prefix shell commands with `rtk`, following `/home/anjar/.codex/RTK.md`.

## Repository Tools

Graphify is installed. Its ignored `graphify-out/` directory contains a code-focused graph generated with AST-only extraction; it has no clustering, wiki, or report artifacts.

- For codebase questions, use `graphify query "<question>"`; use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts.
- Read source directly when the graph omits implementation detail. Use direct document reads and text search for Markdown, configuration, and other content outside the graph.
- After modifying code, run `graphify update .` to refresh the graph.

## Collaboration and Delivery

- Git history is unavailable in this checkout, so no commit convention is confirmed. Use concise, imperative commit subjects.
- Pull requests should state the problem, affected requirements or decisions, validation performed, and remaining uncertainties. Include screenshots or recordings for visual changes once a UI exists.
- Report unit, integration, browser, and real-runtime evidence separately. Simulated activity does not demonstrate live agent execution.
