from dataclasses import dataclass
from urllib.parse import urlsplit

from connectors.hosting.models import Variable
from connectors.http import HttpProviderConnector
from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionStatus,
    ManagedCredential,
    ProviderCredentialMetadata,
)
from core.errors import ResourceConflictError
from core.ids import new_id
from executor.versions import Versions, root

from hosting.models import Import, Mapping
from hosting.proof import fingerprint
from hosting.service import Service


@dataclass(frozen=True)
class Prepared:
    choice: Mapping
    variable: Variable
    provider: Connection
    metadata: ProviderCredentialMetadata
    existing: ManagedCredential | None
    credential_id: str
    reference: str
    fingerprint: str | None = None


async def prepare(
    hosts: Service,
    versions: Versions,
    provider_api: HttpProviderConnector,
    org: str,
    connection: Connection,
    body: Import,
    store: Connection,
) -> tuple[Prepared, ...]:
    variables = {
        item.reference: item for item in await hosts.variables(org, connection.id, body.project_id)
    }
    grant, _ = await hosts.account(org, connection.id)
    adapter = hosts.adapter(grant)
    credentials = await hosts.inventory.credentials(org)
    result, seen = [], set()
    for choice in body.mappings:
        if choice.variable_reference in seen or choice.variable_reference not in variables:
            raise ResourceConflictError("Choose each available variable once")
        seen.add(choice.variable_reference)
        variable = variables[choice.variable_reference]
        provider = await hosts.inventory.get_connection(org, choice.provider_connection_id)
        if (
            provider.archived_at
            or provider.status is not ConnectionStatus.READY
            or (provider.http is None and provider.interface is not ConnectionInterface.BROWSER)
        ):
            raise ResourceConflictError("Choose a connected API credential provider")
        value = await adapter.read(variable)
        secret_fingerprint = fingerprint(value.bytes()) if value is not None else None
        automatic = choice.provider_id is None and provider.interface is ConnectionInterface.BROWSER
        if automatic:
            if value is None:
                raise ResourceConflictError(
                    "Browser providers require a readable workload variable"
                )
            provider_id = (secret_fingerprint or "")[:32]
            metadata = [
                ProviderCredentialMetadata(
                    provider_id=provider_id,
                    name=variable.name,
                    kind="api-key",
                    status="unverified",
                )
            ]
        else:
            if not choice.provider_id:
                raise ResourceConflictError("Choose an active provider credential")
            provider_id = choice.provider_id
            metadata = [
                ProviderCredentialMetadata.model_validate(item)
                for item in await provider_api.metadata(provider)
                if item["provider_id"] == provider_id
            ]
            if (
                len(metadata) != 1
                or metadata[0].status not in {"active", "enabled", "unverified"}
                or metadata[0].disabled
                or not metadata[0].kind
            ):
                raise ResourceConflictError("Choose an active provider credential")
        if choice.health_url:
            endpoint = urlsplit(choice.health_url)
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.fragment
            ):
                raise ResourceConflictError("Use an HTTPS health URL without credentials")
        existing = [
            item
            for item in credentials
            if not item.archived_at
            and item.connection_id == provider.id
            and item.provider_id == provider_id
        ]
        if len(existing) > 1:
            raise ResourceConflictError("Provider key is represented by multiple credentials")
        current = existing[0] if existing else None
        key = current.id if current else new_id("credential")
        if current and (
            current.secret_store_connection_id != store.id or not current.active_generation_id
        ):
            raise ResourceConflictError("Link this key through its existing credential")
        try:
            if provider.interface is ConnectionInterface.BROWSER and value is None:
                raise ResourceConflictError(
                    "Browser providers require a readable existing key for verification"
                )
            if value and fingerprint(value.bytes()) == await provider_api.auth_fingerprint(
                provider
            ):
                raise ResourceConflictError(
                    "Use a separate management credential for the provider connection"
                )
            if (
                not automatic
                and value
                and await provider_api.identify(provider, value) != provider_id
            ):
                raise ResourceConflictError(
                    "Selected variable belongs to another provider credential"
                )
            if provider.interface is ConnectionInterface.BROWSER and not automatic:
                metadata = [
                    ProviderCredentialMetadata.model_validate(item)
                    for item in await provider_api.metadata(provider)
                    if item["provider_id"] == choice.provider_id
                ]
                if len(metadata) != 1:
                    raise ResourceConflictError(
                        "Browser credential disappeared during verification"
                    )
            if current:
                reference = current.secret_reference
                if await versions.current(store, current.secret_resource) != reference:
                    raise ResourceConflictError("Credential generation changed")
            else:
                reference = await versions.record(
                    store, root(org) + "/" + key, "initial", value, current=True
                )
        finally:
            if value:
                value.clear()
        result.append(
            Prepared(
                choice,
                variable,
                provider,
                metadata[0],
                current,
                key,
                reference,
                secret_fingerprint,
            )
        )
    return tuple(result)
