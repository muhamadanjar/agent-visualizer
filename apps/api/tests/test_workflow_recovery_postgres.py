from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
import os
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.workflow_controller import WorkflowController
from app.domain.orchestration import (
    DelegationSpec,
    OrchestrationInputError,
    WorkflowLeaseBinding,
    WorkflowLeaseLost,
    WorkflowLimits,
    WorkflowPlan,
    digest,
)
from app.domain.tasks import TaskSpec
from app.domain.evidence import ApprovalRequestDraft
from app.domain.execution import WorkspacePolicyDraft
from app.infrastructure.postgres.evidence import PostgresApprovalRepository
from app.infrastructure.postgres.orchestration import PostgresOrchestrationRepository
import asyncpg
from dataclasses import replace
from sqlalchemy.engine import make_url

from app.domain.agent_configuration import AgentDefinitionConfiguration
from app.domain.model_endpoints import ModelEndpointDraft
import app.main as api_main
from app.main import create_app
from app.infrastructure.postgres.migrate import (
    apply_alembic_migrations,
    apply_migrations,
    ensure_application_role,
)
from test_api import ORIGIN, TEST_PASSWORD, database_ready, settings

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
                    "CREATE TEMP TABLE orchestration_delegations (owner_id text, parent_run_id uuid, node_id text, status text, child_run_id uuid, output jsonb, output_schema jsonb, updated_at timestamptz DEFAULT now())",
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
def test_expire_due_allows_outer_join_when_no_approvals_are_expired() -> None:
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
        repository = PostgresApprovalRepository(sessions)
        try:
            async with sessions.begin() as session:
                await session.execute(text("""
                    CREATE TEMP TABLE approval_requests (
                        owner_id text NOT NULL, id uuid NOT NULL, run_id uuid NOT NULL,
                        request_id uuid NOT NULL, action_type text NOT NULL,
                        action_digest text NOT NULL, resource_ref text,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        expires_at timestamptz NOT NULL, PRIMARY KEY(owner_id,id)
                    )
                """))
                await session.execute(text("""
                    CREATE TEMP TABLE approval_decisions (
                        owner_id text NOT NULL, approval_id uuid NOT NULL,
                        decision text NOT NULL,
                        decided_at timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY(owner_id,approval_id)
                    )
                """))
            assert await repository.expire_due(datetime.now(UTC)) == 0
        finally:
            await engine.dispose()

    asyncio.run(verify())

