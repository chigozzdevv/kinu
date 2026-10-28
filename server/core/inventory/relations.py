from contracts import (
    Application,
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConsumerBinding,
    ConsumerService,
    Contract,
    ControlVersion,
    CredentialGeneration,
    Environment,
    ManagedCredential,
    ProbeVersion,
)

from core.errors import ResourceConflictError
from core.storage.records import RecordTransaction


async def check_references(rows: RecordTransaction, value: Contract) -> None:
    references: list[tuple[type[Contract], str]] = []
    if isinstance(value, Connection) and value.verification_connection_id:
        references.append((Connection, value.verification_connection_id))
        source = await rows.get(Connection, value.organisation_id, value.verification_connection_id)
        from connectors.resend.client import Resend

        Resend.validate(source)
    elif isinstance(value, Environment):
        references.append((Application, value.application_id))
    elif isinstance(value, ConsumerService):
        references = [
            (Application, value.application_id),
            (Environment, value.environment_id),
            (Connection, value.runtime_connection_id),
        ]
        references.extend((Connection, item) for item in value.telemetry_connection_ids)
        environment = await rows.get(Environment, value.organisation_id, value.environment_id)
        if environment.application_id != value.application_id:
            raise ResourceConflictError("service does not belong to its application environment")
        runtime = await rows.get(Connection, value.organisation_id, value.runtime_connection_id)
        if (
            ConnectionRole.RUNTIME not in runtime.roles
            or runtime.interface is not ConnectionInterface.API
            or not _covered(value.runtime_resource, runtime.allowed_resources)
        ):
            raise ResourceConflictError("service runtime escapes its connection boundary")
        for item in value.telemetry_connection_ids:
            telemetry = await rows.get(Connection, value.organisation_id, item)
            if (
                ConnectionRole.TELEMETRY not in telemetry.roles
                or telemetry.interface is not ConnectionInterface.API
            ):
                raise ResourceConflictError("service requires telemetry connections")
    elif isinstance(value, ManagedCredential):
        references = [
            (Connection, value.connection_id),
            (Connection, value.secret_store_connection_id),
        ]
        references.extend((ConsumerService, item) for item in value.consumer_ids)
        if value.active_generation_id:
            references.append((CredentialGeneration, value.active_generation_id))
            generation = await rows.get(
                CredentialGeneration, value.organisation_id, value.active_generation_id
            )
            if (
                generation.credential_id != value.id
                or generation.secret_reference != value.secret_reference
            ):
                raise ResourceConflictError("credential generation lineage is inconsistent")
        references.append((ControlVersion, value.control_version))
        controls = await rows.get(ControlVersion, value.organisation_id, value.control_version)
        if controls.credential_id != value.id:
            raise ResourceConflictError("control version belongs to another credential")
        provider = await rows.get(Connection, value.organisation_id, value.connection_id)
        secret_store = await rows.get(
            Connection, value.organisation_id, value.secret_store_connection_id
        )
        if (
            ConnectionRole.PROVIDER not in provider.roles
            or ConnectionRole.SECRET_STORE not in secret_store.roles
            or not _covered(value.secret_reference, secret_store.allowed_resources)
        ):
            raise ResourceConflictError("credential escapes its connection boundary")
    elif isinstance(value, ConsumerBinding):
        references.extend(
            [
                (ManagedCredential, value.credential_id),
                (ConsumerService, value.service_id),
                (Environment, value.environment_id),
                (Connection, value.runtime_connection_id),
                (CredentialGeneration, value.current_generation_id),
            ]
        )
        service = await rows.get(ConsumerService, value.organisation_id, value.service_id)
        generation = await rows.get(
            CredentialGeneration, value.organisation_id, value.current_generation_id
        )
        credential = await rows.get(ManagedCredential, value.organisation_id, value.credential_id)
        if (
            value.service_id not in credential.consumer_ids
            or service.environment_id != value.environment_id
            or service.runtime_connection_id != value.runtime_connection_id
            or service.runtime_resource != value.runtime_resource
            or generation.credential_id != value.credential_id
            or generation.secret_reference != value.secret_reference
        ):
            raise ResourceConflictError("consumer binding lineage is inconsistent")
    elif isinstance(value, (CredentialGeneration, ControlVersion)):
        references.append((ManagedCredential, value.credential_id))
    elif isinstance(value, ProbeVersion):
        references.append((Connection, value.definition.connection_id))
        if value.definition.secret_connection_id:
            references.append((Connection, value.definition.secret_connection_id))
    for model, resource_id in references:
        org = str(value.model_dump()["organisation_id"])
        target = await rows.get(model, org, resource_id)
        if getattr(target, "archived_at", None) is not None:
            raise ResourceConflictError("inventory references an archived resource")


def _covered(resource: str, boundaries: tuple[str, ...]) -> bool:
    return any(
        resource == boundary or resource.startswith(boundary.rstrip("/") + "/")
        for boundary in boundaries
    )
