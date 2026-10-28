import json
from datetime import UTC, datetime

import httpx
import pytest
from connectors.base import ConnectorContext
from connectors.base.errors import ConnectorError
from connectors.resend.client import Resend
from connectors.resend.config import definition
from contracts import Connection, CreateRunCommand, RunStatus, Trigger
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from executor.authorization import Authorization
from executor.receipts import Receipts
from executor.vault import Vault
from pydantic import SecretStr
from rotation.hosting import Effects
from rotation.rotate import Rotation
from rotation.snapshot import loader
from testkit import make_run
from tests.storage.resend.fixture import Platform
from tests.storage.resend.rotation import setup

pytestmark = pytest.mark.anyio
KEY = "re_sending-fixture-with-private-material"
ID = "0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c"


def connection() -> Connection:
    now = datetime.now(UTC)
    return Connection.model_validate(
        {
            "id": "provider_resend",
            "organisation_id": "org_one",
            "platform": "resend",
            "display_name": "Resend",
            "roles": ["provider"],
            "interface": "api",
            "authorization": "api-key",
            "authorization_reference": "private://provider_resend",
            "capabilities": list(Resend.tools),
            "allowed_resources": ["resend:credentials:*"],
            "http": definition(ID).model_dump(mode="json"),
            "status": "ready",
            "region": "global",
            "created_at": now,
            "updated_at": now,
        }
    )


async def test_resend_receipts_bind_private_created_keys_to_exact_issuer_ids(
    database: Database,
) -> None:
    vault = Vault(database, SecretStr("private-receipt-test-key-at-least-32-characters"))
    await vault.initialize()
    receipts, source = Receipts(vault), connection()
    await receipts.initialize()
    await vault.put("org_one", source.id, {"token": "management-fixture"})
    active, requests = True, []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active
        requests.append((request.method, request.url.path))
        if request.url.path == "/inboxes":
            return httpx.Response(
                401 if active else 400,
                json={
                    "name": "restricted_api_key" if active else "validation_error",
                    "message": "This API key is restricted to only send emails."
                    if active
                    else "API key is invalid",
                },
            )
        assert request.headers["authorization"] == "Bearer management-fixture"
        if request.method == "POST":
            assert json.loads(request.content) == {
                "name": "Kinu test",
                "permission": "sending_access",
                "domain_id": ID,
            }
            return httpx.Response(201, json={"id": ID, "token": KEY})
        if request.method == "DELETE":
            assert request.url.path == "/api-keys/" + ID
            active = False
            return httpx.Response(200, json={"id": ID})
        return httpx.Response(
            200,
            json={"has_more": False, "data": [{"id": ID, "name": "Kinu test"}] if active else []},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = Resend(Authorization(vault), receipts, http)
        context = ConnectorContext(
            "create_one", "actor_one", source, make_run(), datetime.now(UTC), "create_one"
        )
        with pytest.raises(ConnectorError, match="preserve"):
            await provider.execute(
                "provider.createCredential",
                {"name": "Kinu test", "scopes": ["full_access"]},
                context,
            )
        assert not requests
        response = await provider.execute(
            "provider.createCredential",
            {"name": "Kinu test", "scopes": ["sending_access", "domain:" + ID]},
            context,
        )
        assert response.secret and KEY not in str(response.result)
        with response.secret as value:
            assert await provider.identify(source, value) == ID
            row = await receipts.collection.find_one({})
            assert row and KEY not in str(row) and "sending_access" not in row["value"]
            with pytest.raises(ResourceNotFoundError):
                await receipts.identify(
                    source.model_copy(update={"organisation_id": "org_other"}), value
                )
            with pytest.raises(ResourceConflictError):
                await receipts.remember(
                    source, "another-id", value, {"kind": "api-key", "scopes": ["sending_access"]}
                )
            assert await provider.test_credential(source, value) == 200
            with pytest.raises(ConnectorError, match="Only a Kinu-created"):
                await provider.revoke_credential(source, "untracked-key")
            await provider.revoke_credential(source, ID)
            assert await provider.test_credential(source, value) == 401


async def test_sandbox_initialization_and_full_resend_rotation_preserve_identity_and_private_values(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def sleep(seconds: float) -> None:
        assert 0 <= seconds <= 20

    monkeypatch.setattr("connectors.resend.client.sleep", sleep)
    monkeypatch.setattr("rotation.hosting.sleep", sleep)
    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        credential = await seed.setup(
            org,
            host,
            "provider_resend",
            "app",
            "https://kinu-check.workload.example/probe",
            identity.actor_id,
        )
        assert len(platform.created) == 1 and platform.deployments == 1
        assert (
            await seed.setup(
                org,
                host,
                "provider_resend",
                "app",
                "https://kinu-check.workload.example/probe",
                identity.actor_id,
            )
        ).id == credential.id
        assert len(platform.created) == 1
        old_value = platform.loaded
        run = (
            await RunWorkflow(RunStore(database)).create(
                CreateRunCommand(
                    id="command_resend",
                    organisation_id=org,
                    credential_id=credential.id,
                    control_version=credential.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="resend_rotation",
                        actor_id=identity.actor_id,
                        reason="Rotate the sandbox sending key",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run
        mappings = seed.mappings
        result = await Rotation(
            database,
            Effects(mappings.hosts, mappings.versions, seed.provider, seed.proof),
            loader(database),
            "global",
        ).execute(org, run.id, "worker_resend")
        assert result.status is RunStatus.COMPLETED, result.model_dump_json()
        assert len(platform.created) == 2 and platform.revoked == [credential.provider_id]
        assert platform.loaded != old_value and platform.deployments == 2
        history = (await HistoryStore(database).get(org, run.id)).model_dump_json()
        private = str(await database.collection("versions").find({}).to_list())
        assert all(
            value not in history + private
            for value in (old_value, platform.loaded, "management-fixture")
        )


@pytest.mark.parametrize("failure", ["existing", "unknown"])
async def test_sandbox_never_overwrites_existing_keys_or_repeats_an_uncertain_creation(
    database: Database,
    identity: AuthenticatedIdentity,
    failure: str,
) -> None:
    platform = Platform(
        unknown=failure == "unknown",
        value="existing-key" if failure == "existing" else "setup-required",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        for _ in range(2):
            with pytest.raises((ConnectorError, ResourceConflictError)):
                await seed.setup(
                    org,
                    host,
                    "provider_resend",
                    "app",
                    "https://kinu-check.workload.example/probe",
                    identity.actor_id,
                )
        assert len(platform.created) == (1 if failure == "unknown" else 0)
        assert platform.deployments == 0 and not platform.revoked
