from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from browser.auth import domain_covered, is_domain_pattern
from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionRole,
    Playbook,
    PlaybookDraft,
    PlaybookEffect,
    PlaybookState,
    PlaybookVersion,
    Stage,
)
from policy import digest

from core.errors import PlaybookError

_LIST_SCAN_LIMIT = 500


class ConnectionCatalog(Protocol):
    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...

    async def attach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
        updated_at: datetime,
    ) -> Connection: ...

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]: ...

    async def detach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        updated_at: datetime,
    ) -> Connection: ...


class PlaybookRepository(Protocol):
    async def add_version(
        self,
        playbook_id: str,
        version_id: str,
        organisation_id: str,
        definition: PlaybookDraft,
        definition_digest: str,
        actor_id: str,
        created_at: datetime,
        source_ids: tuple[str, ...],
    ) -> tuple[Playbook, PlaybookVersion]: ...

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]: ...

    async def get(self, organisation_id: str, playbook_id: str) -> Playbook: ...

    async def replace(self, value: Playbook, expected_revision: int) -> Playbook: ...

    async def get_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion: ...

    async def publish(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        actor_id: str,
        published_at: datetime,
    ) -> PlaybookVersion: ...


class PlaybookService:
    def __init__(
        self,
        repository: PlaybookRepository,
        clock: Callable[[], datetime],
        inventory: ConnectionCatalog | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._inventory = inventory

    async def list_playbooks(self, organisation_id: str, limit: int = 100) -> tuple[Playbook, ...]:
        playbooks = await self._repository.list_playbooks(organisation_id, _LIST_SCAN_LIMIT)
        playbooks = tuple(playbook for playbook in playbooks if playbook.archived_at is None)
        ordered = sorted(
            playbooks, key=lambda playbook: (playbook.created_at, playbook.id), reverse=True
        )
        return tuple(ordered[:limit])

    async def get(self, organisation_id: str, playbook_id: str) -> Playbook:
        return await self._repository.get(organisation_id, playbook_id)

    async def get_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        return await self._repository.get_version(organisation_id, playbook_id, version_id)

    async def rename(
        self,
        organisation_id: str,
        playbook_id: str,
        expected_revision: int,
        name: str,
    ) -> Playbook:
        current = await self.get(organisation_id, playbook_id)
        _editable(current, expected_revision)
        changed = current.model_copy(
            update={
                "name": name,
                "updated_at": self._clock(),
                "revision": expected_revision + 1,
            }
        )
        return await self._repository.replace(changed, expected_revision)

    async def archive(
        self,
        organisation_id: str,
        playbook_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> Playbook:
        current = await self.get(organisation_id, playbook_id)
        _editable(current, expected_revision)
        connections = (
            await self._inventory.connections(organisation_id)
            if self._inventory is not None
            else ()
        )
        attached = tuple(
            connection
            for connection in connections
            if connection.archived_at is None and connection.playbook_id == playbook_id
        )
        if attached and not cascade:
            raise PlaybookError("playbook is still attached to an active connection")
        now = self._clock()
        if self._inventory is not None:
            for connection in attached:
                await self._inventory.detach_playbook(
                    organisation_id, connection.id, connection.revision, now
                )
        changed = current.model_copy(
            update={
                "archived_at": now,
                "updated_at": now,
                "revision": expected_revision + 1,
            }
        )
        return await self._repository.replace(changed, expected_revision)

    async def create_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        definition: PlaybookDraft,
        actor_id: str,
        source_ids: tuple[str, ...] = (),
    ) -> tuple[Playbook, PlaybookVersion]:
        validate_definition(definition)
        return await self._repository.add_version(
            playbook_id,
            version_id,
            organisation_id,
            definition,
            digest(definition),
            actor_id,
            self._clock(),
            source_ids,
        )

    async def publish(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        actor_id: str,
    ) -> PlaybookVersion:
        version = await self._repository.get_version(organisation_id, playbook_id, version_id)
        validate_definition(version.definition)
        if digest(version.definition) != version.digest:
            raise PlaybookError("playbook definition digest does not match its immutable version")
        return await self._repository.publish(
            organisation_id,
            playbook_id,
            version_id,
            actor_id,
            self._clock(),
        )

    async def attach(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
    ) -> Connection:
        if self._inventory is None:
            raise RuntimeError("playbook connection catalog is not configured")
        version = await self._repository.get_version(organisation_id, playbook_id, version_id)
        connection = await self._inventory.get_connection(organisation_id, connection_id)
        if version.state is not PlaybookState.PUBLISHED:
            raise PlaybookError("browser connections require a published playbook version")
        if connection.interface is not ConnectionInterface.BROWSER or connection.roles != frozenset(
            {ConnectionRole.PROVIDER}
        ):
            raise PlaybookError("playbooks can only attach to browser provider connections")
        if connection.platform != version.definition.platform:
            raise PlaybookError("connection platform does not match the playbook")
        if any(
            not domain_covered(domain, connection.allowed_resources)
            for domain in version.definition.allowed_domains
        ):
            raise PlaybookError("playbook domains are not covered by the browser connection")
        return await self._inventory.attach_playbook(
            organisation_id,
            connection_id,
            expected_revision,
            playbook_id,
            version_id,
            self._clock(),
        )


def validate_definition(definition: PlaybookDraft) -> None:
    allowed_stages = {Stage.CREATE, Stage.REVOKE}
    invalid_stages = {step.stage for step in definition.steps}.difference(allowed_stages)
    if invalid_stages:
        names = ", ".join(sorted(stage.value for stage in invalid_stages))
        raise PlaybookError(f"browser playbook contains non-browser lifecycle stages: {names}")
    if any(not step.tool.startswith("browser.") for step in definition.steps):
        raise PlaybookError("playbooks can contain browser tools only")
    if len(set(definition.allowed_domains)) != len(definition.allowed_domains) or any(
        not is_domain_pattern(domain) for domain in definition.allowed_domains
    ):
        raise PlaybookError("browser playbook domains must be unique valid domain patterns")
    patterns = (
        definition.login_url_pattern,
        *(step.checkpoint.url_pattern for step in definition.steps if step.checkpoint is not None),
        *(
            url
            for step in definition.steps
            if step.tool == "browser.navigate"
            and isinstance(url := step.parameters.get("url"), str)
        ),
    )
    for pattern in patterns:
        parsed = urlparse(pattern)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or not domain_covered(parsed.hostname, definition.allowed_domains)
        ):
            raise PlaybookError("browser checkpoint escapes the playbook domains")
    creation = tuple(
        index
        for index, step in enumerate(definition.steps)
        if step.effect == PlaybookEffect.CREATE_CREDENTIAL
    )
    if len(creation) != 1:
        raise PlaybookError("browser playbook requires one secure credential creation")
    if any(step.stage == Stage.CREATE for step in definition.steps[creation[0] + 1 :]):
        raise PlaybookError("secure credential creation must finish the create stage")
    revocation = tuple(
        step for step in definition.steps if step.effect == PlaybookEffect.REVOKE_CREDENTIAL
    )
    if len(revocation) != 1:
        raise PlaybookError("browser playbook requires one credential revocation")


def _editable(playbook: Playbook, expected_revision: int) -> None:
    if playbook.archived_at is not None:
        raise PlaybookError("archived playbooks cannot be changed")
    if playbook.revision != expected_revision:
        raise PlaybookError(
            f"playbook expected revision {expected_revision}, found {playbook.revision}"
        )
