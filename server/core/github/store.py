from datetime import UTC, datetime
from typing import Any

from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    GitHubInstallation,
    GitHubOnboardingSession,
    GitHubOnboardingStatus,
    GitHubRepository,
    GitHubRepositoryCandidate,
    GitHubWebhookReceipt,
)
from pymongo.errors import DuplicateKeyError

from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database
from core.storage.records import Records


class GitHubStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_session(self, session: GitHubOnboardingSession) -> GitHubOnboardingSession:
        await self.database.collection("github_onboarding").insert_one(
            {
                "_id": session.id,
                "organisation_id": session.organisation_id,
                "data": session.model_dump(mode="json"),
                "expires_on": session.expires_at,
            }
        )
        return session

    async def get_session(self, organisation_id: str, session_id: str) -> GitHubOnboardingSession:
        row = await self.database.collection("github_onboarding").find_one(
            {
                "_id": session_id,
                "organisation_id": organisation_id,
            }
        )
        if not row:
            raise ResourceNotFoundError("GitHub connection session is unavailable")
        return GitHubOnboardingSession.model_validate(row["data"])

    async def receipt(self, installation_id: int) -> GitHubWebhookReceipt | None:
        row = await self.database.collection("github_receipts").find_one({"_id": installation_id})
        return GitHubWebhookReceipt.model_validate(row["data"]) if row else None

    async def stage(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepositoryCandidate, ...],
    ) -> GitHubOnboardingSession:
        changed = session.model_copy(
            update={
                "status": GitHubOnboardingStatus.DISCOVERED,
                "installation_id": installation.installation_id,
                "installation": installation,
                "repositories": repositories,
            }
        )
        result = await self.database.collection("github_onboarding").update_one(
            {
                "_id": session.id,
                "organisation_id": session.organisation_id,
                "data.status": GitHubOnboardingStatus.PENDING,
            },
            {"$set": {"data": changed.model_dump(mode="json")}},
        )
        if not result.matched_count:
            current = await self.get_session(session.organisation_id, session.id)
            if current.installation_id != installation.installation_id:
                raise ResourceConflictError("GitHub connection session changed")
            return current
        return changed

    async def complete(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepository, ...],
    ) -> GitHubOnboardingSession:
        now, org = datetime.now(UTC), session.organisation_id
        changed = session.model_copy(
            update={"status": GitHubOnboardingStatus.COMPLETE, "completed_at": now}
        )
        try:
            async with Records(self.database).write(org) as rows:
                previous = await self.database.collection("github_installations").find_one(
                    {"_id": installation.installation_id}, session=rows.session
                )
                if previous and previous["organisation_id"] != org:
                    raise ResourceConflictError(
                        "This GitHub installation belongs to another workspace"
                    )
                await self.database.collection("github_installations").update_one(
                    {"_id": installation.installation_id},
                    {
                        "$set": {
                            "organisation_id": org,
                            "data": installation.model_dump(mode="json"),
                            "repositories": [item.model_dump(mode="json") for item in repositories],
                        }
                    },
                    upsert=True,
                    session=rows.session,
                )
                identity = f"conn_github_{installation.installation_id}"
                connections = await rows.match(Connection, org, {"id": identity})
                connection = Connection(
                    id=identity,
                    organisation_id=org,
                    platform="github",
                    display_name=f"GitHub · {installation.account_login}",
                    roles=frozenset({ConnectionRole.INCIDENT}),
                    interface=ConnectionInterface.API,
                    authorization=ConnectionAuthorization.OAUTH,
                    authorization_reference=f"oauth://github/installation/{installation.installation_id}",
                    capabilities=frozenset(
                        {
                            "incident.verifyWebhook",
                            "incident.readFinding",
                            "repository.resolveContext",
                        }
                    ),
                    allowed_resources=tuple(item.full_name for item in repositories),
                    status=ConnectionStatus.READY
                    if installation.ready
                    else ConnectionStatus.SETUP_REQUIRED,
                    authenticated_at=now,
                    last_validated_at=now if installation.ready else None,
                    region="global",
                    created_at=connections[0].created_at if connections else now,
                    updated_at=now,
                    revision=connections[0].revision + 1 if connections else 0,
                )
                if connections:
                    await rows.replace(connection, connections[0].revision)
                else:
                    await rows.add(connection, org)
                await self.database.collection("github_onboarding").update_one(
                    {
                        "_id": session.id,
                        "organisation_id": org,
                        "data.status": GitHubOnboardingStatus.DISCOVERED,
                    },
                    {"$set": {"data": changed.model_dump(mode="json")}},
                    session=rows.session,
                )
        except DuplicateKeyError:
            raise ResourceConflictError("This GitHub installation is already connected") from None
        return changed

    async def installation(self, organisation_id: str, installation_id: int) -> GitHubInstallation:
        row = await self.database.collection("github_installations").find_one(
            {"_id": installation_id, "organisation_id": organisation_id}
        )
        if not row:
            raise ResourceNotFoundError("GitHub installation is unavailable")
        return GitHubInstallation.model_validate(row["data"])

    async def repositories(
        self, organisation_id: str, installation_id: int
    ) -> tuple[GitHubRepository, ...]:
        row = await self.database.collection("github_installations").find_one(
            {"_id": installation_id, "organisation_id": organisation_id}
        )
        return (
            tuple(GitHubRepository.model_validate(item) for item in row["repositories"])
            if row
            else ()
        )

    async def received(self, receipt: GitHubWebhookReceipt) -> None:
        await self.database.collection("github_receipts").update_one(
            {"_id": receipt.installation_id},
            {"$set": {"data": receipt.model_dump(mode="json")}},
            upsert=True,
        )

    async def find(self, installation_id: int) -> dict[str, Any] | None:
        return await self.database.collection("github_installations").find_one(
            {"_id": installation_id}
        )
