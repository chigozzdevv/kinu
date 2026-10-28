import json
from asyncio import sleep
from typing import Any

import httpx

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.http.transport import PublicTransport
from connectors.resend.check import State, backoff, classify


class Proof:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http

    async def check(self, value: SecretValue) -> State:
        client = self.http or httpx.AsyncClient(transport=PublicTransport(), trust_env=False)
        try:
            for attempt in range(3):
                status, payload, retry = await self.read(client, value)
                if status == 429 and attempt < 2:
                    await sleep(backoff(retry))
                    continue
                result = classify(status, payload)
                if result == "unavailable":
                    raise ValueError
                return result
            raise AssertionError("Resend proof did not finish")
        except (ValueError, httpx.HTTPError):
            raise ConnectorError(
                "resend-proof", "Resend did not confirm this key's status"
            ) from None
        finally:
            if self.http is None:
                await client.aclose()

    @staticmethod
    async def read(client: httpx.AsyncClient, value: SecretValue) -> tuple[int, Any, str | None]:
        async with client.stream(
            "GET",
            "https://api.resend.com/inboxes",
            params={"limit": 1},
            headers={"Authorization": "Bearer " + value.bytes().decode(), "User-Agent": "kinu/0.1"},
            timeout=10,
            follow_redirects=False,
        ) as response:
            content = bytearray()
            async for part in response.aiter_bytes():
                content.extend(part)
                if len(content) > 16384:
                    raise ValueError
            return response.status_code, json.loads(content), response.headers.get("Retry-After")
