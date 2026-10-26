import json
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from connectors.base import SecretValue
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionStatus,
)
from core.errors import ResourceConflictError


class SecretReader(Protocol):
    async def access(self, version: str) -> SecretValue: ...


class BrowserAuthBroker:
    def __init__(self, secrets: SecretReader) -> None:
        self._secrets = secrets

    async def storage_state(
        self,
        connection: Connection,
        allowed_domains: tuple[str, ...],
    ) -> dict[str, Any]:
        if (
            connection.interface is not ConnectionInterface.BROWSER
            or connection.authorization is not ConnectionAuthorization.BROWSER_SESSION
        ):
            raise ResourceConflictError("browser authentication requires a browser connection")
        expired = (
            connection.authorization_expires_at is not None
            and connection.authorization_expires_at <= datetime.now(UTC)
        )
        if (
            connection.status is not ConnectionStatus.READY
            or connection.authorization_reference is None
            or expired
        ):
            raise ResourceConflictError(
                "provider browser authentication requires a ready connection"
            )
        secret = await self._secrets.access(connection.authorization_reference)
        try:
            try:
                state = json.loads(secret.bytes())
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ResourceConflictError("browser authentication state is invalid") from error
        finally:
            secret.clear()
        return validate_storage_state(state, allowed_domains)


def validate_storage_state(state: Any, allowed_domains: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ResourceConflictError("browser authentication state must be an object")
    cookies = state.get("cookies", [])
    origins = state.get("origins", [])
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise ResourceConflictError("browser authentication state has invalid collections")
    for cookie in cookies:
        if not isinstance(cookie, dict) or not _allowed_cookie(cookie, allowed_domains):
            raise ResourceConflictError("browser authentication cookie is outside the allowlist")
    for origin in origins:
        if not isinstance(origin, dict) or not _allowed_origin(origin, allowed_domains):
            raise ResourceConflictError("browser authentication origin is outside the allowlist")
    return state


def filter_storage_state(state: Any, allowed_domains: tuple[str, ...]) -> dict[str, Any]:
    # SSO login leaves identity-provider cookies behind; only the provider's own
    # session may be persisted for later rotations.
    if not isinstance(state, dict):
        raise ResourceConflictError("browser authentication state must be an object")
    cookies = state.get("cookies", [])
    origins = state.get("origins", [])
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise ResourceConflictError("browser authentication state has invalid collections")
    return {
        **state,
        "cookies": [
            cookie
            for cookie in cookies
            if isinstance(cookie, dict) and _allowed_cookie(cookie, allowed_domains)
        ],
        "origins": [
            origin
            for origin in origins
            if isinstance(origin, dict) and _allowed_origin(origin, allowed_domains)
        ],
    }


def _allowed_cookie(cookie: dict[str, Any], allowed: tuple[str, ...]) -> bool:
    domain = cookie.get("domain")
    name = cookie.get("name")
    value = cookie.get("value")
    if not all(isinstance(item, str) and item for item in (domain, name, value)):
        return False
    assert isinstance(domain, str)
    return _allowed_domain(domain.lstrip(".").lower(), allowed)


def _allowed_origin(origin: dict[str, Any], allowed: tuple[str, ...]) -> bool:
    value = origin.get("origin")
    storage = origin.get("localStorage", [])
    if not isinstance(value, str) or not isinstance(storage, list):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(
        parsed.hostname and _allowed_domain(parsed.hostname.lower(), allowed)
    )


def is_domain_pattern(value: str) -> bool:
    return bool(value) and "." in value and all(ch.isalnum() or ch in ".-*" for ch in value)


def domain_covered(need: str, allowed: tuple[str, ...]) -> bool:
    needed = need.lower().rstrip(".")
    allowed_norm = tuple(item.lower().rstrip(".") for item in allowed)
    if needed in allowed_norm:
        return True
    host = needed[2:] if needed.startswith("*.") else needed
    if _allowed_domain(host, allowed):
        return True
    if not needed.startswith("*."):
        return False
    suffix = needed[2:]
    for pattern in allowed_norm:
        if pattern.startswith("*."):
            parent = pattern[2:]
            if suffix == parent or suffix.endswith("." + parent):
                return True
        elif suffix == pattern or suffix.endswith("." + pattern):
            return True
    return False


def _allowed_domain(hostname: str, allowed: tuple[str, ...]) -> bool:
    for pattern in allowed:
        expected = pattern.lower().rstrip(".")
        if expected.startswith("*."):
            suffix = expected[2:]
            if hostname.endswith("." + suffix) and hostname != suffix:
                return True
        elif hostname == expected:
            return True
    return False
