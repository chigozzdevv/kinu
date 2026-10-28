import hashlib
from datetime import UTC, datetime

from connectors.hosting.models import Variable
from contracts import Application, Connection, ConsumerService, Contract, Environment
from core.errors import ResourceConflictError
from core.storage.records import RecordTransaction

from hosting.service import project_ref


def identity(prefix: str, *parts: str) -> str:
    return prefix + "_" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]


async def ensure[T: Contract](rows: RecordTransaction, org: str, value: T) -> T:
    matches = await rows.match(type(value), org, {"id": str(value.model_dump()["id"])})
    if matches:
        if getattr(matches[0], "archived_at", None):
            raise ResourceConflictError("Selected workload was archived")
        return matches[0]
    return await rows.add(value, org)


async def workload(
    rows: RecordTransaction,
    connection: Connection,
    variable: Variable,
    project_name: str,
    health_url: str | None,
) -> ConsumerService:
    org, now = connection.organisation_id, datetime.now(UTC)
    app_id = identity("app", connection.id, variable.project_id)
    env_id = identity("environment", app_id, variable.environment)
    service_id = identity("service", env_id, variable.service_id or "web")
    application = await ensure(
        rows,
        org,
        Application(
            id=app_id,
            organisation_id=org,
            display_name=project_name[:160],
            created_at=now,
            updated_at=now,
        ),
    )
    environment = await ensure(
        rows,
        org,
        Environment(
            id=env_id,
            organisation_id=org,
            application_id=application.id,
            display_name=variable.environment_name or variable.environment,
            production=(variable.environment_name or variable.environment).lower() == "production",
            region="global",
            created_at=now,
            updated_at=now,
        ),
    )
    resource = project_ref(connection.platform, variable.project_id) + "/" + variable.environment
    if variable.service_id:
        resource += "/" + variable.service_id
    service = await ensure(
        rows,
        org,
        ConsumerService(
            id=service_id,
            organisation_id=org,
            application_id=application.id,
            environment_id=environment.id,
            runtime_connection_id=connection.id,
            runtime_resource=resource,
            display_name=(variable.service_name or project_name)[:160],
            endpoint=health_url,
            created_at=now,
            updated_at=now,
        ),
    )
    if health_url is not None and service.endpoint != health_url:
        raise ResourceConflictError("Use the workload's configured health URL")
    return service
