from connectors.hosting.models import Variable
from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    CredentialGeneration,
    GenerationState,
    ManagedCredential,
    PlaybookState,
    PlaybookVersion,
)
from core.connection.roles import ConnectionRoles
from core.errors import ResourceConflictError
from core.inventory.relations import _covered
from core.storage.database import Database
from core.storage.records import Records
from executor.versions import store_id
from policy import digest

from rotation.ports import Loader
from rotation.state import Snapshot, Target


def loader(database: Database) -> Loader:
    async def load(org: str, credential: str, controls: str) -> Snapshot:
        return await snapshot(database, org, credential, controls)

    return load


async def snapshot(database: Database, org: str, credential_id: str, controls: str) -> Snapshot:
    async with Records(database).read() as rows:
        credential = await rows.get(ManagedCredential, org, credential_id)
        control = await rows.get(ControlVersion, org, controls)
        bindings = await rows.match(ConsumerBinding, org, {"credential_id": credential_id})
        if not bindings or set(credential.consumer_ids) != {item.service_id for item in bindings}:
            raise ResourceConflictError("Select every consumer using this credential")
        if not credential.active_generation_id:
            raise ResourceConflictError("Credential has no active generation")
        matches = await rows.match(
            ManagedCredential,
            org,
            {
                "connection_id": credential.connection_id,
                "provider_id": credential.provider_id,
                "archived_at": None,
            },
        )
        if len(matches) != 1:
            raise ResourceConflictError("Provider key is managed by more than one credential")
        current = await rows.get(CredentialGeneration, org, credential.active_generation_id)
        store = await rows.get(Connection, org, credential.secret_store_connection_id)
        provider = await rows.get(Connection, org, credential.connection_id)
        playbook, verification = None, None
        if provider.interface is ConnectionInterface.BROWSER:
            if not provider.playbook_version_id or store.platform != "kinu":
                raise ResourceConflictError(
                    "Browser rotation requires a published playbook and the workspace vault"
                )
            playbook = await rows.get(PlaybookVersion, org, provider.playbook_version_id)
            verifier = playbook.definition.verifier
            if verifier and verifier.mode == "resend-receipt":
                if provider.platform != "resend" or not provider.verification_connection_id:
                    raise ResourceConflictError("Select a Resend verification account")
                verification = await rows.get(Connection, org, provider.verification_connection_id)
                from connectors.resend.client import Resend

                Resend.validate(verification)
                if verification.archived_at or verification.status is not ConnectionStatus.READY:
                    raise ResourceConflictError("Reconnect the Resend verification account")
            if (
                playbook.playbook_id != provider.playbook_id
                or playbook.state is not PlaybookState.PUBLISHED
                or playbook.digest != digest(playbook.definition)
                or not verifier
                or not verifier.supports_overlap
                or not set(playbook.definition.allowed_domains) <= set(provider.allowed_resources)
            ):
                raise ResourceConflictError(
                    "Publish a browser playbook with overlapping-key verification"
                )
        targets = []
        for binding in bindings:
            runtime = await rows.get(Connection, org, binding.runtime_connection_id)
            service = await rows.get(ConsumerService, org, binding.service_id)
            mapping = await database.collection("host_bindings").find_one(
                {"organisation_id": org, "binding_id": binding.id}
            )
            variable = Variable.model_validate(mapping["variable"]) if mapping else None
            if runtime.platform == "vercel" and variable and variable.environment != "production":
                raise ResourceConflictError(
                    "Vercel rotation currently requires a production variable"
                )
            if (
                binding.current_generation_id != current.id
                or binding.secret_reference != current.secret_reference
                or binding.runtime_resource != service.runtime_resource
                or binding.runtime_connection_id != service.runtime_connection_id
                or not _covered(binding.runtime_resource, runtime.allowed_resources)
                or ConnectionRole.RUNTIME not in runtime.roles
                or service.archived_at
            ):
                raise ResourceConflictError("Consumer binding changed")
            if store.platform == "kinu" and (
                runtime.platform not in {"vercel", "railway", "render"}
                or variable is None
                or not service.endpoint
                or variable.name != binding.runtime_secret_name
                or not binding.runtime_resource.startswith(
                    runtime.platform + "://" + variable.project_id + "/"
                )
                or not mapping
                or mapping["connection_id"] != runtime.id
                or mapping["credential_id"] != credential.id
            ):
                raise ResourceConflictError(
                    "Configure the selected workload and its health URL before rotating"
                )
            targets.append(Target(binding, runtime, service, variable))
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
        ConnectionRole.SECRET_STORE not in store.roles
        or store.interface is not ConnectionInterface.API
        or credential.archived_at
        or current.state is not GenerationState.ACTIVE
        or current.credential_id != credential.id
        or current.secret_reference != credential.secret_reference
        or current.provider_id != credential.provider_id
        or not current.provider_id
        or credential.control_version != controls
        or control.credential_id != credential_id
        or control.digest != digest(control.definition)
        or control.definition.require_generation_telemetry
        or not 60 <= control.definition.maximum_observation_seconds <= 600
        or not control.definition.preserve_old_generation
        or not required <= control.definition.allowed_tools
        or provider.interface not in {ConnectionInterface.API, ConnectionInterface.BROWSER}
        or provider.roles != frozenset({ConnectionRole.PROVIDER})
        or (
            provider.interface is ConnectionInterface.API
            and (
                provider.http is None
                or not provider.http.supports_overlap
                or provider.http.test_credential is None
                or provider.http.list_credentials.name_field is None
                or provider.http.list_credentials.method != "GET"
                or provider.http.test_credential.method != "GET"
                or provider.http.credential_auth is None
            )
        )
        or (provider.authorization_reference or "").partition("/versions/")[0]
        == credential.secret_resource
        or provider.platform != credential.provider
        or not _covered(credential.secret_resource, store.allowed_resources)
        or any(
            item.archived_at or item.status is not ConnectionStatus.READY
            for item in (store, provider, *(target.runtime for target in targets))
        )
    ):
        raise ResourceConflictError(
            "Selected resources or controls are unsupported by this executor"
        )
    if store.platform == "kinu":
        if store.id != store_id(org):
            raise ResourceConflictError("Select the workspace vault")
    else:
        if len(targets) != 1 or targets[0].binding.runtime_container_name is not None:
            raise ResourceConflictError("Lambda rotation requires one selected consumer")
        roles = ConnectionRoles(database, managed=True, read_only=True)
        await roles.resolve(store)
        await roles.resolve(targets[0].runtime)
    return Snapshot(
        credential, tuple(targets), current, control, store, provider, playbook, verification
    )
