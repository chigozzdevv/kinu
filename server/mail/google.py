import asyncio
import base64
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from core.errors import ResourceConflictError
from pydantic import SecretStr

from mail.models import SCOPE, GoogleConfig, Grant


class ReconnectError(ResourceConflictError):
    pass


class RequestError(ResourceConflictError):
    def __init__(self, status: int) -> None:
        super().__init__("Gmail request failed; try again")
        self.status = status


class Google:
    def __init__(self, config: GoogleConfig, client: httpx.AsyncClient) -> None:
        self.config, self.client = config, client

    def begin(self, callback: str, state: str) -> tuple[str, str]:
        if not self.config.ready:
            raise ResourceConflictError("Gmail is not configured for this deployment")
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.config.client_id,
                "redirect_uri": callback,
                "response_type": "code",
                "scope": SCOPE,
                "state": state,
                "access_type": "offline",
                "prompt": "consent select_account",
                "include_granted_scopes": "false",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        ), verifier

    async def exchange(self, code: str, verifier: str, callback: str) -> Grant:
        value = await self.request(
            "POST",
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": callback,
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret.get_secret_value(),
            },
        )
        if set(str(value.get("scope", "")).split()) != {SCOPE} or not value.get("refresh_token"):
            raise ReconnectError("Approve Gmail read access to connect this mailbox")
        return self._grant(value)

    async def refresh(self, grant: Grant) -> Grant:
        value = await self.request(
            "POST",
            "https://oauth2.googleapis.com/token",
            data={
                "refresh_token": grant.refresh_token.get_secret_value(),
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret.get_secret_value(),
            },
        )
        if "scope" in value and set(str(value["scope"]).split()) != {SCOPE}:
            raise ReconnectError("Reconnect Gmail to restore read access")
        return self._grant(value, grant.refresh_token)

    async def api(self, grant: Grant, path: str, **params: Any) -> dict[str, Any]:
        if path not in {"profile", "messages"} and not re.fullmatch(
            r"messages/[A-Za-z0-9_-]{1,128}", path
        ):
            raise ValueError("unsupported Gmail resource")
        return await self.request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
            headers={"Authorization": "Bearer " + grant.access_token.get_secret_value()},
            params=params,
        )

    async def request(self, method: str, url: str, **params: Any) -> dict[str, Any]:
        async with asyncio.timeout(15), self.client.stream(method, url, **params) as response:
            content = bytearray()
            async for part in response.aiter_bytes():
                content.extend(part)
                if len(content) > 1_048_576:
                    raise ResourceConflictError("Gmail response is too large")
            try:
                value = json.loads(content)
            except (ValueError, UnicodeError):
                raise RequestError(response.status_code) from None
            if not isinstance(value, dict):
                raise ResourceConflictError("Gmail returned an invalid response")
            details = value.get("error")
            reasons = (
                {item.get("reason") for item in details.get("errors", []) if isinstance(item, dict)}
                if isinstance(details, dict)
                else set()
            )
            if response.status_code == 401 or (
                response.status_code == 403
                and reasons.intersection({"authError", "insufficientPermissions"})
            ):
                raise ReconnectError("Reconnect Gmail to restore read access")
            if response.status_code != 200:
                if response.status_code == 400 and details == "invalid_grant":
                    raise ReconnectError("Reconnect Gmail to restore read access")
                raise RequestError(response.status_code)
        return value

    @staticmethod
    def _grant(value: dict[str, Any], refresh: SecretStr | None = None) -> Grant:
        try:
            seconds = int(value.get("expires_in", 0))
        except (ValueError, TypeError):
            raise ReconnectError("Gmail returned an incomplete authorization") from None
        token = value.get("access_token")
        renewal = value.get("refresh_token") or (refresh.get_secret_value() if refresh else None)
        kind = value.get("token_type", "Bearer")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 8192
            or not isinstance(renewal, str)
            or not 1 <= len(renewal) <= 8192
            or not isinstance(kind, str)
            or kind.casefold() != "bearer"
            or not 1 <= seconds <= 86400
        ):
            raise ReconnectError("Gmail returned an incomplete authorization")
        return Grant(
            access_token=SecretStr(token),
            refresh_token=SecretStr(renewal),
            expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
        )
