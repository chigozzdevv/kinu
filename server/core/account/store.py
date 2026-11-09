from collections.abc import Callable
from datetime import datetime

from contracts import (
    MemberRole,
    NotificationEndpoint,
    Organisation,
    OrganisationMembership,
    TeamInvitation,
)
from pymongo.asynchronous.client_session import AsyncClientSession

from core.account.service import principal_id
from core.auth import AuthenticatedIdentity, PrincipalGrant, Role
from core.auth.identity import actor_for
from core.errors import ResourceConflictError, ResourceNotFoundError, RevisionConflictError
from core.storage.database import Database
from core.storage.records import kind_of


class AccountStore:
    def __init__(self, database: Database, clock: Callable[[], datetime]) -> None:
        self._database, self._clock = database, clock

    async def session(self, identity: AuthenticatedIdentity) -> tuple[OrganisationMembership, ...]:
        if identity.email and identity.email_verified:
            await self._accept(identity)
        memberships = []
        async for row in (
            self._database.collection("members")
            .find({"subject": identity.subject, "data.enabled": True})
            .sort("organisation_id")
        ):
            grant = PrincipalGrant.model_validate(row["data"])
            organisation = await self._database.collection("organisations").find_one(
                {"id": row["organisation_id"]}
            )
            role = next((role for role in MemberRole if Role(role.value) in grant.roles), None)
            if role is not None and organisation:
                memberships.append(
                    OrganisationMembership(
                        organisation=Organisation.model_validate(organisation["data"]),
                        role=role,
                    )
                )
        return tuple(memberships)

    async def create_organisation(
        self, organisation: Organisation, identity: AuthenticatedIdentity, created_at: datetime
    ) -> OrganisationMembership:
        async with self._database.transaction() as session:
            await self._database.collection("organisations").insert_one(
                {
                    "id": organisation.id,
                    "data": organisation.model_dump(mode="json"),
                    "write_version": 0,
                },
                session=session,
            )
            await self._database.collection("members").insert_one(
                {
                    "organisation_id": organisation.id,
                    "subject": identity.subject,
                    "id": principal_id(identity.subject),
                    "data": _grant(identity, Role.ADMINISTRATOR, created_at).model_dump(
                        mode="json"
                    ),
                },
                session=session,
            )
        return OrganisationMembership(organisation=organisation, role=MemberRole.ADMINISTRATOR)

    async def get(
        self, organisation_id: str, identity: AuthenticatedIdentity
    ) -> PrincipalGrant | None:
        row = await self._database.collection("members").find_one(
            {"organisation_id": organisation_id, "subject": identity.subject}
        )
        return PrincipalGrant.model_validate(row["data"]) if row else None

    async def sync_identity(
        self, organisation_id: str, identity: AuthenticatedIdentity, updated_at: datetime
    ) -> PrincipalGrant:
        current = await self.get(organisation_id, identity)
        if current is None or not current.enabled:
            raise ResourceNotFoundError("workspace member is unavailable")
        changed = current.model_copy(
            update={
                "email": identity.email,
                "connected_via": identity.connected_via,
                "display_name": current.display_name or identity.display_name,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        return await self._replace(organisation_id, current.revision, changed)

    async def update_profile(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        expected_revision: int,
        display_name: str,
        updated_at: datetime,
    ) -> PrincipalGrant:
        current = await self.get(organisation_id, identity)
        if current is None:
            raise ResourceNotFoundError("workspace member is unavailable")
        return await self._replace(
            organisation_id,
            expected_revision,
            current.model_copy(
                update={
                    "display_name": display_name,
                    "updated_at": updated_at,
                    "revision": expected_revision + 1,
                }
            ),
        )

    async def list_principals(self, organisation_id: str) -> tuple[PrincipalGrant, ...]:
        return tuple(
            [
                PrincipalGrant.model_validate(row["data"])
                async for row in self._database.collection("members")
                .find({"organisation_id": organisation_id})
                .sort("id")
            ]
        )

    async def list_invitations(self, organisation_id: str) -> tuple[TeamInvitation, ...]:
        return tuple(
            [
                TeamInvitation.model_validate(row["data"])
                async for row in self._database.collection("invitations")
                .find({"organisation_id": organisation_id})
                .sort("id")
            ]
        )

    async def save_invitation(self, invitation: TeamInvitation) -> TeamInvitation:
        query = {"organisation_id": invitation.organisation_id, "id": invitation.id}
        async with self._database.transaction(invitation.organisation_id) as session:
            previous = await self._database.collection("invitations").find_one(
                query, session=session
            )
            changed = invitation.model_copy(
                update={
                    "revision": previous["data"]["revision"] + 1 if previous else 0,
                }
            )
            await self._database.collection("invitations").replace_one(
                query,
                {**query, "email": changed.email, "data": changed.model_dump(mode="json")},
                upsert=True,
                session=session,
            )
        return changed

    async def update_member(
        self,
        organisation_id: str,
        member_id: str,
        expected_revision: int,
        role: Role,
        enabled: bool,
        updated_at: datetime,
    ) -> PrincipalGrant:
        query = {"organisation_id": organisation_id, "id": member_id}
        async with self._database.transaction(organisation_id) as session:
            row = await self._database.collection("members").find_one(query, session=session)
            if row is None:
                raise ResourceNotFoundError("workspace member is unavailable")
            current = PrincipalGrant.model_validate(row["data"])
            if current.revision != expected_revision:
                raise RevisionConflictError("workspace member revision changed")
            if current.enabled and Role.ADMINISTRATOR in current.roles:
                count = await self._database.collection("members").count_documents(
                    {
                        "organisation_id": organisation_id,
                        "data.enabled": True,
                        "data.roles": "administrator",
                    },
                    session=session,
                )
                if count <= 1 and (not enabled or role is not Role.ADMINISTRATOR):
                    raise ResourceConflictError("workspace must retain an enabled administrator")
            changed = current.model_copy(
                update={
                    "roles": frozenset({role}),
                    "enabled": enabled,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            await self._database.collection("members").update_one(
                query,
                {"$set": {"data": changed.model_dump(mode="json")}},
                session=session,
            )
            if current.enabled and not enabled:
                await self._database.collection("inventory").delete_many(
                    {
                        "organisation_id": organisation_id,
                        "kind": kind_of(NotificationEndpoint),
                        "data.principal_id": actor_for(current.subject),
                    },
                    session=session,
                )
        return changed

    async def revoke_invitation(
        self, organisation_id: str, invitation_id: str, expected_revision: int, revoked_at: datetime
    ) -> TeamInvitation:
        query = {"organisation_id": organisation_id, "id": invitation_id}
        async with self._database.transaction(organisation_id) as session:
            row = await self._database.collection("invitations").find_one(query, session=session)
            if row is None:
                raise ResourceNotFoundError("invitation is unavailable")
            current = TeamInvitation.model_validate(row["data"])
            if current.revision != expected_revision or current.accepted_at is not None:
                raise RevisionConflictError("invitation revision changed")
            changed = current.model_copy(
                update={
                    "revoked_at": revoked_at,
                    "updated_at": revoked_at,
                    "revision": current.revision + 1,
                }
            )
            await self._database.collection("invitations").update_one(
                query,
                {"$set": {"data": changed.model_dump(mode="json")}},
                session=session,
            )
        return changed

    async def _replace(
        self, organisation_id: str, revision: int, changed: PrincipalGrant
    ) -> PrincipalGrant:
        async with self._database.transaction(organisation_id) as session:
            result = await self._database.collection("members").update_one(
                {
                    "organisation_id": organisation_id,
                    "subject": changed.subject,
                    "data.revision": revision,
                },
                {"$set": {"data": changed.model_dump(mode="json")}},
                session=session,
            )
            if not result.matched_count:
                raise RevisionConflictError("workspace member revision changed")
        return changed

    async def _accept(self, identity: AuthenticatedIdentity) -> None:
        async for row in (
            self._database.collection("invitations")
            .find({"email": (identity.email or "").strip().lower()})
            .sort("organisation_id")
        ):
            async with self._database.transaction(row["organisation_id"]) as session:
                await self._accept_one(row["organisation_id"], row["id"], identity, session)

    async def _accept_one(
        self,
        org: str,
        invitation_id: str,
        identity: AuthenticatedIdentity,
        session: AsyncClientSession,
    ) -> None:
        query = {"organisation_id": org, "id": invitation_id}
        row = await self._database.collection("invitations").find_one(query, session=session)
        if not row:
            return
        invitation = TeamInvitation.model_validate(row["data"])
        now = self._clock()
        if invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= now:
            return
        grant = _grant(identity, Role(invitation.role.value), now)
        await self._database.collection("members").update_one(
            {"organisation_id": org, "subject": identity.subject},
            {
                "$setOnInsert": {
                    "id": principal_id(identity.subject),
                    "data": grant.model_dump(mode="json"),
                }
            },
            upsert=True,
            session=session,
        )
        changed = invitation.model_copy(
            update={
                "accepted_at": now,
                "updated_at": now,
                "revision": invitation.revision + 1,
            }
        )
        await self._database.collection("invitations").update_one(
            query,
            {"$set": {"data": changed.model_dump(mode="json")}},
            session=session,
        )


def _grant(identity: AuthenticatedIdentity, role: Role, now: datetime) -> PrincipalGrant:
    return PrincipalGrant(
        subject=identity.subject,
        roles=frozenset({role}),
        email=identity.email,
        display_name=identity.display_name,
        connected_via=identity.connected_via,
        created_at=now,
        updated_at=now,
    )