def test_expired_lease_fences_stale_holder_during_concurrent_reacquisition() -> None:
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
        schema = f"lease_race_{uuid4().hex}"
        owner_id = f"lease-race-{uuid4()}"
        parent_run_id = uuid4()
        old_holder, new_holder = uuid4(), uuid4()
        bootstrap = create_async_engine(async_url, pool_size=1)
        engine = None
        try:
            async with bootstrap.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f"""
                    CREATE TABLE "{schema}".orchestration_leases (
                        owner_id text PRIMARY KEY,
                        parent_run_id uuid NOT NULL,
                        holder_id uuid NOT NULL,
                        fencing_token bigint NOT NULL,
                        expires_at timestamptz NOT NULL,
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE(owner_id, parent_run_id)
                    )
                """))
                await connection.execute(text(f"""
                    CREATE TABLE "{schema}".orchestration_delegations (
                        owner_id text NOT NULL,
                        parent_run_id uuid NOT NULL,
                        node_id text NOT NULL,
                        status text NOT NULL,
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY(owner_id, parent_run_id, node_id)
                    )
                """))

            engine = create_async_engine(
                async_url,
                pool_size=4,
                connect_args={"server_settings": {"search_path": schema}},
            )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            repository = PostgresOrchestrationRepository(sessions)
            async with sessions.begin() as session:
                await session.execute(text("""
                    INSERT INTO orchestration_delegations(
                        owner_id, parent_run_id, node_id, status
                    ) VALUES (:owner, :parent, 'child', 'approved')
                """), {"owner": owner_id, "parent": parent_run_id})
            old_token = await repository.acquire_lease(
                owner_id, parent_run_id, old_holder, seconds=30,
            )
            assert old_token == 1
            assert await repository.acquire_lease(
                owner_id, parent_run_id, new_holder, seconds=30,
            ) is None

            async with sessions.begin() as session:
                await session.execute(text("""
                    UPDATE orchestration_leases
                    SET expires_at=now()-interval '1 second'
                    WHERE owner_id=:owner
                """), {"owner": owner_id})

            new_token, stale_renewed = await asyncio.gather(
                repository.acquire_lease(
                    owner_id, parent_run_id, new_holder, seconds=30,
                ),
                repository.renew_lease(
                    owner_id, parent_run_id, old_holder, old_token, seconds=30,
                ),
            )
            assert new_token == 2
            assert not stale_renewed

            controller = WorkflowController(
                repository, None, None, None, None, None,  # type: ignore[arg-type]
            )
            with pytest.raises(RuntimeError, match="workflow lease lost"):
                await controller._assert_lease({
                    "owner_id": owner_id,
                    "parent_run_id": parent_run_id,
                    "lease_holder_id": old_holder,
                    "lease_fencing_token": old_token,
                    "lease_lost": asyncio.Event(),
                })
            stale_binding = WorkflowLeaseBinding(
                owner_id, parent_run_id, old_holder, old_token,
            )
            current_binding = WorkflowLeaseBinding(
                owner_id, parent_run_id, new_holder, new_token,
            )
            with pytest.raises(WorkflowLeaseLost, match="workflow lease lost"):
                await repository.set_node_status(
                    owner_id, parent_run_id, "child", "succeeded",
                    expected_status="approved", workflow_lease=stale_binding,
                )
            async with sessions() as session:
                status = await session.execute(text("""
                    SELECT status FROM orchestration_delegations
                    WHERE owner_id=:owner AND parent_run_id=:parent AND node_id='child'
                """), {"owner": owner_id, "parent": parent_run_id})
                assert status.scalar_one() == "approved"
            assert await repository.set_node_status(
                owner_id, parent_run_id, "child", "succeeded",
                expected_status="approved", workflow_lease=current_binding,
            )
            async with sessions() as session:
                status = await session.execute(text("""
                    SELECT status FROM orchestration_delegations
                    WHERE owner_id=:owner AND parent_run_id=:parent AND node_id='child'
                """), {"owner": owner_id, "parent": parent_run_id})
                assert status.scalar_one() == "succeeded"
            assert not await repository.release_lease(
                owner_id, parent_run_id, old_holder, old_token,
            )
            assert await repository.renew_lease(
                owner_id, parent_run_id, new_holder, new_token, seconds=30,
            )
            assert await repository.release_lease(
                owner_id, parent_run_id, new_holder, new_token,
            )
        finally:
            if engine is not None:
                await engine.dispose()
            async with bootstrap.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await bootstrap.dispose()

    asyncio.run(verify())

