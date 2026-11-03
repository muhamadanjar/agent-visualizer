from datetime import UTC, datetime, timedelta
from uuid import uuid4
import app.domain.orchestration as orchestration_module
import pytest
from referencing.exceptions import Unresolvable


from app.domain.orchestration import (
    DelegationSpec,
    OrchestrationInputError,
    WorkflowLimits,
    WorkflowPlan,
    approval_expiry,
    digest,
    parent_runtime_checkpoint,
    select_agent_instances,
    settle_nodes,
    validate_local_schema,
    validate_output,
    child_is_dispatchable,
    validate_parent_runtime_checkpoint,
)


def _node(node_id: str, dependencies: tuple[str, ...] = ()) -> DelegationSpec:
    return DelegationSpec(
        node_id=node_id, child_task_id=uuid4(), child_revision=1, definition_id=uuid4(),
        definition_version=1, instance_id=uuid4(), project_id=None, workspace_path="workspace",
        input_payload={"value": node_id}, resource_refs=(), dependencies=dependencies,
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        model_scope={"model": "m"}, tool_scope=(), limits={"max_child_runs": 8},
    )
def _plan(nodes: tuple[DelegationSpec, ...]) -> WorkflowPlan:
    return WorkflowPlan(
        uuid4(), digest({"nodes": sorted(node.canonical_digest() for node in nodes)}),
        1, nodes, WorkflowLimits(),
    )


def test_digest_is_canonical_and_approval_has_seven_day_ttl() -> None:
    assert digest({"b": 2, "a": 1}) == digest({"a": 1, "b": 2})
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert approval_expiry(now) == now + timedelta(days=7)


def test_dag_rejects_cycle_and_settles_failed_descendants() -> None:
    with pytest.raises(OrchestrationInputError, match="cycle"):
        _plan((_node("a", ("b",)), _node("b", ("a",))))
    plan = _plan((_node("a"), _node("b", ("a",)), _node("c")))
    assert settle_nodes(plan, {"a": "failed", "b": "planned", "c": "succeeded"})["b"] == "blocked"
def test_dag_enforces_depth_when_nodes_are_dependency_first() -> None:
    assert _plan((_node("a"), _node("b", ("a",)), _node("c", ("b",))))
    with pytest.raises(OrchestrationInputError, match="depth"):
        _plan((
            _node("a"),
            _node("b", ("a",)),
            _node("c", ("b",)),
            _node("d", ("c",)),
        ))



def test_schema_output_and_checkpoint_filter_secret_state() -> None:
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    validate_local_schema(schema)
    assert validate_output({"ok": True}, schema) == {"ok": True}
    assert parent_runtime_checkpoint({"status": "waiting", "prompt": "hidden"}) == {
        "status": "waiting"
    }
    with pytest.raises(OrchestrationInputError, match="schema_ref"):
        validate_local_schema({"type": "object", "$ref": "https://example.invalid/schema"})


def test_checkpoint_omits_persisted_child_outputs_regardless_of_text_or_aggregate_size() -> None:
    checkpoint = parent_runtime_checkpoint({
        "status": "succeeded",
        "node_states": {"prompt": "waiting_approval"},
        "outputs": {
            "first": {"summary": "Review the prompt and reasoning."},
            "second": {"text": "x" * 40_000},
            "third": {"text": "y" * 40_000},
        },
    })

    assert checkpoint == {"status": "succeeded"}

def test_checkpoint_validation_rejects_corrupt_dispatch_state() -> None:
    plan = _plan((_node("a"),))
    checkpoint = {
        "plan_digest": plan.plan_digest,
        "plan_revision": plan.revision,
        "status": "waiting_approval",
        "status_sequence": -1,
    }
    assert validate_parent_runtime_checkpoint(checkpoint, plan) == checkpoint

    for damaged in (
        {**checkpoint, "plan_digest": "0" * 64},
        {**checkpoint, "status_sequence": True},
        {**checkpoint, "unexpected": "untrusted"},
        {**checkpoint, "status": []},
        {**checkpoint, "last_emitted_status": []},
        {**checkpoint, "last_emitted_status": "waiting_approval"},
        {
            **checkpoint,
            "pending_status_event": {
                "status": "waiting_children",
                "sequence": 0,
                "occurred_at": "2026-11-03T00:00:00+00:00",
                "extra": "untrusted",
            },
        },
        {
            **checkpoint,
            "pending_status_event": {
                "status": [],
                "sequence": 0,
                "occurred_at": "2026-11-03T00:00:00+00:00",
            },
        },
        {
            **checkpoint,
            "pending_status_event": {
                "status": "waiting_children",
                "sequence": 0,
                "occurred_at": "2026-11-03T00:00:00+00:00",
            },
        },
    ):
        with pytest.raises(OrchestrationInputError, match="checkpoint"):
            validate_parent_runtime_checkpoint(damaged, plan)


