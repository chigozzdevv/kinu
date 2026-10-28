from urllib.parse import urlencode

from contracts import Contract, Identifier
from core.auth import Permission
from core.errors import AuthenticationError, KinuError
from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from mail.models import CALLBACK, Mailbox, SenderUpdate
from pydantic import Field

from api.deps import Identity, browser_session, required, services, session_binding
from api.inventory.shared import _audit_change

router = APIRouter(tags=["connections"])
PREFIX = "/v1/organisations/{organisation_id}/connections/gmail"


class BeginRequest(Contract):
    mailbox_id: Identifier | None = None


class DisconnectRequest(Contract):
    expected_revision: int = Field(ge=0)


@router.get(PREFIX)
async def list_mailboxes(
    organisation_id: Identifier, identity: Identity, request: Request
) -> tuple[Mailbox, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await required(api.gmail, "gmail").list(organisation_id)


@router.post(PREFIX)
async def begin(
    organisation_id: Identifier, body: BeginRequest, identity: Identity, request: Request
) -> dict[str, str]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    target = await required(api.gmail, "gmail").begin(
        organisation_id, identity.actor_id, session_binding(request), body.mailbox_id
    )
    return {"authorization_url": target}


@router.get(CALLBACK)
async def callback(
    request: Request,
    state: str = Query(default="", max_length=128),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=256),
    iss: str | None = Query(default=None, max_length=128),
) -> RedirectResponse:
    destination = "/dashboard/connections?gmail=failed"
    try:
        current = await browser_session(request)
        api = services(request)
        gmail = required(api.gmail, "gmail")
        bound = session_binding(request)
        org = await gmail.owner(state, current.identity.actor_id, bound)
        await api.access.require(current.identity, org, Permission.INVENTORY_WRITE)
        approved = code if not error and iss in {None, "https://accounts.google.com"} else None
        mailbox = await gmail.finish(org, current.identity.actor_id, bound, state, approved)
        destination = "/dashboard/connections?" + urlencode({"gmail": mailbox.id, "workspace": org})
        await _audit_change(
            api, org, current.identity.actor_id, "gmail.connected", mailbox.id, mailbox.revision
        )
    except AuthenticationError:
        destination = "/dashboard/connections?gmail=expired"
    except KinuError:
        pass
    return RedirectResponse(
        destination,
        status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get(PREFIX + "/{mailbox_id}")
async def get(
    organisation_id: Identifier, mailbox_id: Identifier, identity: Identity, request: Request
) -> Mailbox:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await required(api.gmail, "gmail").get(organisation_id, mailbox_id)


@router.patch(PREFIX + "/{mailbox_id}")
async def senders(
    organisation_id: Identifier,
    mailbox_id: Identifier,
    body: SenderUpdate,
    identity: Identity,
    request: Request,
) -> Mailbox:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    result = await required(api.gmail, "gmail").senders(organisation_id, mailbox_id, body)
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "gmail.senders-updated",
        mailbox_id,
        result.revision,
    )
    return result


@router.post(PREFIX + "/{mailbox_id}/disconnect")
async def disconnect(
    organisation_id: Identifier,
    mailbox_id: Identifier,
    body: DisconnectRequest,
    identity: Identity,
    request: Request,
) -> Mailbox:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    result = await required(api.gmail, "gmail").disconnect(
        organisation_id, mailbox_id, body.expected_revision
    )
    await _audit_change(
        api, organisation_id, identity.actor_id, "gmail.disconnected", mailbox_id, result.revision
    )
    return result
