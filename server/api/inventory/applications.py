from contracts import (
    Application,
    ConsumerService,
    Environment,
    Identifier,
)
from core.auth import Permission
from fastapi import APIRouter, Request, status

from api.deps import Identity, required, services
from api.inventory.schemas import (
    ApplicationSetupRequest,
    ApplicationSetupResponse,
    ApplicationUpdateRequest,
    ArchiveRequest,
    EnvironmentUpdateRequest,
    ServiceSetupRequest,
    ServiceUpdateRequest,
)
from api.inventory.shared import _audit_change, _organisation

router = APIRouter()


@router.post("/applications", response_model=Application, status_code=status.HTTP_201_CREATED)
async def add_application(
    organisation_id: Identifier,
    body: Application,
    identity: Identity,
    request: Request,
) -> Application:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.organisation_id, organisation_id)
    return await required(api.inventory, "inventory").add_application(body)


@router.post(
    "/application-setups",
    response_model=ApplicationSetupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_application_setup(
    organisation_id: Identifier,
    body: ApplicationSetupRequest,
    identity: Identity,
    request: Request,
) -> ApplicationSetupResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    application, environment, service = await required(
        api.inventory, "inventory"
    ).add_discovered_application_setup(
        organisation_id,
        body.application_id,
        body.environment_id,
        body.service_id,
        body.runtime_connection_id,
        body.runtime_resource,
        body.environment_name,
    )
    return ApplicationSetupResponse(
        application=application,
        environment=environment,
        service=service,
    )


@router.post("/environments", response_model=Environment, status_code=status.HTTP_201_CREATED)
async def add_environment(
    organisation_id: Identifier,
    body: Environment,
    identity: Identity,
    request: Request,
) -> Environment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.organisation_id, organisation_id)
    return await required(api.inventory, "inventory").add_environment(body)


@router.post("/services", response_model=ConsumerService, status_code=status.HTTP_201_CREATED)
async def add_service(
    organisation_id: Identifier,
    body: ServiceSetupRequest,
    identity: Identity,
    request: Request,
) -> ConsumerService:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await required(api.inventory, "inventory").add_discovered_service(
        organisation_id,
        body.id,
        body.application_id,
        body.environment_id,
        body.runtime_connection_id,
        body.runtime_resource,
    )


@router.get("/applications", response_model=tuple[Application, ...])
async def list_applications(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[Application, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_applications(organisation_id)


@router.get("/applications/{application_id}", response_model=Application)
async def get_application(
    organisation_id: Identifier,
    application_id: Identifier,
    identity: Identity,
    request: Request,
) -> Application:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_application(
        organisation_id, application_id
    )


@router.patch("/applications/{application_id}", response_model=Application)
async def update_application(
    organisation_id: Identifier,
    application_id: Identifier,
    body: ApplicationUpdateRequest,
    identity: Identity,
    request: Request,
) -> Application:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_application(
        organisation_id,
        application_id,
        body.expected_revision,
        display_name=body.display_name,
        repository_ids=body.repository_ids,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "application.updated",
        application_id,
        changed.revision,
    )
    return changed


@router.post("/applications/{application_id}/archive", response_model=Application)
async def archive_application(
    organisation_id: Identifier,
    application_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> Application:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").archive_application(
        organisation_id, application_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "application.archived",
        application_id,
        changed.revision,
    )
    return changed


@router.get("/environments", response_model=tuple[Environment, ...])
async def list_environments(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[Environment, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_environments(organisation_id)


@router.get("/environments/{environment_id}", response_model=Environment)
async def get_environment(
    organisation_id: Identifier,
    environment_id: Identifier,
    identity: Identity,
    request: Request,
) -> Environment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_environment(
        organisation_id, environment_id
    )


@router.patch("/environments/{environment_id}", response_model=Environment)
async def update_environment(
    organisation_id: Identifier,
    environment_id: Identifier,
    body: EnvironmentUpdateRequest,
    identity: Identity,
    request: Request,
) -> Environment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_environment(
        organisation_id,
        environment_id,
        body.expected_revision,
        display_name=body.display_name,
        production=body.production,
        region=body.region,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "environment.updated",
        environment_id,
        changed.revision,
    )
    return changed


@router.post("/environments/{environment_id}/archive", response_model=Environment)
async def archive_environment(
    organisation_id: Identifier,
    environment_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> Environment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").archive_environment(
        organisation_id, environment_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "environment.archived",
        environment_id,
        changed.revision,
    )
    return changed


@router.get("/services", response_model=tuple[ConsumerService, ...])
async def list_services(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[ConsumerService, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_services(organisation_id)


@router.get("/services/{service_id}", response_model=ConsumerService)
async def get_service(
    organisation_id: Identifier,
    service_id: Identifier,
    identity: Identity,
    request: Request,
) -> ConsumerService:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_service(organisation_id, service_id)


@router.patch("/services/{service_id}", response_model=ConsumerService)
async def update_service(
    organisation_id: Identifier,
    service_id: Identifier,
    body: ServiceUpdateRequest,
    identity: Identity,
    request: Request,
) -> ConsumerService:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_service(
        organisation_id,
        service_id,
        body.expected_revision,
        display_name=body.display_name,
        runtime_connection_id=body.runtime_connection_id,
        telemetry_connection_ids=body.telemetry_connection_ids,
        runtime_resource=body.runtime_resource,
        endpoint=body.endpoint,
        repository=body.repository,
        identity=body.identity,
    )
    await _audit_change(
        api, organisation_id, identity.actor_id, "service.updated", service_id, changed.revision
    )
    return changed


@router.post("/services/{service_id}/archive", response_model=ConsumerService)
async def archive_service(
    organisation_id: Identifier,
    service_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> ConsumerService:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").archive_service(
        organisation_id, service_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api, organisation_id, identity.actor_id, "service.archived", service_id, changed.revision
    )
    return changed
