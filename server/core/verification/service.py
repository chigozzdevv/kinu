from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import Probe, ProbeDefinition, ProbeState, ProbeVersion
from policy import digest


class ProbeRepository(Protocol):
    async def create(self, probe: Probe) -> Probe: ...

    async def create_version(
        self,
        organisation_id: str,
        probe_id: str,
        factory: Callable[[int], ProbeVersion],
    ) -> ProbeVersion: ...

    async def activate(
        self,
        organisation_id: str,
        probe_id: str,
        version_id: str,
        actor_id: str,
        now: datetime,
    ) -> ProbeVersion: ...


class ProbeService:
    def __init__(self, repository: ProbeRepository, clock: Callable[[], datetime]) -> None:
        self._repository = repository
        self._clock = clock

    async def create(self, organisation_id: str, probe_id: str, name: str) -> Probe:
        now = self._clock()
        return await self._repository.create(
            Probe(
                id=probe_id,
                organisation_id=organisation_id,
                name=name,
                created_at=now,
                updated_at=now,
            )
        )

    async def create_version(
        self,
        organisation_id: str,
        probe_id: str,
        definition: ProbeDefinition,
        actor_id: str,
    ) -> ProbeVersion:
        if definition.organisation_id != organisation_id:
            raise ValueError("probe definition crosses organisation boundaries")
        now = self._clock()

        def factory(number: int) -> ProbeVersion:
            return ProbeVersion(
                id=definition.id,
                organisation_id=organisation_id,
                probe_id=probe_id,
                number=number,
                definition=definition,
                digest=digest(definition),
                state=ProbeState.DRAFT,
                created_by=actor_id,
                created_at=now,
            )

        return await self._repository.create_version(organisation_id, probe_id, factory)

    async def activate(
        self,
        organisation_id: str,
        probe_id: str,
        version_id: str,
        actor_id: str,
    ) -> ProbeVersion:
        return await self._repository.activate(
            organisation_id, probe_id, version_id, actor_id, self._clock()
        )