def test_malformed_checkpoint_is_preserved_and_blocks_resume_before_dispatch() -> None:
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
        schema = f"checkpoint_guard_{uuid4().hex}"
        owner_id = f"checkpoint-guard-{uuid4()}"
        parent_run_id = uuid4()
        node = DelegationSpec(
            node_id="child",
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
            tool_scope=("list_files", "read_file"),
            limits={},
        )
        plan = WorkflowPlan(
            parent_run_id,
            digest({"nodes": [node.canonical_digest()]}),
            1,
            (node,),
            WorkflowLimits(),
        )
        bootstrap = create_async_engine(async_url, pool_size=1)
        engine = None
        try:
            async with bootstrap.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f"""
                    CREATE TABLE "{schema}".orchestration_leases (
                        owner_id text PRIMARY KEY,
                        parent_run_id uuid NOT NULL,
                        holder_id uuid NOT NULL,
                        fencing_token bigint NOT NULL,
                        expires_at timestamptz NOT NULL,
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE(owner_id, parent_run_id)
                    )
                """))
                await connection.execute(text(f"""
                    CREATE TABLE "{schema}".orchestration_checkpoints (
                        owner_id text NOT NULL,
                        parent_run_id uuid NOT NULL,
                        version integer NOT NULL,
                        state jsonb NOT NULL,
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY(owner_id, parent_run_id)
                    )
                """))

            engine = create_async_engine(
                async_url,
                pool_size=4,
                connect_args={"server_settings": {"search_path": schema}},
            )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            storage = PostgresOrchestrationRepository(sessions)
            async with sessions.begin() as session:
                await session.execute(text("""
                    INSERT INTO orchestration_checkpoints(
                        owner_id, parent_run_id, version, state
                    ) VALUES (:owner, :parent, 7, '[]'::jsonb)
                """), {"owner": owner_id, "parent": parent_run_id})

            class Repository:
                async def get_plan(self, _owner_id, _parent_run_id):
                    return plan

                async def node_statuses(self, _owner_id, _parent_run_id):
                    return {"child": "approved"}

                async def get_checkpoint(self, *args):
                    return await storage.get_checkpoint(*args)

                async def acquire_lease(self, *args, **kwargs):
                    return await storage.acquire_lease(*args, **kwargs)

                async def renew_lease(self, *args, **kwargs):
                    return await storage.renew_lease(*args, **kwargs)

                async def release_lease(self, *args, **kwargs):
                    return await storage.release_lease(*args, **kwargs)

            controller = WorkflowController(
                Repository(), None, None, None, None, None,  # type: ignore[arg-type]
            )

            def fail_if_graph_runs():
                pytest.fail("a malformed checkpoint must not construct or execute the graph")

            async def fail_if_approvals_run(*_args, **_kwargs):
                pytest.fail("a malformed checkpoint must not create or inspect approvals")

            controller.graph = fail_if_graph_runs  # type: ignore[method-assign]
            controller._ensure_pending_approvals = fail_if_approvals_run  # type: ignore[method-assign]
            assert await controller.resume(owner_id, parent_run_id) == {"status": "blocked"}
            async with sessions() as session:
                checkpoint = (
                    await session.execute(text("""
                        SELECT version, state FROM orchestration_checkpoints
                        WHERE owner_id=:owner AND parent_run_id=:parent
                    """), {"owner": owner_id, "parent": parent_run_id})
                ).mappings().one()
                lease_count = await session.execute(text("""
                    SELECT count(*) FROM orchestration_leases WHERE owner_id=:owner
                """), {"owner": owner_id})
                assert checkpoint["version"] == 7
                assert checkpoint["state"] == []
                assert lease_count.scalar_one() == 0
        finally:
            if engine is not None:
                await engine.dispose()
            async with bootstrap.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await bootstrap.dispose()

    asyncio.run(verify())

