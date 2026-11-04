import base64
import hashlib
import json
import math
import secrets
from time import time
from typing import Any
from urllib.parse import urlencode

import httpx
from connectors.base.errors import ConnectorAuthenticationError
from core.errors import ResourceSetupRequiredError

from hosting.models import Platform


class OAuth:
    def __init__(
        self, http: httpx.AsyncClient, platform: Platform, settings: dict[str, Any], origin: str
    ) -> None:
        self.http, self.platform, self.settings = http, platform, settings
        self.callback = origin + f"/connections/{platform}/callback"

    def begin(self, state: str) -> tuple[str, str]:
        if not all(self.settings.get(key) for key in ("client_id", "client_secret")):
            raise ResourceSetupRequiredError(
                "This hosting connection is not configured on this Kinu installation"
            )
        verifier = secrets.token_urlsafe(48)
        if self.platform == "vercel":
            slug = self.settings.get("slug")
            if not slug or any(
                value not in "abcdefghijklmnopqrstuvwxyz0123456789-" for value in slug
            ):
                raise ResourceSetupRequiredError(
                    "The Vercel connection is not configured on this Kinu installation"
                )
            return (
                "https://vercel.com/integrations/"
                + slug
                + "/new?"
                + urlencode({"state": state, "source": "external"}),
                verifier,
            )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        return (
            "https://backboard.railway.com/oauth/auth?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": self.settings["client_id"],
                    "redirect_uri": self.callback,
                    "scope": "openid offline_access project:member",
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "prompt": "consent",
                }
            ),
            verifier,
        )

    async def exchange(self, code: str, verifier: str) -> dict[str, Any]:
        fields = {"code": code, "redirect_uri": self.callback}
        if self.platform == "railway":
            fields |= {"grant_type": "authorization_code", "code_verifier": verifier}
        return await self.request(fields)

    async def refresh(self, grant: dict[str, Any]) -> dict[str, Any]:
        if not grant.get("refresh_token"):
            raise ConnectorAuthenticationError("Reconnect this hosting account")
        updated = await self.request(
            {"grant_type": "refresh_token", "refresh_token": grant["refresh_token"]}
        )
        return (
            grant
            | updated
            | {"refresh_token": updated.get("refresh_token") or grant["refresh_token"]}
        )

    async def request(self, fields: dict[str, str]) -> dict[str, Any]:
        railway = self.platform == "railway"
        url = (
            "https://backboard.railway.com/oauth/token"
            if railway
            else "https://api.vercel.com/v2/oauth/access_token"
        )
        auth = (
            httpx.BasicAuth(self.settings["client_id"], self.settings["client_secret"])
            if railway
            else None
        )
        if not railway:
            fields |= {
                "client_id": self.settings["client_id"],
                "client_secret": self.settings["client_secret"],
            }
        try:
            async with self.http.stream(
                "POST", url, data=fields, auth=auth, timeout=20, follow_redirects=False
            ) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise ConnectorAuthenticationError(
                            "Hosting authorization response is too large"
                        )
                if not response.is_success:
                    raise ConnectorAuthenticationError("Hosting authorization failed; reconnect")
            payload = json.loads(data)
            if payload.get("token_type", "Bearer").lower() != "bearer":
                raise ValueError
            if railway:
                scopes = payload.get("scope")
                if scopes is not None and "project:member" not in scopes.split():
                    raise ValueError
                if fields.get("grant_type") == "authorization_code" and (
                    not isinstance(payload.get("refresh_token"), str)
                    or not scopes
                    or "project:member" not in scopes.split()
                ):
                    raise ValueError
            token = payload.get("access_token")
            if not isinstance(token, str) or not 1 <= len(token) <= 16384:
                raise ValueError
            result = {
                key: payload[key]
                for key in ("access_token", "refresh_token", "team_id", "configuration_id")
                if isinstance(payload.get(key), str)
            }
            expires = payload.get("expires_in")
            if railway or expires is not None:
                seconds = float(expires)
                if not math.isfinite(seconds) or not 0 < seconds <= 31_536_000:
                    raise ValueError
                result["expires_at"] = int(time()) + int(seconds)
            return result
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise ConnectorAuthenticationError("Hosting authorization failed; reconnect") from None
