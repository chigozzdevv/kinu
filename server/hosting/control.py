from typing import Any, Literal

from contracts import Contract, Identifier
from core.errors import ResourceConflictError
from pydantic import Field, SecretStr

from hosting.models import Begin, Platform, Selection
from hosting.service import Service


class Request(Contract):
    operation: Literal["begin", "owner", "finish", "projects", "save", "variables"]
    organisation_id: Identifier | None = None
    actor_id: Identifier
    session: str = Field(pattern=r"^[a-f0-9]{64}$")
    platform: Platform | None = None
    connection_id: Identifier | None = None
    project_id: str | None = Field(default=None, max_length=256)
    state: str = Field(default="", max_length=128)
    code: SecretStr | None = Field(default=None, max_length=4096)
    data: dict[str, Any] = Field(default_factory=dict, max_length=5)


async def control(service: Service, request: Request) -> Any:
    body, actor, session = request, request.actor_id, request.session
    if body.operation == "owner" and body.platform:
        return await service.owner(body.platform, body.state, actor, session)
    org = body.organisation_id
    if not org:
        raise ResourceConflictError("Select a workspace")
    if body.operation == "begin":
        return await service.begin(org, actor, session, Begin.model_validate(body.data))
    if body.operation == "finish" and body.platform:
        return await service.finish(
            org,
            body.platform,
            body.state,
            actor,
            session,
            body.code.get_secret_value() if body.code else None,
        )
    if not body.connection_id:
        raise ResourceConflictError("Choose a hosting connection")
    if body.operation == "projects":
        return await service.projects(org, body.connection_id, actor, session)
    if body.operation == "save":
        return await service.save(
            org, body.connection_id, actor, session, Selection.model_validate(body.data)
        )
    if body.operation == "variables" and body.project_id:
        return await service.variables(org, body.connection_id, body.project_id)
    raise ResourceConflictError("Unknown hosting operation")
