from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from browser.catalog import Catalog
from browser.worker import Worker
from connectors.resend.browser import BrowserResend
from contracts import CancelRunCommand, ConnectionStatus, CreateRunCommand, Trigger
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from executor.authorization import Authorization
from executor.provider import Providers
from hosting.browser import Browsers
from rotation.snapshot import snapshot
from tests.storage.browser.fixture import Planner
from tests.storage.resend.fixture import Platform
from tests.storage.resend.rotation import setup

pytestmark = pytest.mark.anyio


async def test_switching_to_browser_keeps_one_credential_and_vault_version_and_respects_run_locks(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        mappings, versions = seed.mappings, seed.mappings.versions
        catalog = Catalog(database, versions.vault)
        worker = Worker(catalog, versions, Planner())
        monkeypatch.setattr(worker, "ids", AsyncMock(side_effect=lambda _: tuple(platform.keys)))
        providers = Providers(Authorization(versions.vault, versions), worker, versions)
        providers.resend = seed.provider
        providers.resend_browser = BrowserResend(
            catalog, worker, seed.provider, seed.provider.receipts
        )
        browsers = Browsers(mappings, providers)
        target = await browsers.prepare(org, "provider_resend", identity.actor_id)
        assert await browsers.prepare(org, "provider_resend", identity.actor_id) == target
        with pytest.raises(ResourceConflictError, match="connected browser"):
            await browsers.select(
                org, credential.id, target.id, credential.revision, identity.actor_id
            )
        target = await mappings.hosts.inventory.replace_connection(
            target.model_copy(
                update={
                    "status": ConnectionStatus.READY,
                    "authorization_reference": "browser://" + target.id + "/fixture",
                    "revision": target.revision + 1,
                }
            ),
            target.revision,
        )
        workflow = RunWorkflow(RunStore(database))
        run = (
            await workflow.create(
                CreateRunCommand(
                    id="command_lock",
                    organisation_id=org,
                    credential_id=credential.id,
                    control_version=credential.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="lock_fixture",
                        actor_id=identity.actor_id,
                        reason="Lock test",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run
        with pytest.raises(ResourceConflictError, match="active rotation"):
            await browsers.select(
                org, credential.id, target.id, credential.revision, identity.actor_id
            )
        assert await mappings.hosts.inventory.get_credential(org, credential.id) == credential
        await workflow.cancel(
            CancelRunCommand(
                id="command_cancel",
                organisation_id=org,
                run_id=run.id,
                actor_id=identity.actor_id,
                expected_revision=run.revision,
            )
        )
        count = await versions.collection.count_documents({"organisation_id": org})
        changed = await browsers.select(
            org, credential.id, target.id, credential.revision, identity.actor_id
        )
        assert changed.connection_id == target.id
        assert changed.active_generation_id == credential.active_generation_id
        assert changed.secret_reference == credential.secret_reference
        assert len(await mappings.hosts.inventory.credentials(org)) == 1
        assert await versions.collection.count_documents({"organisation_id": org}) == count
        state = await snapshot(database, org, changed.id, changed.control_version)
        assert state.verification and state.verification.id == "provider_resend"
        assert state.playbook and state.provider == target
