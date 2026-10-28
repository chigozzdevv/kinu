import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from core.errors import AuthenticationError, KinuError
from core.storage.database import Database


@dataclass(frozen=True, slots=True)
class AuthForm:
    csrf_token: str
    expires_at: datetime


class FormError(KinuError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class AttemptLimitError(KinuError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many attempts. Please try again later.")
        self.retry_after = retry_after


Attempt = Literal["signin", "email", "code"]


class FormRepository(Protocol):
    async def save(self, token: str, form: AuthForm) -> None: ...

    async def get(self, token: str) -> AuthForm: ...

    async def consume(self, token: str, csrf: str) -> None: ...

    async def limit(self, email: str, address: str, action: Attempt) -> None: ...


class FormStore:
    def __init__(self, database: Database, clock: Callable[[], datetime]) -> None:
        self.database, self.clock = database, clock

    async def save(self, token: str, form: AuthForm) -> None:
        await self.database.collection("auth_forms").insert_one(
            {
                "token_hash": _digest(token),
                "csrf_token": form.csrf_token,
                "expires_at": form.expires_at.isoformat(),
                "expires_on": form.expires_at,
            }
        )

    async def get(self, token: str) -> AuthForm:
        _token(token)
        row = await self.database.collection("auth_forms").find_one(
            {"token_hash": _digest(token), "expires_on": {"$gt": self.clock()}}
        )
        if not row:
            raise AuthenticationError("Sign-in form expired. Please try again.")
        return AuthForm(row["csrf_token"], datetime.fromisoformat(row["expires_at"]))

    async def consume(self, token: str, csrf: str) -> None:
        _token(token)
        row = await self.database.collection("auth_forms").find_one_and_delete(
            {"token_hash": _digest(token), "csrf_token": csrf, "expires_on": {"$gt": self.clock()}}
        )
        if not row:
            raise AuthenticationError("Sign-in form expired. Please try again.")

    async def limit(self, email: str, address: str, action: Attempt) -> None:
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError

        now = self.clock()
        bucket = int(now.timestamp()) // 900
        expiry = datetime.fromtimestamp((bucket + 1) * 900, UTC)
        limits = (3, 30) if action == "email" else (10, 60)
        for kind, value, limit in (
            ("email", email.casefold(), limits[0]),
            ("address", address, limits[1]),
        ):
            key = _digest(f"{action}:{kind}:{value}:{bucket}")
            try:
                row = await self.database.collection("auth_limits").find_one_and_update(
                    {"key": key, "attempts": {"$lt": limit}},
                    {"$inc": {"attempts": 1}, "$setOnInsert": {"expires_on": expiry}},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                row = None
            if not row:
                raise AttemptLimitError(max(1, int((expiry - now).total_seconds())))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _token(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise AuthenticationError("Sign-in form expired. Please try again.")
