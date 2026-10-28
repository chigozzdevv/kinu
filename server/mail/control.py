from typing import Any, Literal

from contracts import Contract, Identifier
from core.errors import ResourceConflictError
from pydantic import Field

from mail.models import SenderUpdate
from mail.service import Service


class ControlRequest(Contract):
    operation: Literal["status", "begin", "owner", "finish", "list", "get", "senders", "disconnect"]
    organisation_id: Identifier | None = None
    actor_id: Identifier | None = None
    session_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    mailbox_id: Identifier | None = None
    state: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43}$")
    code: str | None = Field(default=None, min_length=1, max_length=4096)
    revision: int | None = Field(default=None, ge=0)
    update: SenderUpdate | None = None


async def control(service: Service, request: ControlRequest) -> Any:
    op, org, identity = request.operation, request.organisation_id, request.mailbox_id
    if op == "status":
        return service.google.config.ready
    if op == "owner" and request.state and request.session_hash and request.actor_id:
        return await service.owner(request.state, request.session_hash, request.actor_id)
    if org:
        if op == "list":
            return [box.model_dump(mode="json") for box in await service.store.list(org)]
        if op == "get" and identity:
            return (await service.store.mailbox(org, identity))[0].model_dump(mode="json")
        if op == "begin" and request.actor_id and request.session_hash:
            return await service.begin(org, request.actor_id, request.session_hash, identity)
        if op == "finish" and request.actor_id and request.session_hash and request.state:
            return (
                await service.finish(
                    org, request.actor_id, request.session_hash, request.state, request.code
                )
            ).model_dump(mode="json")
        if op == "senders" and identity and request.update:
            return (await service.senders(org, identity, request.update)).model_dump(mode="json")
        if op == "disconnect" and identity and request.revision is not None:
            return (await service.disconnect(org, identity, request.revision)).model_dump(
                mode="json"
            )
    raise ResourceConflictError("Gmail request is incomplete")
