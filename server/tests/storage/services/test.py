import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from agents.memory import AgentMemoryService
from agents.store import AgentStore
from api.app import create_app
from contracts import (
    AgentKind,
    AgentRegistration,
    AgentStatus,
    ApprovalDecision,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    CredentialGeneration,
    PageCheckpoint,
    PlaybookDraft,
    PlaybookEffect,
    PlaybookState,
    PlaybookStep,
    ProbeDefinition,
    ProbeKind,
    ProbeState,
    ProtectedAction,
    RunStatus,
    SecureField,
    Selector,
    SelectorKind,
    Stage,
    StageExecutionResult,
    StageExecutionStatus,
)
from core.account import AccountService
from core.account.store import AccountStore
from core.approval import ApprovalService
from core.approval.store import ApprovalStore
from core.auth import AuthenticatedIdentity
from core.config import Settings
from core.errors import ApprovalError, PlaybookError, ResourceConflictError, ResourceNotFoundError
from core.inventory.store import InventoryStore
from core.playbook import PlaybookService
from core.playbook.store import PlaybookStore
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.records import Records
from core.verification import ProbeService
from core.verification.store import ProbeStore
from demo.config import DemoConfig
from demo.seed import seed
from policy import digest
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from rotation.journal import Journal
from testkit import make_run
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 28, tzinfo=UTC)


async def workspace(database: Database, identity: AuthenticatedIdentity) -> str:
    membership = await AccountService(
        AccountStore(database, lambda: NOW), lambda: NOW
    ).create_organisation(identity, "Runtime services")
    return membership.organisation.id


def draft() -> PlaybookDraft:
    control = Selector(kind=SelectorKind.TEST_ID, value="key-control")
    checkpoint = PageCheckpoint(url_pattern="https://vendor.example.com/keys")
    return PlaybookDraft(
        name="Vendor",
        platform="vendor",
        allowed_domains=("vendor.example.com",),
        login_url_pattern="https://vendor.example.com/login",
        steps=(
            PlaybookStep(
                id="create_key",
                stage=Stage.CREATE,
                effect=PlaybookEffect.CREATE_CREDENTIAL,
                tool="browser.secure-capture",
                operation="capture",
                objective="Create replacement",
                selectors=(control,),
                checkpoint=checkpoint,
                evidence_checks=frozenset({"captured"}),
                secure_field=SecureField(
                    name="api_key",
                    selector=Selector(kind=SelectorKind.TEST_ID, value="key-value"),
                    provider_id_selector=Selector(kind=SelectorKind.TEST_ID, value="key-id"),
                ),
            ),
            PlaybookStep(
                id="revoke_key",
                stage=Stage.REVOKE,
                effect=PlaybookEffect.REVOKE_CREDENTIAL,
                tool="browser.revokeCredential",
                operation="revoke",
                objective="Retire old key",
                selectors=(control,),
                checkpoint=checkpoint,
                evidence_checks=frozenset({"revoked"}),
            ),
        ),
    )


