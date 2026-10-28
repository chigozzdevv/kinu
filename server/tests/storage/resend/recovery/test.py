import httpx
import pytest
from connectors.base.errors import ConnectorError
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError
from core.storage.database import Database
from hosting.recovery import recover
from tests.storage.resend.fixture import Platform
from tests.storage.resend.rotation import setup

pytestmark = pytest.mark.anyio
URL = "https://kinu-check.workload.example/probe"


async def test_recovery_retires_unused_captured_keys_and_preserves_installed_keys(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: object) -> None:
        raise ConnectorError("resend-proof", "Provider response requires review")

    async def sleep(seconds: float) -> None:
        return

    monkeypatch.setattr("connectors.resend.client.sleep", sleep)
    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        original = seed.provider.proof.check
        monkeypatch.setattr(seed.provider.proof, "check", unavailable)
        with pytest.raises(ConnectorError):
            await seed.setup(org, host, "provider_resend", "app", URL, identity.actor_id)
        row = await seed.rows.find_one({"organisation_id": org})
        assert row and row["phase"] == "stored" and len(platform.created) == 1
        captured = await seed.mappings.versions.vault.get(org, row["id"])
        assert captured["token"] == platform.keys[platform.created[0]]["token"]
        assert captured["token"] not in str(row) and platform.value == "setup-required"
        platform.value = "some-installed-workload-key"
        with pytest.raises(ResourceConflictError, match="installed"):
            await recover(
                seed.mappings, seed.provider, org, host, "provider_resend", "app", identity.actor_id
            )
        assert not platform.revoked
        platform.value = "setup-required"
        await recover(
            seed.mappings, seed.provider, org, host, "provider_resend", "app", identity.actor_id
        )
        assert platform.revoked == platform.created and not await seed.rows.find_one(
            {"organisation_id": org}
        )
        archived = await database.collection("host_seed_attempts").find_one({"id": row["id"]})
        assert archived and archived["phase"] == "retired"
        assert not await database.collection("secrets").find_one({"id": row["id"]})
        monkeypatch.setattr(seed.provider.proof, "check", original)
        credential = await seed.setup(org, host, "provider_resend", "app", URL, identity.actor_id)
        assert credential.provider_id == platform.created[-1] and len(platform.created) == 2


async def test_unknown_creations_cannot_be_reset_without_a_private_creation_receipt(
    database: Database,
    identity: AuthenticatedIdentity,
) -> None:
    platform = Platform(unknown=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        with pytest.raises(ConnectorError):
            await seed.setup(org, host, "provider_resend", "app", URL, identity.actor_id)
        with pytest.raises(ResourceConflictError, match="uncertain"):
            await recover(
                seed.mappings, seed.provider, org, host, "provider_resend", "app", identity.actor_id
            )
        assert not platform.revoked and len(platform.created) == 1


async def test_captured_setup_resumes_without_issuing_another_key(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: object) -> None:
        raise ConnectorError("resend-proof", "Provider response requires review")

    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        original = seed.provider.proof.check
        monkeypatch.setattr(seed.provider.proof, "check", unavailable)
        with pytest.raises(ConnectorError):
            await seed.setup(org, host, "provider_resend", "app", URL, identity.actor_id)
        assert len(platform.created) == 1 and platform.deployments == 0
        monkeypatch.setattr(seed.provider.proof, "check", original)
        credential = await seed.setup(
            org, host, "provider_resend", "app", URL, identity.actor_id, resume=True
        )
        assert credential.provider_id == platform.created[0] and len(platform.created) == 1
        assert platform.deployments == 1 and not platform.revoked
