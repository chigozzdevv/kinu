from typing import Any

from core.private import PrivateClient

from mail.models import Mailbox, SenderUpdate


class Bridge:
    def __init__(self, client: PrivateClient) -> None:
        self.client = client

    async def call(self, operation: str, **values: Any) -> Any:
        return await self.client.call("/mail", {"operation": operation, **values})

    async def ready(self) -> bool:
        try:
            return bool(await self.call("status"))
        except Exception:
            return False

    async def get(self, org: str, identity: str) -> Mailbox:
        return Mailbox.model_validate(
            await self.call("get", organisation_id=org, mailbox_id=identity)
        )

    async def list(self, org: str) -> tuple[Mailbox, ...]:
        return tuple(
            Mailbox.model_validate(box) for box in await self.call("list", organisation_id=org)
        )

    async def begin(self, org: str, actor: str, binding: str, identity: str | None) -> str:
        return str(
            await self.call(
                "begin",
                organisation_id=org,
                actor_id=actor,
                session_hash=binding,
                mailbox_id=identity,
            )
        )

    async def owner(self, state: str, actor: str, binding: str) -> str:
        return str(await self.call("owner", state=state, actor_id=actor, session_hash=binding))

    async def finish(
        self, org: str, actor: str, binding: str, state: str, code: str | None
    ) -> Mailbox:
        return Mailbox.model_validate(
            await self.call(
                "finish",
                organisation_id=org,
                actor_id=actor,
                session_hash=binding,
                state=state,
                code=code,
            )
        )

    async def senders(self, org: str, identity: str, update: SenderUpdate) -> Mailbox:
        return Mailbox.model_validate(
            await self.call(
                "senders",
                organisation_id=org,
                mailbox_id=identity,
                update=update.model_dump(mode="json"),
            )
        )

    async def disconnect(self, org: str, identity: str, revision: int) -> Mailbox:
        return Mailbox.model_validate(
            await self.call(
                "disconnect", organisation_id=org, mailbox_id=identity, revision=revision
            )
        )
