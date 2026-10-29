import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from contracts import ConnectionRole, ConnectionStatus
from core.errors import ResourceConflictError
from core.inventory.store import InventoryStore
from core.storage.database import Database

from mail.google import Google
from mail.links import save_connection
from mail.models import CALLBACK, Grant, Mailbox, Pending, SenderUpdate
from mail.port import MailStore
from mail.vault import Vault


class Service:
    def __init__(
        self, store: MailStore, google: Google, vault: Vault, database: Database, origin: str
    ) -> None:
        self.store, self.google, self.vault, self.database, self.origin = (
            store,
            google,
            vault,
            database,
            origin,
        )

    async def begin(self, org: str, actor: str, binding: str, mailbox_id: str | None) -> str:
        box = (await self.store.mailbox(org, mailbox_id))[0] if mailbox_id else None
        if (
            not box
            and len([item for item in await self.store.list(org) if item.status != "disabled"]) >= 5
        ):
            raise ResourceConflictError("a workspace can connect up to five Gmail accounts")
        state = secrets.token_urlsafe(32)
        url, verifier = self.google.begin(self.origin + CALLBACK, state)
        key = digest(state)
        await self.store.pending(
            key,
            {
                "organisation_id": org,
                "actor": actor,
                "binding": binding,
                "expires": int(datetime.now(UTC).timestamp()) + 600,
                "cipher": self.vault.seal(
                    {
                        "verifier": verifier,
                        "mailbox_id": mailbox_id,
                        "revision": box.revision if box else None,
                    },
                    f"pending:{org}:{key}:{binding}",
                ),
            },
        )
        return url

    async def owner(self, state: str, binding: str, actor: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
            raise ResourceConflictError("Gmail connection expired; connect again")
        pending = await self.store.get("pending:" + digest(state))
        if (
            not pending
            or pending["binding"] != binding
            or pending["actor"] != actor
            or int(pending["expires"]) <= int(datetime.now(UTC).timestamp())
        ):
            raise ResourceConflictError("Gmail connection expired; connect again")
        return str(pending["organisation_id"])

    async def finish(
        self, org: str, actor: str, binding: str, state: str, code: str | None
    ) -> Mailbox:
        if await self.owner(state, binding, actor) != org:
            raise ResourceConflictError("Gmail connection belongs to another workspace")
        key = digest(state)
        stored = await self.store.consume(
            key, binding, actor, org, int(datetime.now(UTC).timestamp())
        )
        pending = Pending.model_validate(
            self.vault.open(stored["cipher"], f"pending:{org}:{key}:{binding}")
        )
        if not code:
            raise ResourceConflictError("Gmail access was not approved")
        grant = await self.google.exchange(
            code, pending.verifier.get_secret_value(), self.origin + CALLBACK
        )
        profile = await self.google.api(grant, "profile")
        email = profile.get("emailAddress")
        if not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ResourceConflictError("Google returned no Gmail account")
        now = datetime.now(UTC)
        if pending.mailbox_id:
            box, item = await self.store.mailbox(org, pending.mailbox_id)
            if box.revision != pending.revision or box.email.casefold() != email.casefold():
                raise ResourceConflictError("Gmail account changed; reconnect the original account")
            previous = box.revision
            box = box.model_copy(
                update={
                    "status": "ready" if box.senders else "setup-required",
                    "connected_at": now,
                    "revision": box.revision + 1,
                }
            )
            cursor = json.loads(item.get("cursor", "{}"))
            cursor["since"] = max(
                int(cursor.get("since", now.timestamp())), int(now.timestamp()) - 7 * 86400
            )
        else:
            accounts = [item for item in await self.store.list(org) if item.status != "disabled"]
            if any(item.email.casefold() == email.casefold() for item in accounts):
                raise ResourceConflictError(
                    "Gmail is already connected; manage the existing account"
                )
            if len(accounts) >= 5:
                raise ResourceConflictError("a workspace can connect up to five Gmail accounts")
            previous, cursor = None, None
            box = Mailbox(
                id="gmail_" + uuid4().hex,
                organisation_id=org,
                email=email.casefold(),
                connected_at=now,
            )
        await self.store.save(box, self.seal_grant(box, grant), previous, cursor=cursor)
        if box.senders:
            await save_connection(self.database, box)
        return box

    async def senders(self, org: str, identity: str, update: SenderUpdate) -> Mailbox:
        box, item = await self.store.mailbox(org, identity)
        if (
            box.revision != update.expected_revision
            or not item.get("grant")
            or box.status in {"reauthentication-required", "disabled"}
        ):
            raise ResourceConflictError("Gmail connection changed; reconnect or refresh")
        inventory = InventoryStore(self.database)
        for rule in update.senders:
            provider = await inventory.get_connection(org, rule.provider_connection_id)
            if (
                ConnectionRole.PROVIDER not in provider.roles
                or provider.status is not ConnectionStatus.READY
                or provider.archived_at
            ):
                raise ResourceConflictError("select a connected credential provider")
        pending = box.model_copy(
            update={
                "senders": update.senders,
                "status": "setup-required",
                "revision": box.revision + 1,
            }
        )
        await self.store.save(pending, item["grant"], box.revision)
        await save_connection(self.database, pending)
        ready = pending.model_copy(update={"status": "ready", "revision": pending.revision + 1})
        await self.store.save(ready, item["grant"], pending.revision)
        return ready

    async def disconnect(self, org: str, identity: str, revision: int) -> Mailbox:
        box, _ = await self.store.mailbox(org, identity)
        if box.status != "disabled":
            if box.revision != revision:
                raise ResourceConflictError("Gmail connection changed; refresh first")
            disabled = box.model_copy(update={"status": "disabled", "revision": box.revision + 1})
            await self.store.save(disabled, None, box.revision)
            box = disabled
        await save_connection(self.database, box, disabled=True)
        return box

    def seal_grant(self, box: Mailbox, grant: Grant) -> str:
        return self.vault.seal(
            {
                "access_token": grant.access_token.get_secret_value(),
                "refresh_token": grant.refresh_token.get_secret_value(),
                "expires_at": grant.expires_at.isoformat(),
            },
            f"grant:{box.organisation_id}:{box.id}",
        )

    def open_grant(self, box: Mailbox, cipher: str) -> Grant:
        return Grant.model_validate(
            self.vault.open(cipher, f"grant:{box.organisation_id}:{box.id}")
        )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
