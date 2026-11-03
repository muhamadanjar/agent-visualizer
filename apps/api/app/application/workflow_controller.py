"""Private LangGraph controller for durable parent-run orchestration."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.graph import END, StateGraph

from app.domain.evidence import ActivityEventDraft, ActivityEventRepository, ApprovalRepository, ApprovalRequestDraft
from app.domain.execution import ExecutionRepository
from app.domain.orchestration import MAX_OUTPUT_BYTES, DelegationSpec, OrchestrationInputError, OrchestrationRepository, WorkflowLeaseBinding, WorkflowPlan, approval_expiry, canonical_json, child_is_dispatchable, settle_nodes, validate_parent_runtime_checkpoint
from app.domain.run_results import RunResultRepository
from app.domain.runs import RunInputError, RunRepository, WorkflowDelegationBinding


logger = logging.getLogger(__name__)

def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


class ControllerState(TypedDict, total=False):
    owner_id: str
    parent_run_id: UUID
    plan: WorkflowPlan
    statuses: dict[str, str]
    active_children: int
    spent_child_runs: int
    reconciled: bool
    status: str
    started_at: str | None
    paused_at: str | None
    paused_seconds: float
    status_sequence: int
    last_emitted_status: str | None
    pending_status_event: dict[str, Any]
    lease_holder_id: UUID
    lease_fencing_token: int
    lease_lost: asyncio.Event



class WorkflowStateConflict(RuntimeError):
    """A durable node status changed after this controller reconciled it."""


class WorkflowCheckpointCorrupt(RuntimeError):
    """Persisted state is unsafe to resume without manual reconciliation."""

class WorkflowController:
    """The only process allowed to advance a workflow parent run."""

    def __init__(self, repository: OrchestrationRepository, runs: RunRepository, execution: ExecutionRepository, approvals: ApprovalRepository, events: ActivityEventRepository, results: RunResultRepository) -> None:
        self.repository = repository
        self.runs = runs
        self.execution = execution
        self.approvals = approvals
        self.events = events
        self.results = results
        self._owner_locks: dict[str, asyncio.Lock] = {}

    async def _ensure_pending_approvals(
        self, owner_id: str, parent_run_id: UUID, plan: WorkflowPlan, *,
        workflow_lease: WorkflowLeaseBinding | None = None,
    ) -> None:
        states = await self.repository.delegation_states(owner_id, parent_run_id)
        state_by_node = {str(row["node_id"]): row for row in states}
        for node in plan.nodes:
            row = state_by_node.get(node.node_id)
            if row is None or row.get("approval_id") is not None or row.get("status") != "waiting_approval":
                continue
            approval, _ = await self.approvals.create_request(ApprovalRequestDraft(
                owner_id=owner_id,
                run_id=parent_run_id,
                request_id=uuid5(NAMESPACE_URL, f"agent-office:workflow-approval:{parent_run_id}:{node.node_id}"),
                action_type="workflow.delegation",
                action_digest=node.canonical_digest(),
                resource_ref=f"workflow:{parent_run_id}:{node.node_id}",
                expires_at=approval_expiry(),
            ))
            await self.repository.bind_approval(
                owner_id, parent_run_id, node.node_id, approval.id,
                workflow_lease=workflow_lease,
            )

    async def _dependency_outputs(
        self, owner_id: str, parent_run_id: UUID, node: DelegationSpec,
    ) -> dict[str, Any]:
        if not node.dependencies:
            return {}
        states = await self.repository.delegation_states(owner_id, parent_run_id)
        state_by_node = {str(row["node_id"]): row for row in states}
        outputs: dict[str, Any] = {}
        for dependency_id in node.dependencies:
            dependency = state_by_node.get(dependency_id)
            output = None if dependency is None else dependency.get("output")
            if dependency is None or dependency.get("status") != "succeeded" or not isinstance(output, dict):
                raise OrchestrationInputError("dependency_output")
            outputs[dependency_id] = output
        if len(canonical_json(outputs).encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise OrchestrationInputError("dependency_output_size")
        return outputs

    def graph(self):
        graph = StateGraph(ControllerState)
        graph.add_node("reconcile", self.reconcile)
        graph.add_node("dispatch", self.dispatch)
        graph.add_node("settle", self.settle)
        graph.set_entry_point("reconcile")
        graph.add_edge("reconcile", "dispatch")
        graph.add_edge("dispatch", "settle")
        graph.add_edge("settle", END)
        return graph.compile()

    async def _assert_lease(self, state: ControllerState) -> None:
        lost = state.get("lease_lost")
        if lost is not None and lost.is_set():
            raise RuntimeError("workflow lease lost")
        holder_id = state.get("lease_holder_id")
        fencing_token = state.get("lease_fencing_token")
        if holder_id is None or fencing_token is None:
            return
        if not await self.repository.renew_lease(
            state["owner_id"], state["parent_run_id"], holder_id, fencing_token, seconds=30,
        ):
            raise RuntimeError("workflow lease lost")

    def _lease_binding(self, state: ControllerState) -> WorkflowLeaseBinding | None:
        holder_id = state.get("lease_holder_id")
        fencing_token = state.get("lease_fencing_token")
        if holder_id is None and fencing_token is None:
            return None
        if not isinstance(holder_id, UUID) or not isinstance(fencing_token, int) or isinstance(fencing_token, bool):
            raise RuntimeError("workflow lease binding is invalid")
        return WorkflowLeaseBinding(
            owner_id=state["owner_id"], parent_run_id=state["parent_run_id"],
            holder_id=holder_id, fencing_token=fencing_token,
        )

    async def _persist_node_status(
        self,
        state: ControllerState,
        node_id: str,
        expected_status: str,
        status: str,
    ) -> None:
        await self._assert_lease(state)
        updated = await self.repository.set_node_status(
            state["owner_id"],
            state["parent_run_id"],
            node_id,
            status,
            expected_status=expected_status,
            workflow_lease=self._lease_binding(state),
        )
        if not updated:
            raise WorkflowStateConflict("workflow status changed during settlement")
        state["statuses"][node_id] = status

    async def reconcile(self, state: ControllerState) -> ControllerState:
        await self._assert_lease(state)
        lease = self._lease_binding(state)
        await self.repository.expire_delegations(
            state["owner_id"], state["parent_run_id"], workflow_lease=lease,
        )
        statuses, active, spent = await self.repository.reconcile_child_runs(
            state["owner_id"], state["parent_run_id"], workflow_lease=lease,
        )
        state["statuses"] = statuses
        state["active_children"] = active
        state["spent_child_runs"] = spent
        state["reconciled"] = True
        now = datetime.now(UTC)
        started_at = state.get("started_at")
        started = _parse_timestamp(started_at)
        paused_at = _parse_timestamp(state.get("paused_at"))
        paused_seconds = state.get("paused_seconds", 0.0)
        if not isinstance(paused_seconds, (int, float)) or isinstance(paused_seconds, bool):
            paused_seconds = 0.0
        waiting_only_for_approval = (
            not any(value in {"queued", "running"} for value in statuses.values())
            and any(value == "waiting_approval" for value in statuses.values())
        )
        if started is not None:
            if waiting_only_for_approval:
                paused_at = paused_at or now
            elif paused_at is not None:
                paused_seconds += max(0.0, (now - paused_at).total_seconds())
                paused_at = None
            elapsed = (now - started).total_seconds() - paused_seconds
            if paused_at is not None:
                elapsed -= max(0.0, (now - paused_at).total_seconds())
            state["paused_at"] = paused_at.isoformat() if paused_at is not None else None
            state["paused_seconds"] = float(paused_seconds)
            if elapsed >= state["plan"].limits.duration_cap * 60:
                terminal = {"succeeded", "failed", "rejected", "expired", "invalid", "blocked", "unknown"}
                for row in await self.repository.delegation_states(state["owner_id"], state["parent_run_id"]):
                    node_id = str(row["node_id"])
                    if row.get("status") in terminal:
                        continue
                    child_run_id = row.get("child_run_id")
                    if child_run_id is None:
                        await self._persist_node_status(
                            state, node_id, str(row["status"]), "expired",
                        )
                        continue
                    await self._assert_lease(state)
                    canceled = await self.execution.cancel_queued_run(
                        state["owner_id"], child_run_id, workflow_lease=lease,
                    )
                    if not canceled:
                        await self.execution.request_cancellation(
                            state["owner_id"], child_run_id, workflow_lease=lease,
                        )
                # A cancellation request is not proof that a child Run has stopped.
        return state

    async def dispatch(self, state: ControllerState) -> ControllerState:
        await self._assert_lease(state)
        plan = state["plan"]
        statuses = state["statuses"]
        active = state.get("active_children", 0)
        spent = state.get("spent_child_runs", 0)
        for node in plan.nodes:
            if not child_is_dispatchable(node, statuses, active_children=active, spent_child_runs=spent, limits=plan.limits, reconciled=bool(state.get("reconciled"))):
                continue
            policy = await self.execution.get_policy_for_project(state["owner_id"], node.project_id or UUID(int=0))
            if (
                policy is None
                or not policy.enabled
                or policy.workspace_path != node.workspace_path
                or not set(node.tool_scope).issubset(set(policy.allowed_tools))
            ):
                await self._persist_node_status(state, node.node_id, "approved", "blocked")
                continue
            await self._assert_lease(state)
            try:
                dependency_outputs = await self._dependency_outputs(
                    state["owner_id"], state["parent_run_id"], node,
                )
                await self.runs.create_for_task(
                    state["owner_id"], node.child_task_id,
                    uuid5(NAMESPACE_URL, f"agent-office:workflow-child:{state['parent_run_id']}:{node.node_id}:r1"),
                    execution_policy_revision=policy.revision,
                    expected_instance_id=node.instance_id,
                    expected_definition_id=node.definition_id,
                    expected_definition_version=node.definition_version,
                    expected_task_revision=node.child_revision,
                    expected_model_scope=node.model_scope,
                    dependency_outputs=dependency_outputs,
                    workflow_lease=self._lease_binding(state),
                    workflow_delegation=WorkflowDelegationBinding(
                        parent_run_id=state["parent_run_id"],
                        node_id=node.node_id,
                        canonical_digest=node.canonical_digest(),
                    ),
                )
            except (RunInputError, OrchestrationInputError):
                await self._persist_node_status(state, node.node_id, "approved", "blocked")
                continue
            statuses[node.node_id] = "queued"
            active += 1
            spent += 1
            if state.get("started_at") is None:
                state["started_at"] = datetime.now(UTC).isoformat()
            if active >= plan.limits.max_active_children:
                break
        state["active_children"] = active
        state["spent_child_runs"] = spent
        return state

    async def settle(self, state: ControllerState) -> ControllerState:
        await self._assert_lease(state)
        previous_statuses = dict(state["statuses"])
        statuses = settle_nodes(state["plan"], previous_statuses)
        state["statuses"] = statuses
        for node_id, status in statuses.items():
            previous_status = previous_statuses.get(node_id)
            if previous_status is None:
                raise RuntimeError("workflow status unavailable during settlement")
            if previous_status == status:
                continue
            await self._assert_lease(state)
            updated = await self.repository.set_node_status(
                state["owner_id"], state["parent_run_id"], node_id, status,
                expected_status=previous_status,
                workflow_lease=self._lease_binding(state),
            )
            if not updated:
                raise WorkflowStateConflict("workflow status changed during settlement")
        terminal_states = {"succeeded", "failed", "rejected", "expired", "invalid", "blocked", "unknown"}
        if all(value in terminal_states for value in statuses.values()):
            state["status"] = "failed" if any(value != "succeeded" for value in statuses.values()) else "succeeded"
            rows = await self.repository.delegation_states(state["owner_id"], state["parent_run_id"])
            output_by_node = {str(row["node_id"]): row.get("output") for row in rows}
            outputs: dict[str, Any] = {}
            for node_id, node_status in statuses.items():
                if node_status != "succeeded":
                    continue
                output = output_by_node.get(node_id)
                if not isinstance(output, dict):
                    raise RuntimeError("succeeded workflow node is missing validated output")
                outputs[node_id] = output
            result_payload = {
                "status": state["status"],
                "node_states": statuses,
                "outputs": outputs,
            }
            await self.results.create_for_run(
                state["owner_id"], state["parent_run_id"], canonical_json(result_payload),
                workflow_lease=self._lease_binding(state),
            )
            await self._emit_status_transition(state, state["status"])
            await self.repository.save_checkpoint(
                state["owner_id"], state["parent_run_id"], state, terminal=True,
                workflow_lease=self._lease_binding(state),
            )
        else:
            state["status"] = "waiting_approval" if any(value == "waiting_approval" for value in statuses.values()) else "waiting_children"
            await self._emit_status_transition(state, state["status"])
            await self.repository.save_checkpoint(
                state["owner_id"], state["parent_run_id"], state,
                workflow_lease=self._lease_binding(state),
            )
        return state

    async def _emit_status_transition(
        self, state: ControllerState, status: str, occurred_at: datetime | None = None,
    ) -> None:
        pending = state.get("pending_status_event")
        if pending is not None:
            pending_status = pending.get("status") if isinstance(pending, dict) else None
            pending_sequence = pending.get("sequence") if isinstance(pending, dict) else None
            pending_timestamp = pending.get("occurred_at") if isinstance(pending, dict) else None
            pending_at = _parse_timestamp(pending_timestamp)
            if (
                not isinstance(pending_status, str)
                or not isinstance(pending_sequence, int)
                or isinstance(pending_sequence, bool)
                or pending_at is None
            ):
                raise RuntimeError("workflow pending status event is invalid")
            state["status_sequence"] = pending_sequence
            await self._append_status_event(state, pending_status, pending_at)
            state["last_emitted_status"] = pending_status
            state.pop("pending_status_event", None)
        if state.get("last_emitted_status") == status:
            return
        event_time = occurred_at or datetime.now(UTC)
        sequence = int(state.get("status_sequence", 0)) + 1
        state["status_sequence"] = sequence
        state["pending_status_event"] = {
            "status": status,
            "sequence": sequence,
            "occurred_at": event_time.isoformat(),
        }
        await self.repository.save_checkpoint(
            state["owner_id"], state["parent_run_id"], state,
            workflow_lease=self._lease_binding(state),
        )
        await self._append_status_event(state, status, event_time)
        state["last_emitted_status"] = status
        state.pop("pending_status_event", None)
    async def _append_status_event(
        self, state: ControllerState, status: str, occurred_at: datetime | None = None,
    ) -> None:
        await self._assert_lease(state)
        parent = await self.runs.get_for_owner(state["owner_id"], state["parent_run_id"])
        if parent is None:
            return
        snapshot = parent.configuration_snapshot
        task_snapshot = snapshot.get("task", {})
        instance_snapshot = snapshot.get("agent_instance", {})
        if not isinstance(task_snapshot, dict) or not isinstance(instance_snapshot, dict):
            raise RuntimeError("workflow parent snapshot is incomplete")
        event_type = f"run.{status}"
        sequence = int(state["status_sequence"])
        event = ActivityEventDraft(
            owner_id=state["owner_id"], source_id="orchestration-controller",
            event_id=uuid5(NAMESPACE_URL, f"agent-office:workflow:{parent.id}:{sequence}"),
            schema_version=1, source_session_id=None, workspace_id=None,
            project_id=UUID(str(task_snapshot["project_id"])), task_id=parent.task_id,
            task_revision=parent.task_revision, agent_instance_id=UUID(str(instance_snapshot["id"])),
            run_id=parent.id, event_type=event_type,
            occurred_at=occurred_at or datetime.now(UTC), source_sequence=sequence,
        )
        if status in {"succeeded", "failed", "canceled"}:
            await self.events.append_controller_terminal_event(
                event, workflow_lease=self._lease_binding(state),
            )
        else:
            await self.events.append_event(
                event, workflow_lease=self._lease_binding(state),
            )


    async def run_once(self, owner_id: str, parent_run_id: UUID, plan: WorkflowPlan, statuses: dict[str, str]) -> ControllerState:
        owner_lock = self._owner_locks.setdefault(owner_id, asyncio.Lock())
        async with owner_lock:
            return await self._run_once_with_lease(owner_id, parent_run_id, plan, statuses)

    async def _run_once_with_lease(
        self, owner_id: str, parent_run_id: UUID, plan: WorkflowPlan, statuses: dict[str, str],
    ) -> ControllerState:
        holder_id = uuid4()
        token = await self.repository.acquire_lease(owner_id, parent_run_id, holder_id, seconds=30)
        if token is None:
            raise RuntimeError("workflow lease unavailable")
        lease_lost = asyncio.Event()

        async def renew() -> None:
            while not lease_lost.is_set():
                await asyncio.sleep(10)
                if not await self.repository.renew_lease(
                    owner_id, parent_run_id, holder_id, token, seconds=30,
                ):
                    lease_lost.set()
                    return

        renewal = asyncio.create_task(renew())
        try:
            stored_checkpoint = await self.repository.get_checkpoint(owner_id, parent_run_id)
            if stored_checkpoint is None:
                raise WorkflowCheckpointCorrupt("workflow checkpoint is missing")
            try:
                checkpoint = validate_parent_runtime_checkpoint(stored_checkpoint, plan)
            except OrchestrationInputError as error:
                raise WorkflowCheckpointCorrupt("workflow checkpoint is invalid") from error
            terminal_nodes = {"succeeded", "failed", "rejected", "expired", "invalid", "blocked", "unknown"}
            if checkpoint["status"] in {"succeeded", "failed", "canceled"} and not all(
                status in terminal_nodes for status in statuses.values()
            ):
                raise WorkflowCheckpointCorrupt("terminal checkpoint has non-terminal delegations")
            pending_status_event = checkpoint.get("pending_status_event")
            state: ControllerState = {
                "owner_id": owner_id,
                "parent_run_id": parent_run_id,
                "plan": plan,
                "statuses": statuses,
                "active_children": 0,
                "spent_child_runs": 0,
                "started_at": checkpoint.get("started_at") if isinstance(checkpoint.get("started_at"), str) else None,
                "paused_at": checkpoint.get("paused_at") if isinstance(checkpoint.get("paused_at"), str) else None,
                "paused_seconds": float(checkpoint.get("paused_seconds", 0.0))
                if isinstance(checkpoint.get("paused_seconds", 0.0), (int, float))
                and not isinstance(checkpoint.get("paused_seconds", 0.0), bool)
                else 0.0,
                "status_sequence": int(checkpoint.get("status_sequence", 0))
                if isinstance(checkpoint.get("status_sequence", 0), int)
                and not isinstance(checkpoint.get("status_sequence", 0), bool)
                else 0,
                "last_emitted_status": checkpoint.get("last_emitted_status")
                if isinstance(checkpoint.get("last_emitted_status"), str)
                else None,
                "lease_holder_id": holder_id,
                "lease_fencing_token": token,
                "lease_lost": lease_lost,
            }
            if pending_status_event is not None:
                state["pending_status_event"] = pending_status_event
            await self._ensure_pending_approvals(
                owner_id, parent_run_id, plan,
                workflow_lease=self._lease_binding(state),
            )
            result = await self.graph().ainvoke(state)
            if lease_lost.is_set():
                raise RuntimeError("workflow lease lost")
            return result
        finally:
            lease_lost.set()
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            await self.repository.release_lease(owner_id, parent_run_id, holder_id, token)

    async def resume(self, owner_id: str, parent_run_id: UUID) -> ControllerState | None:
        plan = await self.repository.get_plan(owner_id, parent_run_id)
        if plan is None:
            return None
        statuses = await self.repository.node_statuses(owner_id, parent_run_id)
        terminal_nodes = {"succeeded", "failed", "rejected", "expired", "invalid", "blocked", "unknown"}
        if statuses and all(status in terminal_nodes for status in statuses.values()):
            checkpoint = await self.repository.get_checkpoint(owner_id, parent_run_id)
            if checkpoint is None:
                return {
                    "status": "succeeded"
                    if all(status == "succeeded" for status in statuses.values())
                    else "failed"
                }
        for attempt in range(2):
            try:
                return await self.run_once(owner_id, parent_run_id, plan, statuses)
            except WorkflowCheckpointCorrupt:
                logger.error(
                    "workflow blocked because its checkpoint requires manual reconciliation",
                    extra={"owner_id": owner_id, "parent_run_id": str(parent_run_id)},
                )
                return {"status": "blocked"}
            except WorkflowStateConflict:
                if attempt == 1:
                    return None
                statuses = await self.repository.node_statuses(owner_id, parent_run_id)
        return None


async def controller_loop(controller: WorkflowController, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                await controller.approvals.expire_due(datetime.now(UTC))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("workflow approval expiry failed")
            try:
                active_parents = await controller.repository.active_parent_runs()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("workflow active parent listing failed")
                continue
            for owner_id, parent_run_id in active_parents:
                try:
                    await controller.resume(owner_id, parent_run_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("workflow parent reconciliation failed")
