import base64
from typing import Annotated, Literal

from contracts import Contract, Identifier
from core.errors import ResourceConflictError, ResourceSetupRequiredError
from fastapi import APIRouter, Depends, HTTPException
from mail.models import ForwardInbox
from pydantic import Field

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


class SourceRequest(Contract):
    operation: Literal[
        "status",
        "email-get",
        "email-create",
        "email-dismiss-confirmation",
        "github-begin",
        "github-discover",
        "github-complete",
        "webhook",
    ]
    organisation_id: Identifier | None = None
    confirmation_url: str | None = Field(default=None, min_length=1, max_length=8192)
    subject: str | None = Field(default=None, min_length=1, max_length=512)
    session_id: Identifier | None = None
    state: str | None = Field(default=None, max_length=256)
    verifier: str | None = Field(default=None, max_length=128)
    code: str | None = Field(default=None, max_length=512)
    installation_id: int | None = Field(default=None, gt=0)
    source: Literal["email", "github"] | None = None
    body: str | None = Field(default=None, max_length=750_000)
    headers: dict[str, str] = Field(default_factory=dict, max_length=8)


@router.post("/incident-sources")
async def control(
    body: SourceRequest, service: Annotated[Runtime, Depends(authenticated)]
) -> object:
    sources, org = service.sources, body.organisation_id
    if body.operation == "status":
        return sources.status()
    if body.operation == "webhook" and body.source and body.body is not None:
        try:
            raw = base64.b64decode(body.body, validate=True)
            if len(raw) > 524_288:
                raise ValueError
            await sources.webhook(body.source, raw, body.headers)
            return {"accepted": True}
        except ValueError:
            raise HTTPException(400, "Invalid source webhook") from None
    if body.operation.startswith("email-") and org:
        if not service.config.email_receiving_ready:
            raise ResourceSetupRequiredError("Email receiving is not configured")
        inbox: ForwardInbox | None
        if body.operation == "email-dismiss-confirmation":
            if not body.confirmation_url:
                raise ResourceConflictError("Forwarding confirmation is missing")
            inbox = await sources.forwarding.dismiss_confirmation(org, body.confirmation_url)
        else:
            inbox = (
                await sources.forwarding.create(org)
                if body.operation == "email-create"
                else await sources.forwarding.get(org)
            )
        if inbox:
            connection = await service.sources.inventory.get_connection(org, inbox.id)
            if connection.archived_at or connection.status.value == "disabled":
                return None
        return inbox.model_dump(mode="json") if inbox else None
    if org and body.subject:
        github = sources.github()
        if body.operation == "github-begin":
            session, state, verifier, installation, authorization = await github.begin(
                org, body.subject
            )
            return {
                "session": session.model_dump(mode="json"),
                "state": state,
                "pkce_verifier": verifier,
                "installation_url": installation,
                "authorization_url": authorization,
            }
        if (
            body.operation == "github-discover"
            and body.session_id
            and body.state
            and body.verifier
            and body.code
        ):
            session, installed, repositories = await github.discover(
                org,
                body.session_id,
                body.subject,
                body.state,
                body.verifier,
                body.code,
                body.installation_id,
            )
            return {
                "session": session.model_dump(mode="json"),
                "installation": installed.model_dump(mode="json"),
                "repositories": [item.model_dump(mode="json") for item in repositories],
            }
        if body.operation == "github-complete" and body.session_id:
            session, installed, confirmed = await github.complete(
                org, body.session_id, body.subject
            )
            return {
                "session": session.model_dump(mode="json"),
                "installation": installed.model_dump(mode="json"),
                "repositories": [item.model_dump(mode="json") for item in confirmed],
            }
    raise ResourceConflictError("Incident source request is incomplete")
