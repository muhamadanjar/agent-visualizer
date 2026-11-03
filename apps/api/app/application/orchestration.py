"""Application service for planning and starting a parent-run workflow."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import UUID, uuid4

from app.domain.execution import ExecutionRepository
from app.domain.instances import AgentInstanceRepository
from app.domain.model_endpoints import ModelEndpointRepository
from app.domain.orchestration import (
    DelegationSpec,
    OrchestrationInputError,
    OrchestrationRepository,
    WorkflowLimits,
    WorkflowPlan,
    canonical_json,
    digest,
    select_agent_instances,
)
from app.domain.registry import AgentRegistryRepository
from app.domain.tasks import TaskSpec


class PlanningBrokerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlanningBroker:
    url: str
    token: str
    timeout_seconds: int = 30

    async def complete(self, request_body: Mapping[str, Any]) -> dict[str, Any]:
        def send() -> dict[str, Any]:
            body = json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode()
            request = urllib.request.Request(
                self.url,
                data=body,
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed = json.loads(response.read(1_048_576))
            except (OSError, urllib.error.URLError, ValueError) as error:
                raise PlanningBrokerError("planning broker unavailable") from error
            if not isinstance(parsed, dict):
                raise PlanningBrokerError("planning broker returned invalid JSON")
            return parsed
        return await asyncio.to_thread(send)


def _content(response: Mapping[str, Any]) -> Mapping[str, Any]:
    if "nodes" in response:
        return response
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OrchestrationInputError("plan_response")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    value = message.get("content") if isinstance(message, dict) else None
    if not isinstance(value, str):
        raise OrchestrationInputError("plan_response")
    try:
        parsed = json.loads(value)
    except ValueError as error:
        raise OrchestrationInputError("plan_response") from error
    if not isinstance(parsed, dict):
        raise OrchestrationInputError("plan_response")
    return parsed


async def create_workflow_plan(
    *, owner_id: str, parent_run_id: UUID, parent_project_id: UUID, goal: str,
    input_refs: tuple[str, ...], limits: WorkflowLimits, broker: PlanningBroker,
    instances: AgentInstanceRepository, definitions: AgentRegistryRepository,
    model_endpoints: ModelEndpointRepository, execution: ExecutionRepository,
    repository: OrchestrationRepository, parent_endpoint: Mapping[str, Any],
    parent_tool_scope: set[str], child_policy_tools: set[str], child_workspace_path: str,
) -> WorkflowPlan:
    limits.validate()
    if not goal.strip() or len(goal) > 10000 or len(input_refs) > 100:
        raise OrchestrationInputError("input")
    if any(not ref.strip() or len(ref) > 2048 for ref in input_refs):
        raise OrchestrationInputError("input_refs")
    if not child_workspace_path:
        raise OrchestrationInputError("workspace")

    all_instances = []
    page_offset = 0
    while True:
        page = await instances.list_for_owner(
            owner_id, include_archived=False, limit=200, offset=page_offset,
        )
        all_instances.extend(page)
        if len(page) < 200:
            break
        page_offset += 200
    all_instances = [
        instance for instance in all_instances
        if instance.project_id in (None, parent_project_id)
    ]
    availability_by_id = {
        item.instance_id: item.availability
        for item in await execution.list_agent_availability(
            owner_id, [instance.id for instance in all_instances], now=datetime.now(UTC),
        )
    }
    available_candidates: list[dict[str, Any]] = []
    provider_ready: set[str] = set()
    for instance in all_instances:
        definition = await definitions.get_for_owner(owner_id, instance.definition_id)
        if definition is None or not definition.enabled or definition.archived_at is not None:
            continue
        configuration = definition.configuration
        if configuration is None:
            continue
        endpoint = await model_endpoints.get_for_owner(owner_id, configuration.model_endpoint_id)
        if endpoint is None or endpoint.archived_at is not None:
            continue
        if endpoint.credential_env not in provider_ready:
            runner = await execution.get_ready_runner(
                max_age_seconds=45, credential_env=endpoint.credential_env,
            )
            if runner is not None:
                provider_ready.add(endpoint.credential_env)
        capabilities_value = definition.unmapped_metadata.get("capabilities", ())
        if not isinstance(capabilities_value, (list, tuple)) or any(not isinstance(value, str) for value in capabilities_value):
            capabilities_value = ()
        availability = availability_by_id.get(instance.id, "unknown")
        active_count = 1 if availability in {"working", "waiting"} else 0
        available_candidates.append({
            "id": instance.id,
            "enabled": definition.enabled,
            "archived": instance.archived_at is not None or definition.archived_at is not None,
            "role": configuration.role,
            "capabilities": tuple(capabilities_value),
            "effective_tools": tuple(set(configuration.allowed_tools) & child_policy_tools & parent_tool_scope),
            "provider_key": endpoint.credential_env,
            "project_id": instance.project_id,
            "queued_or_leased": active_count,
            "workload_limit": configuration.concurrency_limit if availability != "unknown" else 0,
            "definition": definition,
            "instance": instance,
            "configuration": configuration,
            "endpoint": endpoint,
            "availability": availability,
        })
    if not any(candidate["provider_key"] in provider_ready for candidate in available_candidates):
        raise OrchestrationInputError("no_ready_agents")
    if not available_candidates:
        raise OrchestrationInputError("no_eligible_agents")

    catalog = [{
        "instance_id": str(candidate["id"]),
        "display_name": candidate["instance"].display_name,
        "definition_id": str(candidate["definition"].id),
        "definition_version": candidate["definition"].version,
        "role": candidate["role"],
        "capabilities": sorted(candidate["capabilities"]),
        "tools": sorted(candidate["effective_tools"]),
        "project_id": str(candidate["project_id"]) if candidate["project_id"] else None,
        "model_endpoint_id": str(candidate["endpoint"].id),
        "model_id": candidate["configuration"].model_id,
        "availability": candidate["availability"],
    } for candidate in available_candidates if candidate["provider_key"] in provider_ready]
    prompt = canonical_json({
        "goal": goal,
        "input_refs": list(input_refs),
        "limits": {"max_child_runs": limits.child_cap, "max_duration_minutes": limits.duration_cap},
        "eligible_agents": catalog,
        "required_response": {
            "nodes": [{
                "node_id": "stable unique id",
                "role": "role from eligible_agents",
                "capabilities": ["required capabilities from eligible_agents"],
                "title": "Task title",
                "goal": "specific child goal",
                "input_text": "optional bounded instructions",
                "output_description": "acceptance criteria",
                "tool_scope": ["subset of eligible agent tools"],
                "dependencies": ["node_id values"],
                "output_schema": "optional Draft 2020-12 object schema",
            }],
        },
    })
    if len(prompt.encode("utf-8")) > 32 * 1024:
        raise OrchestrationInputError("agent_catalog_size")
    response = await broker.complete({
        "endpoint": {
            "credential_env": parent_endpoint["credential_env"],
            "endpoint_url": parent_endpoint["endpoint_url"],
            "model_id": parent_endpoint["model_id"],
        },
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": "Plan a bounded DAG of child Tasks. Select only agents and privileges in eligible_agents; never invent identifiers, endpoints, tools, dependencies, or credentials. Return JSON only."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    })
    raw = _content(response)
    if set(raw) != {"nodes"} or not isinstance(raw["nodes"], list):
        raise OrchestrationInputError("plan_response")
    raw_nodes = raw["nodes"]
    if limits.child_cap == 0 or not raw_nodes or len(raw_nodes) > limits.child_cap:
        raise OrchestrationInputError("child_runs")

    nodes: list[DelegationSpec] = []
    task_specs: dict[UUID, TaskSpec] = {}
    node_ids: set[str] = set()
    allowed_fields = {
        "node_id", "role", "capabilities", "title", "goal", "input_text",
        "output_description", "tool_scope", "dependencies", "output_schema",
        "model_scope",
    }
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict) or not set(raw_node).issubset(allowed_fields):
            raise OrchestrationInputError("node")
        node_id = raw_node.get("node_id")
        role = raw_node.get("role")
        capabilities = raw_node.get("capabilities", [])
        raw_tools = raw_node.get("tool_scope", [])
        raw_dependencies = raw_node.get("dependencies", [])
        if (
            not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 120
            or node_id != node_id.strip() or not node_id.isascii()
            or not all(char.isalnum() or char in "_-" for char in node_id)
            or node_id in node_ids
            or not isinstance(role, str) or not role.strip() or len(role) > 120
            or not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities)
            or len(set(capabilities)) != len(capabilities)
            or not isinstance(raw_tools, list) or any(not isinstance(value, str) for value in raw_tools)
            or len(set(raw_tools)) != len(raw_tools)
            or not isinstance(raw_dependencies, list) or any(not isinstance(value, str) for value in raw_dependencies)
            or len(set(raw_dependencies)) != len(raw_dependencies)
        ):
            raise OrchestrationInputError("node")
        node_ids.add(node_id)
        selected = select_agent_instances(
            available_candidates, role=role, capabilities=set(capabilities),
            required_tools=set(raw_tools), provider_ready=provider_ready,
            policy_tools=child_policy_tools & parent_tool_scope, project_id=parent_project_id,
        )
        if not selected:
            raise OrchestrationInputError("no_eligible_agent")
        candidate = selected[0]
        instance = candidate["instance"]
        definition = candidate["definition"]
        configuration = candidate["configuration"]
        endpoint = candidate["endpoint"]
        requested_model = raw_node.get("model_scope", {})
        if not isinstance(requested_model, dict) or set(requested_model) - {"model_id"}:
            raise OrchestrationInputError("model_scope")
        if requested_model.get("model_id") not in (None, configuration.model_id):
            raise OrchestrationInputError("model_scope")
        title = raw_node.get("title")
        child_goal = raw_node.get("goal")
        input_text = raw_node.get("input_text", "")
        output_description = raw_node.get("output_description", "workflow output")
        if (
            not isinstance(title, str) or not title.strip() or len(title.strip()) > 200
            or not isinstance(child_goal, str) or not child_goal.strip() or len(child_goal.strip()) > 10000
            or not isinstance(input_text, str) or len(input_text) > 10000
            or not isinstance(output_description, str) or not output_description.strip() or len(output_description.strip()) > 2000
        ):
            raise OrchestrationInputError("node")
        schema = raw_node.get("output_schema")
        if schema is not None and not isinstance(schema, dict):
            raise OrchestrationInputError("schema")
        child_task_id = uuid4()
        task_specs[child_task_id] = TaskSpec(
            project_id=parent_project_id,
            agent_instance_id=instance.id,
            title=title.strip(),
            goal=child_goal.strip(),
            input_text=input_text,
            resource_refs=input_refs,
            acceptance_criteria=(output_description.strip(),),
            blocked_by_task_ids=(),
            max_duration_minutes=limits.duration_cap,
            max_child_runs=None,
            output_schema=schema,
            tool_scope=tuple(raw_tools),
        )
        model_scope = {
            "model_endpoint_id": str(endpoint.id),
            "model_id": configuration.model_id,
            "route": configuration.route or endpoint.default_route,
            "adapter": endpoint.adapter,
            "endpoint_url": endpoint.endpoint_url,
            "credential_env": endpoint.credential_env,
        }
        nodes.append(DelegationSpec(
            node_id=node_id, child_task_id=child_task_id, child_revision=1,
            definition_id=definition.id, definition_version=definition.version,
            instance_id=instance.id, project_id=parent_project_id,
            workspace_path=child_workspace_path,
            input_payload={"input_text": input_text, "refs": list(input_refs)},
            resource_refs=input_refs,
            dependencies=tuple(raw_dependencies),
            output_schema=schema,
            model_scope=model_scope,
            tool_scope=tuple(raw_tools),
            limits={"max_duration_minutes": limits.duration_cap, "max_child_runs": limits.child_cap},
        ))

    plan = WorkflowPlan(
        parent_run_id=parent_run_id,
        plan_digest=digest({"nodes": sorted(node.canonical_digest() for node in nodes)}),
        revision=1, nodes=tuple(nodes), limits=limits,
    )
    task_id_by_node = {node.node_id: node.child_task_id for node in nodes}
    for node in nodes:
        task_specs[node.child_task_id] = replace(
            task_specs[node.child_task_id],
            blocked_by_task_ids=tuple(task_id_by_node[dep] for dep in node.dependencies),
        )
    await repository.persist_plan_with_tasks(owner_id, plan, task_specs)
    return plan