async def test_default_runtime_serves_data_and_reports_unavailable_integrations(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await workspace(database, identity)
    monkeypatch.setattr("api.runtime.application_database", lambda config: database)
    app = create_app(
        browser=make_browser(identity),
        settings=Settings(database_url=SecretStr("postgresql://test")),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        root = f"/v1/organisations/{org}"
        for path in ("playbooks", "approvals", "agents", "overview", "features"):
            response = await client.get(f"{root}/{path}", headers=HEADERS)
            assert response.status_code == 200, response.text
        assert (await client.get(f"{root}/features", headers=HEADERS)).json() == {
            "aws_connections": False,
            "github": False,
            "provider_api": False,
            "browser_setup": False,
            "browser_execution": False,
            "playbook_generation": False,
        }
        assert (await client.get(f"{root}/overview", headers=HEADERS)).json() == {
            "credentials": 0,
            "rotations_in_progress": 0,
            "failed_rotations": 0,
            "open_incidents": 0,
            "pending_approvals": 0,
        }
        assert (await client.get(f"{root}/features")).status_code == 401
        assert (
            await client.get("/v1/organisations/org_other/features", headers=HEADERS)
        ).status_code == 403
        response = await client.post(
            f"{root}/connections/aws",
            headers=HEADERS,
            json={
                "account_id": "123456789012",
                "region": "eu-west-2",
                "display_name": "Test",
                "function_arns": ["arn:aws:lambda:eu-west-2:123456789012:function:test"],
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "setup-required"
        response = await client.post(
            f"{root}/inventory/connections/connection_one/setup",
            headers=HEADERS,
            json={"extra_domains": []},
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "setup-required"
        response = await client.post(
            f"{root}/probes", headers=HEADERS, json={"id": "probe_one", "name": "Health"}
        )
        assert response.status_code == 201, response.text


async def test_playbooks_preserve_versions_and_detach_on_archive(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    inventory = InventoryStore(database)
    service = PlaybookService(PlaybookStore(database), lambda: NOW, inventory)
    versions = await asyncio.gather(
        *(
            service.create_version(
                org, "playbook_one", f"version_{number}", draft(), "actor_author"
            )
            for number in range(2)
        )
    )
    assert {version.number for _, version in versions} == {1, 2}
    current = await PlaybookStore(database).get(org, "playbook_one")
    assert current.latest_version == 2
    assert current.latest_version_id
    published = await service.publish(org, current.id, current.latest_version_id, "actor_reviewer")
    assert published.state is PlaybookState.PUBLISHED
    assert (await PlaybookStore(database).get_version(org, current.id, published.id)) == published
    with pytest.raises(PlaybookError):
        await service.get_version(org, "playbook_other", published.id)
    with pytest.raises(ResourceNotFoundError):
        await service.get_version("org_other", current.id, published.id)
    connection = Connection(
        id="connection_browser",
        organisation_id=org,
        platform="vendor",
        display_name="Browser",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.BROWSER,
        authorization=ConnectionAuthorization.BROWSER_SESSION,
        capabilities=frozenset({"browser.execute"}),
        allowed_resources=("vendor.example.com",),
        status=ConnectionStatus.SETUP_REQUIRED,
        region="eu-west-2",
        created_at=NOW,
        updated_at=NOW,
    )
    await inventory.add_connection(connection)
    await service.attach(org, connection.id, 0, current.id, published.id)
    current = await service.get(org, current.id)
    with pytest.raises(PlaybookError):
        await service.archive(org, current.id, current.revision)
    await service.archive(org, current.id, current.revision, cascade=True)
    detached = await inventory.get_connection(org, connection.id)
    assert detached.playbook_id is None and detached.status is ConnectionStatus.SETUP_REQUIRED
    assert (
        await PlaybookService(PlaybookStore(database), lambda: NOW, inventory).list_playbooks(org)
        == ()
    )


async def test_probe_publication_is_tenant_bound_and_supersedes_previous_version(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    connection = Connection(
        id="connection_http",
        organisation_id=org,
        platform="http",
        display_name="Health",
        roles=frozenset({ConnectionRole.TELEMETRY}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY,
        authorization_reference="secret://health/token/versions/one",
        capabilities=frozenset({"http.request"}),
        allowed_resources=("https://service.example",),
        status=ConnectionStatus.READY,
        region="eu-west-2",
        created_at=NOW,
        updated_at=NOW,
    )
    await InventoryStore(database).add_connection(connection)
    service = ProbeService(ProbeStore(database), lambda: NOW)
    await service.create(org, "probe_one", "Health")
    for number in range(2):
        definition = ProbeDefinition(
            id=f"version_{number}",
            organisation_id=org,
            kind=ProbeKind.HTTP,
            connection_id=connection.id,
            target="https://service.example/health",
        )
        await service.create_version(org, "probe_one", definition, "actor_author")
        active = await service.activate(org, "probe_one", definition.id, "actor_reviewer")
        assert active.state is ProbeState.ACTIVE
    async with Records(database).read() as rows:
        from contracts import ProbeVersion

        old = await rows.get(ProbeVersion, org, "version_0")
    assert old.state is ProbeState.SUPERSEDED
    with pytest.raises(ResourceNotFoundError):
        await service.activate("org_other", "probe_one", "version_1", "actor_reviewer")


async def test_approval_is_bound_to_evidence_and_consumed_once(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    secret = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-ABC123"
    config = DemoConfig(
        organisation_id=org,
        provider_function="arn:aws:lambda:eu-west-2:123456789012:function:kinu-demo-provider",
        consumer_function="arn:aws:lambda:eu-west-2:123456789012:function:kinu-demo-consumer",
        secret=secret,
        region="eu-west-2",
        assessment_function="kinu-assess",
    )
    await seed(
        database,
        config,
        "a" * 24,
        secret + "/versions/" + "1" * 64,
        "arn:aws:iam::123456789012:role/test",
        "x" * 32,
        identity.actor_id,
        ready=True,
    )
    async with Records(database).read() as rows:
        from contracts import ManagedCredential

        credential = await rows.get(ManagedCredential, org, config.credential_id)
        generation = await rows.get(
            CredentialGeneration, org, credential.active_generation_id or ""
        )
    run = make_run(NOW).model_copy(
        update={
            "organisation_id": org,
            "credential_id": credential.id,
            "control_version": credential.control_version,
            "status": RunStatus.PAUSED,
            "current_generation_id": generation.id,
            "target_generation_id": generation.id,
            "plan_hash": "a" * 64,
        }
    )
    async with database.connection() as sql:
        await sql.execute(
            "INSERT INTO kinu.runs VALUES (%s,%s,%s,%s,%s)",
            (org, run.id, run.credential_id, run.revision, Jsonb(run.model_dump(mode="json"))),
        )
    evidence = StageExecutionResult(
        id="evidence_one",
        organisation_id=org,
        run_id=run.id,
        stage=Stage.VERIFY,
        status=StageExecutionStatus.SUCCEEDED,
        checks=frozenset({"provider-valid"}),
        evidence_ids=("evidence_one",),
        started_at=NOW,
        completed_at=NOW,
    )
    await Journal(database).save(evidence)
    action = ProtectedAction(
        id="action_one",
        organisation_id=org,
        run_id=run.id,
        kind="provider.revokeCredential",
        resource="provider/key",
        credential_id=credential.id,
        generation_id=generation.id,
        provider_id=generation.provider_id or "",
        control_version=credential.control_version,
        plan_hash=run.plan_hash or "",
        evidence_hash=digest(evidence),
    )
    service = ApprovalService(ApprovalStore(database), lambda: NOW)
    capability = await service.request(
        "approval_one",
        action,
        action.plan_hash,
        action.evidence_hash,
        "actor_requester",
        NOW + timedelta(minutes=5),
        "s" * 43,
    )
    assert (
        await HistoryStore(database).approval_evidence(org, capability.approval.id)
    ).evidence_hash == digest(evidence)
    with pytest.raises(ApprovalError):
        await service.decide(
            org, capability.approval.id, 0, ApprovalDecision.APPROVED, "actor_requester"
        )
    await service.decide(
        org, capability.approval.id, 0, ApprovalDecision.APPROVED, "actor_reviewer"
    )
    results = await asyncio.gather(
        *(
            service.consume(
                org,
                capability.approval.id,
                capability.token,
                action,
                action.plan_hash,
                action.evidence_hash,
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ApprovalError) for result in results) == 1
    assert (await ApprovalStore(database).list_approvals("org_other", 10)) == ()
    altered = action.model_copy(update={"id": "action_other", "evidence_hash": "b" * 64})
    with pytest.raises(ApprovalError, match="saved verification"):
        await service.request(
            "approval_other",
            altered,
            altered.plan_hash,
            altered.evidence_hash,
            "actor_requester",
            NOW + timedelta(minutes=5),
            "t" * 43,
        )


async def test_agent_memory_is_persistent_scoped_and_expires(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    registration = AgentRegistration(
        id="agent_operator",
        organisation_id=org,
        kind=AgentKind.OPERATOR,
        display_name="Operator",
        version="one",
        skills=frozenset({"assess"}),
        owner="Kinu",
        identity="operator",
        endpoint="https://agent.example",
        deployment="test",
        registry="test",
        ingress_gateway="test",
        egress_gateway="test",
        region="eu-west-2",
        approved_callers=frozenset({"worker"}),
        tool_destinations=frozenset({"assessment"}),
        status=AgentStatus.READY,
        registered_at=NOW,
    )
    store = AgentStore(database)
    await store.activate(registration)
    service = AgentMemoryService(store, lambda: NOW)
    await service.remember(
        registration,
        "memory_one",
        "The provider requires overlapping credentials.",
        ("evidence_one",),
        identity.actor_id,
        timedelta(minutes=5),
    )
    assert (
        len(
            await AgentMemoryService(AgentStore(database), lambda: NOW).retrieve(
                registration, "provider"
            )
        )
        == 1
    )
    assert await AgentStore(database).list_memories("org_other", AgentKind.OPERATOR) == ()
    assert (
        await AgentMemoryService(store, lambda: NOW + timedelta(minutes=6)).retrieve(
            registration, "provider"
        )
        == ()
    )
    with pytest.raises(ResourceConflictError, match="secret"):
        await service.remember(
            registration,
            "memory_secret",
            "password=must-not-persist",
            ("evidence_one",),
            identity.actor_id,
        )
