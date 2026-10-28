import asyncio
from typing import Any

import httpx
import pytest
from connectors.base.errors import ConnectorError
from connectors.http.transport import PublicTransport, public_address


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
