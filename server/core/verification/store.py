from collections.abc import Callable
from datetime import datetime

from contracts import Probe, ProbeState, ProbeVersion
from policy import digest

from core.errors import ResourceConflictError
from core.inventory.relations import check_references
from core.storage.database import Database
from core.storage.records import Records


class ProbeStore:
    def __init__(self, database: Database) -> None:
        self._records = Records(database)

    async def create(self, probe: Probe) -> Probe:
        async with self._records.write(probe.organisation_id) as rows:
            return await rows.add(probe, probe.organisation_id)

    async def create_version(
        self, organisation_id: str, probe_id: str, factory: Callable[[int], ProbeVersion]
    ) -> ProbeVersion:
        async with self._records.write(organisation_id) as rows:
            probe = await rows.get(Probe, organisation_id, probe_id)
            version = factory(probe.latest_version + 1)
            if version.probe_id != probe.id or version.organisation_id != organisation_id:
                raise ResourceConflictError("probe version belongs to another probe")
            await check_references(rows, version)
            await rows.add(version, organisation_id)
            await rows.replace(
                probe.model_copy(
                    update={
                        "latest_version": version.number,
                        "updated_at": version.created_at,
                        "revision": probe.revision + 1,
                    }
                ),
                probe.revision,
            )
            return version

    async def activate(
        self, organisation_id: str, probe_id: str, version_id: str, actor_id: str, now: datetime
    ) -> ProbeVersion:
        async with self._records.write(organisation_id) as rows:
            probe = await rows.get(Probe, organisation_id, probe_id)
            version = await rows.get(ProbeVersion, organisation_id, version_id)
            if version.probe_id != probe_id or version.digest != digest(version.definition):
                raise ResourceConflictError("probe definition does not match its version")
            await check_references(rows, version)
            if probe.active_version_id == version.id and version.state is ProbeState.ACTIVE:
                return version
            if version.state is not ProbeState.DRAFT:
                raise ResourceConflictError("only draft probes can be activated")
            if probe.active_version_id:
                previous = await rows.get(ProbeVersion, organisation_id, probe.active_version_id)
                await rows.publish(
                    previous, previous.model_copy(update={"state": ProbeState.SUPERSEDED})
                )
            active = version.model_copy(
                update={"state": ProbeState.ACTIVE, "approved_by": actor_id, "approved_at": now}
            )
            await rows.publish(version, active)
            await rows.replace(
                probe.model_copy(
                    update={
                        "active_version_id": version.id,
                        "updated_at": now,
                        "revision": probe.revision + 1,
                    }
                ),
                probe.revision,
            )
            return active
