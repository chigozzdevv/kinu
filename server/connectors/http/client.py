import base64
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    HttpAuth,
    HttpAuthScheme,
    HttpOperation,
    HttpProviderApi,
)

from connectors.base import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import AmbiguousMutationError, ConnectorError
from connectors.secrets.store import SecretReader


class HttpProviderConnector:
    tools = frozenset(
        {
            "provider.listCredentialMetadata",
            "provider.createCredential",
            "provider.getCredentialStatus",
            "provider.revokeCredential",
            "provider.testCredential",
        }
    )

    def __init__(
        self,
        secrets: SecretReader,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._secrets = secrets
        self._client = client

    async def metadata(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        api = _api(connection)
        headers = await self._headers(connection, api.auth)
        return tuple(
            self._metadata(api.list_credentials, item) for item in await self._list(api, headers)
        )

    async def execute(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> ConnectorResponse:
        api = _api(context.connection)
        if tool == "provider.listCredentialMetadata":
            metadata = await self.metadata(context.connection)
            return ConnectorResponse(result={"credentials": list(metadata)})
        headers = await self._headers(context.connection, api.auth)
        if tool == "provider.getCredentialStatus":
            key_id = _string(payload, "provider_id")
            keys = await self._list(api, headers)
            field = api.list_credentials.provider_id_field
            match = next((key for key in keys if field and key.get(field) == key_id), None)
            return ConnectorResponse(
                result={
                    "exists": match is not None,
                    "credential": (
                        self._metadata(api.list_credentials, match) if match is not None else None
                    ),
                }
            )
        if tool == "provider.createCredential":
            return await self._create(api, payload, headers)
        if tool == "provider.revokeCredential":
            key_id = _string(payload, "provider_id")
            response = await self._call(
                api, api.revoke_credential, headers, {"provider_id": key_id}
            )
            _expected(response, set(api.revoke_credential.success_statuses))
            return ConnectorResponse(result={"provider_id": key_id, "revoked": True})
        raise ConnectorError("unsupported-tool", f"provider does not support {tool}")

    async def test_credential(
        self,
        connection: Connection,
        value: SecretValue,
    ) -> int:
        api = _api(connection)
        operation = api.test_credential
        auth = api.credential_auth
        if operation is None or auth is None:
            raise ConnectorError(
                "credential-test-unavailable",
                "provider connection has no credential authentication test",
            )
        headers = _value_headers(value, auth)
        response = await self._call(api, operation, headers, {})
        return response.status_code

    async def credential_identity(
        self,
        connection: Connection,
        secret_connection: Connection,
        secret_reference: str,
    ) -> str:
        api = _api(connection)
        operation = api.test_credential
        auth = api.credential_auth
        if operation is None or auth is None or operation.provider_id_field is None:
            raise ConnectorError(
                "credential-identity-unavailable",
                "provider connection has no credential identity test",
            )
        with await self._secrets.access_for(secret_connection, secret_reference) as value:
            response = await self._call(api, operation, _value_headers(value, auth), {})
        _expected(response, set(operation.success_statuses))
        payload = _object(response.json(), "credential identity")
        identity = _dig(payload, operation.provider_id_field)
        if not isinstance(identity, str) or not identity:
            raise ConnectorError(
                "invalid-provider-response",
                "provider credential identity response has no credential identifier",
            )
        return identity

    async def prepare(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> dict[str, str | int | bool | tuple[str, ...]]:
        if tool != "provider.createCredential":
            return {}
        api = _api(context.connection)
        headers = await self._headers(context.connection, api.auth)
        field = api.list_credentials.provider_id_field
        before = tuple(
            sorted(
                key_id
                for key in await self._list(api, headers)
                if field and isinstance(key_id := key.get(field), str)
            )
        )
        return {
            "name": _string(payload, "name"),
            "before_ids": before,
        }

    async def reconcile(
        self,
        tool: str,
        payload: dict[str, Any],
        state: dict[str, str | int | bool | tuple[str, ...]],
        context: ConnectorContext,
    ) -> ConnectorResponse | None:
        if tool != "provider.createCredential":
            return None
        api = _api(context.connection)
        name = _string(payload, "name")
        before = state.get("before_ids")
        if state.get("name") != name or not isinstance(before, tuple):
            raise AmbiguousMutationError("provider reconciliation checkpoint is invalid")
        headers = await self._headers(context.connection, api.auth)
        after = await self._list(api, headers)
        id_field = api.list_credentials.provider_id_field
        name_field = api.list_credentials.name_field or api.create_credential.name_field
        candidates = [
            key
            for key in after
            if name_field
            and id_field
            and key.get(name_field) == name
            and isinstance(key.get(id_field), str)
            and key.get(id_field) not in before
        ]
        if len(candidates) > 1:
            raise AmbiguousMutationError(
                "stale provider create has multiple attributable cleanup candidates"
            )
        if candidates and id_field:
            key_id = candidates[0][id_field]
            assert isinstance(key_id, str)
            response = await self._call(
                api, api.revoke_credential, headers, {"provider_id": key_id}
            )
            _expected(response, set(api.revoke_credential.success_statuses) | {404})
        return None

    async def _create(
        self,
        api: HttpProviderApi,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> ConnectorResponse:
        name = _string(payload, "name")
        id_field = api.create_credential.provider_id_field
        secret_field = api.create_credential.secret_field
        list_id = api.list_credentials.provider_id_field
        if id_field is None or secret_field is None or list_id is None:
            raise ConnectorError("invalid-provider-api", "create declaration is incomplete")
        before = {
            key_id
            for key in await self._list(api, headers)
            if isinstance(key_id := key.get(list_id), str)
        }
        try:
            response = await self._call(api, api.create_credential, headers, payload)
        except httpx.TimeoutException as error:
            await self._reconcile_ambiguous(api, name, before, headers)
            raise AmbiguousMutationError("provider create timed out and was reconciled") from error
        _expected(response, set(api.create_credential.success_statuses))
        value = _object(response.json(), "provider create")
        key_id = _dig(value, id_field)
        secret = _dig(value, secret_field)
        if not isinstance(key_id, str) or not isinstance(secret, str):
            raise ConnectorError(
                "invalid-provider-response", "provider returned no key ID or secret"
            )
        scopes = payload.get("scopes")
        return ConnectorResponse(
            result={
                "provider_id": key_id,
                "name": name,
                "scopes": scopes if isinstance(scopes, list) else [],
            },
            secret=SecretValue(secret.encode()),
        )

    async def _reconcile_ambiguous(
        self,
        api: HttpProviderApi,
        name: str,
        before: set[str],
        headers: dict[str, str],
    ) -> None:
        after = await self._list(api, headers)
        id_field = api.list_credentials.provider_id_field
        name_field = api.list_credentials.name_field or api.create_credential.name_field
        candidates = [
            key
            for key in after
            if name_field
            and id_field
            and key.get(name_field) == name
            and str(key.get(id_field)) not in before
        ]
        if len(candidates) != 1 or not id_field:
            raise AmbiguousMutationError(
                "provider create outcome could not be attributed to exactly one orphan"
            )
        key_id = candidates[0].get(id_field)
        if not isinstance(key_id, str):
            raise AmbiguousMutationError("provider orphan has no provider ID")
        response = await self._call(api, api.revoke_credential, headers, {"provider_id": key_id})
        _expected(response, set(api.revoke_credential.success_statuses) | {404})

    async def _list(self, api: HttpProviderApi, headers: dict[str, str]) -> list[dict[str, Any]]:
        response = await self._call(api, api.list_credentials, headers, {})
        _expected(response, set(api.list_credentials.success_statuses))
        value = response.json()
        items = value
        if api.list_credentials.list_items:
            items = (
                _dig(value, api.list_credentials.list_items) if isinstance(value, dict) else None
            )
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ConnectorError(
                "invalid-provider-response", "provider returned invalid key metadata"
            )
        return items

    @staticmethod
    def _metadata(operation: HttpOperation, item: dict[str, Any]) -> dict[str, Any]:
        provider_id = _dig(item, operation.provider_id_field or "")
        if not isinstance(provider_id, str) or not provider_id:
            raise ConnectorError(
                "invalid-provider-response", "provider metadata has no credential identifier"
            )
        result: dict[str, Any] = {"provider_id": provider_id}
        if operation.name_field is not None:
            name = _dig(item, operation.name_field)
            if name is not None and not isinstance(name, str):
                raise ConnectorError(
                    "invalid-provider-response", "provider credential name is not a string"
                )
            if name:
                result["name"] = name
        for canonical, provider_path in operation.metadata_fields.items():
            value = _dig(item, provider_path)
            if value is not None:
                result[canonical] = _metadata_value(canonical, value)
        return result

    async def _call(
        self,
        api: HttpProviderApi,
        operation: HttpOperation,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        path = _render_path(operation.path, payload)
        url = _operation_url(api, path)
        body = _render_body(operation.body, payload) if operation.body else None
        client = self._client or httpx.AsyncClient(timeout=30)
        close = self._client is None
        try:
            return await client.request(
                operation.method,
                url,
                headers=headers,
                params=operation.query or None,
                json=body,
            )
        finally:
            if close:
                await client.aclose()

    async def _headers(self, connection: Connection, auth: HttpAuth) -> dict[str, str]:
        auth_reference = connection.authorization_reference
        if auth_reference is None:
            raise ConnectorError(
                "missing-auth-reference", "provider connection is not authenticated"
            )
        with await self._secrets.access_for(connection, auth_reference) as secret:
            return _value_headers(secret, auth)


def _api(connection: Connection) -> HttpProviderApi:
    if (
        connection.interface is not ConnectionInterface.API
        or ConnectionRole.PROVIDER not in connection.roles
        or connection.authorization
        not in {ConnectionAuthorization.API_KEY, ConnectionAuthorization.OAUTH}
    ):
        raise ConnectorError(
            "invalid-provider-connection",
            "HTTP provider operations require an API provider connection",
        )
    if (
        connection.authorization_expires_at is not None
        and connection.authorization_expires_at <= datetime.now(UTC)
    ):
        raise ConnectorError("provider-reauthentication", "provider authorization has expired")
    if connection.http is None:
        raise ConnectorError("missing-provider-api", "provider connection has no HTTP API")
    if (
        connection.authorization is ConnectionAuthorization.OAUTH
        and connection.http.auth.scheme is not HttpAuthScheme.BEARER
    ):
        raise ConnectorError(
            "invalid-provider-authorization",
            "OAuth provider connections require bearer transport",
        )
    return connection.http


def _value_headers(value: SecretValue, auth: HttpAuth) -> dict[str, str]:
    token = value.bytes().decode()
    if auth.scheme is HttpAuthScheme.BEARER:
        prefix = auth.prefix if auth.prefix is not None else "Bearer "
        return {auth.header: f"{prefix}{token}"}
    if auth.scheme is HttpAuthScheme.HEADER:
        prefix = auth.prefix or ""
        return {auth.header: f"{prefix}{token}"}
    encoded = base64.b64encode(value.bytes()).decode()
    return {auth.header: f"Basic {encoded}"}


def _render_path(path: str, payload: dict[str, Any]) -> str:
    def replace(match: Any) -> str:
        key = match.group(1)
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ConnectorError("invalid-parameter", f"{key} is required")
        return quote(value, safe="")

    return _PATH_PARAMETER.sub(replace, path)


def _operation_url(api: HttpProviderApi, path: str) -> str:
    base = urlsplit(api.base_url)
    url = urljoin(api.base_url.rstrip("/") + "/", path.lstrip("/"))
    target = urlsplit(url)
    if (
        target.scheme != base.scheme
        or target.hostname != base.hostname
        or target.port != base.port
        or target.username is not None
        or target.password is not None
    ):
        raise ConnectorError(
            "invalid-provider-api", "provider operation resolved outside its configured origin"
        )
    return url


def _render_body(template: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {key: _render_value(value, payload) for key, value in template.items()}


def _render_value(value: Any, payload: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return payload.get(value[2:-1])
    if isinstance(value, dict):
        return {key: _render_value(item, payload) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, payload) for item in value]
    return value


def _dig(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConnectorError("invalid-provider-response", f"{label} returned a non-object")
    return value


def _metadata_value(name: str, value: Any) -> str | bool | list[str]:
    if name == "disabled" and isinstance(value, bool):
        return value
    if (
        name == "scopes"
        and isinstance(value, list)
        and all(isinstance(item, str) for item in value)
    ):
        return value
    if name != "disabled" and name != "scopes" and isinstance(value, str) and len(value) <= 512:
        return value
    raise ConnectorError(
        "invalid-provider-response", f"provider metadata field {name} has an invalid type"
    )


def _expected(response: httpx.Response, statuses: set[int]) -> None:
    if response.status_code not in statuses:
        retryable = response.status_code in {408, 429, 500, 502, 503, 504}
        raise ConnectorError(
            "provider-api-error",
            f"provider returned HTTP {response.status_code}",
            retryable=retryable,
        )


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectorError("invalid-parameter", f"{key} is required")
    return value


_PATH_PARAMETER = re.compile(r"{([A-Za-z][A-Za-z0-9_]*)}")
