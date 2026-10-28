import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from connectors.base import ConnectorContext, SecretValue
from connectors.base.errors import AmbiguousMutationError, ConnectorError
from connectors.http import HttpProviderConnector
from connectors.http.transport import PublicTransport, public_address
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
)
from testkit import make_http_provider_api, make_run


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "::ffff:127.0.0.1", "224.0.0.1"]
)
def test_non_public_addresses_are_rejected(address: str) -> None:
    assert not public_address(address)


@pytest.mark.anyio
async def test_transport_pins_public_dns_and_keeps_host_and_tls_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(*args: Any, **kwargs: Any) -> list[Any]:
        return [(2, 1, 6, "", ("1.1.1.1", 443))]

    async def send(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "1.1.1.1"
        assert request.headers["host"] == "api.example"
        assert request.extensions["sni_hostname"] == "api.example"
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    async with PublicTransport() as transport:
        monkeypatch.setattr(transport._transport, "handle_async_request", send)
        response = await transport.handle_async_request(
            httpx.Request("GET", "https://api.example/keys")
        )
        assert response.status_code == 200


@pytest.mark.anyio
async def test_mixed_public_and_private_dns_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(*args: Any, **kwargs: Any) -> list[Any]:
        return [(2, 1, 6, "", ("1.1.1.1", 443)), (2, 1, 6, "", ("10.0.0.1", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    async with PublicTransport() as transport:
        with pytest.raises(ConnectorError, match="only to public"):
            await transport.handle_async_request(httpx.Request("GET", "https://api.example/keys"))


@pytest.mark.anyio
async def test_reconciliation_uses_nested_metadata_and_rejects_unknown_outcomes() -> None:
    class Secrets:
        async def access_for(self, connection: Connection, version: str) -> SecretValue:
            return SecretValue(b"fixture-management")

    matches = [{"data": {"id": "orphan", "name": "kinu-attempt"}}]
    deleted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(200, json={"result": matches})

    api = make_http_provider_api()
    api = api.model_copy(
        update={
            "list_credentials": api.list_credentials.model_copy(
                update={"provider_id_field": "data.id", "name_field": "data.name"}
            )
        }
    )
    connection = Connection(
        id="provider_one",
        organisation_id="org_one",
        platform="provider",
        display_name="Provider",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY,
        authorization_reference="secret://management/versions/one",
        capabilities=frozenset({"provider.createCredential"}),
        allowed_resources=("provider:keys",),
        http=api,
        status=ConnectionStatus.READY,
        region="global",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    context = ConnectorContext(
        "request_one", "worker_one", connection, make_run(), datetime.now(UTC), "attempt_one"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = HttpProviderConnector(Secrets(), client)
        await provider.reconcile(
            "provider.createCredential",
            {"name": "kinu-attempt"},
            {"name": "kinu-attempt", "before_ids": ()},
            context,
        )
        assert deleted == ["/v3/api_keys/orphan"]
        matches.clear()
        with pytest.raises(AmbiguousMutationError):
            await provider.reconcile(
                "provider.createCredential",
                {"name": "kinu-attempt"},
                {"name": "kinu-attempt", "before_ids": ()},
                context,
            )