def test_local_schema_references_must_be_acyclic_and_resolvable() -> None:
    schema = {
        "type": "object",
        "$defs": {"label": {"type": "string"}},
        "properties": {"label": {"$ref": "#/$defs/label"}},
    }
    validate_local_schema(schema)
    assert validate_output({"label": "verified"}, schema) == {"label": "verified"}

    for recursive_schema in (
        {"type": "object", "$ref": "#"},
        {
            "type": "object",
            "$defs": {
                "node": {
                    "type": "object",
                    "properties": {"next": {"$ref": "#/$defs/node"}},
                },
            },
        },
        {"type": "object", "properties": {"label": {"$ref": "#/$defs/missing"}}},
    ):
        with pytest.raises(OrchestrationInputError, match="schema_ref"):
            validate_local_schema(recursive_schema)


def test_schema_recursion_failure_becomes_invalid_output_error(monkeypatch) -> None:
    class RecursiveValidator:
        @staticmethod
        def check_schema(_schema: object) -> None:
            return None

        def __init__(self, _schema: object) -> None:
            return None

        def iter_errors(self, _output: object):
            raise RecursionError("schema recursion")

    monkeypatch.setattr(orchestration_module, "Draft202012Validator", RecursiveValidator)

    with pytest.raises(OrchestrationInputError, match="output_schema"):
        validate_output({}, {"type": "object"})


def test_percent_encoded_local_refs_use_uri_fragment_decoding() -> None:
    schema = {
        "type": "object",
        "$defs": {"a": {"type": "string"}},
        "properties": {"label": {"$ref": "#/$defs/%61"}},
    }
    validate_local_schema(schema)
    assert validate_output({"label": "resolved"}, schema) == {"label": "resolved"}

    escaped_defs_schema = {
        "type": "object",
        "$defs": {"a/b": {"type": "string"}},
        "properties": {"label": {"$ref": "#/%24defs/a~1b"}},
    }
    validate_local_schema(escaped_defs_schema)
    assert validate_output({"label": "resolved"}, escaped_defs_schema) == {"label": "resolved"}

    malformed_fragment = {
        "type": "object",
        "$defs": {"a": {"type": "string"}},
        "properties": {"label": {"$ref": "#/$defs/%FF"}},
    }
    with pytest.raises(OrchestrationInputError, match="schema_ref"):
        validate_local_schema(malformed_fragment)

    mismatched_schema = {
        "type": "object",
        "$defs": {"%61": {"type": "string"}},
        "properties": {"label": {"$ref": "#/$defs/%61"}},
    }
    with pytest.raises(OrchestrationInputError, match="schema_ref"):
        validate_local_schema(mismatched_schema)


def test_regex_schema_keywords_are_rejected_without_rejecting_property_names() -> None:
    validate_local_schema({
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
    })
    validate_local_schema({"type": "object", "enum": [{"pattern": "literal data"}]})
    for schema in (
        {"type": "object", "properties": {"value": {"type": "string", "pattern": "^(a+)+$"}}},
        {"type": "object", "patternProperties": {"^(a+)+$": {"type": "string"}}},
    ):
        with pytest.raises(OrchestrationInputError, match="schema_regex"):
            validate_local_schema(schema)



def test_unevaluated_schema_keywords_are_rejected_before_annotation_revalidation() -> None:
    definitions: dict[str, object] = {"n000": {"type": "object"}}
    for index in range(1, 31):
        definitions[f"n{index:03d}"] = {
            "allOf": [{"$ref": f"#/$defs/n{index - 1:03d}"}],
            "unevaluatedProperties": False,
        }
    composed_schema = {
        "type": "object",
        "$defs": definitions,
        "allOf": [{"$ref": "#/$defs/n030"}],
    }
    array_schema = {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "unevaluatedItems": False,
            },
        },
    }

    for schema in (composed_schema, array_schema):
        with pytest.raises(OrchestrationInputError, match="schema_annotation"):
            validate_local_schema(schema)

def test_local_refs_cannot_target_literal_const_data() -> None:
    schema = {
        "type": "object",
        "$defs": {
            "stash": {
                "const": {
                    "type": "string",
                    "pattern": "^(a+)+$",
                },
            },
        },
        "properties": {"value": {"$ref": "#/$defs/stash/const"}},
    }
    with pytest.raises(OrchestrationInputError, match="schema_ref"):
        validate_local_schema(schema)


