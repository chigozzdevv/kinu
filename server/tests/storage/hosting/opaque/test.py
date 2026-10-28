from datetime import UTC, datetime

import httpx
import pytest
from contracts import CreateRunCommand, RunStatus, Trigger
from core.auth import AuthenticatedIdentity
from core.storage.database import Database
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from hosting.proof import Proof
from rotation.hosting import Effects
from rotation.rotate import Rotation
from rotation.snapshot import loader
from tests.storage.hosting.opaque.fixture import Opaque
from tests.storage.hosting.rotation.test import setup

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("failure", [None, "proof", "identity"])
async def test_opaque_key_requires_identity_and_holds_partial_rollback(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    platform = Opaque(failure)
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http, "vercel")
        org = credential.organisation_id
        store = await hosts.inventory.get_connection(org, credential.secret_store_connection_id)
        assert not await versions.has_value(store, credential.secret_reference)
        run = (
            await RunWorkflow(RunStore(database)).create(
                CreateRunCommand(
                    id="command_opaque",
                    organisation_id=org,
                    credential_id=credential.id,
                    control_version=credential.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="event_opaque",
                        actor_id=identity.actor_id,
                        reason="Opaque secret rotation",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run

        async def sleep(seconds: float) -> None:
            assert seconds == 20

        monkeypatch.setattr("rotation.hosting.sleep", sleep)
        result = await Rotation(
            database, Effects(hosts, versions, provider, Proof(http)), loader(database), "global"
        ).execute(org, run.id, "worker_one")
        if failure == "identity":
            assert result.status is RunStatus.FAILED and "new" not in platform.keys
        elif failure:
            assert result.status is RunStatus.CLEANUP and not platform.revoked
            assert platform.keys["new"]["active"] and platform.keys["old"]["active"]
            assert all(value.endswith("-old") for value in platform.current.values())
        else:
            assert result.status is RunStatus.COMPLETED, result.model_dump_json()
            assert platform.revoked == ["old"]
