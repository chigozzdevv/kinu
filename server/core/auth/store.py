import hashlib
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

from psycopg.types.json import Jsonb

from core.auth.identity import AuthenticatedIdentity
from core.auth.session import BrowserSession
from core.errors import AuthenticationError
from core.storage.database import Database


class SessionStore:
    def __init__(self, database: Database, clock: Callable[[], datetime]) -> None:
        self._database = database
        self._clock = clock

    async def save(self, token: str, session: BrowserSession) -> None:
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                "DELETE FROM kinu.sessions WHERE expires_at <= %s", (self._clock(),)
            )
            await connection.execute(
                "INSERT INTO kinu.sessions VALUES (%s, %s, %s, %s)",
                (
                    _hash(token),
                    Jsonb(asdict(session.identity)),
                    session.csrf_token,
                    session.expires_at,
                ),
            )

    async def get(self, token: str) -> BrowserSession:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise AuthenticationError("session is invalid or expired")
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT identity, csrf_token, expires_at FROM kinu.sessions "
                "WHERE token_hash = %s AND expires_at > %s",
                (_hash(token), self._clock()),
            )
            row = await cursor.fetchone()
        if row is None:
            raise AuthenticationError("session is invalid or expired")
        return BrowserSession(
            identity=AuthenticatedIdentity(**row["identity"]),
            csrf_token=row["csrf_token"],
            expires_at=row["expires_at"],
        )

    async def delete(self, token: str) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                "DELETE FROM kinu.sessions WHERE token_hash = %s", (_hash(token),)
            )

    async def revoke(self, email: str, issuer: str) -> None:
        async with self._database.connection() as sql:
            await sql.execute(
                "DELETE FROM kinu.sessions WHERE lower(identity->>'email')=lower(%s) "
                "AND identity->>'issuer'=%s",
                (email, issuer),
            )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
