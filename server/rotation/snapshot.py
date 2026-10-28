from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ControlVersion,
    CredentialGeneration,
    GenerationState,
    ManagedCredential,
)
from core.connection.roles import ConnectionRoles
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records
from policy import digest

from rotation.ports import Loader
from rotation.state import Snapshot


def loader(database: Database) -> Loader:
    async def load(org: str, credential: str, controls: str) -> Snapshot:
        return await snapshot(database, org, credential, controls)

    return load


async def snapshot(database: Database, org: str, credential_id: str, controls: str) -> Snapshot:
    async with Records(database).read() as rows:
        credential = await rows.get(ManagedCredential, org, credential_id)
        control = await rows.get(ControlVersion, org, controls)
        bindings = await rows.match(ConsumerBinding, org, {"credential_id": credential_id})
        if len(bindings) != 1 or credential.consumer_ids != (bindings[0].service_id,):
            raise ResourceConflictError("Lambda rotation requires one selected consumer")
        binding = bindings[0]
        if not credential.active_generation_id:
            raise ResourceConflictError("credential has no active generation")
        current = await rows.get(CredentialGeneration, org, credential.active_generation_id)
        store = await rows.get(Connection, org, credential.secret_store_connection_id)
        runtime = await rows.get(Connection, org, binding.runtime_connection_id)
        provider = await rows.get(Connection, org, credential.connection_id)
    required = {
        "provider.createCredential",
        "provider.revokeCredential",
        "provider.testCredential",
        "runtime.deployCandidate",
        "runtime.shiftTraffic",
        "runtime.rollback",
        "secretStore.disableVersion",
    }
    if (
        credential.archived_at
        or current.state is not GenerationState.ACTIVE
        or current.credential_id != credential.id
        or current.secret_reference != credential.secret_reference
        or current.provider_id != credential.provider_id
        or not current.provider_id
        or binding.current_generation_id != current.id
        or binding.secret_reference != current.secret_reference
        or credential.control_version != controls
        or control.credential_id != credential_id
        or control.digest != digest(control.definition)
        or control.definition.require_revoke_approval
        or control.definition.require_generation_telemetry
        or not 60 <= control.definition.maximum_observation_seconds <= 600
        or not control.definition.preserve_old_generation
        or not required <= control.definition.allowed_tools
        or binding.runtime_container_name is not None
        or provider.interface is not ConnectionInterface.API
        or provider.roles != frozenset({ConnectionRole.PROVIDER})
        or provider.http is None
        or not provider.http.supports_overlap
        or provider.http.test_credential is None
        or provider.http.list_credentials.name_field is None
        or provider.http.list_credentials.method != "GET"
        or provider.http.test_credential.method != "GET"
        or provider.http.credential_auth is None
        or provider.platform != credential.provider
        or (provider.authorization_reference or "").partition("/versions/")[0]
        == credential.secret_resource
        or credential.secret_resource not in store.allowed_resources
        or binding.runtime_resource not in runtime.allowed_resources
        or any(
            item.archived_at or item.status is not ConnectionStatus.READY
            for item in (store, runtime, provider)
        )
    ):
        raise ResourceConflictError(
            "Selected resources or controls are unsupported by the Lambda API executor."
        )
    roles = ConnectionRoles(database, managed=True, read_only=True)
    await roles.resolve(store)
    await roles.resolve(runtime)
    return Snapshot(credential, binding, current, control, store, runtime, provider)
