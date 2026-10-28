import asyncio
import socket
from unittest.mock import AsyncMock

import pytest
from browser.driver import BrowserDriver
from browser.launch import pins
from contracts import BrowserActionKind, BrowserPolicy
from core.errors import ResourceConflictError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["::1"]])
async def test_browser_pins_reject_private_and_mixed_dns(
    monkeypatch: pytest.MonkeyPatch, addresses: list[str]
) -> None:
    resolver = AsyncMock(
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 443)) for value in addresses
        ]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    with pytest.raises(ResourceConflictError, match="public provider"):
        await pins(("provider.example",))


async def test_browser_pins_lock_exact_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = AsyncMock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    assert (
        await pins(("provider.example",))
        == "MAP provider.example 8.8.8.8, MAP * ~NOTFOUND, EXCLUDE localhost"
    )
    with pytest.raises(ResourceConflictError, match="exact hostnames"):
        await pins(("*.provider.example",))


async def test_egress_guard_covers_the_entire_browser_context() -> None:
    page = AsyncMock()
    driver = BrowserDriver(
        page,
        BrowserPolicy(
            allowed_domains=("provider.example",),
            allowed_actions=frozenset({BrowserActionKind.CLICK}),
        ),
    )
    await driver.enforce_egress()
    page.context.route.assert_awaited_once()
    page.route.assert_not_awaited()
