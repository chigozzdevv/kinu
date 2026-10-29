import base64
from typing import Annotated, Any, Literal

from contracts import Contract, Identifier
from core.errors import ResourceConflictError
from fastapi import APIRouter, Depends
from pydantic import Field, SecretStr

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


class Request(Contract):
    operation: Literal["begin", "get", "complete", "abort", "stream", "reap", "forget"]
    organisation_id: Identifier
    connection_id: Identifier | None = None
    setup_id: Identifier | None = None
    subject: str = Field(default="", max_length=512)
    revision: int = Field(default=0, ge=0)
    token: SecretStr = SecretStr("")
    data: dict[str, Any] = Field(default_factory=dict, max_length=5)


@router.post("/browser")
async def browser(body: Request, runtime: Annotated[Runtime, Depends(authenticated)]) -> Any:
    service, org = runtime.browser_setups, body.organisation_id
    if body.operation == "begin" and body.connection_id:
        extras = body.data.get("extra_domains", [])
        if (
            not isinstance(extras, list)
            or len(extras) > 20
            or any(not isinstance(value, str) or len(value) > 253 for value in extras)
        ):
            raise ResourceConflictError("Choose a bounded login domain list")
        session, token = await service.begin(org, body.connection_id, body.subject, tuple(extras))
        return {"session": session, "token": token}
    if body.operation == "reap":
        return tuple(session for session in await service.reap() if session.organisation_id == org)
    if body.operation == "forget" and body.connection_id:
        connection = await runtime.hosts.inventory.get_connection(org, body.connection_id)
        if not connection.archived_at or not (connection.authorization_reference or "").startswith(
            "browser://" + connection.id + "/"
        ):
            raise ResourceConflictError("Disconnect the browser connection first")
        for live in tuple(service.live.values()):
            if live.connection.id == connection.id and live.session.organisation_id == org:
                async with live.lock:
                    await service.close(live)
        identity = (connection.authorization_reference or "").rsplit("/", 1)[-1]
        await runtime.vault.database.collection("secrets").delete_one(
            {"organisation_id": org, "id": identity}
        )
        return True
    if not body.setup_id:
        raise ResourceConflictError("Choose a browser setup session")
    if body.operation == "get":
        return await service.get(org, body.setup_id)
    if body.operation == "complete":
        session, connection = await service.complete(
            org, body.setup_id, body.subject, body.token.get_secret_value(), body.revision
        )
        return {"session": session, "connection": connection}
    if body.operation == "abort":
        return await service.abort(org, body.setup_id, body.subject, body.revision)
    if body.operation == "stream":
        try:
            return await service.stream(
                org, body.setup_id, body.subject, body.token.get_secret_value(), body.data
            )
        except (KeyError, ValueError, TypeError):
            raise ResourceConflictError("Browser interaction could not be completed") from None
    raise ResourceConflictError("Unknown browser setup operation")


class ImageRequest(Contract):
    organisation_id: Identifier
    run_id: Identifier
    activity_id: Identifier
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/browser/image")
async def image(body: ImageRequest, runtime: Annotated[Runtime, Depends(authenticated)]) -> Any:
    content, kind = await runtime.browser_worker.images.read_image(
        body.organisation_id, body.run_id, body.activity_id, body.digest
    )
    return {"image": base64.b64encode(content).decode(), "content_type": kind}
