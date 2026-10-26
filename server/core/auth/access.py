from datetime import datetime
from enum import StrEnum
from typing import Protocol

from contracts import Contract

from core.auth.identity import AuthenticatedIdentity
from core.errors import AuthorizationError


class Permission(StrEnum):
    RUN_READ = "run.read"
    RUN_WRITE = "run.write"
    INVENTORY_READ = "inventory.read"
    INVENTORY_WRITE = "inventory.write"
    PLAYBOOK_READ = "playbook.read"
    PLAYBOOK_WRITE = "playbook.write"
    PLAYBOOK_APPROVE = "playbook.approve"
    VERIFICATION_READ = "verification.read"
    VERIFICATION_WRITE = "verification.write"
    VERIFICATION_APPROVE = "verification.approve"
    APPROVAL_READ = "approval.read"
    APPROVAL_DECIDE = "approval.decide"
    INCIDENT_WRITE = "incident.write"
    NOTIFICATION_READ = "notification.read"
    NOTIFICATION_WRITE = "notification.write"
    PROFILE_READ = "profile.read"
    PROFILE_WRITE = "profile.write"
    TEAM_READ = "team.read"
    TEAM_WRITE = "team.write"
    AGENT_READ = "agent.read"
    AGENT_WRITE = "agent.write"
    AUDIT_READ = "audit.read"


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMINISTRATOR = "administrator"
    AUTOMATION = "automation"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(
        {
            Permission.RUN_READ,
            Permission.INVENTORY_READ,
            Permission.PLAYBOOK_READ,
            Permission.VERIFICATION_READ,
            Permission.APPROVAL_READ,
            Permission.AGENT_READ,
            Permission.AUDIT_READ,
            Permission.NOTIFICATION_READ,
            Permission.PROFILE_READ,
            Permission.PROFILE_WRITE,
        }
    ),
    Role.OPERATOR: frozenset(
        {
            Permission.RUN_READ,
            Permission.RUN_WRITE,
            Permission.INVENTORY_READ,
            Permission.PLAYBOOK_READ,
            Permission.PLAYBOOK_WRITE,
            Permission.VERIFICATION_READ,
            Permission.VERIFICATION_WRITE,
            Permission.APPROVAL_READ,
            Permission.AGENT_READ,
            Permission.INCIDENT_WRITE,
            Permission.NOTIFICATION_READ,
            Permission.PROFILE_READ,
            Permission.PROFILE_WRITE,
        }
    ),
    Role.AUTOMATION: frozenset(
        {
            Permission.RUN_READ,
            Permission.RUN_WRITE,
            Permission.INVENTORY_READ,
            Permission.PLAYBOOK_READ,
            Permission.PLAYBOOK_WRITE,
            Permission.VERIFICATION_READ,
            Permission.VERIFICATION_WRITE,
            Permission.APPROVAL_READ,
            Permission.AGENT_READ,
            Permission.INCIDENT_WRITE,
            Permission.NOTIFICATION_READ,
        }
    ),
    Role.ADMINISTRATOR: frozenset(Permission),
}


class PrincipalGrant(Contract):
    subject: str
    roles: frozenset[Role]
    enabled: bool = True
    email: str | None = None
    display_name: str | None = None
    connected_via: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    revision: int = 0


class AccessRepository(Protocol):
    async def get(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> PrincipalGrant | None: ...


class AccessControl:
    def __init__(self, repository: AccessRepository) -> None:
        self._repository = repository

    async def require(
        self,
        identity: AuthenticatedIdentity,
        organisation_id: str,
        permission: Permission,
    ) -> None:
        allowed = await self.permissions(identity, organisation_id)
        if permission not in allowed:
            raise AuthorizationError(f"principal lacks {permission.value}")

    async def permissions(
        self,
        identity: AuthenticatedIdentity,
        organisation_id: str,
    ) -> frozenset[Permission]:
        grant = await self._repository.get(organisation_id, identity)
        if grant is None or not grant.enabled:
            raise AuthorizationError("principal is not enabled for this organisation")
        return frozenset(item for role in grant.roles for item in ROLE_PERMISSIONS[role])
