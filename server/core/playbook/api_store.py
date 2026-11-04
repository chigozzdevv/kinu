from datetime import datetime

from contracts import PlaybookState
from contracts.api_playbook import ApiPlaybook, ApiPlaybookDraft, ApiPlaybookVersion
from policy import digest

from core.errors import PlaybookError, ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records


class ApiPlaybookStore:
    def __init__(self, database: Database) -> None:
        self._records = Records(database)

    async def get(self, organisation_id: str, playbook_id: str) -> ApiPlaybook:
        async with self._records.read() as rows:
            return await rows.get(ApiPlaybook, organisation_id, playbook_id)

    async def replace(self, value: ApiPlaybook, expected_revision: int) -> ApiPlaybook:
        async with self._records.write(value.organisation_id) as rows:
            return await rows.replace(value, expected_revision)

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[ApiPlaybook, ...]:
        async with self._records.read() as rows:
            values = await rows.list(ApiPlaybook, organisation_id)
        values = tuple(value for value in values if value.archived_at is None)
        return tuple(
            sorted(values, key=lambda value: (value.created_at, value.id), reverse=True)[:limit]
        )

    async def get_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> ApiPlaybookVersion:
        async with self._records.read() as rows:
            version = await rows.get(ApiPlaybookVersion, organisation_id, version_id)
        if version.playbook_id != playbook_id:
            raise PlaybookError("version belongs to another playbook")
        return version

    async def add_version(
        self,
        playbook_id: str,
        version_id: str,
        organisation_id: str,
        definition: ApiPlaybookDraft,
        definition_digest: str,
        actor_id: str,
        created_at: datetime,
        source_ids: tuple[str, ...],
    ) -> tuple[ApiPlaybook, ApiPlaybookVersion]:
        if definition_digest != digest(definition):
            raise PlaybookError("The API playbook definition changed")
        async with self._records.write(organisation_id) as rows:
            existing = await rows.match(ApiPlaybook, organisation_id, {"id": playbook_id})
            current = existing[0] if existing else None
            if current and (
                current.archived_at
                or current.platform != definition.platform
                or current.name != definition.name
            ):
                raise PlaybookError("Use the active playbook name and platform")
            saved = await rows.match(ApiPlaybookVersion, organisation_id, {"id": version_id})
            if saved:
                version = saved[0]
                if (
                    not current
                    or version.playbook_id != playbook_id
                    or version.definition != definition
                    or version.digest != definition_digest
                    or version.source_ids != source_ids
                ):
                    raise ResourceConflictError(
                        "playbook version already contains another definition"
                    )
                return current, version
            version = ApiPlaybookVersion(
                id=version_id,
                organisation_id=organisation_id,
                playbook_id=playbook_id,
                number=current.latest_version + 1 if current else 1,
                definition=definition,
                digest=definition_digest,
                state=PlaybookState.DRAFT,
                source_ids=source_ids,
                created_by=actor_id,
                created_at=created_at,
            )
            await rows.add(version, organisation_id)
            if current:
                changed = current.model_copy(
                    update={
                        "latest_version": version.number,
                        "latest_version_id": version.id,
                        "updated_at": created_at,
                        "revision": current.revision + 1,
                    }
                )
                playbook = await rows.replace(changed, current.revision)
            else:
                playbook = await rows.add(
                    ApiPlaybook(
                        id=playbook_id,
                        organisation_id=organisation_id,
                        name=definition.name,
                        platform=definition.platform,
                        latest_version=1,
                        latest_version_id=version.id,
                        created_at=created_at,
                        updated_at=created_at,
                    ),
                    organisation_id,
                )
        return playbook, version

    async def publish(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        actor_id: str,
        published_at: datetime,
    ) -> ApiPlaybookVersion:
        async with self._records.write(organisation_id) as rows:
            playbook = await rows.get(ApiPlaybook, organisation_id, playbook_id)
            version = await rows.get(ApiPlaybookVersion, organisation_id, version_id)
            if playbook.archived_at or version.playbook_id != playbook_id:
                raise PlaybookError("publication requires an active playbook and its own version")
            if (
                playbook.active_version_id == version.id
                and version.state is PlaybookState.PUBLISHED
            ):
                return version
            if version.state is not PlaybookState.DRAFT:
                raise PlaybookError("only draft versions can be published")
            if playbook.active_version_id:
                previous = await rows.get(
                    ApiPlaybookVersion, organisation_id, playbook.active_version_id
                )
                await rows.publish(
                    previous, previous.model_copy(update={"state": PlaybookState.SUPERSEDED})
                )
            published = version.model_copy(
                update={
                    "state": PlaybookState.PUBLISHED,
                    "published_by": actor_id,
                    "published_at": published_at,
                }
            )
            await rows.publish(version, published)
            await rows.replace(
                playbook.model_copy(
                    update={
                        "active_version_id": version.id,
                        "updated_at": published_at,
                        "revision": playbook.revision + 1,
                    }
                ),
                playbook.revision,
            )
            return published

    async def published(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> ApiPlaybookVersion:
        playbook = await self.get(organisation_id, playbook_id)
        version = await self.get_version(organisation_id, playbook_id, version_id)
        if (
            playbook.archived_at
            or playbook.active_version_id != version.id
            or version.state is not PlaybookState.PUBLISHED
            or version.digest != digest(version.definition)
        ):
            raise PlaybookError("Choose a published API playbook")
        return version
