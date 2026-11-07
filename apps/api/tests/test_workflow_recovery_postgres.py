from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.orchestration import (
    DelegationSpec,
    OrchestrationInputError,
    WorkflowLeaseBinding,
    WorkflowLimits,
    WorkflowPlan,
    digest,
)
from app.domain.tasks import TaskSpec
from app.infrastructure.postgres.orchestration import PostgresOrchestrationRepository


def test_active_workflow_scan_includes_missing_and_unsettled_checkpoints() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    if not unquote(urlsplit(database_url).path.lstrip("/")).endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable database ending in _test")
    async_url = (
        database_url
        if "postgresql+asyncpg://" in database_url
        else database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )

    async def verify() -> None:
        engine = create_async_engine(async_url, pool_size=1)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        repository = PostgresOrchestrationRepository(sessions)
        active_missing = (f"active-missing-{uuid4()}", uuid4())
        terminal_missing = (f"terminal-missing-{uuid4()}", uuid4())
        pending_terminal = (f"pending-terminal-{uuid4()}", uuid4())
        malformed = (f"malformed-{uuid4()}", uuid4())
        try:
            async with sessions() as session:
                await session.execute(text("""
                    CREATE TEMP TABLE orchestration_delegations (
                        owner_id text, parent_run_id uuid, status text
                    )
                """))
                await session.execute(text("""
                    CREATE TEMP TABLE orchestration_checkpoints (
                        owner_id text, parent_run_id uuid, state jsonb
                    )
                """))
                await session.execute(text("""
                    INSERT INTO orchestration_delegations(owner_id,parent_run_id,status) VALUES
                        (:active_owner,:active_run,'waiting_approval'),
                        (:terminal_owner,:terminal_run,'succeeded'),
                        (:pending_owner,:pending_run,'succeeded'),
                        (:malformed_owner,:malformed_run,'succeeded')
                """), {
                    "active_owner": active_missing[0], "active_run": active_missing[1],
                    "terminal_owner": terminal_missing[0], "terminal_run": terminal_missing[1],
                    "pending_owner": pending_terminal[0], "pending_run": pending_terminal[1],
                    "malformed_owner": malformed[0], "malformed_run": malformed[1],
                })
                await session.execute(text("""
                    INSERT INTO orchestration_checkpoints(owner_id,parent_run_id,state) VALUES
                        (:pending_owner,:pending_run,
                            '{"status":"succeeded","pending_status_event":{"status":"succeeded"}}'::jsonb),
                        (:malformed_owner,:malformed_run,'[]'::jsonb)
                """), {
                    "pending_owner": pending_terminal[0], "pending_run": pending_terminal[1],
                    "malformed_owner": malformed[0], "malformed_run": malformed[1],
                })
                await session.commit()

            recovered = set(await repository.active_parent_runs())
            assert recovered == {active_missing, pending_terminal, malformed}
            with pytest.raises(OrchestrationInputError, match="workflow_active"):
                await repository.persist_plan_with_tasks(
                    active_missing[0],
                    SimpleNamespace(parent_run_id=uuid4()),
                    {},
                )
        finally:
            await engine.dispose()

    asyncio.run(verify())
