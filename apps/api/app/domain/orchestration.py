"""Pure workflow orchestration rules.

This module deliberately knows nothing about LangGraph, Runner, or provider
credentials.  It is the small, deterministic policy core used by the API
controller and by persistence adapters.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote
from typing import Any, AsyncContextManager, Mapping, Protocol

from jsonschema import Draft202012Validator, SchemaError
from referencing.exceptions import Unresolvable

from app.domain.tasks import TaskSpec

MAX_CHILD_RUNS = 8
MAX_DURATION_MINUTES = 60
MAX_OUTPUT_BYTES = 64 * 1024
MAX_SCHEMA_DEPTH = 32
MAX_SCHEMA_NODES = 2048
MAX_OUTPUT_NODES = 8192
MAX_SCHEMA_REFERENCE_DEPTH = 32
MAX_SCHEMA_EVALUATION_STEPS = 1_000_000
DRAFT_2020_12_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
APPROVAL_TTL = timedelta(days=7)

PARENT_RUNTIME_CHECKPOINT_FIELDS = frozenset({
    "plan_digest", "plan_revision", "active_children", "spent_child_runs",
    "started_at", "paused_at", "paused_seconds", "status", "status_sequence",
    "last_emitted_status", "pending_status_event",
})


class OrchestrationInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WorkflowLeaseBinding:
    owner_id: str
    parent_run_id: UUID
    holder_id: UUID
    fencing_token: int


class WorkflowLeaseLost(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as error:
        raise OrchestrationInputError("json_value") from error


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _depth(value: Any, current: int = 0) -> int:
    if current > MAX_SCHEMA_DEPTH:
        raise OrchestrationInputError("depth")
    if isinstance(value, dict):
        return max([current, *(_depth(v, current + 1) for v in value.values())])
    if isinstance(value, list):
        return max([current, *(_depth(v, current + 1) for v in value)])
    return current


def _count_nodes(value: Any, limit: int, reason: str) -> int:
    pending = [value]
    count = 0
    while pending:
        current = pending.pop()
        count += 1
        if count > limit:
            raise OrchestrationInputError(reason)
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return count


def _resolve_local_ref(schema: Mapping[str, Any], reference: Any) -> tuple[str, Any]:
    if not isinstance(reference, str) or not reference.startswith("#"):
        raise OrchestrationInputError("schema_ref")
    try:
        fragment = unquote(reference[1:], encoding="utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise OrchestrationInputError("schema_ref") from error
    if not fragment.startswith("/$defs/"):
        raise OrchestrationInputError("schema_ref")
    tokens: list[str] = []
    for encoded in fragment[1:].split("/"):
        decoded = ""
        index = 0
        while index < len(encoded):
            if encoded[index] != "~":
                decoded += encoded[index]
                index += 1
                continue
            if index + 1 >= len(encoded) or encoded[index + 1] not in {"0", "1"}:
                raise OrchestrationInputError("schema_ref")
            decoded += "~" if encoded[index + 1] == "0" else "/"
            index += 2
        tokens.append(decoded)
    # Nested pointers can turn annotation data such as `const` into executable schemas.
    if len(tokens) != 2 or tokens[0] != "$defs":
        raise OrchestrationInputError("schema_ref")
    definitions = schema.get("$defs")
    if not isinstance(definitions, Mapping) or tokens[1] not in definitions:
        raise OrchestrationInputError("schema_ref")
    current: Any = schema
    for token in tokens:
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise OrchestrationInputError("schema_ref")
    return tokens[1], current


def _validated_schema_cost(schema: Mapping[str, Any]) -> int:
    if not isinstance(schema, Mapping):
        raise OrchestrationInputError("schema")
    encoded = canonical_json(schema).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise OrchestrationInputError("schema_size")
    _depth(schema)
    if schema.get("type") != "object":
        raise OrchestrationInputError("schema_root")
    if "$schema" in schema and schema["$schema"] != DRAFT_2020_12_SCHEMA:
        raise OrchestrationInputError("schema_dialect")
    _count_nodes(schema, MAX_SCHEMA_NODES, "schema_complexity")

    definitions = schema.get("$defs", {})
    if not isinstance(definitions, Mapping):
        raise OrchestrationInputError("schema")

    schema_keywords = {
        "additionalProperties", "contains", "else", "if", "items", "not",
        "propertyNames", "then",
    }
    schema_array_keywords = {"allOf", "anyOf", "oneOf", "prefixItems"}
    schema_map_keywords = {"$defs", "dependentSchemas", "properties"}

    def references(node: Any) -> list[str]:
        found: list[str] = []
        pending = [node]
        while pending:
            current = pending.pop()
            if not isinstance(current, Mapping):
                continue
            for key, value in current.items():
                if key == "$schema":
                    raise OrchestrationInputError("schema_dialect")
                if key in {"$id", "$dynamicRef", "$recursiveRef"}:
                    raise OrchestrationInputError("schema_ref")
                if key == "$ref":
                    target, _ = _resolve_local_ref(schema, value)
                    found.append(target)
                elif key in {"pattern", "patternProperties"}:
                    raise OrchestrationInputError("schema_regex")
                elif key in {"unevaluatedItems", "unevaluatedProperties"}:
                    raise OrchestrationInputError("schema_annotation")
                elif key in schema_keywords:
                    pending.append(value)
                elif key in schema_array_keywords and isinstance(value, list):
                    pending.extend(value)
                elif key in schema_map_keywords and isinstance(value, Mapping):
                    pending.extend(value.values())
        return found

    graph = {str(name): references(value) for name, value in definitions.items()}
    root_references = references({
        key: value for key, value in schema.items() if key not in {"$defs", "$schema"}
    })
    expanded: dict[str, tuple[int, int]] = {}

    def definition_cost(name: str, active: frozenset[str]) -> int:
        if name in active:
            raise OrchestrationInputError("schema_ref")
        if len(active) >= MAX_SCHEMA_REFERENCE_DEPTH:
            raise OrchestrationInputError("schema_complexity")
        if name in expanded:
            cost, reference_depth = expanded[name]
            if len(active) + reference_depth > MAX_SCHEMA_REFERENCE_DEPTH:
                raise OrchestrationInputError("schema_complexity")
            return cost
        cost = _count_nodes(definitions[name], MAX_SCHEMA_NODES, "schema_complexity")
        reference_depth = 1
        next_active = active | {name}
        for target in graph[name]:
            cost += definition_cost(target, next_active)
            reference_depth = max(reference_depth, 1 + expanded[target][1])
            if (
                cost > MAX_SCHEMA_EVALUATION_STEPS
                or len(active) + reference_depth > MAX_SCHEMA_REFERENCE_DEPTH
            ):
                raise OrchestrationInputError("schema_complexity")
        expanded[name] = (cost, reference_depth)
        return cost

    for name in graph:
        definition_cost(name, frozenset())
    cost = _count_nodes(schema, MAX_SCHEMA_NODES, "schema_complexity")
    for target in root_references:
        cost += definition_cost(target, frozenset())
        if cost > MAX_SCHEMA_EVALUATION_STEPS:
            raise OrchestrationInputError("schema_complexity")
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError as error:
        raise OrchestrationInputError("schema") from error
    return cost


def validate_local_schema(schema: Mapping[str, Any]) -> None:
    """Validate a bounded Draft 2020-12 schema with resolvable acyclic local refs."""
    _validated_schema_cost(schema)


def validate_output(output: Any, schema: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise OrchestrationInputError("output_root")
    if len(canonical_json(output).encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise OrchestrationInputError("output_size")
    _depth(output)
    output_nodes = _count_nodes(output, MAX_OUTPUT_NODES, "output_complexity")
    if schema is None:
        if not all(
            isinstance(item, dict)
            and set(item) <= {"artifact_id", "artifact_type", "display_name"}
            and isinstance(item.get("artifact_id"), str)
            for item in output.values()
        ):
            raise OrchestrationInputError("artifact_output")
        return output
    schema_cost = _validated_schema_cost(schema)
    if schema_cost * output_nodes > MAX_SCHEMA_EVALUATION_STEPS:
        raise OrchestrationInputError("output_complexity")
    validator = Draft202012Validator(dict(schema))
    try:
        if any(validator.iter_errors(output)):
            raise OrchestrationInputError("output_schema")
    except (RecursionError, Unresolvable) as error:
        raise OrchestrationInputError("output_schema") from error
    return output


@dataclass(frozen=True, slots=True)
class WorkflowLimits:
    max_child_runs: int | None = None
    max_duration_minutes: int | None = None
    max_active_children: int = 2
    max_depth: int = 3

    @property
    def child_cap(self) -> int:
        return MAX_CHILD_RUNS if self.max_child_runs is None else self.max_child_runs

    @property
    def duration_cap(self) -> int:
        return MAX_DURATION_MINUTES if self.max_duration_minutes is None else self.max_duration_minutes

    def validate(self) -> None:
        if self.max_child_runs is not None and not 0 <= self.max_child_runs <= MAX_CHILD_RUNS:
            raise OrchestrationInputError("max_child_runs")
        if self.max_duration_minutes is not None and not 1 <= self.max_duration_minutes <= MAX_DURATION_MINUTES:
            raise OrchestrationInputError("max_duration_minutes")
        if self.max_active_children != 2 or self.max_depth != 3:
            raise OrchestrationInputError("fixed_limits")


@dataclass(frozen=True, slots=True)
class DelegationSpec:
    node_id: str
    child_task_id: UUID
    child_revision: int
    definition_id: UUID
    definition_version: int
    instance_id: UUID
    project_id: UUID | None
    workspace_path: str
    input_payload: dict[str, Any]
    resource_refs: tuple[str, ...]
    dependencies: tuple[str, ...]
    output_schema: dict[str, Any] | None
    model_scope: dict[str, Any]
    tool_scope: tuple[str, ...]
    limits: dict[str, Any]

    def __post_init__(self) -> None:
        payload = self.input_payload
        if any(secret in canonical_json(payload).lower() for secret in ("api_key", "access_token", "authorization", "password")):
            raise OrchestrationInputError("secret")
        if self.output_schema is not None:
            validate_local_schema(self.output_schema)

    def canonical_digest(self) -> str:
        return digest({
            "node_id": self.node_id, "child_task_id": str(self.child_task_id), "child_revision": self.child_revision,
            "definition_id": str(self.definition_id), "definition_version": self.definition_version,
            "instance_id": str(self.instance_id), "project_id": str(self.project_id) if self.project_id else None,
            "workspace_path": self.workspace_path, "input_payload": self.input_payload,
            "resource_refs": self.resource_refs, "dependencies": self.dependencies,
            "output_schema": self.output_schema, "model_scope": self.model_scope,
            "tool_scope": self.tool_scope, "limits": self.limits,
        })


@dataclass(frozen=True, slots=True)
class WorkflowPlan:
    parent_run_id: UUID
    plan_digest: str
    revision: int
    nodes: tuple[DelegationSpec, ...]
    limits: WorkflowLimits
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.limits.validate()
        if not self.nodes or len(self.nodes) > self.limits.child_cap:
            raise OrchestrationInputError("child_runs")
        by_id = {node.node_id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise OrchestrationInputError("duplicate_node")
        if any(dep not in by_id for node in self.nodes for dep in node.dependencies):
            raise OrchestrationInputError("dependency")
        expected_digest = digest({"nodes": sorted(node.canonical_digest() for node in self.nodes)})
        if self.plan_digest != expected_digest:
            raise OrchestrationInputError("plan_digest")
        visiting: set[str] = set()
        depths: dict[str, int] = {}

        def depth(node_id: str) -> int:
            if node_id in visiting:
                raise OrchestrationInputError("cycle")
            if node_id in depths:
                return depths[node_id]
            visiting.add(node_id)
            node_depth = 1 + max(
                (depth(dep) for dep in by_id[node_id].dependencies),
                default=0,
            )
            visiting.remove(node_id)
            if node_depth > self.limits.max_depth:
                raise OrchestrationInputError("depth")
            depths[node_id] = node_depth
            return node_depth

        for node_id in by_id:
            depth(node_id)


class OrchestrationRepository(Protocol):
    def workflow_start_lock(self, owner_id: str) -> AsyncContextManager[None]: ...
    async def save_plan(self, owner_id: str, plan: WorkflowPlan) -> None: ...
    async def persist_plan_with_tasks(self, owner_id: str, plan: WorkflowPlan, task_specs: Mapping[UUID, TaskSpec]) -> None: ...
    async def bind_approval(self, owner_id: str, parent_run_id: UUID, node_id: str, approval_id: UUID, *, workflow_lease: WorkflowLeaseBinding | None = None) -> None: ...
    async def node_statuses(self, owner_id: str, parent_run_id: UUID) -> dict[str, str]: ...
    async def reconcile_child_runs(self, owner_id: str, parent_run_id: UUID, *, workflow_lease: WorkflowLeaseBinding) -> tuple[dict[str, str], int, int]: ...
    async def expire_delegations(self, owner_id: str, parent_run_id: UUID, *, workflow_lease: WorkflowLeaseBinding) -> int: ...
    async def set_node_status(self, owner_id: str, parent_run_id: UUID, node_id: str, status: str, *, expected_status: str, workflow_lease: WorkflowLeaseBinding) -> bool: ...
    async def get_plan(self, owner_id: str, parent_run_id: UUID) -> WorkflowPlan | None: ...
    async def active_parent_runs(self) -> list[tuple[str, UUID]]: ...
    async def delegation_states(self, owner_id: str, parent_run_id: UUID) -> list[Mapping[str, Any]]: ...
    async def save_checkpoint(self, owner_id: str, parent_run_id: UUID, state: Mapping[str, Any], *, terminal: bool = False, workflow_lease: WorkflowLeaseBinding) -> None: ...
    async def get_checkpoint(self, owner_id: str, parent_run_id: UUID) -> Any | None: ...
    async def acquire_lease(self, owner_id: str, parent_run_id: UUID, holder_id: UUID, *, seconds: int) -> int | None: ...
    async def renew_lease(self, owner_id: str, parent_run_id: UUID, holder_id: UUID, fencing_token: int, *, seconds: int) -> bool: ...
    async def release_lease(self, owner_id: str, parent_run_id: UUID, holder_id: UUID, fencing_token: int) -> bool: ...


def select_agent_instances(
    candidates: list[Mapping[str, Any]], *, role: str, capabilities: set[str],
    required_tools: set[str], provider_ready: set[str], policy_tools: set[str],
    project_id: UUID | None,
) -> list[Mapping[str, Any]]:
    """Apply FR-ORCH-002 filters without trusting prompt-provided privileges."""
    selected = []
    for candidate in candidates:
        if not candidate.get("enabled") or candidate.get("archived"):
            continue
        if candidate.get("role") != role or not capabilities.issubset(set(candidate.get("capabilities", ()) )):
            continue
        if not required_tools.issubset(set(candidate.get("effective_tools", ())) & policy_tools):
            continue
        if candidate.get("provider_key") not in provider_ready:
            continue
        if candidate.get("project_id") not in (None, project_id):
            continue
        if candidate.get("queued_or_leased", 0) >= candidate.get("workload_limit", 1):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: (item.get("queued_or_leased", 0), str(item.get("id", ""))))


def settle_nodes(plan: WorkflowPlan, statuses: Mapping[str, str]) -> dict[str, str]:
    """Settle independent branches and block only descendants of bad nodes."""
    result = dict(statuses)
    changed = True
    while changed:
        changed = False
        for node in plan.nodes:
            if result.get(node.node_id) in {"succeeded", "failed", "rejected", "expired", "invalid", "unknown", "blocked"}:
                continue
            dependency_states = [result.get(dep) for dep in node.dependencies]
            if any(state in {"failed", "rejected", "expired", "invalid", "unknown", "blocked"} for state in dependency_states):
                result[node.node_id] = "blocked"
                changed = True
    return result


def child_is_dispatchable(
    node: DelegationSpec, statuses: Mapping[str, str], *, active_children: int,
    spent_child_runs: int, limits: WorkflowLimits, reconciled: bool,
) -> bool:
    """Gate every child dispatch, including resume after restart or lease loss."""
    if not reconciled or active_children >= limits.max_active_children or spent_child_runs >= limits.child_cap:
        return False
    if statuses.get(node.node_id) != "approved":
        return False
    return all(statuses.get(dep) == "succeeded" for dep in node.dependencies)


def approval_expiry(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + APPROVAL_TTL


def parent_runtime_checkpoint(state: Mapping[str, Any]) -> dict[str, Any]:
    """Persist bounded controller metadata; outputs remain in Delegation rows."""
    result = {key: state[key] for key in PARENT_RUNTIME_CHECKPOINT_FIELDS if key in state}
    encoded = canonical_json(result)
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise OrchestrationInputError("checkpoint_size")
    return result

def validate_parent_runtime_checkpoint(value: Any, plan: WorkflowPlan) -> dict[str, Any]:
    """Reject damaged controller state rather than reconstructing dispatch metadata."""
    if not isinstance(value, Mapping) or not set(value).issubset(PARENT_RUNTIME_CHECKPOINT_FIELDS):
        raise OrchestrationInputError("checkpoint")
    state = dict(value)
    if (
        state.get("plan_digest") != plan.plan_digest
        or type(state.get("plan_revision")) is not int
        or state["plan_revision"] != plan.revision
        or not isinstance(state.get("status"), str)
        or state["status"] not in {
            "waiting_approval", "waiting_children", "succeeded", "failed", "canceled",
        }
        or type(state.get("status_sequence")) is not int
        or state["status_sequence"] < -1
    ):
        raise OrchestrationInputError("checkpoint")
    for key in ("active_children", "spent_child_runs"):
        if key in state and (type(state[key]) is not int or state[key] < 0):
            raise OrchestrationInputError("checkpoint")
    paused_seconds = state.get("paused_seconds", 0)
    if (
        not isinstance(paused_seconds, (int, float))
        or isinstance(paused_seconds, bool)
        or not math.isfinite(paused_seconds)
        or paused_seconds < 0
    ):
        raise OrchestrationInputError("checkpoint")
    statuses = {"waiting_approval", "waiting_children", "succeeded", "failed", "canceled"}
    for key in ("started_at", "paused_at"):
        timestamp = state.get(key)
        if timestamp is not None:
            if not isinstance(timestamp, str):
                raise OrchestrationInputError("checkpoint")
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError as error:
                raise OrchestrationInputError("checkpoint") from error
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise OrchestrationInputError("checkpoint")
    last_status = state.get("last_emitted_status")
    if last_status is not None and (
        not isinstance(last_status, str)
        or last_status not in statuses
        or state["status_sequence"] < 0
    ):
        raise OrchestrationInputError("checkpoint")
    pending = state.get("pending_status_event")
    if pending is not None and (
        not isinstance(pending, Mapping)
        or set(pending) != {"status", "sequence", "occurred_at"}
        or not isinstance(pending.get("status"), str)
        or pending.get("status") not in statuses
        or type(pending.get("sequence")) is not int
        or pending["sequence"] < 0
        or pending["sequence"] != state["status_sequence"]
        or not isinstance(pending.get("occurred_at"), str)
    ):
        raise OrchestrationInputError("checkpoint")
    if pending is not None:
        timestamp = pending["occurred_at"]
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise OrchestrationInputError("checkpoint") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise OrchestrationInputError("checkpoint")
    return state
