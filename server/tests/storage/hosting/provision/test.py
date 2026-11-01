import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from connectors.base.errors import ConnectorError
from core.auth import AuthenticatedIdentity
from core.errors import StorageUnavailableError
from core.storage.database import Database
from hosting.mapping import Mappings
from hosting.models import Provision, Selection
from hosting.proof import Proof
from hosting.provision import Provisioning
from jobs.queue import Jobs
from tests.storage.hosting.rotation.fixture import OLD
from tests.storage.hosting.rotation.fixture import Platform as IssuerPlatform
from tests.storage.hosting.rotation.test import setup as issuer_setup
from tests.storage.resend.fixture import Platform
from tests.storage.resend.rotation import setup

pytestmark = pytest.mark.anyio


def customer(*, unknown: bool = False, deployment_state: str = "live") -> Platform:
    return Platform(
        project_name="Customer mail service",
        variable="MAIL_TOKEN",
        health_host="customer.workload.example",
        value="customer-placeholder",
        unknown=unknown,
        deployment_state=deployment_state,
    )


def request() -> Provision:
    return Provision(
        project_id="app",
        variable_reference="render://app/production/MAIL_TOKEN",
        provider_connection_id="provider_resend",
        health_url="https://customer.workload.example/probe",
    )


def queue() -> Jobs:
    jobs = Mock(spec=Jobs)
    jobs.add = AsyncMock()
    return jobs


