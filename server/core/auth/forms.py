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
        async with self.database.connection() as sql, sql.transaction():
            await sql.execute("DELETE FROM kinu.auth_forms WHERE expires_at <= %s", (self.clock(),))
            await sql.execute(
                "INSERT INTO kinu.auth_forms VALUES (%s,%s,%s)",
                (_digest(token), form.csrf_token, form.expires_at),
            )

    async def get(self, token: str) -> AuthForm:
        _token(token)
        async with self.database.connection() as sql:
            row = await (
                await sql.execute(
                    "SELECT csrf_token,expires_at FROM kinu.auth_forms "
                    "WHERE token_hash=%s AND expires_at>%s",
                    (_digest(token), self.clock()),
                )
            ).fetchone()
        if not row:
            raise AuthenticationError("Sign-in form expired. Please try again.")
        return AuthForm(**row)

    async def consume(self, token: str, csrf: str) -> None:
        _token(token)
        async with self.database.connection() as sql:
            row = await (
                await sql.execute(
                    "DELETE FROM kinu.auth_forms WHERE token_hash=%s AND csrf_token=%s "
                    "AND expires_at>%s RETURNING token_hash",
                    (_digest(token), csrf, self.clock()),
                )
            ).fetchone()
        if not row:
            raise AuthenticationError("Sign-in form expired. Please try again.")

    async def limit(self, email: str, address: str, action: Attempt) -> None:
        now = self.clock()
        bucket = int(now.timestamp()) // 900
        expiry = datetime.fromtimestamp((bucket + 1) * 900, UTC)
        limits = (3, 30) if action == "email" else (10, 60)
        counters = [
            (_digest(f"{action}:{kind}:{value}"), limit)
            for kind, value, limit in (
                ("email", email.casefold(), limits[0]),
                ("address", address, limits[1]),
            )
        ]
        async with self.database.connection() as sql, sql.transaction():
            await sql.execute("DELETE FROM kinu.auth_limits WHERE expires_at<=%s", (now,))
            for key, limit in sorted(counters):
                row = await (
                    await sql.execute(
                        "INSERT INTO kinu.auth_limits VALUES (%s,%s,1,%s) "
                        "ON CONFLICT (key,bucket) DO UPDATE SET "
                        "attempts=kinu.auth_limits.attempts+1 "
                        "WHERE kinu.auth_limits.attempts<%s RETURNING attempts",
                        (key, bucket, expiry, limit),
                    )
                ).fetchone()
                if not row:
                    raise AttemptLimitError(max(1, int((expiry - now).total_seconds())))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _token(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise AuthenticationError("Sign-in form expired. Please try again.")