def test_stale_controller_cannot_mutate_during_concurrent_takeover() -> None:
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
        schema = f"controller_race_{uuid4().hex}"
        owner_id = f"controller-race-{uuid4()}"
        parent_run_id = uuid4()
        node = DelegationSpec(
            node_id="child",
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
            tool_scope=("list_files", "read_file"),
            limits={},
        )
        plan = WorkflowPlan(
            parent_run_id,
            digest({"nodes": [node.canonical_digest()]}),
            1,
            (node,),
            WorkflowLimits(),
        )
        checkpoint = {
            "plan_digest": plan.plan_digest,
            "plan_revision": plan.revision,
            "active_children": 0,
            "spent_child_runs": 0,
            "status": "waiting_approval",
            "status_sequence": 0,
            "last_emitted_status": "waiting_approval",
        }
        bootstrap = create_async_engine(async_url, pool_size=1)
        engine = None
        old_task = replacement_task = None
        stale_policy_ready = asyncio.Event()
        replacement_policy_ready = asyncio.Event()
        release_stale_policy = asyncio.Event()
        release_replacement_policy = asyncio.Event()
        try:
            async with bootstrap.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                for table in (
                    """
                    CREATE TABLE "{schema}".orchestration_leases (
                        owner_id text PRIMARY KEY,
                        parent_run_id uuid NOT NULL,
                        holder_id uuid NOT NULL,
                        fencing_token bigint NOT NULL,
                        expires_at timestamptz NOT NULL,
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE(owner_id, parent_run_id)
                    )
                    """,
                    """
                    CREATE TABLE "{schema}".orchestration_checkpoints (
                        owner_id text NOT NULL,
                        parent_run_id uuid NOT NULL,
                        version integer NOT NULL,
                        state jsonb NOT NULL,
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY(owner_id, parent_run_id)
                    )
                    """,
                    """
                    CREATE TABLE "{schema}".orchestration_delegations (
                        owner_id text NOT NULL,
                        parent_run_id uuid NOT NULL,
                        node_id text NOT NULL,
                        status text NOT NULL,
                        approval_id uuid,
                        child_run_id uuid,
                        output jsonb,
                        output_schema jsonb NOT NULL,
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY(owner_id, parent_run_id, node_id)
                    )
                    """,
                    """
                    CREATE TABLE "{schema}".approval_decisions (
                        approval_id uuid NOT NULL,
                        owner_id text NOT NULL,
                        decision text NOT NULL
                    )
                    """,
                    """
                    CREATE TABLE "{schema}".run_dispatches (
                        owner_id text NOT NULL,
                        run_id uuid NOT NULL,
                        state text NOT NULL
                    )
                    """,
                    """
                    CREATE TABLE "{schema}".run_results (
                        owner_id text NOT NULL,
                        run_id uuid NOT NULL,
                        result_text text
                    )
                    """,
                ):
                    await connection.execute(text(table.format(schema=schema)))

            engine = create_async_engine(
                async_url,
                pool_size=6,
                connect_args={"server_settings": {"search_path": schema}},
            )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            repository = PostgresOrchestrationRepository(sessions)
            async with sessions.begin() as session:
                await session.execute(text("""
                    INSERT INTO orchestration_checkpoints(
                        owner_id, parent_run_id, version, state
                    ) VALUES (:owner, :parent, 1, CAST(:state AS jsonb))
                """), {
                    "owner": owner_id,
                    "parent": parent_run_id,
                    "state": json.dumps(checkpoint),
                })
                await session.execute(text("""
                    INSERT INTO orchestration_delegations(
                        owner_id, parent_run_id, node_id, status, output_schema
                    ) VALUES (:owner, :parent, 'child', 'approved', '{"type":"object"}')
                """), {"owner": owner_id, "parent": parent_run_id})

            class GatedExecution:
                def __init__(self, entered, release):
                    self.entered = entered
                    self.release = release

                async def get_policy_for_project(self, *_args):
                    self.entered.set()
                    await self.release.wait()
                    return None

            class Runs:
                async def create_for_task(self, *_args, **_kwargs):
                    pytest.fail("disabled test policy must not create a child Run")

                async def get_for_owner(self, *_args):
                    return None

            class Results:
                async def create_for_run(self, *_args, **_kwargs):
                    return None

            stale_controller = WorkflowController(
                repository, Runs(), GatedExecution(stale_policy_ready, release_stale_policy),
                None, None, Results(),
            )
            replacement_controller = WorkflowController(
                repository, Runs(),
                GatedExecution(replacement_policy_ready, release_replacement_policy),
                None, None, Results(),
            )
            statuses = {"child": "approved"}
            old_task = asyncio.create_task(
                stale_controller.run_once(owner_id, parent_run_id, plan, statuses),
            )
            await asyncio.wait_for(stale_policy_ready.wait(), timeout=5)
            async with sessions.begin() as session:
                await session.execute(text("""
                    UPDATE orchestration_leases
                    SET expires_at=now()-interval '1 second'
                    WHERE owner_id=:owner
                """), {"owner": owner_id})
            replacement_task = asyncio.create_task(
                replacement_controller.run_once(owner_id, parent_run_id, plan, statuses),
            )
            await asyncio.wait_for(replacement_policy_ready.wait(), timeout=5)
            release_stale_policy.set()
            with pytest.raises(RuntimeError, match="workflow lease lost"):
                await asyncio.wait_for(old_task, timeout=5)
            async with sessions() as session:
                delegation = await session.execute(text("""
                    SELECT status FROM orchestration_delegations
                    WHERE owner_id=:owner AND parent_run_id=:parent AND node_id='child'
                """), {"owner": owner_id, "parent": parent_run_id})
                active_lease = (
                    await session.execute(text("""
                        SELECT holder_id, fencing_token FROM orchestration_leases
                        WHERE owner_id=:owner
                    """), {"owner": owner_id})
                ).mappings().one()
                assert delegation.scalar_one() == "approved"
                assert active_lease["fencing_token"] == 2
            release_replacement_policy.set()
            replacement_result = await asyncio.wait_for(replacement_task, timeout=5)
            assert replacement_result["status"] == "failed"
            assert replacement_result["statuses"]["child"] == "blocked"
            async with sessions() as session:
                delegation = await session.execute(text("""
                    SELECT status FROM orchestration_delegations
                    WHERE owner_id=:owner AND parent_run_id=:parent AND node_id='child'
                """), {"owner": owner_id, "parent": parent_run_id})
                checkpoint_row = await session.execute(text("""
                    SELECT count(*) FROM orchestration_checkpoints
                    WHERE owner_id=:owner AND parent_run_id=:parent
                """), {"owner": owner_id, "parent": parent_run_id})
                assert delegation.scalar_one() == "blocked"
                assert checkpoint_row.scalar_one() == 0
        finally:
            release_stale_policy.set()
            release_replacement_policy.set()
            if old_task is not None or replacement_task is not None:
                await asyncio.gather(
                    *(task for task in (old_task, replacement_task) if task is not None),
                    return_exceptions=True,
                )
            if engine is not None:
                await engine.dispose()
            async with bootstrap.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await bootstrap.dispose()

    asyncio.run(verify())
