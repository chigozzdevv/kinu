from contracts import (
    Connection,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    CredentialGeneration,
    ManagedCredential,
)
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records
from policy import digest
from rotation.ports import Loader
from rotation.state import Snapshot, Target

from demo.config import DemoConfig


async def snapshot(
    database: Database, config: DemoConfig, org: str, credential_id: str, controls: str
) -> Snapshot:
    if org != config.organisation_id or credential_id != config.credential_id:
        raise ResourceConflictError("the demo worker only manages its assigned demo credential")
    async with Records(database).read() as rows:
        credential = await rows.get(ManagedCredential, org, credential_id)
        control = await rows.get(ControlVersion, org, controls)
        bindings = await rows.match(ConsumerBinding, org, {"credential_id": credential_id})
        if len(bindings) != 1 or credential.consumer_ids != (bindings[0].service_id,):
            raise ResourceConflictError("the API demo requires its single declared consumer")
        binding = bindings[0]
        if not credential.active_generation_id:
            raise ResourceConflictError("the credential has no current generation")
        current = await rows.get(CredentialGeneration, org, credential.active_generation_id)
        store = await rows.get(Connection, org, credential.secret_store_connection_id)
        runtime = await rows.get(Connection, org, binding.runtime_connection_id)
        provider = await rows.get(Connection, org, credential.connection_id)
        service = await rows.get(ConsumerService, org, binding.service_id)
    if (
        credential.archived_at
        or credential.secret_resource != config.secret
        or binding.runtime_resource != config.consumer_function
        or current.secret_reference != credential.secret_reference
        or current.provider_id != credential.provider_id
        or binding.current_generation_id != current.id
        or credential.control_version != controls
        or control.credential_id != credential_id
        or control.digest != digest(control.definition)
        or control.definition.require_revoke_approval
        or credential.scopes != frozenset({"demo:read"})
    ):
        raise ResourceConflictError("demo credential mappings or controls require attention")
    return Snapshot(
        credential, (Target(binding, runtime, service),), current, control, store, provider
    )


def loader(database: Database, config: DemoConfig) -> Loader:
    async def load(org: str, credential: str, controls: str) -> Snapshot:
        return await snapshot(database, config, org, credential, controls)

    return load
