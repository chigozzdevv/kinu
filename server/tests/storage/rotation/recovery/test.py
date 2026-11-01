from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from connectors.base.errors import ConnectorError
from connectors.hosting.models import Deployment, Variable
from connectors.hosting.render import Render
from contracts import CreateRunCommand, RecoveryMode, RunStatus, Trigger
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError, StorageUnavailableError
from core.storage.database import Database
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from hosting.proof import Proof
from jobs.queue import Jobs
from rotation.hosting import Effects
from rotation.recovery import Recovery
from rotation.rotate import Rotation
from rotation.snapshot import loader
from tests.storage.hosting.rotation.fixture import NEW, OLD, Platform
from tests.storage.hosting.rotation.test import setup

pytestmark = pytest.mark.anyio


async def create(database: Database, org: str, credential: str, controls: str, actor: str) -> str:
    return (
        await RunWorkflow(RunStore(database)).create(
            CreateRunCommand(
                id="command_host_recovery",
                organisation_id=org,
                credential_id=credential,
                control_version=controls,
                trigger=Trigger(
                    source="manual",
                    kind="manual",
                    event_id="host_recovery",
                    actor_id=actor,
                    reason="Verify hosting recovery",
                    urgency="routine",
                    received_at=datetime.now(UTC),
                ),
            )
        )
    ).run.id


