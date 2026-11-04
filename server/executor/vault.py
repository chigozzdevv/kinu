import base64
import hashlib
import json
import secrets
from typing import Any

from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr


class Vault:
    def __init__(
        self,
        database: Database,
        key: SecretStr,
        integrations: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.database = database
        self._integrations = integrations or {}
        self.cipher = AESGCM(hashlib.sha256(key.get_secret_value().encode()).digest())

    async def initialize(self) -> None:
        await self.database.collection("secrets").create_index(
            [("organisation_id", 1), ("id", 1)], unique=True
        )
        await self.database.collection("integrations").create_index("id", unique=True)

    def seal(self, value: dict[str, Any], context: str) -> str:
        nonce = secrets.token_bytes(12)
        return base64.urlsafe_b64encode(
            nonce
            + self.cipher.encrypt(
                nonce, json.dumps(value, separators=(",", ":")).encode(), context.encode()
            )
        ).decode()

    def open(self, value: str, context: str) -> dict[str, Any]:
        payload = base64.urlsafe_b64decode(value)
        result: dict[str, Any] = json.loads(
            self.cipher.decrypt(payload[:12], payload[12:], context.encode())
        )
        return result

    async def get(self, organisation_id: str, identity: str) -> dict[str, Any]:
        row = await self.database.collection("secrets").find_one(
            {"organisation_id": organisation_id, "id": identity}
        )
        if not row:
            raise ResourceNotFoundError("Private authorization is unavailable")
        return self.open(row["value"], f"{organisation_id}:{identity}")

    async def put(self, organisation_id: str, identity: str, value: dict[str, Any]) -> None:
        await self.database.collection("secrets").update_one(
            {"organisation_id": organisation_id, "id": identity},
            {"$set": {"value": self.seal(value, f"{organisation_id}:{identity}")}},
            upsert=True,
        )

    async def integration(self, identity: str) -> dict[str, Any]:
        if identity in self._integrations:
            return dict(self._integrations[identity])
        row = await self.database.collection("integrations").find_one({"id": identity})
        return self.open(row["value"], "integration:" + identity) if row else {}

    async def configure(self, identity: str, value: dict[str, Any]) -> None:
        if identity in self._integrations:
            raise ResourceConflictError("This integration is managed by installation configuration")
        await self.database.collection("integrations").update_one(
            {"id": identity},
            {"$set": {"value": self.seal(value, "integration:" + identity)}},
            upsert=True,
        )