def test_persist_plan_round_trips_jsonb_delegation_and_checkpoint_values() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    if not unquote(urlsplit(database_url).path.lstrip("/")).endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable database ending in _test")
    async_url = (
        database_url
        if "postgresql+asyncpg://" in database_url
        else database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )

    async def verify() -> None:
        engine = create_async_engine(async_url, pool_size=1)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = f"workflow-jsonb-{uuid4()}"
        parent_run_id = uuid4()
        child_task_id = uuid4()
        project_id = uuid4()
        instance_id = uuid4()
        definition_id = uuid4()
        input_payload = {"input_text": "Inspect safe files only.", "refs": []}
        output_schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
        model_scope = {"model_endpoint_id": str(uuid4()), "model_id": "test-model"}
        limits = {"max_child_runs": 8, "max_duration_minutes": 60}
        node = DelegationSpec(
            node_id="jsonb_round_trip",
            child_task_id=child_task_id,
            child_revision=1,
            definition_id=definition_id,
            definition_version=1,
            instance_id=instance_id,
            project_id=project_id,
            workspace_path="services",
            input_payload=input_payload,
            resource_refs=("README.md",),
            dependencies=(),
            output_schema=output_schema,
            model_scope=model_scope,
            tool_scope=("list_files", "read_file"),
            limits=limits,
        )
        plan = WorkflowPlan(
            parent_run_id,
            digest({"nodes": [node.canonical_digest()]}),
            1,
            (node,),
            WorkflowLimits(),
        )
        task_spec = TaskSpec(
            project_id=project_id,
            agent_instance_id=instance_id,
            title="Persist delegated task",
            goal="Persist its structured input.",
            input_text="",
            resource_refs=("README.md",),
            acceptance_criteria=("JSON values round-trip.",),
            blocked_by_task_ids=(),
            max_duration_minutes=60,
            max_child_runs=8,
            output_schema=output_schema,
            tool_scope=("list_files", "read_file"),
        )
        repository = PostgresOrchestrationRepository(sessions)
        try:
            async with sessions() as session:
                for ddl in (
                    "CREATE TEMP TABLE tasks (id uuid PRIMARY KEY, owner_id text NOT NULL, current_revision integer NOT NULL, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now())",
                    "CREATE TEMP TABLE task_revisions (task_id uuid NOT NULL, owner_id text NOT NULL, revision integer NOT NULL, project_id uuid NOT NULL, agent_instance_id uuid, title text NOT NULL, goal text NOT NULL, input_text text NOT NULL, resource_refs text[] NOT NULL, acceptance_criteria text[] NOT NULL, max_duration_minutes integer, max_child_runs integer, output_schema jsonb, tool_scope text[], created_at timestamptz DEFAULT now())",
                    "CREATE TEMP TABLE orchestration_delegations (owner_id text NOT NULL, id uuid NOT NULL, parent_run_id uuid NOT NULL, node_id text NOT NULL, revision integer NOT NULL, child_task_id uuid NOT NULL, child_revision integer NOT NULL, definition_id uuid NOT NULL, definition_version integer NOT NULL, instance_id uuid NOT NULL, project_id uuid, workspace_path text NOT NULL, input_payload jsonb NOT NULL, resource_refs jsonb NOT NULL, dependencies jsonb NOT NULL, output_schema jsonb, model_scope jsonb NOT NULL, tool_scope jsonb NOT NULL, limits jsonb NOT NULL, canonical_digest text NOT NULL, plan_digest text NOT NULL, approval_id uuid, child_run_id uuid, output jsonb, status text NOT NULL, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now())",
                    "CREATE TEMP TABLE orchestration_checkpoints (owner_id text NOT NULL, parent_run_id uuid NOT NULL, version integer NOT NULL, state jsonb NOT NULL, updated_at timestamptz DEFAULT now())",
                    "CREATE TEMP TABLE orchestration_leases (owner_id text NOT NULL, parent_run_id uuid NOT NULL, holder_id uuid NOT NULL, fencing_token integer NOT NULL, expires_at timestamptz NOT NULL)",
                ):
                    await session.execute(text(ddl))
                await session.commit()

            await repository.persist_plan_with_tasks(
                owner_id, plan, {child_task_id: task_spec},
            )
            holder_id = uuid4()
            async with sessions.begin() as session:
                await session.execute(text("""
                    INSERT INTO orchestration_leases(owner_id,parent_run_id,holder_id,fencing_token,expires_at)
                    VALUES (:owner,:parent,:holder,1,now()+interval '5 minutes')
                """), {
                    "owner": owner_id, "parent": parent_run_id, "holder": holder_id,
                })
            workflow_lease = WorkflowLeaseBinding(
                owner_id, parent_run_id, holder_id, 1,
            )
            async with sessions() as session:
                initial_checkpoint_updated_at = (
                    await session.execute(text("""
                        SELECT updated_at FROM orchestration_checkpoints
                        WHERE owner_id=:owner AND parent_run_id=:parent
                    """), {"owner": owner_id, "parent": parent_run_id})
                ).scalar_one()
            updated_state = {
                "plan_digest": plan.plan_digest,
                "plan_revision": 1,
                "active_children": 0,
                "spent_child_runs": 0,
                "status": "waiting_children",
                "status_sequence": 0,
                "pending_status_event": {
                    "status": "waiting_children",
                    "sequence": 0,
                    "occurred_at": "2026-11-07T16:00:00+00:00",
                },
            }
            await repository.save_checkpoint(
                owner_id, parent_run_id, updated_state,
                workflow_lease=workflow_lease,
            )


            async with sessions() as session:
                delegation = (
                    await session.execute(text("""
                        SELECT input_payload, resource_refs, dependencies, output_schema,
                               model_scope, tool_scope, limits, status
                        FROM orchestration_delegations
                        WHERE owner_id=:owner AND parent_run_id=:parent
                    """), {"owner": owner_id, "parent": parent_run_id})
                ).mappings().one()
                checkpoint = (
                    await session.execute(text("""
                        SELECT state, version, updated_at FROM orchestration_checkpoints
                        WHERE owner_id=:owner AND parent_run_id=:parent
                    """), {"owner": owner_id, "parent": parent_run_id})
                ).mappings().one()
                assert delegation["input_payload"] == input_payload
                assert delegation["resource_refs"] == ["README.md"]
                assert delegation["dependencies"] == []
                assert delegation["output_schema"] == output_schema
                assert delegation["model_scope"] == model_scope
                assert delegation["tool_scope"] == ["list_files", "read_file"]
                assert delegation["limits"] == limits
                assert delegation["status"] == "waiting_approval"
                assert checkpoint["state"]["status"] == "waiting_children"
                assert checkpoint["state"]["plan_digest"] == plan.plan_digest
                assert checkpoint["state"]["pending_status_event"]["status"] == "waiting_children"
                assert checkpoint["version"] == 2
                assert checkpoint["updated_at"] > initial_checkpoint_updated_at
            await repository.save_checkpoint(
                owner_id, parent_run_id, {"status": "succeeded"},
                terminal=True, workflow_lease=workflow_lease,
            )
            async with sessions() as session:
                remaining = await session.execute(text("""
                    SELECT count(*) FROM orchestration_checkpoints
                    WHERE owner_id=:owner AND parent_run_id=:parent
                """), {"owner": owner_id, "parent": parent_run_id})
                assert remaining.scalar_one() == 0
        finally:
            await engine.dispose()

    asyncio.run(verify())
