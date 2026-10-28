import hashlib
from datetime import UTC, datetime
from typing import Any

from connectors.base import SecretValue
from contracts import Connection
from core.errors import ResourceConflictError, ResourceNotFoundError
from pymongo.errors import DuplicateKeyError

from executor.vault import Vault


class Receipts:
    def __init__(self, vault: Vault) -> None:
        self.vault, self.collection = vault, vault.database.collection("provider_receipts")

    async def initialize(self) -> None:
        for field in ("fingerprint", "provider_id"):
            await self.collection.create_index(
                [("organisation_id", 1), ("connection_id", 1), (field, 1)], unique=True
            )

    async def remember(
        self, connection: Connection, provider_id: str, value: SecretValue, metadata: dict[str, Any]
    ) -> None:
        fingerprint = hashlib.sha256(value.bytes()).hexdigest()
        query = {
            "organisation_id": connection.organisation_id,
            "connection_id": connection.id,
            "fingerprint": fingerprint,
        }
        data = {"provider_id": provider_id, "fingerprint": fingerprint, "metadata": metadata}
        context = self.context(connection, fingerprint)
        try:
            await self.collection.update_one(
                query,
                {
                    "$setOnInsert": {
                        **query,
                        "provider_id": provider_id,
                        "value": self.vault.seal(data, context),
                        "created_at": datetime.now(UTC),
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            raise ResourceConflictError(
                "Provider key already has a different creation record"
            ) from None
        row = await self.collection.find_one(query)
        if not row or self.vault.open(row["value"], context) != data:
            raise ResourceConflictError("Provider creation record changed")

    async def identify(self, connection: Connection, value: SecretValue) -> dict[str, Any]:
        fingerprint = hashlib.sha256(value.bytes()).hexdigest()
        row = await self.collection.find_one(
            {
                "organisation_id": connection.organisation_id,
                "connection_id": connection.id,
                "fingerprint": fingerprint,
            }
        )
        if not row:
            raise ResourceNotFoundError("Create this key through Kinu before linking it")
        result = self.vault.open(row["value"], self.context(connection, fingerprint))
        if result["fingerprint"] != fingerprint or result["provider_id"] != row["provider_id"]:
            raise ResourceConflictError("Provider key identity changed")
        return result

    async def metadata(self, connection: Connection, provider_id: str) -> dict[str, Any] | None:
        row = await self.collection.find_one(
            {
                "organisation_id": connection.organisation_id,
                "connection_id": connection.id,
                "provider_id": provider_id,
            }
        )
        if not row:
            return None
        data = self.vault.open(row["value"], self.context(connection, row["fingerprint"]))
        if data["provider_id"] != provider_id or data["fingerprint"] != row["fingerprint"]:
            raise ResourceConflictError("Provider key identity changed")
        return dict(data["metadata"])

    @staticmethod
    def context(connection: Connection, fingerprint: str) -> str:
        return f"{connection.organisation_id}:{connection.id}:{fingerprint}"
