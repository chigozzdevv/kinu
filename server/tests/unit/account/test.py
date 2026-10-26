import hashlib
from datetime import UTC, datetime

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from contracts import (
    MemberRole,
    MemberStatus,
    Notification,
    NotificationEndpoint,
    NotificationKind,
    Organisation,
    OrganisationMembership,
    TeamInvitation,
)
from core.account import AccountService
from core.auth import AccessControl, AuthenticatedIdentity, PrincipalGrant, Role
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.notification import EmailDeliveryConfiguration, NotificationService
from core.workflow import RunWorkflow
from testkit import MemoryRunRepository

NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)
IDENTITY = AuthenticatedIdentity(
    subject="user-one",
    issuer="https://identity.example.com/kinu",
    email="owner@acme.example",
    email_verified=True,
    display_name="Original Owner",
    connected_via="SSO",
)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Accounts:
    def __init__(self) -> None:
        self.organisations = {
            "org_one": Organisation(id="org_one", name="Acme", created_at=NOW, updated_at=NOW)
        }
        self.principals = {
            IDENTITY.document_id: PrincipalGrant(
                subject=IDENTITY.subject,
                roles=frozenset({Role.ADMINISTRATOR}),
                email=IDENTITY.email,
                display_name=IDENTITY.display_name,
                connected_via=IDENTITY.connected_via,
                created_at=NOW,
                updated_at=NOW,
            ),
            _document_id("user-two"): PrincipalGrant(
                subject="user-two",
                roles=frozenset({Role.OPERATOR}),
                email="operator@acme.example",
                display_name="Operator",
                connected_via="Organisation SSO",
                created_at=NOW,
                updated_at=NOW,
            ),
        }
        self.invitations: dict[str, TeamInvitation] = {}

    async def session(self, identity: AuthenticatedIdentity) -> tuple[OrganisationMembership, ...]:
        grant = self.principals.get(identity.document_id)
        if grant is None and identity.email_verified and identity.email:
            invitation = next(
                (
                    value
                    for value in self.invitations.values()
                    if value.email == identity.email.strip().lower()
                    and value.accepted_at is None
                    and (value.revoked_at is None)
                    and (value.expires_at > NOW)
                ),
                None,
            )
            if invitation:
                grant = PrincipalGrant(
                    subject=identity.subject,
                    roles=frozenset({Role(invitation.role.value)}),
                    email=identity.email,
                    display_name=identity.display_name,
                    connected_via=identity.connected_via,
                    created_at=NOW,
                    updated_at=NOW,
                )
                self.principals[identity.document_id] = grant
        if grant is None:
            return ()
        role = next(
            Role(value)
            for value in ("administrator", "operator", "viewer")
            if Role(value) in grant.roles
        )
        return (
            OrganisationMembership(
                organisation=self.organisations["org_one"], role=MemberRole(role.value)
            ),
        )

    async def create_organisation(
        self, organisation: Organisation, identity: AuthenticatedIdentity, created_at: datetime
    ) -> OrganisationMembership:
        self.organisations[organisation.id] = organisation
        self.principals[identity.document_id] = PrincipalGrant(
            subject=identity.subject,
            roles=frozenset({Role.ADMINISTRATOR}),
            email=identity.email,
            display_name=identity.display_name,
            connected_via=identity.connected_via,
            created_at=created_at,
            updated_at=created_at,
        )
        return OrganisationMembership(organisation=organisation, role=MemberRole.ADMINISTRATOR)

    async def get(
        self, organisation_id: str, identity: AuthenticatedIdentity
    ) -> PrincipalGrant | None:
        return self.principals.get(identity.document_id)

    async def sync_identity(
        self, organisation_id: str, identity: AuthenticatedIdentity, updated_at: datetime
    ) -> PrincipalGrant:
        grant = self.principals.get(identity.document_id)
        if grant is None:
            raise ResourceNotFoundError("member missing")
        return grant

    async def update_profile(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        expected_revision: int,
        display_name: str,
        updated_at: datetime,
    ) -> PrincipalGrant:
        current = self.principals[identity.document_id]
        if current.revision != expected_revision:
            raise ResourceConflictError("revision changed")
        changed = current.model_copy(
            update={
                "display_name": display_name,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.principals[identity.document_id] = changed
        return changed

    async def list_principals(self, organisation_id: str) -> tuple[PrincipalGrant, ...]:
        return tuple(self.principals.values())

    async def list_invitations(self, organisation_id: str) -> tuple[TeamInvitation, ...]:
        return tuple(self.invitations.values())

    async def save_invitation(self, invitation: TeamInvitation) -> TeamInvitation:
        self.invitations[invitation.id] = invitation
        return invitation

    async def update_member(
        self,
        organisation_id: str,
        member_id: str,
        expected_revision: int,
        role: Role,
        enabled: bool,
        updated_at: datetime,
    ) -> PrincipalGrant:
        document_id = member_id.removeprefix("principal_")
        current = self.principals[document_id]
        changed = current.model_copy(
            update={
                "roles": frozenset({role}),
                "enabled": enabled,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.principals[document_id] = changed
        return changed

    async def revoke_invitation(
        self, organisation_id: str, invitation_id: str, expected_revision: int, revoked_at: datetime
    ) -> TeamInvitation:
        current = self.invitations[invitation_id]
        changed = current.model_copy(
            update={
                "revoked_at": revoked_at,
                "updated_at": revoked_at,
                "revision": current.revision + 1,
            }
        )
        self.invitations[invitation_id] = changed
        return changed


class Notifications:
    def __init__(self) -> None:
        self.values: dict[str, Notification] = {}
        self.endpoints: dict[str, NotificationEndpoint] = {}

    async def emit(self, notification: Notification) -> tuple[Notification, bool]:
        self.values[notification.id] = notification
        return (notification, True)

    async def list_notifications(
        self, organisation_id: str, limit: int
    ) -> tuple[Notification, ...]:
        return tuple(self.values.values())[:limit]

    async def mark_read(
        self, organisation_id: str, notification_id: str, expected_revision: int, read_at: datetime
    ) -> Notification:
        raise AssertionError("not used")

    async def register_endpoint(self, endpoint: NotificationEndpoint) -> NotificationEndpoint:
        self.endpoints[endpoint.id] = endpoint
        return endpoint

    async def list_endpoints(self, organisation_id: str) -> tuple[NotificationEndpoint, ...]:
        return tuple(self.endpoints.values())

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
        updated_at: datetime,
    ) -> NotificationEndpoint:
        raise AssertionError("not used")


async def test_profile_uses_identity_metadata_and_updates_only_the_name() -> None:
    service = AccountService(Accounts(), lambda: NOW)
    profile = await service.profile("org_one", IDENTITY)
    changed = await service.update_profile("org_one", IDENTITY, profile.revision, "Chigozie Okafor")
    assert changed.display_name == "Chigozie Okafor"
    assert changed.email == "owner@acme.example"
    assert changed.connected_via == "SSO"
    assert changed.revision == 1


async def test_invitation_is_pending_until_the_verified_email_signs_in() -> None:
    repository = Accounts()
    service = AccountService(repository, lambda: NOW)
    invited = await service.invite(
        "org_one", IDENTITY, "new.member@acme.example", MemberRole.VIEWER
    )
    team = await service.team("org_one")
    assert invited.status is MemberStatus.PENDING
    assert invited.email == "new.member@acme.example"
    assert any(member.id == invited.id for member in team)


async def test_invited_account_joins_without_creating_an_organisation() -> None:
    repository = Accounts()
    repository.principals.pop(_document_id("user-two"))
    service = AccountService(repository, lambda: NOW)
    await service.invite("org_one", IDENTITY, "invited@acme.example", MemberRole.OPERATOR)
    invited_identity = AuthenticatedIdentity(
        subject="invited-user",
        issuer=IDENTITY.issuer,
        email="invited@acme.example",
        email_verified=True,
        display_name="Invited User",
        connected_via="Email",
    )
    session = await service.session(invited_identity)
    assert session.organisations[0].organisation.id == "org_one"
    assert session.organisations[0].role is MemberRole.OPERATOR


async def test_first_account_can_create_its_organisation() -> None:
    service = AccountService(Accounts(), lambda: NOW)
    membership = await service.create_organisation(IDENTITY, "  Kinu Labs  ")
    assert membership.organisation.name == "Kinu Labs"
    assert membership.role is MemberRole.ADMINISTRATOR


async def test_administrator_can_change_or_disable_another_member() -> None:
    repository = Accounts()
    service = AccountService(repository, lambda: NOW)
    member_id = _member_id("user-two")
    changed = await service.update_member(
        "org_one", IDENTITY, member_id, 0, MemberRole.VIEWER, False
    )
    assert changed.role is MemberRole.VIEWER
    assert changed.status is MemberStatus.DISABLED


async def test_member_cannot_change_own_access() -> None:
    service = AccountService(Accounts(), lambda: NOW)
    with pytest.raises(ResourceConflictError, match="another administrator"):
        await service.update_member(
            "org_one", IDENTITY, _member_id(IDENTITY.subject), 0, MemberRole.VIEWER, True
        )


async def test_settings_api_exposes_profile_and_team_mutations() -> None:
    repository = Accounts()
    service = AccountService(repository, lambda: NOW)
    notification_repository = Notifications()
    notifications = NotificationService(
        notification_repository,
        lambda: NOW,
        EmailDeliveryConfiguration(
            "secret://kinu-project/email-delivery/versions/2", "invite@kinu.example"
        ),
    )

    class Tokens:
        async def verify(self, token: str) -> AuthenticatedIdentity:
            assert token == "valid-token"
            return IDENTITY

    application = create_app(
        ApiServices(
            workflow=RunWorkflow(MemoryRunRepository()),
            access=AccessControl(repository),
            accounts=service,
            notifications=notifications,
        ),
        tokens=Tokens(),
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="https://kinu.test") as client:
        headers = {"Authorization": "Bearer valid-token"}
        profile = await client.get("/v1/organisations/org_one/settings/profile", headers=headers)
        invited = await client.post(
            "/v1/organisations/org_one/settings/team/invitations",
            headers=headers,
            json={"email": "new.member@acme.example", "role": "viewer"},
        )
        team = await client.get("/v1/organisations/org_one/settings/team", headers=headers)
        session = await client.get("/v1/session", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["connected_via"] == "SSO"
    assert invited.status_code == 201
    assert invited.json()["status"] == "pending"
    assert len(notification_repository.endpoints) == 1
    invitation = next(iter(notification_repository.values.values()))
    assert invitation.kind is NotificationKind.TEAM_INVITATION
    assert invitation.title == "Join Acme on Kinu"
    assert invitation.link_path == "/auth"
    assert any(member["email"] == "new.member@acme.example" for member in team.json())
    assert session.status_code == 200
    assert session.json()["organisations"][0]["organisation"]["name"] == "Acme"


def _member_id(subject: str) -> str:
    return f"principal_{_document_id(subject)}"


def _document_id(subject: str) -> str:
    return hashlib.sha256(subject.encode()).hexdigest()
