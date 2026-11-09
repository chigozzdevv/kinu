import asyncio
import json
import logging
from typing import Any

import httpx

from connectors.base.errors import ConnectorError


class Client:
    def __init__(
        self, http: httpx.AsyncClient, token: str, origin: str, query: dict[str, str] | None = None
    ) -> None:
        if origin not in {
            "https://api.vercel.com",
            "https://api.render.com",
            "https://backboard.railway.com",
        }:
            raise ConnectorError("host-origin", "Unsupported hosting API")
        self.http, self.token, self.origin, self.query = http, token, origin, query or {}

    async def call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ConnectorError("host-path", "Invalid hosting operation")
        for attempt in range(4):
            try:
                async with self.http.stream(
                    method,
                    self.origin + path,
                    headers={"Authorization": "Bearer " + self.token},
                    params=self.query | (query or {}),
                    json=body,
                    timeout=20,
                    follow_redirects=False,
                ) as response:
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2_097_152:
                            raise ConnectorError(
                                "host-limit", "Hosting response exceeded its limit"
                            )
                    challenged = (
                        response.status_code == 403
                        and b"Vercel Security Checkpoint" in data
                    )
                    if challenged and attempt < 3:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    if not response.is_success:
                        logging.getLogger("kinu.connectors").warning(
                            "hosting API %s %s rejected with %s: %s",
                            method,
                            path,
                            response.status_code,
                            bytes(data[:400]).decode("utf-8", "replace"),
                        )
                        raise ConnectorError(
                            "host-request",
                            f"Hosting API rejected the request ({response.status_code})",
                        )
                    if response.status_code == 204 or not data:
                        return {}
                    return json.loads(data)
            except (httpx.HTTPError, ValueError):
                raise ConnectorError("host-unavailable", "Hosting API is unavailable") from None
        raise ConnectorError("host-request", "Hosting API rejected the request (403)")

    async def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        result = await self.call(
            "POST", "/graphql/v2", body={"query": query, "variables": variables}
        )
        if (
            not isinstance(result, dict)
            or result.get("errors")
            or not isinstance(result.get("data"), dict)
        ):
            logging.getLogger("kinu.connectors").warning(
                "hosting graphql rejected: %s", (result or {}).get("errors")
            )
            raise ConnectorError("host-query", "Hosting API rejected this operation")
        data: dict[str, Any] = result["data"]
        return data
