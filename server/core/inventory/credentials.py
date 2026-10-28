import hashlib

from contracts import (
    Application,
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlPreferences,
    ControlVersion,
    CredentialGeneration,
    Environment,
    ManagedCredential,
    ProviderCredentialMetadata,
    RuntimeConsumerSetup,
)
from policy import digest

from core.errors import ResourceConflictError
from core.inventory.catalog import InventoryCatalog
from core.inventory.controls import compile_controls
from core.inventory.validation import (
    _required_text,
    _resource_covered,
    _runtime_binding_matches,
)


class CredentialService:
    def __init__(self, catalog: InventoryCatalog) -> None:
        self.catalog = catalog

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        preferences: ControlPreferences,
        actor_id: str,
        service_setup: tuple[Application, Environment, ConsumerService] | None = None,
    ) -> ManagedCredential:
        management = await self.catalog.repository.get_connection(
            credential.organisation_id,
            credential.connection_id,
        )
        if (
            ConnectionRole.PROVIDER not in management.roles
            or management.platform != credential.provider
            or management.archived_at is not None
            or management.status is not ConnectionStatus.READY
        ):
            raise ResourceConflictError(
                "credential requires a provider connection for the same platform"
            )
        if management.interface is ConnectionInterface.BROWSER and (
            management.playbook_id is None or management.playbook_version_id is None
        ):
            raise ResourceConflictError("browser-managed credentials require a connection playbook")
        secret_store = await self.catalog.repository.get_connection(
            credential.organisation_id,
            credential.secret_store_connection_id,
        )
        if (
            ConnectionRole.SECRET_STORE not in secret_store.roles
            or secret_store.interface is not ConnectionInterface.API
            or secret_store.archived_at is not None
            or secret_store.status is not ConnectionStatus.READY
        ):
            raise ResourceConflictError("credential requires an API secret-store connection")
        if not any(
            credential.secret_resource == boundary
            or credential.secret_resource.startswith(boundary.rstrip("/") + "/")
            for boundary in secret_store.allowed_resources
        ):
            raise ResourceConflictError("credential secret resource escapes the secret store")
        if generation.credential_id != credential.id:
            raise ResourceConflictError("generation does not belong to the credential")
        if credential.active_generation_id != generation.id:
            raise ResourceConflictError("imported generation must be the active generation")
        if credential.secret_reference != generation.secret_reference:
            raise ResourceConflictError("credential and active generation secret references differ")
        if credential.secret_reference.partition("/versions/")[0] != credential.secret_resource:
            raise ResourceConflictError("credential secret version does not belong to its resource")
        if "/versions/" not in credential.secret_reference:
            raise ResourceConflictError(
                "credential secret reference must identify one immutable version"
            )
        if len(bindings) != len(credential.consumer_ids) or set(credential.consumer_ids) != {
            binding.service_id for binding in bindings
        }:
            raise ResourceConflictError("credential consumers and bindings must match exactly")
        if any(
            binding.credential_id != credential.id
            or binding.current_generation_id != generation.id
            or binding.organisation_id != credential.organisation_id
            for binding in bindings
        ):
            raise ResourceConflictError("credential binding lineage is inconsistent")
        setup_service = service_setup[2] if service_setup is not None else None
        services: list[ConsumerService] = []
        connections: dict[str, Connection] = {
            connection.id: connection
            for connection in await self.catalog.repository.connections(credential.organisation_id)
            if connection.archived_at is None
        }
        connections.update({management.id: management, secret_store.id: secret_store})
        for binding in bindings:
            service = (
                setup_service
                if setup_service is not None and setup_service.id == binding.service_id
                else await self.catalog.repository.get_service(
                    credential.organisation_id,
                    binding.service_id,
                )
            )
            if (
                binding.environment_id != service.environment_id
                or binding.runtime_connection_id != service.runtime_connection_id
                or binding.runtime_resource != service.runtime_resource
            ):
                raise ResourceConflictError(
                    "credential binding does not match its consumer service"
                )
            runtime = await self.catalog.repository.get_connection(
                credential.organisation_id,
                binding.runtime_connection_id,
            )
            if (
                ConnectionRole.RUNTIME not in runtime.roles
                or runtime.interface is not ConnectionInterface.API
                or runtime.archived_at is not None
                or runtime.status is not ConnectionStatus.READY
            ):
                raise ResourceConflictError("credential binding requires an API runtime connection")
            if binding.secret_reference != generation.secret_reference:
                raise ResourceConflictError("credential binding must use the imported generation")
            resources = await self.catalog.list_runtime_resources(
                credential.organisation_id, binding.runtime_connection_id
            )
            runtime_resource = next(
                (item for item in resources if item.reference == binding.runtime_resource), None
            )
            if runtime_resource is None or not _runtime_binding_matches(
                runtime_resource,
                credential.secret_resource,
                credential.secret_reference,
                binding.runtime_secret_name,
                binding.runtime_container_name,
            ):
                raise ResourceConflictError(
                    "runtime service does not use the selected secret version"
                )
            services.append(service)
            connections[runtime.id] = runtime
            for connection_id in service.telemetry_connection_ids:
                telemetry = await self.catalog.repository.get_connection(
                    credential.organisation_id, connection_id
                )
                if (
                    ConnectionRole.TELEMETRY not in telemetry.roles
                    or telemetry.interface is not ConnectionInterface.API
                    or telemetry.archived_at is not None
                    or telemetry.status is not ConnectionStatus.READY
                ):
                    raise ResourceConflictError(
                        "credential consumer requires ready API telemetry connections"
                    )
                connections[telemetry.id] = telemetry
        await self._verify_import_binding(credential, management, secret_store)
        now = self.catalog.clock()
        definition, probes = compile_controls(
            credential,
            bindings,
            tuple(services),
            tuple(connections.values()),
            preferences,
            actor_id,
            now,
        )
        controls = ControlVersion(
            id=credential.control_version,
            organisation_id=credential.organisation_id,
            credential_id=credential.id,
            number=1,
            definition=definition,
            digest=digest(definition),
            created_by=actor_id,
            created_at=now,
        )
        return await self.catalog.repository.import_credential(
            credential, generation, bindings, controls, probes, service_setup
        )

    async def import_discovered_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        consumer: RuntimeConsumerSetup,
        preferences: ControlPreferences,
        actor_id: str,
    ) -> ManagedCredential:
        resources = await self.catalog.list_runtime_resources(
            credential.organisation_id, consumer.runtime_connection_id
        )
        selected = tuple(item for item in resources if item.reference == consumer.runtime_resource)
        if len(selected) != 1:
            raise ResourceConflictError("runtime service is no longer available")
        resource = selected[0]
        existing = tuple(
            service
            for service in await self.catalog.repository.services(credential.organisation_id)
            if service.archived_at is None
            and service.runtime_connection_id == consumer.runtime_connection_id
            and service.runtime_resource == consumer.runtime_resource
        )
        if len(existing) > 1:
            raise ResourceConflictError("runtime service mapping is ambiguous")
        service_setup: tuple[Application, Environment, ConsumerService] | None = None
        if existing:
            service = existing[0]
            if (
                consumer.service_id != service.id
                or consumer.application_id != service.application_id
                or consumer.environment_id != service.environment_id
            ):
                raise ResourceConflictError("runtime service selection changed before import")
        else:
            resolved_environment = resource.environment_name or consumer.environment_name
            if not resolved_environment:
                raise ResourceConflictError("runtime service environment is unavailable")
            now = self.catalog.clock()
            application = Application(
                id=consumer.application_id,
                organisation_id=credential.organisation_id,
                display_name=resource.display_name,
                created_at=now,
                updated_at=now,
            )
            environment = Environment(
                id=consumer.environment_id,
                organisation_id=credential.organisation_id,
                application_id=application.id,
                display_name=resolved_environment,
                production=(
                    resource.production
                    if resource.production is not None
                    else resolved_environment.lower() == "production"
                ),
                region=_required_text(resource.region, "runtime region"),
                created_at=now,
                updated_at=now,
            )
            service = ConsumerService(
                id=consumer.service_id,
                organisation_id=credential.organisation_id,
                application_id=application.id,
                environment_id=environment.id,
                runtime_connection_id=consumer.runtime_connection_id,
                runtime_resource=resource.reference,
                display_name=resource.display_name,
                endpoint=resource.endpoint,
                identity=resource.identity,
                created_at=now,
                updated_at=now,
            )
            service_setup = (application, environment, service)
        binding = ConsumerBinding(
            id=consumer.binding_id,
            organisation_id=credential.organisation_id,
            credential_id=credential.id,
            service_id=service.id,
            environment_id=service.environment_id,
            runtime_connection_id=service.runtime_connection_id,
            runtime_resource=service.runtime_resource,
            runtime_secret_name=consumer.runtime_secret_name,
            runtime_container_name=consumer.runtime_container_name,
            secret_reference=credential.secret_reference,
            current_generation_id=generation.id,
        )
        return await self.import_credential(
            credential,
            generation,
            (binding,),
            preferences,
            actor_id,
            service_setup,
        )

    async def _verify_import_binding(
        self,
        credential: ManagedCredential,
        management: Connection,
        secret_store: Connection,
    ) -> None:
        metadata = await self._resolve_credential(
            management,
            secret_store,
            credential.secret_reference,
        )
        if management.interface is ConnectionInterface.BROWSER:
            if credential.provider_id is not None:
                raise ResourceConflictError(
                    "browser credential identity is assigned by the first rotation"
                )
            if metadata.kind != credential.kind:
                raise ResourceConflictError("credential type differs from browser metadata")
            if credential.scopes:
                raise ResourceConflictError(
                    "browser credential scopes are assigned by the provider during rotation"
                )
        else:
            if credential.provider_id is None:
                raise ResourceConflictError("credential import requires a provider credential ID")
            if metadata.provider_id != credential.provider_id:
                raise ResourceConflictError("credential identity differs from provider metadata")
            if metadata.kind != credential.kind:
                raise ResourceConflictError("credential type differs from provider metadata")
            if frozenset(metadata.scopes) != credential.scopes:
                raise ResourceConflictError("credential scopes differ from provider metadata")
        if management.interface is ConnectionInterface.BROWSER:
            existing = await self.catalog.repository.credentials(credential.organisation_id)
            if any(
                item.archived_at is None
                and item.connection_id == credential.connection_id
                and item.secret_reference == credential.secret_reference
                for item in existing
            ):
                raise ResourceConflictError(
                    "secret version is already imported for this browser connection"
                )
            return
        existing = await self.catalog.repository.credentials(credential.organisation_id)
        if any(
            item.archived_at is None
            and item.connection_id == credential.connection_id
            and item.provider_id == credential.provider_id
            for item in existing
        ):
            raise ResourceConflictError("provider credential is already imported")

    async def resolve_credential(
        self,
        organisation_id: str,
        connection_id: str,
        secret_store_connection_id: str,
        secret_reference: str,
    ) -> ProviderCredentialMetadata:
        management = await self.catalog.get_connection(organisation_id, connection_id)
        secret_store = await self.catalog.secret_connection(
            organisation_id, secret_store_connection_id
        )
        if (
            ConnectionRole.PROVIDER not in management.roles
            or management.status is not ConnectionStatus.READY
            or management.archived_at is not None
        ):
            raise ResourceConflictError(
                "credential resolution requires a ready provider connection"
            )
        if management.interface is ConnectionInterface.BROWSER and (
            management.playbook_id is None or management.playbook_version_id is None
        ):
            raise ResourceConflictError(
                "browser credential resolution requires a connection playbook"
            )
        if management.interface not in {ConnectionInterface.API, ConnectionInterface.BROWSER}:
            raise ResourceConflictError(
                "credential resolution requires a supported provider connection"
            )
        if not _resource_covered(secret_reference, secret_store.allowed_resources):
            raise ResourceConflictError("credential secret reference escapes the secret store")
        if "/versions/" not in secret_reference:
            raise ResourceConflictError(
                "credential resolution requires one immutable secret version"
            )
        return await self._resolve_credential(
            management,
            secret_store,
            secret_reference,
        )

    async def _resolve_credential(
        self,
        management: Connection,
        secret_store: Connection,
        secret_reference: str,
    ) -> ProviderCredentialMetadata:
        if management.interface is ConnectionInterface.BROWSER:
            if management.playbook_id is None or management.playbook_version_id is None:
                raise ResourceConflictError(
                    "browser credential resolution requires a connection playbook"
                )
            if self.catalog.secret_metadata is None:
                raise ResourceConflictError("browser credential verification is unavailable")
            secret_resource = secret_reference.partition("/versions/")[0]
            versions = await self.catalog.secret_metadata.versions_for(
                secret_store, secret_resource
            )
            selected = tuple(
                item
                for item in versions
                if item.get("name") == secret_reference and item.get("state") == "ENABLED"
            )
            if len(selected) != 1:
                raise ResourceConflictError("selected credential secret version is not enabled")
            return ProviderCredentialMetadata(
                provider_id=f"browser-secret-{hashlib.sha256(secret_reference.encode()).hexdigest()[:48]}",
                kind="api-key",
                scopes=(),
                status="unknown",
                disabled=False,
            )
        if self.catalog.provider_metadata is None or self.catalog.credential_verifier is None:
            raise ResourceConflictError("credential import verification is unavailable")
        discovered = tuple(
            ProviderCredentialMetadata.model_validate(item)
            for item in await self.catalog.provider_metadata.metadata(management)
        )
        identity = await self.catalog.credential_verifier.credential_identity(
            management,
            secret_store,
            secret_reference,
        )
        matches = tuple(item for item in discovered if item.provider_id == identity)
        if len(matches) != 1 or matches[0].disabled is True:
            raise ResourceConflictError(
                "This secret is not managed by the selected provider connection"
            )
        if matches[0].kind is None:
            raise ResourceConflictError("provider credential type is unavailable")
        return matches[0]