async def test_first_customer_key_is_created_installed_and_registered_without_sandbox_names(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    platform = customer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        service = Provisioning(seed.mappings, queue(), seed.proof)
        await service.initialize()
        pending = await service.request(org, host, identity.actor_id, "customer_setup", request())
        assert pending["phase"] == "pending" and not platform.created
        await service.execute(org, pending["id"])
        row = await service.get(org, pending["id"])
        assert row["phase"] == "completed", row.get("message")
        credential = await seed.mappings.hosts.inventory.get_credential(org, row["credential_id"])
        assert credential.provider_id == platform.created[0]
        assert credential.consumer_ids and platform.loaded == platform.value
        assert not platform.revoked and platform.deployments == 1 and "value" not in row
        assert not await database.collection("provision_locks").find_one({"organisation_id": org})
        assert "re_private-fixture" not in json.dumps(await service.list(org, host))
        assert (
            await database.collection("audit").count_documents(
                {"organisation_id": org, "data.kind": "hosting.key-setup.completed"}
            )
            == 1
        )
        same = await service.request(org, host, identity.actor_id, "customer_setup", request())
        await service.execute(org, same["id"])
        assert same["credential_id"] == credential.id
        assert len(platform.created) == platform.deployments == 1


async def test_captured_key_can_resume_after_temporary_receipt_verification_failure(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = customer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        service = Provisioning(seed.mappings, queue(), seed.proof)
        await service.initialize()
        await service.request(org, host, identity.actor_id, "captured_setup", request())
        original = seed.provider.identify
        monkeypatch.setattr(
            seed.provider,
            "identify",
            AsyncMock(side_effect=ConnectorError("temporary-proof", "Issuer is unavailable")),
        )
        await service.execute(org, "captured_setup")
        row = await service.get(org, "captured_setup")
        assert row["phase"] == "review-required" and row["held_phase"] == "stored"
        assert platform.value == "customer-placeholder" and platform.deployments == 0
        monkeypatch.setattr(seed.provider, "identify", original)
        await service.recover(org, "captured_setup")
        await service.execute(org, "captured_setup")
        assert (await service.get(org, "captured_setup"))["phase"] == "completed"
        assert len(platform.created) == platform.deployments == 1


async def test_unknown_creation_is_held_and_never_replayed(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    platform = customer(unknown=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        service = Provisioning(seed.mappings, queue(), seed.proof)
        await service.initialize()
        await service.request(org, host, identity.actor_id, "unknown_setup", request())
        await service.execute(org, "unknown_setup")
        await service.recover(org, "unknown_setup")
        await service.execute(org, "unknown_setup")
        row = await service.get(org, "unknown_setup")
        assert row["phase"] == "review-required" and len(platform.created) == 1
        assert platform.value == "customer-placeholder" and not platform.revoked
        assert await database.collection("provision_locks").find_one({"organisation_id": org})


@pytest.mark.parametrize("succeeds", [True, False])
async def test_pending_first_deployment_keeps_its_key_until_the_outcome_is_known(
    database: Database, identity: AuthenticatedIdentity, succeeds: bool
) -> None:
    platform = customer(deployment_state="build_in_progress")
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        service = Provisioning(seed.mappings, queue(), seed.proof)
        await service.initialize()
        await service.request(org, host, identity.actor_id, "pending_setup", request())
        await service.execute(org, "pending_setup")
        await service.execute(org, "pending_setup")
        assert (await service.get(org, "pending_setup"))["phase"] == "deploying"
        assert len(platform.created) == platform.deployments == 1 and not platform.revoked
        platform.deployment_state = "live" if succeeds else "build_failed"
        if succeeds:
            assert platform.candidate
            platform.current = platform.candidate
            platform.loaded = platform.value
        await service.execute(org, "pending_setup")
        if not succeeds:
            assert (await service.get(org, "pending_setup"))["phase"] == "review-required"
            await service.recover(org, "pending_setup")
            await service.execute(org, "pending_setup")
        row = await service.get(org, "pending_setup")
        assert row["phase"] == ("completed" if succeeds else "cancelled"), row.get("message")
        assert platform.revoked == ([] if succeeds else platform.created)
        assert len(platform.created) == 1 and "value" not in row
        if not succeeds:
            assert platform.value == platform.loaded == "customer-placeholder"


async def test_first_key_setup_uses_the_selected_custom_provider_adapter(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    platform = IssuerPlatform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, existing = await issuer_setup(database, identity, http)
        org = existing.organisation_id
        platform.variables["project-three"] = OLD
        platform.loaded["project-three"] = OLD
        platform.current["project-three"] = "project-three-old"
        platform.deployments["project-three-old"] = OLD
        host = (
            await hosts.inventory.get_service(org, existing.consumer_ids[0])
        ).runtime_connection_id
        connection = await hosts.inventory.get_connection(org, host)
        await hosts.save(
            org,
            host,
            identity.actor_id,
            "a" * 64,
            Selection(
                expected_revision=connection.revision,
                project_ids=("project-one", "project-two", "project-three"),
            ),
        )
        service = Provisioning(Mappings(hosts, versions, provider), queue(), Proof(http))
        await service.initialize()
        await service.request(
            org,
            host,
            identity.actor_id,
            "custom_setup",
            Provision(
                project_id="project-three",
                variable_reference="render://project-three/production/API_KEY",
                provider_connection_id=existing.connection_id,
                health_url="https://project-three.workload.example/health",
                scopes=frozenset({"read"}),
            ),
        )
        await service.execute(org, "custom_setup")
        row = await service.get(org, "custom_setup")
        assert row["phase"] == "completed", row.get("message")
        created = await hosts.inventory.get_credential(org, row["credential_id"])
        assert created.provider_id == "new" and created.connection_id == existing.connection_id
        assert not platform.revoked and platform.keys["old"]["active"]
        assert len(await hosts.inventory.credentials(org)) == 2


@pytest.mark.parametrize("stage", ["creating", "writing"])
async def test_interrupted_first_key_mutations_become_reviewable_without_replay(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    platform = customer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        service = Provisioning(seed.mappings, queue(), seed.proof)
        await service.initialize()
        await service.request(org, host, identity.actor_id, "interrupted_setup", request())
        with monkeypatch.context() as stopped:
            if stage == "creating":
                stopped.setattr(
                    seed.provider,
                    "create_credential",
                    AsyncMock(side_effect=asyncio.CancelledError),
                )
            else:
                stopped.setattr(
                    "connectors.hosting.render.Render.write",
                    AsyncMock(side_effect=asyncio.CancelledError),
                )
            with pytest.raises(asyncio.CancelledError):
                await service.execute(org, "interrupted_setup")
        await service.rows.update_one(
            {"organisation_id": org, "id": "interrupted_setup"},
            {"$set": {"updated_at": datetime.now(UTC) - timedelta(minutes=6)}},
        )
        await service.poll()
        row = await service.get(org, "interrupted_setup")
        assert row["phase"] == "review-required" and row["held_phase"] == stage
        await service.recover(org, "interrupted_setup")
        await service.execute(org, "interrupted_setup")
        row = await service.get(org, "interrupted_setup")
        assert row["phase"] == "review-required" and not platform.revoked
        assert len(platform.created) == (0 if stage == "creating" else 1)
        if stage == "writing":
            assert row["held_phase"] == "writing"
            await service.recover(org, "interrupted_setup")
            await service.execute(org, "interrupted_setup")
            assert (await service.get(org, "interrupted_setup"))["phase"] == "review-required"
            assert not platform.revoked
            platform.value = platform.keys[platform.created[0]]["token"]
            await service.recover(org, "interrupted_setup")
            await service.execute(org, "interrupted_setup")
            assert (await service.get(org, "interrupted_setup"))["phase"] == "cancelled"
            assert platform.revoked == platform.created
        assert platform.value == platform.loaded == "customer-placeholder"


async def test_unhealthy_first_key_can_restore_the_previous_value_before_retirement(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    platform = customer()

    def response(request: httpx.Request) -> httpx.Response:
        result = platform.response(request)
        if request.url.host == platform.health_host and platform.loaded != "customer-placeholder":
            return httpx.Response(
                200, json=result.json() | {"ok": False, "state": "provider-rejected"}
            )
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        org, host, seed = await setup(database, identity, http)
        service = Provisioning(seed.mappings, queue(), seed.proof)
        await service.initialize()
        await service.request(org, host, identity.actor_id, "unhealthy_setup", request())
        await service.execute(org, "unhealthy_setup")
        assert (await service.get(org, "unhealthy_setup"))["held_phase"] == "registering"
        await service.recover(org, "unhealthy_setup")
        await service.execute(org, "unhealthy_setup")
        row = await service.get(org, "unhealthy_setup")
        assert row["phase"] == "cancelled", row.get("message")
        assert platform.value == platform.loaded == "customer-placeholder"
        assert platform.revoked == platform.created


async def test_finalized_setup_releases_its_lock_after_a_worker_interruption(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = customer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        org, host, seed = await setup(database, identity, http)
        service = Provisioning(seed.mappings, queue(), seed.proof)
        await service.initialize()
        await service.request(org, host, identity.actor_id, "finalizing_setup", request())
        with monkeypatch.context() as interrupted:
            interrupted.setattr(
                service, "finalize", AsyncMock(side_effect=StorageUnavailableError("Interrupted"))
            )
            await service.execute(org, "finalizing_setup")
        assert (await service.get(org, "finalizing_setup"))["phase"] == "completed"
        assert await database.collection("provision_locks").find_one({"organisation_id": org})
        await service.poll()
        assert not await database.collection("provision_locks").find_one({"organisation_id": org})
        row = await service.get(org, "finalizing_setup")
        assert "value" not in row and row["finalized"]
        assert len(platform.created) == platform.deployments == 1
