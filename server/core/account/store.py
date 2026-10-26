from collections.abc import Callable
from datetime import datetime

from contracts import MemberRole, Organisation, OrganisationMembership, TeamInvitation
from psycopg.types.json import Jsonb

from core.account.service import principal_id
from core.auth import AuthenticatedIdentity, PrincipalGrant, Role
from core.errors import ResourceConflictError, ResourceNotFoundError, RevisionConflictError
from core.storage.database import Database


class AccountStore:
    def __init__(self, database: Database, clock: Callable[[], datetime]) -> None:
        self._database = database
        self._clock = clock

    async def session(self, identity: AuthenticatedIdentity) -> tuple[OrganisationMembership, ...]:
        if identity.email and identity.email_verified:
            await self._accept(identity)
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT o.data AS organisation, m.data AS member FROM kinu.members m "
                "JOIN kinu.organisations o ON o.id = m.organisation_id "
                "WHERE m.subject = %s AND (m.data->>'enabled')::boolean ORDER BY o.id",
                (identity.subject,),
            )
            rows = await cursor.fetchall()
        memberships = []
        for row in rows:
            grant = PrincipalGrant.model_validate(row["member"])
            role = next(
                (
                    role
                    for role in (
                        MemberRole.ADMINISTRATOR,
                        MemberRole.OPERATOR,
                        MemberRole.VIEWER,
                    )
                    if Role(role.value) in grant.roles
                ),
                None,
            )
            if role is not None:
                memberships.append(
                    OrganisationMembership(
                        organisation=Organisation.model_validate(row["organisation"]), role=role
                    )
                )
        return tuple(memberships)

    async def create_organisation(
        self, organisation: Organisation, identity: AuthenticatedIdentity, created_at: datetime
    ) -> OrganisationMembership:
        grant = _grant(identity, Role.ADMINISTRATOR, created_at)
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                "INSERT INTO kinu.organisations VALUES (%s, %s)",
                (organisation.id, Jsonb(organisation.model_dump(mode="json"))),
            )
            await connection.execute(
                "INSERT INTO kinu.members VALUES (%s, %s, %s, %s)",
                (organisation.id, identity.subject, principal_id(identity.subject), _json(grant)),
            )
        return OrganisationMembership(organisation=organisation, role=MemberRole.ADMINISTRATOR)

    async def get(
        self, organisation_id: str, identity: AuthenticatedIdentity
    ) -> PrincipalGrant | None:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT data FROM kinu.members WHERE organisation_id = %s AND subject = %s",
                (organisation_id, identity.subject),
            )
            row = await cursor.fetchone()
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
        changed = current.model_copy(
            update={
                "display_name": display_name,
                "updated_at": updated_at,
                "revision": expected_revision + 1,
            }
        )
        return await self._replace(organisation_id, expected_revision, changed)

    async def list_principals(self, organisation_id: str) -> tuple[PrincipalGrant, ...]:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT data FROM kinu.members WHERE organisation_id = %s ORDER BY id",
                (organisation_id,),
            )
            return tuple(
                PrincipalGrant.model_validate(row["data"]) for row in await cursor.fetchall()
            )

    async def list_invitations(self, organisation_id: str) -> tuple[TeamInvitation, ...]:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT data FROM kinu.invitations WHERE organisation_id = %s ORDER BY id",
                (organisation_id,),
            )
            return tuple(
                TeamInvitation.model_validate(row["data"]) for row in await cursor.fetchall()
            )

    async def save_invitation(self, invitation: TeamInvitation) -> TeamInvitation:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "INSERT INTO kinu.invitations VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (organisation_id, id) DO UPDATE SET data = "
                "jsonb_set(EXCLUDED.data, '{revision}', "
                "to_jsonb((kinu.invitations.data->>'revision')::int + 1)) RETURNING data",
                (
                    invitation.organisation_id,
                    invitation.id,
                    invitation.email,
                    Jsonb(invitation.model_dump(mode="json")),
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ResourceConflictError("invitation could not be saved")
        return TeamInvitation.model_validate(row["data"])

    async def update_member(
        self,
        organisation_id: str,
        member_id: str,
        expected_revision: int,
        role: Role,
        enabled: bool,
        updated_at: datetime,
    ) -> PrincipalGrant:
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                "SELECT id FROM kinu.organisations WHERE id = %s FOR UPDATE", (organisation_id,)
            )
            cursor = await connection.execute(
                "SELECT data FROM kinu.members WHERE organisation_id = %s AND id = %s FOR UPDATE",
                (organisation_id, member_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError("workspace member is unavailable")
            current = PrincipalGrant.model_validate(row["data"])
            if current.revision != expected_revision:
                raise RevisionConflictError("workspace member revision changed")
            if current.enabled and Role.ADMINISTRATOR in current.roles:
                cursor = await connection.execute(
                    "SELECT count(*) AS total FROM kinu.members WHERE organisation_id = %s "
                    "AND (data->>'enabled')::boolean AND data->'roles' ? 'administrator'",
                    (organisation_id,),
                )
                count = await cursor.fetchone()
                if (
                    count
                    and count["total"] <= 1
                    and (not enabled or role is not Role.ADMINISTRATOR)
                ):
                    raise ResourceConflictError("workspace must retain an enabled administrator")
            changed = current.model_copy(
                update={
                    "roles": frozenset({role}),
                    "enabled": enabled,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            await connection.execute(
                "UPDATE kinu.members SET data = %s WHERE organisation_id = %s AND id = %s",
                (_json(changed), organisation_id, member_id),
            )
        return changed

    async def revoke_invitation(
        self, organisation_id: str, invitation_id: str, expected_revision: int, revoked_at: datetime
    ) -> TeamInvitation:
        async with self._database.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                "SELECT data FROM kinu.invitations WHERE organisation_id = %s AND id = %s "
                "FOR UPDATE",
                (organisation_id, invitation_id),
            )
            row = await cursor.fetchone()
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
            await connection.execute(
                "UPDATE kinu.invitations SET data = %s WHERE organisation_id = %s AND id = %s",
                (Jsonb(changed.model_dump(mode="json")), organisation_id, invitation_id),
            )
        return changed

    async def _replace(
        self, organisation_id: str, revision: int, changed: PrincipalGrant
    ) -> PrincipalGrant:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "UPDATE kinu.members SET data = %s WHERE organisation_id = %s AND subject = %s "
                "AND (data->>'revision')::int = %s RETURNING data",
                (_json(changed), organisation_id, changed.subject, revision),
            )
            if await cursor.fetchone() is None:
                raise RevisionConflictError("workspace member revision changed")
        return changed

    async def _accept(self, identity: AuthenticatedIdentity) -> None:
        async with self._database.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                "SELECT data FROM kinu.invitations WHERE email = %s ORDER BY organisation_id "
                "FOR UPDATE",
                ((identity.email or "").strip().lower(),),
            )
            for row in await cursor.fetchall():
                invitation = TeamInvitation.model_validate(row["data"])
                now = self._clock()
                if (
                    invitation.accepted_at is not None
                    or invitation.revoked_at is not None
                    or invitation.expires_at <= now
                ):
                    continue
                grant = _grant(identity, Role(invitation.role.value), now)
                await connection.execute(
                    "INSERT INTO kinu.members VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (
                        invitation.organisation_id,
                        identity.subject,
                        principal_id(identity.subject),
                        _json(grant),
                    ),
                )
                changed = invitation.model_copy(
                    update={
                        "accepted_at": now,
                        "updated_at": now,
                        "revision": invitation.revision + 1,
                    }
                )
                await connection.execute(
                    "UPDATE kinu.invitations SET data = %s WHERE organisation_id = %s AND id = %s",
                    (
                        Jsonb(changed.model_dump(mode="json")),
                        invitation.organisation_id,
                        invitation.id,
                    ),
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


def _json(grant: PrincipalGrant) -> Jsonb:
    return Jsonb(grant.model_dump(mode="json"))