def test_reference_resolution_failure_becomes_invalid_output_error(monkeypatch) -> None:
    class UnresolvableValidator:
        @staticmethod
        def check_schema(_schema: object) -> None:
            return None

        def __init__(self, _schema: object) -> None:
            return None

        def iter_errors(self, _output: object):
            raise Unresolvable("#/$defs/missing")

    monkeypatch.setattr(orchestration_module, "Draft202012Validator", UnresolvableValidator)
    with pytest.raises(OrchestrationInputError, match="output_schema"):
        validate_output({}, {"type": "object"})


def test_nested_schema_dialect_switch_is_rejected() -> None:
    schema = {
        "type": "object",
        "$defs": {
            "draft7": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "dependencies": {
                    "trigger": {
                        "properties": {"value": {"pattern": "^(a+)+$"}},
                    },
                },
            },
        },
        "properties": {"trigger": {"$ref": "#/$defs/draft7"}},
    }
    with pytest.raises(OrchestrationInputError, match="schema_dialect"):
        validate_local_schema(schema)


def test_memoized_local_refs_still_obey_maximum_chain_depth() -> None:
    definitions: dict[str, object] = {"n000": {"type": "object"}}
    for index in range(1, 40):
        definitions[f"n{index:03d}"] = {"$ref": f"#/$defs/n{index - 1:03d}"}
    schema = {
        "type": "object",
        "$defs": definitions,
        "properties": {"value": {"$ref": "#/$defs/n039"}},
    }
    with pytest.raises(OrchestrationInputError, match="schema_complexity"):
        validate_local_schema(schema)



def test_schema_and_output_validation_have_independent_complexity_bounds() -> None:
    wide_schema = {
        "type": "object",
        "properties": {
            f"field_{index}": {"type": "string"}
            for index in range(600)
        },
    }
    with pytest.raises(OrchestrationInputError, match="schema_complexity"):
        validate_local_schema(wide_schema)

    wide_output = {format(index, "x"): "" for index in range(4_100)}
    with pytest.raises(OrchestrationInputError, match="output_complexity"):
        validate_output(wide_output, {"type": "object"})

def test_dispatch_gate_requires_approval_dependencies_and_reconciliation() -> None:
    node = _node("b", ("a",))
    limits = WorkflowLimits()
    assert not child_is_dispatchable(node, {"b": "approved", "a": "succeeded"}, active_children=0, spent_child_runs=0, limits=limits, reconciled=False)
    assert child_is_dispatchable(node, {"b": "approved", "a": "succeeded"}, active_children=0, spent_child_runs=0, limits=limits, reconciled=True)
    assert not child_is_dispatchable(node, {"b": "approved", "a": "unknown"}, active_children=0, spent_child_runs=0, limits=limits, reconciled=True)


def test_workflow_limits_enforce_hard_caps_and_zero_disables_children() -> None:
    assert WorkflowLimits().child_cap == 8
    assert WorkflowLimits().duration_cap == 60
    assert WorkflowLimits(max_child_runs=0).child_cap == 0
    with pytest.raises(OrchestrationInputError, match="max_child_runs"):
        WorkflowLimits(max_child_runs=9).validate()
    with pytest.raises(OrchestrationInputError, match="max_duration_minutes"):
        WorkflowLimits(max_duration_minutes=61).validate()


def test_output_without_schema_is_artifact_only_and_plan_digest_survives_ordering() -> None:
    assert validate_output({"report": {"artifact_id": "artifact-1"}}, None) == {"report": {"artifact_id": "artifact-1"}}
    with pytest.raises(OrchestrationInputError, match="artifact_output"):
        validate_output({"report": "private text"}, None)
    first = _node("a")
    second = _node("b", ("a",))
    assert _plan((first, second)).plan_digest == _plan((second, first)).plan_digest


def test_unknown_child_is_terminal_failure_without_retry_permission() -> None:
    node = _node("a")
    limits = WorkflowLimits()
    assert not child_is_dispatchable(node, {"a": "unknown"}, active_children=0, spent_child_runs=0, limits=limits, reconciled=True)
    assert not child_is_dispatchable(node, {"a": "approved"}, active_children=0, spent_child_runs=limits.child_cap, limits=limits, reconciled=True)


def test_selection_requires_effective_tools_provider_and_project_scope() -> None:
    candidate = {"id": "a", "enabled": True, "archived": False, "role": "reviewer", "capabilities": ["review"], "effective_tools": ["read"], "provider_key": "p", "project_id": None, "queued_or_leased": 0, "workload_limit": 1}
    assert select_agent_instances([candidate], role="reviewer", capabilities={"review"}, required_tools={"read"}, provider_ready={"p"}, policy_tools={"read"}, project_id=uuid4()) == [candidate]
    assert select_agent_instances([candidate], role="reviewer", capabilities={"review"}, required_tools={"write"}, provider_ready={"p"}, policy_tools={"read"}, project_id=uuid4()) == []
