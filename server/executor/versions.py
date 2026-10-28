import base64
import hashlib
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from connectors.base import SecretValue
from contracts import Connection, ConnectionRole, ConnectionStatus
from core.errors import ResourceConflictError, ResourceNotFoundError
from pymongo.errors import DuplicateKeyError

from executor.vault import Vault


def store_id(org: str) -> str:
    return "vault_" + hashlib.sha256(org.encode()).hexdigest()[:24]


def root(org: str) -> str:
    return "kinu://vault/" + org


class Versions:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.collection = vault.database.collection("versions")
        self.heads = vault.database.collection("version_heads")

    async def initialize(self) -> None:
        await self.collection.create_index([("organisation_id", 1), ("reference", 1)], unique=True)
        await self.heads.create_index([("organisation_id", 1), ("resource", 1)], unique=True)

    def scope(self, connection: Connection, reference: str) -> None:
        org = connection.organisation_id
        if (
            connection.id != store_id(org)
            or connection.platform != "kinu"
            or connection.roles != frozenset({ConnectionRole.SECRET_STORE})
            or connection.status is not ConnectionStatus.READY
            or connection.archived_at
            or not reference.startswith(root(org) + "/")
            or not any(
                reference.startswith(item.rstrip("/") + "/")
                for item in connection.allowed_resources
            )
        ):
            raise ResourceConflictError("Secret version is outside the workspace vault")

    async def record(
        self,
        connection: Connection,
        resource: str,
        identity: str,
        value: SecretValue | None,
        *,
        current: bool = False,
    ) -> str:
        self.scope(connection, resource)
        reference = resource + "/versions/" + identity
        self.scope(connection, reference)
        content = value.bytes() if value else None
        fingerprint = hashlib.sha256(content).hexdigest() if content is not None else None
        payload = {"secret": base64.b64encode(content).decode()} if content is not None else None
        query = {"organisation_id": connection.organisation_id, "reference": reference}
        row = {
            **query,
            "resource": resource,
            "fingerprint": fingerprint,
            "enabled": True,
            "created_at": datetime.now(UTC),
            "value": self.vault.seal(payload, reference) if payload else None,
        }
        with suppress(DuplicateKeyError):
            await self.collection.update_one(query, {"$setOnInsert": row}, upsert=True)
        saved = await self.collection.find_one(query)
        if not saved or saved["fingerprint"] != fingerprint:
            raise ResourceConflictError("Secret generation changed")
        if current:
            await self.heads.update_one(
                {"organisation_id": connection.organisation_id, "resource": resource},
                {"$setOnInsert": {"reference": reference}},
                upsert=True,
            )
        return reference

    async def access_for(self, connection: Connection, reference: str) -> SecretValue:
        self.scope(connection, reference)
        row = await self.collection.find_one(
            {"organisation_id": connection.organisation_id, "reference": reference, "enabled": True}
        )
        if not row or not row.get("value"):
            raise ResourceNotFoundError("This generation has no readable secret value")
        payload = self.vault.open(row["value"], reference)
        return SecretValue(base64.b64decode(payload["secret"]))

    async def has_value(self, connection: Connection, reference: str) -> bool:
        self.scope(connection, reference)
        row = await self.collection.find_one(
            {"organisation_id": connection.organisation_id, "reference": reference, "enabled": True}
        )
        return bool(row and row.get("value"))

    async def current(self, connection: Connection, resource: str) -> str:
        self.scope(connection, resource)
        head = await self.heads.find_one(
            {"organisation_id": connection.organisation_id, "resource": resource}
        )
        if not head:
            raise ResourceNotFoundError("Current secret generation is unavailable")
        return str(head["reference"])

    async def promote(self, connection: Connection, reference: str, previous: str) -> None:
        self.scope(connection, reference)
        resource = reference.partition("/versions/")[0]
        if previous.partition("/versions/")[0] != resource or not await self.has_value(
            connection, reference
        ):
            raise ResourceConflictError("Replacement secret generation is unavailable")
        result = await self.heads.update_one(
            {
                "organisation_id": connection.organisation_id,
                "resource": resource,
                "reference": previous,
            },
            {"$set": {"reference": reference}},
        )
        if not result.modified_count and await self.current(connection, resource) != reference:
            raise ResourceConflictError("Current secret generation changed")

    async def disable_for(
        self, connection: Connection, reference: str, access_token: SecretValue | None = None
    ) -> dict[str, Any]:
        self.scope(connection, reference)
        if await self.current(connection, reference.partition("/versions/")[0]) == reference:
            raise ResourceConflictError("The current secret generation cannot be disabled")
        await self.collection.update_one(
            {"organisation_id": connection.organisation_id, "reference": reference},
            {"$set": {"enabled": False}, "$unset": {"value": ""}},
        )
        return {"disabled": True, "secret_reference": reference}
