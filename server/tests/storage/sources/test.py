import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from connectors.github import GitHubOnboardingConnector
from contracts import Confidence, ConnectionStatus, IngestionEvent, Severity, SourceResource
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AccessControl, AuthenticatedIdentity
from core.github.bridge import GitHubBridge
from core.github.service import GitHubOnboardingService
from core.inventory.store import InventoryStore
from core.private import PrivateClient
from core.storage.database import Database
from executor.app import app as executor
from executor.config import ExecutorSettings
from executor.runtime import Runtime
from executor.vault import Vault
from jobs.queue import Jobs
from mail.models import ForwardInbox
from pydantic import SecretStr
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio
KEY = b"receiving-signature-fixture-32bytes"
GITHUB_SECRET = "github-webhook-fixture"
EMAIL_ID = "37e4414c-5e25-4dbc-a071-43552a4bd53b"


def email_signature(body: bytes) -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    content = b"msg_fixture." + timestamp.encode() + b"." + body
    signature = base64.b64encode(hmac.new(KEY, content, hashlib.sha256).digest()).decode()
    return {
        "svix-id": "msg_fixture",
        "svix-timestamp": timestamp,
        "svix-signature": "v1," + signature,
    }


def github_signature(body: bytes, event: str) -> dict[str, str]:
    return {
        "x-hub-signature-256": "sha256="
        + hmac.new(GITHUB_SECRET.encode(), body, hashlib.sha256).hexdigest(),
        "x-github-event": event,
        "x-github-delivery": "delivery-fixture",
    }


