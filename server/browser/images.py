import base64
import hashlib
from datetime import UTC, datetime, timedelta

from core.errors import ResourceConflictError, ResourceNotFoundError
from cryptography.exceptions import InvalidTag
from executor.vault import Vault

LIMIT = 524_288


class Images:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.collection = vault.database.collection("browser_images")

    async def initialize(self) -> None:
        await self.collection.create_index(
            [("organisation_id", 1), ("run_id", 1), ("id", 1)], unique=True
        )
        await self.collection.create_index("expires_on", expireAfterSeconds=0)

    async def save(self, org: str, run: str, activity: str, content: bytes) -> tuple[str, str]:
        validate(content)
        digest = hashlib.sha256(content).hexdigest()
        reference = f"kinu://browser-images/{run}/{activity}"
        context = f"browser-image:{org}:{run}:{activity}"
        query = {"organisation_id": org, "run_id": run, "id": activity}
        await self.collection.update_one(
            query,
            {
                "$setOnInsert": {
                    "digest": digest,
                    "value": self.vault.seal(
                        {"image": base64.b64encode(content).decode()}, context
                    ),
                    "expires_on": datetime.now(UTC) + timedelta(days=90),
                }
            },
            upsert=True,
        )
        saved = await self.collection.find_one(query)
        if not saved or saved["digest"] != digest:
            raise ResourceConflictError("Browser screenshot evidence changed")
        return reference, digest

    async def read_image(
        self, org: str, run: str, activity: str, expected_digest: str
    ) -> tuple[bytes, str]:
        row = await self.collection.find_one(
            {"organisation_id": org, "run_id": run, "id": activity}
        )
        if not row or row["expires_on"] <= datetime.now(UTC):
            raise ResourceNotFoundError("Browser screenshot is unavailable or expired")
        if row["digest"] != expected_digest:
            raise ResourceConflictError("Browser screenshot digest changed")
        try:
            value = self.vault.open(row["value"], f"browser-image:{org}:{run}:{activity}")
            content = base64.b64decode(value["image"], validate=True)
        except (ValueError, KeyError, TypeError, InvalidTag):
            raise ResourceConflictError("Browser screenshot could not be verified") from None
        validate(content)
        if hashlib.sha256(content).hexdigest() != expected_digest:
            raise ResourceConflictError("Browser screenshot digest changed")
        return content, "image/png"


def validate(content: bytes) -> None:
    if not content.startswith(b"\x89PNG\r\n\x1a\n") or not 24 <= len(content) <= LIMIT:
        raise ResourceConflictError("Browser screenshot must be a bounded PNG image")
