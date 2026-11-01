from asyncio import Lock, sleep
from datetime import datetime
from time import monotonic
from typing import Any, Protocol

import httpx
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    HttpOperation,
    HttpProviderApi,
)

from connectors.base import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import ConnectorError
from connectors.http import HttpProviderConnector
from connectors.resend.check import backoff
from connectors.resend.config import definition, scopes
from connectors.resend.proof import Proof
from connectors.secrets.store import SecretReader


class CreationRecords(Protocol):
    async def remember(
        self, connection: Connection, provider_id: str, value: SecretValue, metadata: dict[str, Any]
    ) -> None: ...

    async def identify(self, connection: Connection, value: SecretValue) -> dict[str, Any]: ...

    async def metadata(self, connection: Connection, provider_id: str) -> dict[str, Any] | None: ...


class Resend(HttpProviderConnector):
    def creation_scopes(self, connection: Connection, requested: frozenset[str]) -> frozenset[str]:
        self.validate(connection)
        assert connection.http
        configured = frozenset(scopes(connection.http))
        if requested and requested != configured:
            raise ConnectorError("resend-scope", "Use the connection's selected sending domain")
        return configured

    def __init__(
        self,
        secrets: SecretReader,
        receipts: CreationRecords,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(secrets, http)
        self.receipts, self.proof = receipts, Proof(http)
        self.lock, self.next_call = Lock(), 0.0

    async def _call(
        self,
        api: HttpProviderApi,
        operation: HttpOperation,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        for attempt in range(3):
            async with self.lock:
                await sleep(max(0, self.next_call - monotonic()))
                try:
                    response = await super()._call(api, operation, headers, payload)
                finally:
                    self.next_call = monotonic() + 0.55
            if operation.method != "GET" or response.status_code != 429 or attempt == 2:
                return response
            await sleep(backoff(response.headers.get("Retry-After")))
        raise AssertionError("Resend read did not finish")

    async def _list(self, api: HttpProviderApi, headers: dict[str, str]) -> list[dict[str, Any]]:
        result, cursor = [], None
        for _ in range(10):
            operation = api.list_credentials.model_copy(
                update={
                    "query": {"limit": "100", **({"after": cursor} if cursor else {})},
                }
            )
            response = await self._call(api, operation, headers, {})
            if response.status_code != 200:
                raise ConnectorError("resend-list", "Resend account access could not be verified")
            data = response.json()
            page = data.get("data") if isinstance(data, dict) else None
            if (
                not isinstance(page, list)
                or len(page) > 100
                or any(not isinstance(item, dict) for item in page)
            ):
                raise ConnectorError("resend-list", "Resend returned invalid key metadata")
            result.extend(page)
            if not isinstance(data.get("has_more"), bool):
                raise ConnectorError("resend-list", "Resend pagination could not be verified")
            if not data["has_more"]:
                return result
            cursor = page[-1].get("id") if page else None
            if not isinstance(cursor, str) or not cursor:
                raise ConnectorError("resend-list", "Resend pagination could not be verified")
        raise ConnectorError("resend-list", "Resend account exceeds the inspection limit")

    async def domains(self, token: SecretValue) -> tuple[dict[str, str], ...]:
        api = definition()
        api = api.model_copy(
            update={
                "list_credentials": api.list_credentials.model_copy(update={"path": "/domains"}),
            }
        )
        rows = await self._list(api, {"Authorization": "Bearer " + token.bytes().decode()})
        result = []
        for item in rows:
            if item.get("status") != "verified":
                continue
            if not isinstance(item.get("id"), str) or not isinstance(item.get("name"), str):
                raise ConnectorError("resend-domains", "Resend returned an invalid domain")
            definition(item["id"])
            result.append({"id": item["id"], "name": item["name"]})
        return tuple(result)

    @staticmethod
    def validate(connection: Connection) -> None:
        if (
            connection.platform != "resend"
            or connection.http is None
            or connection.interface is not ConnectionInterface.API
            or connection.roles != frozenset({ConnectionRole.PROVIDER})
            or connection.authorization is not ConnectionAuthorization.API_KEY
        ):
            raise ConnectorError("resend-config", "Choose a Resend provider connection")
        domain = connection.http.create_credential.body.get("domain_id")
        if not isinstance(domain, str):
            raise ConnectorError("resend-config", "Resend domain configuration is invalid")
        if connection.http != definition(domain):
            raise ConnectorError("resend-config", "Use Kinu's Resend connection configuration")

    async def selected_domain(self, connection: Connection) -> dict[str, str]:
        self.validate(connection)
        assert connection.http and connection.authorization_reference
        with await self._secrets.access_for(
            connection, connection.authorization_reference
        ) as token:
            domains = await self.domains(token)
        domain = connection.http.create_credential.body["domain_id"]
        selected = next((item for item in domains if item["id"] == domain), None)
        if selected is None:
            raise ConnectorError("resend-domain", "Reconnect a verified Resend domain")
        return selected

    async def metadata(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        self.validate(connection)
        result = []
        for item in await super().metadata(connection):
            for field in ("created_at", "last_used_at"):
                if field in item:
                    try:
                        date = datetime.fromisoformat(item[field])
                        if date.utcoffset() is None:
                            raise ValueError
                        item[field] = date.isoformat()
                    except (ValueError, TypeError):
                        raise ConnectorError(
                            "resend-date", "Resend returned an invalid timestamp"
                        ) from None
            recorded = await self.receipts.metadata(connection, str(item["provider_id"]))
            result.append(
                {
                    **item,
                    "kind": "api-key",
                    "scopes": recorded["scopes"] if recorded else [],
                    "status": "active" if recorded else "unverified",
                    "disabled": False,
                }
            )
        return tuple(result)

    async def identify(self, connection: Connection, value: SecretValue) -> str:
        self.validate(connection)
        record = await self.receipts.identify(connection, value)
        state = await self.proof.check(value)
        if state != "sending_access" or not connection.http:
            raise ConnectorError("resend-proof", "Resend key has changed or is no longer active")
        if record["metadata"]["scopes"] != scopes(connection.http):
            raise ConnectorError("resend-scope", "Resend domain or permissions changed")
        identity = str(record["provider_id"])
        if not any(item["provider_id"] == identity for item in await self.metadata(connection)):
            raise ConnectorError("resend-proof", "Resend key is no longer listed by its issuer")
        return identity

    async def test_credential(self, connection: Connection, value: SecretValue) -> int:
        self.validate(connection)
        state = await self.proof.check(value)
        if state == "full_access":
            raise ConnectorError("resend-scope", "Use a sending-only workload key")
        return 401 if state == "rejected" else 200

    async def execute(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> ConnectorResponse:
        self.validate(context.connection)
        assert context.connection.http
        expected = scopes(context.connection.http)
        if tool == "provider.createCredential" and sorted(payload.get("scopes", [])) != sorted(
            expected
        ):
            raise ConnectorError(
                "resend-scope", "Creation must preserve the selected Resend permissions"
            )
        if tool != "provider.createCredential":
            return await super().execute(tool, payload, context)
        return await self.issue(context.connection, str(payload.get("name", "")))

    async def issue(self, connection: Connection, name: str) -> ConnectorResponse:
        self.validate(connection)
        if connection.status != "ready" or connection.archived_at:
            raise ConnectorError("resend-config", "Reconnect the Resend account")
        assert connection.http
        expected = scopes(connection.http)
        response = await self._create(
            connection.http,
            {"name": name, "scopes": expected},
            await self._headers(connection, connection.http.auth),
        )
        if response.secret is None:
            raise ConnectorError("resend-create", "Resend did not return a new key")
        try:
            await self.receipts.remember(
                connection,
                str(response.result["provider_id"]),
                response.secret,
                {"kind": "api-key", "scopes": expected},
            )
        except BaseException:
            response.secret.clear()
            raise
        return response

    async def create_credential(
        self, connection: Connection, name: str, scopes: frozenset[str]
    ) -> ConnectorResponse:
        self.creation_scopes(connection, scopes)
        return await self.issue(connection, name)

    async def revoke_credential(self, connection: Connection, provider_id: str) -> None:
        self.validate(connection)
        if await self.receipts.metadata(connection, provider_id) is None:
            raise ConnectorError("resend-proof", "Only a Kinu-created Resend key can be retired")
        await super().revoke_credential(connection, provider_id)