@pytest.fixture
async def source_app(database: Database, identity: AuthenticatedIdentity) -> Any:
    def now() -> datetime:
        return datetime.now(UTC)

    accounts = AccountStore(database, now)
    account = AccountService(accounts, now)
    one = await account.create_organisation(identity, "Sources one")
    two = await account.create_organisation(identity, "Sources two")
    config = ExecutorSettings(
        vault_key=SecretStr("vault-test-encryption-material-at-least-32"),
        executor_token=SecretStr("executor-private"),
        native_token=SecretStr("native-private"),
        resend_key=SecretStr("receiving-fixture-key"),
        inbound_email_domain="inbound.example.com",
        resend_webhook_secret=SecretStr("whsec_" + base64.b64encode(KEY).decode()),
        github_app_slug="kinu-fixture",
        github_client_id="github-client-fixture",
        github_client_secret=SecretStr("github-client-secret-fixture"),
        github_webhook_secret=SecretStr(GITHUB_SECRET),
    )
    assert config.vault_key is not None
    vault = Vault(database, config.vault_key)
    await vault.initialize()
    queue = Jobs("redis://localhost:6379")
    previous = getattr(executor.state, "service", None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=executor), base_url="http://executor"
    ) as private_http:
        service = Runtime(config, database, vault, private_http, queue)
        executor.state.service = service
        private = PrivateClient(private_http, "http://executor", SecretStr("executor-private"))
        app = create_app(
            ApiServices(
                access=AccessControl(accounts),
                accounts=account,
                private=private,
                github=GitHubBridge(private),
            ),
            browser=make_browser(identity),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            try:
                yield SimpleNamespace(
                    client=client,
                    service=service,
                    one=one.organisation.id,
                    two=two.organisation.id,
                    identity_email=identity.email,
                )
            finally:
                executor.state.service = previous
                await queue.close()


async def test_forwarding_addresses_are_stable_and_workspace_scoped(
    source_app: Any, database: Database
) -> None:
    http, org, other = source_app.client, source_app.one, source_app.two
    path = f"/v1/organisations/{org}/connections/email"
    assert (await http.get(path, headers=HEADERS)).json() is None
    first = await http.post(path, headers=HEADERS, json={})
    assert first.status_code == 201, first.text
    second = await http.post(path, headers=HEADERS, json={})
    assert first.json()["address"] == second.json()["address"]
    different = await http.post(
        f"/v1/organisations/{other}/connections/email", headers=HEADERS, json={}
    )
    assert different.json()["address"] != first.json()["address"]
    assert (
        await http.post("/v1/organisations/org_unowned/connections/email", headers=HEADERS, json={})
    ).status_code == 403
    features = await http.get(f"/v1/organisations/{org}/features", headers=HEADERS)
    assert features.json()["email"] and features.json()["github"]
    assert "receiving-fixture-key" not in features.text
    assert await database.collection("inventory").count_documents({"kind": "forward-inbox"}) == 2


async def test_receipt_is_durable_deduplicated_and_workspace_scoped(
    source_app: Any, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, service, org = source_app.client, source_app.service, source_app.one
    inbox = (
        await http.post(f"/v1/organisations/{org}/connections/email", headers=HEADERS, json={})
    ).json()
    await http.post(
        f"/v1/organisations/{source_app.two}/connections/email", headers=HEADERS, json={}
    )
    body = json.dumps({"type": "email.received", "data": {"email_id": EMAIL_ID}}).encode()
    assert (await http.post("/v1/webhooks/resend", content=body)).status_code == 400
    for _ in range(2):
        response = await http.post(
            "/v1/webhooks/resend", content=body, headers=email_signature(body)
        )
        assert response.status_code == 202, response.text
    assert await database.collection("source_deliveries").count_documents({}) == 1

    async def received(self: Any, identity: str) -> dict[str, Any]:
        assert identity == EMAIL_ID
        return {
            "id": identity,
            "from": "sender@example.com",
            "subject": "Hello",
            "text": "Ordinary mail, no credential incident.",
            "to": ["original@example.com"],
            "received_for": [inbox["address"]],
        }

    monkeypatch.setattr("mail.receiving.Receiving.get", received)
    await service.sources.drain()
    box = (await http.get(f"/v1/organisations/{org}/connections/email", headers=HEADERS)).json()
    other = (
        await http.get(f"/v1/organisations/{source_app.two}/connections/email", headers=HEADERS)
    ).json()
    assert box["receiving"] and box["last_received_at"]
    assert not other["receiving"]
    assert (
        await InventoryStore(database).get_connection(org, inbox["id"])
    ).status is ConnectionStatus.READY
    assert await database.collection("inventory").count_documents({"kind": "incident"}) == 0
    assert "Ordinary mail" not in str(await database.collection("source_deliveries").find_one({}))


async def test_forwarding_confirmation_is_shown_without_a_test_message_or_incident(
    source_app: Any, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, service, org = source_app.client, source_app.service, source_app.one
    inbox = (
        await http.post(f"/v1/organisations/{org}/connections/email", headers=HEADERS, json={})
    ).json()
    url = "https://mail-settings.google.com/mail/vf-fixture?token=fixture"

    async def received(self: Any, identity: str) -> dict[str, Any]:
        return {
            "id": identity,
            "from": "forwarding-noreply@google.com",
            "received_for": [inbox["address"]],
            "text": f"Confirm forwarding to {inbox['address']}: {url}",
            "authentication": {"dmarc": "pass"},
        }

    monkeypatch.setattr("mail.receiving.Receiving.get", received)
    body = json.dumps({"type": "email.received", "data": {"email_id": EMAIL_ID}}).encode()
    assert (
        await http.post("/v1/webhooks/resend", content=body, headers=email_signature(body))
    ).status_code == 202
    await service.sources.drain()
    saved = ForwardInbox.model_validate(
        (await http.get(f"/v1/organisations/{org}/connections/email", headers=HEADERS)).json()
    )
    assert saved.confirmation_url == url and not saved.receiving
    assert await database.collection("inventory").count_documents({"kind": "incident"}) == 0


async def test_dismissing_confirmation_is_scoped_and_does_not_verify_forwarding(
    source_app: Any,
) -> None:
    http, service, org = source_app.client, source_app.service, source_app.one
    path = f"/v1/organisations/{org}/connections/email"
    inbox = ForwardInbox.model_validate((await http.post(path, headers=HEADERS, json={})).json())
    url = "https://mail-settings.google.com/mail/vf-fixture?token=fixture"
    await service.sources.forwarding.received(inbox, url)
    body = {"confirmation_url": url}

    assert (
        await http.post(
            "/v1/organisations/org_unowned/connections/email/confirmation/dismiss",
            headers=HEADERS,
            json=body,
        )
    ).status_code == 403
    assert (
        await http.post(
            path + "/confirmation/dismiss",
            headers=HEADERS,
            json={"confirmation_url": url + "-stale"},
        )
    ).status_code == 409
    assert (await http.get(path, headers=HEADERS)).json()["confirmation_url"] == url

    for _ in range(2):
        dismissed = await http.post(path + "/confirmation/dismiss", headers=HEADERS, json=body)
        assert dismissed.status_code == 200, dismissed.text
        assert dismissed.json()["confirmation_url"] is None
        assert not dismissed.json()["receiving"]
    assert (
        await service.sources.inventory.get_connection(org, inbox.id)
    ).status is ConnectionStatus.SETUP_REQUIRED


class Connector:
    async def verify(
        self, code: str, verifier: str, installation_id: int | None
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        return {
            "installation_id": 123,
            "account_id": 44,
            "account_login": "fixture",
            "account_type": "Organization",
            "repository_selection": "selected",
            "permissions": {"secret_scanning_alerts": "read"},
            "events": ["secret_scanning_alert"],
        }, (
            {
                "repository_id": 456,
                "full_name": "fixture/api",
                "private": True,
                "default_branch": "main",
                "secret_scanning": "enabled",
            },
        )


async def connect_github(http: httpx.AsyncClient, org: str) -> httpx.Response:
    path = f"/v1/organisations/{org}/github/onboarding"
    begin = (await http.post(path, headers=HEADERS, json={})).json()
    session = path + "/" + begin["session"]["id"]
    result = await http.post(
        session + "/discover",
        headers=HEADERS,
        json={
            "state": begin["state"],
            "pkce_verifier": begin["pkce_verifier"],
            "code": "oauth-code",
            "installation_id": 123,
        },
    )
    assert result.status_code == 200, result.text
    return await http.post(session + "/complete", headers=HEADERS, json={})


async def test_github_is_wired_through_private_execution_and_installations_cannot_cross_workspaces(
    source_app: Any, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, service, org = source_app.client, source_app.service, source_app.one
    monkeypatch.setattr(
        service.sources,
        "github",
        lambda: GitHubOnboardingService(
            service.sources.github_store,
            cast(GitHubOnboardingConnector, Connector()),
            "kinu-fixture",
            "client",
            "https://app.example/callback",
            lambda: datetime.now(UTC),
        ),
    )
    payload = json.dumps({"action": "created", "installation": {"id": 123}}).encode()
    assert (
        await http.post(
            "/v1/webhooks/github",
            content=payload,
            headers=github_signature(payload, "installation"),
        )
    ).status_code == 202
    completed = await connect_github(http, org)
    assert completed.status_code == 200, completed.text
    connection = await InventoryStore(database).get_connection(org, "conn_github_123")
    assert connection.status is ConnectionStatus.READY
    assert connection.allowed_resources == ("fixture/api",)
    conflict = await connect_github(http, source_app.two)
    assert conflict.status_code == 409, conflict.text
    assert (await service.sources.github_store.find(123))["organisation_id"] == org
    alert = json.dumps(
        {
            "action": "created",
            "installation": {"id": 123},
            "repository": {"id": 456, "full_name": "fixture/api"},
            "alert": {
                "number": 7,
                "html_url": "https://github.com/fixture/api/security/secret-scanning/7",
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": "2026-11-05T00:00:00Z",
                "secret": "fixture-secret-must-never-be-stored",
            },
        }
    ).encode()
    proof = github_signature(alert, "secret_scanning_alert")
    assert (
        await http.post(
            "/v1/webhooks/github",
            content=alert,
            headers=proof | {"x-hub-signature-256": "sha256=wrong"},
        )
    ).status_code == 400
    for _ in range(2):
        assert (
            await http.post("/v1/webhooks/github", content=alert, headers=proof)
        ).status_code == 202
    assert await database.collection("source_deliveries").count_documents({}) == 1
    assert "fixture-secret-must-never-be-stored" not in str(
        await database.collection("source_deliveries").find_one({})
    )
    await service.sources.drain()
    assert await database.collection("inventory").count_documents({"kind": "incident"}) == 1
    assert await database.collection("runs").count_documents({}) == 0
    deleted = json.dumps({"action": "deleted", "installation": {"id": 123}}).encode()
    assert (
        await http.post(
            "/v1/webhooks/github",
            content=deleted,
            headers=github_signature(deleted, "installation"),
        )
    ).status_code == 202
    assert (
        await InventoryStore(database).get_connection(org, "conn_github_123")
    ).status is ConnectionStatus.DISABLED


@pytest.mark.parametrize("approved", [True, False])
async def test_queued_github_alert_rechecks_current_exposure_permission(
    source_app: Any, monkeypatch: pytest.MonkeyPatch, approved: bool
) -> None:
    sources, org = source_app.service.sources, source_app.one
    event = IngestionEvent(
        id="ingestion_queued_github",
        organisation_id=org,
        source="github-secret-scanning",
        source_event_id="fixture/api#7:queued",
        kind="credential-exposure-detected",
        observed_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        severity=Severity.CRITICAL,
        confidence=Confidence.VERIFIED,
        resource=SourceResource(
            connection_id="conn_github_123",
            repository="fixture/api",
            credential_id="credential_fixture",
            generation_id="generation_fixture",
        ),
        source_reference="https://github.com/fixture/api/security/secret-scanning/7",
    )
    calls: list[str] = []

    async def connection(*args: Any) -> Any:
        return SimpleNamespace(
            id="conn_github_123",
            archived_at=None,
            status=ConnectionStatus.READY,
            allowed_resources=("fixture/api",),
        )

    async def credential(*args: Any) -> Any:
        return SimpleNamespace(
            id="credential_fixture",
            archived_at=None,
            control_version="controls_current",
            active_generation_id="generation_fixture",
        )

    async def controls(*args: Any) -> Any:
        assert args[-1] == "controls_current"
        return SimpleNamespace(
            definition=SimpleNamespace(
                exposure_sources=(
                    SimpleNamespace(connection_id="conn_github_123", resource="fixture/api"),
                )
                if approved
                else ()
            )
        )

    async def rotate(*args: Any) -> None:
        calls.append("rotate")

    async def review(*args: Any) -> None:
        calls.append("review")

    monkeypatch.setattr(sources.inventory, "get_connection", connection)
    monkeypatch.setattr(sources.inventory, "get_credential", credential)
    monkeypatch.setattr(sources.inventory, "get_control_version", controls)
    monkeypatch.setattr(sources.automation, "ingest", rotate)
    monkeypatch.setattr(sources.assessment.incidents, "ingest", review)
    await sources.queue("github", event.id, {"event": event.model_dump(mode="json"), "exact": True})
    await sources.drain()
    assert calls == (["rotate"] if approved else ["review"])
