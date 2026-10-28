from typing import Any

import httpx
from pydantic import SecretStr

from core.errors import ResourceConflictError, ResourceNotFoundError, ResourceSetupRequiredError


class PrivateClient:
    def __init__(self, http: httpx.AsyncClient, origin: str, token: SecretStr) -> None:
        self.http, self.origin, self.token = http, origin.rstrip("/"), token

    async def call(self, path: str, data: dict[str, Any]) -> Any:
        try:
            response = await self.http.post(
                self.origin + path,
                json=data,
                headers={"Authorization": "Bearer " + self.token.get_secret_value()},
                timeout=30,
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
