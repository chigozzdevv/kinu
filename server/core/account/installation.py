import secrets
from datetime import UTC, datetime

from pydantic import SecretStr
from pymongo.errors import DuplicateKeyError

from core.auth.identity import AuthenticatedIdentity
from core.errors import AuthorizationError, ResourceConflictError, ResourceSetupRequiredError
from core.storage.database import Database


class Installation:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def is_admin(self, identity: AuthenticatedIdentity) -> bool:
        return bool(
            identity.email_verified
            and await self.database.collection("installation").find_one(
                {"_id": "main", "administrators": identity.actor_id}
            )
        )

    async def require_admin(self, identity: AuthenticatedIdentity) -> None:
        if not await self.is_admin(identity):
            raise AuthorizationError("An installation administrator must configure integrations")

    async def claim(
        self, identity: AuthenticatedIdentity, token: SecretStr, expected: SecretStr | None
    ) -> None:
        if not identity.email_verified:
            raise AuthorizationError("Verify your email before completing setup")
        if not expected:
            raise ResourceSetupRequiredError("Prepare installation setup on the host")
        value = token.get_secret_value()
        if not value.isascii() or not secrets.compare_digest(value, expected.get_secret_value()):
            raise AuthorizationError("The setup link is invalid")
        try:
            await self.database.collection("installation").update_one(
                {"_id": "main", "administrators": {"$exists": False}},
                {
                    "$set": {
                        "administrators": [identity.actor_id],
                        "initialized_at": datetime.now(UTC),
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            if not await self.is_admin(identity):
                raise ResourceConflictError("Installation setup is already complete") from None
