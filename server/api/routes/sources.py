import base64
from typing import Literal

from contracts import Identifier
from core.auth import Permission
from core.errors import ResourceConflictError, ResourceSetupRequiredError
from fastapi import APIRouter, HTTPException, Request, status
from mail.models import ForwardInbox

from api.deps import Identity, required, services

router = APIRouter(tags=["incident sources"])
prefix = "/v1/organisations/{organisation_id}/connections/email"


@router.get(prefix, response_model=ForwardInbox | None)
async def get(organisation_id: Identifier, identity: Identity, request: Request) -> object:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.private, "email receiving").call(
        "/incident-sources", {"operation": "email-get", "organisation_id": organisation_id}
    )


@router.post(prefix, response_model=ForwardInbox, status_code=status.HTTP_201_CREATED)
async def create(organisation_id: Identifier, identity: Identity, request: Request) -> object:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await required(api.private, "email receiving").call(
        "/incident-sources", {"operation": "email-create", "organisation_id": organisation_id}
    )


@router.post("/v1/webhooks/{source}", status_code=status.HTTP_202_ACCEPTED)
async def webhook(source: Literal["resend", "github"], request: Request) -> object:
    api = services(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 524_288:
            raise HTTPException(413, "Webhook exceeded its limit")
    headers = {
        name: request.headers[name]
        for name in (
            "svix-id",
            "svix-timestamp",
            "svix-signature",
            "x-hub-signature-256",
            "x-github-delivery",
            "x-github-event",
        )
        if name in request.headers
    }
    try:
        return await required(api.private, "incident sources").call(
            "/incident-sources",
            {
                "operation": "webhook",
                "source": "email" if source == "resend" else source,
                "body": base64.b64encode(body).decode(),
                "headers": headers,
            },
            timeout=60,
        )
    except ResourceSetupRequiredError:
        raise HTTPException(503, "Incident source is not configured") from None
    except ResourceConflictError:
        raise HTTPException(400, "Webhook could not be accepted") from None
