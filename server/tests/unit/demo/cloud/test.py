import asyncio
from importlib import import_module
from typing import Any

import pytest

provider = import_module("demo.handler")


def test_warm_provider_can_alternate_iam_and_http_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    async def administer(aws: Any, event: object) -> dict[str, object]:
        await asyncio.sleep(0)
        return {"demo": True}

    monkeypatch.setattr(provider, "administer", administer)
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/",
        "rawQueryString": "",
        "headers": {"host": "provider.example"},
        "requestContext": {"http": {"method": "GET", "path": "/", "sourceIp": "127.0.0.1"}},
        "isBase64Encoded": False,
    }
    try:
        for _ in range(2):
            assert provider.handler({"operation": "metadata"}, None) == {"demo": True}
            response = provider.handler(event, None)
            assert response["statusCode"] == 200
            assert "Demo provider" in response["body"]
    finally:
        asyncio.set_event_loop(None)