def test_reconcile_child_runs_persists_validated_output_as_jsonb() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    if not unquote(urlsplit(database_url).path.lstrip("/")).endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable database ending in _test")
    async_url = (
        database_url
        if "postgresql+asyncpg://" in database_url
        else database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )

    async def verify() -> None:
        engine = create_async_engine(async_url, pool_size=1)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = f"workflow-output-{uuid4()}"
        parent_run_id = uuid4()
        child_run_id = uuid4()
        holder_id = uuid4()
        output_schema = (
            '{"type":"object","required":["manifest_files","service_directories"],'
            '"properties":{"manifest_files":{"type":"array","items":{"type":"string"}},'
            '"service_directories":{"type":"array","items":{"type":"string"}}},'
            '"additionalProperties":false}'
        )
        output = (
            '{"manifest_files":["apps/api/pyproject.toml"],'
            '"service_directories":["apps/api"]}'
        )
        repository = PostgresOrchestrationRepository(sessions)
        try:
            async with sessions.begin() as session:
                for ddl in (
                    "CREATE TEMP TABLE orchestration_leases (owner_id text, parent_run_id uuid, holder_id uuid, fencing_token integer, expires_at timestamptz)",
                    "CREATE TEMP TABLE orchestration_delegations (owner_id text, parent_run_id uuid, node_id text, status text, child_run_id uuid, output jsonb, output_schema jsonb)",
                    "CREATE TEMP TABLE run_dispatches (owner_id text, run_id uuid, state text)",
                    "CREATE TEMP TABLE run_results (owner_id text, run_id uuid, result_text text)",
                ):
                    await session.execute(text(ddl))
                await session.execute(text("""
                    INSERT INTO orchestration_leases(owner_id,parent_run_id,holder_id,fencing_token,expires_at)
                    VALUES (:owner,:parent,:holder,1,now()+interval '5 minutes')
                """), {"owner": owner_id, "parent": parent_run_id, "holder": holder_id})
                await session.execute(text("""
                    INSERT INTO orchestration_delegations(
                        owner_id,parent_run_id,node_id,status,child_run_id,output,output_schema
                    ) VALUES (
                        :owner,:parent,'list_workspace_root','queued',:child,NULL,CAST(:schema AS jsonb)
                    )
                """), {
                    "owner": owner_id, "parent": parent_run_id,
                    "child": child_run_id, "schema": output_schema,
                })
                await session.execute(text("""
                    INSERT INTO run_dispatches(owner_id,run_id,state)
                    VALUES (:owner,:child,'succeeded')
                """), {"owner": owner_id, "child": child_run_id})
                await session.execute(text("""
                    INSERT INTO run_results(owner_id,run_id,result_text)
                    VALUES (:owner,:child,:output)
                """), {"owner": owner_id, "child": child_run_id, "output": output})

            statuses, active, spent = await repository.reconcile_child_runs(
                owner_id, parent_run_id,
                workflow_lease=WorkflowLeaseBinding(owner_id, parent_run_id, holder_id, 1),
            )
            assert statuses == {"list_workspace_root": "succeeded"}
            assert active == 0
            assert spent == 1
            async with sessions() as session:
                persisted = (
                    await session.execute(text("""
                        SELECT status,output FROM orchestration_delegations
                        WHERE owner_id=:owner AND parent_run_id=:parent
                    """), {"owner": owner_id, "parent": parent_run_id})
                ).mappings().one()
                assert persisted["status"] == "succeeded"
                assert persisted["output"] == {
                    "manifest_files": ["apps/api/pyproject.toml"],
                    "service_directories": ["apps/api"],
                }
        finally:
            await engine.dispose()

    asyncio.run(verify())
