import asyncio
from collections.abc import Callable
from time import monotonic

import httpx
from jwt import PyJWK, PyJWTError

from core.errors import AuthenticationError, IdentityUnavailableError


class SigningKeys:
    def __init__(
        self,
        url: str,
        http: httpx.AsyncClient,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._url = url
        self._http = http
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._expires = 0.0
        self._refreshed = float("-inf")
        self._unavailable = False
        self._lock = asyncio.Lock()

    async def get(self, key_id: str) -> PyJWK:
        if self._clock() < self._expires and key_id in self._keys:
            return self._keys[key_id]
        async with self._lock:
            now = self._clock()
            if now < self._expires and key_id in self._keys:
                return self._keys[key_id]
            if now - self._refreshed < 10:
                if self._unavailable:
                    raise IdentityUnavailableError("identity signing keys are unavailable")
                raise AuthenticationError("identity signing key is unknown")
            self._refreshed = now
            try:
                self._keys = await self._load()
            except IdentityUnavailableError:
                self._unavailable = True
                raise
            self._unavailable = False
            self._expires = self._clock() + 300
            key = self._keys.get(key_id)
            if key is None:
                raise AuthenticationError("identity signing key is unknown")
            return key

    async def _load(self) -> dict[str, PyJWK]:
        try:
            response = await self._http.get(self._url, timeout=5, follow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 262_144:
                raise ValueError("signing key response is too large")
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
                raise ValueError("signing key response is invalid")
            keys: dict[str, PyJWK] = {}
            for item in body["keys"]:
                if not isinstance(item, dict) or item.get("kty") != "RSA":
                    continue
                if item.get("use", "sig") != "sig" or item.get("alg", "RS256") != "RS256":
                    continue
                key_id = item.get("kid")
                if not isinstance(key_id, str) or not key_id or key_id in keys:
                    raise ValueError("signing key identifier is invalid")
                keys[key_id] = PyJWK.from_dict(item, algorithm="RS256")
            if not keys:
                raise ValueError("no supported signing keys")
            return keys
        except (httpx.HTTPError, PyJWTError, ValueError, TypeError) as error:
            raise IdentityUnavailableError("identity signing keys are unavailable") from error
