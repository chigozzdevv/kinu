from typing import Any

import httpx
import pytest
from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.resend.check import classify
from connectors.resend.client import Resend
from connectors.resend.config import definition
from connectors.resend.proof import Proof
from core.connection.provider import ProviderInput
from core.storage.database import Database
from executor.authorization import Authorization
from executor.receipts import Receipts
from executor.vault import Vault
from pydantic import SecretStr, ValidationError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (
            401,
            {
                "name": "restricted_api_key",
                "message": "This API key is restricted to only send emails.",
            },
            "sending_access",
        ),
        (
            401,
            {
                "name": "restricted_api_key",
                "message": "This API key is restricted to only send emails",
            },
            "sending_access",
        ),
        (200, {"object": "list", "data": []}, "full_access"),
        (400, {"name": "validation_error", "message": "API key is invalid"}, "rejected"),
        (401, {"name": "validation_error", "message": "API key is invalid"}, "rejected"),
        (403, {"name": "restricted_api_key", "message": "API key is not active"}, "rejected"),
        (403, {"name": "restricted_api_key", "message": "Another restriction"}, "unavailable"),
        (400, {"name": "validation_error", "message": "Invalid query"}, "unavailable"),
        (401, {"name": "missing_api_key", "message": "Missing API Key"}, "unavailable"),
        (429, {}, "unavailable"),
    ],
)
def test_resend_authentication_distinguishes_invalid_keys_from_denied_reads(
    status: int, payload: Any, expected: str
) -> None:
    assert classify(status, payload) == expected


def test_resend_connections_require_a_domain_and_cannot_expand_creation_permissions() -> None:
    domain = "0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c"
    body = {"display_name": "Resend", "platform": "resend", "token": "private", "domain_id": domain}
    value = ProviderInput.model_validate(body)
    assert value.http and value.http.create_credential.body == {
        "name": "${name}",
        "permission": "sending_access",
        "domain_id": domain,
    }
    assert "private" not in value.model_dump_json()
    for change in (
        {"domain_id": None},
        {"domain_id": "invalid"},
        {"http": definition().model_dump(mode="json")},
    ):
        with pytest.raises(ValidationError):
            ProviderInput.model_validate(body | change)


@pytest.mark.anyio
async def test_resend_proof_never_sends_mail_and_unknown_failures_stay_unavailable() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/inboxes"
        assert request.headers["authorization"] == "Bearer private-fixture"
        return httpx.Response(429, json={"name": "rate_limit_exceeded"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with SecretValue(b"private-fixture") as value:
            with pytest.raises(ConnectorError, match="did not confirm"):
                await Proof(http).check(value)


@pytest.mark.anyio
async def test_domain_discovery_follows_every_page_and_read_retries_never_replay_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    domain = "0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c"
    calls: list[httpx.Request] = []

    async def sleep(seconds: float) -> None:
        assert 0 <= seconds <= 3

    monkeypatch.setattr("connectors.resend.client.sleep", sleep)

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["permission"] == "sending_access"
            return httpx.Response(429, headers={"Retry-After": "1"})
        assert request.url.path == "/domains"
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(
                200,
                json={
                    "has_more": True,
                    "data": [
                        {"id": domain, "name": "verified.example", "status": "verified"},
                    ],
                },
            )
        assert after == domain
        return httpx.Response(
            200,
            json={
                "has_more": False,
                "data": [
                    {"id": "pending", "name": "pending.example", "status": "pending"},
                ],
            },
        )

    database = Database("mongodb://localhost:27017")
    vault = Vault(database, SecretStr("test-material-at-least-thirty-two-characters"))
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            provider = Resend(Authorization(vault), Receipts(vault), http)
            with SecretValue(b"management-fixture") as token:
                assert await provider.domains(token) == (
                    {"id": domain, "name": "verified.example"},
                )
            assert len(calls) == 3
            with pytest.raises(ConnectorError, match="429"):
                await provider._create(definition(domain), {"name": "test"}, {})
            assert len(calls) == 4
    finally:
        await database.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"has_more": True, "data": []},
        {"has_more": "false", "data": []},
        {"has_more": False, "data": ["invalid"]},
    ],
)
async def test_domain_discovery_refuses_unverifiable_or_truncated_metadata(payload: Any) -> None:
    database = Database("mongodb://localhost:27017")
    vault = Vault(database, SecretStr("test-material-at-least-thirty-two-characters"))
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        ) as http:
            provider = Resend(Authorization(vault), Receipts(vault), http)
            with SecretValue(b"management-fixture") as token, pytest.raises(ConnectorError):
                await provider.domains(token)
    finally:
        await database.close()