@pytest.mark.parametrize("retired", [False, True])
async def test_operator_reconciliation_preserves_unknown_work_and_releases_only_verified_outcomes(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
    retired: bool,
) -> None:
    platform = Platform(None if retired else "pending")
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        run = (
            await RunWorkflow(RunStore(database)).create(
                CreateRunCommand(
                    id="command_recovery",
                    organisation_id=org,
                    credential_id=credential.id,
                    control_version=credential.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="recovery_fixture",
                        actor_id=identity.actor_id,
                        reason="Recovery check",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run

        async def sleep(seconds: float) -> None:
            assert seconds == 20

        async def pending(*args: object) -> None:
            raise ConnectorError("host-deployment", "Deployment remains pending")

        async def unavailable(*args: object) -> None:
            raise ResourceConflictError("Registration interrupted")

        monkeypatch.setattr("rotation.hosting.sleep", sleep)
        if retired:
            monkeypatch.setattr("rotation.stages.generations.promote", unavailable)
        else:
            monkeypatch.setattr("hosting.rollout.Rollout.wait", pending)
        rotation = Rotation(
            database, Effects(hosts, versions, provider, Proof(http)), loader(database), "global"
        )
        held = await rotation.execute(org, run.id, "worker_one")
        assert held.status is RunStatus.CLEANUP
        queue = Mock(spec=Jobs)
        queue.add = AsyncMock()
        recovery = Recovery(database, rotation, queue, provider)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(
                RunWorkflow,
                "recover",
                AsyncMock(side_effect=StorageUnavailableError("Interrupted")),
            )
            with pytest.raises(StorageUnavailableError):
                await recovery.request(org, run.id, held.revision, identity.actor_id, "recover_one")
        requested = await recovery.request(
            org, run.id, held.revision, identity.actor_id, "recover_one"
        )
        assert requested.status is RunStatus.RECOVERING
        repeated = await recovery.request(
            org, run.id, held.revision, identity.actor_id, "recover_one"
        )
        assert repeated == requested
        if retired:
            platform.keys["old"]["active"] = True
            still_held = await recovery.execute(org, run.id)
            assert still_held.status is RunStatus.CLEANUP and platform.revoked == ["old"]
            platform.keys["old"]["active"] = False
            requested = await recovery.request(
                org, run.id, still_held.revision, identity.actor_id, "recover_two"
            )
        else:
            still_held = await recovery.execute(org, run.id)
            assert still_held.status is RunStatus.CLEANUP and not platform.revoked
            platform.failure = None
            platform.current["project-one"] = "project-one-new"
            platform.loaded["project-one"] = NEW
            requested = await recovery.request(
                org, run.id, still_held.revision, identity.actor_id, "recover_two"
            )
        monkeypatch.undo()
        result = await recovery.execute(org, run.id)
        assert result.status is RunStatus.COMPENSATED, result.model_dump_json()
        assert result.recovery_mode is (
            RecoveryMode.ROLLFORWARD if retired else RecoveryMode.ROLLBACK
        )
        assert platform.revoked == (["old"] if retired else ["new"])
        assert not await database.collection("run_locks").find_one(
            {"organisation_id": org, "credential_id": credential.id}
        )


async def test_reconciliation_recognizes_new_rollback_deployment_ids_without_repeating_restore(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = Platform("proof")
    restorations: list[str] = []

    async def restore(adapter: Render, variable: Variable, identity: str) -> Deployment:
        assert identity == variable.project_id + "-old"
        restored = variable.project_id + "-restored"
        platform.current[variable.project_id] = restored
        platform.loaded[variable.project_id] = OLD
        platform.deployments[restored] = OLD
        restorations.append(restored)
        return Deployment(id=restored, state="ready")

    async def sleep(seconds: float) -> None:
        assert seconds == 20

    monkeypatch.setattr(Render, "restore", restore)
    monkeypatch.setattr("rotation.hosting.sleep", sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        run_id = await create(
            database, org, credential.id, credential.control_version, identity.actor_id
        )
        rotation = Rotation(
            database, Effects(hosts, versions, provider, Proof(http)), loader(database), "global"
        )
        original = provider.revoke_credential
        monkeypatch.setattr(
            provider,
            "revoke_credential",
            AsyncMock(side_effect=ConnectorError("temporary", "Retry")),
        )
        held = await rotation.execute(org, run_id, "worker_one")
        assert held.status is RunStatus.CLEANUP and len(restorations) == 2
        monkeypatch.setattr(provider, "revoke_credential", original)
        jobs = Mock(spec=Jobs)
        jobs.add = AsyncMock()
        recovery = Recovery(database, rotation, jobs, provider)
        await recovery.request(org, run_id, held.revision, identity.actor_id, "restore_retry")
        result = await recovery.execute(org, run_id)
        assert result.status is RunStatus.COMPENSATED, result.failure
        assert len(restorations) == 2 and platform.revoked == ["new"]


async def test_late_variable_write_cannot_lose_its_key_before_its_outcome_is_proven(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    platform = Platform()
    pending_project: str | None = None

    def response(request: httpx.Request) -> httpx.Response:
        nonlocal pending_project
        if (
            request.url.host == "api.render.com"
            and request.method == "PUT"
            and NEW.encode() in request.content
        ):
            pending_project = request.url.path.split("/")[3]
            raise httpx.ReadTimeout("Variable write outcome is unknown")
        return platform.response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        run_id = await create(
            database, org, credential.id, credential.control_version, identity.actor_id
        )
        rotation = Rotation(
            database, Effects(hosts, versions, provider, Proof(http)), loader(database), "global"
        )
        held = await rotation.execute(org, run_id, "worker_one")
        assert held.status is RunStatus.CLEANUP and not platform.revoked and not platform.writes
        jobs = Mock(spec=Jobs)
        jobs.add = AsyncMock()
        recovery = Recovery(database, rotation, jobs, provider)
        await recovery.request(org, run_id, held.revision, identity.actor_id, "write_pending")
        held = await recovery.execute(org, run_id)
        assert held.status is RunStatus.CLEANUP and not platform.revoked and not platform.writes
        assert pending_project is not None
        platform.variables[pending_project] = NEW
        await recovery.request(org, run_id, held.revision, identity.actor_id, "write_confirmed")
        result = await recovery.execute(org, run_id)
        assert result.status is RunStatus.COMPENSATED, result.failure
        assert platform.variables[pending_project] == OLD and platform.revoked == ["new"]
