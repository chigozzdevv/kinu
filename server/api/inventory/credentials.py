import hashlib

from contracts import (
    ApprovalDecision,
    CancelRunCommand,
    ControlVersion,
    Identifier,
    IncidentStatus,
    ManagedCredential,
    RunStatus,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Request, Response, status

from api.deps import Identity, required, services
from api.inventory.schemas import (
    ArchiveRequest,
    ControlsUpdateRequest,
    CredentialControlsResponse,
    CredentialUpdateRequest,
    ImportCredentialRequest,
    InventoryGraph,
)
from api.inventory.shared import _audit_change, _organisation

router = APIRouter()
_ACTIVE_RUN_STATUSES = frozenset(
    {
        RunStatus.PENDING,
        RunStatus.RUNNING,
        RunStatus.PAUSED,
        RunStatus.RECOVERING,
        RunStatus.CLEANUP,
    }
)
_OPEN_INCIDENT_STATUSES = frozenset(
    status
    for status in IncidentStatus
    if status not in {IncidentStatus.RESOLVED, IncidentStatus.DISMISSED}
)


@router.post("/credentials", response_model=ManagedCredential)
async def import_credential(
    organisation_id: Identifier,
    body: ImportCredentialRequest,
    identity: Identity,
    request: Request,
    response: Response,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.credential.organisation_id, organisation_id)
    _organisation(body.generation.organisation_id, organisation_id)
    result = await required(api.inventory, "inventory").import_discovered_credential(
        body.credential,
        body.generation,
        body.consumer,
        body.controls,
        identity.actor_id,
    )
    response.status_code = status.HTTP_201_CREATED
    return result


@router.get("/credentials", response_model=tuple[ManagedCredential, ...])
async def list_credentials(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[ManagedCredential, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_credentials(organisation_id)


@router.get("/credentials/{credential_id}", response_model=ManagedCredential)
async def get_credential(
    organisation_id: Identifier,
    credential_id: Identifier,
    identity: Identity,
    request: Request,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_credential(organisation_id, credential_id)


@router.patch("/credentials/{credential_id}", response_model=ManagedCredential)
async def update_credential(
    organisation_id: Identifier,
    credential_id: Identifier,
    body: CredentialUpdateRequest,
    identity: Identity,
    request: Request,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_credential(
        organisation_id,
        credential_id,
        body.expected_revision,
        display_name=body.display_name,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "credential.updated",
        credential_id,
        changed.revision,
    )
    return changed


@router.get(
    "/credentials/{credential_id}/controls/{version_id}",
    response_model=ControlVersion,
)
async def get_credential_controls(
    organisation_id: Identifier,
    credential_id: Identifier,
    version_id: Identifier,
    identity: Identity,
    request: Request,
) -> ControlVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_controls(
        organisation_id, credential_id, version_id
    )


@router.post(
    "/credentials/{credential_id}/controls",
    response_model=CredentialControlsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def update_credential_controls(
    organisation_id: Identifier,
    credential_id: Identifier,
    body: ControlsUpdateRequest,
    identity: Identity,
    request: Request,
) -> CredentialControlsResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    credential, controls = await required(api.inventory, "inventory").update_controls(
        organisation_id,
        credential_id,
        body.expected_revision,
        body.version_id,
        body.controls,
        identity.actor_id,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "credential.controls.updated",
        credential_id,
        credential.revision,
    )
    return CredentialControlsResponse(credential=credential, controls=controls)


@router.post("/credentials/{credential_id}/archive", response_model=ManagedCredential)
async def archive_credential(
    organisation_id: Identifier,
    credential_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    inventory = required(api.inventory, "inventory")
    credential = await inventory.get_credential(organisation_id, credential_id)
    if credential.revision != body.expected_revision:
        raise ResourceConflictError(
            f"credential expected revision {body.expected_revision}, found {credential.revision}"
        )
    if not body.cascade:
        changed = await inventory.archive_credential(
            organisation_id, credential_id, body.expected_revision, False
        )
        await _audit_change(
            api,
            organisation_id,
            identity.actor_id,
            "credential.archived",
            credential_id,
            changed.revision,
        )
        return changed
    all_runs = await required(api.workflow, "workflow").list_runs(organisation_id, None, 500)
    related_runs = tuple(run for run in all_runs if run.credential_id == credential_id)
    active_runs = tuple(run for run in related_runs if run.status in _ACTIVE_RUN_STATUSES)
    for run in active_runs:
        await required(api.workflow, "workflow").cancel(
            CancelRunCommand(
                id=_delete_command_id(credential_id, run.id),
                organisation_id=organisation_id,
                run_id=run.id,
                actor_id=identity.actor_id,
                expected_revision=run.revision,
            )
        )
    related_run_ids = {run.id for run in related_runs}
    pending_approvals = await required(api.approvals, "approvals").list_approvals(
        organisation_id, frozenset({ApprovalDecision.PENDING}), 500
    )
    for approval in pending_approvals:
        if approval.run_id in related_run_ids:
            await required(api.approvals, "approvals").decide(
                organisation_id,
                approval.id,
                approval.revision,
                ApprovalDecision.CANCELLED,
                identity.actor_id,
            )
    open_incidents = await required(api.incidents, "incidents").list_incidents(
        organisation_id, _OPEN_INCIDENT_STATUSES, 500
    )
    for incident in open_incidents:
        if incident.credential_id == credential_id:
            await required(api.incidents, "incidents").dismiss(
                organisation_id,
                incident.id,
                incident.revision,
                "Credential removed from Kinu.",
                identity.actor_id,
            )
    changed = await inventory.archive_credential(
        organisation_id, credential_id, body.expected_revision, True
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "credential.archived",
        credential_id,
        changed.revision,
    )
    return changed


def _delete_command_id(credential_id: str, run_id: str) -> str:
    value = hashlib.sha256(f"{credential_id}\0{run_id}\0delete".encode()).hexdigest()
    return f"cmd_{value[:40]}"


@router.get("/graph", response_model=InventoryGraph)
async def graph(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> InventoryGraph:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    credentials, consumer_services, bindings = await required(api.inventory, "inventory").graph(
        organisation_id
    )
    return InventoryGraph(
        credentials=credentials,
        services=consumer_services,
        bindings=bindings,
    )
