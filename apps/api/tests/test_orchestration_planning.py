import asyncio

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.orchestration import create_workflow_plan
from app.domain.orchestration import OrchestrationInputError, WorkflowLimits


def test_planner_rejects_child_output_without_schema() -> None:
    instance_id = uuid4()
    definition_id = uuid4()
    endpoint_id = uuid4()
    instance = SimpleNamespace(
        id=instance_id, definition_id=definition_id, project_id=None,
        archived_at=None, display_name="Agent",
    )
    definition = SimpleNamespace(
        id=definition_id, version=1, enabled=True, archived_at=None,
        configuration=SimpleNamespace(
            model_endpoint_id=endpoint_id, role="researcher", allowed_tools=("list_files",),
            concurrency_limit=1, model_id="model",
        ),
        unmapped_metadata={"capabilities": []},
    )
    endpoint = SimpleNamespace(
        id=endpoint_id, credential_env="provider", default_route="/v1",
        adapter="openai", endpoint_url="http://localhost/v1", archived_at=None,
    )

    class Instances:
        async def list_for_owner(self, *_args, **_kwargs):
            return [instance]

    class Definitions:
        async def get_for_owner(self, *_args):
            return definition

    class Endpoints:
        async def get_for_owner(self, *_args):
            return endpoint

    class Execution:
        async def list_agent_availability(self, *_args, **_kwargs):
            return [SimpleNamespace(instance_id=instance_id, availability="idle")]

        async def get_ready_runner(self, **_kwargs):
            return object()

    class Broker:
        async def complete(self, request):
            assert request["messages"][1]["role"] == "user"
            assert "eligible_agents" in request["messages"][1]["content"]
            assert "Inventory services" in request["messages"][1]["content"]
            return {"nodes": [{
                "node_id": "child", "role": "researcher", "capabilities": [],
                "title": "Inventory", "goal": "Inventory services",
                "output_description": "Structured service inventory",
                "tool_scope": ["list_files"], "dependencies": [],
                "output_schema": None,
            }]}

    class Repository:
        async def persist_plan_with_tasks(self, *_args):
            raise AssertionError("invalid plan must not be persisted")

    with pytest.raises(OrchestrationInputError, match="schema"):
        asyncio.run(create_workflow_plan(
            owner_id="owner", parent_run_id=uuid4(), parent_project_id=uuid4(),
            goal="Inventory services", input_refs=(), limits=WorkflowLimits(),
            broker=Broker(), instances=Instances(), definitions=Definitions(),
            model_endpoints=Endpoints(), execution=Execution(), repository=Repository(),
            parent_endpoint={"credential_env": "provider", "endpoint_url": "http://localhost/v1", "model_id": "model"},
            parent_tool_scope={"list_files"}, child_policy_tools={"list_files"},
            child_workspace_path="workspace",
        ))
