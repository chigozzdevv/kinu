import hashlib
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

from core.auth.identity import AuthenticatedIdentity
from core.auth.session import BrowserSession
from core.errors import AuthenticationError
from core.storage.database import Database


class SessionStore:
    def __init__(self, database: Database, clock: Callable[[], datetime]) -> None:
        self._database, self._clock = database, clock

    async def save(self, token: str, session: BrowserSession) -> None:
        await self._database.collection("sessions").insert_one(
            {
                "token_hash": _hash(token),
                "identity": asdict(session.identity),
                "csrf_token": session.csrf_token,
                "expires_at": session.expires_at.isoformat(),
                "expires_on": session.expires_at,
                "account_id": session.account_id,
                "account_version": session.account_version,
            }
        )

    async def get(self, token: str) -> BrowserSession:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise AuthenticationError("session is invalid or expired")
        row = await self._database.collection("sessions").find_one(
            {"token_hash": _hash(token), "expires_on": {"$gt": self._clock()}}
        )
        if row is None:
            raise AuthenticationError("session is invalid or expired")
        if row.get("account_id"):
            user = await self._database.collection("users").find_one(
                {
                    "id": row["account_id"],
                    "auth_version": row["account_version"],
                    "verified": True,
                    "disabled": False,
                }
            )
            if not user:
                raise AuthenticationError("session is invalid or expired")
        return BrowserSession(
            AuthenticatedIdentity(**row["identity"]),
            row["csrf_token"],
            datetime.fromisoformat(row["expires_at"]),
            row.get("account_id"),
            row.get("account_version", 0),
        )

    async def delete(self, token: str) -> None:
        await self._database.collection("sessions").delete_one({"token_hash": _hash(token)})

    async def revoke(self, email: str, issuer: str) -> None:
        await self._database.collection("sessions").delete_many(
            {
                "identity.email": {"$regex": "^" + re.escape(email) + "$", "$options": "i"},
                "identity.issuer": issuer,
            }
        )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
