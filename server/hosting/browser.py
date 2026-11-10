from datetime import UTC, datetime

from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ControlVersion,
    ManagedCredential,
)
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.ids import new_id
from core.inventory.controls import compile_controls
from executor.provider import Providers
from policy import digest
from rotation.stages import stable

from hosting.mapping import Mappings


class Browsers:
    def __init__(self, mappings: Mappings, provider: Providers) -> None:
        self.mappings, self.inventory, self.provider = mappings, mappings.hosts.inventory, provider

    async def prepare(self, org: str, source_id: str, actor: str) -> Connection:
        source = await self.inventory.get_connection(org, source_id)
        if source.archived_at or source.status is not ConnectionStatus.READY:
            raise ResourceConflictError("Reconnect the Resend account")
        now = datetime.now(UTC)
        identity = stable("connection", "browser:" + source_id)
        try:
            existing = await self.inventory.get_connection(org, identity)
        except ResourceNotFoundError:
            existing = None
        if existing:
            if existing.archived_at or existing.verification_connection_id != source_id:
                raise ResourceConflictError("The provider browser connection is unavailable")
            return existing
        return await self.inventory.add_connection(
            Connection(
                id=identity,
                organisation_id=org,
                platform="resend",
                display_name="Resend browser",
                roles=frozenset({ConnectionRole.PROVIDER}),
                interface=ConnectionInterface.BROWSER,
                authorization=ConnectionAuthorization.BROWSER_SESSION,
                capabilities=self.provider.tools
                | {"browser.execute", "browser.authenticate", "browser.secureCapture"},
                allowed_resources=("resend.com",),
                verification_connection_id=source.id,
                status=ConnectionStatus.SETUP_REQUIRED,
                region="global",
                created_at=now,
                updated_at=now,
            )
        )

    async def select(
        self, org: str, credential_id: str, browser_id: str, revision: int, actor: str
    ) -> ManagedCredential:
        current = await self.inventory.get_credential(org, credential_id)
        target = await self.inventory.get_connection(org, browser_id)
        if (
            current.revision != revision
            or current.archived_at
            or target.verification_connection_id != current.connection_id
            or target.interface is not ConnectionInterface.BROWSER
            or target.status is not ConnectionStatus.READY
            or target.archived_at
        ):
            raise ResourceConflictError("Choose a connected browser for this provider account")
        source = await self.provider.resend_browser.validate(target)
        store = await self.inventory.get_connection(org, current.secret_store_connection_id)
        if store.platform != "kinu" or not current.active_generation_id:
            raise ResourceConflictError(
                "Browser rotation requires a managed key in the workspace vault"
            )
        with await self.mappings.versions.access_for(store, current.secret_reference) as value:
            if await self.provider.identify(target, value) != current.provider_id:
                raise ResourceConflictError("Browser credential identity changed")
        previous = await self.inventory.get_control_version(
            org, current.id, current.control_version
        )
        preferences = self.mappings.preferences(previous)
        now, controls_id = datetime.now(UTC), new_id("controls")
        changed = current.model_copy(
            update={
                "connection_id": target.id,
                "control_version": controls_id,
                "revision": revision + 1,
                "updated_at": now,
            }
        )
        bindings = tuple(
            item for item in await self.inventory.bindings(org) if item.credential_id == current.id
        )
        definition, probes = compile_controls(
            changed,
            bindings,
            await self.inventory.services(org),
            await self.inventory.connections(org),
            preferences,
            actor,
            now,
        )
        controls = ControlVersion(
            id=controls_id,
            organisation_id=org,
            credential_id=current.id,
            number=previous.number + 1,
            definition=definition,
            digest=digest(definition),
            created_by=actor,
            created_at=now,
        )
        return await self.inventory.replace_provider(
            changed, revision, controls, probes, source, target
        )
