import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from contracts import (
    AccountProfile,
    AccountSession,
    MemberRole,
    MemberStatus,
    Organisation,
    OrganisationMembership,
    TeamInvitation,
    TeamMember,
)

from core.auth import AuthenticatedIdentity, PrincipalGrant, Role
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.ids import new_id


class AccountRepository(Protocol):
    async def session(
        self,
        identity: AuthenticatedIdentity,
    ) -> tuple[OrganisationMembership, ...]: ...

    async def create_organisation(
        self,
        organisation: Organisation,
        identity: AuthenticatedIdentity,
        created_at: datetime,
    ) -> OrganisationMembership: ...

    async def get(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> PrincipalGrant | None: ...

    async def sync_identity(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        updated_at: datetime,
    ) -> PrincipalGrant: ...

    async def update_profile(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        expected_revision: int,
        display_name: str,
        updated_at: datetime,
    ) -> PrincipalGrant: ...

    async def list_principals(self, organisation_id: str) -> tuple[PrincipalGrant, ...]: ...

    async def list_invitations(self, organisation_id: str) -> tuple[TeamInvitation, ...]: ...

    async def save_invitation(self, invitation: TeamInvitation) -> TeamInvitation: ...

    async def update_member(
        self,
        organisation_id: str,
        member_id: str,
        expected_revision: int,
        role: Role,
        enabled: bool,
        updated_at: datetime,
    ) -> PrincipalGrant: ...

    async def revoke_invitation(
        self,
        organisation_id: str,
        invitation_id: str,
        expected_revision: int,
        revoked_at: datetime,
    ) -> TeamInvitation: ...


class AccountService:
    def __init__(
        self,
        repository: AccountRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def session(self, identity: AuthenticatedIdentity) -> AccountSession:
        return AccountSession(organisations=await self._repository.session(identity))

    async def create_organisation(
        self,
        identity: AuthenticatedIdentity,
        name: str,
    ) -> OrganisationMembership:
        value = name.strip()
        if not value:
            raise ResourceConflictError("organisation name cannot be empty")
        if identity.email is None or not identity.email_verified:
            raise ResourceConflictError("verify your email before creating an organisation")
        now = self._clock()
        organisation = Organisation(
            id=new_id("org"),
            name=value,
            created_at=now,
            updated_at=now,
        )
        return await self._repository.create_organisation(
            organisation,
            identity,
            now,
        )

    async def profile(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> AccountProfile:
        grant = await self._repository.sync_identity(organisation_id, identity, self._clock())
        return _profile(organisation_id, identity, grant)

    async def update_profile(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        expected_revision: int,
        display_name: str,
    ) -> AccountProfile:
        name = display_name.strip()
        if not name:
            raise ResourceConflictError("profile name cannot be empty")
        grant = await self._repository.update_profile(
            organisation_id,
            identity,
            expected_revision,
            name,
            self._clock(),
        )
        return _profile(organisation_id, identity, grant)

    async def team(self, organisation_id: str) -> tuple[TeamMember, ...]:
        now = self._clock()
        principals = await self._repository.list_principals(organisation_id)
        invitations = await self._repository.list_invitations(organisation_id)
        members = [
            _principal_member(organisation_id, principal, now)
            for principal in principals
            if Role.AUTOMATION not in principal.roles and principal.email is not None
        ]
        active_emails = {member.email for member in members}
        members.extend(
            _invited_member(invitation)
            for invitation in invitations
            if invitation.accepted_at is None
            and invitation.revoked_at is None
            and invitation.expires_at > now
            and invitation.email not in active_emails
        )
        return tuple(
            sorted(
                members,
                key=lambda item: (item.status is not MemberStatus.ACTIVE, item.email),
            )
        )

    async def invite(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        email: str,
        role: MemberRole,
    ) -> TeamMember:
        normalised = _normalise_email(email)
        if identity.email and normalised == identity.email.strip().lower():
            raise ResourceConflictError("you are already a member of this organisation")
        existing = await self.team(organisation_id)
        if any(member.email == normalised for member in existing):
            raise ResourceConflictError("this email already belongs to the team")
        now = self._clock()
        invitation = TeamInvitation(
            id=invitation_id(normalised),
            organisation_id=organisation_id,
            email=normalised,
            role=role,
            invited_by=identity.actor_id,
            expires_at=now + timedelta(days=7),
            created_at=now,
            updated_at=now,
        )
        return _invited_member(await self._repository.save_invitation(invitation))

    async def update_member(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        member_id: str,
        expected_revision: int,
        role: MemberRole,
        enabled: bool,
    ) -> TeamMember:
        if member_id == principal_id(identity.subject):
            raise ResourceConflictError("change your own access from another administrator account")
        grant = await self._repository.update_member(
            organisation_id,
            member_id,
            expected_revision,
            Role(role.value),
            enabled,
            self._clock(),
        )
        return _principal_member(organisation_id, grant, self._clock())

    async def revoke_invitation(
        self,
        organisation_id: str,
        invitation_id_value: str,
        expected_revision: int,
    ) -> TeamMember:
        invitation = await self._repository.revoke_invitation(
            organisation_id,
            invitation_id_value,
            expected_revision,
            self._clock(),
        )
        return _invited_member(invitation)


def invitation_id(email: str) -> str:
    digest = hashlib.sha256(_normalise_email(email).encode()).hexdigest()
    return f"invitation_{digest[:32]}"


def _profile(
    organisation_id: str,
    identity: AuthenticatedIdentity,
    grant: PrincipalGrant,
) -> AccountProfile:
    email = grant.email or identity.email
    if email is None:
        raise ResourceNotFoundError("the signed-in account has no email address")
    return AccountProfile(
        id=principal_id(identity.subject),
        organisation_id=organisation_id,
        display_name=grant.display_name or identity.display_name or email.split("@", 1)[0],
        email=email,
        connected_via=grant.connected_via or identity.connected_via,
        role=_member_role(grant.roles),
        revision=grant.revision,
    )


def _principal_member(
    organisation_id: str,
    grant: PrincipalGrant,
    now: datetime,
) -> TeamMember:
    if grant.email is None:
        raise ResourceNotFoundError("team member has no email address")
    created_at = grant.created_at or grant.updated_at or now
    return TeamMember(
        id=principal_id(grant.subject),
        organisation_id=organisation_id,
        display_name=grant.display_name,
        email=grant.email,
        connected_via=grant.connected_via,
        role=_member_role(grant.roles),
        status=MemberStatus.ACTIVE if grant.enabled else MemberStatus.DISABLED,
        created_at=created_at,
        updated_at=grant.updated_at or created_at,
        revision=grant.revision,
    )


def _invited_member(invitation: TeamInvitation) -> TeamMember:
    return TeamMember(
        id=invitation.id,
        organisation_id=invitation.organisation_id,
        email=invitation.email,
        role=invitation.role,
        status=MemberStatus.DISABLED if invitation.revoked_at is not None else MemberStatus.PENDING,
        created_at=invitation.created_at,
        updated_at=invitation.updated_at,
        revision=invitation.revision,
    )


def _member_role(roles: frozenset[Role]) -> MemberRole:
    for role in (Role.ADMINISTRATOR, Role.OPERATOR, Role.VIEWER):
        if role in roles:
            return MemberRole(role.value)
    raise ResourceNotFoundError("team member has no human role")


def _normalise_email(email: str) -> str:
    value = email.strip().lower()
    local, separator, domain = value.rpartition("@")
    if not separator or not local or "." not in domain or domain.startswith("."):
        raise ResourceConflictError("enter a valid email address")
    return value


def principal_id(subject: str) -> str:
    return f"principal_{hashlib.sha256(subject.encode()).hexdigest()}"
