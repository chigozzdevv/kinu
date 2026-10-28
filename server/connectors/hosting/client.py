import json
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
                        raise ConnectorError("host-limit", "Hosting response exceeded its limit")
                if not response.is_success:
                    raise ConnectorError(
                        "host-request", f"Hosting API rejected the request ({response.status_code})"
                    )
                if response.status_code == 204 or not data:
                    return {}
                return json.loads(data)
        except (httpx.HTTPError, ValueError):
            raise ConnectorError("host-unavailable", "Hosting API is unavailable") from None

    async def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        result = await self.call(
            "POST", "/graphql/v2", body={"query": query, "variables": variables}
        )
        if (
            not isinstance(result, dict)
            or result.get("errors")
            or not isinstance(result.get("data"), dict)
        ):
            raise ConnectorError("host-query", "Hosting API rejected this operation")
        data: dict[str, Any] = result["data"]
        return data
