import asyncio
import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pwdlib import PasswordHash
from pydantic import SecretStr
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from core.auth.forms import FormError
from core.auth.identity import AuthenticatedIdentity
from core.ids import new_id
from core.storage.database import Database


@dataclass(frozen=True, slots=True)
class SignInResult:
    next: Literal["complete", "verify", "forgot"]
    identity: AuthenticatedIdentity | None = None
    version: int = 0


class AuthMailer(Protocol):
    async def code(self, email: str, code: str, purpose: str, message_id: str) -> None: ...


class Passwords(Protocol):
    async def signin(self, email: str, password: SecretStr) -> SignInResult: ...
    async def signup(self, email: str, name: str, password: SecretStr) -> bool: ...
    async def verify(self, email: str, code: SecretStr) -> None: ...
    async def resend(self, email: str) -> None: ...
    async def forgot(self, email: str) -> None: ...
    async def reset(self, email: str, code: SecretStr, password: SecretStr) -> None: ...


class PasswordClient:
    def __init__(
        self,
        database: Database,
        mailer: AuthMailer,
        issuer: str,
        pepper: SecretStr,
        clock: Callable[[], datetime],
    ) -> None:
        self.database, self.mailer, self.issuer, self.clock = database, mailer, issuer, clock
        self.pepper = pepper.get_secret_value().encode()
        self.hasher = PasswordHash.recommended()
        self.dummy = self.hasher.hash(secrets.token_urlsafe(32))

    async def signin(self, email: str, password: SecretStr) -> SignInResult:
        user = await self.database.collection("users").find_one({"email": email.casefold()})
        hashed = user["password_hash"] if user else self.dummy
        try:
            valid, replacement = await asyncio.to_thread(
                self.hasher.verify_and_update, password.get_secret_value(), hashed
            )
        except Exception:
            valid, replacement = False, None
        if not valid or not user or user.get("disabled"):
            raise FormError("Email or password is incorrect.", 401)
        if not user["verified"]:
            return SignInResult("verify")
        if replacement:
            await self.database.collection("users").update_one(
                {"id": user["id"], "password_hash": hashed},
                {"$set": {"password_hash": replacement}},
            )
        return SignInResult(
            "complete",
            AuthenticatedIdentity(
                subject=user["id"],
                issuer=self.issuer,
                email=user["email"],
                email_verified=True,
                display_name=user["name"],
                connected_via="Email and password",
            ),
            user["auth_version"],
        )

    async def signup(self, email: str, name: str, password: SecretStr) -> bool:
        _password(password)
        hashed = await asyncio.to_thread(self.hasher.hash, password.get_secret_value())
        user = {
            "id": new_id("user"),
            "email": email.casefold(),
            "name": name,
            "password_hash": hashed,
            "verified": False,
            "disabled": False,
            "auth_version": 0,
            "created_at": self.clock(),
        }
        try:
            await self.database.collection("users").insert_one(user)
        except DuplicateKeyError:
            return False
        await self._send(email, "verify")
        return False

    async def verify(self, email: str, code: SecretStr) -> None:
        async with self.database.transaction() as session:
            saved = await self.database.collection("auth_codes").find_one_and_delete(
                {
                    "email": email.casefold(),
                    "purpose": "verify",
                    "digest": self._digest(email, "verify", code.get_secret_value()),
                    "expires_on": {"$gt": self.clock()},
                },
                session=session,
            )
            if not saved:
                raise FormError("Code is invalid or expired. Request a new code.")
            await self.database.collection("users").update_one(
                {"id": saved["user_id"]},
                {"$set": {"verified": True}},
                session=session,
            )

    async def resend(self, email: str) -> None:
        await self._send(email, "verify")

    async def forgot(self, email: str) -> None:
        await self._send(email, "reset")

    async def reset(self, email: str, code: SecretStr, password: SecretStr) -> None:
        _password(password)
        hashed = await asyncio.to_thread(self.hasher.hash, password.get_secret_value())
        async with self.database.transaction() as session:
            saved = await self.database.collection("auth_codes").find_one_and_delete(
                {
                    "email": email.casefold(),
                    "purpose": "reset",
                    "digest": self._digest(email, "reset", code.get_secret_value()),
                    "expires_on": {"$gt": self.clock()},
                },
                session=session,
            )
            if not saved:
                raise FormError("Code is invalid or expired. Request a new code.")
            await self.database.collection("users").update_one(
                {"id": saved["user_id"], "verified": True},
                {"$set": {"password_hash": hashed}, "$inc": {"auth_version": 1}},
                session=session,
            )
            await self.database.collection("sessions").delete_many(
                {"identity.subject": saved["user_id"]},
                session=session,
            )

    async def _send(self, email: str, purpose: str) -> None:
        email = email.casefold()
        user = await self.database.collection("users").find_one({"email": email})
        if (
            not user
            or user.get("disabled")
            or (purpose == "verify" and user["verified"])
            or (purpose == "reset" and not user["verified"])
        ):
            return
        code = f"{secrets.randbelow(1_000_000):06d}"
        identity = new_id("email")
        saved = await self.database.collection("auth_codes").find_one_and_update(
            {"email": email, "purpose": purpose},
            {
                "$set": {
                    "user_id": user["id"],
                    "digest": self._digest(email, purpose, code),
                    "expires_on": self.clock() + timedelta(minutes=10),
                    "message_id": identity,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        assert saved is not None
        try:
            await self.mailer.code(email, code, purpose, identity)
        except Exception:
            await self.database.collection("auth_codes").delete_one(
                {"_id": saved["_id"], "message_id": identity}
            )
            raise

    def _digest(self, email: str, purpose: str, code: str) -> str:
        return hmac.new(
            self.pepper, f"{email.casefold()}:{purpose}:{code}".encode(), hashlib.sha256
        ).hexdigest()


def _password(password: SecretStr) -> None:
    value = password.get_secret_value()
    if len(value) < 12 or not all(
        (
            any(c.isupper() for c in value),
            any(c.islower() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        )
    ):
        raise FormError("Use 12+ characters with uppercase, lowercase, a number and a symbol.")