def test_production_app_lifespans_fence_stale_controller_takeover(monkeypatch) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    if not unquote(urlsplit(database_url).path.lstrip("/")).endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable database ending in _test")

    database_name = f"workflow_api_race_{uuid4().hex}_test"
    isolated_url = make_url(database_url).set(database=database_name).render_as_string(
        hide_password=False,
    )
    isolated_dsn = isolated_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    admin_dsn = make_url(database_url).set(drivername="postgresql").render_as_string(
        hide_password=False,
    )
    app_role_name = f"agent_office_reconcile_{uuid4().hex}"
    app_role_password = secrets.token_urlsafe(32)

    class FakeRedis:
        async def aclose(self) -> None:
            return None

    async def no_background_work(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(api_main.Redis, "from_url", lambda *_args, **_kwargs: FakeRedis())
    monkeypatch.setattr(api_main, "consume_stream", no_background_work)
    monkeypatch.setattr(api_main, "publish_outbox", no_background_work)
    monkeypatch.setattr(api_main, "controller_loop", no_background_work)

    async def verify() -> None:
        admin = await asyncpg.connect(dsn=admin_dsn, timeout=5)
        database_created = False
        try:
            await admin.execute(f'CREATE DATABASE "{database_name}"')
            database_created = True
            migration_connection = await asyncpg.connect(dsn=isolated_dsn, timeout=5)
            try:
                await apply_migrations(migration_connection)
            finally:
                await migration_connection.close()
            await apply_alembic_migrations(isolated_url)
            role_connection = await asyncpg.connect(dsn=isolated_dsn, timeout=5)
            try:
                await ensure_application_role(
                    role_connection, app_role_name, app_role_password,
                )
                assert await role_connection.fetchval(
                    "SELECT has_table_privilege($1, 'public.workflow_reconciliation_audits', 'SELECT')",
                    app_role_name,
                )
                assert await role_connection.fetchval(
                    "SELECT has_table_privilege($1, 'public.workflow_reconciliation_audits', 'INSERT')",
                    app_role_name,
                )
                for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                    assert not await role_connection.fetchval(
                        "SELECT has_table_privilege($1, 'public.workflow_reconciliation_audits', $2)",
                        app_role_name, privilege,
                    )
            finally:
                await role_connection.close()
            app_database_url = make_url(isolated_url).set(
                username=app_role_name, password=app_role_password,
            ).render_as_string(hide_password=False)

            app_settings = replace(
                settings(),
                database_url=app_database_url,
                redis_url="redis://127.0.0.1:1/0",
            )
            first_app = create_app(app_settings, database_check=database_ready)
            second_app = create_app(app_settings, database_check=database_ready)
            stale_policy_ready = asyncio.Event()
            replacement_policy_ready = asyncio.Event()
            release_stale_policy = asyncio.Event()
            release_replacement_policy = asyncio.Event()
            stale_task = replacement_task = None
            try:
                async with first_app.router.lifespan_context(first_app):
                    async with second_app.router.lifespan_context(second_app):
                        assert (
                            first_app.state.workflow_controller.repository
                            is first_app.state.orchestration_repository
                        )
                        assert (
                            second_app.state.workflow_controller.repository
                            is second_app.state.orchestration_repository
                        )
                        owner_id = "owner"
                        project = await first_app.state.project_repository.create(
                            owner_id, "takeover test", "Isolated API wiring test.",
                        )
                        endpoint = await first_app.state.model_endpoint_repository.create(
                            owner_id,
                            ModelEndpointDraft.create(
                                "Takeover test endpoint",
                                "http://localhost:20128/v1",
                                "NINEROUTER_KEY",
                                "openrouter/cohere/north-mini-code:free",
                                "free",
                            ),
                        )
                        configuration = AgentDefinitionConfiguration.create(
                            role="Takeover test worker",
                            model_endpoint_id=endpoint.id,
                            model_id=endpoint.default_model_id,
                            route=endpoint.default_route,
                            allowed_tools=["list_files", "read_file"],
                        )
                        definition = await first_app.state.agent_registry_repository.create(
                            owner_id,
                            f"takeover-{uuid4().hex}",
                            "",
                            "# Takeover test worker",
                            {},
                            configuration,
                        )
                        instance = await first_app.state.agent_instance_repository.create(
                            owner_id, "Takeover test instance", definition.id, project.id,
                        )
                        parent_spec = TaskSpec(
                            project_id=project.id,
                            agent_instance_id=instance.id,
                            title="Parent workflow task",
                            goal="Exercise isolated controller takeover.",
                            input_text="",
                            resource_refs=(),
                            acceptance_criteria=("Controller recovery is fenced.",),
                            blocked_by_task_ids=(),
                            max_duration_minutes=60,
                            max_child_runs=8,
                        )
                        parent_task = await first_app.state.task_repository.create(
                            owner_id, parent_spec,
                        )
                        parent_run, created = await first_app.state.run_repository.create_for_task(
                            owner_id, parent_task.id, uuid4(),
                        )
                        assert created

                        child_task_id = uuid4()
                        child_spec = TaskSpec(
                            project_id=project.id,
                            agent_instance_id=instance.id,
                            title="Delegated child task",
                            goal="Remain unstarted when the stale controller resumes.",
                            input_text="",
                            resource_refs=(),
                            acceptance_criteria=("No stale child Run is created.",),
                            blocked_by_task_ids=(),
                            max_duration_minutes=60,
                            max_child_runs=8,
                            output_schema={"type": "object"},
                            tool_scope=("list_files", "read_file"),
                        )
                        node = DelegationSpec(
                            node_id="child",
                            child_task_id=child_task_id,
                            child_revision=1,
                            definition_id=definition.id,
                            definition_version=definition.version,
                            instance_id=instance.id,
                            project_id=project.id,
                            workspace_path="workspace",
                            input_payload={},
                            resource_refs=(),
                            dependencies=(),
                            output_schema={"type": "object"},
                            model_scope={
                                "model_endpoint_id": str(endpoint.id),
                                "model_id": endpoint.default_model_id,
                                "route": endpoint.default_route,
                                "adapter": endpoint.adapter,
                                "endpoint_url": endpoint.endpoint_url,
                                "credential_env": endpoint.credential_env,
                            },
                            tool_scope=("list_files", "read_file"),
                            limits={"max_child_runs": 8, "max_duration_minutes": 60},
                        )
                        plan = WorkflowPlan(
                            parent_run.id,
                            digest({"nodes": [node.canonical_digest()]}),
                            1,
                            (node,),
                            WorkflowLimits(),
                        )
                        await first_app.state.orchestration_repository.persist_plan_with_tasks(
                            owner_id, plan, {child_task_id: child_spec},
                        )
                        approval, created = await first_app.state.approval_repository.create_request(
                            ApprovalRequestDraft(
                                owner_id=owner_id,
                                run_id=parent_run.id,
                                request_id=uuid5(
                                    NAMESPACE_URL,
                                    f"agent-office:workflow-approval:{parent_run.id}:child",
                                ),
                                action_type="workflow.delegation",
                                action_digest=node.canonical_digest(),
                                resource_ref=f"workflow:{parent_run.id}:child",
                                expires_at=datetime.now(UTC) + timedelta(days=7),
                            )
                        )
                        assert created
                        await first_app.state.orchestration_repository.bind_approval(
                            owner_id, parent_run.id, node.node_id, approval.id,
                        )
                        resolved = await first_app.state.approval_repository.resolve(
                            owner_id, approval.id, node.canonical_digest(),
                            "approved", datetime.now(UTC),
                        )
                        assert resolved.decision == "approved"
                        policy = await first_app.state.execution_repository.save_policy(
                            owner_id,
                            project.id,
                            WorkspacePolicyDraft.create(
                                workspace_path="workspace",
                                allowed_tools=("list_files", "read_file"),
                                max_duration_seconds=3600,
                                max_inference_turns=1,
                                max_tool_calls=1,
                            ),
                        )
                        assert policy is not None

                        def gate_policy(entered, release, get_policy):
                            async def lookup(*args):
                                entered.set()
                                await release.wait()
                                return await get_policy(*args)
                            return lookup

                        stale_policy_lookup = first_app.state.execution_repository.get_policy_for_project
                        replacement_policy_lookup = second_app.state.execution_repository.get_policy_for_project
                        first_app.state.execution_repository.get_policy_for_project = (
                            gate_policy(stale_policy_ready, release_stale_policy, stale_policy_lookup)
                        )
                        second_app.state.execution_repository.get_policy_for_project = (
                            gate_policy(replacement_policy_ready, release_replacement_policy, replacement_policy_lookup)
                        )
                        statuses = {"child": "approved"}
                        stale_task = asyncio.create_task(
                            first_app.state.workflow_controller.run_once(
                                owner_id, parent_run.id, plan, statuses,
                            ),
                        )
                        await asyncio.wait_for(stale_policy_ready.wait(), timeout=10)
                        async with first_app.state.session_factory.begin() as session:
                            await session.execute(text("""
                                UPDATE orchestration_leases
                                SET expires_at=now()-interval '1 second'
                                WHERE owner_id=:owner AND parent_run_id=:parent
                            """), {"owner": owner_id, "parent": parent_run.id})
                        replacement_task = asyncio.create_task(
                            second_app.state.workflow_controller.run_once(
                                owner_id, parent_run.id, plan, statuses,
                            ),
                        )
                        await asyncio.wait_for(replacement_policy_ready.wait(), timeout=10)
                        async with first_app.state.session_factory() as session:
                            token = await session.execute(text("""
                                SELECT fencing_token FROM orchestration_leases
                                WHERE owner_id=:owner AND parent_run_id=:parent
                            """), {"owner": owner_id, "parent": parent_run.id})
                            assert token.scalar_one() == 2

                        release_stale_policy.set()
                        with pytest.raises(RuntimeError, match="workflow lease lost"):
                            await asyncio.wait_for(stale_task, timeout=10)
                        async with first_app.state.session_factory() as session:
                            status = await session.execute(text("""
                                SELECT status FROM orchestration_delegations
                                WHERE owner_id=:owner AND parent_run_id=:parent AND node_id='child'
                            """), {"owner": owner_id, "parent": parent_run.id})
                            child_run_count = await session.execute(text("""
                                SELECT count(*) FROM runs WHERE owner_id=:owner AND task_id=:task
                            """), {"owner": owner_id, "task": child_task_id})
                            assert status.scalar_one() == "approved"
                            assert child_run_count.scalar_one() == 0
                        release_replacement_policy.set()
                        result = await asyncio.wait_for(replacement_task, timeout=10)
                        assert result["status"] == "waiting_children"
                        assert result["statuses"]["child"] == "queued"
                        async with second_app.state.session_factory() as session:
                            checkpoint_count = await session.execute(text("""
                                SELECT count(*) FROM orchestration_checkpoints
                                WHERE owner_id=:owner AND parent_run_id=:parent
                            """), {"owner": owner_id, "parent": parent_run.id})
                            child_dispatch = await session.execute(text("""
                                SELECT child_run.id, child_run.status AS run_status,
                                       dispatch.state AS dispatch_state,
                                       delegation.status AS delegation_status,
                                       delegation.child_run_id
                                FROM runs AS child_run
                                JOIN run_dispatches AS dispatch
                                  ON dispatch.owner_id=child_run.owner_id
                                 AND dispatch.run_id=child_run.id
                                JOIN orchestration_delegations AS delegation
                                  ON delegation.owner_id=child_run.owner_id
                                 AND delegation.child_task_id=child_run.task_id
                                WHERE child_run.owner_id=:owner AND child_run.task_id=:task
                            """), {"owner": owner_id, "task": child_task_id})
                            dispatch = child_dispatch.mappings().one()
                            assert checkpoint_count.scalar_one() == 1
                            assert dispatch["run_status"] == "created"
                            assert dispatch["dispatch_state"] == "queued"
                            assert dispatch["delegation_status"] == "queued"
                            assert dispatch["child_run_id"] == dispatch["id"]
                        corrupt_checkpoint = {
                            "plan_digest": "invalid",
                            "plan_revision": -1,
                            "status": "waiting_children",
                            "status_sequence": -1,
                        }
                        async with second_app.state.session_factory() as session:
                            await session.execute(text("""
                                UPDATE orchestration_checkpoints
                                SET state=CAST(:state AS jsonb)
                                WHERE owner_id=:owner AND parent_run_id=:parent
                            """), {
                                "state": json.dumps(corrupt_checkpoint),
                                "owner": owner_id,
                                "parent": parent_run.id,
                            })
                            await session.commit()

                        route = f"/api/v1/runs/{parent_run.id}/workflow/reconcile"
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=second_app),
                            base_url="http://testserver",
                        ) as client:
                            request_headers = {"origin": ORIGIN}
                            request_body = {"confirm_abandon": True}
                            assert (
                                await client.post(
                                    route, json=request_body, headers=request_headers,
                                )
                            ).status_code == 401
                            login = await client.post(
                                "/api/v1/auth/login",
                                json={"username": "owner", "password": TEST_PASSWORD},
                                headers=request_headers,
                            )
                            assert login.status_code == 200
                            active_child = await client.post(
                                route, json=request_body, headers=request_headers,
                            )
                            assert active_child.status_code == 409
                            async with second_app.state.session_factory() as session:
                                unchanged = await session.execute(text("""
                                    SELECT state FROM orchestration_checkpoints
                                    WHERE owner_id=:owner AND parent_run_id=:parent
                                """), {"owner": owner_id, "parent": parent_run.id})
                                assert unchanged.scalar_one() == corrupt_checkpoint
                            assert await second_app.state.execution_repository.cancel_queued_run(
                                owner_id, dispatch["id"],
                            )
                            first_result = await client.post(
                                route, json=request_body, headers=request_headers,
                            )
                            assert first_result.status_code == 200
                            reconciled = first_result.json()
                            assert reconciled["status"] == "unknown"
                            replay = await client.post(
                                route, json=request_body, headers=request_headers,
                            )
                            assert replay.status_code == 200
                            assert replay.json() == reconciled

                        async with second_app.state.session_factory() as session:
                            audit = await session.execute(text("""
                                SELECT actor_owner_id, outcome, checkpoint_snapshot,
                                       delegations_snapshot, event_cursor
                                FROM workflow_reconciliation_audits
                                WHERE owner_id=:owner AND parent_run_id=:parent
                            """), {"owner": owner_id, "parent": parent_run.id})
                            audit_row = audit.mappings().one()
                            assert audit_row["actor_owner_id"] == owner_id
                            assert audit_row["outcome"] == "abandoned_unknown"
                            assert audit_row["checkpoint_snapshot"] == corrupt_checkpoint
                            assert audit_row["delegations_snapshot"][0]["status"] == "queued"
                            assert audit_row["event_cursor"] == reconciled["event_cursor"]
                            checkpoint_count = await session.execute(text("""
                                SELECT count(*) FROM orchestration_checkpoints
                                WHERE owner_id=:owner AND parent_run_id=:parent
                            """), {"owner": owner_id, "parent": parent_run.id})
                            delegation_status = await session.execute(text("""
                                SELECT status FROM orchestration_delegations
                                WHERE owner_id=:owner AND parent_run_id=:parent
                            """), {"owner": owner_id, "parent": parent_run.id})
                            projection_status = await session.execute(text("""
                                SELECT status FROM run_activity_projections
                                WHERE owner_id=:owner AND run_id=:parent
                            """), {"owner": owner_id, "parent": parent_run.id})
                            event_count = await session.execute(text("""
                                SELECT count(*) FROM activity_events
                                WHERE owner_id=:owner AND run_id=:parent
                                  AND event_type='run.unknown'
                            """), {"owner": owner_id, "parent": parent_run.id})
                            assert checkpoint_count.scalar_one() == 0
                            assert delegation_status.scalar_one() == "unknown"
                            assert projection_status.scalar_one() == "unknown"
                            assert event_count.scalar_one() == 1
                        active_parents = await second_app.state.orchestration_repository.active_parent_runs()
                        assert (owner_id, parent_run.id) not in active_parents
            finally:
                release_stale_policy.set()
                release_replacement_policy.set()
                if stale_task is not None or replacement_task is not None:
                    await asyncio.gather(
                        *(task for task in (stale_task, replacement_task) if task is not None),
                        return_exceptions=True,
                    )
        finally:
            if database_created:
                await admin.execute(f'DROP DATABASE "{database_name}" WITH (FORCE)')
            if await admin.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=$1)",
                app_role_name,
            ):
                drop_owned = await admin.fetchval(
                    "SELECT format('DROP OWNED BY %I', $1::text)",
                    app_role_name,
                )
                await admin.execute(drop_owned)
                drop_role = await admin.fetchval(
                    "SELECT format('DROP ROLE %I', $1::text)",
                    app_role_name,
                )
                await admin.execute(drop_role)
            await admin.close()

    asyncio.run(verify())
