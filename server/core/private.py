import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import SecretStr

from core.errors import ResourceConflictError, ResourceNotFoundError, ResourceSetupRequiredError


class PrivateClient:
    def __init__(self, http: httpx.AsyncClient, origin: str, token: SecretStr) -> None:
        self.http, self.origin, self.token = http, origin.rstrip("/"), token

    async def call(self, path: str, data: dict[str, Any], *, timeout: float = 30) -> Any:
        try:
            response = await self.http.post(
                self.origin + path,
                json=data,
                headers={"Authorization": "Bearer " + self.token.get_secret_value()},
                timeout=timeout,
                follow_redirects=False,
            )
            if len(response.content) > 1_048_576:
                raise ResourceConflictError("Private service response exceeded its limit")
            if response.status_code == 404:
                raise ResourceNotFoundError("Resource is unavailable")
            if response.status_code == 503:
                raise ResourceSetupRequiredError("Integration setup is incomplete")
            if not response.is_success:
                raise ResourceConflictError("Connection request failed; check its configuration")
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise ResourceConflictError("Private service is unavailable; try again") from None

    async def stream(
        self, path: str, data: dict[str, Any], *, timeout: float = 60
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            async with self.http.stream(
                "POST",
                self.origin + path,
                json=data,
                headers={"Authorization": "Bearer " + self.token.get_secret_value()},
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if not response.is_success or "application/x-ndjson" not in response.headers.get(
                    "content-type", ""
                ):
                    raise ResourceConflictError("Private stream is unavailable")
                pending, size, lines = b"", 0, 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 1_048_576:
                        raise ResourceConflictError("Private stream exceeded its limit")
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        lines += 1
                        if len(line) > 32768 or lines > 512:
                            raise ResourceConflictError("Private stream exceeded its limit")
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise ValueError
                        yield value
                    if len(pending) > 32768:
                        raise ResourceConflictError("Private stream exceeded its limit")
                if pending:
                    raise ResourceConflictError("Private stream ended unexpectedly")
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ResourceConflictError("Private stream is unavailable; try again") from None
