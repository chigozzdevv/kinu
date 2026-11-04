import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from contracts import (
    ApprovalDecision,
    CancelRunCommand,
    Confidence,
    CreateRunCommand,
    Failure,
    Incident,
    IncidentStatus,
    RotationRun,
    RunStatus,
    Severity,
    SourceResource,
    Stage,
    StartRunCommand,
    Trigger,
)
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AccessControl, AuthenticatedIdentity
from core.errors import (
    ActiveRunConflictError,
    IdempotencyConflictError,
    ResourceConflictError,
    RunNotFoundError,
)
from core.incident.store import IncidentStore
from core.overview.store import OverviewStore
from core.storage.database import Database
from core.storage.incidents import sync_incidents
from core.storage.records import Records
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from pymongo.asynchronous.client_session import AsyncClientSession
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 27, tzinfo=UTC)


async def test_current_runs_do_not_resurface_history_but_preserve_unfinished_work(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    _, command = await setup(database, identity)
    org = command.organisation_id

    def run(name: str, key: str, status: RunStatus, minute: int) -> RotationRun:
        return RotationRun(
            id=name,
            organisation_id=org,
            credential_id=key,
            control_version="controls_one",
            trigger=command.trigger,
            status=status,
            stage=Stage.COMPLETE if status is RunStatus.COMPLETED else Stage.CREATE,
            failure=Failure(code="test-failure", message="Stopped", retryable=False)
            if status in {RunStatus.FAILED, RunStatus.CLEANUP}
            else None,
            created_at=NOW + timedelta(minutes=minute),
            updated_at=NOW + timedelta(minutes=minute),
        )

    records = [
        run("old_failed", "key_one", RunStatus.FAILED, 0),
        run("completed", "key_one", RunStatus.COMPLETED, 1),
        run("failed_first", "key_two", RunStatus.FAILED, 2),
        run("failed_latest", "key_two", RunStatus.FAILED, 3),
        run("cleanup", "key_three", RunStatus.CLEANUP, 0),
        run("completed_after_cleanup", "key_three", RunStatus.COMPLETED, 4),
        run("paused", "key_four", RunStatus.PAUSED, 0),
        run("completed_after_pause", "key_four", RunStatus.COMPLETED, 5),
    ]
    await database.collection("runs").insert_many(
        [
            {
                "organisation_id": org,
                "id": item.id,
                "credential_id": item.credential_id,
                "revision": item.revision,
                "data": item.model_dump(mode="json"),
            }
            for item in records
        ]
    )
    store = RunStore(database)
    seen: list[str] = []
    before = None
    while page := await store.list_runs(org, 2, current=True, before=before):
        seen.extend(item.id for item in page)
        before = page[-1].id
    assert len(seen) == len(set(seen)) == 6
    assert set(seen) == {item.id for item in records} - {"old_failed", "failed_first"}
    assert [
        item.id
        for item in await store.list_runs(
            org, 100, current=True, statuses=frozenset({RunStatus.FAILED})
        )
    ] == ["failed_latest"]
    assert len(await store.list_runs(org, 100)) == 8
    assert await store.list_runs("org_other", 100, current=True) == ()
    overview = OverviewStore(database)
    assert await overview.count_runs(org, frozenset({RunStatus.FAILED})) == 1
    assert await overview.count_runs(org, frozenset({RunStatus.CLEANUP, RunStatus.PAUSED})) == 2
    app = create_app(
        ApiServices(
            workflow=RunWorkflow(store), access=AccessControl(AccountStore(database, lambda: NOW))
        ),
        browser=make_browser(identity),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        current_response = await client.get(
            f"/v1/organisations/{org}/runs?current=true&status=failed", headers=HEADERS
        )
        history_response = await client.get(
            f"/v1/organisations/{org}/runs?status=failed", headers=HEADERS
        )
    assert current_response.status_code == history_response.status_code == 200
    assert [item["id"] for item in current_response.json()] == ["failed_latest"]
    assert len(history_response.json()) == 3


async def test_pending_approval_counts_only_include_current_matching_requests(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    _, command = await setup(database, identity)
    org = command.organisation_id
    variants = [
        "valid",
        "completed",
        "failed",
        "wrong_plan",
        "wrong_generation",
        "old_controls",
        "archived",
        "expired",
        "consumed",
    ]
    for name in variants:
        key = f"key_{name}"
        run_id = f"run_{name}"
        await database.collection("runs").insert_one(
            {
                "id": run_id,
                "organisation_id": org,
                "credential_id": key,
                "data": {
                    "status": name if name in {"completed", "failed"} else "paused",
                    "plan_hash": "plan",
                    "current_generation_id": "old",
                    "target_generation_id": "new",
                    "control_version": "controls",
                },
            }
        )
        await database.collection("inventory").insert_many(
            [
                {
                    "id": key,
                    "organisation_id": org,
                    "kind": "credential",
                    "data": {
                        "control_version": "changed" if name == "old_controls" else "controls",
                        "archived_at": NOW.isoformat() if name == "archived" else None,
                    },
                },
                {
                    "id": f"approval_{name}",
                    "organisation_id": org,
                    "kind": "approval",
                    "data": {
                        "run_id": run_id,
                        "decision": "pending",
                        "plan_hash": "wrong" if name == "wrong_plan" else "plan",
                        "generation_id": "wrong" if name == "wrong_generation" else "old",
                        "expires_at": (
                            NOW + timedelta(days=-1 if name == "expired" else 1)
                        ).isoformat(),
                        "consumed_at": NOW.isoformat() if name == "consumed" else None,
                    },
                },
            ]
        )
    overview = OverviewStore(database)
    assert await overview.count_approvals(org, frozenset({ApprovalDecision.PENDING}), NOW) == 1
    assert (
        await overview.count_approvals("org_other", frozenset({ApprovalDecision.PENDING}), NOW) == 0
    )


async def test_run_pages_cover_history_beyond_500_with_stable_ties_and_scoped_cursors(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    workflow, command = await setup(database, identity)
    original = (await workflow.create(command)).run
    records = []
    for number in range(600):
        run = original.model_copy(update={"id": f"run_{number:06d}"})
        records.append(
            {
                "organisation_id": run.organisation_id,
                "id": run.id,
                "credential_id": run.credential_id,
                "revision": run.revision,
                "data": run.model_dump(mode="json"),
            }
        )
    await database.collection("runs").insert_many(records)
    seen: list[str] = []
    before: str | None = None
    while True:
        page = await workflow.list_runs(original.organisation_id, limit=100, before=before)
        if not page:
            break
        seen.extend(run.id for run in page)
        before = page[-1].id
    assert len(seen) == len(set(seen)) == 601
    assert set(seen) == {original.id, *(record["id"] for record in records)}
    with pytest.raises(RunNotFoundError):
        await workflow.list_runs("org_other", before=seen[0])


async def setup(
    database: Database, identity: AuthenticatedIdentity
) -> tuple[RunWorkflow, CreateRunCommand]:
    workspace = await AccountService(
        AccountStore(database, lambda: NOW), lambda: NOW
    ).create_organisation(identity, "Rotation")
    command = CreateRunCommand(
        id="create_one",
        organisation_id=workspace.organisation.id,
        credential_id="credential_one",
        control_version="controls_one",
        trigger=Trigger(
            source="email",
            event_id="message_one",
            actor_id="operator_one",
            reason="Credential exposure alert",
            urgency="high",
            received_at=NOW,
        ),
    )
    return RunWorkflow(RunStore(database), clock=lambda: NOW), command


async def test_concurrent_delivery_deduplicates_and_run_survives_repository_restart(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    workflow, command = await setup(database, identity)
    results = await asyncio.gather(*(workflow.create(command) for _ in range(4)))
    assert sum(result.applied for result in results) == 1
    assert len({result.run.id for result in results}) == 1
    run = results[0].run
    store = RunStore(database)
    assert await store.get(run.organisation_id, run.id) == run
    assert len(await store.steps(run.organisation_id, run.id)) == 1
    with pytest.raises(RunNotFoundError):
        await store.get("org_other", run.id)
    with pytest.raises(IdempotencyConflictError):
        await workflow.create(command.model_copy(update={"credential_id": "credential_other"}))


async def test_credential_lock_blocks_overlap_and_terminal_command_releases_it(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    workflow, command = await setup(database, identity)
    result = await workflow.create(command)
    next_command = command.model_copy(
        update={
            "id": "create_two",
            "trigger": command.trigger.model_copy(update={"event_id": "message_two"}),
        }
    )
    with pytest.raises(ActiveRunConflictError):
        await workflow.create(next_command)
    run = result.run
    started = await workflow.start(
        StartRunCommand(
            id="start_one",
            organisation_id=run.organisation_id,
            run_id=run.id,
            actor_id="operator_one",
            expected_revision=0,
            owner_id="worker_one",
            expires_at=NOW + timedelta(minutes=5),
        )
    )
    cancelled = await workflow.cancel(
        CancelRunCommand(
            id="cancel_one",
            organisation_id=run.organisation_id,
            run_id=run.id,
            actor_id="operator_one",
            expected_revision=started.run.revision,
        )
    )
    assert cancelled.run.status is RunStatus.CANCELLED
    assert (await workflow.create(next_command)).applied
    assert len(await RunStore(database).steps(run.organisation_id, run.id)) == 3


async def test_incident_updates_are_atomic_scoped_and_preserve_closed_alerts(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, command = await setup(database, identity)
    run = (await workflow.create(command)).run
    org = run.organisation_id
    foreign = await AccountService(
        AccountStore(database, lambda: NOW), lambda: NOW
    ).create_organisation(identity, "Other workspace")
    incident = Incident(
        id="incident_run",
        organisation_id=org,
        event_id="event_run",
        source="email",
        source_event_id="message_run",
        severity=Severity.HIGH,
        confidence=Confidence.VERIFIED,
        status=IncidentStatus.ROTATING,
        resource=SourceResource(credential_id=run.credential_id),
        credential_id=run.credential_id,
        run_id=run.id,
        created_at=NOW,
        updated_at=NOW,
    )
    async with Records(database).write(org) as rows:
        await rows.add(incident, org)
        for status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
            await rows.add(
                incident.model_copy(
                    update={
                        "id": "incident_" + status.value,
                        "event_id": "event_" + status.value,
                        "source_event_id": "message_" + status.value,
                        "status": status,
                        "dismissal_reason": "Reviewed"
                        if status is IncidentStatus.DISMISSED
                        else None,
                    }
                ),
                org,
            )
    foreign_org = foreign.organisation.id
    async with Records(database).write(foreign_org) as rows:
        await rows.add(incident.model_copy(update={"organisation_id": foreign_org}), foreign_org)
    started = (
        await workflow.start(
            StartRunCommand(
                id="start_one",
                organisation_id=org,
                run_id=run.id,
                actor_id="operator_one",
                expected_revision=run.revision,
                owner_id="worker_one",
                expires_at=NOW + timedelta(minutes=5),
            )
        )
    ).run
    cancel = CancelRunCommand(
        id="cancel_one",
        organisation_id=org,
        run_id=run.id,
        actor_id="operator_one",
        expected_revision=started.revision,
    )

    async def interrupted(
        database: Database, current: RotationRun, session: AsyncClientSession
    ) -> tuple[Incident, ...]:
        await sync_incidents(database, current, session)
        raise ResourceConflictError("Interrupted after incident update")

    with monkeypatch.context() as patch:
        patch.setattr("core.storage.runs.sync_incidents", interrupted)
        with pytest.raises(ResourceConflictError, match="Interrupted"):
            await workflow.cancel(cancel)
    assert (await RunStore(database).get(org, run.id)).status is RunStatus.RUNNING
    store = IncidentStore(database)
    assert (await store.get(org, incident.id)).status is IncidentStatus.ROTATING
    assert await database.collection("run_locks").count_documents({"organisation_id": org}) == 1
    result = await workflow.cancel(cancel)
    assert result.run.status is RunStatus.CANCELLED
    assert (await store.get(org, incident.id)).status is IncidentStatus.ACTION
    assert (await store.get(foreign_org, incident.id)).status is IncidentStatus.ROTATING
    for status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
        assert (await store.get(org, "incident_" + status.value)).status is status
    assert not (await workflow.cancel(cancel)).applied
    assert await RunStore(database).reconcile_incidents(org, run.id) == ()
