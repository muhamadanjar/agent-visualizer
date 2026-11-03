from __future__ import annotations

import json
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import app.application.workflow_controller as controller_module
import pytest
from app.application.workflow_controller import WorkflowCheckpointCorrupt, WorkflowController, WorkflowStateConflict
from app.domain.orchestration import (
    DelegationSpec,
    WorkflowLimits,
    WorkflowPlan,
    digest,
)


def test_workflow_active_limit_pauses_during_owner_approval(monkeypatch) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    class Clock(datetime):
        current = datetime(2026, 1, 1, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    class Repository:
        statuses = {"one": "waiting_approval"}

        async def expire_delegations(self, _owner_id: str, _run_id: object, **_kwargs: object):
            return 0
        async def reconcile_child_runs(self, _owner_id: str, _run_id: object, **_kwargs: object):
            active = int(any(value in {"queued", "running"} for value in self.statuses.values()))
            return dict(self.statuses), active, active
        async def delegation_states(self, _owner_id: str, _run_id: object):
            return []

    async def scenario() -> None:
        monkeypatch.setattr(controller_module, "datetime", Clock)
        repository = Repository()
        controller = WorkflowController(repository, None, None, None, None, None)  # type: ignore[arg-type]
        state = {
            "owner_id": "owner",
            "parent_run_id": uuid4(),
            "plan": SimpleNamespace(limits=WorkflowLimits(max_duration_minutes=60)),
            "statuses": {},
            "started_at": (now - timedelta(minutes=30)).isoformat(),
            "paused_at": None,
            "paused_seconds": 0.0,
        }

        await controller.reconcile(state)  # type: ignore[arg-type]
        paused_at = state["paused_at"]
        assert paused_at == now.isoformat()

        Clock.current = now + timedelta(hours=2)
        await controller.reconcile(state)  # type: ignore[arg-type]
        assert state["statuses"] == {"one": "waiting_approval"}
        assert state["paused_at"] == paused_at

        repository.statuses = {"one": "queued"}
        await controller.reconcile(state)  # type: ignore[arg-type]
        assert state["paused_at"] is None
        assert state["paused_seconds"] == 2 * 60 * 60
        assert state["statuses"] == {"one": "queued"}

        Clock.current += timedelta(minutes=31)
        await controller.reconcile(state)  # type: ignore[arg-type]
        assert state["statuses"] == {"one": "queued"}

    asyncio.run(scenario())


def test_duration_cap_keeps_live_child_open_until_cancellation_is_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    child_run_id = uuid4()
    parent_run_id = uuid4()
    node = DelegationSpec(
        node_id="child",
        child_task_id=uuid4(),
        child_revision=1,
        definition_id=uuid4(),
        definition_version=1,
        instance_id=uuid4(),
        project_id=None,
        workspace_path="workspace",
        input_payload={},
        resource_refs=(),
        dependencies=(),
        output_schema=None,
        model_scope={},
        tool_scope=(),
        limits={},
    )
    approval = DelegationSpec(
        node_id="approval",
        child_task_id=uuid4(),
        child_revision=1,
        definition_id=uuid4(),
        definition_version=1,
        instance_id=uuid4(),
        project_id=None,
        workspace_path="workspace",
        input_payload={},
        resource_refs=(),
        dependencies=(),
        output_schema=None,
        model_scope={},
        tool_scope=(),
        limits={},
    )
    nodes = (node, approval)
    plan = WorkflowPlan(
        parent_run_id,
        digest({"nodes": sorted(item.canonical_digest() for item in nodes)}),
        1,
        nodes,
        WorkflowLimits(max_duration_minutes=60),
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    class Repository:
        def __init__(self) -> None:
            self.statuses = {"child": "queued", "approval": "waiting_approval"}
            self.checkpoint_terminal: bool | None = None

        async def expire_delegations(self, *_args, **_kwargs):
            return 0

        async def reconcile_child_runs(self, *_args, **_kwargs):
            return dict(self.statuses), 1, 1

        async def delegation_states(self, *_args, **_kwargs):
            return [
                {
                    "node_id": "child",
                    "status": self.statuses["child"],
                    "child_run_id": child_run_id,
                },
                {
                    "node_id": "approval",
                    "status": self.statuses["approval"],
                    "child_run_id": None,
                },
            ]

        async def set_node_status(
            self,
            _owner_id,
            _parent_run_id,
            node_id,
            status,
            *,
            expected_status,
            **_kwargs,
        ):
            if self.statuses[node_id] != expected_status:
                return False
            self.statuses[node_id] = status
            return True

        async def save_checkpoint(self, *_args, terminal: bool = False, **_kwargs):
            self.checkpoint_terminal = terminal

    class Execution:
        def __init__(self) -> None:
            self.cancellation_requests: list[UUID] = []

        async def cancel_queued_run(self, *_args, **_kwargs):
            return False

        async def request_cancellation(self, _owner_id, run_id, **_kwargs):
            self.cancellation_requests.append(run_id)
            return "cancellation_pending"

    class Results:
        def __init__(self) -> None:
            self.created: list[str] = []

        async def create_for_run(self, _owner_id, _run_id, payload, **_kwargs):
            self.created.append(payload)

    async def scenario() -> None:
        monkeypatch.setattr(controller_module, "datetime", Clock)
        repository = Repository()
        execution = Execution()
        results = Results()
        controller = WorkflowController(
            repository, None, execution, None, None, results,  # type: ignore[arg-type]
        )
        emitted: list[str] = []

        async def emit(_state, status: str) -> None:
            emitted.append(status)

        controller._emit_status_transition = emit  # type: ignore[method-assign]
        state = {
            "owner_id": "owner",
            "parent_run_id": parent_run_id,
            "plan": plan,
            "statuses": {"child": "queued", "approval": "waiting_approval"},
            "started_at": (now - timedelta(hours=2)).isoformat(),
            "paused_at": None,
            "paused_seconds": 0.0,
        }

        await controller.reconcile(state)  # type: ignore[arg-type]
        await controller.settle(state)  # type: ignore[arg-type]

        assert execution.cancellation_requests == [child_run_id]
        assert state["statuses"] == {"child": "queued", "approval": "expired"}
        assert repository.statuses == {"child": "queued", "approval": "expired"}
        assert state["status"] == "waiting_children"
        assert repository.checkpoint_terminal is False
        assert results.created == []
        assert emitted == ["waiting_children"]

    asyncio.run(scenario())


def test_parent_status_events_use_monotonic_sequences_and_deduplicate() -> None:
    async def scenario() -> None:
        parent_id = uuid4()
        task_id = uuid4()
        instance_id = uuid4()
        project_id = uuid4()
        parent = SimpleNamespace(
            id=parent_id,
            task_id=task_id,
            task_revision=1,
            configuration_snapshot={
                "task": {"project_id": str(project_id)},
                "agent_instance": {"id": str(instance_id)},
            },
        )

        class Repository:
            def __init__(self) -> None:
                self.snapshots = []
                self.order = []

            async def save_checkpoint(self, _owner_id: str, _run_id: object, state, **_kwargs: object):
                self.snapshots.append(dict(state["pending_status_event"]))
                self.order.append("checkpoint")

        class Runs:
            async def get_for_owner(self, _owner_id: str, _run_id: object):
                return parent

        class Events:
            def __init__(self) -> None:
                self.rows = []

            async def append_event(self, event, **_kwargs: object):
                if not any(existing.event_id == event.event_id for existing in self.rows):
                    self.rows.append(event)
                repository.order.append("event")

            async def append_controller_terminal_event(self, event, **_kwargs: object):
                self.rows.append(event)

        repository = Repository()
        events = Events()
        controller = WorkflowController(repository, Runs(), None, None, events, None)  # type: ignore[arg-type]
        state = {
            "owner_id": "owner",
            "parent_run_id": parent_id,
            "status_sequence": -1,
            "last_emitted_status": None,
        }
        for status in ("waiting_approval", "waiting_approval", "waiting_children", "waiting_approval"):
            await controller._emit_status_transition(state, status)  # type: ignore[arg-type]

        assert [event.event_type for event in events.rows] == [
            "run.waiting_approval",
            "run.waiting_children",
            "run.waiting_approval",
        ]
        assert [event.source_sequence for event in events.rows] == [0, 1, 2]
        assert repository.order == ["checkpoint", "event"] * 3
        assert [item["sequence"] for item in repository.snapshots] == [0, 1, 2]
        pending = repository.snapshots[0]
        state["status_sequence"] = pending["sequence"]
        state["last_emitted_status"] = None
        state["pending_status_event"] = pending
        await controller._emit_status_transition(state, pending["status"])  # type: ignore[arg-type]
        assert len(events.rows) == 3
        assert events.rows[0].occurred_at == datetime.fromisoformat(pending["occurred_at"])

    asyncio.run(scenario())


def test_settlement_does_not_overwrite_approval_committed_after_reconciliation() -> None:
    async def scenario() -> None:
        parent_run_id = uuid4()
        upstream = DelegationSpec(
            node_id="upstream", child_task_id=uuid4(), child_revision=1,
            definition_id=uuid4(), definition_version=1, instance_id=uuid4(),
            project_id=None, workspace_path="workspace", input_payload={},
            resource_refs=(), dependencies=(), output_schema=None,
            model_scope={}, tool_scope=(), limits={},
        )
        downstream = DelegationSpec(
            node_id="downstream", child_task_id=uuid4(), child_revision=1,
            definition_id=uuid4(), definition_version=1, instance_id=uuid4(),
            project_id=None, workspace_path="workspace", input_payload={},
            resource_refs=(), dependencies=("upstream",), output_schema=None,
            model_scope={}, tool_scope=(), limits={},
        )
        nodes = (upstream, downstream)
        plan = WorkflowPlan(
            parent_run_id,
            digest({"nodes": sorted(node.canonical_digest() for node in nodes)}),
            1, nodes, WorkflowLimits(),
        )

        class Repository:
            durable_statuses = {"upstream": "failed", "downstream": "approved"}
            checkpoint_saved = False

            async def set_node_status(
                self, _owner_id, _run_id, node_id, status, *,
                expected_status, workflow_lease,
            ):
                if self.durable_statuses[node_id] != expected_status:
                    return False
                self.durable_statuses[node_id] = status
                return True

            async def save_checkpoint(self, *_args, **_kwargs):
                self.checkpoint_saved = True

        repository = Repository()
        controller = WorkflowController(repository, None, None, None, None, None)  # type: ignore[arg-type]
        state = {
            "owner_id": "owner",
            "parent_run_id": parent_run_id,
            "plan": plan,
            "statuses": {"upstream": "failed", "downstream": "waiting_approval"},
        }

        with pytest.raises(RuntimeError, match="status changed"):
            await controller.settle(state)  # type: ignore[arg-type]

        assert repository.durable_statuses["downstream"] == "approved"
        assert not repository.checkpoint_saved

    asyncio.run(scenario())


def test_resume_reloads_durable_status_once_after_compare_and_set_conflict(monkeypatch) -> None:
    async def scenario() -> None:
        parent_run_id = uuid4()
        plan = SimpleNamespace()

        class Repository:
            status_reads = 0

            async def get_plan(self, _owner_id: str, _run_id: object):
                return plan

            async def node_statuses(self, _owner_id: str, _run_id: object):
                self.status_reads += 1
                return {"node": "waiting_approval" if self.status_reads == 1 else "approved"}

        repository = Repository()
        controller = WorkflowController(repository, None, None, None, None, None)  # type: ignore[arg-type]
        observed_statuses = []

        async def run_once(_owner_id, _run_id, _plan, statuses):
            observed_statuses.append(dict(statuses))
            if len(observed_statuses) == 1:
                raise WorkflowStateConflict("workflow status changed during settlement")
            return {"statuses": statuses}

        monkeypatch.setattr(controller, "run_once", run_once)
        result = await controller.resume("owner", parent_run_id)

        assert observed_statuses == [
            {"node": "waiting_approval"},
            {"node": "approved"},
        ]
        assert result == {"statuses": {"node": "approved"}}

    asyncio.run(scenario())

def test_resume_blocks_impossible_emitted_status_checkpoint_before_graph(monkeypatch) -> None:
    async def scenario() -> None:
        parent_run_id = uuid4()
        checkpoint = {
            "plan_digest": "plan-digest",
            "plan_revision": 1,
            "status": "waiting_approval",
            "status_sequence": -1,
            "last_emitted_status": "waiting_approval",
        }

        class Repository:
            async def get_plan(self, _owner_id: str, _run_id: object):
                return SimpleNamespace(plan_digest="plan-digest", revision=1)

            async def node_statuses(self, _owner_id: str, _run_id: object):
                return {"node": "approved"}

            async def get_checkpoint(self, _owner_id: str, _run_id: object):
                return checkpoint

            async def acquire_lease(self, *_args, **_kwargs):
                return 1

            async def renew_lease(self, *_args, **_kwargs):
                return True

            async def release_lease(self, *_args, **_kwargs):
                return None

        controller = WorkflowController(Repository(), None, None, None, None, None)  # type: ignore[arg-type]

        def fail_if_graph_runs():
            pytest.fail("corrupt checkpoint must be rejected before graph construction")

        async def fail_if_approvals_run(*_args, **_kwargs):
            pytest.fail("corrupt checkpoint must be rejected before approval creation")

        monkeypatch.setattr(controller, "graph", fail_if_graph_runs)
        monkeypatch.setattr(controller, "_ensure_pending_approvals", fail_if_approvals_run)
        assert await controller.resume("owner", parent_run_id) == {"status": "blocked"}

    asyncio.run(scenario())


def test_resume_accepts_purged_checkpoint_for_terminal_workflow() -> None:
    async def scenario() -> None:
        parent_run_id = uuid4()

        class Repository:
            async def get_plan(self, _owner_id: str, _run_id: object):
                return SimpleNamespace()

            async def node_statuses(self, _owner_id: str, _run_id: object):
                return {"node": "succeeded"}

            async def get_checkpoint(self, _owner_id: str, _run_id: object):
                return None

        controller = WorkflowController(Repository(), None, None, None, None, None)  # type: ignore[arg-type]
        assert await controller.resume("owner", parent_run_id) == {"status": "succeeded"}

    asyncio.run(scenario())


def test_dispatch_hands_validated_dependency_outputs_to_child_run() -> None:
    async def scenario() -> None:
        parent_run_id = uuid4()
        upstream = DelegationSpec(
            node_id="upstream",
            child_task_id=uuid4(),
            child_revision=1,
            definition_id=uuid4(),
            definition_version=1,
            instance_id=uuid4(),
            project_id=uuid4(),
            workspace_path="workspace",
            input_payload={},
            resource_refs=(),
            dependencies=(),
            output_schema=None,
            model_scope={},
            tool_scope=(),
            limits={},
        )
        downstream = DelegationSpec(
            node_id="downstream",
            child_task_id=uuid4(),
            child_revision=1,
            definition_id=uuid4(),
            definition_version=1,
            instance_id=uuid4(),
            project_id=uuid4(),
            workspace_path="workspace",
            input_payload={},
            resource_refs=(),
            dependencies=("upstream",),
            output_schema=None,
            model_scope={},
            tool_scope=(),
            limits={},
        )
        limits = WorkflowLimits()
        plan = WorkflowPlan(
            parent_run_id=parent_run_id,
            plan_digest=digest({
                "nodes": sorted((upstream.canonical_digest(), downstream.canonical_digest()))
            }),
            revision=1,
            nodes=(upstream, downstream),
            limits=limits,
        )

        class Repository:
            async def delegation_states(self, _owner_id: str, _parent_id: object):
                return [
                    {"node_id": "upstream", "status": "succeeded", "output": {"decision": "approved"}},
                    {"node_id": "downstream", "status": "approved", "output": None},
                ]


        class Runs:
            dependency_outputs = None

            async def create_for_task(self, *_args: object, **kwargs: object):
                self.dependency_outputs = kwargs.get("dependency_outputs")
                return SimpleNamespace(id=uuid4()), True

        class Execution:
            async def get_policy_for_project(self, _owner_id: str, _project_id: object):
                return SimpleNamespace(
                    enabled=True, workspace_path="workspace", allowed_tools=[], revision=1,
                )

        runs = Runs()
        controller = WorkflowController(
            Repository(), runs, Execution(), None, None, None,  # type: ignore[arg-type]
        )
        state = {
            "owner_id": "owner",
            "parent_run_id": parent_run_id,
            "plan": plan,
            "statuses": {"upstream": "succeeded", "downstream": "approved"},
            "active_children": 0,
            "spent_child_runs": 0,
            "reconciled": True,
        }
        await controller.dispatch(state)  # type: ignore[arg-type]

        assert runs.dependency_outputs == {"upstream": {"decision": "approved"}}
        assert state["statuses"]["downstream"] == "queued"

    asyncio.run(scenario())


def test_terminal_parent_result_aggregates_child_outputs() -> None:
    async def scenario() -> None:
        parent_run_id = uuid4()
        node = DelegationSpec(
            node_id="final",
            child_task_id=uuid4(),
            child_revision=1,
            definition_id=uuid4(),
            definition_version=1,
            instance_id=uuid4(),
            project_id=uuid4(),
            workspace_path="workspace",
            input_payload={},
            resource_refs=(),
            dependencies=(),
            output_schema={"type": "object"},
            model_scope={},
            tool_scope=(),
            limits={},
        )
        second_node = replace(node, node_id="audit", child_task_id=uuid4())
        nodes = (node, second_node)
        plan = WorkflowPlan(
            parent_run_id=parent_run_id,
            plan_digest=digest({"nodes": sorted(item.canonical_digest() for item in nodes)}),
            revision=1,
            nodes=nodes,
            limits=WorkflowLimits(),
        )

        class Repository:

            async def set_node_status(self, *_args: object, **_kwargs: object):
                return True

            async def delegation_states(self, *_args: object):
                return [
                    {"node_id": "final", "status": "succeeded", "output": {"summary": "Review the prompt and reasoning.", "text": "x" * 40_000}},
                    {"node_id": "audit", "status": "succeeded", "output": {"summary": "Accepted", "text": "y" * 40_000}},
                ]

            checkpoint_calls = []

            async def save_checkpoint(self, *_args: object, **kwargs: object):
                state = _args[2] if len(_args) > 2 else None
                snapshot = dict(state) if isinstance(state, dict) else None
                if snapshot is not None and isinstance(snapshot.get("pending_status_event"), dict):
                    snapshot["pending_status_event"] = dict(snapshot["pending_status_event"])
                self.checkpoint_calls.append((kwargs, snapshot))

        class Results:
            payload = None

            async def create_for_run(self, _owner_id: str, _run_id: object, payload: str, **_kwargs: object):
                self.payload = payload
                return SimpleNamespace(created_at=datetime.now(UTC)), True

        project_id = uuid4()
        instance_id = uuid4()
        parent = SimpleNamespace(
            id=parent_run_id, task_id=uuid4(), task_revision=1,
            configuration_snapshot={
                "task": {"project_id": str(project_id)},
                "agent_instance": {"id": str(instance_id)},
            },
        )

        class Runs:
            async def get_for_owner(self, _owner_id: str, _run_id: object):
                return parent

        class Events:
            def __init__(self) -> None:
                self.rows = []

            async def append_event(self, event, **_kwargs: object):
                self.rows.append(event)

            async def append_controller_terminal_event(self, event, **_kwargs: object):
                self.rows.append(event)

        repository = Repository()
        results = Results()
        events = Events()
        controller = WorkflowController(
            repository, Runs(), None, None, events, results,  # type: ignore[arg-type]
        )
        state = {
            "owner_id": "owner",
            "parent_run_id": parent_run_id,
            "plan": plan,
            "statuses": {"final": "succeeded", "audit": "succeeded"},
            "status_sequence": -1,
        }
        await controller.settle(state)  # type: ignore[arg-type]

        assert results.payload is not None
        payload = json.loads(results.payload)
        assert payload["status"] == "succeeded"
        assert payload["outputs"] == {
            "final": {"summary": "Review the prompt and reasoning.", "text": "x" * 40_000},
            "audit": {"summary": "Accepted", "text": "y" * 40_000},
        }
        assert [event.event_type for event in events.rows] == ["run.succeeded"]
        assert len(repository.checkpoint_calls) == 2
        pending_checkpoint, terminal_checkpoint = repository.checkpoint_calls
        assert pending_checkpoint[1]["pending_status_event"]["status"] == "succeeded"
        assert pending_checkpoint[1]["pending_status_event"]["sequence"] == 0
        assert "outputs" not in pending_checkpoint[1]
        assert terminal_checkpoint[0] == {"terminal": True, "workflow_lease": None}

    asyncio.run(scenario())


def test_blocked_dispatch_states_are_durable_before_terminal_parent_settlement() -> None:
    async def scenario() -> None:
        parent_run_id = uuid4()
        first_project_id = uuid4()
        second_project_id = uuid4()
        first = DelegationSpec(
            node_id="policy_changed",
            child_task_id=uuid4(),
            child_revision=1,
            definition_id=uuid4(),
            definition_version=1,
            instance_id=uuid4(),
            project_id=first_project_id,
            workspace_path="workspace",
            input_payload={},
            resource_refs=(),
            dependencies=(),
            output_schema=None,
            model_scope={},
            tool_scope=(),
            limits={},
        )
        second = replace(
            first,
            node_id="snapshot_changed",
            child_task_id=uuid4(),
            project_id=second_project_id,
        )
        nodes = (first, second)
        plan = WorkflowPlan(
            parent_run_id,
            digest({"nodes": sorted(node.canonical_digest() for node in nodes)}),
            1,
            nodes,
            WorkflowLimits(),
        )

        class Repository:
            statuses = {node.node_id: "approved" for node in nodes}
            terminal_checkpoint = False

            async def set_node_status(
                self,
                _owner_id,
                _parent_run_id,
                node_id,
                status,
                *,
                expected_status,
                workflow_lease,
            ):
                if self.statuses[node_id] != expected_status:
                    return False
                self.statuses[node_id] = status
                return True

            async def delegation_states(self, *_args, **_kwargs):
                return [
                    {"node_id": node_id, "status": status, "output": None}
                    for node_id, status in self.statuses.items()
                ]

            async def save_checkpoint(self, *_args, terminal=False, **_kwargs):
                self.terminal_checkpoint = terminal

        class Execution:
            async def get_policy_for_project(self, _owner_id, project_id):
                if project_id == first_project_id:
                    return None
                return SimpleNamespace(
                    enabled=True,
                    workspace_path="workspace",
                    allowed_tools=[],
                    revision=1,
                )

        class Runs:
            async def create_for_task(self, _owner_id, task_id, *_args, **_kwargs):
                if task_id == second.child_task_id:
                    raise controller_module.RunInputError("task_revision")

        class Results:
            payload = None

            async def create_for_run(self, _owner_id, _run_id, payload, **_kwargs):
                self.payload = payload

        repository = Repository()
        results = Results()
        controller = WorkflowController(
            repository, Runs(), Execution(), None, None, results,  # type: ignore[arg-type]
        )

        async def emit(_state, _status):
            return None

        controller._emit_status_transition = emit  # type: ignore[method-assign]
        state = {
            "owner_id": "owner",
            "parent_run_id": parent_run_id,
            "plan": plan,
            "statuses": dict(repository.statuses),
            "active_children": 0,
            "spent_child_runs": 0,
            "reconciled": True,
        }

        await controller.dispatch(state)  # type: ignore[arg-type]
        await controller.settle(state)  # type: ignore[arg-type]

        assert repository.statuses == {
            "policy_changed": "blocked",
            "snapshot_changed": "blocked",
        }
        assert state["statuses"] == repository.statuses
        assert state["status"] == "failed"
        assert repository.terminal_checkpoint is True
        assert json.loads(results.payload)["node_states"] == repository.statuses

    asyncio.run(scenario())


def test_controller_loop_recovers_from_transient_storage_failures(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        parent_run_id = uuid4()
        ticks = 0

        async def immediate_tick(awaitable, *, timeout):
            nonlocal ticks
            awaitable.close()
            ticks += 1
            if ticks <= 3:
                raise asyncio.TimeoutError
            stop.set()

        monkeypatch.setattr(controller_module.asyncio, "wait_for", immediate_tick)

        class Approvals:
            calls = 0

            async def expire_due(self, _now):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("temporary approval database failure")

        class Repository:
            calls = 0

            async def active_parent_runs(self):
                self.calls += 1
                if self.calls == 2:
                    raise OSError("temporary parent-list database failure")
                return [("owner", parent_run_id)]

        class Controller:
            approvals = Approvals()
            repository = Repository()
            resume_calls = 0

            async def resume(self, _owner_id, _parent_run_id):
                self.resume_calls += 1
                if self.resume_calls == 1:
                    raise OSError("temporary parent database failure")

        controller = Controller()
        await controller_module.controller_loop(controller, stop)  # type: ignore[arg-type]

        assert ticks == 4
        assert controller.approvals.calls == 3
        assert controller.repository.calls == 3
        assert controller.resume_calls == 2
        messages = [record.getMessage() for record in caplog.records]
        assert any("approval expiry failed" in message for message in messages)
        assert any("active parent listing failed" in message for message in messages)
        assert any("parent reconciliation failed" in message for message in messages)

    asyncio.run(scenario())


def test_controller_loop_propagates_shutdown_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()

        async def cancel_tick(awaitable, *, timeout):
            awaitable.close()
            raise asyncio.CancelledError

        monkeypatch.setattr(controller_module.asyncio, "wait_for", cancel_tick)
        controller = SimpleNamespace(approvals=None, repository=None)
        with pytest.raises(asyncio.CancelledError):
            await controller_module.controller_loop(controller, stop)  # type: ignore[arg-type]

    asyncio.run(scenario())
