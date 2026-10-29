from datetime import UTC, datetime

import httpx
from browser.catalog import Catalog
from browser.setupserver import Setups
from browser.worker import Worker
from connectors.resend.browser import BrowserResend
from contracts import CreateRunCommand, RunStatus, Trigger
from core.auth import AuthenticatedIdentity
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from executor.authorization import Authorization
from executor.provider import Providers
from hosting.browser import Browsers
from rotation.hosting import Effects
from rotation.rotate import Rotation
from rotation.snapshot import loader
from tests.storage.browser.fixture import Planner, executable
from tests.storage.resend.browser.fixture import Launcher
from tests.storage.resend.fixture import Platform
from tests.storage.resend.rotation import setup


async def check_rotation(database: Database, identity: AuthenticatedIdentity) -> None:
    executable()
    platform, mutations = Platform(), []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.resend.com" and request.method != "GET":
            mutations.append((request.method, request.url.path))
        return platform.response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        org, host, seed = await setup(database, identity, http)
        credential = await seed.setup(
            org,
            host,
            "provider_resend",
            "app",
            "https://kinu-check.workload.example/probe",
            identity.actor_id,
        )
        assert len(mutations) == 1
        mutations.clear()
        mappings, versions = seed.mappings, seed.mappings.versions
        catalog, planner, launcher = (
            Catalog(database, versions.vault),
            Planner(),
            Launcher(platform),
        )
        worker = Worker(catalog, versions, planner, launcher)
        providers = Providers(Authorization(versions.vault, versions), worker, versions)
        providers.resend = seed.provider
        providers.resend_browser = BrowserResend(
            catalog, worker, seed.provider, seed.provider.receipts
        )
        browsers = Browsers(mappings, providers)
        target = await browsers.prepare(org, "provider_resend", identity.actor_id)
        setups = Setups(catalog, launcher)
        session, token = await setups.begin(org, target.id, identity.subject, ())
        await setups.stream(
            org,
            session.id,
            identity.subject,
            token,
            {
                "type": "action",
                "action": {"kind": "navigate", "url": "https://resend.com/api-keys"},
            },
        )
        session = await setups.get(org, session.id)
        _, target = await setups.complete(
            org, session.id, identity.subject, token, session.revision
        )
        assert set(await worker.ids(target)) == set(platform.keys)
        changed = await browsers.select(
            org, credential.id, target.id, credential.revision, identity.actor_id
        )
        run = (
            await RunWorkflow(RunStore(database)).create(
                CreateRunCommand(
                    id="command_browser_resend",
                    organisation_id=org,
                    credential_id=changed.id,
                    control_version=changed.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="browser_resend",
                        actor_id=identity.actor_id,
                        reason="Browser rotation test",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run
        result = await Rotation(
            database,
            Effects(mappings.hosts, versions, providers, seed.proof),
            loader(database),
            "global",
        ).execute(org, run.id, "worker_browser")
        assert result.status is RunStatus.COMPLETED, (result.model_dump_json(), launcher.failures)
        assert platform.revoked == [credential.provider_id]
        assert not mutations
        assert len(planner.calls) == 2
        history_store = HistoryStore(database, worker.images)
        public = await history_store.get(org, run.id)
        history = public.model_dump_json()
        assert "re_private-" not in history + str(planner.calls)
        proposals = [
            activity for activity in public.computer_use if activity.phase.value == "proposal"
        ]
        assert len(proposals) == 2 and all(
            activity.image_reference and activity.content for activity in proposals
        )
        for activity in proposals:
            content, kind = await history_store.image(org, run.id, activity.id)
            assert content.startswith(b"\x89PNG") and kind == "image/png"
        assert all(
            any(detail.label == "Method" and detail.value == "Browser" for detail in stage.details)
            for stage in public.stages
            if stage.stage.value in {"create", "revoke"}
        )
        assert platform.loaded.startswith("re_private-browser-fixture-")
