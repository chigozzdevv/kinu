import asyncio
import base64
import hashlib
import secrets
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from contracts import Connection, SetupSession, SetupStatus, Stage
from core.errors import ResourceConflictError
from core.ids import new_id
from core.inventory.store import InventoryStore
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from browser.auth import filter_storage_state
from browser.catalog import Catalog
from browser.launch import BrowserFactory, Launcher, Tab
from browser.setupstore import Store


@dataclass
class Live:
    session: SetupSession
    connection: Connection
    tab: Tab
    stack: AsyncExitStack
    key: rsa.RSAPrivateKey
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Setups:
    def __init__(self, catalog: Catalog, launcher: BrowserFactory | None = None) -> None:
        self.catalog, self.launcher = catalog, launcher or Launcher()
        self.store = Store(catalog)
        self.live: dict[str, Live] = {}
        self.begin_lock = asyncio.Lock()

    async def begin(
        self, org: str, identity: str, subject: str, extra: tuple[str, ...]
    ) -> tuple[SetupSession, str]:
        async with self.begin_lock:
            return await self._begin(org, identity, subject, extra)

    async def _begin(
        self, org: str, identity: str, subject: str, extra: tuple[str, ...]
    ) -> tuple[SetupSession, str]:
        await self.reap()
        for stale in tuple(self.live.values()):
            if stale.session.subject == subject and stale.session.organisation_id == org:
                await self.close(stale)
        if len(self.live) >= 4:
            raise ResourceConflictError("Close the existing browser setup before opening another")
        connection = await InventoryStore(self.catalog.database).get_connection(org, identity)
        if connection.archived_at:
            raise ResourceConflictError("Connection is archived")
        version = await self.catalog.definition(connection)
        stack = AsyncExitStack()
        try:
            tab = await stack.enter_async_context(
                self.launcher.open(version.definition, extra_domains=extra)
            )
            now, token, setup_id = datetime.now(UTC), secrets.token_urlsafe(32), new_id("setup")
            session = SetupSession(
                id=setup_id,
                organisation_id=org,
                connection_id=identity,
                secret_container=f"browser://{identity}",
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                subject=subject,
                allowed_domains=tuple(dict.fromkeys((*version.definition.allowed_domains, *extra))),
                status=SetupStatus.READY,
                worker_instance="executor",
                internal_address="private",
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(minutes=20),
            )
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.live[setup_id] = Live(session, connection, tab, stack, key)
            await self.store.save(session)
            return session, token
        except BaseException:
            await stack.aclose()
            raise

    async def current(
        self, org: str, identity: str, subject: str, token: str | None = None
    ) -> Live:
        await self.reap()
        live = self.live.get(identity)
        if not live or live.session.organisation_id != org or live.session.subject != subject:
            raise ResourceConflictError("Browser setup expired; connect again")
        if token is not None and not secrets.compare_digest(
            live.session.token_hash, hashlib.sha256(token.encode()).hexdigest()
        ):
            raise ResourceConflictError("Browser setup token is invalid")
        current = await InventoryStore(self.catalog.database).get_connection(
            org, live.connection.id
        )
        if current != live.connection or current.archived_at:
            await self.close(live)
            raise ResourceConflictError("Connection changed during browser setup")
        return live

    async def get(self, org: str, identity: str) -> SetupSession:
        await self.reap()
        session = await self.store.get(org, identity)
        if session.status is SetupStatus.READY and identity not in self.live:
            session = session.model_copy(
                update={
                    "status": SetupStatus.TERMINATED,
                    "terminated_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    "revision": session.revision + 1,
                }
            )
            await self.store.save(session)
        return session

    async def authenticated(self, live: Live) -> bool:
        version = await self.catalog.definition(live.connection)
        first = next(
            (
                step
                for step in version.definition.steps
                if step.tool == "browser.navigate" and step.stage is Stage.CREATE
            ),
            None,
        )
        if not first or not first.checkpoint:
            raise ResourceConflictError("Login verification requires a key-page checkpoint")
        try:
            try:
                await live.tab.driver.validate_step(first)
            except Exception:
                await live.tab.driver.setup_enter(str(first.parameters["url"]))
                await live.tab.driver.validate_step(first)
            return True
        except Exception:
            return False

    async def stream(
        self, org: str, identity: str, subject: str, token: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        live = await self.current(org, identity, subject, token)
        async with live.lock:
            kind = data.get("type")
            if live.session.id not in self.live or live.session.expires_at <= datetime.now(UTC):
                raise ResourceConflictError("Browser setup expired; connect again")
            if kind == "frame":
                return {
                    "type": "frame",
                    "image": base64.b64encode(await live.tab.driver.setup_screenshot()).decode(),
                }
            if kind == "status":
                return {"type": "status", "authenticated": await self.authenticated(live)}
            if kind == "secure-key":
                return {
                    "type": "secure-key",
                    "public_key": live.key.public_key()
                    .public_bytes(
                        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                    .decode(),
                }
            if kind == "secure-input":
                plain = bytearray(
                    live.key.decrypt(
                        base64.b64decode(data["ciphertext"], validate=True),
                        padding.OAEP(
                            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
                        ),
                    )
                )
                try:
                    await live.tab.driver.setup_type(plain.decode("utf-8"))
                finally:
                    plain[:] = b"\x00" * len(plain)
                return {"type": "secure-input", "succeeded": True}
            if kind == "action":
                action = data.get("action", {})
                operation = action.get("kind")
                if operation == "navigate":
                    await live.tab.driver.setup_navigate(action["url"])
                elif operation == "click":
                    await live.tab.driver.setup_click(action["x"], action["y"])
                elif operation == "scroll":
                    await live.tab.driver.setup_scroll(action["delta_y"])
                elif operation == "key":
                    await live.tab.driver.setup_key(action["key"])
                else:
                    raise ResourceConflictError("Unsupported browser interaction")
                return {"type": "action", "succeeded": True}
            raise ResourceConflictError("Unsupported browser setup request")

    async def complete(
        self, org: str, identity: str, subject: str, token: str, revision: int
    ) -> tuple[SetupSession, Connection]:
        live = await self.current(org, identity, subject, token)
        async with live.lock:
            if live.session.revision != revision or not await self.authenticated(live):
                raise ResourceConflictError("Finish provider sign-in before saving")
            version = await self.catalog.definition(live.connection)
            state = filter_storage_state(
                await live.tab.context.storage_state(), version.definition.allowed_domains
            )
            if not state.get("cookies") and not state.get("origins"):
                raise ResourceConflictError("Provider did not establish a browser session")
            await self.catalog.vault.put(
                org, identity, {"connection_id": live.connection.id, "state": state}
            )
            reference = f"browser://{live.connection.id}/{identity}"
            try:
                connection = await self.store.authenticate(live.connection, reference)
            except BaseException:
                await self.catalog.vault.database.collection("secrets").delete_one(
                    {"organisation_id": org, "id": identity}
                )
                raise
            previous = live.connection.authorization_reference or ""
            if previous.startswith(f"browser://{live.connection.id}/"):
                await self.catalog.vault.database.collection("secrets").delete_one(
                    {"organisation_id": org, "id": previous.rsplit("/", 1)[-1]}
                )
            live.session = live.session.model_copy(
                update={
                    "status": SetupStatus.COMPLETE,
                    "auth_reference": reference,
                    "revision": revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self.close(live, complete=True)
            return live.session, connection

    async def abort(self, org: str, identity: str, subject: str, revision: int) -> SetupSession:
        live = await self.current(org, identity, subject)
        async with live.lock:
            if live.session.revision != revision:
                raise ResourceConflictError("Browser setup changed")
            await self.close(live)
            return live.session

    async def close(self, live: Live, *, complete: bool = False) -> None:
        self.live.pop(live.session.id, None)
        if not complete:
            live.session = live.session.model_copy(
                update={
                    "status": SetupStatus.TERMINATED,
                    "terminated_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    "revision": live.session.revision + 1,
                }
            )
        try:
            await self.store.save(live.session)
        finally:
            await live.stack.aclose()

    async def reap(self) -> tuple[SetupSession, ...]:
        expired = []
        for live in tuple(self.live.values()):
            if live.session.expires_at <= datetime.now(UTC):
                async with live.lock:
                    await self.close(live)
                    expired.append(live.session)
        return tuple(expired)

    async def shutdown(self) -> None:
        for live in tuple(self.live.values()):
            async with live.lock:
                await self.close(live)
